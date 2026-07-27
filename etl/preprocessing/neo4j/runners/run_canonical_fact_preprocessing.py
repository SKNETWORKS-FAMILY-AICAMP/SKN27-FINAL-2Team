import _bootstrap

from argparse import ArgumentParser, Namespace
from datetime import datetime, timezone
from json import dump, dumps
from pathlib import Path

import pandas as pd

from source_relationships.build import load_source_relationship_policy
from source_relationships.canonical_facts import (
    build_canonical_fact_relationships,
)


def parse_arguments() -> Namespace:
    """Canonical 사실 관계 통합 CLI 인자를 읽는다."""
    neo4j_root = Path(__file__).resolve().parent.parent
    output_directory = neo4j_root / "output" / "source_relationships"
    parser = ArgumentParser(
        description=(
            "구조화 원천 관계와 공식 설명문 관계를 통합합니다. "
            "Neo4j에는 적재하지 않습니다."
        )
    )
    parser.add_argument(
        "--config",
        default=str(neo4j_root / "config" / "source_relationships.json"),
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
        "--structured-relationships",
        default=str(
            output_directory / "canonical_entity_relationships.csv"
        ),
    )
    parser.add_argument(
        "--description-relationships",
        default=str(
            output_directory / "description_canonical_relationships.csv"
        ),
    )
    parser.add_argument(
        "--output-dir",
        default=str(output_directory),
    )
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def run_canonical_fact_preprocessing(
    cli_args: Namespace,
) -> dict[str, object]:
    """두 종류의 직접 사실 관계를 병합하고 manifest를 쓴다."""
    policy = load_source_relationship_policy(cli_args.config)
    registry_path = Path(cli_args.canonical_registry)
    structured_path = Path(cli_args.structured_relationships)
    description_path = Path(cli_args.description_relationships)
    output_directory = Path(cli_args.output_dir)
    input_paths = [
        registry_path,
        structured_path,
        description_path,
    ]
    missing_inputs = [
        str(path) for path in input_paths if not path.is_file()
    ]
    if missing_inputs:
        raise FileNotFoundError(
            "Canonical 사실 관계 입력이 없습니다: "
            + ", ".join(missing_inputs)
        )
    if cli_args.dry_run:
        return {
            "status": "READY",
            "stage": "CANONICAL_FACT_PREPROCESSING",
            "dry_run": True,
            "input_paths": [str(path) for path in input_paths],
            "output_directory": str(output_directory),
        }

    canonical_registry = pd.read_csv(
        registry_path,
        dtype=str,
    ).fillna("")
    structured_relationships = pd.read_csv(
        structured_path,
        dtype=str,
    ).fillna("")
    description_relationships = pd.read_csv(
        description_path,
        dtype=str,
    ).fillna("")
    facts = build_canonical_fact_relationships(
        structured_relationships,
        description_relationships,
        canonical_registry,
        policy,
    )
    output_directory.mkdir(parents=True, exist_ok=True)
    output_path = (
        output_directory
        / policy["outputs"]["canonical_fact_relationships"]
    )
    facts.to_csv(output_path, index=False, encoding="utf-8-sig")

    classification_types = set(
        policy["canonical_fact_projection"][
            "classification_relation_types"
        ]
    )
    endpoint_ids = set(facts["start_canonical_id"]).union(
        facts["end_canonical_id"]
    )
    manifest = {
        "status": "COMPLETED",
        "stage": "CANONICAL_FACT_PREPROCESSING",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "policy_version": policy["canonical_fact_projection"][
            "policy_version"
        ],
        "structured_input_count": len(structured_relationships),
        "description_input_count": len(description_relationships),
        "canonical_fact_count": len(facts),
        "core_fact_count": int(
            (~facts["relation_type"].isin(classification_types)).sum()
        ),
        "classification_fact_count": int(
            facts["relation_type"].isin(classification_types).sum()
        ),
        "connected_canonical_entity_count": len(endpoint_ids),
        "relation_type_counts": {
            str(relation_type): int(count)
            for relation_type, count in facts[
                "relation_type"
            ].value_counts().items()
        },
        "output_path": str(output_path),
    }
    manifest_path = (
        output_directory
        / policy["outputs"]["canonical_fact_manifest"]
    )
    with manifest_path.open("w", encoding="utf-8") as manifest_file:
        dump(manifest, manifest_file, ensure_ascii=False, indent=2)
    manifest["manifest_path"] = str(manifest_path)
    return manifest


def main() -> None:
    """Canonical 사실 관계 통합 결과를 JSON으로 출력한다."""
    result = run_canonical_fact_preprocessing(parse_arguments())
    print(dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
