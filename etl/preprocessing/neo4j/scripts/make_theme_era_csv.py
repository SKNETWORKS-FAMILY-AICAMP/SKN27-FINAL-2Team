"""주제(Theme)/시대(Era)/실체유형(EntityType) 상위 레이어 CSV 생성.

make_graph_csv.py가 만든 최종 graph CSV와 seed 규칙을 읽어
문제 생성 서비스가 사용하는 상위 레이어 노드/관계 CSV를 만든다.

- Theme: 서비스 고정 주제 10개(사건/인물/정치/제도/문화/사회/군사/경제/사상·종교/외교).
  원천 매핑은 CanonicalCategory -> Theme(seed)이고,
  서비스 쿼리 편의를 위해 Term/Event -> Theme 직통 엣지를 미리 펼쳐 저장한다.
- Era: 표준 시대 10개. 원천 매핑은 Period -> Era(seed)이고,
  Term/Event -> Era 직통 엣지를 미리 펼쳐 저장한다.
  키워드 override(seed)로 고조선/초기 국가처럼 원본 시대 표기가 없는 시대를 보강한다.
- EntityType: 인물/문헌/문화재/장소. 실체 유형 카테고리(인명/서명/문화재/지명)의
  용어를 유형 축으로 연결한다.
"""

from pathlib import Path
import argparse

import pandas as pd

from neo4j_common import (
    read_csv,
    save_csv,
    print_summary,
    resolve_project_root,
    unique_join,
)


def build_default_paths(script_path):
    neo4j_dir = script_path.parents[1]
    project_root = resolve_project_root(script_path)
    import_dir = project_root / "storage" / "neo4j" / "neo4j_import"

    return {
        "theme_seed": neo4j_dir / "seed" / "theme_seed.csv",
        "category_theme_seed": neo4j_dir / "seed" / "category_theme_seed.csv",
        "era_seed": neo4j_dir / "seed" / "era_seed.csv",
        "period_era_seed": neo4j_dir / "seed" / "period_era_seed.csv",
        "entity_type_seed": neo4j_dir / "seed" / "entity_type_seed.csv",
        "keyword_era_seed": neo4j_dir / "seed" / "keyword_era_seed.csv",
        "term_era_candidate": neo4j_dir / "staging" / "term_era_candidate.csv",
        "canonical_categories": import_dir / "nodes" / "canonical_categories.csv",
        "terms": import_dir / "nodes" / "terms.csv",
        "events": import_dir / "nodes" / "events.csv",
        "people": import_dir / "nodes" / "people.csv",
        "periods": import_dir / "nodes" / "periods.csv",
        "source_urls": import_dir / "nodes" / "source_urls.csv",
        "person_involved_in_event": (
            import_dir / "relations" / "person_involved_in_event.csv"
        ),
        "term_refers_to_person": (
            import_dir / "relations" / "term_refers_to_person.csv"
        ),
        "person_relations": neo4j_dir / "normalized" / "person_relations.csv",
        "term_has_canonical_category": (
            import_dir / "relations" / "term_has_canonical_category.csv"
        ),
        "event_has_canonical_category": (
            import_dir / "relations" / "event_has_canonical_category.csv"
        ),
        "term_in_period": import_dir / "relations" / "term_in_period.csv",
        "event_in_period": import_dir / "relations" / "event_in_period.csv",
        "nodes_dir": import_dir / "nodes",
        "relations_dir": import_dir / "relations",
    }


