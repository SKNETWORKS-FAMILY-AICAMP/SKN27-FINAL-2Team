import sys
from argparse import ArgumentParser
from json import dump
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent / "terms"))

from common import load_pipeline_policy
from entity_resolution.build_resolution_package import (
    build_resolution_tables,
    summarize_resolution_tables,
    write_resolution_package,
)
from entity_resolution.semantic_review import (
    build_term_review_tasks,
    write_jsonl,
)
from get_history_terms import count_terms
from match_names import match_names, print_match_report
from prep_thesaurus import (
    calculate_coverage,
    find_homonym_candidates,
    load_encyclopedia_terms,
    prep_thesaurus,
    print_coverage_report,
    print_homonym_report,
    save_thesaurus_json,
)
from prep_json import prep_json
from scan_definitions import print_scan_report, scan_definitions


def resolve_pipeline_paths(
    exam_json_path: str = "",
    thesaurus_csv_path: str = "",
    output_dir: str = "",
    encyclopedia_jsonl_path: str = "",
    itkc_people_csv_path: str = "",
    itkc_events_csv_path: str = "",
) -> dict[str, str]:
    """명시된 경로를 우선하고 비어 있는 입력만 프로젝트 기본 경로로 채운다."""
    project_root = Path(__file__).resolve().parents[3]
    neo4j_root = Path(__file__).resolve().parent
    resolved_exam_path = exam_json_path
    if not resolved_exam_path:
        resolved_exam_path = str(project_root / "ai" / "ml" / "ML_han_v1.json")

    resolved_thesaurus_path = thesaurus_csv_path
    if not resolved_thesaurus_path:
        thesaurus_candidates = list(
            (project_root / "etl" / "raw_data").glob("*20211028*.csv")
        )
        if len(thesaurus_candidates) != 1:
            raise FileNotFoundError(
                "시소러스 CSV를 하나로 확정할 수 없습니다. "
                "경로를 인자로 지정하세요."
            )
        resolved_thesaurus_path = str(thesaurus_candidates[0])

    resolved_output_dir = output_dir
    if not resolved_output_dir:
        resolved_output_dir = str(neo4j_root / "output")

    resolved_encyclopedia_path = encyclopedia_jsonl_path
    if not resolved_encyclopedia_path:
        default_encyclopedia = (
            project_root
            / "etl"
            / "raw_data"
            / "한국민족문화대백과사전"
            / "articles_detail.jsonl"
        )
        if default_encyclopedia.is_file():
            resolved_encyclopedia_path = str(default_encyclopedia)

    itkc_directory = (
        project_root / "etl" / "raw_data" / "한국고전종합DB_관계망"
    )
    resolved_people_path = itkc_people_csv_path
    if not resolved_people_path:
        default_people = itkc_directory / "itkc_people.csv"
        if default_people.is_file():
            resolved_people_path = str(default_people)

    resolved_events_path = itkc_events_csv_path
    if not resolved_events_path:
        default_events = itkc_directory / "itkc_events.csv"
        if default_events.is_file():
            resolved_events_path = str(default_events)

    return {
        "exam_json_path": resolved_exam_path,
        "thesaurus_csv_path": resolved_thesaurus_path,
        "output_dir": resolved_output_dir,
        "encyclopedia_jsonl_path": resolved_encyclopedia_path,
        "itkc_people_csv_path": resolved_people_path,
        "itkc_events_csv_path": resolved_events_path,
    }


def resolve_stage_output_paths(
    output_dir: str,
    policy: dict,
    checkpoint_output: str = "",
    extracted_json_output: str = "",
    extracted_csv_output: str = "",
    thesaurus_json_output: str = "",
    coverage_json_output: str = "",
) -> dict[str, Path]:
    """설정의 업무 단계별 폴더와 파일명으로 출력 경로를 구성한다."""
    output_root = Path(output_dir)
    layout = policy["output_layout"]
    directory_names = layout["directories"]
    file_names = layout["files"]
    internal_directory_name = layout["internal_directory"]
    term_directory = output_root / directory_names["term_extraction"]
    retrieval_directory = output_root / directory_names[
        "candidate_retrieval"
    ]
    term_internal_directory = term_directory / internal_directory_name
    retrieval_internal_directory = (
        retrieval_directory / internal_directory_name
    )
    review_directory = output_root / directory_names["llm_review"]
    paths = {
        "term_checkpoint": term_internal_directory
        / file_names["term_checkpoint"],
        "extracted_terms_json": term_internal_directory
        / file_names["extracted_terms_json"],
        "extracted_terms_csv": term_directory
        / file_names["extracted_terms_csv"],
        "normalized_thesaurus": term_internal_directory
        / file_names["normalized_thesaurus"],
        "coverage_report": retrieval_directory
        / file_names["coverage_report"],
        "name_matches": retrieval_internal_directory
        / file_names["name_matches"],
        "definition_matches": retrieval_internal_directory
        / file_names["definition_matches"],
        "entity_resolution_directory": output_root
        / directory_names["entity_resolution"],
        "llm_review_directory": review_directory,
        "final_identity_directory": output_root
        / directory_names["final_identity"],
        "term_review_tasks": review_directory
        / policy["entity_resolution"]["semantic_review"]["term_task_file"],
    }
    overrides = {
        "term_checkpoint": checkpoint_output,
        "extracted_terms_json": extracted_json_output,
        "extracted_terms_csv": extracted_csv_output,
        "normalized_thesaurus": thesaurus_json_output,
        "coverage_report": coverage_json_output,
    }
    for path_name, override_path in overrides.items():
        if override_path:
            paths[path_name] = Path(override_path)
    return paths


