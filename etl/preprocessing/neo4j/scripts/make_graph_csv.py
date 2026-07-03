"""
정규화 CSV, 사전 CSV, staging CSV를 Neo4j 적재용 최종 노드/관계 CSV로 변환한다.

기본 실행은 dry-run이다. CSV 저장이 필요할 때만 --save를 사용한다.
"""

import argparse
import re
from pathlib import Path

import pandas as pd

from neo4j_common import (
    build_sequential_ids,
    clean_value,
    first_value,
    print_summary,
    read_csv,
    resolve_neo4j_dir,
    save_csv,
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


def build_search_tag_nodes(
    category_dictionary,
    event_category_dictionary,
    event_facet_dictionary,
    country_dictionary,
    region_dictionary,
    economic_domain_dictionary,
    taxonomy_facet_dictionary,
):
    tag_rows = []
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
    start_event_id,
    source_node_type,
    source_node_id,
    source_relation,
    search_tag_lookup,
):
    search_tag_id = search_tag_lookup.get((source_node_type, source_node_id))

    if pd.notna(search_tag_id):
        relation_rows.append(
            {
                "start_event_id": start_event_id,
                "end_search_tag_id": search_tag_id,
                "relation_type": "HAS_SEARCH_TAG",
                "source_node_type": source_node_type,
                "source_node_id": source_node_id,
                "source_relation": source_relation,
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
            row.start_event_id,
            "TaxonomyFacet",
            row.end_taxonomy_facet_id,
            "event_about_taxonomy_facet",
            search_tag_lookup,
        )


def build_event_has_search_tag(
    event_has_source_category,
    event_has_canonical_category,
    event_has_facet,
    event_about_country,
    event_about_region,
    event_about_economic_domain,
    event_about_taxonomy_facet,
    search_tag_nodes,
):
    relation_rows = []
    search_tag_lookup = build_search_tag_lookup(search_tag_nodes)
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

    if len(relation_rows) == 0:
        return pd.DataFrame(
            columns=[
                "start_event_id",
                "end_search_tag_id",
                "relation_type",
                "source_node_type",
                "source_node_id",
                "source_relation",
            ]
        )

    return pd.DataFrame(relation_rows).drop_duplicates().reset_index(drop=True)


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
    relation_data = relation_data.drop(columns=["raw_relation_type"])
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


def build_default_paths(script_path):
    base_dir = resolve_neo4j_dir(script_path)
    normalized_dir = base_dir / "normalized"
    dictionary_dir = base_dir / "dictionary"
    mapping_dir = base_dir / "mapping"
    staging_dir = base_dir / "staging"
    graph_dir = base_dir / "graph"

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
        "nodes_dir": graph_dir / "nodes",
        "relations_dir": graph_dir / "relations",
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
    }


def build_node_outputs(inputs):
    event_group_nodes = build_event_group_nodes(inputs["events"])
    search_tag_nodes = build_search_tag_nodes(
        inputs["canonical_category_dictionary"],
        inputs["source_event_category_dictionary"],
        inputs["event_facet_dictionary"],
        inputs["country_dictionary"],
        inputs["region_dictionary"],
        inputs["economic_domain_dictionary"],
        inputs["taxonomy_facet_dictionary"],
    )

    return {
        "terms": build_term_nodes(inputs["terms"]),
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
        "events": build_event_nodes(inputs["events"], inputs["event_date_parse"]),
        "event_groups": event_group_nodes,
        "people": build_person_nodes(inputs["event_relations"], inputs["person_relations"]),
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
        "event_has_search_tag": build_event_has_search_tag(
            event_has_source_category,
            event_has_canonical_category,
            event_has_facet,
            event_about_country,
            event_about_region,
            event_about_economic_domain,
            event_about_taxonomy_facet,
            node_outputs["search_tags"],
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
