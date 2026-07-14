"""AKS CanonicalEntity에 적용할 문화유산 세부 유형 CSV를 생성한다."""

import argparse
import csv
import json
from pathlib import Path

from neo4j_common import require_file, resolve_import_dir, resolve_project_root


def build_default_paths(script_path):
    neo4j_dir = script_path.parents[1]
    project_root = resolve_project_root(script_path)
    import_dir = resolve_import_dir(project_root)

    return {
        "source_seed": neo4j_dir / "seed" / "aks_source_seed.csv",
        "image_source_seed": neo4j_dir / "seed" / "image_source_seed.csv",
        "rule_seed": neo4j_dir / "seed" / "aks_heritage_rule_seed.csv",
        "override_seed": neo4j_dir / "seed" / "aks_heritage_override_seed.csv",
        "articles_path": project_root
        / "etl"
        / "raw_data"
        / "한국민족문화대백과사전"
        / "articles_detail.jsonl",
        "nodes_dir": import_dir / "nodes",
    }


def parse_args(default_paths):
    parser = argparse.ArgumentParser(
        description="AKS CanonicalEntity 문화유산 세부 유형 CSV를 생성한다."
    )
    parser.add_argument(
        "--source-seed-path",
        type=Path,
        default=default_paths["source_seed"],
    )
    parser.add_argument(
        "--image-source-seed-path",
        type=Path,
        default=default_paths["image_source_seed"],
    )
    parser.add_argument(
        "--rule-seed-path",
        type=Path,
        default=default_paths["rule_seed"],
    )
    parser.add_argument(
        "--override-seed-path",
        type=Path,
        default=default_paths["override_seed"],
    )
    parser.add_argument(
        "--articles-path",
        type=Path,
        default=default_paths["articles_path"],
    )
    parser.add_argument("--nodes-dir", type=Path, default=default_paths["nodes_dir"])
    parser.add_argument("--save", action="store_true")

    return parser.parse_args()


def clean_text(value):
    if value is None:
        return ""

    return str(value).strip()


def read_csv_rows(csv_path, purpose):
    require_file(csv_path, purpose)

    with csv_path.open("r", encoding="utf-8-sig", newline="") as csv_file:
        return [
            {key: clean_text(value) for key, value in row.items()}
            for row in csv.DictReader(csv_file)
        ]


def read_single_config(config_path, purpose, required_columns):
    rows = read_csv_rows(config_path, purpose)

    if len(rows) != 1:
        raise ValueError(f"{purpose}에는 설정이 정확히 한 행이어야 합니다.")

    config = rows[0]
    missing_columns = sorted(
        column_name
        for column_name in required_columns
        if config.get(column_name, "") == ""
    )

    if len(missing_columns) > 0:
        raise ValueError(
            f"{purpose} 필수값이 비어 있습니다: " + ", ".join(missing_columns)
        )

    return config


def read_rule_seed(rule_seed_path):
    rows = read_csv_rows(rule_seed_path, "AKS heritage rule seed")
    priorities = []

    for row in rows:
        required_columns = {
            "priority",
            "entity_type",
            "entity_subtype",
            "heritage_kind",
            "heritage_form",
            "classification_method",
            "review_status",
        }
        missing_columns = sorted(
            column_name
            for column_name in required_columns
            if row.get(column_name, "") == ""
        )

        if len(missing_columns) > 0:
            raise ValueError(
                "AKS heritage rule 필수값이 비어 있습니다: "
                + ", ".join(missing_columns)
            )

        if not row["priority"].isdigit():
            raise ValueError(
                f"AKS heritage rule priority가 정수가 아닙니다: {row['priority']}"
            )

        row["priority_number"] = int(row["priority"])
        priorities.append(row["priority_number"])

    if len(priorities) != len(set(priorities)):
        raise ValueError("AKS heritage rule priority가 중복되었습니다.")

    return sorted(rows, key=lambda row: row["priority_number"])