def run_preprocessing_pipeline(
    exam_json_path: str,
    thesaurus_csv_path: str,
    output_dir: str,
    encyclopedia_jsonl_path: str = "",
    itkc_people_csv_path: str = "",
    itkc_events_csv_path: str = "",
    batch_size: int = 20,
    limit: int = 0,
    max_retries: int = 2,
    threshold: float = 90.0,
    display_limit: int = 20,
    checkpoint_output: str = "",
    extracted_json_output: str = "",
    extracted_csv_output: str = "",
    thesaurus_json_output: str = "",
    coverage_json_output: str = "",
    policy_path: str = "",
) -> dict[str, object]:
    """
    기출문제 전처리부터 용어 추출, 시소러스 변환, 커버리지 비교,
    백과사전·ITKC 이름 매칭, definition 스캔까지 실행한다.
    이름 매칭·definition 스캔은 백과사전과 ITKC 경로가 모두 있을 때만 수행한다.
    """
    exam_path = Path(exam_json_path)
    thesaurus_path = Path(thesaurus_csv_path)
    output_directory = Path(output_dir)
    resolved_policy_path = Path(policy_path)
    if not policy_path:
        resolved_policy_path = (
            Path(__file__).resolve().parent
            / "config"
            / "resolution_policy.json"
        )
    pipeline_policy = load_pipeline_policy(str(resolved_policy_path))

    if not exam_path.is_file():
        raise FileNotFoundError(f"기출문제 JSON을 찾을 수 없습니다: {exam_path}")
    if not thesaurus_path.is_file():
        raise FileNotFoundError(f"시소러스 CSV를 찾을 수 없습니다: {thesaurus_path}")
    if encyclopedia_jsonl_path and not Path(encyclopedia_jsonl_path).is_file():
        raise FileNotFoundError(
            f"백과사전 JSONL을 찾을 수 없습니다: {encyclopedia_jsonl_path}"
        )
    if itkc_people_csv_path and not Path(itkc_people_csv_path).is_file():
        raise FileNotFoundError(
            f"ITKC 인물 CSV를 찾을 수 없습니다: {itkc_people_csv_path}"
        )
    if itkc_events_csv_path and not Path(itkc_events_csv_path).is_file():
        raise FileNotFoundError(
            f"ITKC 사건 CSV를 찾을 수 없습니다: {itkc_events_csv_path}"
        )
    if batch_size <= 0:
        raise ValueError("LLM 호출당 문항 수는 1 이상이어야 합니다.")
    if limit < 0:
        raise ValueError("문항 제한 개수는 0 이상이어야 합니다.")
    if max_retries < 0:
        raise ValueError("재시도 횟수는 0 이상이어야 합니다.")
    if display_limit <= 0:
        raise ValueError("표시 개수는 1 이상이어야 합니다.")

    output_paths = resolve_stage_output_paths(
        str(output_directory),
        pipeline_policy,
        checkpoint_output=checkpoint_output,
        extracted_json_output=extracted_json_output,
        extracted_csv_output=extracted_csv_output,
        thesaurus_json_output=thesaurus_json_output,
        coverage_json_output=coverage_json_output,
    )
    for destination in output_paths.values():
        if destination.suffix:
            destination.parent.mkdir(parents=True, exist_ok=True)
        elif not destination.suffix:
            destination.mkdir(parents=True, exist_ok=True)
    checkpoint_path = output_paths["term_checkpoint"]
    extracted_json_path = output_paths["extracted_terms_json"]
    extracted_csv_path = output_paths["extracted_terms_csv"]
    thesaurus_json_path = output_paths["normalized_thesaurus"]
    coverage_report_path = output_paths["coverage_report"]
    name_matches_path = output_paths["name_matches"]
    definition_scan_path = output_paths["definition_matches"]

    print("[1/6] 기출문제 전처리 및 역사 용어 추출")
    extracted_term_df = count_terms(
        str(exam_path),
        batch_size=batch_size,
        limit=limit,
        checkpoint_path=str(checkpoint_path),
        max_retries=max_retries,
        thesaurus_path=str(thesaurus_path),
        raw_output=str(extracted_json_path),
        model_config=pipeline_policy["term_extraction"],
        policy_version=pipeline_policy["policy_version"],
    )
    extracted_term_df.to_csv(
        extracted_csv_path,
        index=False,
        encoding="utf-8-sig",
    )
    print(f"기출문제 용어 집계 CSV 저장 완료: {extracted_csv_path}")

    print("[2/6] 한국 역사 용어 시소러스 변환")
    thesaurus_df = prep_thesaurus(str(thesaurus_path))
    homonym_candidates = find_homonym_candidates(thesaurus_df)
    print_homonym_report(homonym_candidates, display_limit)
    save_thesaurus_json(thesaurus_df, str(thesaurus_json_path))
    print(
        f"시소러스 JSON 저장 완료: {thesaurus_json_path} "
        f"({len(thesaurus_df)}개 용어)"
    )

    print("[3/6] 전체 역사 용어 커버리지 비교")
    encyclopedia_reference: dict[str, str] = {}
    if encyclopedia_jsonl_path:
        encyclopedia_reference = load_encyclopedia_terms(encyclopedia_jsonl_path)
        print(f"백과사전 표제어·이칭 로드: {len(encyclopedia_reference)}개 키")
    coverage_report = calculate_coverage(
        extracted_term_df,
        thesaurus_df,
        policy=pipeline_policy,
        threshold=threshold,
        encyclopedia_terms=encyclopedia_reference,
    )
    print_coverage_report(coverage_report, display_limit)

    with coverage_report_path.open("w", encoding="utf-8") as output_file:
        dump(
            coverage_report,
            output_file,
            ensure_ascii=False,
            indent=4,
        )
    print(f"커버리지 보고서 저장 완료: {coverage_report_path}")

    can_match_names = bool(
        encyclopedia_jsonl_path and itkc_people_csv_path and itkc_events_csv_path
    )
    if not can_match_names:
        print(
            "[4/6][5/6][6/6] 건너뜀: "
            "백과사전 JSONL과 ITKC CSV 경로가 모두 필요합니다."
        )
        return coverage_report

    print("[4/6] 백과사전·ITKC 이름 매칭")
    name_match_results = match_names(
        terms_csv=str(extracted_csv_path),
        thesaurus_csv=str(thesaurus_path),
        encyclopedia_jsonl=encyclopedia_jsonl_path,
        itkc_people_csv=itkc_people_csv_path,
        itkc_events_csv=itkc_events_csv_path,
        policy=pipeline_policy,
    )
    print_match_report(name_match_results)
    with name_matches_path.open("w", encoding="utf-8") as output_file:
        dump(name_match_results, output_file, ensure_ascii=False, indent=2)
    print(f"이름 매칭 결과 저장 완료: {name_matches_path}")

    print("[5/6] 미매칭 용어 definition 스캔")
    definition_scan_results = scan_definitions(
        match_json=str(name_matches_path),
        encyclopedia_jsonl=encyclopedia_jsonl_path,
        policy=pipeline_policy,
    )
    print_scan_report(definition_scan_results)
    with definition_scan_path.open("w", encoding="utf-8") as output_file:
        dump(definition_scan_results, output_file, ensure_ascii=False, indent=2)
    print(f"definition 스캔 결과 저장 완료: {definition_scan_path}")

    print("[6/6] 문항별 Entity Resolution staging CSV 생성")
    resolution_tables = build_resolution_tables(
        name_match_results,
        definition_scan_results,
        prep_json(str(exam_path)),
        pipeline_policy,
    )
    resolution_output_dir = output_paths["entity_resolution_directory"]
    resolution_paths = write_resolution_package(
        resolution_tables,
        str(resolution_output_dir),
        pipeline_policy,
    )
    resolution_summary = summarize_resolution_tables(resolution_tables)
    print(f"Entity Resolution staging 요약: {resolution_summary}")
    print(f"Entity Resolution CSV 저장 완료: {resolution_paths}")
    term_review_tasks = build_term_review_tasks(
        resolution_tables,
        pipeline_policy,
    )
    review_output_dir = output_paths["llm_review_directory"]
    term_task_path = output_paths["term_review_tasks"]
    write_jsonl(term_review_tasks, str(term_task_path))
    print(
        "Entity Resolution term review task 저장 완료: "
        f"{len(term_review_tasks)}건, {term_task_path}"
    )
    return coverage_report


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")

    parser = ArgumentParser(
        description=(
            "기출문제 전처리·역사 용어 추출·시소러스 커버리지 비교 파이프라인"
        )
    )
    parser.add_argument(
        "exam_json_path",
        metavar="기출문제_json",
        nargs="?",
        default="",
        help="OCR 기출문제 JSON 경로",
    )
    parser.add_argument(
        "thesaurus_csv_path",
        metavar="시소러스_csv",
        nargs="?",
        default="",
        help="한국 역사 용어 시소러스 CSV 경로",
    )
    parser.add_argument(
        "output_dir",
        metavar="출력_폴더",
        nargs="?",
        default="",
        help="파이프라인 결과 저장 폴더",
    )
    parser.add_argument(
        "--encyclopedia-jsonl",
        default="",
        help="백과사전 JSONL 경로 (미커버 용어를 표제어·이칭으로 2차 매칭)",
    )
    parser.add_argument(
        "--itkc-people",
        default="",
        help="ITKC 인물 CSV 경로 (이름 매칭 단계에서 사용)",
    )
    parser.add_argument(
        "--itkc-events",
        default="",
        help="ITKC 사건 CSV 경로 (이름 매칭 단계에서 사용)",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=20,
        help="LLM 호출 한 번에 처리할 문항 수",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="처리할 문항 수(0이면 전체)",
    )
    parser.add_argument(
        "--retries",
        type=int,
        default=2,
        help="배치 실패 시 재시도 횟수",
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=90.0,
        help="시소러스 사용 판정 커버리지 임계치",
    )
    parser.add_argument(
        "--display-limit",
        type=int,
        default=20,
        help="동명이인 후보와 미커버 용어 표시 개수",
    )
    parser.add_argument(
        "--checkpoint-output",
        default="",
        help="배치 체크포인트 JSONL 저장 경로",
    )
    parser.add_argument(
        "--extracted-json-output",
        default="",
        help="문항별 추출 용어 JSON 저장 경로",
    )
    parser.add_argument(
        "--extracted-csv-output",
        default="",
        help="추출 용어 집계 CSV 저장 경로",
    )
    parser.add_argument(
        "--thesaurus-json-output",
        default="",
        help="변환한 시소러스 JSON 저장 경로",
    )
    parser.add_argument(
        "--coverage-json-output",
        default="",
        help="커버리지 보고서 JSON 저장 경로",
    )
    parser.add_argument(
        "--policy",
        default=str(
            Path(__file__).resolve().parent
            / "config"
            / "resolution_policy.json"
        ),
        help="용어 추출·후보 생성·검증 정책 JSON 경로",
    )
    cli_args = parser.parse_args()

    pipeline_paths = resolve_pipeline_paths(
        exam_json_path=cli_args.exam_json_path,
        thesaurus_csv_path=cli_args.thesaurus_csv_path,
        output_dir=cli_args.output_dir,
        encyclopedia_jsonl_path=cli_args.encyclopedia_jsonl,
        itkc_people_csv_path=cli_args.itkc_people,
        itkc_events_csv_path=cli_args.itkc_events,
    )

    run_preprocessing_pipeline(
        exam_json_path=pipeline_paths["exam_json_path"],
        thesaurus_csv_path=pipeline_paths["thesaurus_csv_path"],
        output_dir=pipeline_paths["output_dir"],
        encyclopedia_jsonl_path=pipeline_paths[
            "encyclopedia_jsonl_path"
        ],
        itkc_people_csv_path=pipeline_paths["itkc_people_csv_path"],
        itkc_events_csv_path=pipeline_paths["itkc_events_csv_path"],
        batch_size=cli_args.batch_size,
        limit=cli_args.limit,
        max_retries=cli_args.retries,
        threshold=cli_args.threshold,
        display_limit=cli_args.display_limit,
        checkpoint_output=cli_args.checkpoint_output,
        extracted_json_output=cli_args.extracted_json_output,
        extracted_csv_output=cli_args.extracted_csv_output,
        thesaurus_json_output=cli_args.thesaurus_json_output,
        coverage_json_output=cli_args.coverage_json_output,
        policy_path=cli_args.policy,
    )
