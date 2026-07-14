"""비문 내용, 비석 실물, 비문 원천 텍스트를 서로 다른 graph 개체로 연결한다."""

import argparse
import csv
from pathlib import Path

from neo4j_common import require_file, resolve_import_dir, resolve_project_root


def build_default_paths(script_path):
    neo4j_dir = script_path.parents[1]
    project_root = resolve_project_root(script_path)
    import_dir = resolve_import_dir(project_root)

    return {
        "history_source_seed": neo4j_dir / "seed" / "history_source_seed.csv",
        "content_seed": neo4j_dir / "seed" / "inscription_content_seed.csv",
        "source_seed": neo4j_dir / "seed" / "inscription_source_seed.csv",
        "terms_path": import_dir / "nodes" / "terms.csv",
        "canonical_path": import_dir / "nodes" / "canonical_entities.csv",
        "heritage_path": import_dir / "nodes" / "heritage_entities.csv",
        "nodes_dir": import_dir / "nodes",
        "relations_dir": import_dir / "relations",
    }


def parse_args(default_paths):
    parser = argparse.ArgumentParser(
        description="비문 내용·비석 실물·원천 텍스트 연결 CSV를 생성한다."
    )
    parser.add_argument(
        "--history-source-seed-path",
        type=Path,
        default=default_paths["history_source_seed"],
    )
    parser.add_argument(
        "--content-seed-path",
        type=Path,
        default=default_paths["content_seed"],
    )
    parser.add_argument(
        "--source-seed-path",
        type=Path,
        default=default_paths["source_seed"],
    )
    parser.add_argument(
        "--terms-path",
        type=Path,
        default=default_paths["terms_path"],
    )
    parser.add_argument(
        "--canonical-path",
        type=Path,
        default=default_paths["canonical_path"],
    )
    parser.add_argument(
        "--heritage-path",
        type=Path,
        default=default_paths["heritage_path"],
    )
    parser.add_argument("--nodes-dir", type=Path, default=default_paths["nodes_dir"])
    parser.add_argument(
        "--relations-dir",
        type=Path,
        default=default_paths["relations_dir"],
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


def require_columns(fieldnames, required_columns, purpose):
    missing_columns = sorted(required_columns - fieldnames)

    if len(missing_columns) > 0:
        raise ValueError(
            f"{purpose} 필수 컬럼이 없습니다: " + ", ".join(missing_columns)
        )


def require_row_values(row, required_columns, purpose):
    missing_values = sorted(
        column_name
        for column_name in required_columns
        if row.get(column_name, "") == ""
    )

    if len(missing_values) > 0:
        raise ValueError(
            f"{purpose} 필수값이 비어 있습니다: " + ", ".join(missing_values)
        )


def read_single_config(config_path):
    fieldnames, rows = read_csv_rows(config_path, "History source seed")
    required_columns = {
        "source_id",
        "source_name",
        "source_record_id_prefix",
        "source_text_id_prefix",
        "record_type",
        "raw_relative_path",
        "material_id_column",
        "title_column",
        "era_column",
        "field_column",
        "toc_column",
        "text_column",
        "url_column",
        "markdown_path_column",
        "review_status",
    }
    require_columns(fieldnames, required_columns, "History source seed")

    if len(rows) != 1:
        raise ValueError("History source seed에는 설정이 정확히 한 행이어야 합니다.")

    require_row_values(rows[0], required_columns, "History source seed")

    return rows[0]


def read_indexed_rows(csv_path, purpose, id_column, required_columns):
    fieldnames, rows = read_csv_rows(csv_path, purpose)
    require_columns(fieldnames, required_columns, purpose)
    rows_by_id = {}

    for row in rows:
        row_id = row[id_column]

        if row_id == "":
            raise ValueError(f"{purpose} ID가 비어 있습니다.")

        if row_id in rows_by_id:
            raise ValueError(f"{purpose} ID가 중복되었습니다: {row_id}")

        rows_by_id[row_id] = row

    return rows_by_id


def read_content_seed(content_seed_path):
    fieldnames, rows = read_csv_rows(content_seed_path, "Inscription content seed")
    required_columns = {
        "term_id",
        "inscription_id",
        "target_canonical_id",
        "inscription_kind",
        "content_language",
        "evidence_source_record_id",
        "review_status",
        "note",
    }
    require_columns(fieldnames, required_columns, "Inscription content seed")
    inscription_ids = set()
    term_ids = set()

    for row in rows:
        require_row_values(
            row,
            required_columns - {"note"},
            "Inscription content seed",
        )

        if row["inscription_id"] in inscription_ids:
            raise ValueError(
                f"Inscription ID가 중복되었습니다: {row['inscription_id']}"
            )

        if row["term_id"] in term_ids:
            raise ValueError(f"Inscription Term ID가 중복되었습니다: {row['term_id']}")

        inscription_ids.add(row["inscription_id"])
        term_ids.add(row["term_id"])

    return rows


def read_source_seed(source_seed_path):
    fieldnames, rows = read_csv_rows(source_seed_path, "Inscription source seed")
    required_columns = {
        "material_id",
        "inscription_id",
        "original_start_marker",
        "original_end_marker",
        "review_status",
        "note",
    }
    require_columns(fieldnames, required_columns, "Inscription source seed")
    material_ids = set()

    for row in rows:
        require_row_values(
            row,
            required_columns - {"note"},
            "Inscription source seed",
        )

        if row["material_id"] in material_ids:
            raise ValueError(
                f"Inscription source material ID가 중복되었습니다: {row['material_id']}"
            )

        material_ids.add(row["material_id"])

    return rows


def split_source_text(source_text, source_seed_row):
    original_start_marker = source_seed_row["original_start_marker"]
    original_end_marker = source_seed_row["original_end_marker"]
    original_start = source_text.find(original_start_marker)

    if original_start < 0:
        raise ValueError(
            "사료에서 한문 원문 시작 기준을 찾지 못했습니다: "
            + source_seed_row["material_id"]
        )

    original_end = source_text.find(original_end_marker, original_start)

    if original_end < 0:
        raise ValueError(
            "사료에서 한문 원문 종료 기준을 찾지 못했습니다: "
            + source_seed_row["material_id"]
        )

    original_end += len(original_end_marker)
    translated_text = clean_text(source_text[:original_start])
    original_text = clean_text(source_text[original_start:original_end])
    commentary_text = clean_text(source_text[original_end:])

    if translated_text == "":
        raise ValueError("사료 번역문이 비어 있습니다.")

    if original_text == "":
        raise ValueError("사료 한문 원문이 비어 있습니다.")

    if commentary_text == "":
        raise ValueError("사료 해설이 비어 있습니다.")

    return translated_text, original_text, commentary_text


def build_graph_rows(
    project_root,
    history_config,
    content_seed_rows,
    source_seed_rows,
    terms_by_id,
    canonical_by_id,
    heritage_by_canonical_id,
):
    raw_path = (project_root / history_config["raw_relative_path"]).resolve()
    raw_required_columns = {
        history_config["material_id_column"],
        history_config["title_column"],
        history_config["era_column"],
        history_config["field_column"],
        history_config["toc_column"],
        history_config["text_column"],
        history_config["url_column"],
        history_config["markdown_path_column"],
    }
    raw_rows_by_id = read_indexed_rows(
        raw_path,
        "사료로 본 한국사",
        history_config["material_id_column"],
        raw_required_columns,
    )
    content_by_inscription_id = {
        row["inscription_id"]: row for row in content_seed_rows
    }
    source_by_inscription_id = {
        row["inscription_id"]: row for row in source_seed_rows
    }

    if set(content_by_inscription_id) != set(source_by_inscription_id):
        raise ValueError("비문 내용 seed와 원천 seed의 inscription_id 구성이 다릅니다.")

    inscription_rows = []
    source_text_rows = []
    inscribed_on_rows = []
    presents_rows = []

    for inscription_id, content_seed_row in content_by_inscription_id.items():
        term_id = content_seed_row["term_id"]
        canonical_id = content_seed_row["target_canonical_id"]
        source_seed_row = source_by_inscription_id[inscription_id]
        material_id = source_seed_row["material_id"]

        if term_id not in terms_by_id:
            raise ValueError(f"비문 Term을 찾지 못했습니다: {term_id}")

        if canonical_id not in canonical_by_id:
            raise ValueError(
                f"비문 실물 CanonicalEntity를 찾지 못했습니다: {canonical_id}"
            )

        if canonical_id not in heritage_by_canonical_id:
            raise ValueError(f"비문 실물이 CulturalHeritage가 아닙니다: {canonical_id}")

        if heritage_by_canonical_id[canonical_id]["heritage_form"] != "PHYSICAL":
            raise ValueError(f"비문 대상 문화유산이 실물이 아닙니다: {canonical_id}")

        if material_id not in raw_rows_by_id:
            raise ValueError(f"비문 사료를 찾지 못했습니다: {material_id}")

        raw_row = raw_rows_by_id[material_id]
        translated_text, original_text, commentary_text = split_source_text(
            raw_row[history_config["text_column"]],
            source_seed_row,
        )
        source_record_id = (
            f"{history_config['source_record_id_prefix']}:{material_id}"
        )
        source_text_id = f"{history_config['source_text_id_prefix']}:{material_id}"

        if source_record_id != content_seed_row["evidence_source_record_id"]:
            raise ValueError(
                "비문 내용 seed의 evidence_source_record_id가 생성 규칙과 다릅니다: "
                + inscription_id
            )

        term_row = terms_by_id[term_id]
        inscription_rows.append(
            {
                "term_id": term_id,
                "inscription_id": inscription_id,
                "name": term_row["name"],
                "hanja": term_row["hanja"],
                "inscription_kind": content_seed_row["inscription_kind"],
                "content_language": content_seed_row["content_language"],
                "year_text": term_row["year_text"],
                "start_year": term_row["start_year"],
                "end_year": term_row["end_year"],
                "description": term_row["description"],
                "evidence_source_record_id": source_record_id,
                "review_status": content_seed_row["review_status"],
                "note": content_seed_row["note"],
            }
        )
        source_text_rows.append(
            {
                "source_record_id": source_record_id,
                "source_text_id": source_text_id,
                "source_id": history_config["source_id"],
                "source_name": history_config["source_name"],
                "source_material_id": material_id,
                "record_type": history_config["record_type"],
                "title": raw_row[history_config["title_column"]],
                "era_text": raw_row[history_config["era_column"]],
                "field": raw_row[history_config["field_column"]],
                "table_of_contents": raw_row[history_config["toc_column"]],
                "translated_text": translated_text,
                "original_text": original_text,
                "commentary_text": commentary_text,
                "url": raw_row[history_config["url_column"]],
                "markdown_path": raw_row[history_config["markdown_path_column"]],
                "review_status": history_config["review_status"],
            }
        )
        inscribed_on_rows.append(
            {
                "inscription_id": inscription_id,
                "canonical_id": canonical_id,
                "relation_basis": "CURATED_CONTENT_OBJECT_LINK",
                "review_status": content_seed_row["review_status"],
            }
        )
        presents_rows.append(
            {
                "source_text_id": source_text_id,
                "inscription_id": inscription_id,
                "evidence_field": history_config["text_column"],
                "review_status": source_seed_row["review_status"],
                "note": source_seed_row["note"],
            }
        )

    return inscription_rows, source_text_rows, inscribed_on_rows, presents_rows


def save_rows(rows, output_path, fieldnames):
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = output_path.with_suffix(f"{output_path.suffix}.tmp")

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
            writer.writerows(rows)

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
    history_config = read_single_config(args.history_source_seed_path)
    content_seed_rows = read_content_seed(args.content_seed_path)
    source_seed_rows = read_source_seed(args.source_seed_path)
    terms_by_id = read_indexed_rows(
        args.terms_path,
        "Term nodes",
        "term_id",
        {
            "term_id",
            "name",
            "hanja",
            "year_text",
            "start_year",
            "end_year",
            "description",
        },
    )
    canonical_by_id = read_indexed_rows(
        args.canonical_path,
        "CanonicalEntity nodes",
        "canonical_id",
        {"canonical_id", "name"},
    )
    heritage_by_canonical_id = read_indexed_rows(
        args.heritage_path,
        "CulturalHeritage nodes",
        "canonical_id",
        {"canonical_id", "heritage_id", "heritage_form"},
    )
    inscription_rows, source_text_rows, inscribed_on_rows, presents_rows = (
        build_graph_rows(
            project_root,
            history_config,
            content_seed_rows,
            source_seed_rows,
            terms_by_id,
            canonical_by_id,
            heritage_by_canonical_id,
        )
    )

    if args.save:
        save_rows(
            inscription_rows,
            args.nodes_dir / "inscription_contents.csv",
            [
                "term_id",
                "inscription_id",
                "name",
                "hanja",
                "inscription_kind",
                "content_language",
                "year_text",
                "start_year",
                "end_year",
                "description",
                "evidence_source_record_id",
                "review_status",
                "note",
            ],
        )
        save_rows(
            source_text_rows,
            args.nodes_dir / "source_texts.csv",
            [
                "source_record_id",
                "source_text_id",
                "source_id",
                "source_name",
                "source_material_id",
                "record_type",
                "title",
                "era_text",
                "field",
                "table_of_contents",
                "translated_text",
                "original_text",
                "commentary_text",
                "url",
                "markdown_path",
                "review_status",
            ],
        )
        save_rows(
            inscribed_on_rows,
            args.relations_dir / "inscription_content_inscribed_on.csv",
            [
                "inscription_id",
                "canonical_id",
                "relation_basis",
                "review_status",
            ],
        )
        save_rows(
            presents_rows,
            args.relations_dir / "source_text_presents_inscription.csv",
            [
                "source_text_id",
                "inscription_id",
                "evidence_field",
                "review_status",
                "note",
            ],
        )

    print(f"inscription_contents.csv: {len(inscription_rows)} rows")
    print(f"source_texts.csv: {len(source_text_rows)} rows")
    print(f"inscription_content_inscribed_on.csv: {len(inscribed_on_rows)} rows")
    print(f"source_text_presents_inscription.csv: {len(presents_rows)} rows")

    if not args.save:
        print("dry_run: no files saved. Use --save to write CSV files.")


if __name__ == "__main__":
    main()
