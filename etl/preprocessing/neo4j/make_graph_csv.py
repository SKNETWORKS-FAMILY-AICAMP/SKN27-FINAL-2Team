"""
정규화 CSV, 사전 CSV, staging CSV를 Neo4j 적재용 최종 노드/관계 CSV로 변환한다.

기본 실행은 dry-run이다. CSV 저장이 필요할 때만 --save를 사용한다.
"""

import argparse
import re
from pathlib import Path

import pandas as pd


def parse_args(default_paths):
    parser = argparse.ArgumentParser(
        description="Neo4j 적재용 최종 node/relation CSV를 만든다."
    )
    parser.add_argument("--terms-path", default=default_paths["terms"], type=Path)
    parser.add_argument("--events-path", default=default_paths["events"], type=Path)
    parser.add_argument(
        "--event-relations-path",
        default=default_paths["event_relations"],
        type=Path,
    )
    parser.add_argument(
        "--person-relations-path",
        default=default_paths["person_relations"],
        type=Path,
    )
    parser.add_argument(
        "--category-dictionary-path",
        default=default_paths["category_dictionary"],
        type=Path,
    )
    parser.add_argument(
        "--event-category-dictionary-path",
        default=default_paths["event_category_dictionary"],
        type=Path,
    )
    parser.add_argument(
        "--category-mapping-path",
        default=default_paths["category_mapping"],
        type=Path,
    )
    parser.add_argument(
        "--period-dictionary-path",
        default=default_paths["period_dictionary"],
        type=Path,
    )
    parser.add_argument(
        "--relation-type-dictionary-path",
        default=default_paths["relation_type_dictionary"],
        type=Path,
    )
    parser.add_argument(
        "--source-url-dictionary-path",
        default=default_paths["source_url_dictionary"],
        type=Path,
    )
    parser.add_argument(
        "--term-category-relation-path",
        default=default_paths["term_category_relation"],
        type=Path,
    )
    parser.add_argument(
        "--event-category-relation-path",
        default=default_paths["event_category_relation"],
        type=Path,
    )
    parser.add_argument(
        "--event-date-parse-path",
        default=default_paths["event_date_parse"],
        type=Path,
    )
    parser.add_argument(
        "--nodes-dir",
        default=default_paths["nodes_dir"],
        type=Path,
        help="최종 node CSV 저장 폴더.",
    )
    parser.add_argument(
        "--relations-dir",
        default=default_paths["relations_dir"],
        type=Path,
        help="최종 relation CSV 저장 폴더.",
    )
    parser.add_argument(
        "--save",
        action="store_true",
        help="CSV 파일을 저장한다. 지정하지 않으면 dry-run으로 요약만 출력한다.",
    )
    return parser.parse_args()


def build_sequential_ids(prefix, row_count, width):
    return [f"{prefix}_{idx:0{width}d}" for idx in range(1, row_count + 1)]


def require_file(input_path, purpose):
    if not input_path.exists():
        raise FileNotFoundError(f"{purpose} 파일이 없습니다: {input_path}")


def read_csv(input_path, purpose):
    require_file(input_path, purpose)
    return pd.read_csv(input_path, dtype=str)


def clean_value(value):
    if pd.isna(value):
        return pd.NA

    clean_text = str(value).strip()

    if clean_text == "":
        return pd.NA

    if clean_text.lower() == "nan":
        return pd.NA

    return clean_text


def first_value(values):
    for value in values:
        clean_text = clean_value(value)

        if pd.notna(clean_text):
            return clean_text

    return pd.NA


def unique_join(values):
    clean_values = []

    for value in values:
        clean_text = clean_value(value)

        if pd.notna(clean_text):
            clean_values.append(clean_text)

    unique_values = sorted(set(clean_values))

    if len(unique_values) == 0:
        return pd.NA

    return "|".join(unique_values)


def split_pipe_values(value):
    if pd.isna(value):
        return []

    tokens = []

    for raw_token in str(value).split("|"):
        clean_token = clean_value(raw_token)

        if pd.notna(clean_token):
            tokens.append(clean_token)

    return tokens


