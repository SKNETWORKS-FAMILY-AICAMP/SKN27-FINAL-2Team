"""
EDA 노트북에서 정리한 기준에 맞춰 raw CSV를 정규화한다.

- test/MK/prep_neo4j/check_terms.ipynb
- test/MK/prep_neo4j/ckeck_event.ipynb
- test/MK/prep_neo4j/check_people.ipynb

정규화 결과는 이후 사전 생성과 Neo4j 적재 CSV 생성의 입력으로 사용한다.
"""

import argparse
from pathlib import Path

import pandas as pd

from neo4j_common import (
    join_unique_values,
    print_summary,
    resolve_neo4j_dir,
    resolve_project_root,
    save_csv,
    select_existing_columns,
)


def parse_args(default_paths):
    parser = argparse.ArgumentParser(
        description="Normalize raw Neo4j source CSV files using EDA decisions."
    )
    parser.add_argument(
        "--history-terms-path",
        default=default_paths["history_terms"],
        type=Path,
        help="역사용어 raw CSV 경로.",
    )
    parser.add_argument(
        "--events-path",
        default=default_paths["events"],
        type=Path,
        help="ITKC 이벤트 raw CSV 경로.",
    )
    parser.add_argument(
        "--event-relations-path",
        default=default_paths["event_relations"],
        type=Path,
        help="ITKC event-person 관계 raw CSV 경로.",
    )
    parser.add_argument(
        "--person-relations-path",
        default=default_paths["person_relations"],
        type=Path,
        help="ITKC person-person 관계 raw CSV 경로.",
    )
    parser.add_argument(
        "--output-dir",
        default=default_paths["output_dir"],
        type=Path,
        help="Directory where normalized CSV files will be saved.",
    )
    parser.add_argument(
        "--save",
        action="store_true",
        help="CSV 파일을 저장한다. 지정하지 않으면 dry-run으로 요약만 출력한다.",
    )
    return parser.parse_args()


def build_source_url_frame(data_frame, key_columns, url_column, output_column):
    key_frame = data_frame.loc[:, key_columns].drop_duplicates().reset_index(drop=True)

    if url_column not in data_frame.columns:
        key_frame[output_column] = pd.NA
        return key_frame

    return (
        data_frame
        .groupby(key_columns, dropna=False)[url_column]
        .apply(join_unique_values)
        .reset_index(name=output_column)
    )


def normalize_terms(term_data):
    term_columns = [
        "term_id",
        "topterm_id",
        "term_name",
        "term_kind",
        "term_ch",
        "term_remark",
        "term_year",
        "term_times",
        "term_lk",
        "term_desc",
    ]
    normalized_terms = select_existing_columns(term_data, term_columns)

    if "term_kind" in normalized_terms.columns:
        normalized_terms = normalized_terms[normalized_terms["term_kind"].eq(2)].copy()

    if "term_id" in normalized_terms.columns:
        normalized_terms = normalized_terms.drop_duplicates(subset=["term_id"]).copy()

    return normalized_terms.reset_index(drop=True)


def normalize_events(event_data):
    source_urls = build_source_url_frame(
        event_data,
        ["event_id"],
        "detail_url",
        "source_urls",
    )
    drop_columns = [
        column
        for column in ["scope", "person_count", "detail_url"]
        if column in event_data.columns
    ]
    event_columns = [
        "event_id",
        "event_name",
        "subject_category",
        "period",
        "event_date",
        "related_event",
        "source_urls",
    ]

    normalized_events = (
        event_data
        .drop_duplicates(subset=["event_id"])
        .drop(columns=drop_columns)
        .merge(source_urls, on="event_id", how="left")
    )

    return select_existing_columns(normalized_events, event_columns).reset_index(drop=True)


def normalize_event_relations(event_relation_data):
    key_columns = ["event_id", "person_id", "relation_type"]
    source_urls = build_source_url_frame(
        event_relation_data,
        key_columns,
        "detail_url",
        "source_urls",
    )
    drop_columns = [
        column
        for column in [
            "scope",
            "related_event_id",
            "related_event_name",
            "evidence_url",
            "detail_url",
        ]
        if column in event_relation_data.columns
    ]
    event_relation_columns = [
        "event_id",
        "event_name",
        "relation_type",
        "person_id",
        "person_name",
        "source_urls",
    ]

    normalized_event_relations = (
        event_relation_data
        .drop_duplicates(subset=key_columns)
        .drop(columns=drop_columns)
        .merge(source_urls, on=key_columns, how="left")
    )

    return select_existing_columns(
        normalized_event_relations,
        event_relation_columns,
    ).reset_index(drop=True)


def normalize_person_relations(person_relation_data):
    person_relation_columns = [
        "person_id",
        "person_name",
        "relation_type",
        "related_person_id",
        "related_person_name",
        "related_birth_year",
        "related_death_year",
        "related_bonkwan",
        "related_father",
        "related_count",
        "evidence_url",
        "detail_url",
    ]

    normalized_person_relations = select_existing_columns(
        person_relation_data,
        person_relation_columns,
    )

    normalized_person_relations = normalized_person_relations.drop_duplicates(
        subset=["person_id", "related_person_id", "relation_type"]
    )

    return normalized_person_relations.reset_index(drop=True)


def build_default_paths(project_root, neo4j_dir):
    raw_data_dir = project_root / "etl" / "raw_data"
    itkc_network_dir = raw_data_dir / "한국고전종합DB_관계망"

    return {
        "history_terms": raw_data_dir / "교육부 국사편찬위원회_한국역사용어시소러스 정보_20211028 (1).csv",
        "events": itkc_network_dir / "itkc_events.csv",
        "event_relations": itkc_network_dir / "itkc_event_relations.csv",
        "person_relations": itkc_network_dir / "itkc_person_relations.csv",
        "output_dir": neo4j_dir / "normalized",
    }


def main():
    script_path = Path(__file__).resolve()
    neo4j_dir = resolve_neo4j_dir(script_path)
    project_root = resolve_project_root(neo4j_dir)
    default_paths = build_default_paths(project_root, neo4j_dir)
    args = parse_args(default_paths)

    term_data = pd.read_csv(args.history_terms_path)
    event_data = pd.read_csv(args.events_path)
    event_relation_data = pd.read_csv(args.event_relations_path)
    person_relation_data = pd.read_csv(args.person_relations_path)

    normalized_terms = normalize_terms(term_data)
    normalized_events = normalize_events(event_data)
    normalized_event_relations = normalize_event_relations(event_relation_data)
    normalized_person_relations = normalize_person_relations(person_relation_data)

    output_files = {
        "terms.csv": normalized_terms,
        "events.csv": normalized_events,
        "event_relations.csv": normalized_event_relations,
        "person_relations.csv": normalized_person_relations,
    }

    if args.save:
        for file_name, data_frame in output_files.items():
            save_csv(data_frame, args.output_dir / file_name)
            print_summary(file_name, data_frame)

        print(f"output_dir: {args.output_dir}")

    if not args.save:
        for file_name, data_frame in output_files.items():
            print_summary(file_name, data_frame)

        print("dry_run: no files saved. Use --save to write CSV files.")


if __name__ == "__main__":
    main()
