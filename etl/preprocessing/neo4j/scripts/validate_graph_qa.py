"""seed에 정의된 핵심 graph 양성·음성 사례를 생성 CSV 기준으로 검증한다."""

import argparse
import csv
from collections import defaultdict
from pathlib import Path

from neo4j_common import require_file, resolve_import_dir, resolve_project_root


def build_default_paths(script_path):
    neo4j_dir = script_path.parents[1]
    project_root = resolve_project_root(script_path)
    import_dir = resolve_import_dir(project_root)

    return {
        "case_seed": neo4j_dir / "seed" / "graph_qa_case_seed.csv",
        "nodes_dir": import_dir / "nodes",
        "relations_dir": import_dir / "relations",
        "report_path": neo4j_dir / "staging" / "graph_qa_report.csv",
    }


def parse_args(default_paths):
    parser = argparse.ArgumentParser(
        description="Neo4j graph 핵심 양성·음성 QA 사례를 검증한다."
    )
    parser.add_argument(
        "--case-seed-path",
        type=Path,
        default=default_paths["case_seed"],
    )
    parser.add_argument("--nodes-dir", type=Path, default=default_paths["nodes_dir"])
    parser.add_argument(
        "--relations-dir",
        type=Path,
        default=default_paths["relations_dir"],
    )
    parser.add_argument(
        "--report-path",
        type=Path,
        default=default_paths["report_path"],
    )
    parser.add_argument("--save", action="store_true")

    return parser.parse_args()


def clean_text(value):
    if value is None:
        return ""

    return str(value).strip()


def read_csv_rows(csv_path, purpose):
    require_file(csv_path, purpose)

    with csv_path.open("r", encoding="utf-8-sig", newline="") as csv_file:
        reader = csv.DictReader(csv_file)
        fieldnames = set(reader.fieldnames or [])
        rows = [
            {key: clean_text(value) for key, value in row.items()}
            for row in reader
        ]

    return fieldnames, rows


def read_case_seed(case_seed_path):
    fieldnames, rows = read_csv_rows(case_seed_path, "Graph QA case seed")
    required_columns = {
        "case_id",
        "case_type",
        "start_id",
        "middle_id",
        "target_id",
        "property_name",
        "expected_value",
        "expected_count",
        "description",
    }
    missing_columns = sorted(required_columns - fieldnames)

    if len(missing_columns) > 0:
        raise ValueError(
            "Graph QA case seed 필수 컬럼이 없습니다: "
            + ", ".join(missing_columns)
        )

    case_ids = set()

    for row in rows:
        required_values = {"case_id", "case_type", "expected_count", "description"}
        missing_values = sorted(
            column_name
            for column_name in required_values
            if row.get(column_name, "") == ""
        )

        if len(missing_values) > 0:
            raise ValueError(
                "Graph QA case 필수값이 비어 있습니다: "
                + ", ".join(missing_values)
            )

        if not row["expected_count"].isdigit():
            raise ValueError(
                f"Graph QA expected_count가 정수가 아닙니다: {row['case_id']}"
            )

        if row["case_id"] in case_ids:
            raise ValueError(f"Graph QA case_id가 중복되었습니다: {row['case_id']}")

        case_ids.add(row["case_id"])
        row["expected_count_number"] = int(row["expected_count"])

    return rows


def read_graph_context(nodes_dir, relations_dir):
    graph_files = {
        "actions": (nodes_dir / "royal_actions.csv", "RoyalAction nodes"),
        "heritage": (nodes_dir / "heritage_entities.csv", "CulturalHeritage nodes"),
        "monarch_actions": (
            relations_dir / "monarch_associated_with_royal_action.csv",
            "Monarch action relations",
        ),
        "action_targets": (
            relations_dir / "royal_action_targets_entity.csv",
            "RoyalAction target relations",
        ),
        "monarch_reigns": (
            relations_dir / "monarch_held_reign.csv",
            "Monarch reign relations",
        ),
        "reign_polities": (
            relations_dir / "reign_of_polity.csv",
            "Reign polity relations",
        ),
        "image_relations": (
            relations_dir / "source_image_depicts_entity.csv",
            "SourceImage depicts relations",
        ),
        "inscribed_on": (
            relations_dir / "inscription_content_inscribed_on.csv",
            "Inscription physical object relations",
        ),
        "source_presents": (
            relations_dir / "source_text_presents_inscription.csv",
            "SourceText inscription relations",
        ),
    }
    context = {}

    for context_name, (csv_path, purpose) in graph_files.items():
        context[context_name] = read_csv_rows(csv_path, purpose)[1]

    return context


def build_relation_indexes(context):
    actions_by_id = {row["action_id"]: row for row in context["actions"]}
    action_ids_by_monarch = defaultdict(set)
    targets_by_action = defaultdict(set)
    reign_ids_by_monarch = defaultdict(set)
    polities_by_reign = defaultdict(set)
    image_targets = defaultdict(set)
    physical_targets_by_inscription = defaultdict(set)
    inscriptions_by_source_text = defaultdict(set)

    for row in context["monarch_actions"]:
        action_ids_by_monarch[row["start_canonical_id"]].add(row["end_action_id"])

    for row in context["action_targets"]:
        targets_by_action[row["start_action_id"]].add(row["end_canonical_id"])

    for row in context["monarch_reigns"]:
        reign_ids_by_monarch[row["start_canonical_id"]].add(row["end_reign_id"])

    for row in context["reign_polities"]:
        polities_by_reign[row["start_reign_id"]].add(row["end_polity_id"])

    for row in context["image_relations"]:
        image_targets[row["source_image_id"]].add(row["canonical_id"])

    for row in context["inscribed_on"]:
        physical_targets_by_inscription[row["inscription_id"]].add(
            row["canonical_id"]
        )

    for row in context["source_presents"]:
        inscriptions_by_source_text[row["source_text_id"]].add(
            row["inscription_id"]
        )

    return {
        "actions_by_id": actions_by_id,
        "action_ids_by_monarch": action_ids_by_monarch,
        "targets_by_action": targets_by_action,
        "reign_ids_by_monarch": reign_ids_by_monarch,
        "polities_by_reign": polities_by_reign,
        "image_targets": image_targets,
        "physical_targets_by_inscription": physical_targets_by_inscription,
        "inscriptions_by_source_text": inscriptions_by_source_text,
    }