def split_period_tokens(period_text):
    if pd.isna(period_text):
        return []

    tokens = []

    for raw_token in re.split(r"-|,|~", str(period_text)):
        clean_token = clean_value(raw_token)

        if pd.notna(clean_token):
            tokens.append(clean_token)

    return tokens


def filter_actual_terms(terms_data):
    target_data = terms_data.copy()

    if "term_kind" in target_data.columns:
        target_data = target_data[target_data["term_kind"].eq("2")].copy()

    return target_data


def build_term_nodes(terms_data):
    target_data = filter_actual_terms(terms_data)
    term_nodes = target_data[
        [
            "term_id",
            "term_name",
            "term_ch",
            "term_remark",
            "term_year",
            "term_times",
            "term_lk",
            "term_desc",
            "topterm_id",
        ]
    ].drop_duplicates(subset=["term_id"]).copy()
    term_nodes = term_nodes.rename(
        columns={
            "term_name": "name",
            "term_ch": "hanja",
            "term_remark": "remark",
            "term_year": "year_text",
            "term_times": "period_text",
            "term_lk": "category_text",
            "term_desc": "description",
        }
    )
    term_nodes["source"] = "history_terms"

    return term_nodes[
        [
            "term_id",
            "name",
            "hanja",
            "remark",
            "year_text",
            "period_text",
            "category_text",
            "description",
            "topterm_id",
            "source",
        ]
    ]


def build_category_nodes(category_dictionary):
    category_nodes = category_dictionary.copy()
    category_nodes = category_nodes.rename(columns={"category_name": "name"})

    return category_nodes[
        [
            "category_id",
            "name",
            "category_path",
            "parent_category_id",
            "parent_category_path",
            "depth",
            "root_category_name",
            "term_count",
            "direct_term_count",
            "source",
            "review_status",
        ]
    ]


def build_event_category_nodes(event_category_dictionary):
    event_category_nodes = event_category_dictionary.copy()
    event_category_nodes = event_category_nodes.rename(
        columns={"event_category_name": "name"}
    )

    return event_category_nodes[
        ["event_category_id", "name", "event_count", "source", "review_status"]
    ]


def build_event_nodes(events_data, event_date_parse):
    event_nodes = events_data.merge(event_date_parse, on="event_id", how="left")
    event_nodes = event_nodes.rename(
        columns={
            "event_name": "name",
            "period": "period_text",
            "related_event": "related_event_name",
        }
    )

    return event_nodes[
        [
            "event_id",
            "name",
            "subject_category",
            "period_text",
            "event_date",
            "related_event_name",
            "source_urls",
            "start_year",
            "end_year",
            "start_month",
            "end_month",
            "start_reign_name",
            "start_reign_year",
            "end_reign_name",
            "end_reign_year",
            "date_precision",
            "parse_status",
        ]
    ]


def build_event_group_nodes(events_data):
    group_data = events_data[["event_id", "related_event"]].dropna().copy()
    group_data["event_group_name"] = group_data["related_event"].map(clean_value)
    group_data = group_data.dropna(subset=["event_group_name"])

    if len(group_data) == 0:
        return pd.DataFrame(columns=["event_group_id", "name", "event_count", "source"])

    event_group_nodes = (
        group_data
        .groupby("event_group_name")["event_id"]
        .nunique()
        .reset_index(name="event_count")
        .sort_values("event_group_name")
        .reset_index(drop=True)
    )
    event_group_nodes.insert(
        0,
        "event_group_id",
        build_sequential_ids("EVENT_GROUP", len(event_group_nodes), 5),
    )
    event_group_nodes = event_group_nodes.rename(columns={"event_group_name": "name"})
    event_group_nodes["source"] = "events.related_event"

    return event_group_nodes[["event_group_id", "name", "event_count", "source"]]


