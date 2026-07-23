import sys
from argparse import ArgumentParser
from json import dumps
from pathlib import Path

from common import load_pipeline_policy
from run_neo4j_preprocessing import (
    resolve_pipeline_paths,
    run_preprocessing_pipeline,
)


def resolve_test_output_directory(
    neo4j_root: Path,
    output_subdirectory: str,
) -> str:
    """테스트 결과가 운영 output 루트와 분리된 하위 폴더에만 저장되게 한다."""
    subdirectory = Path(output_subdirectory)
    if subdirectory.is_absolute() or ".." in subdirectory.parts:
        raise ValueError("테스트 출력은 output 아래의 상대 하위 폴더여야 합니다.")
    output_root = (neo4j_root / "output").resolve()
    output_directory = (output_root / subdirectory).resolve()
    if output_directory == output_root:
        raise ValueError("테스트 runner는 운영 output 루트를 직접 사용할 수 없습니다.")
    if output_root not in output_directory.parents:
        raise ValueError("테스트 출력이 운영 output 디렉터리 밖을 가리킵니다.")
    return str(output_directory)


def main() -> None:
    """설정의 소량 실행값을 사용해 운영 파이프라인을 격리 출력으로 실행한다."""
    neo4j_root = Path(__file__).resolve().parent
    default_policy_path = neo4j_root / "config" / "resolution_policy.json"
    parser = ArgumentParser(
        description="한국사 Neo4j 전처리 파이프라인 소량 테스트 실행"
    )
    parser.add_argument("--exam-json", default="", help="기출문제 JSON 경로")
    parser.add_argument(
        "--thesaurus-csv",
        default="",
        help="한국역사용어시소러스 CSV 경로",
    )
    parser.add_argument(
        "--encyclopedia-jsonl",
        default="",
        help="AKS 백과사전 상세 JSONL 경로",
    )
    parser.add_argument(
        "--itkc-people",
        default="",
        help="ITKC 인물 CSV 경로",
    )
    parser.add_argument(
        "--itkc-events",
        default="",
        help="ITKC 사건 CSV 경로",
    )
    parser.add_argument(
        "--output-subdirectory",
        default="",
        help="etl/preprocessing/neo4j/output 아래 테스트 하위 폴더명",
    )
    parser.add_argument("--limit", type=int, default=None, help="테스트 문항 수")
    parser.add_argument(
        "--batch-size",
        type=int,
        default=None,
        help="LLM 호출당 문항 수",
    )
    parser.add_argument(
        "--retries",
        type=int,
        default=None,
        help="배치 실패 시 재시도 횟수",
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=None,
        help="시소러스 사용 판정 커버리지 임계치",
    )
    parser.add_argument(
        "--display-limit",
        type=int,
        default=None,
        help="보고서 표시 개수",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="LLM을 호출하지 않고 실제 사용할 경로와 설정만 확인",
    )
    parser.add_argument(
        "--policy",
        default=str(default_policy_path),
        help="용어 추출·후보 생성·검증 정책 JSON 경로",
    )
    cli_args = parser.parse_args()

    pipeline_policy = load_pipeline_policy(cli_args.policy)
    if "test_run" not in pipeline_policy:
        raise KeyError("resolution policy에 test_run 설정이 없습니다.")
    test_policy = pipeline_policy["test_run"]

    output_subdirectory = str(test_policy["output_subdirectory"])
    if cli_args.output_subdirectory:
        output_subdirectory = cli_args.output_subdirectory
    output_directory = resolve_test_output_directory(
        neo4j_root,
        output_subdirectory,
    )

    limit = int(test_policy["limit"])
    if cli_args.limit is not None:
        limit = cli_args.limit
    if limit <= 0:
        raise ValueError("테스트 runner의 문항 수는 1 이상이어야 합니다.")

    batch_size = int(test_policy["batch_size"])
    if cli_args.batch_size is not None:
        batch_size = cli_args.batch_size
    max_retries = int(test_policy["max_retries"])
    if cli_args.retries is not None:
        max_retries = cli_args.retries
    threshold = float(test_policy["coverage_threshold"])
    if cli_args.threshold is not None:
        threshold = cli_args.threshold
    display_limit = int(test_policy["display_limit"])
    if cli_args.display_limit is not None:
        display_limit = cli_args.display_limit

    pipeline_paths = resolve_pipeline_paths(
        exam_json_path=cli_args.exam_json,
        thesaurus_csv_path=cli_args.thesaurus_csv,
        output_dir=output_directory,
        encyclopedia_jsonl_path=cli_args.encyclopedia_jsonl,
        itkc_people_csv_path=cli_args.itkc_people,
        itkc_events_csv_path=cli_args.itkc_events,
    )
    execution_settings = {
        **pipeline_paths,
        "limit": limit,
        "batch_size": batch_size,
        "max_retries": max_retries,
        "threshold": threshold,
        "display_limit": display_limit,
        "policy_path": str(Path(cli_args.policy).resolve()),
    }
    print("Neo4j 전처리 소량 테스트 설정")
    print(dumps(execution_settings, ensure_ascii=False, indent=2))
    if cli_args.dry_run:
        print("dry-run 완료: LLM 호출과 파일 생성은 수행하지 않았습니다.")
        return

    run_preprocessing_pipeline(
        exam_json_path=pipeline_paths["exam_json_path"],
        thesaurus_csv_path=pipeline_paths["thesaurus_csv_path"],
        output_dir=pipeline_paths["output_dir"],
        encyclopedia_jsonl_path=pipeline_paths[
            "encyclopedia_jsonl_path"
        ],
        itkc_people_csv_path=pipeline_paths["itkc_people_csv_path"],
        itkc_events_csv_path=pipeline_paths["itkc_events_csv_path"],
        batch_size=batch_size,
        limit=limit,
        max_retries=max_retries,
        threshold=threshold,
        display_limit=display_limit,
        policy_path=cli_args.policy,
    )


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    main()