def parse_args(default_paths):
    parser = argparse.ArgumentParser(
        description="Theme/Era/EntityType 상위 레이어 node/relation CSV를 생성한다."
    )
    parser.add_argument("--theme-seed-path", type=Path, default=default_paths["theme_seed"])
    parser.add_argument(
        "--category-theme-seed-path",
        type=Path,
        default=default_paths["category_theme_seed"],
    )
    parser.add_argument("--era-seed-path", type=Path, default=default_paths["era_seed"])
    parser.add_argument(
        "--period-era-seed-path",
        type=Path,
        default=default_paths["period_era_seed"],
    )
    parser.add_argument(
        "--entity-type-seed-path",
        type=Path,
        default=default_paths["entity_type_seed"],
    )
    parser.add_argument(
        "--keyword-era-seed-path",
        type=Path,
        default=default_paths["keyword_era_seed"],
    )
    parser.add_argument(
        "--term-era-candidate-path",
        type=Path,
        default=default_paths["term_era_candidate"],
    )
    parser.add_argument(
        "--canonical-categories-path",
        type=Path,
        default=default_paths["canonical_categories"],
    )
    parser.add_argument("--terms-path", type=Path, default=default_paths["terms"])
    parser.add_argument("--events-path", type=Path, default=default_paths["events"])
    parser.add_argument("--people-path", type=Path, default=default_paths["people"])
    parser.add_argument("--periods-path", type=Path, default=default_paths["periods"])
    parser.add_argument(
        "--source-urls-path", type=Path, default=default_paths["source_urls"]
    )
    parser.add_argument(
        "--person-involved-in-event-path",
        type=Path,
        default=default_paths["person_involved_in_event"],
    )
    parser.add_argument(
        "--term-refers-to-person-path",
        type=Path,
        default=default_paths["term_refers_to_person"],
    )
    parser.add_argument(
        "--person-relations-path",
        type=Path,
        default=default_paths["person_relations"],
    )
    parser.add_argument(
        "--term-has-canonical-category-path",
        type=Path,
        default=default_paths["term_has_canonical_category"],
    )
    parser.add_argument(
        "--event-has-canonical-category-path",
        type=Path,
        default=default_paths["event_has_canonical_category"],
    )
    parser.add_argument(
        "--term-in-period-path",
        type=Path,
        default=default_paths["term_in_period"],
    )
    parser.add_argument(
        "--event-in-period-path",
        type=Path,
        default=default_paths["event_in_period"],
    )
    parser.add_argument("--nodes-dir", type=Path, default=default_paths["nodes_dir"])
    parser.add_argument(
        "--relations-dir", type=Path, default=default_paths["relations_dir"]
    )
    parser.add_argument(
        "--save",
        action="store_true",
        help="CSV 파일을 실제로 저장한다. 없으면 dry run으로 요약만 출력한다.",
    )

    return parser.parse_args()


def read_optional_csv(input_path, purpose):
    # 검수 후보 파일처럼 아직 없을 수 있는 입력은 빈 DataFrame으로 대체한다.
    if input_path.exists():
        return read_csv(input_path, purpose)

    return pd.DataFrame()


def read_inputs(args):
    return {
        "theme_seed": read_csv(args.theme_seed_path, "theme_seed"),
        "category_theme_seed": read_csv(
            args.category_theme_seed_path, "category_theme_seed"
        ),
        "era_seed": read_csv(args.era_seed_path, "era_seed"),
        "period_era_seed": read_csv(args.period_era_seed_path, "period_era_seed"),
        "entity_type_seed": read_csv(args.entity_type_seed_path, "entity_type_seed"),
        "keyword_era_seed": read_csv(args.keyword_era_seed_path, "keyword_era_seed"),
        "term_era_candidate": read_optional_csv(
            args.term_era_candidate_path, "term_era_candidate"
        ),
        "canonical_categories": read_csv(
            args.canonical_categories_path, "canonical_categories"
        ),
        "terms": read_csv(args.terms_path, "terms"),
        "events": read_csv(args.events_path, "events"),
        "people": read_csv(args.people_path, "people"),
        "periods": read_csv(args.periods_path, "periods"),
        "source_urls": read_csv(args.source_urls_path, "source_urls"),
        "person_involved_in_event": read_csv(
            args.person_involved_in_event_path, "person_involved_in_event"
        ),
        "term_refers_to_person": read_csv(
            args.term_refers_to_person_path, "term_refers_to_person"
        ),
        "person_relations": read_csv(
            args.person_relations_path, "person_relations"
        ),
        "term_has_canonical_category": read_csv(
            args.term_has_canonical_category_path, "term_has_canonical_category"
        ),
        "event_has_canonical_category": read_csv(
            args.event_has_canonical_category_path, "event_has_canonical_category"
        ),
        "term_in_period": read_csv(args.term_in_period_path, "term_in_period"),
        "event_in_period": read_csv(args.event_in_period_path, "event_in_period"),
    }


def require_columns(data_frame, column_names, purpose):
    missing_columns = [column for column in column_names if column not in data_frame.columns]

    if len(missing_columns) > 0:
        missing_text = ", ".join(missing_columns)
        raise ValueError(f"{purpose} missing required columns: {missing_text}")


def validate_unique_columns(data_frame, column_names, purpose):
    for column_name in column_names:
        duplicate_count = data_frame[column_name].duplicated().sum()

        if duplicate_count > 0:
            raise ValueError(
                f"{purpose} has duplicated {column_name}: {duplicate_count}"
            )


