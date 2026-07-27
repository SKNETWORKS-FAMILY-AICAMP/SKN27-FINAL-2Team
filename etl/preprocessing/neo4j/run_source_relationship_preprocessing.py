from __future__ import annotations

from argparse import ArgumentParser, Namespace
from datetime import datetime, timezone
from json import dump, dumps
from pathlib import Path
import sys

import pandas as pd

sys.path.append(str(Path(__file__).resolve().parent))

from source_relationships.build import (
    build_source_relationship_tables,
    calculate_source_release,
    load_source_relationship_policy,
)
from source_relationships.load import load_source_relationships_to_neo4j


def resolve_project_path(project_root: Path, path_value: str) -> Path:
    """상대 경로를 프로젝트 루트 기준 절대 경로로 바꾼다."""
    path = Path(path_value)
    if path.is_absolute():
        return path
    return project_root / path


def parse_arguments() -> Namespace:
    """원천 관계 전처리 CLI 인자를 읽는다."""
    project_root = Path(__file__).resolve().parents[3]
    default_config = (
        project_root
        / "etl"
        / "preprocessing"
        / "neo4j"
        / "config"
        / "source_relationships.json"
    )
    parser = ArgumentParser(
        description=(
            "ITKC 관계와 한국역사용어시소러스 분류를 "
            "Neo4j 적재용 CSV로 변환합니다."
        )
    )
    parser.add_argument("--config", default=str(default_config))
    parser.add_argument("--itkc-people", default="")
    parser.add_argument("--itkc-person-relations", default="")
    parser.add_argument("--itkc-events", default="")
    parser.add_argument("--itkc-event-relations", default="")
    parser.add_argument("--thesaurus", default="")
    parser.add_argument("--canonical-resolutions", default="")
    parser.add_argument("--canonical-registry", default="")
    parser.add_argument("--output-dir", default="")
    parser.add_argument(
        "--load-neo4j",
        action="store_true",
        help="전처리 후 Neo4j에 원천 관계를 upsert합니다.",
    )
    parser.add_argument(
        "--canonical-only",
        action="store_true",
        help="Neo4j 적재 시 CanonicalEntity 직접 사실 관계만 upsert합니다.",
    )
    parser.add_argument("--database", default="")
    parser.add_argument("--batch-size", type=int, default=None)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="입력 존재 여부와 예정 경로만 확인합니다.",
    )
    return parser.parse_args()