def build_period_nodes(period_dictionary):
    period_nodes = period_dictionary.copy()
    period_nodes = period_nodes.rename(columns={"period_name": "name"})

    return period_nodes[
        [
            "period_id",
            "name",
            "period_level",
            "start_year",
            "end_year",
            "term_count",
            "event_count",
            "source",
            "source_values",
            "review_status",
            "note",
        ]
    ]


def build_source_url_nodes(source_url_dictionary):
    return source_url_dictionary[
        [
            "source_url_id",
            "url",
            "source_tables",
            "source_columns",
            "source_types",
            "source_count",
            "use_for_rag",
            "fetch_status",
            "note",
        ]
    ].copy()


def build_people_from_event_relations(event_relations_data):
    people_data = event_relations_data[["person_id", "person_name"]].dropna().copy()
    people_data["birth_year"] = pd.NA
    people_data["death_year"] = pd.NA
    people_data["bonkwan"] = pd.NA
    people_data["father_name"] = pd.NA
    people_data["detail_urls"] = pd.NA
    people_data["source"] = "event_relations.person_id"

    return people_data


def build_people_from_person_sources(person_relations_data):
    people_data = person_relations_data[["person_id", "person_name", "detail_url"]].copy()
    people_data["birth_year"] = pd.NA
    people_data["death_year"] = pd.NA
    people_data["bonkwan"] = pd.NA
    people_data["father_name"] = pd.NA
    people_data = people_data.rename(columns={"detail_url": "detail_urls"})
    people_data["source"] = "person_relations.person_id"

    return people_data[
        [
            "person_id",
            "person_name",
            "birth_year",
            "death_year",
            "bonkwan",
            "father_name",
            "detail_urls",
            "source",
        ]
    ]


def build_people_from_related_persons(person_relations_data):
    people_data = person_relations_data[
        [
            "related_person_id",
            "related_person_name",
            "related_birth_year",
            "related_death_year",
            "related_bonkwan",
            "related_father",
        ]
    ].copy()
    people_data = people_data.rename(
        columns={
            "related_person_id": "person_id",
            "related_person_name": "person_name",
            "related_birth_year": "birth_year",
            "related_death_year": "death_year",
            "related_bonkwan": "bonkwan",
            "related_father": "father_name",
        }
    )
    people_data["detail_urls"] = pd.NA
    people_data["source"] = "person_relations.related_person_id"

    return people_data


def build_person_nodes(event_relations_data, person_relations_data):
    # Person 노드는 event_relations의 사건 참여자와 person_relations의 양쪽 인물 ID를 합쳐 만든다.
    # 같은 person_id에 여러 속성 후보가 있으면 고유값을 "|"로 묶어 검수 가능하게 보존한다.
    people_parts = [
        build_people_from_event_relations(event_relations_data),
        build_people_from_person_sources(person_relations_data),
        build_people_from_related_persons(person_relations_data),
    ]
    people_data = pd.concat(people_parts, ignore_index=True)
    people_data = people_data.dropna(subset=["person_id"]).copy()

    person_nodes = (
        people_data
        .groupby("person_id", dropna=False)
        .agg(
            name=("person_name", first_value),
            name_candidates=("person_name", unique_join),
            birth_year=("birth_year", unique_join),
            death_year=("death_year", unique_join),
            bonkwan=("bonkwan", unique_join),
            father_name=("father_name", unique_join),
            detail_urls=("detail_urls", unique_join),
            source=("source", unique_join),
        )
        .reset_index()
        .sort_values("person_id")
        .reset_index(drop=True)
    )

    return person_nodes[
        [
            "person_id",
            "name",
            "name_candidates",
            "birth_year",
            "death_year",
            "bonkwan",
            "father_name",
            "detail_urls",
            "source",
        ]
    ]


def build_term_has_category(term_category_relation):
    relation_data = term_category_relation.copy()
    relation_data = relation_data.rename(
        columns={
            "term_id": "start_term_id",
            "category_id": "end_category_id",
        }
    )
    relation_data["relation_type"] = "HAS_CATEGORY"

    return relation_data[
        [
            "start_term_id",
            "end_category_id",
            "relation_type",
            "category_path",
            "source_term_lk",
        ]
    ]