def build_theme_nodes(theme_seed):
    theme_nodes = theme_seed.copy()
    require_columns(
        theme_nodes,
        ["theme_id", "theme_name", "theme_order", "note"],
        "theme_seed",
    )
    validate_unique_columns(theme_nodes, ["theme_id", "theme_name"], "theme_seed")
    theme_nodes = theme_nodes.rename(columns={"theme_name": "name"})

    return theme_nodes[["theme_id", "name", "theme_order", "note"]]


def build_theme_id_lookup(theme_nodes):
    return dict(zip(theme_nodes["name"], theme_nodes["theme_id"]))


def build_theme_path_rules(category_theme_seed, theme_nodes):
    # seed의 category_path를 prefix 규칙으로 사용한다. 더 긴 prefix가 우선한다.
    theme_id_lookup = build_theme_id_lookup(theme_nodes)
    seed_rows = category_theme_seed.dropna(subset=["category_path", "theme_name"])

    rules = []
    for seed_row in seed_rows.itertuples(index=False):
        rules.append(
            (
                seed_row.category_path.strip(),
                seed_row.theme_name.strip(),
                theme_id_lookup[seed_row.theme_name.strip()],
            )
        )

    return sorted(rules, key=lambda rule: len(rule[0]), reverse=True)


def match_themes_by_category_path(category_path, theme_path_rules):
    matched_themes = []

    for seed_path, theme_name, theme_id in theme_path_rules:
        if category_path == seed_path or category_path.startswith(seed_path + ">"):
            matched_themes.append({"theme_name": theme_name, "theme_id": theme_id})

    return matched_themes


def build_category_path_theme_lookup(category_paths, theme_path_rules):
    lookup = {}

    for category_path in category_paths:
        matched_themes = match_themes_by_category_path(category_path, theme_path_rules)

        if len(matched_themes) > 0:
            lookup[category_path] = matched_themes

    return lookup


def expand_theme_relations_by_category_path(relation_data, id_column, path_theme_lookup):
    relation_rows = []

    for row in relation_data[[id_column, "category_path"]].to_dict("records"):
        matched_themes = path_theme_lookup.get(row["category_path"], [])

        for matched_theme in matched_themes:
            relation_rows.append(
                {
                    id_column: row[id_column],
                    "theme_name": matched_theme["theme_name"],
                    "end_theme_id": matched_theme["theme_id"],
                }
            )

    if len(relation_rows) == 0:
        return pd.DataFrame(columns=[id_column, "theme_name", "end_theme_id"])

    return pd.DataFrame(relation_rows)


def build_category_theme_relations(canonical_categories, category_theme_seed, theme_nodes):
    # 원천 매핑: seed의 category_path와 정확히 일치하는 카테고리 노드만 연결한다.
    theme_id_lookup = build_theme_id_lookup(theme_nodes)
    relation_data = canonical_categories.merge(
        category_theme_seed,
        on="category_path",
        how="inner",
    )
    relation_data["end_theme_id"] = relation_data["theme_name"].map(theme_id_lookup)
    relation_data["relation_type"] = "HAS_THEME"
    relation_data = relation_data.rename(columns={"category_id": "start_category_id"})

    return relation_data[
        ["start_category_id", "end_theme_id", "relation_type", "category_path", "theme_name"]
    ]


def build_term_theme_relations(term_has_canonical_category, category_theme_seed, theme_nodes):
    # 직통 엣지: 용어의 카테고리 경로를 seed prefix와 매칭해 Term -> Theme으로 펼친다.
    theme_path_rules = build_theme_path_rules(category_theme_seed, theme_nodes)
    relation_data = term_has_canonical_category[
        ["start_term_id", "category_path"]
    ].copy()
    relation_data["category_path"] = relation_data["category_path"].fillna("").str.strip()

    unique_paths = relation_data["category_path"].unique()
    path_theme_lookup = build_category_path_theme_lookup(unique_paths, theme_path_rules)

    relation_data = expand_theme_relations_by_category_path(
        relation_data,
        "start_term_id",
        path_theme_lookup,
    )
    relation_data = relation_data.dropna(subset=["end_theme_id"])
    relation_data["relation_type"] = "HAS_THEME"
    relation_data["match_source"] = "CATEGORY"
    relation_data = relation_data.drop_duplicates(
        subset=["start_term_id", "end_theme_id"]
    )

    return relation_data[
        ["start_term_id", "end_theme_id", "relation_type", "theme_name", "match_source"]
    ]


