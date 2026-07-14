"""한국사 이미지 원천을 독립 SourceImage 노드와 검증된 DEPICTS 관계로 변환한다."""

import argparse
import csv
import re
from collections import defaultdict
from pathlib import Path
from urllib.parse import urlparse

from neo4j_common import require_file, resolve_import_dir, resolve_project_root


def build_default_paths(script_path):
    neo4j_dir = script_path.parents[1]
    project_root = resolve_project_root(script_path)
    import_dir = resolve_import_dir(project_root)

    return {
        "source_seed": neo4j_dir / "seed" / "image_source_seed.csv",
        "override_seed": neo4j_dir / "seed" / "image_entity_override_seed.csv",
        "canonical_path": import_dir / "nodes" / "canonical_entities.csv",
        "nodes_dir": import_dir / "nodes",
        "relations_dir": import_dir / "relations",
        "source_url_path": import_dir / "nodes" / "source_urls.csv",
        "related_content_path": neo4j_dir / "staging" / "image_related_content.csv",
        "review_path": neo4j_dir
        / "staging"
        / "source_image_entity_mapping_review.csv",
    }


def parse_args(default_paths):
    parser = argparse.ArgumentParser(
        description="한국사 이미지 SourceImage와 안전한 DEPICTS CSV를 생성한다."
    )
    parser.add_argument(
        "--source-seed-path",
        type=Path,
        default=default_paths["source_seed"],
    )
    parser.add_argument(
        "--override-seed-path",
        type=Path,
        default=default_paths["override_seed"],
    )
    parser.add_argument(
        "--canonical-path",
        type=Path,
        default=default_paths["canonical_path"],
    )
    parser.add_argument("--nodes-dir", type=Path, default=default_paths["nodes_dir"])
    parser.add_argument(
        "--relations-dir",
        type=Path,
        default=default_paths["relations_dir"],
    )
    parser.add_argument(
        "--source-url-path",
        type=Path,
        default=default_paths["source_url_path"],
    )
    parser.add_argument(
        "--related-content-path",
        type=Path,
        default=default_paths["related_content_path"],
    )
    parser.add_argument(
        "--review-path",
        type=Path,
        default=default_paths["review_path"],
    )
    parser.add_argument("--save", action="store_true")

    return parser.parse_args()


def clean_text(value):
    if value is None:
        return ""

    return str(value).strip()


def normalize_title(value):
    return re.sub(r"\s+", "", clean_text(value)).casefold()


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


def read_source_config(source_seed_path):
    fieldnames, rows = read_csv_rows(source_seed_path, "Image source seed")

    if len(rows) != 1:
        raise ValueError("Image source seed에는 설정이 정확히 한 행이어야 합니다.")

    required_values = {
        "source_id",
        "source_name",
        "source_record_id_prefix",
        "source_image_id_prefix",
        "record_type",
        "media_kind",
        "raw_relative_path",
        "saved_file_base_relative_path",
        "image_id_column",
        "title_column",
        "description_column",
        "era_column",
        "type_column",
        "field_column",
        "source_column",
        "usage_condition_column",
        "keywords_column",
        "related_content_column",
        "thumbnail_url_column",
        "original_url_column",
        "saved_file_column",
        "detail_url_column",
        "review_status",
    }
    missing_columns = sorted(required_values - fieldnames)
    source_config = rows[0]
    missing_values = sorted(
        column_name
        for column_name in required_values
        if source_config.get(column_name, "") == ""
    )

    if len(missing_columns) > 0:
        raise ValueError(
            "Image source seed 필수 컬럼이 없습니다: " + ", ".join(missing_columns)
        )

    if len(missing_values) > 0:
        raise ValueError(
            "Image source seed 필수값이 비어 있습니다: " + ", ".join(missing_values)
        )

    return source_config


