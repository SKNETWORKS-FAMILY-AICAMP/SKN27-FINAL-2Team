"""한국민족문화대백과사전 기사에서 canonical/source graph CSV를 생성한다.

백과사전 기사는 출처 레코드인 SourceArticle로 보존하고, 기사가 설명하는
역사 대상은 CanonicalEntity로 분리한다. 원문 body와 세부 속성은 이후
관계 추출 단계에서 사용하며, Neo4j SourceArticle에는 출처 확인에 필요한
메타데이터만 적재한다.
"""

import argparse
import csv
import json
from contextlib import ExitStack
from pathlib import Path

from neo4j_common import require_file, resolve_import_dir, resolve_project_root


def build_default_paths(script_path):
    neo4j_dir = script_path.parents[1]
    project_root = resolve_project_root(script_path)
    import_dir = resolve_import_dir(project_root)

    return {
        "source_seed": neo4j_dir / "seed" / "aks_source_seed.csv",
        "nodes_dir": import_dir / "nodes",
        "relations_dir": import_dir / "relations",
    }


def parse_args(default_paths):
    parser = argparse.ArgumentParser(
        description="AKS 기사를 CanonicalEntity/SourceArticle CSV로 변환한다."
    )
    parser.add_argument(
        "--source-seed-path",
        type=Path,
        default=default_paths["source_seed"],
    )
    parser.add_argument(
        "--articles-path",
        type=Path,
        default=None,
        help="지정하지 않으면 source seed의 raw_relative_path를 사용한다.",
    )
    parser.add_argument(
        "--nodes-dir",
        type=Path,
        default=default_paths["nodes_dir"],
    )
    parser.add_argument(
        "--relations-dir",
        type=Path,
        default=default_paths["relations_dir"],
    )
    parser.add_argument(
        "--save",
        action="store_true",
        help="CSV를 저장한다. 지정하지 않으면 검증만 수행한다.",
    )

    return parser.parse_args()


def read_source_config(source_seed_path):
    require_file(source_seed_path, "AKS source seed")

    with source_seed_path.open("r", encoding="utf-8-sig", newline="") as source_file:
        source_rows = list(csv.DictReader(source_file))

    if len(source_rows) == 0:
        raise ValueError(f"AKS source seed에 설정이 없습니다: {source_seed_path}")

    if len(source_rows) > 1:
        raise ValueError(
            "AKS source seed는 하나의 source 설정만 가져야 합니다: "
            f"{source_seed_path}"
        )

    source_config = source_rows[0]
    required_columns = {
        "source_id",
        "source_name",
        "source_record_id_prefix",
        "canonical_id_prefix",
        "raw_relative_path",
        "review_status",
    }
    missing_columns = [
        column_name
        for column_name in required_columns
        if not str(source_config.get(column_name, "")).strip()
    ]

    if len(missing_columns) > 0:
        missing_text = ", ".join(sorted(missing_columns))
        raise ValueError(f"AKS source seed 필수값이 비어 있습니다: {missing_text}")

    return {key: str(value).strip() for key, value in source_config.items()}


def resolve_articles_path(args, source_config, project_root):
    if args.articles_path is not None:
        return args.articles_path.resolve()

    return (project_root / source_config["raw_relative_path"]).resolve()


def build_prefixed_id(prefix, source_id):
    return f"{prefix}:{source_id}"


def clean_text(value):
    if value is None:
        return ""

    return str(value).strip()


def build_article_outputs(article, source_config):
    source_eid = clean_text(article.get("eid"))
    headword = clean_text(article.get("headword"))

    if source_eid == "":
        raise ValueError("AKS article eid가 비어 있습니다.")

    if headword == "":
        raise ValueError(f"AKS article headword가 비어 있습니다: {source_eid}")

    source_record_id = build_prefixed_id(
        source_config["source_record_id_prefix"],
        source_eid,
    )
    canonical_id = build_prefixed_id(
        source_config["canonical_id_prefix"],
        source_eid,
    )

    canonical_entity = {
        "canonical_id": canonical_id,
        "name": headword,
        "hanja": clean_text(article.get("origin")),
        "entity_type": clean_text(article.get("primaryTypePartA")),
        "entity_subtype": clean_text(article.get("primaryTypePartB")),
        "primary_type": clean_text(article.get("primaryType")),
        "secondary_type": clean_text(article.get("secondaryType")),
        "contents_type": clean_text(article.get("contentsType")),
        "era_text": clean_text(article.get("era")),
        "anchor_source_id": source_config["source_id"],
        "anchor_source_eid": source_eid,
        "review_status": source_config["review_status"],
    }
    source_article = {
        "source_record_id": source_record_id,
        "source_id": source_config["source_id"],
        "source_name": source_config["source_name"],
        "source_eid": source_eid,
        "url": clean_text(article.get("url")),
        "headword": headword,
        "headword_origin": clean_text(article.get("headwordOrigin")),
        "field": clean_text(article.get("field")),
        "primary_type": clean_text(article.get("primaryType")),
        "secondary_type": clean_text(article.get("secondaryType")),
        "contents_type": clean_text(article.get("contentsType")),
        "era_text": clean_text(article.get("era")),
        "definition": clean_text(article.get("definition")),
        "summary": clean_text(article.get("summary")),
        "reference": clean_text(article.get("reference")),
        "writer_info": clean_text(article.get("writerInfo")),
        "last_modified_time": clean_text(article.get("lastModifiedTime")),
    }
    describes_relation = {
        "start_source_record_id": source_record_id,
        "end_canonical_id": canonical_id,
        "relation_type": "DESCRIBES",
        "match_method": "SOURCE_ANCHOR",
        "confidence": "1.0",
        "review_status": source_config["review_status"],
    }

    return canonical_entity, source_article, describes_relation