def build_event_theme_relations(
    events, event_has_canonical_category, canonical_categories, category_theme_seed, theme_nodes
):
    # 직통 엣지: 모든 Event는 '사건' 주제에 연결하고,
    # 표준 카테고리 매핑이 있는 Event는 해당 주제에도 연결한다.
    theme_id_lookup = build_theme_id_lookup(theme_nodes)
    theme_path_rules = build_theme_path_rules(category_theme_seed, theme_nodes)

    event_theme_rows = events[["event_id"]].copy()
    event_theme_rows = event_theme_rows.rename(columns={"event_id": "start_event_id"})
    event_theme_rows["theme_name"] = "사건"
    event_theme_rows["end_theme_id"] = theme_id_lookup["사건"]
    event_theme_rows["match_source"] = "EVENT_LABEL"

    category_rows = event_has_canonical_category.merge(
        canonical_categories[["category_id", "category_path"]],
        left_on="end_category_id",
        right_on="category_id",
        how="left",
    )
    category_rows["category_path"] = category_rows["category_path"].fillna("").str.strip()
    unique_paths = category_rows["category_path"].unique()
    path_theme_lookup = build_category_path_theme_lookup(unique_paths, theme_path_rules)

    category_rows = expand_theme_relations_by_category_path(
        category_rows,
        "start_event_id",
        path_theme_lookup,
    )
    category_rows = category_rows.dropna(subset=["end_theme_id"])
    category_rows["match_source"] = "CATEGORY"
    category_rows = category_rows[
        ["start_event_id", "theme_name", "end_theme_id", "match_source"]
    ]

    relation_data = pd.concat([event_theme_rows, category_rows], ignore_index=True)
    relation_data["relation_type"] = "HAS_THEME"
    relation_data = relation_data.drop_duplicates(
        subset=["start_event_id", "end_theme_id"]
    )

    return relation_data[
        ["start_event_id", "end_theme_id", "relation_type", "theme_name", "match_source"]
    ]


def build_person_theme_relation_columns():
    return [
        "start_person_id",
        "end_theme_id",
        "relation_type",
        "theme_name",
        "match_source",
        "source_detail",
    ]


def empty_person_theme_relations():
    return pd.DataFrame(columns=build_person_theme_relation_columns())


def build_non_inherited_theme_names():
    return {"사건", "인물"}


def build_person_theme_rows_from_label(people, theme_nodes):
    theme_id_lookup = build_theme_id_lookup(theme_nodes)
    person_theme_id = theme_id_lookup.get("인물")

    if person_theme_id is None:
        return empty_person_theme_relations()

    relation_data = people[["person_id"]].copy()
    relation_data = relation_data.rename(columns={"person_id": "start_person_id"})
    relation_data["end_theme_id"] = person_theme_id
    relation_data["relation_type"] = "HAS_THEME"
    relation_data["theme_name"] = "인물"
    relation_data["match_source"] = "PERSON_LABEL"
    relation_data["source_detail"] = "Person"

    return relation_data[build_person_theme_relation_columns()]


def build_person_theme_rows_from_events(person_involved_in_event, event_has_theme):
    if person_involved_in_event.empty:
        return empty_person_theme_relations()

    inherited_theme_data = event_has_theme[
        ~event_has_theme["theme_name"].isin(build_non_inherited_theme_names())
    ].copy()

    if inherited_theme_data.empty:
        return empty_person_theme_relations()

    relation_data = person_involved_in_event[
        ["start_person_id", "end_event_id"]
    ].merge(
        inherited_theme_data[
            ["start_event_id", "end_theme_id", "theme_name"]
        ],
        left_on="end_event_id",
        right_on="start_event_id",
        how="inner",
    )
    relation_data["relation_type"] = "HAS_THEME"
    relation_data["match_source"] = "EVENT_INVOLVED"
    relation_data["source_detail"] = relation_data["end_event_id"]

    return relation_data[build_person_theme_relation_columns()]


def build_person_theme_rows_from_name_category(term_refers_to_person, term_has_theme):
    if term_refers_to_person.empty:
        return empty_person_theme_relations()

    inherited_theme_data = term_has_theme[
        ~term_has_theme["theme_name"].isin(build_non_inherited_theme_names())
    ].copy()

    if inherited_theme_data.empty:
        return empty_person_theme_relations()

    relation_data = term_refers_to_person[
        ["start_term_id", "end_person_id"]
    ].merge(
        inherited_theme_data[
            ["start_term_id", "end_theme_id", "theme_name"]
        ],
        on="start_term_id",
        how="inner",
    )
    relation_data = relation_data.rename(columns={"end_person_id": "start_person_id"})
    relation_data["relation_type"] = "HAS_THEME"
    relation_data["match_source"] = "NAME_CATEGORY"
    relation_data["source_detail"] = relation_data["start_term_id"]

    return relation_data[build_person_theme_relation_columns()]