def build_category_subcategory_of(category_dictionary):
    relation_data = category_dictionary.dropna(subset=["parent_category_id"]).copy()
    relation_data = relation_data.rename(
        columns={
            "category_id": "start_category_id",
            "parent_category_id": "end_category_id",
        }
    )
    relation_data["relation_type"] = "SUBCATEGORY_OF"

    return relation_data[
        [
            "start_category_id",
            "end_category_id",
            "relation_type",
            "category_path",
            "parent_category_path",
        ]
    ]


def build_event_has_event_category(event_category_relation):
    relation_data = event_category_relation.copy()
    relation_data = relation_data.rename(
        columns={
            "event_id": "start_event_id",
            "event_category_id": "end_event_category_id",
        }
    )
    relation_data["relation_type"] = "HAS_EVENT_CATEGORY"

    return relation_data[
        [
            "start_event_id",
            "end_event_category_id",
            "relation_type",
            "event_category_name",
            "source_subject_category",
        ]
    ]


def filter_mapped_categories(category_mapping):
    return category_mapping.dropna(subset=["mapped_category_id"]).copy()


def build_event_category_mapped_to_category(category_mapping):
    mapped_data = filter_mapped_categories(category_mapping)
    mapped_data = mapped_data.rename(
        columns={
            "event_category_id": "start_event_category_id",
            "mapped_category_id": "end_category_id",
        }
    )
    mapped_data["relation_type"] = "MAPPED_TO_CATEGORY"

    return mapped_data[
        [
            "start_event_category_id",
            "end_category_id",
            "relation_type",
            "event_category_name",
            "mapped_category_path",
            "mapping_type",
            "confidence",
            "review_status",
            "note",
        ]
    ]


def build_event_has_category(event_category_relation, category_mapping):
    # EventCategory가 표준 Category로 매핑된 경우에만 Event -> Category 직접 관계를 만든다.
    # 매핑되지 않은 이벤트 분류는 event_has_event_category 관계로 원형을 보존한다.
    mapped_data = filter_mapped_categories(category_mapping)
    relation_data = event_category_relation.merge(
        mapped_data,
        on=["event_category_id", "event_category_name"],
        how="inner",
    )
    relation_data = relation_data.rename(
        columns={
            "event_id": "start_event_id",
            "mapped_category_id": "end_category_id",
        }
    )
    relation_data["relation_type"] = "HAS_CATEGORY"

    return relation_data[
        [
            "start_event_id",
            "end_category_id",
            "relation_type",
            "event_category_id",
            "event_category_name",
            "mapped_category_path",
            "mapping_type",
            "confidence",
            "review_status",
        ]
    ]


def build_period_lookup(period_dictionary):
    return dict(zip(period_dictionary["period_name"], period_dictionary["period_id"]))


def build_term_in_period(terms_data, period_dictionary):
    period_lookup = build_period_lookup(period_dictionary)
    relation_rows = []
    target_data = filter_actual_terms(terms_data).dropna(subset=["term_times"]).copy()

    for row in target_data[["term_id", "term_times"]].itertuples(index=False):
        for period_name in split_period_tokens(row.term_times):
            period_id = period_lookup.get(period_name)

            if pd.notna(period_id):
                relation_rows.append(
                    {
                        "start_term_id": row.term_id,
                        "end_period_id": period_id,
                        "relation_type": "IN_PERIOD",
                        "period_name": period_name,
                        "source_period_text": row.term_times,
                    }
                )

    return pd.DataFrame(relation_rows)


def build_event_in_period(events_data, period_dictionary):
    period_lookup = build_period_lookup(period_dictionary)
    relation_rows = []
    target_data = events_data.dropna(subset=["period"]).copy()

    for row in target_data[["event_id", "period"]].itertuples(index=False):
        for period_name in split_period_tokens(row.period):
            period_id = period_lookup.get(period_name)

            if pd.notna(period_id):
                relation_rows.append(
                    {
                        "start_event_id": row.event_id,
                        "end_period_id": period_id,
                        "relation_type": "IN_PERIOD",
                        "period_name": period_name,
                        "source_period_text": row.period,
                    }
                )

    return pd.DataFrame(relation_rows)


