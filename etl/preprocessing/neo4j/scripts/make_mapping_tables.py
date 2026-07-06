"""
1차 사전 CSV를 기준으로 Neo4j 적재용 관계/매핑 테이블을 만든다.

먼저 make_base_dictionaries.py --save를 실행해 1차 사전 CSV를 저장해야 한다.
"""

import argparse
from pathlib import Path

import pandas as pd

from neo4j_common import (
    build_sequential_ids,
    print_summary,
    require_file,
    resolve_neo4j_dir,
    save_csv,
    split_category_paths,
    split_event_category_tokens,
    unique_join,
)


def parse_args(default_paths):
    parser = argparse.ArgumentParser(
        description="Neo4j용 2차 관계/매핑 CSV를 만든다."
    )
    parser.add_argument(
        "--terms-path",
        default=default_paths["terms"],
        type=Path,
        help="정규화된 terms CSV 경로.",
    )
    parser.add_argument(
        "--events-path",
        default=default_paths["events"],
        type=Path,
        help="정규화된 events CSV 경로.",
    )
    parser.add_argument(
        "--dictionary-dir",
        default=default_paths["dictionary_dir"],
        type=Path,
        help="1차 사전 CSV가 저장된 폴더.",
    )
    parser.add_argument(
        "--staging-dir",
        default=default_paths["staging_dir"],
        type=Path,
        help="staging CSV 저장 폴더.",
    )
    parser.add_argument(
        "--mapping-dir",
        default=default_paths["mapping_dir"],
        type=Path,
        help="crosswalk/mapping CSV 저장 폴더.",
    )
    parser.add_argument(
        "--taxonomy-crosswalk-seed-path",
        default=default_paths["taxonomy_crosswalk_seed"],
        type=Path,
        help="수동 검수 taxonomy crosswalk seed CSV 경로.",
    )
    parser.add_argument(
        "--event-facet-seed-path",
        default=default_paths["event_facet_seed"],
        type=Path,
        help="원천 이벤트 분류를 facet으로 재분류한 seed CSV 경로.",
    )
    parser.add_argument(
        "--country-seed-path",
        default=default_paths["country_seed"],
        type=Path,
        help="국가/정치체 seed CSV 경로.",
    )
    parser.add_argument(
        "--region-seed-path",
        default=default_paths["region_seed"],
        type=Path,
        help="권역/지역 seed CSV 경로.",
    )
    parser.add_argument(
        "--category-axis-seed-path",
        default=default_paths["category_axis_seed"],
        type=Path,
        help="표준 카테고리에서 의미 축을 추출하는 seed CSV 경로.",
    )
    parser.add_argument(
        "--save",
        action="store_true",
        help="CSV 파일을 저장한다. 지정하지 않으면 dry-run으로 요약만 출력한다.",
    )
    return parser.parse_args()


def build_term_relation_rows(terms_data):
    relation_rows = []
    target_data = terms_data.dropna(subset=["term_lk"]).copy()

    if "term_kind" in target_data.columns:
        target_data = target_data[target_data["term_kind"].eq(2)].copy()

    for row in target_data[["term_id", "term_lk"]].itertuples(index=False):
        for path_parts in split_category_paths(row.term_lk):
            relation_rows.append(
                {
                    "term_id": row.term_id,
                    "category_path": ">".join(path_parts),
                    "source_term_lk": row.term_lk,
                }
            )

    return relation_rows


def build_term_category_relation(terms_data, category_dictionary):
    relation_rows = build_term_relation_rows(terms_data)

    if len(relation_rows) == 0:
        return pd.DataFrame(
            columns=["term_id", "category_id", "category_path", "source_term_lk"]
        )

    term_category_relation = (
        pd.DataFrame(relation_rows)
        .drop_duplicates(subset=["term_id", "category_path"])
        .reset_index(drop=True)
    )
    path_to_id = dict(
        zip(category_dictionary["category_path"], category_dictionary["category_id"])
    )
    term_category_relation["category_id"] = term_category_relation["category_path"].map(path_to_id)

    return term_category_relation[
        ["term_id", "category_id", "category_path", "source_term_lk"]
    ]


def build_event_relation_rows(events_data):
    relation_rows = []
    target_data = events_data.dropna(subset=["subject_category"]).copy()

    for row in target_data[["event_id", "subject_category"]].itertuples(index=False):
        for token in split_event_category_tokens(row.subject_category):
            relation_rows.append(
                {
                    "event_id": row.event_id,
                    "event_category_name": token,
                    "source_subject_category": row.subject_category,
                }
            )

    return relation_rows


def build_event_category_relation(events_data, event_category_dictionary):
    relation_rows = build_event_relation_rows(events_data)

    if len(relation_rows) == 0:
        return pd.DataFrame(
            columns=[
                "event_id",
                "event_category_id",
                "event_category_name",
                "source_subject_category",
            ]
        )

    event_category_relation = (
        pd.DataFrame(relation_rows)
        .drop_duplicates(subset=["event_id", "event_category_name"])
        .reset_index(drop=True)
    )
    name_to_id = dict(
        zip(
            event_category_dictionary["event_category_name"],
            event_category_dictionary["event_category_id"],
        )
    )
    event_category_relation["event_category_id"] = (
        event_category_relation["event_category_name"].map(name_to_id)
    )

    return event_category_relation[
        [
            "event_id",
            "event_category_id",
            "event_category_name",
            "source_subject_category",
        ]
    ]