def aggregate_person_theme_relations(relation_data):
    if relation_data.empty:
        return empty_person_theme_relations()

    relation_data = relation_data.dropna(
        subset=["start_person_id", "end_theme_id"]
    ).copy()

    if relation_data.empty:
        return empty_person_theme_relations()

    return (
        relation_data
        .groupby(
            ["start_person_id", "end_theme_id", "relation_type", "theme_name"],
            dropna=False,
        )
        .agg(
            match_source=("match_source", unique_join),
            source_detail=("source_detail", unique_join),
        )
        .reset_index()[build_person_theme_relation_columns()]
    )


def build_person_theme_relations(
    people,
    person_involved_in_event,
    term_refers_to_person,
    term_has_theme,
    event_has_theme,
    theme_nodes,
):
    relation_parts = [
        build_person_theme_rows_from_label(people, theme_nodes),
        build_person_theme_rows_from_events(person_involved_in_event, event_has_theme),
        build_person_theme_rows_from_name_category(term_refers_to_person, term_has_theme),
    ]
    relation_data = pd.concat(relation_parts, ignore_index=True)

    return aggregate_person_theme_relations(relation_data)


def build_era_nodes(era_seed):
    era_nodes = era_seed.copy()
    require_columns(
        era_nodes,
        ["era_id", "era_name", "era_order", "start_year", "end_year", "note"],
        "era_seed",
    )
    validate_unique_columns(era_nodes, ["era_id", "era_name"], "era_seed")
    era_nodes = era_nodes.rename(columns={"era_name": "name"})

    return era_nodes[["era_id", "name", "era_order", "start_year", "end_year", "note"]]


def build_period_era_relations(periods, period_era_seed, era_nodes):
    # 원천 매핑: 기존 Period 노드(표기 변형 포함)를 표준 시대로 연결한다.
    era_id_lookup = dict(zip(era_nodes["name"], era_nodes["era_id"]))
    relation_data = periods.merge(
        period_era_seed,
        left_on="name",
        right_on="period_name",
        how="inner",
    )
    relation_data["end_era_id"] = relation_data["era_name"].map(era_id_lookup)
    relation_data["relation_type"] = "PART_OF_ERA"
    relation_data = relation_data.rename(columns={"period_id": "start_period_id"})

    return relation_data[
        ["start_period_id", "end_era_id", "relation_type", "period_name", "era_name"]
    ]


def normalize_keyword(name_series):
    # 띄어쓰기, 가운뎃점, 마침표 표기 차이를 무시하고 비교하기 위한 정규화.
    return (
        name_series.fillna("")
        .str.strip()
        .str.replace(r"[\s·.]+", "", regex=True)
    )


def build_in_era_rows_from_period(in_period_data, id_column, period_era_seed, era_nodes):
    # 직통 엣지: X -> IN_PERIOD -> Period -> Era 경로를 X -> IN_ERA로 미리 펼친다.
    era_id_lookup = dict(zip(era_nodes["name"], era_nodes["era_id"]))
    relation_data = in_period_data[[id_column, "period_name"]].merge(
        period_era_seed,
        on="period_name",
        how="inner",
    )
    relation_data["end_era_id"] = relation_data["era_name"].map(era_id_lookup)
    relation_data["relation_type"] = "IN_ERA"
    relation_data["match_source"] = "PERIOD"
    relation_data["source_detail"] = relation_data["period_name"]

    return relation_data[
        [id_column, "end_era_id", "relation_type", "era_name", "match_source", "source_detail"]
    ]


def build_in_era_rows_from_keywords(terms, keyword_era_seed, era_nodes):
    # 시험 빈출 키워드 override를 용어명과 정규화 매칭해 Term -> Era로 연결한다.
    # 원본 시대 표기가 없는 고조선/초기 국가 시대를 보강하는 역할을 한다.
    era_id_lookup = dict(zip(era_nodes["name"], era_nodes["era_id"]))

    term_data = terms[["term_id", "name"]].copy()
    term_data["normalized_name"] = normalize_keyword(term_data["name"])

    keyword_data = keyword_era_seed.copy()
    keyword_data["normalized_keyword"] = normalize_keyword(keyword_data["keyword"])

    relation_data = term_data.merge(
        keyword_data,
        left_on="normalized_name",
        right_on="normalized_keyword",
        how="inner",
    )
    relation_data["end_era_id"] = relation_data["era_name"].map(era_id_lookup)
    relation_data["relation_type"] = "IN_ERA"
    relation_data["match_source"] = "KEYWORD_OVERRIDE"
    relation_data["source_detail"] = relation_data["keyword"]
    relation_data = relation_data.rename(columns={"term_id": "start_term_id"})

    return relation_data[
        [
            "start_term_id",
            "end_era_id",
            "relation_type",
            "era_name",
            "match_source",
            "source_detail",
        ]
    ]