def read_canonical_entities(canonical_path):
    fieldnames, rows = read_csv_rows(canonical_path, "CanonicalEntity nodes")
    required_columns = {"canonical_id", "name"}
    missing_columns = sorted(required_columns - fieldnames)

    if len(missing_columns) > 0:
        raise ValueError(
            "CanonicalEntity 필수 컬럼이 없습니다: " + ", ".join(missing_columns)
        )

    canonical_by_id = {}
    canonical_by_title = defaultdict(list)

    for row in rows:
        canonical_id = row["canonical_id"]

        if canonical_id in canonical_by_id:
            raise ValueError(f"CanonicalEntity ID가 중복되었습니다: {canonical_id}")

        canonical_by_id[canonical_id] = row
        canonical_by_title[normalize_title(row["name"])].append(row)

    return canonical_by_id, canonical_by_title


def read_mapping_overrides(override_seed_path):
    fieldnames, rows = read_csv_rows(
        override_seed_path,
        "Image entity override seed",
    )
    required_columns = {
        "source_image_eid",
        "canonical_id",
        "review_status",
        "note",
    }
    missing_columns = sorted(required_columns - fieldnames)

    if len(missing_columns) > 0:
        raise ValueError(
            "Image entity override 필수 컬럼이 없습니다: "
            + ", ".join(missing_columns)
        )

    overrides_by_eid = {}

    for row in rows:
        source_image_eid = row["source_image_eid"]
        required_values = {
            "source_image_eid",
            "canonical_id",
            "review_status",
        }
        missing_values = sorted(
            column_name
            for column_name in required_values
            if row.get(column_name, "") == ""
        )

        if len(missing_values) > 0:
            raise ValueError(
                "Image entity override 필수값이 비어 있습니다: "
                + ", ".join(missing_values)
            )

        if source_image_eid in overrides_by_eid:
            raise ValueError(
                f"Image entity override ID가 중복되었습니다: {source_image_eid}"
            )

        overrides_by_eid[source_image_eid] = row

    return overrides_by_eid


def read_image_records(project_root, source_config):
    image_path = (project_root / source_config["raw_relative_path"]).resolve()
    fieldnames, rows = read_csv_rows(image_path, "한국사 이미지 자료")
    configured_columns = {
        source_config["image_id_column"],
        source_config["title_column"],
        source_config["description_column"],
        source_config["era_column"],
        source_config["type_column"],
        source_config["field_column"],
        source_config["source_column"],
        source_config["usage_condition_column"],
        source_config["keywords_column"],
        source_config["related_content_column"],
        source_config["thumbnail_url_column"],
        source_config["original_url_column"],
        source_config["saved_file_column"],
        source_config["detail_url_column"],
    }
    missing_columns = sorted(configured_columns - fieldnames)

    if len(missing_columns) > 0:
        raise ValueError(
            "한국사 이미지 자료 필수 컬럼이 없습니다: " + ", ".join(missing_columns)
        )

    if len(rows) == 0:
        raise ValueError("한국사 이미지 자료가 비어 있습니다.")

    return rows


def build_source_image_row(image_row, source_config, project_root):
    image_eid = image_row[source_config["image_id_column"]]
    saved_file = image_row[source_config["saved_file_column"]]
    local_file_available = "N"

    if saved_file != "":
        normalized_saved_file = Path(saved_file.replace("\\", "/"))
        saved_file_path = (
            project_root
            / source_config["saved_file_base_relative_path"]
            / normalized_saved_file
        ).resolve()

        if saved_file_path.is_file():
            local_file_available = "Y"

    return {
        "source_record_id": (
            f"{source_config['source_record_id_prefix']}:{image_eid}"
        ),
        "source_image_id": (
            f"{source_config['source_image_id_prefix']}:{image_eid}"
        ),
        "source_id": source_config["source_id"],
        "source_name": source_config["source_name"],
        "source_image_eid": image_eid,
        "record_type": source_config["record_type"],
        "media_kind": source_config["media_kind"],
        "title": image_row[source_config["title_column"]],
        "description": image_row[source_config["description_column"]],
        "era_text": image_row[source_config["era_column"]],
        "image_type": image_row[source_config["type_column"]],
        "field": image_row[source_config["field_column"]],
        "image_source": image_row[source_config["source_column"]],
        "usage_condition": image_row[source_config["usage_condition_column"]],
        "keywords": image_row[source_config["keywords_column"]],
        "thumbnail_url": image_row[source_config["thumbnail_url_column"]],
        "original_url": image_row[source_config["original_url_column"]],
        "saved_file": saved_file,
        "local_file_available": local_file_available,
        "detail_url": image_row[source_config["detail_url_column"]],
        "review_status": source_config["review_status"],
    }


