"""
정규화 CSV, 사전 CSV, staging CSV를 Neo4j 적재용 최종 노드/관계 CSV로 변환한다.

기본 실행은 dry-run이다. CSV 저장이 필요할 때만 --save를 사용한다.
"""

import argparse
import re
from pathlib import Path

import pandas as pd

from neo4j_common import (
    build_discontinued_relation_output_names,
    build_sequential_ids,
    clean_value,
    first_value,
    normalize_keyword_series,
    print_summary,
    read_csv,
    read_optional_csv,
    remove_stale_output_file,
    resolve_import_dir,
    resolve_neo4j_dir,
    resolve_project_root,
    save_csv,
    split_category_paths,
    split_period_tokens,
    split_pipe_values,
    unique_join,
)


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
        "--canonical-category-dictionary-path",
        default=default_paths["canonical_category_dictionary"],
        type=Path,
    )
    parser.add_argument(
        "--source-event-category-dictionary-path",
        default=default_paths["source_event_category_dictionary"],
        type=Path,
    )
    parser.add_argument(
        "--taxonomy-crosswalk-path",
        default=default_paths["taxonomy_crosswalk"],
        type=Path,
    )
    parser.add_argument(
        "--event-facet-dictionary-path",
        default=default_paths["event_facet_dictionary"],
        type=Path,
    )
    parser.add_argument(
        "--source-event-category-facet-crosswalk-path",
        default=default_paths["source_event_category_facet_crosswalk"],
        type=Path,
    )
    parser.add_argument(
        "--country-dictionary-path",
        default=default_paths["country_dictionary"],
        type=Path,
    )
    parser.add_argument(
        "--canonical-category-country-crosswalk-path",
        default=default_paths["canonical_category_country_crosswalk"],
        type=Path,
    )
    parser.add_argument(
        "--region-dictionary-path",
        default=default_paths["region_dictionary"],
        type=Path,
    )
    parser.add_argument(
        "--canonical-category-region-crosswalk-path",
        default=default_paths["canonical_category_region_crosswalk"],
        type=Path,
    )
    parser.add_argument(
        "--economic-domain-dictionary-path",
        default=default_paths["economic_domain_dictionary"],
        type=Path,
    )
    parser.add_argument(
        "--canonical-category-economic-domain-crosswalk-path",
        default=default_paths["canonical_category_economic_domain_crosswalk"],
        type=Path,
    )
    parser.add_argument(
        "--taxonomy-facet-dictionary-path",
        default=default_paths["taxonomy_facet_dictionary"],
        type=Path,
    )
    parser.add_argument(
        "--canonical-category-taxonomy-facet-crosswalk-path",
        default=default_paths["canonical_category_taxonomy_facet_crosswalk"],
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
        "--term-canonical-category-relation-path",
        default=default_paths["term_canonical_category_relation"],
        type=Path,
    )
    parser.add_argument(
        "--event-source-category-relation-path",
        default=default_paths["event_source_category_relation"],
        type=Path,
    )
    parser.add_argument(
        "--event-date-parse-path",
        default=default_paths["event_date_parse"],
        type=Path,
    )
    parser.add_argument(
        "--term-year-parse-path",
        default=default_paths["term_year_parse"],
        type=Path,
    )
    parser.add_argument(
        "--keyword-era-seed-path",
        default=default_paths["keyword_era_seed"],
        type=Path,
    )
    parser.add_argument(
        "--mention-rule-seed-path",
        default=default_paths["mention_rule_seed"],
        type=Path,
    )
    parser.add_argument(
        "--graph-config-seed-path",
        default=default_paths["graph_config_seed"],
        type=Path,
    )
    parser.add_argument(
        "--term-person-review-approved-path",
        default=default_paths["term_person_review_approved"],
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


def filter_actual_terms(terms_data):
    target_data = terms_data.copy()

    if "term_kind" in target_data.columns:
        target_data = target_data[target_data["term_kind"].eq("2")].copy()

    return target_data


def merge_term_year_parse_columns(term_nodes, term_year_parse):
    parse_columns = [
        "term_id",
        "start_year",
        "end_year",
        "date_precision",
        "parse_status",
    ]
    parse_data = term_year_parse[parse_columns].drop_duplicates(
        subset=["term_id"]
    ).copy()
    parse_data = parse_data.rename(
        columns={
            "date_precision": "year_precision",
            "parse_status": "year_parse_status",
        }
    )
    term_nodes = term_nodes.merge(parse_data, on="term_id", how="left")
    term_nodes["year_precision"] = term_nodes["year_precision"].fillna("UNKNOWN")
    term_nodes["year_parse_status"] = term_nodes["year_parse_status"].fillna("UNKNOWN")

    return term_nodes


def get_graph_config_number(graph_config_seed, config_key):
    matched_rows = graph_config_seed[graph_config_seed["config_key"].eq(config_key)]

    if len(matched_rows) == 0:
        raise ValueError(f"graph_config_seed에 config_key={config_key} 행이 없습니다.")

    return int(matched_rows.iloc[0]["config_value"])


def add_term_question_ready_columns(term_nodes, min_description_length):
    description_text = term_nodes["description"].fillna("").astype(str).str.strip()
    term_nodes["description_length"] = description_text.str.len()
    term_nodes["question_ready"] = "N"
    term_nodes.loc[
        term_nodes["description_length"].ge(min_description_length),
        "question_ready",
    ] = "Y"

    return term_nodes


def add_exam_keyword_column(term_nodes, keyword_era_seed):
    keyword_data = keyword_era_seed.copy()
    keyword_data["normalized_keyword"] = normalize_keyword_series(keyword_data["keyword"])
    exam_keywords = set(keyword_data["normalized_keyword"])

    term_nodes["normalized_name_for_keyword"] = normalize_keyword_series(term_nodes["name"])
    term_nodes["is_exam_keyword"] = "N"
    term_nodes.loc[
        term_nodes["normalized_name_for_keyword"].isin(exam_keywords),
        "is_exam_keyword",
    ] = "Y"
    term_nodes = term_nodes.drop(columns=["normalized_name_for_keyword"])

    return term_nodes


def build_term_nodes(terms_data, keyword_era_seed, term_year_parse, graph_config_seed):
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
    term_nodes["source_record_id"] = (
        term_nodes["source"].astype(str) + ":" + term_nodes["term_id"].astype(str)
    )
    term_nodes = merge_term_year_parse_columns(term_nodes, term_year_parse)
    term_nodes = add_term_question_ready_columns(
        term_nodes,
        get_graph_config_number(graph_config_seed, "question_ready_min_description_length"),
    )
    term_nodes = add_exam_keyword_column(term_nodes, keyword_era_seed)

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
            "start_year",
            "end_year",
            "year_precision",
            "year_parse_status",
            "description_length",
            "question_ready",
            "is_exam_keyword",
            "source",
            "source_record_id",
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
            "range_group",
            "period_order",
            "start_year",
            "end_year",
            "parent_period_name",
            "is_range_expansion_candidate",
            "term_count",
            "event_count",
            "source",
            "source_values",
            "review_status",
            "note",
        ]
    ]


