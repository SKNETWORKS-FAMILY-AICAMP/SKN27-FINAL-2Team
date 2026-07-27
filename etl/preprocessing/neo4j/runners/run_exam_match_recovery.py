import _bootstrap

from argparse import ArgumentParser, Namespace
from datetime import datetime, timezone
from json import dump, dumps
from pathlib import Path

import pandas as pd

from entity_resolution.exam_match_recovery import (
    build_exam_match_recovery_tables,
    load_exam_match_recovery_policy,
)


def parse_arguments() -> Namespace:
    """기출 용어 재매칭 dry-run CLI 인자를 읽는다."""
    neo4j_root = Path(__file__).resolve().parent.parent
    final_directory = neo4j_root / "output" / "final_identity"
    internal_directory = (
        neo4j_root / "output" / "internal" / "entity_resolution"
    )
    fact_directory = neo4j_root / "output" / "source_relationships"
    parser = ArgumentParser(
        description=(
            "공식 정확명과 문항 시대 문맥으로 미매칭 기출 용어를 "
            "재판정하고 사실 그래프 커버리지를 계산합니다. "
            "LLM 호출과 Neo4j 적재는 하지 않습니다."
        )
    )
    parser.add_argument(
        "--config",
        default=str(
            neo4j_root / "config" / "exam_match_recovery.json"
        ),
    )
    parser.add_argument(
        "--resolution-cases",
        default=str(internal_directory / "entity_cases.csv"),
    )
    parser.add_argument(
        "--source-candidates",
        default=str(internal_directory / "candidate_source_records.csv"),
    )
    parser.add_argument(
        "--canonical-registry",
        default=str(final_directory / "canonical_entity_registry.csv"),
    )
    parser.add_argument(
        "--final-assignments",
        default=str(
            final_directory / "exam_problem_entity_assignments_final.csv"
        ),
    )
    parser.add_argument(
        "--problem-contexts",
        default=str(internal_directory / "exam_problem_contexts.csv"),
    )
    parser.add_argument(
        "--canonical-eras",
        default=str(
            final_directory / "neo4j_canonical_to_era_relationships.csv"
        ),
    )
    parser.add_argument(
        "--exam-term-nodes",
        default=str(final_directory / "neo4j_exam_term_nodes.csv"),
    )
    parser.add_argument(
        "--exam-term-relationships",
        default=str(
            final_directory
            / "neo4j_exam_term_to_entity_relationships.csv"
        ),
    )
    parser.add_argument(
        "--current-facts",
        default=str(fact_directory / "canonical_fact_relationships.csv"),
    )
    parser.add_argument(
        "--staged-facts",
        default=str(
            fact_directory
            / "aks_attribute_dry_run"
            / "aks_attribute_canonical_relationships.csv"
        ),
    )
    parser.add_argument(
        "--output-dir",
        default=str(neo4j_root / "output" / "exam_match_recovery"),
    )
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def run_exam_match_recovery(cli_args: Namespace) -> dict[str, object]:
    """재매칭 후보와 예상 커버리지 파일을 생성한다."""
    policy = load_exam_match_recovery_policy(cli_args.config)
    input_paths = {
        "resolution_cases": Path(cli_args.resolution_cases),
        "source_candidates": Path(cli_args.source_candidates),
        "canonical_registry": Path(cli_args.canonical_registry),
        "final_assignments": Path(cli_args.final_assignments),
        "problem_contexts": Path(cli_args.problem_contexts),
        "canonical_eras": Path(cli_args.canonical_eras),
        "exam_term_nodes": Path(cli_args.exam_term_nodes),
        "exam_term_relationships": Path(
            cli_args.exam_term_relationships
        ),
        "current_facts": Path(cli_args.current_facts),
        "staged_facts": Path(cli_args.staged_facts),
    }
    missing_inputs = [
        str(path) for path in input_paths.values() if not path.is_file()
    ]
    if missing_inputs:
        raise FileNotFoundError(
            "기출 용어 재매칭 입력 파일이 없습니다: "
            + ", ".join(missing_inputs)
        )
    output_directory = Path(cli_args.output_dir)
    if cli_args.dry_run:
        return {
            "status": "READY",
            "stage": "EXAM_MATCH_RECOVERY",
            "dry_run": True,
            "llm_used": False,
            "neo4j_load": False,
            "input_paths": {
                name: str(path) for name, path in input_paths.items()
            },
            "output_directory": str(output_directory),
        }

    tables = {
        name: pd.read_csv(path, dtype=str).fillna("")
        for name, path in input_paths.items()
    }
    output_tables, statistics = build_exam_match_recovery_tables(
        tables["resolution_cases"],
        tables["source_candidates"],
        tables["canonical_registry"],
        tables["final_assignments"],
        tables["problem_contexts"],
        tables["canonical_eras"],
        tables["exam_term_nodes"],
        tables["exam_term_relationships"],
        tables["current_facts"],
        tables["staged_facts"],
        policy,
    )
    output_directory.mkdir(parents=True, exist_ok=True)
    recovery_policy = policy["exam_match_recovery"]
    output_paths: dict[str, str] = {}
    for table_name, table in output_tables.items():
        output_path = (
            output_directory
            / recovery_policy["outputs"][table_name]
        )
        table.to_csv(output_path, index=False, encoding="utf-8-sig")
        output_paths[table_name] = str(output_path)

    manifest = {
        "status": "COMPLETED",
        "stage": "EXAM_MATCH_RECOVERY",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "policy_version": recovery_policy["policy_version"],
        "llm_used": False,
        "neo4j_load": False,
        "statistics": statistics,
        "output_paths": output_paths,
    }
    manifest_path = (
        output_directory / recovery_policy["outputs"]["manifest"]
    )
    with manifest_path.open("w", encoding="utf-8") as manifest_file:
        dump(manifest, manifest_file, ensure_ascii=False, indent=2)
    manifest["output_paths"]["manifest"] = str(manifest_path)
    return manifest


def main() -> None:
    """재매칭 결과를 JSON으로 출력한다."""
    result = run_exam_match_recovery(parse_arguments())
    print(dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
