from __future__ import annotations

from argparse import ArgumentParser, Namespace
from datetime import datetime, timezone
from json import dump, dumps
from pathlib import Path
import sys

import pandas as pd

sys.path.append(str(Path(__file__).resolve().parent))

from choice_relation.official_corroboration import (
    build_exam_relation_official_corroboration_tables,
    load_exam_relation_official_policy,
)


def parse_arguments() -> Namespace:
    """기출 관계 공식 검증 CLI 인자를 읽는다."""
    neo4j_root = Path(__file__).resolve().parent
    candidate_directory = (
        neo4j_root / "output" / "exam_relation_candidates"
    )
    source_relationship_directory = (
        neo4j_root / "output" / "source_relationships"
    )
    final_identity_directory = (
        neo4j_root / "output" / "final_identity"
    )
    parser = ArgumentParser(
        description=(
            "기출의 참 관계 후보를 기존 공식 사실 관계와 대조합니다. "
            "새 사실 생성, LLM 호출, Neo4j 적재는 하지 않습니다."
        )
    )
    parser.add_argument(
        "--config",
        default=str(
            neo4j_root / "config" / "exam_relation_candidates.json"
        ),
    )
    parser.add_argument(
        "--relation-candidates",
        default=str(
            candidate_directory / "exam_relation_candidates.csv"
        ),
    )
    parser.add_argument(
        "--canonical-registry",
        default=str(
            final_identity_directory / "canonical_entity_registry.csv"
        ),
    )
    parser.add_argument(
        "--canonical-facts",
        default=str(
            source_relationship_directory
            / "canonical_fact_relationships.csv"
        ),
    )
    parser.add_argument(
        "--output-dir",
        default=str(
            neo4j_root / "output"
            / "exam_relation_official_corroboration"
        ),
    )
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def run_exam_relation_official_corroboration(
    cli_args: Namespace,
) -> dict[str, object]:
    """기출 관계 후보와 기존 공식 사실의 연결 감사표를 생성한다."""
    policy = load_exam_relation_official_policy(cli_args.config)
    input_paths = {
        "relation_candidates": Path(cli_args.relation_candidates),
        "canonical_registry": Path(cli_args.canonical_registry),
        "canonical_facts": Path(cli_args.canonical_facts),
    }
    missing_inputs = [
        str(path) for path in input_paths.values() if not path.is_file()
    ]
    if missing_inputs:
        raise FileNotFoundError(
            "기출 관계 공식 검증 입력 파일이 없습니다: "
            + ", ".join(missing_inputs)
        )
    output_directory = Path(cli_args.output_dir)
    if cli_args.dry_run:
        return {
            "status": "READY",
            "stage": "EXAM_RELATION_OFFICIAL_CORROBORATION",
            "dry_run": True,
            "llm_used": False,
            "neo4j_load": False,
            "creates_new_fact": False,
            "input_paths": {
                name: str(path) for name, path in input_paths.items()
            },
            "output_directory": str(output_directory),
        }

    relation_candidates = pd.read_csv(
        input_paths["relation_candidates"],
        dtype=str,
    ).fillna("")
    canonical_registry = pd.read_csv(
        input_paths["canonical_registry"],
        dtype=str,
    ).fillna("")
    canonical_facts = pd.read_csv(
        input_paths["canonical_facts"],
        dtype=str,
    ).fillna("")
    tables, statistics = (
        build_exam_relation_official_corroboration_tables(
            relation_candidates,
            canonical_registry,
            canonical_facts,
            policy,
        )
    )
    output_directory.mkdir(parents=True, exist_ok=True)
    corroboration_policy = policy[
        "exam_relation_official_corroboration"
    ]
    output_paths: dict[str, str] = {}
    for table_name, table in tables.items():
        output_path = (
            output_directory
            / corroboration_policy["outputs"][table_name]
        )
        table.to_csv(output_path, index=False, encoding="utf-8-sig")
        output_paths[table_name] = str(output_path)

    manifest = {
        "status": "COMPLETED",
        "stage": "EXAM_RELATION_OFFICIAL_CORROBORATION",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "policy_version": corroboration_policy["policy_version"],
        "llm_used": False,
        "neo4j_load": False,
        "creates_new_fact": False,
        "statistics": statistics,
        "output_paths": output_paths,
    }
    manifest_path = (
        output_directory / corroboration_policy["outputs"]["manifest"]
    )
    with manifest_path.open("w", encoding="utf-8") as manifest_file:
        dump(manifest, manifest_file, ensure_ascii=False, indent=2)
    manifest["output_paths"]["manifest"] = str(manifest_path)
    return manifest


def main() -> None:
    """기출 관계 공식 검증 결과를 JSON으로 출력한다."""
    result = run_exam_relation_official_corroboration(
        parse_arguments()
    )
    print(dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
