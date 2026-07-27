from __future__ import annotations

import _bootstrap

from argparse import ArgumentParser, Namespace
from datetime import datetime, timezone
from json import dump, dumps
from pathlib import Path

import pandas as pd

from choice_relation.source_layer_expansion_eda import (
    build_source_layer_expansion_tables,
    load_source_layer_expansion_policy,
)


def parse_arguments() -> Namespace:
    """소스 레이어 확장 안전성 EDA 실행 인자를 읽는다."""
    neo4j_root = Path(__file__).resolve().parent.parent
    parser = ArgumentParser(
        description=(
            "미해결 공식 소스 endpoint를 사실로 승격하지 않고 "
            "검색 후보로만 확장해 충돌과 커버리지를 측정합니다."
        )
    )
    parser.add_argument(
        "--eda-config",
        default=str(
            neo4j_root
            / "config"
            / "source_layer_expansion_eda.json"
        ),
    )
    parser.add_argument(
        "--retrieval-config",
        default=str(
            neo4j_root / "config" / "fact_retrieval.json"
        ),
    )
    parser.add_argument(
        "--resolution-config",
        default=str(
            neo4j_root / "config" / "entity_resolution.json"
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
        "--anchor-nodes",
        default=str(
            neo4j_root
            / "output"
            / "fact_retrieval"
            / "entity_anchor_nodes.csv"
        ),
    )
    parser.add_argument(
        "--anchor-facts",
        default=str(
            neo4j_root
            / "output"
            / "fact_retrieval"
            / "anchor_fact_relationships.csv"
        ),
    )
    parser.add_argument(
        "--source-nodes",
        default=str(
            neo4j_root
            / "output"
            / "source_relationships"
            / "source_record_nodes.csv"
        ),
    )
    parser.add_argument(
        "--exam-term-links",
        default=str(
            neo4j_root
            / "output"
            / "final_identity"
            / "neo4j_exam_term_to_entity_relationships.csv"
        ),
    )
    parser.add_argument(
        "--existing-swap-candidates",
        default=str(
            neo4j_root
            / "output"
            / "fact_retrieval"
            / "rag_swap_candidates.csv"
        ),
    )
    parser.add_argument(
        "--output-dir",
        default=str(
            neo4j_root
            / "output"
            / "source_layer_expansion_eda"
        ),
    )
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def run_source_layer_expansion_eda(
    cli_args: Namespace,
) -> dict[str, object]:
    """격리 EDA를 실행하고 CSV와 manifest를 저장한다."""
    policy = load_source_layer_expansion_policy(
        cli_args.eda_config,
        cli_args.retrieval_config,
        cli_args.resolution_config,
    )
    input_paths = {
        "canonical_registry": Path(cli_args.canonical_registry),
        "canonical_facts": Path(cli_args.canonical_facts),
        "anchor_nodes": Path(cli_args.anchor_nodes),
        "anchor_facts": Path(cli_args.anchor_facts),
        "source_nodes": Path(cli_args.source_nodes),
        "exam_term_links": Path(cli_args.exam_term_links),
        "existing_swap_candidates": Path(
            cli_args.existing_swap_candidates
        ),
    }
    missing_inputs = [
        str(path) for path in input_paths.values() if not path.is_file()
    ]
    if missing_inputs:
        raise FileNotFoundError(
            "소스 레이어 확장 EDA 입력 파일이 없습니다: "
            + ", ".join(missing_inputs)
        )
    output_directory = Path(cli_args.output_dir)
    if cli_args.dry_run:
        return {
            "status": "READY",
            "stage": "SOURCE_LAYER_EXPANSION_EDA",
            "dry_run": True,
            "llm_used": False,
            "neo4j_load": False,
            "input_paths": {
                name: str(path) for name, path in input_paths.items()
            },
            "output_directory": str(output_directory),
        }
    tables, statistics = build_source_layer_expansion_tables(
        pd.read_csv(
            input_paths["canonical_registry"],
            dtype=str,
        ).fillna(""),
        pd.read_csv(
            input_paths["canonical_facts"],
            dtype=str,
        ).fillna(""),
        pd.read_csv(
            input_paths["anchor_nodes"],
            dtype=str,
        ).fillna(""),
        pd.read_csv(
            input_paths["anchor_facts"],
            dtype=str,
        ).fillna(""),
        pd.read_csv(
            input_paths["source_nodes"],
            dtype=str,
        ).fillna(""),
        pd.read_csv(
            input_paths["exam_term_links"],
            dtype=str,
        ).fillna(""),
        pd.read_csv(
            input_paths["existing_swap_candidates"],
            dtype=str,
        ).fillna(""),
        policy,
    )
    output_directory.mkdir(parents=True, exist_ok=True)
    eda_policy = policy["source_layer_expansion_eda"]
    output_paths: dict[str, str] = {}
    for table_name, table in tables.items():
        output_path = (
            output_directory / eda_policy["outputs"][table_name]
        )
        table.to_csv(output_path, index=False, encoding="utf-8-sig")
        output_paths[table_name] = str(output_path)
    manifest = {
        "status": "COMPLETED",
        "stage": "SOURCE_LAYER_EXPANSION_EDA",
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
    result = run_source_layer_expansion_eda(parse_arguments())
    print(dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