def empty_taxonomy_crosswalk_seed():
    return pd.DataFrame(
        columns=[
            "event_category_name",
            "mapped_category_path",
            "mapping_type",
            "confidence",
            "review_status",
            "note",
        ]
    )


def read_taxonomy_crosswalk_seed(seed_path):
    if not seed_path.exists():
        return empty_taxonomy_crosswalk_seed()

    return pd.read_csv(seed_path)


def normalize_taxonomy_crosswalk_seed(
    taxonomy_crosswalk_seed,
    event_category_dictionary,
    category_dictionary,
):
    if len(taxonomy_crosswalk_seed) == 0:
        return pd.DataFrame(
            columns=[
                "event_category_id",
                "event_category_name",
                "mapped_category_id",
                "mapped_category_path",
                "mapping_type",
                "confidence",
                "review_status",
                "note",
            ]
        )

    seed_data = taxonomy_crosswalk_seed.copy()
    category_lookup = category_dictionary[
        ["category_id", "category_path"]
    ].drop_duplicates()
    event_category_lookup = event_category_dictionary[
        ["event_category_id", "event_category_name"]
    ].drop_duplicates()

    seed_data = seed_data.merge(
        event_category_lookup,
        on="event_category_name",
        how="inner",
    )
    seed_data = seed_data.merge(
        category_lookup,
        left_on="mapped_category_path",
        right_on="category_path",
        how="left",
    )
    seed_data = seed_data.rename(columns={"category_id": "mapped_category_id"})

    for column_name, default_value in build_seed_defaults().items():
        if column_name not in seed_data.columns:
            seed_data[column_name] = default_value

        seed_data[column_name] = seed_data[column_name].fillna(default_value)

    return seed_data[
        [
            "event_category_id",
            "event_category_name",
            "mapped_category_id",
            "mapped_category_path",
            "mapping_type",
            "confidence",
            "review_status",
            "note",
        ]
    ]


def build_seed_defaults():
    return {
        "mapping_type": "MANUAL",
        "confidence": "MEDIUM",
        "review_status": "PENDING",
        "note": "",
    }


def build_event_facet_seed_defaults():
    return {
        "confidence": "MEDIUM",
        "review_status": "PENDING",
        "note": "",
    }


def build_country_seed_defaults():
    return {
        "country_type": "COUNTRY_OR_POLITY",
        "aliases": "",
        "review_status": "PENDING",
        "note": "",
    }


def empty_event_facet_seed():
    return pd.DataFrame(
        columns=[
            "event_category_name",
            "facet_type",
            "facet_name",
            "confidence",
            "review_status",
            "note",
        ]
    )


def read_event_facet_seed(seed_path):
    if not seed_path.exists():
        return empty_event_facet_seed()

    return pd.read_csv(seed_path)


def empty_country_seed():
    return pd.DataFrame(
        columns=[
            "country_name",
            "country_type",
            "canonical_category_path",
            "aliases",
            "review_status",
            "note",
        ]
    )


def read_country_seed(seed_path):
    if not seed_path.exists():
        return empty_country_seed()

    return pd.read_csv(seed_path)


def build_region_seed_defaults():
    return {
        "region_type": "MACRO_REGION",
        "parent_region_name": "",
        "aliases": "",
        "review_status": "PENDING",
        "note": "",
    }


def empty_region_seed():
    return pd.DataFrame(
        columns=[
            "region_name",
            "region_type",
            "canonical_category_path",
            "parent_region_name",
            "aliases",
            "review_status",
            "note",
        ]
    )


def read_region_seed(seed_path):
    if not seed_path.exists():
        return empty_region_seed()

    return pd.read_csv(seed_path)


def build_category_axis_seed_columns():
    return [
        "axis_key",
        "axis_name",
        "root_category_name",
        "axis_depth",
        "axis_type",
        "review_status",
        "note",
    ]


def empty_category_axis_seed():
    return pd.DataFrame(columns=build_category_axis_seed_columns())


def read_category_axis_seed(seed_path):
    if not seed_path.exists():
        return empty_category_axis_seed()

    return pd.read_csv(seed_path)


def normalize_category_axis_seed(category_axis_seed):
    if len(category_axis_seed) == 0:
        return empty_category_axis_seed()

    seed_data = category_axis_seed.copy()

    for column_name in build_category_axis_seed_columns():
        if column_name not in seed_data.columns:
            seed_data[column_name] = pd.NA

    seed_data["axis_depth"] = pd.to_numeric(
        seed_data["axis_depth"],
        errors="coerce",
    )

    return seed_data[build_category_axis_seed_columns()].drop_duplicates(
        subset=["axis_key"]
    )


def get_category_axis_config(category_axis_seed, axis_key):
    seed_data = normalize_category_axis_seed(category_axis_seed)
    matched_data = seed_data[seed_data["axis_key"].eq(axis_key)].copy()

    if len(matched_data) == 0:
        return {
            "axis_key": axis_key,
            "axis_name": pd.NA,
            "root_category_name": pd.NA,
            "axis_depth": pd.NA,
            "axis_type": pd.NA,
            "review_status": pd.NA,
            "note": "",
        }

    return matched_data.iloc[0].to_dict()