def build_depicts_row(source_image_row, canonical_id, mapping_method, review_status):
    return {
        "source_image_id": source_image_row["source_image_id"],
        "canonical_id": canonical_id,
        "mapping_method": mapping_method,
        "evidence_field": "title",
        "evidence_text": source_image_row["title"],
        "review_status": review_status,
    }


def build_review_row(source_image_row, review_status, candidates, note):
    return {
        "source_image_id": source_image_row["source_image_id"],
        "source_image_eid": source_image_row["source_image_eid"],
        "title": source_image_row["title"],
        "image_type": source_image_row["image_type"],
        "review_status": review_status,
        "candidate_canonical_ids": " | ".join(
            row["canonical_id"] for row in candidates
        ),
        "candidate_names": " | ".join(row["name"] for row in candidates),
        "note": note,
    }


def build_graph_rows(
    image_rows,
    source_config,
    project_root,
    canonical_by_id,
    canonical_by_title,
    overrides_by_eid,
):
    source_image_rows = []
    depicts_rows = []
    review_rows = []
    found_override_eids = set()
    source_image_eids = set()

    for image_row in image_rows:
        source_image_row = build_source_image_row(
            image_row,
            source_config,
            project_root,
        )
        source_image_eid = source_image_row["source_image_eid"]

        if source_image_eid == "":
            raise ValueError("한국사 이미지 자료의 이미지 ID가 비어 있습니다.")

        if source_image_eid in source_image_eids:
            raise ValueError(f"한국사 이미지 ID가 중복되었습니다: {source_image_eid}")

        source_image_eids.add(source_image_eid)
        source_image_rows.append(source_image_row)

        if source_image_eid in overrides_by_eid:
            override = overrides_by_eid[source_image_eid]
            canonical_id = override["canonical_id"]

            if canonical_id not in canonical_by_id:
                raise ValueError(
                    f"Image override 대상 CanonicalEntity가 없습니다: {canonical_id}"
                )

            found_override_eids.add(source_image_eid)
            depicts_rows.append(
                build_depicts_row(
                    source_image_row,
                    canonical_id,
                    "CURATED_OVERRIDE",
                    override["review_status"],
                )
            )
            continue

        normalized_title = normalize_title(source_image_row["title"])
        candidates = canonical_by_title.get(normalized_title, [])

        if len(candidates) == 1:
            depicts_rows.append(
                build_depicts_row(
                    source_image_row,
                    candidates[0]["canonical_id"],
                    "NORMALIZED_EXACT_TITLE",
                    "AUTO_EXACT",
                )
            )
        elif len(candidates) == 0:
            review_rows.append(
                build_review_row(
                    source_image_row,
                    "NO_EXACT_MATCH",
                    [],
                    "키워드와 관련콘텐츠는 묘사 대상 근거로 사용하지 않음",
                )
            )
        elif len(candidates) > 1:
            review_rows.append(
                build_review_row(
                    source_image_row,
                    "AMBIGUOUS_EXACT_TITLE",
                    candidates,
                    "동일 표제 CanonicalEntity가 여러 개라 자동 연결하지 않음",
                )
            )

    missing_override_eids = sorted(set(overrides_by_eid) - found_override_eids)

    if len(missing_override_eids) > 0:
        raise ValueError(
            "한국사 이미지 자료에서 override 대상을 찾지 못했습니다: "
            + ", ".join(missing_override_eids)
        )

    return source_image_rows, depicts_rows, review_rows