def build_event_part_of_group(events_data, event_group_nodes):
    group_lookup = dict(zip(event_group_nodes["name"], event_group_nodes["event_group_id"]))
    relation_rows = []
    target_data = events_data.dropna(subset=["related_event"]).copy()

    for row in target_data[["event_id", "related_event"]].itertuples(index=False):
        group_name = clean_value(row.related_event)
        group_id = group_lookup.get(group_name)

        if pd.notna(group_id):
            relation_rows.append(
                {
                    "start_event_id": row.event_id,
                    "end_event_group_id": group_id,
                    "relation_type": "PART_OF_EVENT_GROUP",
                    "event_group_name": group_name,
                }
            )

    return pd.DataFrame(relation_rows).drop_duplicates()


def build_person_involved_in_event(event_relations_data):
    relation_data = event_relations_data.dropna(subset=["event_id", "person_id"]).copy()
    relation_data = relation_data.drop_duplicates(
        subset=["event_id", "person_id", "relation_type"]
    ).reset_index(drop=True)
    relation_data.insert(
        0,
        "event_person_relation_id",
        build_sequential_ids("EVENT_PERSON_REL", len(relation_data), 6),
    )
    relation_data = relation_data.rename(
        columns={
            "person_id": "start_person_id",
            "event_id": "end_event_id",
            "relation_type": "raw_relation_type",
        }
    )
    relation_data["relation_type"] = "INVOLVED_IN_EVENT"

    return relation_data[
        [
            "event_person_relation_id",
            "start_person_id",
            "end_event_id",
            "relation_type",
            "raw_relation_type",
            "person_name",
            "event_name",
            "source_urls",
        ]
    ]


def build_person_related_to_person(person_relations_data, relation_type_dictionary):
    # 관계 타입은 Neo4j 관계명으로 쪼개기보다 RELATED_TO 하나로 두고,
    # raw/normalized relation type을 속성으로 보존한다.
    relation_data = person_relations_data.dropna(
        subset=["person_id", "related_person_id", "relation_type"]
    ).copy()
    relation_data = relation_data.merge(
        relation_type_dictionary,
        left_on="relation_type",
        right_on="raw_relation_type",
        how="left",
    )
    relation_data = relation_data.drop_duplicates(
        subset=["person_id", "related_person_id", "relation_type"]
    ).reset_index(drop=True)
    relation_data.insert(
        0,
        "person_relation_id",
        build_sequential_ids("PERSON_REL", len(relation_data), 7),
    )
    relation_data = relation_data.rename(
        columns={
            "person_id": "start_person_id",
            "related_person_id": "end_person_id",
            "relation_type": "raw_relation_type",
        }
    )
    relation_data["relation_type"] = "RELATED_TO"

    return relation_data[
        [
            "person_relation_id",
            "start_person_id",
            "end_person_id",
            "relation_type",
            "raw_relation_type",
            "normalized_relation_type",
            "relation_group",
            "direction_rule",
            "is_symmetric",
            "inverse_relation_type",
            "person_name",
            "related_person_name",
            "related_count",
            "evidence_url",
            "detail_url",
        ]
    ]


def build_url_lookup(source_url_nodes):
    return dict(zip(source_url_nodes["url"], source_url_nodes["source_url_id"]))


def append_url_relation_rows(relation_rows, start_id, url_value, url_lookup, source_column):
    for url in split_pipe_values(url_value):
        source_url_id = url_lookup.get(url)

        if pd.notna(source_url_id):
            relation_rows.append(
                {
                    "start_id": start_id,
                    "end_source_url_id": source_url_id,
                    "relation_type": "HAS_SOURCE_URL",
                    "source_column": source_column,
                    "url": url,
                }
            )


