"""
정규화된 CSV에서 Neo4j 적재 전에 필요한 1차 사전과 검수 후보를 만든다.

기본 실행은 dry-run이다. CSV 저장이 필요할 때만 --save를 사용한다.
"""

import argparse
import re
from pathlib import Path

import pandas as pd


def parse_args(default_paths):
    parser = argparse.ArgumentParser(
        description="Neo4j용 1차 사전과 검수 후보 CSV를 만든다."
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
        "--event-relations-path",
        default=default_paths["event_relations"],
        type=Path,
        help="정규화된 event-person 관계 CSV 경로.",
    )
    parser.add_argument(
        "--person-relations-path",
        default=default_paths["person_relations"],
        type=Path,
        help="정규화된 person-person 관계 CSV 경로.",
    )
    parser.add_argument(
        "--dictionary-dir",
        default=default_paths["dictionary_dir"],
        type=Path,
        help="사전 CSV 저장 폴더.",
    )
    parser.add_argument(
        "--staging-dir",
        default=default_paths["staging_dir"],
        type=Path,
        help="staging CSV 저장 폴더.",
    )
    parser.add_argument(
        "--save",
        action="store_true",
        help="CSV 파일을 저장한다. 지정하지 않으면 dry-run으로 요약만 출력한다.",
    )
    return parser.parse_args()


def build_sequential_ids(prefix, row_count, width):
    return [f"{prefix}_{idx:0{width}d}" for idx in range(1, row_count + 1)]


def split_category_paths(term_lk):
    invalid_values = {"", "_NULL_", "NULL", "None", "nan"}

    if pd.isna(term_lk):
        return []

    # term_lk에서 ">>"는 복수 카테고리 경로, ">"는 경로 안의 depth를 뜻한다.
    # 예: A>B>>C>D는 해당 용어가 leaf B와 leaf D에 모두 속한다는 의미다.
    category_paths = []
    for raw_path in str(term_lk).split(">>"):
        path_parts = []

        for path_part in raw_path.split(">"):
            clean_path_part = path_part.strip()

            if clean_path_part not in invalid_values:
                path_parts.append(clean_path_part)

        if len(path_parts) > 0:
            category_paths.append(path_parts)

    return category_paths


def build_category_rows(terms_data):
    category_rows = []
    direct_relation_rows = []
    target_data = terms_data.dropna(subset=["term_lk"]).copy()

    if "term_kind" in target_data.columns:
        # term_kind=2가 실제 용어 행이다. 카테고리 행은 term_lk에서 다시 만든다.
        target_data = target_data[target_data["term_kind"].eq(2)].copy()

    for row in target_data[["term_id", "term_lk"]].itertuples(index=False):
        for path_parts in split_category_paths(row.term_lk):
            leaf_category_path = ">".join(path_parts)
            direct_relation_rows.append(
                {
                    "term_id": row.term_id,
                    "category_path": leaf_category_path,
                }
            )

            for path_index, category_name in enumerate(path_parts):
                depth = path_index + 1
                category_path = ">".join(path_parts[:depth])
                parent_category_path = pd.NA

                if depth > 1:
                    parent_category_path = ">".join(path_parts[: depth - 1])

                # Neo4j에서 카테고리 계층 관계를 만들 수 있도록 depth별 경로를 모두 저장한다.
                category_rows.append(
                    {
                        "term_id": row.term_id,
                        "category_name": category_name,
                        "category_path": category_path,
                        "parent_category_path": parent_category_path,
                        "depth": depth,
                        "root_category_name": path_parts[0],
                        "source": "history_terms.term_lk",
                    }
                )

    return category_rows, direct_relation_rows