def read_related_content(related_content_path):
    fieldnames, rows = read_csv_rows(
        related_content_path,
        "Image related content staging",
    )
    required_columns = {
        "source_image_eid",
        "content_title",
        "content_collection",
        "url",
    }
    missing_columns = sorted(required_columns - fieldnames)

    if len(missing_columns) > 0:
        raise ValueError(
            "Image related content staging 필수 컬럼이 없습니다: "
            + ", ".join(missing_columns)
        )

    relation_keys = set()

    for row_number, row in enumerate(rows, start=2):
        missing_values = sorted(
            column_name
            for column_name in required_columns
            if row[column_name] == ""
        )

        if len(missing_values) > 0:
            raise ValueError(
                "Image related content staging 필수값이 비어 있습니다: "
                f"row={row_number}, columns={','.join(missing_values)}"
            )

        parsed_url = urlparse(row["url"])

        if (
            parsed_url.scheme not in {"http", "https"}
            or parsed_url.netloc == ""
            or re.search(r"\s", row["url"]) is not None
        ):
            raise ValueError(
                "Image related content staging URL이 올바르지 않습니다: "
                f"row={row_number}, value={row['url']}"
            )

        relation_key = (row["source_image_eid"], row["url"])

        if relation_key in relation_keys:
            raise ValueError(
                "Image related content staging 관계가 중복되었습니다: "
                f"source_image_eid={row['source_image_eid']}, url={row['url']}"
            )

        relation_keys.add(relation_key)

    return rows


def read_source_urls(source_url_path):
    fieldnames, rows = read_csv_rows(source_url_path, "SourceUrl nodes")
    required_columns = {"source_url_id", "url"}
    missing_columns = sorted(required_columns - fieldnames)

    if len(missing_columns) > 0:
        raise ValueError(
            "SourceUrl 필수 컬럼이 없습니다: " + ", ".join(missing_columns)
        )

    source_url_id_by_url = {}
    source_url_ids = set()

    for row in rows:
        source_url_id = row["source_url_id"]
        url = row["url"]

        if source_url_id == "" or url == "":
            raise ValueError("SourceUrl ID 또는 URL이 비어 있습니다.")

        if url in source_url_id_by_url:
            raise ValueError(f"SourceUrl URL이 중복되었습니다: {url}")

        if source_url_id in source_url_ids:
            raise ValueError(f"SourceUrl ID가 중복되었습니다: {source_url_id}")

        source_url_ids.add(source_url_id)
        source_url_id_by_url[url] = source_url_id

    return source_url_id_by_url