def build_source_url_node_columns():
    return [
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


def build_source_url_nodes(source_url_dictionary):
    return source_url_dictionary[build_source_url_node_columns()].copy()


def build_event_facet_nodes(event_facet_dictionary):
    event_facet_nodes = event_facet_dictionary.copy()
    event_facet_nodes = event_facet_nodes.rename(columns={"facet_name": "name"})

    return event_facet_nodes[
        [
            "event_facet_id",
            "facet_type",
            "name",
            "source_event_category_count",
            "event_count",
            "confidence",
            "review_status",
            "note",
            "source",
        ]
    ]


def build_country_nodes(country_dictionary):
    country_nodes = country_dictionary.copy()
    country_nodes = country_nodes.rename(columns={"country_name": "name"})

    return country_nodes[
        [
            "country_id",
            "name",
            "country_type",
            "canonical_category_id",
            "canonical_category_path",
            "aliases",
            "review_status",
            "note",
            "source",
        ]
    ]


def build_region_nodes(region_dictionary):
    region_nodes = region_dictionary.copy()
    region_nodes = region_nodes.rename(columns={"region_name": "name"})

    return region_nodes[
        [
            "region_id",
            "name",
            "region_type",
            "canonical_category_id",
            "canonical_category_path",
            "parent_region_id",
            "parent_region_name",
            "aliases",
            "review_status",
            "note",
            "source",
        ]
    ]


def build_economic_domain_nodes(economic_domain_dictionary):
    economic_domain_nodes = economic_domain_dictionary.copy()
    economic_domain_nodes = economic_domain_nodes.rename(
        columns={"economic_domain_name": "name"}
    )

    return economic_domain_nodes[
        [
            "economic_domain_id",
            "name",
            "domain_type",
            "canonical_category_id",
            "canonical_category_path",
            "review_status",
            "note",
            "source",
        ]
    ]


def build_taxonomy_facet_nodes(taxonomy_facet_dictionary):
    taxonomy_facet_nodes = taxonomy_facet_dictionary.copy()
    taxonomy_facet_nodes = taxonomy_facet_nodes.rename(
        columns={"taxonomy_facet_name": "name"}
    )

    return taxonomy_facet_nodes[
        [
            "taxonomy_facet_id",
            "name",
            "taxonomy_facet_path",
            "taxonomy_facet_depth",
            "root_category_name",
            "canonical_category_id",
            "child_category_count",
            "descendant_category_count",
            "term_count",
            "direct_term_count",
            "facet_type",
            "review_status",
            "note",
            "source",
        ]
    ]


def build_canonical_category_tag_rows(category_dictionary):
    tag_rows = []

    for row in category_dictionary.itertuples(index=False):
        tag_rows.append(
            {
                "tag_type": "CANONICAL_CATEGORY",
                "tag_name": row.category_name,
                "tag_value": row.category_path,
                "source_node_type": "CanonicalCategory",
                "source_node_id": row.category_id,
                "source": "canonical_category_dictionary",
                "review_status": row.review_status,
            }
        )

    return tag_rows


def build_source_event_category_tag_rows(event_category_dictionary):
    tag_rows = []

    for row in event_category_dictionary.itertuples(index=False):
        tag_rows.append(
            {
                "tag_type": "SOURCE_EVENT_CATEGORY",
                "tag_name": row.event_category_name,
                "tag_value": row.event_category_name,
                "source_node_type": "SourceEventCategory",
                "source_node_id": row.event_category_id,
                "source": "source_event_category_dictionary",
                "review_status": row.review_status,
            }
        )

    return tag_rows


def build_event_facet_tag_rows(event_facet_dictionary):
    tag_rows = []

    for row in event_facet_dictionary.itertuples(index=False):
        tag_rows.append(
            {
                "tag_type": row.facet_type,
                "tag_name": row.facet_name,
                "tag_value": row.facet_name,
                "source_node_type": "EventFacet",
                "source_node_id": row.event_facet_id,
                "source": "event_facet_dictionary",
                "review_status": row.review_status,
            }
        )

    return tag_rows


def build_country_tag_rows(country_dictionary):
    tag_rows = []

    for row in country_dictionary.itertuples(index=False):
        tag_rows.append(
            {
                "tag_type": "COUNTRY",
                "tag_name": row.country_name,
                "tag_value": row.country_name,
                "source_node_type": "Country",
                "source_node_id": row.country_id,
                "source": "country_dictionary",
                "review_status": row.review_status,
            }
        )

    return tag_rows


def build_region_tag_rows(region_dictionary):
    tag_rows = []

    for row in region_dictionary.itertuples(index=False):
        tag_rows.append(
            {
                "tag_type": "REGION",
                "tag_name": row.region_name,
                "tag_value": row.region_name,
                "source_node_type": "Region",
                "source_node_id": row.region_id,
                "source": "region_dictionary",
                "review_status": row.review_status,
            }
        )

    return tag_rows


def build_economic_domain_tag_rows(economic_domain_dictionary):
    tag_rows = []

    for row in economic_domain_dictionary.itertuples(index=False):
        tag_rows.append(
            {
                "tag_type": "ECONOMIC_DOMAIN",
                "tag_name": row.economic_domain_name,
                "tag_value": row.economic_domain_name,
                "source_node_type": "EconomicDomain",
                "source_node_id": row.economic_domain_id,
                "source": "economic_domain_dictionary",
                "review_status": row.review_status,
            }
        )

    return tag_rows


def build_taxonomy_facet_tag_rows(taxonomy_facet_dictionary):
    tag_rows = []

    for row in taxonomy_facet_dictionary.itertuples(index=False):
        tag_rows.append(
            {
                "tag_type": "TAXONOMY_FACET",
                "tag_name": row.taxonomy_facet_name,
                "tag_value": row.taxonomy_facet_path,
                "source_node_type": "TaxonomyFacet",
                "source_node_id": row.taxonomy_facet_id,
                "source": "taxonomy_facet_dictionary",
                "review_status": row.review_status,
            }
        )

    return tag_rows


def build_named_node_tag_rows(
    node_data,
    id_column,
    name_column,
    tag_type,
    source_node_type,
    source,
):
    tag_rows = []
    if node_data.empty:
        return tag_rows

    for row in node_data[[id_column, name_column]].drop_duplicates().itertuples(index=False):
        tag_name = clean_value(getattr(row, name_column))
        source_node_id = clean_value(getattr(row, id_column))

        if tag_name and source_node_id:
            tag_rows.append(
                {
                    "tag_type": tag_type,
                    "tag_name": tag_name,
                    "tag_value": tag_name,
                    "source_node_type": source_node_type,
                    "source_node_id": source_node_id,
                    "source": source,
                    "review_status": "APPROVED",
                }
            )

    return tag_rows


def build_person_alias_search_tag_data(people):
    alias_data = split_person_name_columns(people)
    columns = [
        "person_id",
        "alias_name",
        "alias_value",
        "alias_source_id",
    ]

    if alias_data.empty:
        return pd.DataFrame(columns=columns)

    alias_data = alias_data.rename(columns={"base_name": "alias_name"}).copy()
    alias_data["hanja"] = alias_data["hanja"].fillna("").str.strip()
    alias_data["alias_value"] = alias_data["alias_name"]
    hanja_mask = alias_data["hanja"].ne("")
    alias_data.loc[hanja_mask, "alias_value"] = (
        alias_data.loc[hanja_mask, "alias_name"]
        + "("
        + alias_data.loc[hanja_mask, "hanja"]
        + ")"
    )
    alias_data = (
        alias_data[["person_id", "alias_name", "alias_value"]]
        .drop_duplicates()
        .sort_values(["person_id", "alias_name", "alias_value"])
        .reset_index(drop=True)
    )
    alias_numbers = alias_data.groupby("person_id").cumcount() + 1
    alias_data["alias_source_id"] = (
        alias_data["person_id"].astype(str)
        + "::alias::"
        + alias_numbers.astype(str)
    )

    return alias_data[columns]


def build_person_alias_tag_rows(people):
    tag_rows = []
    alias_data = build_person_alias_search_tag_data(people)

    for row in alias_data.itertuples(index=False):
        tag_rows.append(
            {
                "tag_type": "PERSON_ALIAS",
                "tag_name": row.alias_name,
                "tag_value": row.alias_value,
                "source_node_type": "PersonAlias",
                "source_node_id": row.alias_source_id,
                "source": "people.name_candidates",
                "review_status": "APPROVED",
            }
        )

    return tag_rows


def build_search_tag_nodes(
    terms,
    events,
    people,
    period_dictionary,
    category_dictionary,
    event_category_dictionary,
    event_facet_dictionary,
    country_dictionary,
    region_dictionary,
    economic_domain_dictionary,
    taxonomy_facet_dictionary,
):
    tag_rows = []
    tag_rows.extend(
        build_named_node_tag_rows(
            terms,
            "term_id",
            "name",
            "TERM_NAME",
            "Term",
            "terms",
        )
    )
    tag_rows.extend(
        build_named_node_tag_rows(
            events,
            "event_id",
            "event_name",
            "EVENT_NAME",
            "Event",
            "events",
        )
    )
    tag_rows.extend(
        build_named_node_tag_rows(
            people,
            "person_id",
            "name",
            "PERSON_NAME",
            "Person",
            "people",
        )
    )
    tag_rows.extend(build_person_alias_tag_rows(people))
    tag_rows.extend(
        build_named_node_tag_rows(
            period_dictionary,
            "period_id",
            "period_name",
            "PERIOD",
            "Period",
            "period_dictionary",
        )
    )
    tag_rows.extend(build_canonical_category_tag_rows(category_dictionary))
    tag_rows.extend(build_source_event_category_tag_rows(event_category_dictionary))
    tag_rows.extend(build_event_facet_tag_rows(event_facet_dictionary))
    tag_rows.extend(build_country_tag_rows(country_dictionary))
    tag_rows.extend(build_region_tag_rows(region_dictionary))
    tag_rows.extend(build_economic_domain_tag_rows(economic_domain_dictionary))
    tag_rows.extend(build_taxonomy_facet_tag_rows(taxonomy_facet_dictionary))

    if len(tag_rows) == 0:
        return pd.DataFrame(
            columns=[
                "search_tag_id",
                "tag_type",
                "tag_name",
                "tag_value",
                "source_node_type",
                "source_node_id",
                "source",
                "review_status",
            ]
        )

    search_tag_nodes = (
        pd.DataFrame(tag_rows)
        .drop_duplicates(subset=["source_node_type", "source_node_id"])
        .sort_values(["tag_type", "tag_name", "tag_value"])
        .reset_index(drop=True)
    )
    search_tag_nodes.insert(
        0,
        "search_tag_id",
        build_sequential_ids("SEARCH_TAG", len(search_tag_nodes), 6),
    )

    return search_tag_nodes[
        [
            "search_tag_id",
            "tag_type",
            "tag_name",
            "tag_value",
            "source_node_type",
            "source_node_id",
            "source",
            "review_status",
        ]
    ]


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
    degree_lookup = build_person_degree_lookup(event_relations_data, person_relations_data)

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
    person_nodes["degree"] = (
        person_nodes["person_id"].map(degree_lookup).fillna(0).astype(int)
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
            "degree",
            "source",
        ]
    ]


def build_person_degree_lookup(event_relations_data, person_relations_data):
    # 그래프에서 인물의 연결 정도를 미리 계산해 출제 우선순위나 중심 인물 후보에 쓴다.
    degree_parts = []

    if "person_id" in event_relations_data.columns:
        degree_parts.append(event_relations_data["person_id"])

    if "person_id" in person_relations_data.columns:
        degree_parts.append(person_relations_data["person_id"])

    if "related_person_id" in person_relations_data.columns:
        degree_parts.append(person_relations_data["related_person_id"])

    if len(degree_parts) == 0:
        return {}

    degree_data = pd.concat(degree_parts, ignore_index=True).dropna()
    degree_data = degree_data.astype(str).str.strip()
    degree_data = degree_data[degree_data.ne("")]

    return degree_data.value_counts().to_dict()


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


def collect_geo_facet_category_paths(country_crosswalk, region_crosswalk):
    excluded_paths = set()

    for crosswalk_data in [country_crosswalk, region_crosswalk]:
        if "canonical_category_path" in crosswalk_data.columns:
            category_paths = (
                crosswalk_data["canonical_category_path"].dropna().astype(str)
            )

            for category_path in category_paths:
                excluded_paths.add(category_path)

    return excluded_paths


def build_category_subcategory_of(
    category_dictionary,
    excluded_category_paths,
):
    relation_data = category_dictionary.dropna(subset=["parent_category_id"]).copy()

    if len(excluded_category_paths) > 0:
        category_path_text = relation_data["category_path"].astype(str)
        parent_path_text = relation_data["parent_category_path"].astype(str)
        relation_data = relation_data[
            ~category_path_text.isin(excluded_category_paths)
            & ~parent_path_text.isin(excluded_category_paths)
        ].copy()

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


def build_event_has_source_category(event_category_relation):
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


def filter_mapped_canonical_categories(taxonomy_crosswalk):
    return taxonomy_crosswalk.dropna(subset=["mapped_category_id"]).copy()


def build_source_category_mapped_to_canonical_category(taxonomy_crosswalk):
    mapped_data = filter_mapped_canonical_categories(taxonomy_crosswalk)
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


def build_event_has_canonical_category(event_category_relation, taxonomy_crosswalk):
    # EventCategory가 표준 Category로 매핑된 경우에만 Event -> Category 직접 관계를 만든다.
    # 매핑되지 않은 이벤트 분류는 event_has_source_category 관계로 원형을 보존한다.
    mapped_data = filter_mapped_canonical_categories(taxonomy_crosswalk)
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


def build_event_has_facet(event_category_relation, source_event_category_facet_crosswalk):
    relation_data = event_category_relation.merge(
        source_event_category_facet_crosswalk,
        left_on=["event_category_id", "event_category_name"],
        right_on=["source_event_category_id", "source_event_category_name"],
        how="inner",
    )
    relation_data = relation_data.rename(
        columns={
            "event_id": "start_event_id",
            "event_facet_id": "end_event_facet_id",
        }
    )
    relation_data["relation_type"] = "HAS_EVENT_FACET"

    return relation_data[
        [
            "start_event_id",
            "end_event_facet_id",
            "relation_type",
            "source_event_category_id",
            "source_event_category_name",
            "facet_type",
            "facet_name",
            "confidence",
            "review_status",
            "note",
        ]
    ].drop_duplicates()


def build_canonical_category_about_country(canonical_category_country_crosswalk):
    relation_data = canonical_category_country_crosswalk.copy()
    relation_data = relation_data.rename(
        columns={
            "canonical_category_id": "start_category_id",
            "country_id": "end_country_id",
        }
    )
    relation_data["relation_type"] = "ABOUT_COUNTRY"

    return relation_data[
        [
            "start_category_id",
            "end_country_id",
            "relation_type",
            "canonical_category_path",
            "country_name",
            "match_type",
            "review_status",
            "note",
        ]
    ].drop_duplicates()


def build_term_about_country(term_has_canonical_category, canonical_category_country_crosswalk):
    relation_data = term_has_canonical_category.merge(
        canonical_category_country_crosswalk,
        left_on="end_category_id",
        right_on="canonical_category_id",
        how="inner",
    )
    relation_data = relation_data.rename(
        columns={
            "start_term_id": "start_term_id",
            "country_id": "end_country_id",
        }
    )
    relation_data["relation_type"] = "ABOUT_COUNTRY"

    return relation_data[
        [
            "start_term_id",
            "end_country_id",
            "relation_type",
            "country_name",
            "canonical_category_id",
            "canonical_category_path",
            "match_type",
        ]
    ].drop_duplicates()


def build_event_about_country(event_has_canonical_category, canonical_category_country_crosswalk):
    relation_data = event_has_canonical_category.merge(
        canonical_category_country_crosswalk,
        left_on="end_category_id",
        right_on="canonical_category_id",
        how="inner",
    )
    relation_data = relation_data.rename(
        columns={
            "start_event_id": "start_event_id",
            "country_id": "end_country_id",
        }
    )
    relation_data["relation_type"] = "ABOUT_COUNTRY"

    return relation_data[
        [
            "start_event_id",
            "end_country_id",
            "relation_type",
            "country_name",
            "canonical_category_id",
            "canonical_category_path",
            "match_type",
        ]
    ].drop_duplicates()


def build_region_subregion_of(region_dictionary):
    relation_data = region_dictionary.dropna(subset=["parent_region_id"]).copy()
    relation_data = relation_data.rename(
        columns={
            "region_id": "start_region_id",
            "parent_region_id": "end_region_id",
        }
    )
    relation_data["relation_type"] = "SUBREGION_OF"

    return relation_data[
        [
            "start_region_id",
            "end_region_id",
            "relation_type",
            "region_name",
            "parent_region_name",
            "canonical_category_path",
        ]
    ].drop_duplicates()


