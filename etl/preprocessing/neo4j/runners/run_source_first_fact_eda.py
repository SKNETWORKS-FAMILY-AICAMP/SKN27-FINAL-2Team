from __future__ import annotations

import _bootstrap

from argparse import ArgumentParser, Namespace
from datetime import datetime, timezone
from json import dump, dumps
from pathlib import Path

import pandas as pd

from choice_relation.source_first_fact_eda import (
    build_source_first_fact_eda_tables,
    load_source_first_fact_policy,
)


def parse_arguments() -> Namespace:
    """원천 중심 사실 EDA 인자를 읽는다."""
    neo4j_root = Path(__file__).resolve().parent.parent
    project_root = neo4j_root.parents[2]
    parser = ArgumentParser(
        description=(
            "ITKC 기존 사실, AKS EID 문장, 용어집 표제어에서 "
            "원천 중심 관계 후보를 조사합니다. LLM 호출과 Neo4j "
            "적재는 하지 않습니다."
        )
    )
    parser.add_argument(
        "--eda-config",
        default=str(
            neo4j_root / "config" / "source_first_fact_eda.json"
        ),
    )
    parser.add_argument(
        "--relation-config",
        default=str(
            neo4j_root / "config" / "exam_relation_candidates.json"
        ),
    )
    parser.add_argument(
        "--canonical-registry",
        default=str(
            neo4j_root
            / "output"
            / "final_identity"
            / "canonical_entity_registry.csv"
        ),
    )
    parser.add_argument(
        "--canonical-facts",
        default=str(
            neo4j_root
            / "output"
            / "source_relationships"
            / "canonical_fact_relationships.csv"
        ),
    )
    parser.add_argument(
        "--exam-term-matches",
        default=str(
            neo4j_root
            / "output"
            / "exam_match_recovery"
            / "exam_term_match_recovery.csv"
        ),
    )
    parser.add_argument(
        "--aks-details",
        default=str(
            project_root
            / "etl"
            / "raw_data"
            / "한국민족문화대백과사전"
            / "articles_detail.jsonl"
        ),
    )
    parser.add_argument(
        "--thesaurus",
        default=str(
            neo4j_root
            / "output"
            / "internal"
            / "shared"
            / "normalized_history_thesaurus.json"
        ),
    )
    parser.add_argument(
        "--output-dir",
        default=str(
            neo4j_root / "output" / "source_first_fact_eda"
        ),
    )
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def run_source_first_fact_eda(
    cli_args: Namespace,
) -> dict[str, object]:
    """원천 중심 EDA를 실행하고 CSV·manifest를 저장한다."""
    policy = load_source_first_fact_policy(
        cli_args.eda_config,
        cli_args.relation_config,
    )
    input_paths = {
        "canonical_registry": Path(cli_args.canonical_registry),
        "canonical_facts": Path(cli_args.canonical_facts),
        "exam_term_matches": Path(cli_args.exam_term_matches),
        "aks_details": Path(cli_args.aks_details),
        "thesaurus": Path(cli_args.thesaurus),
    }
    missing_inputs = [
        str(path) for path in input_paths.values() if not path.is_file()
    ]
    if missing_inputs:
        raise FileNotFoundError(
            "원천 중심 EDA 입력 파일이 없습니다: "
            + ", ".join(missing_inputs)
        )
    output_directory = Path(cli_args.output_dir)
    if cli_args.dry_run:
        return {
            "status": "READY",
            "stage": "SOURCE_FIRST_FACT_EDA",
            "dry_run": True,
            "llm_used": False,
            "neo4j_load": False,
            "input_paths": {
                name: str(path) for name, path in input_paths.items()
            },
            "output_directory": str(output_directory),
        }
    canonical_registry = pd.read_csv(
        input_paths["canonical_registry"],
        dtype=str,
    ).fillna("")
    canonical_facts = pd.read_csv(
        input_paths["canonical_facts"],
        dtype=str,
    ).fillna("")
    exam_term_matches = pd.read_csv(
        input_paths["exam_term_matches"],
        dtype=str,
    ).fillna("")
    tables, statistics = build_source_first_fact_eda_tables(
        canonical_registry,
        canonical_facts,
        exam_term_matches,
        str(input_paths["aks_details"]),
        str(input_paths["thesaurus"]),
        policy,
    )
    output_directory.mkdir(parents=True, exist_ok=True)
    eda_policy = policy["source_first_fact_eda"]
    output_paths: dict[str, str] = {}
    for table_name, table in tables.items():
        output_path = (
            output_directory / eda_policy["outputs"][table_name]
        )
        table.to_csv(output_path, index=False, encoding="utf-8-sig")
        output_paths[table_name] = str(output_path)
    manifest = {
        "status": "COMPLETED",
        "stage": "SOURCE_FIRST_FACT_EDA",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "policy_version": str(eda_policy["policy_version"]),
        "llm_used": False,
        "neo4j_load": False,
        "statistics": statistics,
        "output_paths": output_paths,
    }
    manifest_path = (
        output_directory / eda_policy["outputs"]["manifest"]
    )
    with manifest_path.open("w", encoding="utf-8") as output_file:
        dump(manifest, output_file, ensure_ascii=False, indent=2)
    manifest["output_paths"]["manifest"] = str(manifest_path)
    return manifest


def main() -> None:
    """CLI 실행 결과를 JSON으로 출력한다."""
    result = run_source_first_fact_eda(parse_arguments())
    print(dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