def normalize_country_seed(country_seed):
    if len(country_seed) == 0:
        return empty_country_seed()

    seed_data = country_seed.copy()

    for column_name, default_value in build_country_seed_defaults().items():
        if column_name not in seed_data.columns:
            seed_data[column_name] = default_value

        seed_data[column_name] = seed_data[column_name].fillna(default_value)

    return seed_data[
        [
            "country_name",
            "country_type",
            "canonical_category_path",
            "aliases",
            "review_status",
            "note",
        ]
    ]


def build_country_dictionary(category_dictionary, country_seed):
    seed_data = normalize_country_seed(country_seed)

    if len(seed_data) == 0:
        return pd.DataFrame(
            columns=[
                "country_id",
                "country_name",
                "country_type",
                "canonical_category_id",
                "canonical_category_path",
                "aliases",
                "review_status",
                "note",
                "source",
            ]
        )

    category_lookup = category_dictionary[
        ["category_id", "category_path"]
    ].drop_duplicates()
    country_dictionary = seed_data.merge(
        category_lookup,
        left_on="canonical_category_path",
        right_on="category_path",
        how="left",
    )
    country_dictionary = country_dictionary.rename(
        columns={"category_id": "canonical_category_id"}
    )
    country_dictionary = (
        country_dictionary
        .drop_duplicates(subset=["country_name", "canonical_category_path"])
        .sort_values(["country_name"])
        .reset_index(drop=True)
    )
    country_dictionary.insert(
        0,
        "country_id",
        build_sequential_ids("COUNTRY", len(country_dictionary), 5),
    )
    country_dictionary["source"] = "country_seed"

    return country_dictionary[
        [
            "country_id",
            "country_name",
            "country_type",
            "canonical_category_id",
            "canonical_category_path",
            "aliases",
            "review_status",
            "note",
            "source",
        ]
    ]


def normalize_region_seed(region_seed):
    if len(region_seed) == 0:
        return empty_region_seed()

    seed_data = region_seed.copy()

    for column_name, default_value in build_region_seed_defaults().items():
        if column_name not in seed_data.columns:
            seed_data[column_name] = default_value

        seed_data[column_name] = seed_data[column_name].fillna(default_value)

    return seed_data[
        [
            "region_name",
            "region_type",
            "canonical_category_path",
            "parent_region_name",
            "aliases",
            "review_status",
            "note",
        ]
    ]