def build_canonical_category_about_region(canonical_category_region_crosswalk):
    relation_data = canonical_category_region_crosswalk.copy()
    relation_data = relation_data.rename(
        columns={
            "canonical_category_id": "start_category_id",
            "region_id": "end_region_id",
        }
    )
    relation_data["relation_type"] = "ABOUT_REGION"

    return relation_data[
        [
            "start_category_id",
            "end_region_id",
            "relation_type",
            "canonical_category_path",
            "region_name",
            "region_type",
            "region_path",
            "match_type",
            "review_status",
            "note",
        ]
    ].drop_duplicates()


def build_term_about_region(term_has_canonical_category, canonical_category_region_crosswalk):
    relation_data = term_has_canonical_category.merge(
        canonical_category_region_crosswalk,
        left_on="end_category_id",
        right_on="canonical_category_id",
        how="inner",
    )
    relation_data = relation_data.rename(
        columns={
            "region_id": "end_region_id",
        }
    )
    relation_data["relation_type"] = "ABOUT_REGION"

    return relation_data[
        [
            "start_term_id",
            "end_region_id",
            "relation_type",
            "region_name",
            "region_type",
            "canonical_category_id",
            "canonical_category_path",
            "match_type",
        ]
    ].drop_duplicates()


def build_event_about_region(event_has_canonical_category, canonical_category_region_crosswalk):
    relation_data = event_has_canonical_category.merge(
        canonical_category_region_crosswalk,
        left_on="end_category_id",
        right_on="canonical_category_id",
        how="inner",
    )
    relation_data = relation_data.rename(
        columns={
            "region_id": "end_region_id",
        }
    )
    relation_data["relation_type"] = "ABOUT_REGION"

    return relation_data[
        [
            "start_event_id",
            "end_region_id",
            "relation_type",
            "region_name",
            "region_type",
            "canonical_category_id",
            "canonical_category_path",
            "match_type",
        ]
    ].drop_duplicates()


def build_canonical_category_about_economic_domain(
    canonical_category_economic_domain_crosswalk,
):
    relation_data = canonical_category_economic_domain_crosswalk.copy()
    relation_data = relation_data.rename(
        columns={
            "canonical_category_id": "start_category_id",
            "economic_domain_id": "end_economic_domain_id",
        }
    )
    relation_data["relation_type"] = "ABOUT_ECONOMIC_DOMAIN"

    return relation_data[
        [
            "start_category_id",
            "end_economic_domain_id",
            "relation_type",
            "canonical_category_path",
            "economic_domain_name",
            "match_type",
            "review_status",
            "note",
        ]
    ].drop_duplicates()


def build_term_about_economic_domain(
    term_has_canonical_category,
    canonical_category_economic_domain_crosswalk,
):
    relation_data = term_has_canonical_category.merge(
        canonical_category_economic_domain_crosswalk,
        left_on="end_category_id",
        right_on="canonical_category_id",
        how="inner",
    )
    relation_data = relation_data.rename(
        columns={
            "economic_domain_id": "end_economic_domain_id",
        }
    )
    relation_data["relation_type"] = "ABOUT_ECONOMIC_DOMAIN"

    return relation_data[
        [
            "start_term_id",
            "end_economic_domain_id",
            "relation_type",
            "economic_domain_name",
            "canonical_category_id",
            "canonical_category_path",
            "match_type",
        ]
    ].drop_duplicates()


def build_event_about_economic_domain(
    event_has_canonical_category,
    canonical_category_economic_domain_crosswalk,
):
    relation_data = event_has_canonical_category.merge(
        canonical_category_economic_domain_crosswalk,
        left_on="end_category_id",
        right_on="canonical_category_id",
        how="inner",
    )
    relation_data = relation_data.rename(
        columns={
            "economic_domain_id": "end_economic_domain_id",
        }
    )
    relation_data["relation_type"] = "ABOUT_ECONOMIC_DOMAIN"

    return relation_data[
        [
            "start_event_id",
            "end_economic_domain_id",
            "relation_type",
            "economic_domain_name",
            "canonical_category_id",
            "canonical_category_path",
            "match_type",
        ]
    ].drop_duplicates()


def build_canonical_category_about_taxonomy_facet(
    canonical_category_taxonomy_facet_crosswalk,
):
    relation_data = canonical_category_taxonomy_facet_crosswalk.copy()
    relation_data = relation_data.rename(
        columns={
            "canonical_category_id": "start_category_id",
            "taxonomy_facet_id": "end_taxonomy_facet_id",
        }
    )
    relation_data["relation_type"] = "ABOUT_TAXONOMY_FACET"

    return relation_data[
        [
            "start_category_id",
            "end_taxonomy_facet_id",
            "relation_type",
            "canonical_category_path",
            "taxonomy_facet_name",
            "taxonomy_facet_path",
            "taxonomy_facet_depth",
            "root_category_name",
            "match_type",
            "review_status",
            "note",
        ]
    ].drop_duplicates()


def build_term_about_taxonomy_facet(
    term_has_canonical_category,
    canonical_category_taxonomy_facet_crosswalk,
):
    relation_data = term_has_canonical_category.merge(
        canonical_category_taxonomy_facet_crosswalk,
        left_on="end_category_id",
        right_on="canonical_category_id",
        how="inner",
    )
    relation_data = relation_data.rename(
        columns={
            "taxonomy_facet_id": "end_taxonomy_facet_id",
        }
    )
    relation_data["relation_type"] = "ABOUT_TAXONOMY_FACET"

    return relation_data[
        [
            "start_term_id",
            "end_taxonomy_facet_id",
            "relation_type",
            "taxonomy_facet_name",
            "taxonomy_facet_path",
            "canonical_category_id",
            "canonical_category_path",
            "match_type",
        ]
    ].drop_duplicates()


def build_event_about_taxonomy_facet(
    event_has_canonical_category,
    canonical_category_taxonomy_facet_crosswalk,
):
    relation_data = event_has_canonical_category.merge(
        canonical_category_taxonomy_facet_crosswalk,
        left_on="end_category_id",
        right_on="canonical_category_id",
        how="inner",
    )
    relation_data = relation_data.rename(
        columns={
            "taxonomy_facet_id": "end_taxonomy_facet_id",
        }
    )
    relation_data["relation_type"] = "ABOUT_TAXONOMY_FACET"

    return relation_data[
        [
            "start_event_id",
            "end_taxonomy_facet_id",
            "relation_type",
            "taxonomy_facet_name",
            "taxonomy_facet_path",
            "canonical_category_id",
            "canonical_category_path",
            "match_type",
        ]
    ].drop_duplicates()


def build_search_tag_lookup(search_tag_nodes):
    return {
        (row.source_node_type, row.source_node_id): row.search_tag_id
        for row in search_tag_nodes.itertuples(index=False)
    }


def append_search_tag_relation_rows(
    relation_rows,
    start_id_column,
    start_node_id,
    source_node_type,
    source_node_id,
    source_relation,
    search_tag_lookup,
    source_detail="",
):
    search_tag_id = search_tag_lookup.get((source_node_type, source_node_id))

    if pd.notna(search_tag_id):
        relation_rows.append(
            {
                start_id_column: start_node_id,
                "end_search_tag_id": search_tag_id,
                "relation_type": "HAS_SEARCH_TAG",
                "source_node_type": source_node_type,
                "source_node_id": source_node_id,
                "source_relation": source_relation,
                "source_detail": source_detail,
            }
        )


def append_source_category_search_tag_rows(
    relation_rows,
    event_has_source_category,
    search_tag_lookup,
):
    for row in event_has_source_category.itertuples(index=False):
        append_search_tag_relation_rows(
            relation_rows,
            "start_event_id",
            row.start_event_id,
            "SourceEventCategory",
            row.end_event_category_id,
            "event_has_source_category",
            search_tag_lookup,
        )


def append_canonical_category_search_tag_rows(
    relation_rows,
    event_has_canonical_category,
    search_tag_lookup,
):
    for row in event_has_canonical_category.itertuples(index=False):
        append_search_tag_relation_rows(
            relation_rows,
            "start_event_id",
            row.start_event_id,
            "CanonicalCategory",
            row.end_category_id,
            "event_has_canonical_category",
            search_tag_lookup,
        )


def append_event_facet_search_tag_rows(
    relation_rows,
    event_has_facet,
    search_tag_lookup,
):
    for row in event_has_facet.itertuples(index=False):
        append_search_tag_relation_rows(
            relation_rows,
            "start_event_id",
            row.start_event_id,
            "EventFacet",
            row.end_event_facet_id,
            "event_has_facet",
            search_tag_lookup,
        )


def append_country_search_tag_rows(
    relation_rows,
    event_about_country,
    search_tag_lookup,
):
    for row in event_about_country.itertuples(index=False):
        append_search_tag_relation_rows(
            relation_rows,
            "start_event_id",
            row.start_event_id,
            "Country",
            row.end_country_id,
            "event_about_country",
            search_tag_lookup,
        )


def append_region_search_tag_rows(
    relation_rows,
    event_about_region,
    search_tag_lookup,
):
    for row in event_about_region.itertuples(index=False):
        append_search_tag_relation_rows(
            relation_rows,
            "start_event_id",
            row.start_event_id,
            "Region",
            row.end_region_id,
            "event_about_region",
            search_tag_lookup,
        )


def append_economic_domain_search_tag_rows(
    relation_rows,
    event_about_economic_domain,
    search_tag_lookup,
):
    for row in event_about_economic_domain.itertuples(index=False):
        append_search_tag_relation_rows(
            relation_rows,
            "start_event_id",
            row.start_event_id,
            "EconomicDomain",
            row.end_economic_domain_id,
            "event_about_economic_domain",
            search_tag_lookup,
        )


def append_taxonomy_facet_search_tag_rows(
    relation_rows,
    event_about_taxonomy_facet,
    search_tag_lookup,
):
    for row in event_about_taxonomy_facet.itertuples(index=False):
        append_search_tag_relation_rows(
            relation_rows,
            "start_event_id",
            row.start_event_id,
            "TaxonomyFacet",
            row.end_taxonomy_facet_id,
            "event_about_taxonomy_facet",
            search_tag_lookup,
        )


def build_search_tag_relation_columns(start_id_column):
    return [
        start_id_column,
        "end_search_tag_id",
        "relation_type",
        "source_node_type",
        "source_node_id",
        "source_relation",
        "source_detail",
    ]


def append_node_name_search_tag_rows(
    relation_rows,
    node_data,
    node_id_column,
    start_id_column,
    source_node_type,
    source_relation,
    search_tag_lookup,
):
    if node_data.empty:
        return

    for row in node_data[[node_id_column]].drop_duplicates().itertuples(index=False):
        node_id = getattr(row, node_id_column)
        append_search_tag_relation_rows(
            relation_rows,
            start_id_column,
            node_id,
            source_node_type,
            node_id,
            source_relation,
            search_tag_lookup,
        )


def append_person_alias_search_tag_rows(
    relation_rows,
    person_nodes,
    search_tag_lookup,
):
    alias_data = build_person_alias_search_tag_data(person_nodes)

    for row in alias_data.itertuples(index=False):
        append_search_tag_relation_rows(
            relation_rows,
            "start_person_id",
            row.person_id,
            "PersonAlias",
            row.alias_source_id,
            "person_alias",
            search_tag_lookup,
            row.alias_value,
        )


def append_axis_search_tag_rows(
    relation_rows,
    relation_data,
    start_id_column,
    source_node_type,
    source_id_column,
    source_relation,
    search_tag_lookup,
):
    required_columns = [start_id_column, source_id_column]
    if relation_data.empty:
        return

    if any(column_name not in relation_data.columns for column_name in required_columns):
        return

    for row in relation_data[required_columns].drop_duplicates().itertuples(index=False):
        append_search_tag_relation_rows(
            relation_rows,
            start_id_column,
            getattr(row, start_id_column),
            source_node_type,
            getattr(row, source_id_column),
            source_relation,
            search_tag_lookup,
        )