def build_event_has_source_url(events_data, event_relations_data, source_url_nodes):
    # 사건 상세 URL은 Event 노드와 SourceUrl 노드의 관계로 연결한다.
    # event_relations에도 같은 사건 URL이 있을 수 있으므로 중복 제거한다.
    url_lookup = build_url_lookup(source_url_nodes)
    relation_rows = []

    for row in events_data[["event_id", "source_urls"]].dropna().itertuples(index=False):
        append_url_relation_rows(
            relation_rows,
            row.event_id,
            row.source_urls,
            url_lookup,
            "events.source_urls",
        )

    for row in event_relations_data[["event_id", "source_urls"]].dropna().itertuples(index=False):
        append_url_relation_rows(
            relation_rows,
            row.event_id,
            row.source_urls,
            url_lookup,
            "event_relations.source_urls",
        )

    relation_data = pd.DataFrame(relation_rows).drop_duplicates()
    relation_data = relation_data.rename(columns={"start_id": "start_event_id"})

    return relation_data[
        ["start_event_id", "end_source_url_id", "relation_type", "source_column", "url"]
    ]


def build_person_has_source_url(person_relations_data, source_url_nodes):
    url_lookup = build_url_lookup(source_url_nodes)
    relation_rows = []

    for row in person_relations_data[["person_id", "detail_url"]].dropna().itertuples(index=False):
        append_url_relation_rows(
            relation_rows,
            row.person_id,
            row.detail_url,
            url_lookup,
            "person_relations.detail_url",
        )

    relation_data = pd.DataFrame(relation_rows).drop_duplicates()
    relation_data = relation_data.rename(columns={"start_id": "start_person_id"})

    return relation_data[
        ["start_person_id", "end_source_url_id", "relation_type", "source_column", "url"]
    ]


def save_csv(data_frame, output_path):
    output_path.parent.mkdir(parents=True, exist_ok=True)
    data_frame.to_csv(output_path, index=False, encoding="utf-8-sig")


def print_summary(file_name, data_frame):
    print(f"{file_name}: {len(data_frame)} rows, {len(data_frame.columns)} columns")


def build_default_paths(script_path):
    base_dir = script_path.parent
    normalized_dir = base_dir / "normalized"
    dictionary_dir = base_dir / "dictionary"
    staging_dir = base_dir / "staging"
    graph_dir = base_dir / "graph"

    return {
        "terms": normalized_dir / "terms.csv",
        "events": normalized_dir / "events.csv",
        "event_relations": normalized_dir / "event_relations.csv",
        "person_relations": normalized_dir / "person_relations.csv",
        "category_dictionary": dictionary_dir / "category_dictionary.csv",
        "event_category_dictionary": dictionary_dir / "event_category_dictionary.csv",
        "category_mapping": dictionary_dir / "category_mapping.csv",
        "period_dictionary": dictionary_dir / "period_dictionary.csv",
        "relation_type_dictionary": dictionary_dir / "relation_type_dictionary.csv",
        "source_url_dictionary": dictionary_dir / "source_url_dictionary.csv",
        "term_category_relation": staging_dir / "term_category_relation.csv",
        "event_category_relation": staging_dir / "event_category_relation.csv",
        "event_date_parse": staging_dir / "event_date_parse.csv",
        "nodes_dir": graph_dir / "nodes",
        "relations_dir": graph_dir / "relations",
    }


def read_inputs(args):
    return {
        "terms": read_csv(args.terms_path, "terms"),
        "events": read_csv(args.events_path, "events"),
        "event_relations": read_csv(args.event_relations_path, "event_relations"),
        "person_relations": read_csv(args.person_relations_path, "person_relations"),
        "category_dictionary": read_csv(args.category_dictionary_path, "category_dictionary"),
        "event_category_dictionary": read_csv(
            args.event_category_dictionary_path,
            "event_category_dictionary",
        ),
        "category_mapping": read_csv(args.category_mapping_path, "category_mapping"),
        "period_dictionary": read_csv(args.period_dictionary_path, "period_dictionary"),
        "relation_type_dictionary": read_csv(
            args.relation_type_dictionary_path,
            "relation_type_dictionary",
        ),
        "source_url_dictionary": read_csv(
            args.source_url_dictionary_path,
            "source_url_dictionary",
        ),
        "term_category_relation": read_csv(
            args.term_category_relation_path,
            "term_category_relation",
        ),
        "event_category_relation": read_csv(
            args.event_category_relation_path,
            "event_category_relation",
        ),
        "event_date_parse": read_csv(args.event_date_parse_path, "event_date_parse"),
    }


