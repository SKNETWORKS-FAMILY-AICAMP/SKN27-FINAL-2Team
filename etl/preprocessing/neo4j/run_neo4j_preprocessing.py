import sys
from argparse import ArgumentParser
from json import dump
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent / "terms"))

from get_history_terms import count_terms
from prep_thesaurus import (
    calculate_coverage,
    find_homonym_candidates,
    prep_thesaurus,
    print_coverage_report,
    print_homonym_report,
    save_thesaurus_json,
)


def run_preprocessing_pipeline(
    exam_json_path: str,
    thesaurus_csv_path: str,
    output_dir: str,
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
) -> dict[str, object]:
    """
    기출문제 전처리부터 용어 추출, 시소러스 변환, 커버리지 비교까지 실행한다.
    """
    exam_path = Path(exam_json_path)
    thesaurus_path = Path(thesaurus_csv_path)
    output_directory = Path(output_dir)

    if not exam_path.is_file():
        raise FileNotFoundError(f"기출문제 JSON을 찾을 수 없습니다: {exam_path}")
    if not thesaurus_path.is_file():
        raise FileNotFoundError(f"시소러스 CSV를 찾을 수 없습니다: {thesaurus_path}")
    if batch_size <= 0:
        raise ValueError("LLM 호출당 문항 수는 1 이상이어야 합니다.")
    if limit < 0:
        raise ValueError("문항 제한 개수는 0 이상이어야 합니다.")
    if max_retries < 0:
        raise ValueError("재시도 횟수는 0 이상이어야 합니다.")
    if display_limit <= 0:
        raise ValueError("표시 개수는 1 이상이어야 합니다.")

    json_directory = output_directory / "json"
    csv_directory = output_directory / "csv"
    json_directory.mkdir(parents=True, exist_ok=True)
    csv_directory.mkdir(parents=True, exist_ok=True)

    checkpoint_path = json_directory / "terms_checkpoint.jsonl"
    if checkpoint_output:
        checkpoint_path = Path(checkpoint_output)

    extracted_json_path = json_directory / "exam_history_terms.json"
    if extracted_json_output:
        extracted_json_path = Path(extracted_json_output)

    extracted_csv_path = csv_directory / "exam_history_terms.csv"
    if extracted_csv_output:
        extracted_csv_path = Path(extracted_csv_output)

    thesaurus_json_path = json_directory / "history_thesaurus.json"
    if thesaurus_json_output:
        thesaurus_json_path = Path(thesaurus_json_output)

    coverage_report_path = json_directory / "coverage_report.json"
    if coverage_json_output:
        coverage_report_path = Path(coverage_json_output)

    for destination in [
        checkpoint_path,
        extracted_json_path,
        extracted_csv_path,
        thesaurus_json_path,
        coverage_report_path,
    ]:
        destination.parent.mkdir(parents=True, exist_ok=True)

    print("[1/3] 기출문제 전처리 및 역사 용어 추출")
    extracted_term_df = count_terms(
        str(exam_path),
        batch_size=batch_size,
        limit=limit,
        checkpoint_path=str(checkpoint_path),
        max_retries=max_retries,
        thesaurus_path=str(thesaurus_path),
        raw_output=str(extracted_json_path),
    )
    extracted_term_df.to_csv(
        extracted_csv_path,
        index=False,
        encoding="utf-8-sig",
    )
    print(f"기출문제 용어 집계 CSV 저장 완료: {extracted_csv_path}")

    print("[2/3] 한국 역사 용어 시소러스 변환")
    thesaurus_df = prep_thesaurus(str(thesaurus_path))
    homonym_candidates = find_homonym_candidates(thesaurus_df)
    print_homonym_report(homonym_candidates, display_limit)
    save_thesaurus_json(thesaurus_df, str(thesaurus_json_path))
    print(
        f"시소러스 JSON 저장 완료: {thesaurus_json_path} "
        f"({len(thesaurus_df)}개 용어)"
    )

    print("[3/3] 전체 역사 용어 커버리지 비교")
    coverage_report = calculate_coverage(
        extracted_term_df,
        thesaurus_df,
        threshold=threshold,
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
    cli_args = parser.parse_args()

    project_root = Path(__file__).resolve().parents[3]

    exam_json_path = cli_args.exam_json_path
    if not exam_json_path:
        exam_json_path = str(project_root / "test" / "CJ" / "test_ml" / "ML_han_v1.json")

    thesaurus_csv_path = cli_args.thesaurus_csv_path
    if not thesaurus_csv_path:
        thesaurus_candidates = list(
            (project_root / "etl" / "raw_data").glob("*20211028*.csv")
        )
        if len(thesaurus_candidates) != 1:
            raise FileNotFoundError(
                "시소러스 CSV를 하나로 확정할 수 없습니다. "
                "경로를 인자로 지정하세요."
            )
        thesaurus_csv_path = str(thesaurus_candidates[0])

    output_dir = cli_args.output_dir
    if not output_dir:
        output_dir = str(Path(__file__).resolve().parent / "output")

    run_preprocessing_pipeline(
        exam_json_path=exam_json_path,
        thesaurus_csv_path=thesaurus_csv_path,
        output_dir=output_dir,
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
    )