def empty_search_tag_relations(start_id_column):
    return pd.DataFrame(columns=build_search_tag_relation_columns(start_id_column))


def dataframe_from_search_tag_relation_rows(relation_rows, start_id_column):
    if len(relation_rows) == 0:
        return empty_search_tag_relations(start_id_column)

    return (
        pd.DataFrame(relation_rows)
        .drop_duplicates()
        .reset_index(drop=True)[build_search_tag_relation_columns(start_id_column)]
    )


def aggregate_search_tag_relation_details(relation_data, start_id_column):
    relation_columns = build_search_tag_relation_columns(start_id_column)

    if relation_data.empty:
        return empty_search_tag_relations(start_id_column)

    if "source_detail" not in relation_data.columns:
        relation_data = relation_data.copy()
        relation_data["source_detail"] = ""

    group_columns = [
        column_name
        for column_name in relation_columns
        if column_name != "source_detail"
    ]

    return (
        relation_data[relation_columns]
        .groupby(group_columns, dropna=False)
        .agg(source_detail=("source_detail", unique_join))
        .reset_index()[relation_columns]
    )


def build_term_has_search_tag(
    term_nodes,
    term_has_canonical_category,
    term_about_country,
    term_about_region,
    term_about_economic_domain,
    term_about_taxonomy_facet,
    term_in_period,
    search_tag_nodes,
):
    relation_rows = []
    search_tag_lookup = build_search_tag_lookup(search_tag_nodes)
    append_node_name_search_tag_rows(
        relation_rows,
        term_nodes,
        "term_id",
        "start_term_id",
        "Term",
        "term_name",
        search_tag_lookup,
    )
    append_axis_search_tag_rows(
        relation_rows,
        term_has_canonical_category,
        "start_term_id",
        "CanonicalCategory",
        "end_category_id",
        "term_has_canonical_category",
        search_tag_lookup,
    )
    append_axis_search_tag_rows(
        relation_rows,
        term_about_country,
        "start_term_id",
        "Country",
        "end_country_id",
        "term_about_country",
        search_tag_lookup,
    )
    append_axis_search_tag_rows(
        relation_rows,
        term_about_region,
        "start_term_id",
        "Region",
        "end_region_id",
        "term_about_region",
        search_tag_lookup,
    )
    append_axis_search_tag_rows(
        relation_rows,
        term_about_economic_domain,
        "start_term_id",
        "EconomicDomain",
        "end_economic_domain_id",
        "term_about_economic_domain",
        search_tag_lookup,
    )
    append_axis_search_tag_rows(
        relation_rows,
        term_about_taxonomy_facet,
        "start_term_id",
        "TaxonomyFacet",
        "end_taxonomy_facet_id",
        "term_about_taxonomy_facet",
        search_tag_lookup,
    )
    append_axis_search_tag_rows(
        relation_rows,
        term_in_period,
        "start_term_id",
        "Period",
        "end_period_id",
        "term_in_period",
        search_tag_lookup,
    )

    return dataframe_from_search_tag_relation_rows(relation_rows, "start_term_id")


def build_event_has_search_tag(
    event_nodes,
    event_has_source_category,
    event_has_canonical_category,
    event_has_facet,
    event_in_period,
    event_about_country,
    event_about_region,
    event_about_economic_domain,
    event_about_taxonomy_facet,
    search_tag_nodes,
):
    relation_rows = []
    search_tag_lookup = build_search_tag_lookup(search_tag_nodes)
    append_node_name_search_tag_rows(
        relation_rows,
        event_nodes,
        "event_id",
        "start_event_id",
        "Event",
        "event_name",
        search_tag_lookup,
    )
    append_source_category_search_tag_rows(
        relation_rows,
        event_has_source_category,
        search_tag_lookup,
    )
    append_canonical_category_search_tag_rows(
        relation_rows,
        event_has_canonical_category,
        search_tag_lookup,
    )
    append_event_facet_search_tag_rows(
        relation_rows,
        event_has_facet,
        search_tag_lookup,
    )
    append_axis_search_tag_rows(
        relation_rows,
        event_in_period,
        "start_event_id",
        "Period",
        "end_period_id",
        "event_in_period",
        search_tag_lookup,
    )
    append_country_search_tag_rows(
        relation_rows,
        event_about_country,
        search_tag_lookup,
    )
    append_region_search_tag_rows(
        relation_rows,
        event_about_region,
        search_tag_lookup,
    )
    append_economic_domain_search_tag_rows(
        relation_rows,
        event_about_economic_domain,
        search_tag_lookup,
    )
    append_taxonomy_facet_search_tag_rows(
        relation_rows,
        event_about_taxonomy_facet,
        search_tag_lookup,
    )

    return dataframe_from_search_tag_relation_rows(relation_rows, "start_event_id")


def build_person_has_search_tag_from_event_tags(
    person_involved_in_event,
    event_has_search_tag,
):
    if person_involved_in_event.empty or event_has_search_tag.empty:
        return empty_search_tag_relations("start_person_id")

    relation_data = person_involved_in_event[
        ["start_person_id", "end_event_id"]
    ].merge(
        event_has_search_tag,
        left_on="end_event_id",
        right_on="start_event_id",
        how="inner",
    )
    relation_data["relation_type"] = "HAS_SEARCH_TAG"
    relation_data["source_relation"] = "person_involved_in_event"
    relation_data["source_detail"] = relation_data["end_event_id"]

    return aggregate_search_tag_relation_details(relation_data, "start_person_id")


def build_person_has_search_tag_from_term_tags(
    term_refers_to_person,
    term_has_search_tag,
):
    if term_refers_to_person.empty or term_has_search_tag.empty:
        return empty_search_tag_relations("start_person_id")

    relation_data = term_refers_to_person[
        ["start_term_id", "end_person_id"]
    ].merge(
        term_has_search_tag,
        on="start_term_id",
        how="inner",
    )
    relation_data = relation_data.rename(columns={"end_person_id": "start_person_id"})
    relation_data["relation_type"] = "HAS_SEARCH_TAG"
    relation_data["source_relation"] = "term_refers_to_person"
    relation_data["source_detail"] = relation_data["start_term_id"]

    return aggregate_search_tag_relation_details(relation_data, "start_person_id")


def build_person_has_search_tag(
    person_nodes,
    person_involved_in_event,
    term_refers_to_person,
    term_has_search_tag,
    event_has_search_tag,
    search_tag_nodes,
):
    relation_rows = []
    search_tag_lookup = build_search_tag_lookup(search_tag_nodes)
    append_node_name_search_tag_rows(
        relation_rows,
        person_nodes,
        "person_id",
        "start_person_id",
        "Person",
        "person_name",
        search_tag_lookup,
    )
    append_person_alias_search_tag_rows(
        relation_rows,
        person_nodes,
        search_tag_lookup,
    )
    name_relations = dataframe_from_search_tag_relation_rows(
        relation_rows,
        "start_person_id",
    )
    event_relations = build_person_has_search_tag_from_event_tags(
        person_involved_in_event,
        event_has_search_tag,
    )
    term_relations = build_person_has_search_tag_from_term_tags(
        term_refers_to_person,
        term_has_search_tag,
    )

    return (
        pd.concat([name_relations, event_relations, term_relations], ignore_index=True)
        .drop_duplicates()
        .reset_index(drop=True)[build_search_tag_relation_columns("start_person_id")]
    )


def build_period_lookup(period_dictionary):
    return dict(zip(period_dictionary["period_name"], period_dictionary["period_id"]))


def build_period_metadata(period_dictionary):
    metadata = {}
    target_data = period_dictionary.copy()
    target_data["period_order_number"] = pd.to_numeric(
        target_data["period_order"],
        errors="coerce",
    )

    for row in target_data.itertuples(index=False):
        metadata[row.period_name] = {
            "period_id": row.period_id,
            "range_group": clean_value(row.range_group),
            "period_order": row.period_order_number,
        }

    return metadata


def build_range_candidate_periods(period_dictionary):
    target_data = period_dictionary.copy()
    target_data["period_order_number"] = pd.to_numeric(
        target_data["period_order"],
        errors="coerce",
    )
    target_data["range_group_clean"] = target_data["range_group"].map(clean_value)
    target_data = target_data[
        target_data["is_range_expansion_candidate"].astype(str).eq("Y")
        & target_data["period_order_number"].notna()
        & target_data["range_group_clean"].notna()
    ].copy()

    return target_data.sort_values(["range_group_clean", "period_order_number"])


def split_period_expression_segments(period_text):
    if pd.isna(period_text):
        return []

    cleaned_text = str(period_text).replace("\r", "\n")
    segments = []

    for raw_segment in re.split(r",|\n+", cleaned_text):
        clean_segment = clean_value(raw_segment)

        if pd.notna(clean_segment):
            segments.append(clean_segment)

    return segments


def has_period_range_delimiter(period_text):
    if pd.isna(period_text):
        return False

    return re.search(r"-|~|∼", str(period_text)) is not None


def build_period_match(
    period_name,
    match_type,
    source_period_text,
    range_start_period_name,
    range_end_period_name,
    period_metadata,
):
    period_data = period_metadata.get(period_name)

    if period_data is None:
        return None

    period_id = period_data["period_id"]

    if pd.isna(period_id):
        return None

    return {
        "period_id": period_id,
        "period_name": period_name,
        "match_type": match_type,
        "source_period_text": source_period_text,
        "range_start_period_name": range_start_period_name,
        "range_end_period_name": range_end_period_name,
    }


def find_middle_period_names(
    start_period_name,
    end_period_name,
    period_metadata,
    range_candidate_periods,
):
    start_period = period_metadata.get(start_period_name)
    end_period = period_metadata.get(end_period_name)

    if start_period is None or end_period is None:
        return []

    start_group = start_period["range_group"]
    end_group = end_period["range_group"]
    start_order = start_period["period_order"]
    end_order = end_period["period_order"]

    if pd.isna(start_group) or pd.isna(end_group):
        return []

    if start_group != end_group:
        return []

    if pd.isna(start_order) or pd.isna(end_order):
        return []

    if start_order >= end_order:
        return []

    middle_data = range_candidate_periods[
        range_candidate_periods["range_group_clean"].eq(start_group)
        & range_candidate_periods["period_order_number"].gt(start_order)
        & range_candidate_periods["period_order_number"].lt(end_order)
    ].copy()

    return middle_data["period_name"].drop_duplicates().tolist()


def dedupe_period_matches(period_matches):
    if len(period_matches) == 0:
        return []

    priority_lookup = {
        "DIRECT": 1,
        "RANGE_START": 2,
        "RANGE_END": 2,
        "RANGE_MIDDLE": 3,
    }
    match_frame = pd.DataFrame(period_matches)
    match_frame["match_priority"] = (
        match_frame["match_type"].map(priority_lookup).fillna(9)
    )
    match_frame = (
        match_frame
        .sort_values(["period_id", "match_priority"])
        .drop_duplicates(subset=["period_id"])
        .drop(columns=["match_priority"])
        .reset_index(drop=True)
    )

    return match_frame.to_dict("records")


def expand_period_text(period_text, period_metadata, range_candidate_periods):
    period_matches = []

    for segment in split_period_expression_segments(period_text):
        period_names = split_period_tokens(segment)

        if has_period_range_delimiter(segment) and len(period_names) == 2:
            start_period_name = period_names[0]
            end_period_name = period_names[1]
            start_match = build_period_match(
                start_period_name,
                "RANGE_START",
                period_text,
                start_period_name,
                end_period_name,
                period_metadata,
            )
            end_match = build_period_match(
                end_period_name,
                "RANGE_END",
                period_text,
                start_period_name,
                end_period_name,
                period_metadata,
            )

            if start_match is not None:
                period_matches.append(start_match)

            for middle_period_name in find_middle_period_names(
                start_period_name,
                end_period_name,
                period_metadata,
                range_candidate_periods,
            ):
                middle_match = build_period_match(
                    middle_period_name,
                    "RANGE_MIDDLE",
                    period_text,
                    start_period_name,
                    end_period_name,
                    period_metadata,
                )

                if middle_match is not None:
                    period_matches.append(middle_match)

            if end_match is not None:
                period_matches.append(end_match)

        if not has_period_range_delimiter(segment) or len(period_names) != 2:
            for period_name in period_names:
                direct_match = build_period_match(
                    period_name,
                    "DIRECT",
                    period_text,
                    pd.NA,
                    pd.NA,
                    period_metadata,
                )

                if direct_match is not None:
                    period_matches.append(direct_match)

    return dedupe_period_matches(period_matches)