def count_royal_action_path(case_row, indexes):
    actual_count = 0

    for action_id in indexes["action_ids_by_monarch"].get(
        case_row["start_id"],
        set(),
    ):
        action_row = indexes["actions_by_id"].get(action_id)

        if action_row is None:
            continue

        if case_row["target_id"] not in indexes["targets_by_action"].get(
            action_id,
            set(),
        ):
            continue

        if action_row.get(case_row["property_name"], "") != case_row["expected_value"]:
            continue

        actual_count += 1

    return actual_count


def count_reign_path(case_row, indexes):
    actual_count = 0

    for reign_id in indexes["reign_ids_by_monarch"].get(
        case_row["start_id"],
        set(),
    ):
        if case_row["target_id"] in indexes["polities_by_reign"].get(
            reign_id,
            set(),
        ):
            actual_count += 1

    return actual_count


def count_heritage_property(case_row, context):
    return sum(
        1
        for row in context["heritage"]
        if row["canonical_id"] == case_row["target_id"]
        and row.get(case_row["property_name"], "") == case_row["expected_value"]
    )


def count_heritage_absence(case_row, context):
    return sum(
        1
        for row in context["heritage"]
        if row["canonical_id"] == case_row["target_id"]
    )


def count_image_relation(case_row, indexes):
    if case_row["target_id"] in indexes["image_targets"].get(
        case_row["start_id"],
        set(),
    ):
        return 1

    return 0


def count_inscription_source_path(case_row, indexes):
    if case_row["middle_id"] not in indexes["inscriptions_by_source_text"].get(
        case_row["start_id"],
        set(),
    ):
        return 0

    if case_row["target_id"] in indexes["physical_targets_by_inscription"].get(
        case_row["middle_id"],
        set(),
    ):
        return 1

    return 0


def evaluate_case(case_row, context, indexes):
    case_type = case_row["case_type"]

    if case_type == "ROYAL_ACTION_PATH":
        return count_royal_action_path(case_row, indexes)
    elif case_type == "REIGN_PATH":
        return count_reign_path(case_row, indexes)
    elif case_type == "HERITAGE_PROPERTY":
        return count_heritage_property(case_row, context)
    elif case_type == "HERITAGE_ABSENT":
        return count_heritage_absence(case_row, context)
    elif case_type == "IMAGE_RELATION_PATH":
        return count_image_relation(case_row, indexes)
    elif case_type == "INSCRIPTION_SOURCE_PATH":
        return count_inscription_source_path(case_row, indexes)

    raise ValueError(f"지원하지 않는 Graph QA case_type입니다: {case_type}")


def build_report_rows(case_rows, context, indexes):
    report_rows = []

    for case_row in case_rows:
        actual_count = evaluate_case(case_row, context, indexes)
        expected_count = case_row["expected_count_number"]
        status = "FAIL"

        if actual_count == expected_count:
            status = "PASS"

        report_rows.append(
            {
                "case_id": case_row["case_id"],
                "case_type": case_row["case_type"],
                "expected_count": expected_count,
                "actual_count": actual_count,
                "status": status,
                "description": case_row["description"],
            }
        )

    return report_rows


def save_report(report_rows, report_path):
    report_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = report_path.with_suffix(f"{report_path.suffix}.tmp")

    try:
        with temporary_path.open(
            "w", encoding="utf-8-sig", newline=""
        ) as output_file:
            writer = csv.DictWriter(
                output_file,
                fieldnames=[
                    "case_id",
                    "case_type",
                    "expected_count",
                    "actual_count",
                    "status",
                    "description",
                ],
                extrasaction="raise",
            )
            writer.writeheader()
            writer.writerows(report_rows)

        temporary_path.replace(report_path)
    except Exception:
        if temporary_path.exists():
            temporary_path.unlink()

        raise


def main():
    script_path = Path(__file__).resolve()
    default_paths = build_default_paths(script_path)
    args = parse_args(default_paths)
    case_rows = read_case_seed(args.case_seed_path)
    context = read_graph_context(args.nodes_dir, args.relations_dir)
    indexes = build_relation_indexes(context)
    report_rows = build_report_rows(case_rows, context, indexes)
    failed_rows = [row for row in report_rows if row["status"] == "FAIL"]

    if args.save:
        save_report(report_rows, args.report_path)

    print(f"graph_qa_cases: {len(report_rows)}")
    print(f"graph_qa_passed: {len(report_rows) - len(failed_rows)}")
    print(f"graph_qa_failed: {len(failed_rows)}")
    print(f"report_path: {args.report_path}")

    if len(failed_rows) > 0:
        for row in failed_rows:
            print(
                f"[FAIL] {row['case_id']}: expected={row['expected_count']} "
                f"actual={row['actual_count']}"
            )

        raise SystemExit(1)

    if not args.save:
        print("dry_run: no files saved. Use --save to write the QA report.")


if __name__ == "__main__":
    main()