def read_override_seed(override_seed_path):
    rows = read_csv_rows(override_seed_path, "AKS heritage override seed")
    canonical_eids = []

    for row in rows:
        required_columns = {
            "canonical_eid",
            "heritage_kind",
            "heritage_subtype",
            "heritage_form",
            "evidence_source_kind",
            "evidence_id",
            "classification_method",
            "review_status",
        }
        missing_columns = sorted(
            column_name
            for column_name in required_columns
            if row.get(column_name, "") == ""
        )

        if len(missing_columns) > 0:
            raise ValueError(
                "AKS heritage override 필수값이 비어 있습니다: "
                + ", ".join(missing_columns)
            )

        canonical_eids.append(row["canonical_eid"])

    if len(canonical_eids) != len(set(canonical_eids)):
        raise ValueError("AKS heritage override canonical_eid가 중복되었습니다.")

    return rows


def resolve_image_path(image_source_config, project_root):
    return (project_root / image_source_config["raw_relative_path"]).resolve()


def read_image_records(image_path, image_source_config):
    rows = read_csv_rows(image_path, "한국사 이미지 자료")
    image_id_column = image_source_config["image_id_column"]
    required_columns = {
        image_id_column,
        image_source_config["title_column"],
        image_source_config["type_column"],
        image_source_config["description_column"],
    }

    if len(rows) == 0:
        raise ValueError("한국사 이미지 자료가 비어 있습니다.")

    missing_columns = sorted(required_columns - set(rows[0]))

    if len(missing_columns) > 0:
        raise ValueError(
            "한국사 이미지 자료 필수 컬럼이 없습니다: " + ", ".join(missing_columns)
        )

    image_records = {}

    for row in rows:
        image_id = row[image_id_column]

        if image_id in image_records:
            raise ValueError(f"한국사 이미지 자료 ID가 중복되었습니다: {image_id}")

        image_records[image_id] = row

    return image_records


def matches_rule(entity_type, entity_subtype, rule_row):
    if rule_row["entity_type"] != entity_type:
        return False

    if rule_row["entity_subtype"] == "*":
        return True

    return rule_row["entity_subtype"] == entity_subtype


def find_heritage_rule(article, rule_rows):
    entity_type = clean_text(article.get("primaryTypePartA"))
    entity_subtype = clean_text(article.get("primaryTypePartB"))

    for rule_row in rule_rows:
        if matches_rule(entity_type, entity_subtype, rule_row):
            return rule_row

    return None


def validate_override_evidence(
    override_row,
    article,
    image_records,
    image_source_config,
):
    source_kind = override_row["evidence_source_kind"]

    if source_kind == "AKS_ARTICLE":
        if override_row["evidence_id"] != clean_text(article.get("eid")):
            raise ValueError(
                f"AKS heritage override evidence EID가 대상과 다릅니다: "
                f"{override_row['canonical_eid']}"
            )

        return

    if source_kind == "IMAGE_CSV":
        image_id = override_row["evidence_id"]

        if image_id not in image_records:
            raise ValueError(
                f"AKS heritage override 이미지 근거가 없습니다: {image_id}"
            )

        image_title = image_records[image_id][image_source_config["title_column"]]
        article_name = clean_text(article.get("headword"))

        if image_title != article_name:
            raise ValueError(
                f"AKS heritage override 이미지 제목이 대상과 다릅니다: "
                f"{image_id} {image_title} != {article_name}"
            )

        return

    raise ValueError(
        f"지원하지 않는 heritage evidence_source_kind입니다: {source_kind}"
    )


def build_heritage_row(article, classification, source_config):
    source_eid = clean_text(article.get("eid"))
    heritage_subtype = clean_text(article.get("primaryTypePartB"))

    if classification.get("heritage_subtype", "") != "":
        heritage_subtype = classification["heritage_subtype"]

    return {
        "canonical_id": f"{source_config['canonical_id_prefix']}:{source_eid}",
        "heritage_id": f"{source_config['heritage_id_prefix']}:{source_eid}",
        "name": clean_text(article.get("headword")),
        "heritage_kind": classification["heritage_kind"],
        "heritage_form": classification["heritage_form"],
        "heritage_subtype": heritage_subtype,
        "primary_type": clean_text(article.get("primaryType")),
        "secondary_type": clean_text(article.get("secondaryType")),
        "contents_type": clean_text(article.get("contentsType")),
        "classification_method": classification["classification_method"],
        "evidence_source_kind": classification.get(
            "evidence_source_kind",
            "AKS_ARTICLE",
        ),
        "evidence_id": classification.get("evidence_id", source_eid),
        "review_status": classification["review_status"],
        "note": classification.get("note", ""),
    }