def empty_term_in_period():
    return pd.DataFrame(
        columns=[
            "start_term_id",
            "end_period_id",
            "relation_type",
            "period_name",
            "source_period_text",
            "match_type",
            "range_start_period_name",
            "range_end_period_name",
        ]
    )


def build_term_in_period(terms_data, period_dictionary):
    period_metadata = build_period_metadata(period_dictionary)
    range_candidate_periods = build_range_candidate_periods(period_dictionary)
    relation_rows = []
    target_data = filter_actual_terms(terms_data).dropna(subset=["term_times"]).copy()

    for row in target_data[["term_id", "term_times"]].itertuples(index=False):
        for period_match in expand_period_text(
            row.term_times,
            period_metadata,
            range_candidate_periods,
        ):
            relation_rows.append(
                {
                    "start_term_id": row.term_id,
                    "end_period_id": period_match["period_id"],
                    "relation_type": "IN_PERIOD",
                    "period_name": period_match["period_name"],
                    "source_period_text": period_match["source_period_text"],
                    "match_type": period_match["match_type"],
                    "range_start_period_name": period_match["range_start_period_name"],
                    "range_end_period_name": period_match["range_end_period_name"],
                }
            )

    if len(relation_rows) == 0:
        return empty_term_in_period()

    return pd.DataFrame(relation_rows).drop_duplicates().reset_index(drop=True)


def empty_event_in_period():
    return pd.DataFrame(
        columns=[
            "start_event_id",
            "end_period_id",
            "relation_type",
            "period_name",
            "source_period_text",
            "match_type",
            "range_start_period_name",
            "range_end_period_name",
        ]
    )


def build_event_in_period(events_data, period_dictionary):
    period_metadata = build_period_metadata(period_dictionary)
    range_candidate_periods = build_range_candidate_periods(period_dictionary)
    relation_rows = []
    target_data = events_data.dropna(subset=["period"]).copy()

    for row in target_data[["event_id", "period"]].itertuples(index=False):
        for period_match in expand_period_text(
            row.period,
            period_metadata,
            range_candidate_periods,
        ):
            relation_rows.append(
                {
                    "start_event_id": row.event_id,
                    "end_period_id": period_match["period_id"],
                    "relation_type": "IN_PERIOD",
                    "period_name": period_match["period_name"],
                    "source_period_text": period_match["source_period_text"],
                    "match_type": period_match["match_type"],
                    "range_start_period_name": period_match["range_start_period_name"],
                    "range_end_period_name": period_match["range_end_period_name"],
                }
            )

    if len(relation_rows) == 0:
        return empty_event_in_period()

    return pd.DataFrame(relation_rows).drop_duplicates().reset_index(drop=True)


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
    relation_data["relation_type"] = "INVOLVED_IN"

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


def split_person_alias_values(row):
    aliases = []

    for column_name in ["name", "name_candidates"]:
        if column_name not in row.index:
            continue

        for candidate in split_pipe_values(row[column_name]):
            normalized_candidate = (
                str(candidate)
                .replace("\r\n", "\n")
                .replace("\r", "\n")
            )

            for alias in normalized_candidate.split("\n"):
                clean_alias = clean_value(alias)

                if pd.isna(clean_alias):
                    continue

                if clean_alias in aliases:
                    continue

                aliases.append(clean_alias)

    return aliases


def split_person_name_columns(person_nodes):
    # Person name/name_candidates can hold aliases like "이도(李祹)\n세종(世宗)".
    person_rows = []
    source_columns = ["person_id", "name"]

    if "name_candidates" in person_nodes.columns:
        source_columns.append("name_candidates")

    for _, row in person_nodes[source_columns].iterrows():
        for alias_index, alias in enumerate(split_person_alias_values(row)):
            base_name = re.sub(r"\(.*?\)", "", alias).strip()

            if base_name == "":
                continue

            hanja_match = re.search(r"\(([^)]*)\)", alias)
            hanja = ""

            if hanja_match:
                hanja = hanja_match.group(1).strip()

            person_rows.append(
                {
                    "person_id": row["person_id"],
                    "base_name": base_name,
                    "hanja": hanja,
                    "is_primary_alias": alias_index == 0,
                }
            )

    return pd.DataFrame(
        person_rows,
        columns=["person_id", "base_name", "hanja", "is_primary_alias"],
    ).drop_duplicates()


def empty_person_description_context_tokens():
    return pd.DataFrame(columns=["person_id", "base_name", "hanja"])


def build_person_description_context_rows(person_relations_data):
    required_columns = [
        "person_id",
        "person_name",
        "related_person_id",
        "related_person_name",
    ]
    missing_columns = [
        column_name
        for column_name in required_columns
        if column_name not in person_relations_data.columns
    ]

    if missing_columns:
        return pd.DataFrame(columns=["person_id", "context_name"])

    person_to_related = person_relations_data[
        ["person_id", "related_person_name"]
    ].rename(columns={"related_person_name": "context_name"})
    related_to_person = person_relations_data[
        ["related_person_id", "person_name"]
    ].rename(
        columns={
            "related_person_id": "person_id",
            "person_name": "context_name",
        }
    )
    context_rows = pd.concat(
        [person_to_related, related_to_person],
        ignore_index=True,
    )
    context_rows["person_id"] = context_rows["person_id"].fillna("").str.strip()
    context_rows["context_name"] = context_rows["context_name"].fillna("").str.strip()
    context_rows = context_rows[
        context_rows["person_id"].ne("") & context_rows["context_name"].ne("")
    ].copy()

    return context_rows.drop_duplicates()


def build_person_description_context_tokens(person_relations_data):
    context_rows = build_person_description_context_rows(person_relations_data)

    if context_rows.empty:
        return empty_person_description_context_tokens()

    context_nodes = context_rows.rename(columns={"context_name": "name"})
    context_tokens = split_person_name_columns(context_nodes)

    if context_tokens.empty:
        return empty_person_description_context_tokens()

    context_tokens = context_tokens[
        context_tokens["base_name"].fillna("").str.len().ge(2)
    ].copy()

    if context_tokens.empty:
        return empty_person_description_context_tokens()

    return context_tokens[["person_id", "base_name", "hanja"]].drop_duplicates()


def build_person_description_context_lookup(person_context_tokens):
    context_lookup = {}

    if person_context_tokens.empty:
        return context_lookup

    for person_id, token_rows in person_context_tokens.groupby("person_id"):
        tokens = []

        for row in token_rows.itertuples(index=False):
            for token_value in [row.base_name, row.hanja]:
                token = str(token_value or "").strip()

                if len(token) < 2:
                    continue

                if token in tokens:
                    continue

                tokens.append(token)

        context_lookup[person_id] = tokens

    return context_lookup


def find_description_context_matches(description, person_id, context_lookup):
    description_text = str(description or "")
    matches = []

    for token in context_lookup.get(person_id, []):
        if token not in description_text:
            continue

        matches.append(token)

    return "|".join(matches)


def add_description_context_match_columns(data_frame, person_relations_data):
    enriched_data = data_frame.copy()
    context_tokens = build_person_description_context_tokens(person_relations_data)
    context_lookup = build_person_description_context_lookup(context_tokens)

    if "description" not in enriched_data.columns:
        enriched_data["description"] = ""

    if "person_id" not in enriched_data.columns:
        enriched_data["description_context_matches"] = ""
        return enriched_data

    enriched_data["description_context_matches"] = [
        find_description_context_matches(row.description, row.person_id, context_lookup)
        for row in enriched_data[["description", "person_id"]].itertuples(index=False)
    ]

    return enriched_data


def build_description_context_match_mask(data_frame):
    if "description_context_matches" not in data_frame.columns:
        return pd.Series(False, index=data_frame.index)

    return data_frame["description_context_matches"].fillna("").ne("")


def find_unique_names(left_names, right_names):
    left_counts = left_names.value_counts()
    right_counts = right_names.value_counts()
    unique_left = set(left_counts[left_counts == 1].index)
    unique_right = set(right_counts[right_counts == 1].index)

    return unique_left & unique_right


def merge_person_life_year_columns(person_data, person_nodes):
    enriched_data = person_data.copy()

    for column_name in ["birth_year", "death_year"]:
        if column_name in person_nodes.columns:
            enriched_data = enriched_data.merge(
                person_nodes[["person_id", column_name]],
                on="person_id",
                how="left",
            )

        if column_name not in enriched_data.columns:
            enriched_data[column_name] = pd.NA

    return enriched_data


def build_exact_term_person_year_mask(data_frame):
    required_columns = ["start_year", "end_year", "birth_year", "death_year"]
    missing_columns = [
        column_name
        for column_name in required_columns
        if column_name not in data_frame.columns
    ]

    if missing_columns:
        return pd.Series(False, index=data_frame.index)

    term_start_year = pd.to_numeric(data_frame["start_year"], errors="coerce")
    term_end_year = pd.to_numeric(data_frame["end_year"], errors="coerce")
    birth_year = pd.to_numeric(data_frame["birth_year"], errors="coerce")
    death_year = pd.to_numeric(data_frame["death_year"], errors="coerce")
    has_complete_years = (
        term_start_year.notna()
        & term_end_year.notna()
        & birth_year.notna()
        & death_year.notna()
    )

    return (
        has_complete_years
        & term_start_year.eq(birth_year)
        & term_end_year.eq(death_year)
    )


def build_unique_exact_term_person_year_group_mask(data_frame):
    exact_year_match = build_exact_term_person_year_mask(data_frame)

    if "term_id" not in data_frame.columns or "person_id" not in data_frame.columns:
        return exact_year_match

    exact_person_counts = (
        data_frame[exact_year_match].groupby("term_id")["person_id"].nunique()
    )
    mapped_counts = data_frame["term_id"].map(exact_person_counts).fillna(0)

    return mapped_counts.eq(1)


def build_unique_exact_term_person_year_mask(data_frame):
    exact_year_match = build_exact_term_person_year_mask(data_frame)

    return exact_year_match & build_unique_exact_term_person_year_group_mask(data_frame)


def build_term_person_relation_columns():
    return [
        "start_term_id",
        "end_person_id",
        "relation_type",
        "match_type",
        "matched_name",
        "matched_hanja",
    ]


def empty_term_person_relations():
    return pd.DataFrame(columns=build_term_person_relation_columns())


def build_manual_term_person_links(
    term_nodes,
    person_nodes,
    term_person_review_approved,
):
    if term_person_review_approved.empty:
        return empty_term_person_relations()

    if "review_status" not in term_person_review_approved.columns:
        return empty_term_person_relations()

    approved_data = term_person_review_approved[
        term_person_review_approved["review_status"].isin(["APPROVED", "AUTO_APPROVED"])
    ].copy()

    if approved_data.empty:
        return empty_term_person_relations()

    approved_data = approved_data.rename(
        columns={
            "term_id": "start_term_id",
            "person_id": "end_person_id",
        }
    )
    term_data = term_nodes[["term_id", "name", "hanja"]].rename(
        columns={
            "term_id": "start_term_id",
            "name": "matched_name",
            "hanja": "matched_hanja",
        }
    )
    person_data = person_nodes[["person_id"]].rename(
        columns={"person_id": "end_person_id"}
    )
    link_data = approved_data[["start_term_id", "end_person_id"]].merge(
        term_data,
        on="start_term_id",
        how="inner",
    )
    link_data = link_data.merge(person_data, on="end_person_id", how="inner")
    link_data["relation_type"] = "REFERS_TO"
    link_data["match_type"] = "MANUAL"

    return link_data[build_term_person_relation_columns()]