def build_in_era_rows_from_candidates(term_era_candidate, era_nodes):
    # 검수를 통과한(AUTO_APPROVED/APPROVED) 시대 후보만 Term -> Era로 반영한다.
    if term_era_candidate.empty:
        return pd.DataFrame(
            columns=[
                "start_term_id",
                "end_era_id",
                "relation_type",
                "era_name",
                "match_source",
                "source_detail",
            ]
        )

    era_id_lookup = dict(zip(era_nodes["name"], era_nodes["era_id"]))
    relation_data = term_era_candidate[
        term_era_candidate["review_status"].isin(["AUTO_APPROVED", "APPROVED"])
    ].copy()
    relation_data["end_era_id"] = relation_data["era_name"].map(era_id_lookup)
    relation_data = relation_data.dropna(subset=["end_era_id"])
    relation_data["relation_type"] = "IN_ERA"
    relation_data["match_source"] = "DESC_KEYWORD"
    relation_data["source_detail"] = relation_data["matched_marker"]
    relation_data = relation_data.rename(columns={"term_id": "start_term_id"})

    return relation_data[
        [
            "start_term_id",
            "end_era_id",
            "relation_type",
            "era_name",
            "match_source",
            "source_detail",
        ]
    ]


def build_term_era_relations(
    terms, term_in_period, keyword_era_seed, term_era_candidate, period_era_seed, era_nodes
):
    period_rows = build_in_era_rows_from_period(
        term_in_period, "start_term_id", period_era_seed, era_nodes
    )
    keyword_rows = build_in_era_rows_from_keywords(terms, keyword_era_seed, era_nodes)
    candidate_rows = build_in_era_rows_from_candidates(term_era_candidate, era_nodes)

    relation_data = pd.concat(
        [period_rows, keyword_rows, candidate_rows], ignore_index=True
    )
    relation_data = relation_data.drop_duplicates(
        subset=["start_term_id", "end_era_id"]
    )

    return relation_data


def build_event_era_relations(event_in_period, period_era_seed, era_nodes):
    relation_data = build_in_era_rows_from_period(
        event_in_period, "start_event_id", period_era_seed, era_nodes
    )
    relation_data = relation_data.drop_duplicates(
        subset=["start_event_id", "end_era_id"]
    )

    return relation_data


