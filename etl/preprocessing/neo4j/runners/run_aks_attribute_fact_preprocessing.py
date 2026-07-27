import _bootstrap

from argparse import ArgumentParser, Namespace
from datetime import datetime, timezone
from json import dump, dumps
from pathlib import Path

import pandas as pd

from source_relationships.aks_attributes import (
    build_aks_attribute_tables,
)
from source_relationships.build import load_source_relationship_policy


def parse_arguments() -> Namespace:
    """AKS 구조화 속성 관계 후보 생성 CLI 인자를 읽는다."""
    neo4j_root = Path(__file__).resolve().parent.parent
    project_root = neo4j_root.parents[2]
    parser = ArgumentParser(
        description=(
            "한국민족문화대백과의 구조화 속성을 CanonicalEntity "
            "관계 후보로 변환합니다. Neo4j에는 적재하지 않습니다."
        )
    )
    parser.add_argument(
        "--config",
        default=str(
            neo4j_root / "config" / "source_relationships.json"
        ),
    )
    parser.add_argument(
        "--articles",
        default=str(
            project_root
            / "etl"
            / "raw_data"
            / "한국민족문화대백과사전"
            / "articles_detail.jsonl"
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
        "--source-resolutions",
        default=str(
            neo4j_root
            / "output"
            / "final_identity"
            / "neo4j_source_to_entity_relationships.csv"
        ),
    )
    parser.add_argument(
        "--output-dir",
        default=str(
            neo4j_root / "output" / "source_relationships"
        ),
    )
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def run_aks_attribute_fact_preprocessing(
    cli_args: Namespace,
) -> dict[str, object]:
    """AKS 속성 관계 후보 CSV와 감사용 manifest를 생성한다."""
    policy = load_source_relationship_policy(cli_args.config)
    articles_path = Path(cli_args.articles)
    registry_path = Path(cli_args.canonical_registry)
    source_resolutions_path = Path(cli_args.source_resolutions)
    output_directory = Path(cli_args.output_dir)
    input_paths = [
        articles_path,
        registry_path,
        source_resolutions_path,
    ]
    missing_inputs = [
        str(path) for path in input_paths if not path.is_file()
    ]
    if missing_inputs:
        raise FileNotFoundError(
            "AKS 구조화 속성 입력 파일이 없습니다: "
            + ", ".join(missing_inputs)
        )
    if cli_args.dry_run:
        return {
            "status": "READY",
            "stage": "AKS_ATTRIBUTE_FACT_PREPROCESSING",
            "dry_run": True,
            "input_paths": [str(path) for path in input_paths],
            "output_directory": str(output_directory),
            "neo4j_load": False,
            "llm_used": False,
        }

    canonical_registry = pd.read_csv(
        registry_path,
        dtype=str,
    ).fillna("")
    source_resolutions = pd.read_csv(
        source_resolutions_path,
        dtype=str,
    ).fillna("")
    tables, statistics = build_aks_attribute_tables(
        articles_path,
        canonical_registry,
        source_resolutions,
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

    projection_policy = policy["aks_attribute_projection"]
    manifest = {
        "status": "COMPLETED",
        "stage": "AKS_ATTRIBUTE_FACT_PREPROCESSING",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "policy_version": projection_policy["policy_version"],
        "neo4j_load": False,
        "llm_used": False,
        "input_paths": {
            "articles": str(articles_path),
            "canonical_registry": str(registry_path),
            "source_resolutions": str(source_resolutions_path),
        },
        "input_counts": {
            "canonical_registry": len(canonical_registry),
            "source_resolutions": len(source_resolutions),
        },
        "statistics": statistics,
        "output_paths": output_paths,
    }
    manifest_path = (
        output_directory
        / policy["outputs"]["aks_attribute_manifest"]
    )
    with manifest_path.open(
        "w",
        encoding="utf-8",
    ) as manifest_file:
        dump(manifest, manifest_file, ensure_ascii=False, indent=2)
    manifest["output_paths"]["manifest"] = str(manifest_path)
    return manifest


def main() -> None:
    """AKS 구조화 속성 관계 후보 생성 결과를 JSON으로 출력한다."""
    result = run_aks_attribute_fact_preprocessing(parse_arguments())
    print(dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