def build_node_outputs(inputs):
    event_group_nodes = build_event_group_nodes(inputs["events"])

    return {
        "terms": build_term_nodes(inputs["terms"]),
        "categories": build_category_nodes(inputs["category_dictionary"]),
        "event_categories": build_event_category_nodes(inputs["event_category_dictionary"]),
        "events": build_event_nodes(inputs["events"], inputs["event_date_parse"]),
        "event_groups": event_group_nodes,
        "people": build_person_nodes(inputs["event_relations"], inputs["person_relations"]),
        "periods": build_period_nodes(inputs["period_dictionary"]),
        "source_urls": build_source_url_nodes(inputs["source_url_dictionary"]),
    }


def build_relation_outputs(inputs, node_outputs):
    return {
        "term_has_category": build_term_has_category(inputs["term_category_relation"]),
        "category_subcategory_of": build_category_subcategory_of(
            inputs["category_dictionary"]
        ),
        "event_has_event_category": build_event_has_event_category(
            inputs["event_category_relation"]
        ),
        "event_category_mapped_to_category": build_event_category_mapped_to_category(
            inputs["category_mapping"]
        ),
        "event_has_category": build_event_has_category(
            inputs["event_category_relation"],
            inputs["category_mapping"],
        ),
        "term_in_period": build_term_in_period(inputs["terms"], inputs["period_dictionary"]),
        "event_in_period": build_event_in_period(
            inputs["events"],
            inputs["period_dictionary"],
        ),
        "event_part_of_event_group": build_event_part_of_group(
            inputs["events"],
            node_outputs["event_groups"],
        ),
        "person_involved_in_event": build_person_involved_in_event(
            inputs["event_relations"]
        ),
        "person_related_to_person": build_person_related_to_person(
            inputs["person_relations"],
            inputs["relation_type_dictionary"],
        ),
        "event_has_source_url": build_event_has_source_url(
            inputs["events"],
            inputs["event_relations"],
            node_outputs["source_urls"],
        ),
        "person_has_source_url": build_person_has_source_url(
            inputs["person_relations"],
            node_outputs["source_urls"],
        ),
    }


def build_output_files(args, node_outputs, relation_outputs):
    output_files = []

    for output_name, data_frame in node_outputs.items():
        output_files.append((f"{output_name}.csv", data_frame, args.nodes_dir / f"{output_name}.csv"))

    for output_name, data_frame in relation_outputs.items():
        output_files.append(
            (f"{output_name}.csv", data_frame, args.relations_dir / f"{output_name}.csv")
        )

    return output_files


def write_or_print_outputs(args, output_files):
    if args.save:
        for file_name, data_frame, output_path in output_files:
            save_csv(data_frame, output_path)
            print_summary(file_name, data_frame)

        print(f"nodes_dir: {args.nodes_dir}")
        print(f"relations_dir: {args.relations_dir}")

    if not args.save:
        for file_name, data_frame, output_path in output_files:
            print_summary(file_name, data_frame)
            print(f"planned_path: {output_path}")

        print("dry_run: no files saved. Use --save to write CSV files.")


def main():
    script_path = Path(__file__).resolve()
    default_paths = build_default_paths(script_path)
    args = parse_args(default_paths)
    inputs = read_inputs(args)
    node_outputs = build_node_outputs(inputs)
    relation_outputs = build_relation_outputs(inputs, node_outputs)
    output_files = build_output_files(args, node_outputs, relation_outputs)

    write_or_print_outputs(args, output_files)


if __name__ == "__main__":
    main()
