from __future__ import annotations

from argparse import ArgumentParser, Namespace
from datetime import datetime, timezone
from json import dump, dumps
from pathlib import Path
import sys

import pandas as pd

sys.path.append(str(Path(__file__).resolve().parent))

from choice_relation.analysis import load_problem_records
from choice_relation.deterministic_candidates import (
    build_exam_relation_candidate_tables,
    load_exam_relation_candidate_policy,
)


def parse_arguments() -> Namespace:
    """기출 관계 후보 전처리 CLI 인자를 읽는다."""
    neo4j_root = Path(__file__).resolve().parent
    project_root = neo4j_root.parents[2]
    internal_directory = (
        neo4j_root / "output" / "internal" / "entity_resolution"
    )
    final_directory = neo4j_root / "output" / "final_identity"
    parser = ArgumentParser(
        description=(
            "제시문과 모든 선지를 관계 claim으로 보존하고, "
            "문항 내 진릿값과 코드 기반 관계 후보를 생성합니다. "
            "LLM 호출과 Neo4j 적재는 하지 않습니다."
        )
    )
    parser.add_argument(
        "--config",
        default=str(
            neo4j_root / "config" / "exam_relation_candidates.json"
        ),
    )
    parser.add_argument(
        "--problems",
        default=str(project_root / "ai" / "ml" / "ML_han_v1.json"),
    )
    parser.add_argument(
        "--resolution-cases",
        default=str(internal_directory / "entity_cases.csv"),
    )
    parser.add_argument(
        "--final-assignments",
        default=str(
            final_directory / "exam_problem_entity_assignments_final.csv"
        ),
    )
    parser.add_argument(
        "--output-dir",
        default=str(neo4j_root / "output" / "exam_relation_candidates"),
    )
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def run_exam_relation_candidate_preprocessing(
    cli_args: Namespace,
) -> dict[str, object]:
    """기출 관계 claim과 후보 CSV를 생성한다."""
    policy = load_exam_relation_candidate_policy(cli_args.config)
    input_paths = {
        "problems": Path(cli_args.problems),
        "resolution_cases": Path(cli_args.resolution_cases),
        "final_assignments": Path(cli_args.final_assignments),
    }
    missing_inputs = [
        str(path) for path in input_paths.values() if not path.is_file()
    ]
    if missing_inputs:
        raise FileNotFoundError(
            "기출 관계 후보 입력 파일이 없습니다: "
            + ", ".join(missing_inputs)
        )
    output_directory = Path(cli_args.output_dir)
    if cli_args.dry_run:
        return {
            "status": "READY",
            "stage": "EXAM_RELATION_CANDIDATE_PREPROCESSING",
            "dry_run": True,
            "llm_used": False,
            "neo4j_load": False,
            "input_paths": {
                name: str(path) for name, path in input_paths.items()
            },
            "output_directory": str(output_directory),
        }

    problem_records = load_problem_records(str(input_paths["problems"]))
    resolution_cases = pd.read_csv(
        input_paths["resolution_cases"],
        dtype=str,
    ).fillna("")
    final_assignments = pd.read_csv(
        input_paths["final_assignments"],
        dtype=str,
    ).fillna("")
    tables, statistics = build_exam_relation_candidate_tables(
        problem_records,
        resolution_cases,
        final_assignments,
        policy,
    )
    output_directory.mkdir(parents=True, exist_ok=True)
    candidate_policy = policy["exam_relation_candidates"]
    output_paths: dict[str, str] = {}
    for table_name, table in tables.items():
        output_path = (
            output_directory
            / candidate_policy["outputs"][table_name]
        )
        table.to_csv(output_path, index=False, encoding="utf-8-sig")
        output_paths[table_name] = str(output_path)

    manifest = {
        "status": "COMPLETED",
        "stage": "EXAM_RELATION_CANDIDATE_PREPROCESSING",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "policy_version": candidate_policy["policy_version"],
        "llm_used": False,
        "neo4j_load": False,
        "statistics": statistics,
        "output_paths": output_paths,
    }
    manifest_path = (
        output_directory / candidate_policy["outputs"]["manifest"]
    )
    with manifest_path.open("w", encoding="utf-8") as manifest_file:
        dump(manifest, manifest_file, ensure_ascii=False, indent=2)
    manifest["output_paths"]["manifest"] = str(manifest_path)
    return manifest


def main() -> None:
    """기출 관계 후보 처리 결과를 JSON으로 출력한다."""
    result = run_exam_relation_candidate_preprocessing(
        parse_arguments()
    )
    print(dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
