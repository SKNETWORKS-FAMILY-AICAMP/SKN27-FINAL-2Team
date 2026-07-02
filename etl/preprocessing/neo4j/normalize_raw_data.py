"""
Raw CSV normalization based on EDA notebooks:

- test/MK/prep_neo4j/check_terms.ipynb
- test/MK/prep_neo4j/ckeck_event.ipynb
- test/MK/prep_neo4j/check_people.ipynb

Outputs are saved as normalized CSV files for later dictionary generation and
Neo4j import preparation.
"""

import argparse
from pathlib import Path

import pandas as pd


def parse_args(default_paths):
    parser = argparse.ArgumentParser(
        description="Normalize raw Neo4j source CSV files using EDA decisions."
    )
    parser.add_argument(
        "--history-terms-path",
        default=default_paths["history_terms"],
        type=Path,
        help="Path to history terms raw CSV.",
    )
    parser.add_argument(
        "--events-path",
        default=default_paths["events"],
        type=Path,
        help="Path to ITKC events raw CSV.",
    )
    parser.add_argument(
        "--event-relations-path",
        default=default_paths["event_relations"],
        type=Path,
        help="Path to ITKC event-person relations raw CSV.",
    )
    parser.add_argument(
        "--person-relations-path",
        default=default_paths["person_relations"],
        type=Path,
        help="Path to ITKC person-person relations raw CSV.",
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
        help="Write normalized CSV files. Without this flag, only print summaries.",
    )
    return parser.parse_args()


def select_existing_columns(data_frame, columns):
    existing_columns = [column for column in columns if column in data_frame.columns]
    return data_frame.loc[:, existing_columns].copy()


def join_unique_values(values):
    text_values = values.dropna().astype(str).str.strip()
    text_values = text_values[text_values.ne("")]
    return "|".join(sorted(text_values.unique()))


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


def save_csv(data_frame, output_path):
    output_path.parent.mkdir(parents=True, exist_ok=True)
    data_frame.to_csv(output_path, index=False, encoding="utf-8-sig")


def print_summary(file_name, data_frame):
    print(f"{file_name}: {len(data_frame)} rows, {len(data_frame.columns)} columns")


def build_default_paths(project_root, script_path):
    raw_data_dir = project_root / "etl" / "raw_data"
    itkc_network_dir = raw_data_dir / "한국고전종합DB_관계망"

    return {
        "history_terms": raw_data_dir / "교육부 국사편찬위원회_한국역사용어시소러스 정보_20211028 (1).csv",
        "events": itkc_network_dir / "itkc_events.csv",
        "event_relations": itkc_network_dir / "itkc_event_relations.csv",
        "person_relations": itkc_network_dir / "itkc_person_relations.csv",
        "output_dir": script_path.parent / "normalized",
    }


def main():
    script_path = Path(__file__).resolve()
    project_root = script_path.parents[3]
    default_paths = build_default_paths(project_root, script_path)
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