def build_term_mentions_person_columns():
    return [
        "start_term_id",
        "end_person_id",
        "relation_type",
        "match_type",
        "matched_name",
        "matched_hanja",
        "source_field",
        "match_context",
        "context_rule",
        "confidence",
    ]


def empty_term_mentions_person_relations():
    return pd.DataFrame(columns=build_term_mentions_person_columns())


def build_mention_rules(mention_rule_seed, graph_config_seed):
    # 인물 언급 판정 규칙(접미사 목록, 문맥 창 크기)은 seed에서 관리한다.
    rule_data = mention_rule_seed.dropna(subset=["rule_type", "value"])
    rule_specs = [
        ("general_suffixes", "GENERAL_SUFFIX"),
        ("strong_suffixes", "STRONG_SUFFIX"),
        ("temple_name_suffixes", "TEMPLE_NAME_SUFFIX"),
    ]
    mention_rules = {}

    for rule_key, rule_type in rule_specs:
        values = rule_data[rule_data["rule_type"].eq(rule_type)]["value"].tolist()

        if len(values) == 0:
            raise ValueError(f"mention_rule_seed에 rule_type={rule_type} 행이 없습니다.")

        mention_rules[rule_key] = values

    mention_rules["context_window_size"] = get_graph_config_number(
        graph_config_seed,
        "mention_context_window_size",
    )

    return mention_rules


def build_person_mention_token_variants(token, suffixes):
    values = []
    value = str(token or "").strip()

    while value:
        if value not in values:
            values.append(value)

        stripped = False
        for suffix in suffixes:
            if len(value) > len(suffix) + 1 and value.endswith(suffix):
                value = value[: -len(suffix)]
                stripped = True
                break

        if stripped:
            continue

        break

    return values


def should_require_person_mention_context(alias_name, temple_name_suffixes):
    alias_text = str(alias_name or "").strip()

    if len(alias_text) <= 2:
        return True

    if len(alias_text) <= 3 and alias_text.endswith(tuple(temple_name_suffixes)):
        return True

    return False


def extract_person_mention_context(text, start_index, end_index, window_size):
    text_value = str(text)
    context_start = max(0, start_index - window_size)
    context_end = min(len(text_value), end_index + window_size)

    return re.sub(r"\s+", " ", text_value[context_start:context_end]).strip()


def has_person_hanja_context(context, alias_name, matched_hanja):
    hanja_text = str(matched_hanja or "").strip()

    if len(hanja_text) < 2:
        return False

    compact_context = re.sub(r"\s+", "", str(context))
    compact_alias_hanja = f"{alias_name}({hanja_text})"

    if compact_alias_hanja in compact_context:
        return True

    if hanja_text in compact_context:
        return True

    return False


def has_reign_year_context(context, alias_name):
    escaped_alias = re.escape(str(alias_name))
    patterns = [
        rf"\d+\s*년\s*\(\s*{escaped_alias}\s*\d+",
        rf"\(\s*{escaped_alias}\s*\d+\s*\)",
    ]

    for pattern in patterns:
        if re.search(pattern, str(context)):
            return True

    return False


def has_strong_person_suffix_context(token, alias_name, strong_suffixes):
    token_text = str(token or "")
    alias_text = str(alias_name or "")

    for suffix in strong_suffixes:
        if token_text.startswith(f"{alias_text}{suffix}"):
            return True

    return False


def decide_person_mention_context_rule(
    text,
    token,
    matched_name,
    matched_hanja,
    match_start,
    match_end,
    requires_context,
    mention_rules,
):
    context = extract_person_mention_context(
        text,
        match_start,
        match_end,
        mention_rules["context_window_size"],
    )

    if has_person_hanja_context(context, matched_name, matched_hanja):
        return "HANJA_CONTEXT", context

    if has_reign_year_context(context, matched_name):
        return "REIGN_YEAR_CONTEXT", context

    if has_strong_person_suffix_context(token, matched_name, mention_rules["strong_suffixes"]):
        return "TITLE_SUFFIX_CONTEXT", context

    if not requires_context:
        return "TOKEN_MATCH", context

    return None, context


def build_person_mention_confidence(context_rule):
    if context_rule in [
        "HANJA_CONTEXT",
        "REIGN_YEAR_CONTEXT",
        "TITLE_SUFFIX_CONTEXT",
    ]:
        return "HIGH"

    return "MEDIUM"


def build_mention_person_alias_lookup(person_nodes, term_refers_to_person, mention_rules):
    if term_refers_to_person.empty:
        return {}

    reliable_link_data = term_refers_to_person[
        [
            "end_person_id",
            "matched_name",
            "matched_hanja",
        ]
    ].drop_duplicates()
    reliable_matched_alias_data = reliable_link_data.rename(
        columns={
            "end_person_id": "person_id",
            "matched_name": "base_name",
            "matched_hanja": "hanja",
        }
    )
    reliable_matched_alias_data["base_name"] = (
        reliable_matched_alias_data["base_name"].fillna("").str.strip()
    )
    reliable_matched_alias_data["hanja"] = (
        reliable_matched_alias_data["hanja"].fillna("").str.strip()
    )
    reliable_matched_alias_data = reliable_matched_alias_data[
        reliable_matched_alias_data["base_name"].str.len().ge(2)
    ].copy()

    if not reliable_matched_alias_data.empty:
        reliable_name_counts = reliable_matched_alias_data.groupby("base_name")[
            "person_id"
        ].nunique()
        reliable_unique_names = set(
            reliable_name_counts[reliable_name_counts == 1].index
        )
        reliable_matched_alias_data = reliable_matched_alias_data[
            reliable_matched_alias_data["base_name"].isin(reliable_unique_names)
        ].copy()

    person_alias_data = split_person_name_columns(person_nodes)
    reliable_alias_data = pd.DataFrame(columns=["person_id", "base_name", "hanja"])

    if not person_alias_data.empty:
        person_alias_data = person_alias_data[
            person_alias_data["base_name"].str.len().ge(2)
            & ~person_alias_data["is_primary_alias"]
        ].copy()

    if not person_alias_data.empty:
        alias_person_counts = person_alias_data.groupby("base_name")[
            "person_id"
        ].nunique()
        unique_aliases = set(alias_person_counts[alias_person_counts == 1].index)
        unique_alias_data = person_alias_data[
            person_alias_data["base_name"].isin(unique_aliases)
        ].copy()
        reliable_alias_data = unique_alias_data.merge(
            reliable_link_data,
            left_on=["person_id", "base_name"],
            right_on=["end_person_id", "matched_name"],
            how="inner",
        )

    if not reliable_alias_data.empty:
        alias_hanja = reliable_alias_data["hanja"].fillna("")
        matched_hanja = reliable_alias_data["matched_hanja"].fillna("")
        hanja_compatible = (
            alias_hanja.eq("")
            | matched_hanja.eq("")
            | alias_hanja.eq(matched_hanja)
        )
        reliable_alias_data = reliable_alias_data[hanja_compatible].copy()
        reliable_alias_data = reliable_alias_data[["person_id", "base_name", "hanja"]]

    alias_source_data = pd.concat(
        [
            reliable_alias_data,
            reliable_matched_alias_data[["person_id", "base_name", "hanja"]],
        ],
        ignore_index=True,
    ).drop_duplicates()

    if alias_source_data.empty:
        return {}

    alias_source_counts = alias_source_data.groupby("base_name")[
        "person_id"
    ].nunique()
    alias_unique_names = set(alias_source_counts[alias_source_counts == 1].index)
    alias_source_data = alias_source_data[
        alias_source_data["base_name"].isin(alias_unique_names)
    ].copy()

    if alias_source_data.empty:
        return {}

    alias_source_data = alias_source_data.sort_values(
        ["base_name", "person_id", "hanja"]
    )

    alias_lookup = {}
    for base_name, alias_rows in alias_source_data.groupby("base_name"):
        alias_lookup[base_name] = {
            "person_id": first_value(alias_rows["person_id"]),
            "hanja": first_value(alias_rows["hanja"]),
            "requires_context": should_require_person_mention_context(
                base_name,
                mention_rules["temple_name_suffixes"],
            ),
        }

    return alias_lookup


def collect_person_mentions(text, alias_lookup, mention_rules):
    if pd.isna(text):
        return []

    mentions = []
    seen_mentions = set()
    text_value = str(text)

    for token_match in re.finditer(r"[가-힣A-Za-z0-9]+", text_value):
        token = token_match.group(0)
        token_variants = build_person_mention_token_variants(
            token,
            mention_rules["general_suffixes"],
        )

        for token_variant in token_variants:
            if token_variant not in alias_lookup:
                continue

            if token_variant in seen_mentions:
                continue

            alias_data = alias_lookup[token_variant]
            context_rule, context = decide_person_mention_context_rule(
                text_value,
                token,
                token_variant,
                alias_data["hanja"],
                token_match.start(),
                token_match.end(),
                alias_data["requires_context"],
                mention_rules,
            )

            if context_rule is None:
                continue

            seen_mentions.add(token_variant)
            mentions.append(
                {
                    "matched_name": token_variant,
                    "person_id": alias_data["person_id"],
                    "matched_hanja": alias_data["hanja"],
                    "match_context": context,
                    "context_rule": context_rule,
                    "confidence": build_person_mention_confidence(context_rule),
                }
            )

    return mentions


def is_person_category_term(category_text):
    # 인물 term은 REFERS_TO로 정밀 연결하므로 MENTIONS_PERSON 추출에서 제외한다.
    # category_text는 "인명" 단일 값 또는 "인명>성격별>..." 하위경로(">>"로 복수 경로 가능)다.
    for path_parts in split_category_paths(category_text):
        if path_parts[0] == "인명":
            return True

    return False


def build_term_mentions_person(
    term_nodes,
    person_nodes,
    term_refers_to_person,
    mention_rule_seed,
    graph_config_seed,
):
    mention_rules = build_mention_rules(mention_rule_seed, graph_config_seed)
    alias_lookup = build_mention_person_alias_lookup(
        person_nodes,
        term_refers_to_person,
        mention_rules,
    )

    if not alias_lookup:
        return empty_term_mentions_person_relations()

    relation_rows = []
    source_fields = ["name", "remark", "description"]
    term_columns = ["term_id", "category_text", *source_fields]
    term_data = term_nodes[term_columns].copy()

    for row in term_data.itertuples(index=False):
        if is_person_category_term(row.category_text):
            continue

        for source_field in source_fields:
            text = getattr(row, source_field)
            mentions = collect_person_mentions(text, alias_lookup, mention_rules)

            for mention in mentions:
                relation_rows.append(
                    {
                        "start_term_id": row.term_id,
                        "end_person_id": mention["person_id"],
                        "relation_type": "MENTIONS_PERSON",
                        "match_type": "UNIQUE_ALIAS",
                        "matched_name": mention["matched_name"],
                        "matched_hanja": mention["matched_hanja"],
                        "source_field": source_field,
                        "match_context": mention["match_context"],
                        "context_rule": mention["context_rule"],
                        "confidence": mention["confidence"],
                    }
                )

    relation_data = pd.DataFrame(
        relation_rows,
        columns=build_term_mentions_person_columns(),
    ).drop_duplicates(
        subset=["start_term_id", "end_person_id", "source_field"]
    )

    return relation_data[build_term_mentions_person_columns()]