def empty_category_dictionary():
    return pd.DataFrame(
        columns=[
            "category_id",
            "category_name",
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
    )


def build_direct_term_counts(direct_relation_rows):
    if len(direct_relation_rows) == 0:
        return pd.DataFrame(columns=["category_path", "direct_term_count"])

    return (
        pd.DataFrame(direct_relation_rows)
        .drop_duplicates(subset=["term_id", "category_path"])
        .groupby("category_path")["term_id"]
        .nunique()
        .reset_index(name="direct_term_count")
    )


def build_category_dictionary(terms_data):
    category_rows, direct_relation_rows = build_category_rows(terms_data)

    if len(category_rows) == 0:
        return empty_category_dictionary()

    category_base = (
        pd.DataFrame(category_rows)
        .drop_duplicates(subset=["category_path"])
        .sort_values(["depth", "category_path"])
        .reset_index(drop=True)
    )
    category_base.insert(
        0,
        "category_id",
        build_sequential_ids("CAT", len(category_base), 5),
    )

    # category_path를 안정적인 lookup key로 만든 뒤 parent_category_id를 채운다.
    path_to_id = dict(zip(category_base["category_path"], category_base["category_id"]))
    category_base["parent_category_id"] = category_base["parent_category_path"].map(path_to_id)

    term_counts = (
        pd.DataFrame(category_rows)
        .groupby("category_path")["term_id"]
        .nunique()
        .reset_index(name="term_count")
    )
    direct_counts = build_direct_term_counts(direct_relation_rows)

    category_dictionary = (
        category_base
        .merge(term_counts, on="category_path", how="left")
        .merge(direct_counts, on="category_path", how="left")
    )
    category_dictionary["direct_term_count"] = (
        category_dictionary["direct_term_count"].fillna(0).astype(int)
    )
    category_dictionary["review_status"] = "PENDING"

    return category_dictionary[
        [
            "category_id",
            "category_name",
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


def split_event_category_tokens(subject_category):
    if pd.isna(subject_category):
        return []

    # event의 subject_category는 현재 계층이 아니라 평면 라벨 묶음으로 본다.
    cleaned_text = str(subject_category).replace("\r", "\n")
    raw_tokens = re.split(r",|\n+", cleaned_text)
    tokens = []

    for raw_token in raw_tokens:
        clean_token = raw_token.strip()

        if clean_token != "":
            tokens.append(clean_token)

    return tokens


def build_event_category_rows(events_data):
    category_rows = []
    target_data = events_data.dropna(subset=["subject_category"]).copy()

    for row in target_data[["event_id", "subject_category"]].itertuples(index=False):
        for token in split_event_category_tokens(row.subject_category):
            category_rows.append(
                {
                    "event_id": row.event_id,
                    "event_category_name": token,
                    "source": "itkc_events.subject_category",
                }
            )

    return category_rows


def build_event_category_dictionary(events_data):
    category_rows = build_event_category_rows(events_data)

    if len(category_rows) == 0:
        return pd.DataFrame(
            columns=[
                "event_category_id",
                "event_category_name",
                "event_count",
                "source",
                "review_status",
            ]
        )

    event_counts = (
        pd.DataFrame(category_rows)
        .groupby(["event_category_name", "source"])["event_id"]
        .nunique()
        .reset_index(name="event_count")
        .sort_values(["event_category_name"])
        .reset_index(drop=True)
    )
    event_counts.insert(
        0,
        "event_category_id",
        build_sequential_ids("EVENT_CAT", len(event_counts), 5),
    )
    event_counts["review_status"] = "PENDING"

    return event_counts[
        [
            "event_category_id",
            "event_category_name",
            "event_count",
            "source",
            "review_status",
        ]
    ]


def split_period_tokens(period_text):
    if pd.isna(period_text):
        return []

    # 여기서는 시대명만 분리한다. 숫자 연도 범위는 event_date_parse에서 처리한다.
    raw_tokens = re.split(r"-|,|~", str(period_text))
    tokens = []

    for raw_token in raw_tokens:
        clean_token = raw_token.strip()

        if clean_token != "":
            tokens.append(clean_token)

    return tokens


def collect_period_rows(terms_data, events_data):
    period_rows = []

    if "term_times" in terms_data.columns:
        for row in terms_data[["term_id", "term_times"]].dropna().itertuples(index=False):
            for token in split_period_tokens(row.term_times):
                period_rows.append(
                    {
                        "period_name": token,
                        "term_id": row.term_id,
                        "event_id": pd.NA,
                        "source": "terms.term_times",
                        "source_value": row.term_times,
                    }
                )

    if "period" in events_data.columns:
        for row in events_data[["event_id", "period"]].dropna().itertuples(index=False):
            for token in split_period_tokens(row.period):
                period_rows.append(
                    {
                        "period_name": token,
                        "term_id": pd.NA,
                        "event_id": row.event_id,
                        "source": "events.period",
                        "source_value": row.period,
                    }
                )

    return period_rows


def build_period_dictionary(terms_data, events_data):
    period_rows = collect_period_rows(terms_data, events_data)

    if len(period_rows) == 0:
        return pd.DataFrame(
            columns=[
                "period_id",
                "period_name",
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
        )

    period_frame = pd.DataFrame(period_rows)
    period_dictionary = (
        period_frame
        .groupby("period_name", dropna=False)
        .agg(
            term_count=("term_id", lambda values: values.dropna().nunique()),
            event_count=("event_id", lambda values: values.dropna().nunique()),
            source=("source", lambda values: "|".join(sorted(values.dropna().unique()))),
            source_values=(
                "source_value",
                lambda values: "|".join(sorted(values.dropna().astype(str).unique())),
            ),
        )
        .reset_index()
        .sort_values("period_name")
        .reset_index(drop=True)
    )
    period_dictionary.insert(
        0,
        "period_id",
        build_sequential_ids("PERIOD", len(period_dictionary), 5),
    )
    period_dictionary["period_level"] = "UNCLASSIFIED"
    period_dictionary["start_year"] = pd.NA
    period_dictionary["end_year"] = pd.NA
    period_dictionary["review_status"] = "PENDING"
    period_dictionary["note"] = "year range requires manual dictionary enrichment"

    return period_dictionary[
        [
            "period_id",
            "period_name",
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


def parse_date_part(date_part):
    # 날짜 파서는 보수적으로 둔다. 애매한 한국사 날짜 표현은 검수 대상으로 남긴다.
    year_match = re.search(r"(\d{3,4})년", date_part)
    month_match = re.search(r"(\d{1,2})월", date_part)
    reign_match = re.search(r"\(([가-힣A-Za-z·]+)\s*(\d+)", date_part)

    parsed_year = pd.NA
    parsed_month = pd.NA
    parsed_reign_name = pd.NA
    parsed_reign_year = pd.NA

    if year_match:
        parsed_year = int(year_match.group(1))

    if month_match:
        parsed_month = int(month_match.group(1))

    if reign_match:
        parsed_reign_name = reign_match.group(1)
        parsed_reign_year = int(reign_match.group(2))

    return {
        "year": parsed_year,
        "month": parsed_month,
        "reign_name": parsed_reign_name,
        "reign_year": parsed_reign_year,
    }


def determine_date_precision(start_part, end_part, has_range):
    has_start_year = pd.notna(start_part["year"])
    has_end_year = pd.notna(end_part["year"])
    has_start_month = pd.notna(start_part["month"])
    has_end_month = pd.notna(end_part["month"])

    if has_range and has_start_year and has_end_year and has_start_month and has_end_month:
        return "YEAR_MONTH_RANGE"

    if has_range and has_start_year and has_end_year:
        return "YEAR_RANGE"

    if has_start_year and has_start_month:
        return "YEAR_MONTH"

    if has_start_year:
        return "EXACT_YEAR"

    return "UNKNOWN"


def determine_parse_status(start_part):
    if pd.notna(start_part["year"]):
        return "PARSED"

    return "FAILED"


def parse_event_date(event_id, date_text):
    clean_text = ""

    if pd.notna(date_text):
        clean_text = str(date_text).replace("\r", "\n").strip()

    # 물결표는 event_date에 시작 표현과 종료 표현이 함께 있다는 뜻이다.
    date_parts = [part.strip() for part in re.split(r"\s*~\s*", clean_text) if part.strip()]
    has_range = len(date_parts) > 1
    start_text = clean_text
    end_text = clean_text

    if len(date_parts) > 0:
        start_text = date_parts[0]
        end_text = date_parts[-1]

    start_part = parse_date_part(start_text)
    end_part = parse_date_part(end_text)
    date_precision = determine_date_precision(start_part, end_part, has_range)
    parse_status = determine_parse_status(start_part)

    return {
        "event_id": event_id,
        "date_text": date_text,
        "start_year": start_part["year"],
        "end_year": end_part["year"],
        "start_month": start_part["month"],
        "end_month": end_part["month"],
        "start_reign_name": start_part["reign_name"],
        "start_reign_year": start_part["reign_year"],
        "end_reign_name": end_part["reign_name"],
        "end_reign_year": end_part["reign_year"],
        "date_precision": date_precision,
        "parse_status": parse_status,
    }


def build_event_date_parse(events_data):
    event_date_rows = []

    for row in events_data[["event_id", "event_date"]].itertuples(index=False):
        event_date_rows.append(parse_event_date(row.event_id, row.event_date))

    return pd.DataFrame(event_date_rows)


def build_relation_type_rules():
    # 이 부분은 도메인 seed 데이터라서 수동 매핑이 일부 필요하다.
    # 규칙이 안정되기 전까지는 한 함수에 모아두고, 안정되면 seed CSV로 분리한다.
    rule_rows = [
        {
            "raw_relation_type": "부",
            "normalized_relation_type": "HAS_FATHER",
            "neo4j_rel_type": "RELATED_TO",
            "relation_group": "FAMILY_PARENT",
            "direction_rule": "person_to_related",
            "is_symmetric": "N",
            "inverse_relation_type": "HAS_CHILD",
            "review_status": "PENDING",
            "note": "",
        },
        {
            "raw_relation_type": "생부",
            "normalized_relation_type": "HAS_BIOLOGICAL_FATHER",
            "neo4j_rel_type": "RELATED_TO",
            "relation_group": "FAMILY_PARENT",
            "direction_rule": "person_to_related",
            "is_symmetric": "N",
            "inverse_relation_type": "HAS_BIOLOGICAL_CHILD",
            "review_status": "PENDING",
            "note": "",
        },
        {
            "raw_relation_type": "모",
            "normalized_relation_type": "HAS_MOTHER",
            "neo4j_rel_type": "RELATED_TO",
            "relation_group": "FAMILY_PARENT",
            "direction_rule": "person_to_related",
            "is_symmetric": "N",
            "inverse_relation_type": "HAS_CHILD",
            "review_status": "PENDING",
            "note": "",
        },
        {
            "raw_relation_type": "생모",
            "normalized_relation_type": "HAS_BIOLOGICAL_MOTHER",
            "neo4j_rel_type": "RELATED_TO",
            "relation_group": "FAMILY_PARENT",
            "direction_rule": "person_to_related",
            "is_symmetric": "N",
            "inverse_relation_type": "HAS_BIOLOGICAL_CHILD",
            "review_status": "PENDING",
            "note": "",
        },
        {
            "raw_relation_type": "자",
            "normalized_relation_type": "HAS_CHILD",
            "neo4j_rel_type": "RELATED_TO",
            "relation_group": "FAMILY_CHILD",
            "direction_rule": "person_to_related",
            "is_symmetric": "N",
            "inverse_relation_type": "HAS_PARENT",
            "review_status": "PENDING",
            "note": "",
        },
        {
            "raw_relation_type": "조부",
            "normalized_relation_type": "HAS_GRANDFATHER",
            "neo4j_rel_type": "RELATED_TO",
            "relation_group": "FAMILY_ANCESTOR",
            "direction_rule": "person_to_related",
            "is_symmetric": "N",
            "inverse_relation_type": "HAS_GRANDCHILD",
            "review_status": "PENDING",
            "note": "",
        },
        {
            "raw_relation_type": "증조부",
            "normalized_relation_type": "HAS_GREAT_GRANDFATHER",
            "neo4j_rel_type": "RELATED_TO",
            "relation_group": "FAMILY_ANCESTOR",
            "direction_rule": "person_to_related",
            "is_symmetric": "N",
            "inverse_relation_type": "HAS_GREAT_GRANDCHILD",
            "review_status": "PENDING",
            "note": "",
        },
        {
            "raw_relation_type": "형제",
            "normalized_relation_type": "SIBLING_OF",
            "neo4j_rel_type": "RELATED_TO",
            "relation_group": "FAMILY_SIBLING",
            "direction_rule": "undirected",
            "is_symmetric": "Y",
            "inverse_relation_type": "SIBLING_OF",
            "review_status": "PENDING",
            "note": "symmetric relation; query both directions",
        },
        {
            "raw_relation_type": "아내",
            "normalized_relation_type": "HAS_WIFE",
            "neo4j_rel_type": "RELATED_TO",
            "relation_group": "SPOUSE",
            "direction_rule": "person_to_related",
            "is_symmetric": "N",
            "inverse_relation_type": "HAS_HUSBAND",
            "review_status": "PENDING",
            "note": "",
        },
        {
            "raw_relation_type": "남편",
            "normalized_relation_type": "HAS_HUSBAND",
            "neo4j_rel_type": "RELATED_TO",
            "relation_group": "SPOUSE",
            "direction_rule": "person_to_related",
            "is_symmetric": "N",
            "inverse_relation_type": "HAS_WIFE",
            "review_status": "PENDING",
            "note": "",
        },
        {
            "raw_relation_type": "장인",
            "normalized_relation_type": "HAS_FATHER_IN_LAW",
            "neo4j_rel_type": "RELATED_TO",
            "relation_group": "FAMILY_IN_LAW",
            "direction_rule": "person_to_related",
            "is_symmetric": "N",
            "inverse_relation_type": "HAS_SON_IN_LAW",
            "review_status": "PENDING",
            "note": "",
        },
        {
            "raw_relation_type": "사위",
            "normalized_relation_type": "HAS_SON_IN_LAW",
            "neo4j_rel_type": "RELATED_TO",
            "relation_group": "FAMILY_IN_LAW",
            "direction_rule": "person_to_related",
            "is_symmetric": "N",
            "inverse_relation_type": "HAS_FATHER_IN_LAW",
            "review_status": "PENDING",
            "note": "",
        },
        {
            "raw_relation_type": "교유",
            "normalized_relation_type": "ASSOCIATED_WITH",
            "neo4j_rel_type": "RELATED_TO",
            "relation_group": "SOCIAL",
            "direction_rule": "undirected",
            "is_symmetric": "Y",
            "inverse_relation_type": "ASSOCIATED_WITH",
            "review_status": "PENDING",
            "note": "social symmetric relation",
        },
        {
            "raw_relation_type": "스승",
            "normalized_relation_type": "HAS_TEACHER",
            "neo4j_rel_type": "RELATED_TO",
            "relation_group": "SOCIAL_TEACHER",
            "direction_rule": "person_to_related",
            "is_symmetric": "N",
            "inverse_relation_type": "HAS_STUDENT",
            "review_status": "PENDING",
            "note": "",
        },
        {
            "raw_relation_type": "제자",
            "normalized_relation_type": "HAS_STUDENT",
            "neo4j_rel_type": "RELATED_TO",
            "relation_group": "SOCIAL_STUDENT",
            "direction_rule": "person_to_related",
            "is_symmetric": "N",
            "inverse_relation_type": "HAS_TEACHER",
            "review_status": "PENDING",
            "note": "",
        },
        {
            "raw_relation_type": "출자",
            "normalized_relation_type": "LINEAGE_RELATED",
            "neo4j_rel_type": "RELATED_TO",
            "relation_group": "FAMILY_LINEAGE",
            "direction_rule": "person_to_related",
            "is_symmetric": "N",
            "inverse_relation_type": "LINEAGE_RELATED",
            "review_status": "REVIEW",
            "note": "meaning requires historical relation review",
        },
    ]

    return pd.DataFrame(rule_rows)


def build_missing_relation_type_defaults():
    return {
        "normalized_relation_type": "RELATED_TO",
        "neo4j_rel_type": "RELATED_TO",
        "relation_group": "UNKNOWN",
        "direction_rule": "person_to_related",
        "is_symmetric": "N",
        "inverse_relation_type": "UNKNOWN",
        "review_status": "REVIEW",
        "note": "new relation type requires mapping",
    }


def build_relation_type_dictionary(person_relations_data):
    relation_counts = (
        person_relations_data["relation_type"]
        .value_counts(dropna=False)
        .rename_axis("raw_relation_type")
        .reset_index(name="relation_count")
    )
    relation_rules = build_relation_type_rules()
    relation_type_dictionary = relation_counts.merge(
        relation_rules,
        on="raw_relation_type",
        how="left",
    )

    missing_rule_mask = relation_type_dictionary["normalized_relation_type"].isna()

    for column_name, default_value in build_missing_relation_type_defaults().items():
        relation_type_dictionary.loc[missing_rule_mask, column_name] = default_value

    relation_type_dictionary = relation_type_dictionary.sort_values(
        ["relation_group", "raw_relation_type"]
    ).reset_index(drop=True)
    relation_type_dictionary.insert(
        0,
        "relation_type_id",
        build_sequential_ids("RELTYPE", len(relation_type_dictionary), 5),
    )
    relation_type_dictionary["source"] = "person_relations.relation_type"

    return relation_type_dictionary[
        [
            "relation_type_id",
            "raw_relation_type",
            "normalized_relation_type",
            "neo4j_rel_type",
            "relation_group",
            "direction_rule",
            "is_symmetric",
            "inverse_relation_type",
            "relation_count",
            "source",
            "review_status",
            "note",
        ]
    ]


def split_source_urls(url_value):
    if pd.isna(url_value):
        return []

    urls = []
    for raw_url in str(url_value).split("|"):
        clean_url = raw_url.strip()

        if clean_url != "":
            urls.append(clean_url)

    return urls


def collect_source_url_rows(data_frame, source_table, source_column, source_type):
    url_rows = []

    if source_column not in data_frame.columns:
        return url_rows

    for row_index, url_value in data_frame[source_column].items():
        for url in split_source_urls(url_value):
            url_rows.append(
                {
                    "url": url,
                    "source_table": source_table,
                    "source_column": source_column,
                    "source_type": source_type,
                    "source_row_index": row_index,
                }
            )

    return url_rows


def build_source_url_dictionary(events_data, event_relations_data, person_relations_data):
    url_rows = []

    # URL 수집 대상은 정규화 CSV 컬럼명에 의존하므로 여기에서 명세한다.
    source_specs = [
        (events_data, "events", "source_urls", "EVENT_DETAIL"),
        (event_relations_data, "event_relations", "source_urls", "EVENT_RELATION_DETAIL"),
        (person_relations_data, "person_relations", "evidence_url", "PERSON_RELATION_EVIDENCE"),
        (person_relations_data, "person_relations", "detail_url", "PERSON_DETAIL"),
    ]

    for data_frame, source_table, source_column, source_type in source_specs:
        url_rows.extend(
            collect_source_url_rows(data_frame, source_table, source_column, source_type)
        )

    if len(url_rows) == 0:
        return pd.DataFrame(
            columns=[
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
        )

    url_frame = pd.DataFrame(url_rows)
    source_url_dictionary = (
        url_frame
        .groupby("url", dropna=False)
        .agg(
            source_tables=("source_table", lambda values: "|".join(sorted(values.unique()))),
            source_columns=("source_column", lambda values: "|".join(sorted(values.unique()))),
            source_types=("source_type", lambda values: "|".join(sorted(values.unique()))),
            source_count=("source_row_index", "count"),
        )
        .reset_index()
        .sort_values("url")
        .reset_index(drop=True)
    )
    source_url_dictionary.insert(
        0,
        "source_url_id",
        build_sequential_ids("URL", len(source_url_dictionary), 6),
    )
    source_url_dictionary["use_for_rag"] = "Y"
    source_url_dictionary["fetch_status"] = "PENDING"
    source_url_dictionary["note"] = ""

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
    ]


def save_csv(data_frame, output_path):
    output_path.parent.mkdir(parents=True, exist_ok=True)
    data_frame.to_csv(output_path, index=False, encoding="utf-8-sig")


def print_summary(file_name, data_frame):
    print(f"{file_name}: {len(data_frame)} rows, {len(data_frame.columns)} columns")


def build_default_paths(script_path):
    normalized_dir = script_path.parent / "normalized"

    return {
        "terms": normalized_dir / "terms.csv",
        "events": normalized_dir / "events.csv",
        "event_relations": normalized_dir / "event_relations.csv",
        "person_relations": normalized_dir / "person_relations.csv",
        "dictionary_dir": script_path.parent / "dictionary",
        "staging_dir": script_path.parent / "staging",
    }


def build_output_specs():
    # target_dir_key로 사전 산출물인지 staging 산출물인지 구분한다.
    return [
        ("category_dictionary", "category_dictionary.csv", "dictionary_dir"),
        ("event_category_dictionary", "event_category_dictionary.csv", "dictionary_dir"),
        ("period_dictionary", "period_dictionary.csv", "dictionary_dir"),
        ("relation_type_dictionary", "relation_type_dictionary.csv", "dictionary_dir"),
        ("source_url_dictionary", "source_url_dictionary.csv", "dictionary_dir"),
        ("event_date_parse", "event_date_parse.csv", "staging_dir"),
    ]


def build_output_files(args, outputs):
    output_files = []

    for output_key, file_name, target_dir_key in build_output_specs():
        target_dir = getattr(args, target_dir_key)
        output_files.append((file_name, outputs[output_key], target_dir / file_name))

    return output_files


def build_outputs(terms_data, events_data, event_relations_data, person_relations_data):
    return {
        "category_dictionary": build_category_dictionary(terms_data),
        "event_category_dictionary": build_event_category_dictionary(events_data),
        "period_dictionary": build_period_dictionary(terms_data, events_data),
        "relation_type_dictionary": build_relation_type_dictionary(person_relations_data),
        "source_url_dictionary": build_source_url_dictionary(
            events_data,
            event_relations_data,
            person_relations_data,
        ),
        "event_date_parse": build_event_date_parse(events_data),
    }


def main():
    script_path = Path(__file__).resolve()
    default_paths = build_default_paths(script_path)
    args = parse_args(default_paths)

    terms_data = pd.read_csv(args.terms_path)
    events_data = pd.read_csv(args.events_path)
    event_relations_data = pd.read_csv(args.event_relations_path)
    person_relations_data = pd.read_csv(args.person_relations_path)

    outputs = build_outputs(
        terms_data,
        events_data,
        event_relations_data,
        person_relations_data,
    )
    output_files = build_output_files(args, outputs)

    if args.save:
        for file_name, data_frame, output_path in output_files:
            save_csv(data_frame, output_path)
            print_summary(file_name, data_frame)

        print(f"dictionary_dir: {args.dictionary_dir}")
        print(f"staging_dir: {args.staging_dir}")

    if not args.save:
        for file_name, data_frame, output_path in output_files:
            print_summary(file_name, data_frame)
            print(f"planned_path: {output_path}")

        print("dry_run: no files saved. Use --save to write CSV files.")


if __name__ == "__main__":
    main()