def write_dataframe(dataframe: pd.DataFrame, output_path: Path) -> None:
    """DataFrame을 UTF-8 BOM CSV로 저장한다."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    dataframe.to_csv(output_path, index=False, encoding="utf-8-sig")


def run_source_relationship_preprocessing(
    cli_args: Namespace,
) -> dict[str, object]:
    """원천 파일을 읽어 관계 테이블과 실행 manifest를 생성한다."""
    project_root = Path(__file__).resolve().parents[3]
    policy = load_source_relationship_policy(cli_args.config)
    input_overrides = {
        "itkc_people": cli_args.itkc_people,
        "itkc_person_relations": cli_args.itkc_person_relations,
        "itkc_events": cli_args.itkc_events,
        "itkc_event_relations": cli_args.itkc_event_relations,
        "thesaurus": cli_args.thesaurus,
        "canonical_resolutions": cli_args.canonical_resolutions,
        "canonical_registry": cli_args.canonical_registry,
    }
    input_paths: dict[str, Path] = {}
    for input_name, override in input_overrides.items():
        path_value = override or policy["inputs"][input_name]
        input_paths[input_name] = resolve_project_path(
            project_root,
            path_value,
        )
    output_value = (
        cli_args.output_dir or policy["outputs"]["default_directory"]
    )
    output_directory = resolve_project_path(project_root, output_value)

    required_input_names = [
        "itkc_people",
        "itkc_person_relations",
        "itkc_events",
        "itkc_event_relations",
        "thesaurus",
    ]
    missing_inputs = [
        str(input_paths[input_name])
        for input_name in required_input_names
        if not input_paths[input_name].is_file()
    ]
    if missing_inputs:
        raise FileNotFoundError(
            "원천 관계 입력 파일이 없습니다: " + ", ".join(missing_inputs)
        )

    canonical_resolution_available = input_paths[
        "canonical_resolutions"
    ].is_file()
    canonical_registry_available = input_paths[
        "canonical_registry"
    ].is_file()
    if cli_args.dry_run:
        return {
            "status": "READY",
            "stage": "SOURCE_RELATIONSHIP_PREPROCESSING",
            "dry_run": True,
            "policy_version": policy["policy_version"],
            "input_paths": {
                name: str(path)
                for name, path in input_paths.items()
            },
            "canonical_resolution_available": (
                canonical_resolution_available
            ),
            "canonical_registry_available": (
                canonical_registry_available
            ),
            "output_directory": str(output_directory),
        }

    releases = {
        input_name: calculate_source_release(
            input_paths[input_name],
            policy,
        )
        for input_name in required_input_names
    }
    people = pd.read_csv(
        input_paths["itkc_people"],
        dtype=str,
    ).fillna("")
    person_relations = pd.read_csv(
        input_paths["itkc_person_relations"],
        dtype=str,
    ).fillna("")
    events = pd.read_csv(
        input_paths["itkc_events"],
        dtype=str,
    ).fillna("")
    event_relations = pd.read_csv(
        input_paths["itkc_event_relations"],
        dtype=str,
    ).fillna("")
    thesaurus = pd.read_csv(
        input_paths["thesaurus"],
        dtype=str,
    ).fillna("")
    canonical_resolutions: pd.DataFrame | None = None
    if canonical_resolution_available:
        canonical_resolutions = pd.read_csv(
            input_paths["canonical_resolutions"],
            dtype=str,
        ).fillna("")
    canonical_registry: pd.DataFrame | None = None
    if canonical_registry_available:
        canonical_registry = pd.read_csv(
            input_paths["canonical_registry"],
            dtype=str,
        ).fillna("")

    tables = build_source_relationship_tables(
        people,
        person_relations,
        events,
        event_relations,
        thesaurus,
        releases,
        policy,
        canonical_resolutions,
        canonical_registry,
    )
    output_directory.mkdir(parents=True, exist_ok=True)
    output_paths: dict[str, str] = {}
    table_names = [
        "source_record_nodes",
        "source_record_relationships",
        "thesaurus_category_nodes",
        "source_category_relationships",
        "thesaurus_category_relationships",
        "canonical_entity_relationships",
        "canonical_projection_exclusions",
    ]
    for table_name in table_names:
        output_path = output_directory / policy["outputs"][table_name]
        write_dataframe(tables[table_name], output_path)
        output_paths[table_name] = str(output_path)

    relationship_counts = {
        str(relation_type): int(count)
        for relation_type, count in tables[
            "source_record_relationships"
        ]["relation_type"].value_counts().items()
    }
    included_projection_types = set(
        policy["canonical_projection"]["included_relation_types"]
    )
    projectable_source_relationship_count = int(
        tables["source_record_relationships"]["relation_type"]
        .isin(included_projection_types)
        .sum()
    )
    projected_source_relationship_count = 0
    if not tables["canonical_entity_relationships"].empty:
        projected_source_relationship_count = int(
            tables["canonical_entity_relationships"][
                "evidence_count"
            ]
            .astype(int)
            .sum()
        )
    recorded_projection_exclusion_count = len(
        tables["canonical_projection_exclusions"]
    )
    suppressed_both_unresolved_count = (
        projectable_source_relationship_count
        - projected_source_relationship_count
        - recorded_projection_exclusion_count
    )
    manifest = {
        "status": "COMPLETED",
        "stage": "SOURCE_RELATIONSHIP_PREPROCESSING",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "policy_version": policy["policy_version"],
        "canonical_resolution_available": canonical_resolution_available,
        "canonical_registry_available": canonical_registry_available,
        "input_releases": releases,
        "input_row_counts": {
            "itkc_people": len(people),
            "itkc_person_relations": len(person_relations),
            "itkc_events": len(events),
            "itkc_event_relations": len(event_relations),
            "thesaurus": len(thesaurus),
        },
        "output_counts": {
            table_name: len(tables[table_name])
            for table_name in table_names
        },
        "source_relationship_type_counts": relationship_counts,
        "canonical_projection_counts": {
            "projectable_source_relationships": (
                projectable_source_relationship_count
            ),
            "projected_source_relationships": (
                projected_source_relationship_count
            ),
            "recorded_exclusions": (
                recorded_projection_exclusion_count
            ),
            "both_unresolved_not_written": (
                suppressed_both_unresolved_count
            ),
        },
        "deduplicated_row_counts": {
            "itkc_person_relations": (
                len(person_relations)
                - int(
                    (
                        tables["source_record_relationships"][
                            "source_dataset"
                        ]
                        == policy["relationships"][
                            "itkc_person_dataset"
                        ]
                    ).sum()
                )
            ),
            "itkc_event_relations": (
                len(event_relations)
                - int(
                    (
                        tables["source_record_relationships"][
                            "source_dataset"
                        ]
                        == policy["relationships"]["itkc_event_dataset"]
                    ).sum()
                )
            ),
        },
        "output_paths": output_paths,
    }
    manifest_path = output_directory / policy["outputs"]["manifest"]
    with manifest_path.open("w", encoding="utf-8") as manifest_file:
        dump(manifest, manifest_file, ensure_ascii=False, indent=2)
    manifest["output_paths"]["manifest"] = str(manifest_path)
    if cli_args.load_neo4j:
        manifest["neo4j_load"] = load_source_relationships_to_neo4j(
            str(output_directory),
            policy,
            str(project_root),
            database=cli_args.database,
            batch_size=cli_args.batch_size,
            canonical_only=cli_args.canonical_only,
        )
        with manifest_path.open(
            "w",
            encoding="utf-8",
        ) as manifest_file:
            dump(manifest, manifest_file, ensure_ascii=False, indent=2)
    return manifest


def main() -> None:
    """CLI 실행 결과를 JSON으로 출력한다."""
    result = run_source_relationship_preprocessing(parse_arguments())
    print(dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