def build_term_refers_to_person(
    term_nodes,
    person_nodes,
    person_relations_data,
    term_person_review_approved,
):
    # Context links use relation clues from descriptions. Life-year links rescue cases
    # where name, hanja, and complete years identify exactly one person for the term.
    term_data = term_nodes[
        ["term_id", "name", "hanja", "description", "start_year", "end_year"]
    ].copy()
    term_data["name"] = term_data["name"].fillna("").str.strip()
    term_data["hanja"] = term_data["hanja"].fillna("").str.strip()
    term_data["description"] = term_data["description"].fillna("").astype(str)
    person_data = split_person_name_columns(person_nodes)
    person_data = merge_person_life_year_columns(person_data, person_nodes)
    person_data["base_name"] = person_data["base_name"].fillna("").str.strip()
    person_data["hanja"] = person_data["hanja"].fillna("").str.strip()

    person_name_data = person_data.drop_duplicates(subset=["person_id", "base_name"])
    auto_names = find_unique_names(term_data["name"], person_name_data["base_name"])
    auto_links = term_data[term_data["name"].isin(auto_names)].merge(
        person_name_data,
        left_on="name",
        right_on="base_name",
        suffixes=("", "_person"),
    )
    term_hanja = auto_links["hanja"].fillna("")
    person_hanja = auto_links["hanja_person"].fillna("")
    hanja_compatible = (
        term_hanja.eq("")
        | person_hanja.eq("")
        | term_hanja.eq(person_hanja)
    )
    auto_links = auto_links[hanja_compatible].copy()
    auto_links = add_description_context_match_columns(
        auto_links,
        person_relations_data,
    )
    auto_links = auto_links[build_description_context_match_mask(auto_links)].copy()
    auto_links = auto_links[
        build_unique_exact_term_person_year_mask(auto_links)
    ].copy()
    auto_links["match_type"] = "EXACT_NAME"

    remaining_terms = term_data[
        ~term_data["term_id"].isin(auto_links["term_id"]) & term_data["hanja"].ne("")
    ].copy()
    remaining_persons = person_data[person_data["hanja"].ne("")].copy()
    hanja_links = remaining_terms.merge(
        remaining_persons,
        left_on=["name", "hanja"],
        right_on=["base_name", "hanja"],
        suffixes=("", "_person"),
    )
    hanja_links = add_description_context_match_columns(
        hanja_links,
        person_relations_data,
    )
    hanja_links = hanja_links[build_description_context_match_mask(hanja_links)].copy()
    hanja_links = hanja_links[
        build_unique_exact_term_person_year_mask(hanja_links)
    ].copy()
    hanja_links["match_type"] = "NAME_HANJA"

    life_year_terms = term_data[term_data["name"].ne("") & term_data["hanja"].ne("")]
    life_year_persons = person_data[
        person_data["base_name"].ne("") & person_data["hanja"].ne("")
    ].drop_duplicates(subset=["person_id", "base_name", "hanja"])
    life_year_links = life_year_terms.merge(
        life_year_persons,
        left_on=["name", "hanja"],
        right_on=["base_name", "hanja"],
        suffixes=("", "_person"),
    )
    life_year_links = life_year_links[
        build_unique_exact_term_person_year_mask(life_year_links)
    ].copy()
    life_year_links["match_type"] = "EXACT_NAME_HANJA_LIFE_YEAR"

    auto_link_data = pd.concat(
        [auto_links, hanja_links, life_year_links],
        ignore_index=True,
    )
    auto_link_data["relation_type"] = "REFERS_TO"
    auto_link_data = auto_link_data.rename(
        columns={
            "term_id": "start_term_id",
            "person_id": "end_person_id",
            "name": "matched_name",
            "hanja": "matched_hanja",
        }
    )
    auto_link_data = auto_link_data[build_term_person_relation_columns()]

    manual_links = build_manual_term_person_links(
        term_nodes,
        person_nodes,
        term_person_review_approved,
    )
    link_data = pd.concat([auto_link_data, manual_links], ignore_index=True)
    link_data = link_data.drop_duplicates(
        subset=["start_term_id", "end_person_id"]
    )

    return link_data[build_term_person_relation_columns()]


def build_term_refers_to_event(term_nodes, event_nodes):
    # 사건명은 한자 병기가 없으므로 이름이 양쪽에서 유일한 경우만 연결한다.
    term_data = term_nodes[["term_id", "name"]].copy()
    term_data["name"] = term_data["name"].fillna("").str.strip()
    event_data = event_nodes[["event_id", "name"]].copy()
    event_data["name"] = event_data["name"].fillna("").str.strip()

    auto_names = find_unique_names(term_data["name"], event_data["name"])
    link_data = term_data[term_data["name"].isin(auto_names)].merge(
        event_data,
        on="name",
        suffixes=("", "_event"),
    )
    link_data["relation_type"] = "REFERS_TO"
    link_data["match_type"] = "EXACT_NAME"
    link_data = link_data.rename(
        columns={
            "term_id": "start_term_id",
            "event_id": "end_event_id",
            "name": "matched_name",
        }
    )

    return link_data[
        [
            "start_term_id",
            "end_event_id",
            "relation_type",
            "match_type",
            "matched_name",
        ]
    ]


def drop_symmetric_duplicate_pairs(relation_data):
    # 대칭 관계(is_symmetric=Y)는 원본에 A->B, B->A 양방향으로 들어 있어
    # 무방향 쌍 + 관계 유형 기준으로 한 방향만 남긴다.
    pair_start = relation_data[["person_id", "related_person_id"]].min(axis=1)
    pair_end = relation_data[["person_id", "related_person_id"]].max(axis=1)
    pair_key = pair_start + ">" + pair_end + ">" + relation_data["relation_type"]

    symmetric_mask = relation_data["is_symmetric"].eq("Y")
    duplicated_pair_mask = pair_key.duplicated()
    drop_mask = symmetric_mask & duplicated_pair_mask

    return relation_data[~drop_mask].reset_index(drop=True)


def build_person_related_to_person(person_relations_data, relation_type_dictionary):
    # 관계 타입은 Neo4j 관계명으로 쪼개기보다 RELATED_TO 하나로 두고,
    # raw/normalized relation type을 속성으로 보존한다.
    relation_data = person_relations_data.dropna(
        subset=["person_id", "related_person_id", "relation_type"]
    ).copy()
    relation_data = relation_data[
        relation_data["person_id"].ne(relation_data["related_person_id"])
    ].copy()
    relation_data = relation_data.merge(
        relation_type_dictionary,
        left_on="relation_type",
        right_on="raw_relation_type",
        how="left",
    )
    relation_data = relation_data.drop(columns=["raw_relation_type"])
    relation_data = relation_data.drop_duplicates(
        subset=["person_id", "related_person_id", "relation_type"]
    ).reset_index(drop=True)
    relation_data = drop_symmetric_duplicate_pairs(relation_data)
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
            "related_count",
            "evidence_url",
        ]
    ]


def build_url_lookup(source_url_nodes):
    return dict(zip(source_url_nodes["url"], source_url_nodes["source_url_id"]))


