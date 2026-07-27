import _bootstrap

from argparse import ArgumentParser, Namespace
from datetime import datetime, timezone
from json import dump, dumps
from pathlib import Path

import pandas as pd

from source_relationships.build import load_source_relationship_policy
from source_relationships.description_facts import (
    build_description_fact_tables,
)


def parse_arguments() -> Namespace:
    """공식 설명문 관계 전처리 CLI 인자를 읽는다."""
    neo4j_root = Path(__file__).resolve().parent.parent
    parser = ArgumentParser(
        description=(
            "AKS·시소러스 공식 설명문에서 CanonicalEntity "
            "직접 사실 관계를 추출합니다."
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
        "--source-candidates",
        default=str(
            neo4j_root
            / "output"
            / "internal"
            / "entity_resolution"
            / "candidate_source_records.csv"
        ),
    )
    parser.add_argument(
        "--output-dir",
        default=str(neo4j_root / "output" / "source_relationships"),
    )
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def run_description_fact_preprocessing(
    cli_args: Namespace,
) -> dict[str, object]:
    """설명문 언급 후보와 패턴 확정 관계 CSV를 생성한다."""
    policy = load_source_relationship_policy(cli_args.config)
    registry_path = Path(cli_args.canonical_registry)
    candidate_path = Path(cli_args.source_candidates)
    output_directory = Path(cli_args.output_dir)
    missing_inputs = [
        str(path)
        for path in [registry_path, candidate_path]
        if not path.is_file()
    ]
    if missing_inputs:
        raise FileNotFoundError(
            "설명문 관계 입력 파일이 없습니다: "
            + ", ".join(missing_inputs)
        )
    if cli_args.dry_run:
        return {
            "status": "READY",
            "stage": "DESCRIPTION_FACT_PREPROCESSING",
            "dry_run": True,
            "canonical_registry": str(registry_path),
            "source_candidates": str(candidate_path),
            "output_directory": str(output_directory),
        }

    canonical_registry = pd.read_csv(
        registry_path,
        dtype=str,
    ).fillna("")
    source_candidates = pd.read_csv(
        candidate_path,
        dtype=str,
    ).fillna("")
    tables = build_description_fact_tables(
        canonical_registry,
        source_candidates,
        policy,
    )
    output_directory.mkdir(parents=True, exist_ok=True)
    output_paths: dict[str, str] = {}
    for table_name, table in tables.items():
        output_path = (
            output_directory / policy["outputs"][table_name]
        )
        table.to_csv(output_path, index=False, encoding="utf-8-sig")
        output_paths[table_name] = str(output_path)

    relationships = tables["description_canonical_relationships"]
    mentions = tables["description_mention_candidates"]
    manifest = {
        "status": "COMPLETED",
        "stage": "DESCRIPTION_FACT_PREPROCESSING",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "policy_version": policy["description_projection"][
            "policy_version"
        ],
        "canonical_entity_count": len(canonical_registry),
        "mention_candidate_count": len(mentions),
        "pattern_asserted_mention_count": int(
            mentions["proposed_relation_type"].ne("").sum()
        ),
        "review_candidate_count": len(
            tables["description_relation_review"]
        ),
        "canonical_relationship_count": len(relationships),
        "relation_type_counts": {
            str(relation_type): int(count)
            for relation_type, count in relationships[
                "relation_type"
            ].value_counts().items()
        },
        "output_paths": output_paths,
    }
    manifest_path = (
        output_directory
        / policy["outputs"]["description_manifest"]
    )
    with manifest_path.open(
        "w",
        encoding="utf-8",
    ) as manifest_file:
        dump(manifest, manifest_file, ensure_ascii=False, indent=2)
    manifest["output_paths"]["manifest"] = str(manifest_path)
    return manifest


def main() -> None:
    """CLI 실행 결과를 JSON으로 출력한다."""
    result = run_description_fact_preprocessing(parse_arguments())
    print(dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