def process_articles(
    articles_path,
    rule_rows,
    override_rows,
    source_config,
    image_records,
    image_source_config,
):
    require_file(articles_path, "AKS articles_detail")
    overrides_by_eid = {row["canonical_eid"]: row for row in override_rows}
    found_override_eids = set()
    heritage_rows = []

    with articles_path.open("r", encoding="utf-8-sig") as articles_file:
        for line_number, raw_line in enumerate(articles_file, 1):
            if raw_line.strip() == "":
                continue

            try:
                article = json.loads(raw_line)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"AKS JSONL parsing failed at line {line_number}: {exc}"
                ) from exc

            source_eid = clean_text(article.get("eid"))
            classification = None

            if source_eid in overrides_by_eid:
                classification = overrides_by_eid[source_eid]
                found_override_eids.add(source_eid)
                validate_override_evidence(
                    classification,
                    article,
                    image_records,
                    image_source_config,
                )
            elif source_eid not in overrides_by_eid:
                classification = find_heritage_rule(article, rule_rows)

            if classification is None:
                continue

            heritage_rows.append(
                build_heritage_row(article, classification, source_config)
            )

    missing_override_eids = sorted(set(overrides_by_eid) - found_override_eids)

    if len(missing_override_eids) > 0:
        raise ValueError(
            "AKS 원문에서 heritage override 대상을 찾지 못했습니다: "
            + ", ".join(missing_override_eids)
        )

    heritage_ids = [row["heritage_id"] for row in heritage_rows]

    if len(heritage_ids) != len(set(heritage_ids)):
        raise ValueError("생성된 heritage_id가 중복되었습니다.")

    return heritage_rows


def save_heritage_rows(heritage_rows, output_path):
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = output_path.with_suffix(f"{output_path.suffix}.tmp")
    fieldnames = [
        "canonical_id",
        "heritage_id",
        "name",
        "heritage_kind",
        "heritage_form",
        "heritage_subtype",
        "primary_type",
        "secondary_type",
        "contents_type",
        "classification_method",
        "evidence_source_kind",
        "evidence_id",
        "review_status",
        "note",
    ]

    try:
        with temporary_path.open(
            "w", encoding="utf-8-sig", newline=""
        ) as output_file:
            writer = csv.DictWriter(
                output_file,
                fieldnames=fieldnames,
                extrasaction="raise",
            )
            writer.writeheader()
            writer.writerows(heritage_rows)

        temporary_path.replace(output_path)
    except Exception:
        if temporary_path.exists():
            temporary_path.unlink()

        raise


def main():
    script_path = Path(__file__).resolve()
    project_root = resolve_project_root(script_path)
    default_paths = build_default_paths(script_path)
    args = parse_args(default_paths)
    source_config = read_single_config(
        args.source_seed_path,
        "AKS source seed",
        {
            "source_id",
            "source_record_id_prefix",
            "canonical_id_prefix",
            "heritage_id_prefix",
        },
    )
    image_source_config = read_single_config(
        args.image_source_seed_path,
        "Image source seed",
        {
            "source_id",
            "raw_relative_path",
            "image_id_column",
            "title_column",
            "type_column",
            "description_column",
        },
    )
    rule_rows = read_rule_seed(args.rule_seed_path)
    override_rows = read_override_seed(args.override_seed_path)
    image_path = resolve_image_path(image_source_config, project_root)
    image_records = read_image_records(image_path, image_source_config)
    heritage_rows = process_articles(
        args.articles_path,
        rule_rows,
        override_rows,
        source_config,
        image_records,
        image_source_config,
    )
    output_path = args.nodes_dir / "heritage_entities.csv"

    if args.save:
        save_heritage_rows(heritage_rows, output_path)

    print(f"heritage_entities.csv: {len(heritage_rows)} rows")
    print(f"output_path: {output_path}")

    if not args.save:
        print("dry_run: no files saved. Use --save to write CSV files.")


if __name__ == "__main__":
    main()