def empty_region_dictionary():
    return pd.DataFrame(
        columns=[
            "region_id",
            "region_name",
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
    )


def build_region_dictionary(category_dictionary, region_seed):
    seed_data = normalize_region_seed(region_seed)

    if len(seed_data) == 0:
        return empty_region_dictionary()

    category_lookup = category_dictionary[
        ["category_id", "category_path"]
    ].drop_duplicates()
    region_dictionary = seed_data.merge(
        category_lookup,
        left_on="canonical_category_path",
        right_on="category_path",
        how="left",
    )
    region_dictionary = region_dictionary.rename(
        columns={"category_id": "canonical_category_id"}
    )
    region_dictionary = (
        region_dictionary
        .drop_duplicates(subset=["region_name", "canonical_category_path"])
        .sort_values(["canonical_category_path", "region_name"])
        .reset_index(drop=True)
    )
    region_dictionary.insert(
        0,
        "region_id",
        build_sequential_ids("REGION", len(region_dictionary), 5),
    )
    region_lookup = dict(
        zip(region_dictionary["region_name"], region_dictionary["region_id"])
    )
    region_dictionary["parent_region_id"] = (
        region_dictionary["parent_region_name"].map(region_lookup)
    )
    region_dictionary["source"] = "region_seed"

    return region_dictionary[empty_region_dictionary().columns.tolist()]


def empty_canonical_category_region_crosswalk():
    return pd.DataFrame(
        columns=[
            "canonical_category_id",
            "canonical_category_path",
            "region_id",
            "region_name",
            "region_type",
            "region_path",
            "match_type",
            "review_status",
            "note",
        ]
    )


def build_canonical_category_region_crosswalk(category_dictionary, region_dictionary):
    if len(region_dictionary) == 0:
        return empty_canonical_category_region_crosswalk()

    category_data = category_dictionary[
        ["category_id", "category_path"]
    ].drop_duplicates().copy()
    crosswalk_rows = []

    for region_row in region_dictionary.itertuples(index=False):
        region_path = str(region_row.canonical_category_path)
        region_prefix = f"{region_path}>"

        for category_row in category_data.itertuples(index=False):
            category_path = str(category_row.category_path)

            if category_path == region_path or category_path.startswith(region_prefix):
                match_type = "DESCENDANT_PATH"

                if category_path == region_path:
                    match_type = "SELF_PATH"

                crosswalk_rows.append(
                    {
                        "canonical_category_id": category_row.category_id,
                        "canonical_category_path": category_path,
                        "region_id": region_row.region_id,
                        "region_name": region_row.region_name,
                        "region_type": region_row.region_type,
                        "region_path": region_path,
                        "match_type": match_type,
                        "review_status": region_row.review_status,
                        "note": region_row.note,
                    }
                )

    if len(crosswalk_rows) == 0:
        return empty_canonical_category_region_crosswalk()

    return (
        pd.DataFrame(crosswalk_rows)
        .drop_duplicates(subset=["canonical_category_id", "region_id"])
        .sort_values(["region_path", "canonical_category_path"])
        .reset_index(drop=True)
    )[empty_canonical_category_region_crosswalk().columns.tolist()]


def extract_country_from_category_path(category_path, country_names, axis_config):
    if pd.isna(category_path):
        return pd.NA

    root_category_name = axis_config["root_category_name"]

    if pd.isna(root_category_name):
        return pd.NA

    path_parts = str(category_path).split(">")

    if len(path_parts) < 2:
        return pd.NA

    if path_parts[0] != root_category_name:
        return pd.NA

    country_name = path_parts[1]

    if country_name in country_names:
        return country_name

    return pd.NA


def build_canonical_category_country_crosswalk(
    category_dictionary,
    country_dictionary,
    country_axis_config,
):
    if len(country_dictionary) == 0:
        return pd.DataFrame(
            columns=[
                "canonical_category_id",
                "canonical_category_path",
                "country_id",
                "country_name",
                "match_type",
                "review_status",
                "note",
            ]
        )

    country_names = set(country_dictionary["country_name"].dropna())
    category_data = category_dictionary[
        ["category_id", "category_path"]
    ].drop_duplicates().copy()
    category_data["country_name"] = category_data["category_path"].apply(
        lambda category_path: extract_country_from_category_path(
            category_path,
            country_names,
            country_axis_config,
        )
    )
    category_data = category_data.dropna(subset=["country_name"]).copy()
    country_lookup = country_dictionary[
        ["country_id", "country_name"]
    ].drop_duplicates()
    country_crosswalk = category_data.merge(
        country_lookup,
        on="country_name",
        how="left",
    )
    country_crosswalk = country_crosswalk.rename(
        columns={
            "category_id": "canonical_category_id",
            "category_path": "canonical_category_path",
        }
    )
    country_crosswalk["match_type"] = "PATH_SECOND_LEVEL"
    country_crosswalk["review_status"] = "PENDING"
    country_crosswalk["note"] = country_axis_config["note"]

    return country_crosswalk[
        [
            "canonical_category_id",
            "canonical_category_path",
            "country_id",
            "country_name",
            "match_type",
            "review_status",
            "note",
        ]
    ]


def extract_economic_domain_from_category_path(category_path, economic_axis_config):
    if pd.isna(category_path):
        return pd.NA

    root_category_name = economic_axis_config["root_category_name"]
    axis_depth = economic_axis_config["axis_depth"]

    if pd.isna(root_category_name) or pd.isna(axis_depth):
        return pd.NA

    path_parts = str(category_path).split(">")
    target_index = int(axis_depth) - 1

    if len(path_parts) <= target_index:
        return pd.NA

    if path_parts[0] != root_category_name:
        return pd.NA

    return path_parts[target_index]


def build_economic_domain_dictionary(category_dictionary, economic_axis_config):
    root_category_name = economic_axis_config["root_category_name"]
    axis_depth = economic_axis_config["axis_depth"]

    if pd.isna(root_category_name) or pd.isna(axis_depth):
        return pd.DataFrame(
            columns=[
                "economic_domain_id",
                "economic_domain_name",
                "domain_type",
                "canonical_category_id",
                "canonical_category_path",
                "review_status",
                "note",
                "source",
            ]
        )

    category_data = category_dictionary[
        ["category_id", "category_name", "category_path", "depth"]
    ].drop_duplicates().copy()
    domain_data = category_data[
        category_data["category_path"].str.startswith(f"{root_category_name}>", na=False)
        & category_data["depth"].astype(str).eq(str(int(axis_depth)))
    ].copy()
    domain_data = domain_data.sort_values("category_path").reset_index(drop=True)
    domain_data.insert(
        0,
        "economic_domain_id",
        build_sequential_ids("ECON_DOMAIN", len(domain_data), 5),
    )
    domain_data = domain_data.rename(
        columns={
            "category_id": "canonical_category_id",
            "category_name": "economic_domain_name",
            "category_path": "canonical_category_path",
        }
    )
    domain_data["domain_type"] = economic_axis_config["axis_type"]
    domain_data["review_status"] = "PENDING"
    domain_data["note"] = economic_axis_config["note"]
    domain_data["source"] = "canonical_category_dictionary"

    return domain_data[
        [
            "economic_domain_id",
            "economic_domain_name",
            "domain_type",
            "canonical_category_id",
            "canonical_category_path",
            "review_status",
            "note",
            "source",
        ]
    ]


def build_canonical_category_economic_domain_crosswalk(
    category_dictionary,
    economic_domain_dictionary,
    economic_axis_config,
):
    if len(economic_domain_dictionary) == 0:
        return pd.DataFrame(
            columns=[
                "canonical_category_id",
                "canonical_category_path",
                "economic_domain_id",
                "economic_domain_name",
                "match_type",
                "review_status",
                "note",
            ]
        )

    category_data = category_dictionary[
        ["category_id", "category_path"]
    ].drop_duplicates().copy()
    category_data["economic_domain_name"] = category_data["category_path"].apply(
        lambda category_path: extract_economic_domain_from_category_path(
            category_path,
            economic_axis_config,
        )
    )
    category_data = category_data.dropna(subset=["economic_domain_name"]).copy()
    domain_lookup = economic_domain_dictionary[
        ["economic_domain_id", "economic_domain_name"]
    ].drop_duplicates()
    domain_crosswalk = category_data.merge(
        domain_lookup,
        on="economic_domain_name",
        how="left",
    )
    domain_crosswalk = domain_crosswalk.rename(
        columns={
            "category_id": "canonical_category_id",
            "category_path": "canonical_category_path",
        }
    )
    domain_crosswalk["match_type"] = "PATH_SECOND_LEVEL"
    domain_crosswalk["review_status"] = "PENDING"
    domain_crosswalk["note"] = economic_axis_config["note"]

    return domain_crosswalk[
        [
            "canonical_category_id",
            "canonical_category_path",
            "economic_domain_id",
            "economic_domain_name",
            "match_type",
            "review_status",
            "note",
        ]
    ]


def build_typed_facet_exclusion_paths(
    country_dictionary,
    economic_domain_dictionary,
    region_dictionary,
):
    exclusion_paths = set()

    for dictionary_data in [country_dictionary, economic_domain_dictionary, region_dictionary]:
        if "canonical_category_path" in dictionary_data.columns:
            paths = dictionary_data["canonical_category_path"].dropna().astype(str)

            for category_path in paths:
                exclusion_paths.add(category_path)

    return exclusion_paths


def count_descendant_category_paths(category_path, all_category_paths):
    path_prefix = f"{category_path}>"
    descendant_count = 0

    for target_path in all_category_paths:
        if target_path.startswith(path_prefix):
            descendant_count += 1

    return descendant_count


def count_child_category_paths(category_path, all_category_paths):
    path_prefix = f"{category_path}>"
    child_count = 0

    for target_path in all_category_paths:
        if target_path.startswith(path_prefix):
            remaining_path = target_path[len(path_prefix):]

            if ">" not in remaining_path:
                child_count += 1

    return child_count


def empty_taxonomy_facet_dictionary():
    return pd.DataFrame(
        columns=[
            "taxonomy_facet_id",
            "taxonomy_facet_name",
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
    )


def build_taxonomy_facet_dictionary(category_dictionary, excluded_category_paths):
    category_data = category_dictionary[
        [
            "category_id",
            "category_name",
            "category_path",
            "depth",
            "root_category_name",
            "term_count",
            "direct_term_count",
        ]
    ].drop_duplicates().copy()
    category_data["depth_number"] = pd.to_numeric(
        category_data["depth"],
        errors="coerce",
    ).fillna(0)
    all_category_paths = category_data["category_path"].dropna().astype(str).tolist()
    facet_rows = []

    for row in category_data.itertuples(index=False):
        category_path = str(row.category_path)
        descendant_count = count_descendant_category_paths(
            category_path,
            all_category_paths,
        )

        if row.depth_number > 1 and descendant_count > 0:
            if category_path not in excluded_category_paths:
                facet_rows.append(
                    {
                        "taxonomy_facet_name": row.category_name,
                        "taxonomy_facet_path": category_path,
                        "taxonomy_facet_depth": int(row.depth_number),
                        "root_category_name": row.root_category_name,
                        "canonical_category_id": row.category_id,
                        "child_category_count": count_child_category_paths(
                            category_path,
                            all_category_paths,
                        ),
                        "descendant_category_count": descendant_count,
                        "term_count": row.term_count,
                        "direct_term_count": row.direct_term_count,
                        "facet_type": "INTERMEDIATE_CANONICAL_CATEGORY",
                        "review_status": "PENDING",
                        "note": "하위 표준 카테고리를 가진 중간 경로에서 자동 추출",
                        "source": "canonical_category_dictionary",
                    }
                )

    if len(facet_rows) == 0:
        return empty_taxonomy_facet_dictionary()

    taxonomy_facet_dictionary = (
        pd.DataFrame(facet_rows)
        .drop_duplicates(subset=["taxonomy_facet_path"])
        .sort_values(["root_category_name", "taxonomy_facet_depth", "taxonomy_facet_path"])
        .reset_index(drop=True)
    )
    taxonomy_facet_dictionary.insert(
        0,
        "taxonomy_facet_id",
        build_sequential_ids("TAXONOMY_FACET", len(taxonomy_facet_dictionary), 5),
    )

    return taxonomy_facet_dictionary[
        empty_taxonomy_facet_dictionary().columns.tolist()
    ]


def empty_canonical_category_taxonomy_facet_crosswalk():
    return pd.DataFrame(
        columns=[
            "canonical_category_id",
            "canonical_category_path",
            "taxonomy_facet_id",
            "taxonomy_facet_name",
            "taxonomy_facet_path",
            "taxonomy_facet_depth",
            "root_category_name",
            "match_type",
            "review_status",
            "note",
        ]
    )


def build_canonical_category_taxonomy_facet_crosswalk(
    category_dictionary,
    taxonomy_facet_dictionary,
):
    if len(taxonomy_facet_dictionary) == 0:
        return empty_canonical_category_taxonomy_facet_crosswalk()

    category_data = category_dictionary[
        ["category_id", "category_path"]
    ].drop_duplicates().copy()
    crosswalk_rows = []

    for facet_row in taxonomy_facet_dictionary.itertuples(index=False):
        facet_path = str(facet_row.taxonomy_facet_path)
        facet_prefix = f"{facet_path}>"

        for category_row in category_data.itertuples(index=False):
            category_path = str(category_row.category_path)

            if category_path == facet_path or category_path.startswith(facet_prefix):
                match_type = "DESCENDANT_PATH"

                if category_path == facet_path:
                    match_type = "SELF_PATH"

                crosswalk_rows.append(
                    {
                        "canonical_category_id": category_row.category_id,
                        "canonical_category_path": category_path,
                        "taxonomy_facet_id": facet_row.taxonomy_facet_id,
                        "taxonomy_facet_name": facet_row.taxonomy_facet_name,
                        "taxonomy_facet_path": facet_path,
                        "taxonomy_facet_depth": facet_row.taxonomy_facet_depth,
                        "root_category_name": facet_row.root_category_name,
                        "match_type": match_type,
                        "review_status": "PENDING",
                        "note": "표준 카테고리 중간 경로 축에서 자동 연결",
                    }
                )

    if len(crosswalk_rows) == 0:
        return empty_canonical_category_taxonomy_facet_crosswalk()

    return (
        pd.DataFrame(crosswalk_rows)
        .drop_duplicates(subset=["canonical_category_id", "taxonomy_facet_id"])
        .sort_values(["taxonomy_facet_path", "canonical_category_path"])
        .reset_index(drop=True)
    )[empty_canonical_category_taxonomy_facet_crosswalk().columns.tolist()]


def normalize_event_facet_seed(event_facet_seed, event_category_dictionary):
    if len(event_facet_seed) == 0:
        return pd.DataFrame(
            columns=[
                "event_category_id",
                "event_category_name",
                "facet_type",
                "facet_name",
                "confidence",
                "review_status",
                "note",
                "event_count",
            ]
        )

    seed_data = event_facet_seed.copy()

    for column_name, default_value in build_event_facet_seed_defaults().items():
        if column_name not in seed_data.columns:
            seed_data[column_name] = default_value

        seed_data[column_name] = seed_data[column_name].fillna(default_value)

    source_lookup = event_category_dictionary[
        ["event_category_id", "event_category_name", "event_count"]
    ].drop_duplicates()
    seed_data = seed_data.merge(
        source_lookup,
        on="event_category_name",
        how="inner",
    )

    return seed_data[
        [
            "event_category_id",
            "event_category_name",
            "facet_type",
            "facet_name",
            "confidence",
            "review_status",
            "note",
            "event_count",
        ]
    ]


def build_event_facet_dictionary(event_category_dictionary, event_facet_seed):
    facet_seed = normalize_event_facet_seed(event_facet_seed, event_category_dictionary)

    if len(facet_seed) == 0:
        return pd.DataFrame(
            columns=[
                "event_facet_id",
                "facet_type",
                "facet_name",
                "source_event_category_count",
                "event_count",
                "confidence",
                "review_status",
                "note",
                "source",
            ]
        )

    facet_seed["event_count"] = pd.to_numeric(facet_seed["event_count"], errors="coerce")
    event_facet_dictionary = (
        facet_seed
        .groupby(["facet_type", "facet_name"], dropna=False)
        .agg(
            source_event_category_count=("event_category_id", "nunique"),
            event_count=("event_count", "sum"),
            confidence=("confidence", unique_join),
            review_status=("review_status", unique_join),
            note=("note", unique_join),
        )
        .reset_index()
        .sort_values(["facet_type", "facet_name"])
        .reset_index(drop=True)
    )
    event_facet_dictionary.insert(
        0,
        "event_facet_id",
        [f"EVENT_FACET_{idx:05d}" for idx in range(1, len(event_facet_dictionary) + 1)],
    )
    event_facet_dictionary["source"] = "event_facet_seed"

    return event_facet_dictionary[
        [
            "event_facet_id",
            "facet_type",
            "facet_name",
            "source_event_category_count",
            "event_count",
            "confidence",
            "review_status",
            "note",
            "source",
        ]
    ]


def build_source_event_category_facet_crosswalk(
    event_category_dictionary,
    event_facet_seed,
    event_facet_dictionary,
):
    facet_seed = normalize_event_facet_seed(event_facet_seed, event_category_dictionary)

    if len(facet_seed) == 0:
        return pd.DataFrame(
            columns=[
                "source_event_category_id",
                "source_event_category_name",
                "event_facet_id",
                "facet_type",
                "facet_name",
                "confidence",
                "review_status",
                "note",
            ]
        )

    facet_lookup = event_facet_dictionary[
        ["event_facet_id", "facet_type", "facet_name"]
    ].drop_duplicates()
    facet_crosswalk = facet_seed.merge(
        facet_lookup,
        on=["facet_type", "facet_name"],
        how="left",
    )
    facet_crosswalk = facet_crosswalk.rename(
        columns={
            "event_category_id": "source_event_category_id",
            "event_category_name": "source_event_category_name",
        }
    )

    return facet_crosswalk[
        [
            "source_event_category_id",
            "source_event_category_name",
            "event_facet_id",
            "facet_type",
            "facet_name",
            "confidence",
            "review_status",
            "note",
        ]
    ]


def apply_taxonomy_crosswalk_seed(category_mapping, seed_mapping):
    if len(seed_mapping) == 0:
        return category_mapping

    seeded_event_category_ids = seed_mapping["event_category_id"].dropna().unique()
    category_mapping = category_mapping[
        ~category_mapping["event_category_id"].isin(seeded_event_category_ids)
    ].copy()
    category_mapping = pd.concat([category_mapping, seed_mapping], ignore_index=True)

    return (
        category_mapping
        .sort_values(["event_category_name", "mapping_type", "mapped_category_path"])
        .reset_index(drop=True)
    )


def build_taxonomy_crosswalk(
    event_category_dictionary,
    category_dictionary,
    taxonomy_crosswalk_seed,
):
    category_leaf = category_dictionary.loc[
        :,
        ["category_id", "category_name", "category_path"],
    ].copy()
    mapping_rows = []

    for row in event_category_dictionary.itertuples(index=False):
        # 우선 이름이 정확히 같은 후보만 자동 매핑한다. 애매하거나 의미 기반인 매핑은 검수 대상으로 둔다.
        exact_matches = category_leaf[
            category_leaf["category_name"].eq(row.event_category_name)
        ]

        if len(exact_matches) > 0:
            for match in exact_matches.itertuples(index=False):
                mapping_rows.append(
                    {
                        "event_category_id": row.event_category_id,
                        "event_category_name": row.event_category_name,
                        "mapped_category_id": match.category_id,
                        "mapped_category_path": match.category_path,
                        "mapping_type": "EXACT_NAME",
                        "confidence": "HIGH",
                        "review_status": "PENDING",
                        "note": "event category name equals category_name",
                    }
                )

        if len(exact_matches) == 0:
            mapping_rows.append(
                {
                    "event_category_id": row.event_category_id,
                    "event_category_name": row.event_category_name,
                    "mapped_category_id": pd.NA,
                    "mapped_category_path": pd.NA,
                    "mapping_type": "UNMAPPED",
                    "confidence": "LOW",
                    "review_status": "PENDING",
                    "note": "manual mapping required",
                }
            )

    category_mapping = pd.DataFrame(mapping_rows)
    seed_mapping = normalize_taxonomy_crosswalk_seed(
        taxonomy_crosswalk_seed,
        event_category_dictionary,
        category_dictionary,
    )

    return apply_taxonomy_crosswalk_seed(category_mapping, seed_mapping)


def build_default_paths(script_path):
    neo4j_dir = resolve_neo4j_dir(script_path)
    normalized_dir = neo4j_dir / "normalized"
    seed_dir = neo4j_dir / "seed"

    return {
        "terms": normalized_dir / "terms.csv",
        "events": normalized_dir / "events.csv",
        "dictionary_dir": neo4j_dir / "dictionary",
        "staging_dir": neo4j_dir / "staging",
        "mapping_dir": neo4j_dir / "mapping",
        "taxonomy_crosswalk_seed": seed_dir / "taxonomy_crosswalk_seed.csv",
        "event_facet_seed": seed_dir / "event_facet_seed.csv",
        "country_seed": seed_dir / "country_seed.csv",
        "region_seed": seed_dir / "region_seed.csv",
        "category_axis_seed": seed_dir / "category_axis_seed.csv",
    }


def build_dictionary_paths(args):
    return {
        "category_dictionary": args.dictionary_dir / "canonical_category_dictionary.csv",
        "event_category_dictionary": args.dictionary_dir / "source_event_category_dictionary.csv",
    }


def read_dictionary_files(dictionary_paths):
    guidance = "먼저 make_base_dictionaries.py --save를 실행해 1차 사전을 저장하세요."
    require_file(dictionary_paths["category_dictionary"], "category_dictionary", guidance)
    require_file(
        dictionary_paths["event_category_dictionary"],
        "event_category_dictionary",
        guidance,
    )

    return {
        "category_dictionary": pd.read_csv(dictionary_paths["category_dictionary"]),
        "event_category_dictionary": pd.read_csv(dictionary_paths["event_category_dictionary"]),
    }


def build_output_specs():
    # crosswalk는 사전 자체가 아니라 서로 다른 기준표를 잇는 매핑표다.
    return [
        ("term_category_relation", "term_canonical_category_relation.csv", "staging_dir"),
        ("event_category_relation", "event_source_category_relation.csv", "staging_dir"),
        ("taxonomy_crosswalk", "taxonomy_crosswalk.csv", "mapping_dir"),
        ("event_facet_dictionary", "event_facet_dictionary.csv", "dictionary_dir"),
        ("country_dictionary", "country_dictionary.csv", "dictionary_dir"),
        ("region_dictionary", "region_dictionary.csv", "dictionary_dir"),
        ("economic_domain_dictionary", "economic_domain_dictionary.csv", "dictionary_dir"),
        ("taxonomy_facet_dictionary", "taxonomy_facet_dictionary.csv", "dictionary_dir"),
        (
            "canonical_category_country_crosswalk",
            "canonical_category_country_crosswalk.csv",
            "mapping_dir",
        ),
        (
            "canonical_category_region_crosswalk",
            "canonical_category_region_crosswalk.csv",
            "mapping_dir",
        ),
        (
            "canonical_category_economic_domain_crosswalk",
            "canonical_category_economic_domain_crosswalk.csv",
            "mapping_dir",
        ),
        (
            "canonical_category_taxonomy_facet_crosswalk",
            "canonical_category_taxonomy_facet_crosswalk.csv",
            "mapping_dir",
        ),
        (
            "source_event_category_facet_crosswalk",
            "source_event_category_facet_crosswalk.csv",
            "mapping_dir",
        ),
    ]


def build_output_files(args, outputs):
    output_files = []

    for output_key, file_name, target_dir_key in build_output_specs():
        target_dir = getattr(args, target_dir_key)
        output_files.append((file_name, outputs[output_key], target_dir / file_name))

    return output_files


def build_outputs(
    terms_data,
    events_data,
    dictionaries,
    taxonomy_crosswalk_seed,
    event_facet_seed,
    country_seed,
    region_seed,
    category_axis_seed,
):
    category_dictionary = dictionaries["category_dictionary"]
    event_category_dictionary = dictionaries["event_category_dictionary"]
    country_axis_config = get_category_axis_config(category_axis_seed, "country")
    economic_axis_config = get_category_axis_config(category_axis_seed, "economic_domain")
    event_facet_dictionary = build_event_facet_dictionary(
        event_category_dictionary,
        event_facet_seed,
    )
    country_dictionary = build_country_dictionary(category_dictionary, country_seed)
    region_dictionary = build_region_dictionary(category_dictionary, region_seed)
    economic_domain_dictionary = build_economic_domain_dictionary(
        category_dictionary,
        economic_axis_config,
    )
    typed_facet_exclusion_paths = build_typed_facet_exclusion_paths(
        country_dictionary,
        economic_domain_dictionary,
        region_dictionary,
    )
    taxonomy_facet_dictionary = build_taxonomy_facet_dictionary(
        category_dictionary,
        typed_facet_exclusion_paths,
    )

    return {
        "term_category_relation": build_term_category_relation(
            terms_data,
            category_dictionary,
        ),
        "event_category_relation": build_event_category_relation(
            events_data,
            event_category_dictionary,
        ),
        "taxonomy_crosswalk": build_taxonomy_crosswalk(
            event_category_dictionary,
            category_dictionary,
            taxonomy_crosswalk_seed,
        ),
        "event_facet_dictionary": event_facet_dictionary,
        "country_dictionary": country_dictionary,
        "region_dictionary": region_dictionary,
        "economic_domain_dictionary": economic_domain_dictionary,
        "taxonomy_facet_dictionary": taxonomy_facet_dictionary,
        "canonical_category_country_crosswalk": build_canonical_category_country_crosswalk(
            category_dictionary,
            country_dictionary,
            country_axis_config,
        ),
        "canonical_category_region_crosswalk": build_canonical_category_region_crosswalk(
            category_dictionary,
            region_dictionary,
        ),
        "canonical_category_economic_domain_crosswalk": build_canonical_category_economic_domain_crosswalk(
            category_dictionary,
            economic_domain_dictionary,
            economic_axis_config,
        ),
        "canonical_category_taxonomy_facet_crosswalk": build_canonical_category_taxonomy_facet_crosswalk(
            category_dictionary,
            taxonomy_facet_dictionary,
        ),
        "source_event_category_facet_crosswalk": build_source_event_category_facet_crosswalk(
            event_category_dictionary,
            event_facet_seed,
            event_facet_dictionary,
        ),
    }


def main():
    script_path = Path(__file__).resolve()
    default_paths = build_default_paths(script_path)
    args = parse_args(default_paths)

    terms_data = pd.read_csv(args.terms_path)
    events_data = pd.read_csv(args.events_path)
    dictionary_paths = build_dictionary_paths(args)
    dictionaries = read_dictionary_files(dictionary_paths)
    taxonomy_crosswalk_seed = read_taxonomy_crosswalk_seed(args.taxonomy_crosswalk_seed_path)
    event_facet_seed = read_event_facet_seed(args.event_facet_seed_path)
    country_seed = read_country_seed(args.country_seed_path)
    region_seed = read_region_seed(args.region_seed_path)
    category_axis_seed = read_category_axis_seed(args.category_axis_seed_path)

    outputs = build_outputs(
        terms_data,
        events_data,
        dictionaries,
        taxonomy_crosswalk_seed,
        event_facet_seed,
        country_seed,
        region_seed,
        category_axis_seed,
    )
    output_files = build_output_files(args, outputs)

    if args.save:
        for file_name, data_frame, output_path in output_files:
            save_csv(data_frame, output_path)
            print_summary(file_name, data_frame)

        print(f"dictionary_dir: {args.dictionary_dir}")
        print(f"staging_dir: {args.staging_dir}")
        print(f"mapping_dir: {args.mapping_dir}")

    if not args.save:
        for file_name, data_frame, output_path in output_files:
            print_summary(file_name, data_frame)
            print(f"planned_path: {output_path}")

        print("dry_run: no files saved. Use --save to write CSV files.")
        print(f"mapping_dir: {args.mapping_dir}")


if __name__ == "__main__":
    main()