def append_url_relation_rows(
    relation_rows,
    start_id,
    url_value,
    url_lookup,
    source_column,
    relation_type="HAS_SOURCE_URL",
):
    for url in split_pipe_values(url_value):
        source_url_id = url_lookup.get(url)

        if pd.notna(source_url_id):
            relation_rows.append(
                {
                    "start_id": start_id,
                    "end_source_url_id": source_url_id,
                    "relation_type": relation_type,
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

def build_default_paths(script_path):
    base_dir = resolve_neo4j_dir(script_path)
    project_root = resolve_project_root(script_path)
    import_dir = resolve_import_dir(project_root)
    normalized_dir = base_dir / "normalized"
    dictionary_dir = base_dir / "dictionary"
    mapping_dir = base_dir / "mapping"
    staging_dir = base_dir / "staging"
    seed_dir = base_dir / "seed"

    return {
        "terms": normalized_dir / "terms.csv",
        "events": normalized_dir / "events.csv",
        "event_relations": normalized_dir / "event_relations.csv",
        "person_relations": normalized_dir / "person_relations.csv",
        "canonical_category_dictionary": dictionary_dir / "canonical_category_dictionary.csv",
        "source_event_category_dictionary": dictionary_dir / "source_event_category_dictionary.csv",
        "taxonomy_crosswalk": mapping_dir / "taxonomy_crosswalk.csv",
        "event_facet_dictionary": dictionary_dir / "event_facet_dictionary.csv",
        "source_event_category_facet_crosswalk": (
            mapping_dir / "source_event_category_facet_crosswalk.csv"
        ),
        "country_dictionary": dictionary_dir / "country_dictionary.csv",
        "canonical_category_country_crosswalk": (
            mapping_dir / "canonical_category_country_crosswalk.csv"
        ),
        "region_dictionary": dictionary_dir / "region_dictionary.csv",
        "canonical_category_region_crosswalk": (
            mapping_dir / "canonical_category_region_crosswalk.csv"
        ),
        "economic_domain_dictionary": dictionary_dir / "economic_domain_dictionary.csv",
        "canonical_category_economic_domain_crosswalk": (
            mapping_dir / "canonical_category_economic_domain_crosswalk.csv"
        ),
        "taxonomy_facet_dictionary": dictionary_dir / "taxonomy_facet_dictionary.csv",
        "canonical_category_taxonomy_facet_crosswalk": (
            mapping_dir / "canonical_category_taxonomy_facet_crosswalk.csv"
        ),
        "period_dictionary": dictionary_dir / "period_dictionary.csv",
        "relation_type_dictionary": dictionary_dir / "relation_type_dictionary.csv",
        "source_url_dictionary": dictionary_dir / "source_url_dictionary.csv",
        "term_canonical_category_relation": staging_dir / "term_canonical_category_relation.csv",
        "event_source_category_relation": staging_dir / "event_source_category_relation.csv",
        "event_date_parse": staging_dir / "event_date_parse.csv",
        "term_year_parse": staging_dir / "term_year_parse.csv",
        "keyword_era_seed": seed_dir / "keyword_era_seed.csv",
        "mention_rule_seed": seed_dir / "mention_rule_seed.csv",
        "graph_config_seed": seed_dir / "graph_config_seed.csv",
        "term_person_review_approved": seed_dir / "term_person_review_approved.csv",
        "nodes_dir": import_dir / "nodes",
        "relations_dir": import_dir / "relations",
    }


def read_inputs(args):
    return {
        "terms": read_csv(args.terms_path, "terms"),
        "events": read_csv(args.events_path, "events"),
        "event_relations": read_csv(args.event_relations_path, "event_relations"),
        "person_relations": read_csv(args.person_relations_path, "person_relations"),
        "canonical_category_dictionary": read_csv(
            args.canonical_category_dictionary_path,
            "canonical_category_dictionary",
        ),
        "source_event_category_dictionary": read_csv(
            args.source_event_category_dictionary_path,
            "source_event_category_dictionary",
        ),
        "taxonomy_crosswalk": read_csv(args.taxonomy_crosswalk_path, "taxonomy_crosswalk"),
        "event_facet_dictionary": read_csv(
            args.event_facet_dictionary_path,
            "event_facet_dictionary",
        ),
        "source_event_category_facet_crosswalk": read_csv(
            args.source_event_category_facet_crosswalk_path,
            "source_event_category_facet_crosswalk",
        ),
        "country_dictionary": read_csv(args.country_dictionary_path, "country_dictionary"),
        "canonical_category_country_crosswalk": read_csv(
            args.canonical_category_country_crosswalk_path,
            "canonical_category_country_crosswalk",
        ),
        "region_dictionary": read_csv(args.region_dictionary_path, "region_dictionary"),
        "canonical_category_region_crosswalk": read_csv(
            args.canonical_category_region_crosswalk_path,
            "canonical_category_region_crosswalk",
        ),
        "economic_domain_dictionary": read_csv(
            args.economic_domain_dictionary_path,
            "economic_domain_dictionary",
        ),
        "canonical_category_economic_domain_crosswalk": read_csv(
            args.canonical_category_economic_domain_crosswalk_path,
            "canonical_category_economic_domain_crosswalk",
        ),
        "taxonomy_facet_dictionary": read_csv(
            args.taxonomy_facet_dictionary_path,
            "taxonomy_facet_dictionary",
        ),
        "canonical_category_taxonomy_facet_crosswalk": read_csv(
            args.canonical_category_taxonomy_facet_crosswalk_path,
            "canonical_category_taxonomy_facet_crosswalk",
        ),
        "period_dictionary": read_csv(args.period_dictionary_path, "period_dictionary"),
        "relation_type_dictionary": read_csv(
            args.relation_type_dictionary_path,
            "relation_type_dictionary",
        ),
        "source_url_dictionary": read_csv(
            args.source_url_dictionary_path,
            "source_url_dictionary",
        ),
        "term_canonical_category_relation": read_csv(
            args.term_canonical_category_relation_path,
            "term_canonical_category_relation",
        ),
        "event_source_category_relation": read_csv(
            args.event_source_category_relation_path,
            "event_source_category_relation",
        ),
        "event_date_parse": read_csv(args.event_date_parse_path, "event_date_parse"),
        "term_year_parse": read_csv(args.term_year_parse_path, "term_year_parse"),
        "keyword_era_seed": read_csv(args.keyword_era_seed_path, "keyword_era_seed"),
        "mention_rule_seed": read_csv(args.mention_rule_seed_path, "mention_rule_seed"),
        "graph_config_seed": read_csv(args.graph_config_seed_path, "graph_config_seed"),
        "term_person_review_approved": read_optional_csv(
            args.term_person_review_approved_path,
            "term_person_review_approved",
        ),
    }


def build_node_outputs(inputs):
    event_group_nodes = build_event_group_nodes(inputs["events"])
    term_nodes = build_term_nodes(
        inputs["terms"],
        inputs["keyword_era_seed"],
        inputs["term_year_parse"],
        inputs["graph_config_seed"],
    )
    event_nodes = build_event_nodes(inputs["events"], inputs["event_date_parse"])
    people_nodes = build_person_nodes(
        inputs["event_relations"],
        inputs["person_relations"],
    )
    search_tag_nodes = build_search_tag_nodes(
        term_nodes,
        inputs["events"],
        people_nodes,
        inputs["period_dictionary"],
        inputs["canonical_category_dictionary"],
        inputs["source_event_category_dictionary"],
        inputs["event_facet_dictionary"],
        inputs["country_dictionary"],
        inputs["region_dictionary"],
        inputs["economic_domain_dictionary"],
        inputs["taxonomy_facet_dictionary"],
    )

    return {
        "terms": term_nodes,
        "canonical_categories": build_category_nodes(inputs["canonical_category_dictionary"]),
        "source_event_categories": build_event_category_nodes(
            inputs["source_event_category_dictionary"]
        ),
        "event_facets": build_event_facet_nodes(inputs["event_facet_dictionary"]),
        "countries": build_country_nodes(inputs["country_dictionary"]),
        "regions": build_region_nodes(inputs["region_dictionary"]),
        "economic_domains": build_economic_domain_nodes(
            inputs["economic_domain_dictionary"]
        ),
        "taxonomy_facets": build_taxonomy_facet_nodes(
            inputs["taxonomy_facet_dictionary"]
        ),
        "search_tags": search_tag_nodes,
        "events": event_nodes,
        "event_groups": event_group_nodes,
        "people": people_nodes,
        "periods": build_period_nodes(inputs["period_dictionary"]),
        "source_urls": build_source_url_nodes(inputs["source_url_dictionary"]),
    }


def build_relation_outputs(inputs, node_outputs):
    event_has_source_category = build_event_has_source_category(
        inputs["event_source_category_relation"]
    )
    event_has_canonical_category = build_event_has_canonical_category(
        inputs["event_source_category_relation"],
        inputs["taxonomy_crosswalk"],
    )
    event_has_facet = build_event_has_facet(
        inputs["event_source_category_relation"],
        inputs["source_event_category_facet_crosswalk"],
    )
    term_has_canonical_category = build_term_has_category(
        inputs["term_canonical_category_relation"]
    )
    geo_facet_category_paths = collect_geo_facet_category_paths(
        inputs["canonical_category_country_crosswalk"],
        inputs["canonical_category_region_crosswalk"],
    )
    canonical_category_about_country = build_canonical_category_about_country(
        inputs["canonical_category_country_crosswalk"]
    )
    canonical_category_about_region = build_canonical_category_about_region(
        inputs["canonical_category_region_crosswalk"]
    )
    canonical_category_about_economic_domain = build_canonical_category_about_economic_domain(
        inputs["canonical_category_economic_domain_crosswalk"]
    )
    canonical_category_about_taxonomy_facet = build_canonical_category_about_taxonomy_facet(
        inputs["canonical_category_taxonomy_facet_crosswalk"]
    )
    term_about_country = build_term_about_country(
        term_has_canonical_category,
        inputs["canonical_category_country_crosswalk"],
    )
    term_about_region = build_term_about_region(
        term_has_canonical_category,
        inputs["canonical_category_region_crosswalk"],
    )
    term_about_economic_domain = build_term_about_economic_domain(
        term_has_canonical_category,
        inputs["canonical_category_economic_domain_crosswalk"],
    )
    term_about_taxonomy_facet = build_term_about_taxonomy_facet(
        term_has_canonical_category,
        inputs["canonical_category_taxonomy_facet_crosswalk"],
    )
    term_refers_to_person = build_term_refers_to_person(
        node_outputs["terms"],
        node_outputs["people"],
        inputs["person_relations"],
        inputs["term_person_review_approved"],
    )
    event_about_country = build_event_about_country(
        event_has_canonical_category,
        inputs["canonical_category_country_crosswalk"],
    )
    event_about_region = build_event_about_region(
        event_has_canonical_category,
        inputs["canonical_category_region_crosswalk"],
    )
    event_about_economic_domain = build_event_about_economic_domain(
        event_has_canonical_category,
        inputs["canonical_category_economic_domain_crosswalk"],
    )
    event_about_taxonomy_facet = build_event_about_taxonomy_facet(
        event_has_canonical_category,
        inputs["canonical_category_taxonomy_facet_crosswalk"],
    )
    term_in_period = build_term_in_period(inputs["terms"], inputs["period_dictionary"])
    event_in_period = build_event_in_period(
        inputs["events"],
        inputs["period_dictionary"],
    )
    term_has_search_tag = build_term_has_search_tag(
        node_outputs["terms"],
        term_has_canonical_category,
        term_about_country,
        term_about_region,
        term_about_economic_domain,
        term_about_taxonomy_facet,
        term_in_period,
        node_outputs["search_tags"],
    )
    event_has_search_tag = build_event_has_search_tag(
        node_outputs["events"],
        event_has_source_category,
        event_has_canonical_category,
        event_has_facet,
        event_in_period,
        event_about_country,
        event_about_region,
        event_about_economic_domain,
        event_about_taxonomy_facet,
        node_outputs["search_tags"],
    )
    person_involved_in_event = build_person_involved_in_event(
        inputs["event_relations"]
    )
    person_related_to_person = build_person_related_to_person(
        inputs["person_relations"],
        inputs["relation_type_dictionary"],
    )
    term_mentions_person = build_term_mentions_person(
        node_outputs["terms"],
        node_outputs["people"],
        term_refers_to_person,
        inputs["mention_rule_seed"],
        inputs["graph_config_seed"],
    )
    term_refers_to_event = build_term_refers_to_event(
        node_outputs["terms"],
        node_outputs["events"],
    )
    person_has_search_tag = build_person_has_search_tag(
        node_outputs["people"],
        person_involved_in_event,
        term_refers_to_person,
        term_has_search_tag,
        event_has_search_tag,
        node_outputs["search_tags"],
    )

    return {
        "term_has_canonical_category": term_has_canonical_category,
        "canonical_category_subcategory_of": build_category_subcategory_of(
            inputs["canonical_category_dictionary"],
            geo_facet_category_paths,
        ),
        "canonical_category_about_country": canonical_category_about_country,
        "canonical_category_about_region": canonical_category_about_region,
        "canonical_category_about_economic_domain": canonical_category_about_economic_domain,
        "canonical_category_about_taxonomy_facet": canonical_category_about_taxonomy_facet,
        "region_subregion_of": build_region_subregion_of(inputs["region_dictionary"]),
        "event_has_source_category": event_has_source_category,
        "source_category_mapped_to_canonical_category": build_source_category_mapped_to_canonical_category(
            inputs["taxonomy_crosswalk"]
        ),
        "event_has_canonical_category": event_has_canonical_category,
        "event_has_facet": event_has_facet,
        "term_about_country": term_about_country,
        "event_about_country": event_about_country,
        "term_about_region": term_about_region,
        "event_about_region": event_about_region,
        "term_about_economic_domain": term_about_economic_domain,
        "event_about_economic_domain": event_about_economic_domain,
        "term_about_taxonomy_facet": term_about_taxonomy_facet,
        "event_about_taxonomy_facet": event_about_taxonomy_facet,
        "term_has_search_tag": term_has_search_tag,
        "event_has_search_tag": event_has_search_tag,
        "person_has_search_tag": person_has_search_tag,
        "term_in_period": term_in_period,
        "event_in_period": event_in_period,
        "event_part_of_event_group": build_event_part_of_group(
            inputs["events"],
            node_outputs["event_groups"],
        ),
        "person_involved_in_event": person_involved_in_event,
        "person_related_to_person": person_related_to_person,
        "term_refers_to_person": term_refers_to_person,
        "term_mentions_person": term_mentions_person,
        "term_refers_to_event": term_refers_to_event,
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


def build_optional_empty_relation_output_names():
    return {
        "event_about_region",
        "event_about_economic_domain",
    }


def should_skip_empty_relation_output(output_name, data_frame):
    optional_output_names = build_optional_empty_relation_output_names()

    if output_name in optional_output_names and len(data_frame) == 0:
        return True

    return False


def build_output_files(args, node_outputs, relation_outputs):
    output_files = []
    skipped_output_files = []

    for output_name, data_frame in node_outputs.items():
        output_files.append(
            (f"{output_name}.csv", data_frame, args.nodes_dir / f"{output_name}.csv")
        )

    for output_name, data_frame in relation_outputs.items():
        output_file = (
            f"{output_name}.csv",
            data_frame,
            args.relations_dir / f"{output_name}.csv",
        )
        skip_output = should_skip_empty_relation_output(output_name, data_frame)

        if skip_output:
            skipped_output_files.append(output_file)

        if not skip_output:
            output_files.append(output_file)

    for output_name in build_discontinued_relation_output_names():
        skipped_output_files.append(
            (
                f"{output_name}.csv",
                pd.DataFrame(),
                args.relations_dir / f"{output_name}.csv",
            )
        )

    return output_files, skipped_output_files


def write_or_print_outputs(args, output_files, skipped_output_files):
    if args.save:
        for file_name, data_frame, output_path in output_files:
            save_csv(data_frame, output_path)
            print_summary(file_name, data_frame)

        for file_name, data_frame, output_path in skipped_output_files:
            remove_stale_output_file(output_path)
            print_summary(file_name, data_frame)
            print(f"skipped_empty_output: {output_path}")

        print(f"nodes_dir: {args.nodes_dir}")
        print(f"relations_dir: {args.relations_dir}")

    if not args.save:
        for file_name, data_frame, output_path in output_files:
            print_summary(file_name, data_frame)
            print(f"planned_path: {output_path}")

        for file_name, data_frame, output_path in skipped_output_files:
            print_summary(file_name, data_frame)
            print(f"planned_skip_empty_output: {output_path}")

        print("dry_run: no files saved. Use --save to write CSV files.")


def main():
    script_path = Path(__file__).resolve()
    default_paths = build_default_paths(script_path)
    args = parse_args(default_paths)
    inputs = read_inputs(args)
    node_outputs = build_node_outputs(inputs)
    relation_outputs = build_relation_outputs(inputs, node_outputs)
    output_files, skipped_output_files = build_output_files(
        args,
        node_outputs,
        relation_outputs,
    )

    write_or_print_outputs(args, output_files, skipped_output_files)


if __name__ == "__main__":
    main()