def build_person_era_relations(people, person_involved_in_event, event_in_era, era_nodes):
    # 1차(BIRTH_YEAR): 생몰년이 Era 연도 범위와 겹치면 연결. 몰년이 없으면 출생 연도만 사용.
    # 2차(EVENT_INFERRED): 생년이 없는 인물은 참여 사건의 Era를 따라 보조 추론.
    era_data = era_nodes[["era_id", "name", "start_year", "end_year"]].copy()
    era_data["era_start"] = pd.to_numeric(era_data["start_year"], errors="coerce")
    era_data["era_end"] = pd.to_numeric(era_data["end_year"], errors="coerce")

    person_data = people[["person_id", "birth_year", "death_year"]].copy()
    person_data["birth"] = pd.to_numeric(person_data["birth_year"], errors="coerce")
    person_data["death"] = pd.to_numeric(person_data["death_year"], errors="coerce")

    # 생년 또는 몰년 중 하나만 있어도 그 연도로 생애 구간을 잡는다.
    # "15??" 같은 부분 연도는 세기 해석이 애매하므로 사용하지 않는다.
    birth_data = person_data.dropna(subset=["birth", "death"], how="all").copy()
    birth_data["lifespan_start"] = birth_data["birth"].fillna(birth_data["death"])
    birth_data["lifespan_end"] = birth_data["death"].fillna(birth_data["birth"])

    birth_rows = birth_data.merge(era_data, how="cross")
    overlap_mask = (
        birth_rows["era_start"].isna()
        | (birth_rows["era_start"] <= birth_rows["lifespan_end"])
    ) & (
        birth_rows["era_end"].isna()
        | (birth_rows["era_end"] >= birth_rows["lifespan_start"])
    )
    birth_rows = birth_rows[overlap_mask].copy()
    birth_rows["match_source"] = "BIRTH_YEAR"
    birth_rows["source_event_ids"] = ""

    no_birth_ids = set(
        person_data[person_data["birth"].isna() & person_data["death"].isna()][
            "person_id"
        ]
    )
    event_rows = person_involved_in_event[
        person_involved_in_event["start_person_id"].isin(no_birth_ids)
    ][["start_person_id", "end_event_id"]].merge(
        event_in_era[["start_event_id", "end_era_id", "era_name"]],
        left_on="end_event_id",
        right_on="start_event_id",
    )
    event_rows = (
        event_rows.groupby(["start_person_id", "end_era_id", "era_name"])["end_event_id"]
        .apply(lambda event_ids: "|".join(sorted(set(event_ids))))
        .reset_index(name="source_event_ids")
    )
    event_rows["match_source"] = "EVENT_INFERRED"
    event_rows["birth_year"] = ""
    event_rows["death_year"] = ""

    birth_rows = birth_rows.rename(
        columns={"person_id": "start_person_id", "era_id": "end_era_id", "name": "era_name"}
    )

    relation_data = pd.concat(
        [
            birth_rows[
                [
                    "start_person_id",
                    "end_era_id",
                    "era_name",
                    "match_source",
                    "birth_year",
                    "death_year",
                    "source_event_ids",
                ]
            ],
            event_rows[
                [
                    "start_person_id",
                    "end_era_id",
                    "era_name",
                    "match_source",
                    "birth_year",
                    "death_year",
                    "source_event_ids",
                ]
            ],
        ],
        ignore_index=True,
    )
    relation_data["relation_type"] = "IN_ERA"
    relation_data = relation_data.drop_duplicates(
        subset=["start_person_id", "end_era_id"]
    )

    return relation_data[
        [
            "start_person_id",
            "end_era_id",
            "relation_type",
            "era_name",
            "match_source",
            "birth_year",
            "death_year",
            "source_event_ids",
        ]
    ]


def build_person_evidence_url_relations(person_relations, source_urls):
    # 인물 관계의 evidence_url을 SourceUrl 노드와 연결해 고립 URL을 해소한다.
    # 대칭 관계 dedup 이전의 normalized 원본을 사용해 양방향 URL을 모두 수집하고,
    # 관계의 양쪽 인물 모두에서 근거 URL을 탐색할 수 있게 연결한다.
    url_id_lookup = dict(zip(source_urls["url"], source_urls["source_url_id"]))

    evidence_data = person_relations.dropna(subset=["evidence_url"])[
        ["person_id", "related_person_id", "relation_type", "evidence_url"]
    ].copy()
    evidence_data = evidence_data.rename(
        columns={"relation_type": "raw_relation_type"}
    )

    start_rows = evidence_data[
        ["person_id", "raw_relation_type", "evidence_url"]
    ].copy()
    start_rows["evidence_role"] = "START_PERSON"

    end_rows = evidence_data[
        ["related_person_id", "raw_relation_type", "evidence_url"]
    ].rename(columns={"related_person_id": "person_id"})
    end_rows["evidence_role"] = "RELATED_PERSON"

    relation_data = pd.concat([start_rows, end_rows], ignore_index=True)
    relation_data["end_source_url_id"] = relation_data["evidence_url"].map(url_id_lookup)
    relation_data = relation_data.dropna(subset=["end_source_url_id"])
    relation_data["relation_type"] = "HAS_EVIDENCE_URL"
    relation_data["source_column"] = "person_relations.evidence_url"
    relation_data = relation_data.rename(
        columns={"person_id": "start_person_id", "evidence_url": "url"}
    )
    relation_data = relation_data.drop_duplicates(
        subset=["start_person_id", "end_source_url_id", "evidence_role", "raw_relation_type"]
    )

    return relation_data[
        [
            "start_person_id",
            "end_source_url_id",
            "relation_type",
            "source_column",
            "evidence_role",
            "raw_relation_type",
            "url",
        ]
    ]


def build_entity_type_nodes(entity_type_seed):
    require_columns(
        entity_type_seed,
        [
            "entity_type_id",
            "root_category_name",
            "entity_type_name",
            "entity_type_order",
            "note",
        ],
        "entity_type_seed",
    )
    entity_type_nodes = entity_type_seed.drop_duplicates(
        subset=["entity_type_id", "entity_type_name"]
    ).copy()
    validate_unique_columns(
        entity_type_nodes,
        ["entity_type_id", "entity_type_name"],
        "entity_type_seed",
    )
    entity_type_nodes = entity_type_nodes.rename(columns={"entity_type_name": "name"})

    return entity_type_nodes[["entity_type_id", "name", "entity_type_order", "note"]]