def build_related_content_relations(
    image_rows,
    source_image_rows,
    source_config,
    related_content_rows,
    source_url_id_by_url,
):
    source_image_id_by_eid = {
        row["source_image_eid"]: row["source_image_id"]
        for row in source_image_rows
    }
    expected_counts = {}

    for image_row in image_rows:
        source_image_eid = image_row[source_config["image_id_column"]]
        raw_value = image_row[source_config["related_content_column"]]
        expected_counts[source_image_eid] = len(
            [line for line in raw_value.splitlines() if line.strip() != ""]
        )

    actual_counts = defaultdict(int)
    relation_rows = []

    for row in related_content_rows:
        source_image_eid = row["source_image_eid"]
        url = row["url"]
        source_image_id = source_image_id_by_eid.get(source_image_eid)
        source_url_id = source_url_id_by_url.get(url)

        if source_image_id is None:
            raise ValueError(
                "Image related content staging의 SourceImage가 없습니다: "
                f"{source_image_eid}"
            )

        if source_url_id is None:
            raise ValueError(
                "Image related content staging의 SourceUrl이 없습니다: "
                f"{url}"
            )

        actual_counts[source_image_eid] += 1
        relation_rows.append(
            {
                "source_image_id": source_image_id,
                "source_url_id": source_url_id,
                "relation_type": "HAS_RELATED_CONTENT",
                "content_title": row["content_title"],
                "content_collection": row["content_collection"],
                "mapping_method": "SOURCE_DECLARED_URL",
                "review_status": "SOURCE_ANCHORED",
            }
        )

    count_mismatches = [
        (
            source_image_eid,
            expected_count,
            actual_counts.get(source_image_eid, 0),
        )
        for source_image_eid, expected_count in expected_counts.items()
        if expected_count != actual_counts.get(source_image_eid, 0)
    ]

    if len(count_mismatches) > 0:
        mismatch_text = "; ".join(
            f"{source_image_eid}: raw={expected_count}, staging={actual_count}"
            for source_image_eid, expected_count, actual_count in count_mismatches[:10]
        )
        raise ValueError(
            "이미지 관련콘텐츠 원천과 staging 건수가 일치하지 않습니다: "
            + mismatch_text
        )

    return relation_rows


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
    source_config = read_source_config(args.source_seed_path)
    canonical_by_id, canonical_by_title = read_canonical_entities(
        args.canonical_path
    )
    overrides_by_eid = read_mapping_overrides(args.override_seed_path)
    image_rows = read_image_records(project_root, source_config)
    related_content_rows = read_related_content(args.related_content_path)
    source_url_id_by_url = read_source_urls(args.source_url_path)
    source_image_rows, depicts_rows, review_rows = build_graph_rows(
        image_rows,
        source_config,
        project_root,
        canonical_by_id,
        canonical_by_title,
        overrides_by_eid,
    )
    related_content_relation_rows = build_related_content_relations(
        image_rows,
        source_image_rows,
        source_config,
        related_content_rows,
        source_url_id_by_url,
    )
    source_image_path = args.nodes_dir / "source_images.csv"
    depicts_path = args.relations_dir / "source_image_depicts_entity.csv"
    related_content_path = (
        args.relations_dir / "source_image_has_related_content.csv"
    )

    if args.save:
        save_rows(
            source_image_rows,
            source_image_path,
            [
                "source_record_id",
                "source_image_id",
                "source_id",
                "source_name",
                "source_image_eid",
                "record_type",
                "media_kind",
                "title",
                "description",
                "era_text",
                "image_type",
                "field",
                "image_source",
                "usage_condition",
                "keywords",
                "thumbnail_url",
                "original_url",
                "saved_file",
                "local_file_available",
                "detail_url",
                "review_status",
            ],
        )
        save_rows(
            related_content_relation_rows,
            related_content_path,
            [
                "source_image_id",
                "source_url_id",
                "relation_type",
                "content_title",
                "content_collection",
                "mapping_method",
                "review_status",
            ],
        )
        save_rows(
            depicts_rows,
            depicts_path,
            [
                "source_image_id",
                "canonical_id",
                "mapping_method",
                "evidence_field",
                "evidence_text",
                "review_status",
            ],
        )
        save_rows(
            review_rows,
            args.review_path,
            [
                "source_image_id",
                "source_image_eid",
                "title",
                "image_type",
                "review_status",
                "candidate_canonical_ids",
                "candidate_names",
                "note",
            ],
        )

    print(f"source_images.csv: {len(source_image_rows)} rows")
    print(f"source_image_depicts_entity.csv: {len(depicts_rows)} rows")
    print(
        "source_image_has_related_content.csv: "
        f"{len(related_content_relation_rows)} rows"
    )
    print(f"source_image_entity_mapping_review.csv: {len(review_rows)} rows")

    if not args.save:
        print("dry_run: no files saved. Use --save to write CSV files.")


if __name__ == "__main__":
    main()
