from __future__ import annotations

import argparse
import csv
import hashlib
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def parse_arguments(neo4j_root: Path) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Report active CanonicalEntity nodes that share the same "
            "normalized name, including each entity's verified eras."
        )
    )
    parser.add_argument(
        "--release-dir",
        default=str(neo4j_root / "output" / "fact_graph_release"),
    )
    parser.add_argument(
        "--output-dir",
        default=str(neo4j_root / "output" / "fact_graph_eda"),
    )
    return parser.parse_args()


def read_csv_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as input_file:
        return list(csv.DictReader(input_file))


def write_csv_rows(
    path: Path,
    fieldnames: list[str],
    rows: list[dict[str, Any]],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_name(f".{path.name}.tmp")
    try:
        with temporary_path.open(
            "w",
            encoding="utf-8-sig",
            newline="",
        ) as output_file:
            writer = csv.DictWriter(
                output_file,
                fieldnames=fieldnames,
                extrasaction="ignore",
            )
            writer.writeheader()
            writer.writerows(rows)
        temporary_path.replace(path)
    finally:
        if temporary_path.is_file():
            temporary_path.unlink()


def build_duplicate_name_report(
    release_directory: Path,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    required_paths = {
        "entities": release_directory / "entities.csv",
        "eras": release_directory / "eras.csv",
        "entity_era_links": release_directory / "entity_era_links.csv",
        "manifest": release_directory / "manifest.json",
    }
    missing_paths = [
        str(path)
        for path in required_paths.values()
        if not path.is_file()
    ]
    if missing_paths:
        raise FileNotFoundError(
            "Missing canonical duplicate report inputs: "
            + ", ".join(missing_paths)
        )

    with required_paths["manifest"].open(
        "r",
        encoding="utf-8",
    ) as input_file:
        manifest = json.load(input_file)
    canonical_entities = [
        row
        for row in read_csv_rows(required_paths["entities"])
        if row["entity_kind"] == "CANONICAL"
        and row["lifecycle_status"] == "ACTIVE"
    ]
    era_name_by_id = {
        row["era_id"]: row["name"]
        for row in read_csv_rows(required_paths["eras"])
    }
    era_ids_by_canonical_id: dict[str, set[str]] = defaultdict(set)
    for row in read_csv_rows(required_paths["entity_era_links"]):
        if row["verification_status"] != "VERIFIED":
            continue
        era_ids_by_canonical_id[row["canonical_id"]].add(
            row["era_id"]
        )

    entities_by_normalized_name: dict[
        str,
        list[dict[str, str]],
    ] = defaultdict(list)
    for entity in canonical_entities:
        normalized_name = entity["normalized_search_text"]
        if normalized_name:
            entities_by_normalized_name[normalized_name].append(entity)
    duplicate_groups = {
        normalized_name: entities
        for normalized_name, entities
        in entities_by_normalized_name.items()
        if len(entities) > 1
    }

    report_rows: list[dict[str, Any]] = []
    group_era_status_counts: Counter[str] = Counter()
    for normalized_name, group_entities in sorted(
        duplicate_groups.items()
    ):
        group_id = (
            "canonical-duplicate-name:"
            + hashlib.sha256(
                normalized_name.encode("utf-8")
            ).hexdigest()[:24]
        )
        era_signature_by_canonical_id = {
            entity["entity_id"]: tuple(
                sorted(
                    era_ids_by_canonical_id.get(
                        entity["entity_id"],
                        set(),
                    )
                )
            )
            for entity in group_entities
        }
        era_signatures = set(era_signature_by_canonical_id.values())
        if any(not signature for signature in era_signatures):
            group_era_status = "MISSING_ERA"
        elif len(era_signatures) == 1:
            group_era_status = "SAME_ERA_SET"
        elif len(era_signatures) > 1:
            group_era_status = "DIFFERENT_ERA_SETS"
        group_era_status_counts[group_era_status] += 1
        type_counts = Counter(
            entity["entity_type"]
            for entity in group_entities
        )
        era_signature_counts = Counter(
            era_signature_by_canonical_id.values()
        )

        for entity in sorted(
            group_entities,
            key=lambda row: (
                row["entity_type"],
                row["entity_id"],
            ),
        ):
            era_ids = list(
                era_signature_by_canonical_id[entity["entity_id"]]
            )
            era_names = [
                era_name_by_id.get(era_id, era_id)
                for era_id in era_ids
            ]
            era_resolution_status = "MISSING"
            if len(era_ids) == 1:
                era_resolution_status = "UNIQUE"
            elif len(era_ids) > 1:
                era_resolution_status = "MULTIPLE"
            report_rows.append(
                {
                    "duplicate_group_id": group_id,
                    "normalized_name": normalized_name,
                    "display_name": entity["display_name"],
                    "duplicate_entity_count": len(group_entities),
                    "canonical_id": entity["entity_id"],
                    "entity_type": entity["entity_type"],
                    "same_name_same_type_count": type_counts[
                        entity["entity_type"]
                    ],
                    "same_name_same_era_set_count": (
                        era_signature_counts[
                            era_signature_by_canonical_id[
                                entity["entity_id"]
                            ]
                        ]
                    ),
                    "group_era_status": group_era_status,
                    "era_resolution_status": era_resolution_status,
                    "era_count": len(era_ids),
                    "era_names_json": json.dumps(
                        era_names,
                        ensure_ascii=False,
                    ),
                    "era_ids_json": json.dumps(
                        era_ids,
                        ensure_ascii=False,
                    ),
                    "exact_search_status": entity[
                        "exact_search_status"
                    ],
                    "identity_confidence": entity[
                        "identity_confidence"
                    ],
                    "source_support_count": entity[
                        "source_support_count"
                    ],
                    "graph_release_id": entity["graph_release_id"],
                }
            )

    entity_era_status_counts = Counter(
        row["era_resolution_status"]
        for row in report_rows
    )
    summary = {
        "status": "COMPLETED",
        "stage": "CANONICAL_DUPLICATE_NAME_ERA_REPORT",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "graph_release_id": manifest["graph_release_id"],
        "active_canonical_entity_count": len(canonical_entities),
        "duplicate_name_group_count": len(duplicate_groups),
        "canonical_entity_in_duplicate_name_group_count": len(
            report_rows
        ),
        "same_name_same_type_group_count": sum(
            len(
                {
                    entity["entity_type"]
                    for entity in group_entities
                }
            )
            == 1
            for group_entities in duplicate_groups.values()
        ),
        "cross_entity_type_group_count": sum(
            len(
                {
                    entity["entity_type"]
                    for entity in group_entities
                }
            )
            > 1
            for group_entities in duplicate_groups.values()
        ),
        "group_era_status_counts": dict(
            sorted(group_era_status_counts.items())
        ),
        "entity_era_status_counts": dict(
            sorted(entity_era_status_counts.items())
        ),
    }
    return report_rows, summary


def main() -> None:
    neo4j_root = Path(__file__).resolve().parent.parent
    args = parse_arguments(neo4j_root)
    release_directory = Path(args.release_dir)
    output_directory = Path(args.output_dir)
    report_rows, summary = build_duplicate_name_report(
        release_directory
    )

    report_path = (
        output_directory / "canonical_duplicate_name_eras.csv"
    )
    summary_path = (
        output_directory
        / "canonical_duplicate_name_era_summary.json"
    )
    write_csv_rows(
        report_path,
        [
            "duplicate_group_id",
            "normalized_name",
            "display_name",
            "duplicate_entity_count",
            "canonical_id",
            "entity_type",
            "same_name_same_type_count",
            "same_name_same_era_set_count",
            "group_era_status",
            "era_resolution_status",
            "era_count",
            "era_names_json",
            "era_ids_json",
            "exact_search_status",
            "identity_confidence",
            "source_support_count",
            "graph_release_id",
        ],
        report_rows,
    )
    summary["report_path"] = str(report_path.resolve())
    temporary_summary_path = summary_path.with_name(
        f".{summary_path.name}.tmp"
    )
    try:
        with temporary_summary_path.open(
            "w",
            encoding="utf-8",
        ) as output_file:
            json.dump(
                summary,
                output_file,
                ensure_ascii=False,
                indent=2,
            )
        temporary_summary_path.replace(summary_path)
    finally:
        if temporary_summary_path.is_file():
            temporary_summary_path.unlink()
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