def build_term_entity_type_relations(
    term_has_canonical_category, entity_type_seed, entity_type_nodes
):
    # 용어의 카테고리 경로 루트가 실체 유형 카테고리(인명/서명 등)면 유형 축으로 연결한다.
    entity_type_id_lookup = dict(
        zip(entity_type_nodes["name"], entity_type_nodes["entity_type_id"])
    )
    root_type_lookup = dict(
        zip(
            entity_type_seed["root_category_name"],
            entity_type_seed["entity_type_name"],
        )
    )

    relation_data = term_has_canonical_category[
        ["start_term_id", "category_path"]
    ].copy()
    relation_data["root_category_name"] = (
        relation_data["category_path"].fillna("").str.split(">").str[0].str.strip()
    )
    relation_data["entity_type_name"] = relation_data["root_category_name"].map(
        root_type_lookup
    )
    relation_data = relation_data.dropna(subset=["entity_type_name"])
    relation_data["end_entity_type_id"] = relation_data["entity_type_name"].map(
        entity_type_id_lookup
    )
    relation_data["relation_type"] = "HAS_ENTITY_TYPE"
    relation_data = relation_data.drop_duplicates(
        subset=["start_term_id", "end_entity_type_id"]
    )

    return relation_data[
        ["start_term_id", "end_entity_type_id", "relation_type", "entity_type_name"]
    ]


def build_outputs(inputs):
    theme_nodes = build_theme_nodes(inputs["theme_seed"])
    era_nodes = build_era_nodes(inputs["era_seed"])
    entity_type_nodes = build_entity_type_nodes(inputs["entity_type_seed"])

    event_in_era = build_event_era_relations(
        inputs["event_in_period"],
        inputs["period_era_seed"],
        era_nodes,
    )
    term_has_theme = build_term_theme_relations(
        inputs["term_has_canonical_category"],
        inputs["category_theme_seed"],
        theme_nodes,
    )
    event_has_theme = build_event_theme_relations(
        inputs["events"],
        inputs["event_has_canonical_category"],
        inputs["canonical_categories"],
        inputs["category_theme_seed"],
        theme_nodes,
    )

    node_outputs = {
        "themes": theme_nodes,
        "eras": era_nodes,
        "entity_types": entity_type_nodes,
    }
    relation_outputs = {
        "canonical_category_has_theme": build_category_theme_relations(
            inputs["canonical_categories"],
            inputs["category_theme_seed"],
            theme_nodes,
        ),
        "term_has_theme": term_has_theme,
        "event_has_theme": event_has_theme,
        "person_has_theme": build_person_theme_relations(
            inputs["people"],
            inputs["person_involved_in_event"],
            inputs["term_refers_to_person"],
            term_has_theme,
            event_has_theme,
            theme_nodes,
        ),
        "period_part_of_era": build_period_era_relations(
            inputs["periods"],
            inputs["period_era_seed"],
            era_nodes,
        ),
        "term_in_era": build_term_era_relations(
            inputs["terms"],
            inputs["term_in_period"],
            inputs["keyword_era_seed"],
            inputs["term_era_candidate"],
            inputs["period_era_seed"],
            era_nodes,
        ),
        "event_in_era": event_in_era,
        "person_in_era": build_person_era_relations(
            inputs["people"],
            inputs["person_involved_in_event"],
            event_in_era,
            era_nodes,
        ),
        "person_has_evidence_url": build_person_evidence_url_relations(
            inputs["person_relations"],
            inputs["source_urls"],
        ),
        "term_has_entity_type": build_term_entity_type_relations(
            inputs["term_has_canonical_category"],
            inputs["entity_type_seed"],
            entity_type_nodes,
        ),
    }

    return node_outputs, relation_outputs


def write_or_print_outputs(args, node_outputs, relation_outputs):
    output_files = []

    for output_name, data_frame in node_outputs.items():
        output_files.append(
            (f"{output_name}.csv", data_frame, args.nodes_dir / f"{output_name}.csv")
        )

    for output_name, data_frame in relation_outputs.items():
        output_files.append(
            (
                f"{output_name}.csv",
                data_frame,
                args.relations_dir / f"{output_name}.csv",
            )
        )

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
    node_outputs, relation_outputs = build_outputs(inputs)

    write_or_print_outputs(args, node_outputs, relation_outputs)


if __name__ == "__main__":
    main()