def build_output_specs(args):
    return {
        "canonical_entities": {
            "path": args.nodes_dir / "canonical_entities.csv",
            "fieldnames": [
                "canonical_id",
                "name",
                "hanja",
                "entity_type",
                "entity_subtype",
                "primary_type",
                "secondary_type",
                "contents_type",
                "era_text",
                "anchor_source_id",
                "anchor_source_eid",
                "review_status",
            ],
        },
        "source_articles": {
            "path": args.nodes_dir / "source_articles.csv",
            "fieldnames": [
                "source_record_id",
                "source_id",
                "source_name",
                "source_eid",
                "url",
                "headword",
                "headword_origin",
                "field",
                "primary_type",
                "secondary_type",
                "contents_type",
                "era_text",
                "definition",
                "summary",
                "reference",
                "writer_info",
                "last_modified_time",
            ],
        },
        "source_article_describes_entity": {
            "path": args.relations_dir / "source_article_describes_entity.csv",
            "fieldnames": [
                "start_source_record_id",
                "end_canonical_id",
                "relation_type",
                "match_method",
                "confidence",
                "review_status",
            ],
        },
    }


def open_output_writers(exit_stack, output_specs):
    writers = {}

    for output_name, output_spec in output_specs.items():
        output_path = output_spec["path"]
        output_path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = output_path.with_suffix(f"{output_path.suffix}.tmp")
        output_file = exit_stack.enter_context(
            temporary_path.open("w", encoding="utf-8-sig", newline="")
        )
        writer = csv.DictWriter(
            output_file,
            fieldnames=output_spec["fieldnames"],
            extrasaction="raise",
        )
        writer.writeheader()
        writers[output_name] = {
            "writer": writer,
            "temporary_path": temporary_path,
            "output_path": output_path,
        }

    return writers


def remove_temporary_outputs(output_specs):
    for output_spec in output_specs.values():
        output_path = output_spec["path"]
        temporary_path = output_path.with_suffix(f"{output_path.suffix}.tmp")

        if temporary_path.exists():
            temporary_path.unlink()


def finalize_outputs(writers):
    for writer_info in writers.values():
        writer_info["temporary_path"].replace(writer_info["output_path"])


def process_articles(articles_path, source_config, output_specs, save_outputs):
    require_file(articles_path, "AKS articles_detail")
    seen_eids = set()
    record_count = 0
    writers = {}

    remove_temporary_outputs(output_specs)

    try:
        with ExitStack() as exit_stack:
            if save_outputs:
                writers = open_output_writers(exit_stack, output_specs)

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

                    canonical_entity, source_article, describes_relation = (
                        build_article_outputs(article, source_config)
                    )
                    source_eid = source_article["source_eid"]

                    if source_eid in seen_eids:
                        raise ValueError(
                            f"AKS articles_detail에 중복 eid가 있습니다: {source_eid}"
                        )

                    seen_eids.add(source_eid)
                    record_count += 1

                    if save_outputs:
                        writers["canonical_entities"]["writer"].writerow(
                            canonical_entity
                        )
                        writers["source_articles"]["writer"].writerow(source_article)
                        writers["source_article_describes_entity"]["writer"].writerow(
                            describes_relation
                        )

        if save_outputs:
            finalize_outputs(writers)
    except Exception:
        remove_temporary_outputs(output_specs)
        raise

    return record_count


def print_summary(record_count, articles_path, output_specs, save_outputs):
    print(f"articles_path: {articles_path}")
    print(f"validated_articles: {record_count}")

    for output_name, output_spec in output_specs.items():
        print(f"{output_name}.csv: {record_count} rows")
        print(f"output_path: {output_spec['path']}")

    if not save_outputs:
        print("dry_run: no files saved. Use --save to write CSV files.")


def main():
    script_path = Path(__file__).resolve()
    project_root = resolve_project_root(script_path)
    default_paths = build_default_paths(script_path)
    args = parse_args(default_paths)
    source_config = read_source_config(args.source_seed_path)
    articles_path = resolve_articles_path(args, source_config, project_root)
    output_specs = build_output_specs(args)
    record_count = process_articles(
        articles_path,
        source_config,
        output_specs,
        args.save,
    )

    print_summary(
        record_count,
        articles_path,
        output_specs,
        args.save,
    )


if __name__ == "__main__":
    main()
