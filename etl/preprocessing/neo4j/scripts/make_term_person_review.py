"""
Term-Person 동명이인 수동 검수 후보 CSV를 만든다.

이 스크립트는 runner 6단계에서 검수 후보를 재생성한다.
승인 결과는 seed/term_person_review_approved.csv에 남긴다.
"""

import argparse
from pathlib import Path

import pandas as pd

from make_graph_csv import (
    add_description_context_match_columns,
    build_description_context_match_mask,
    build_unique_exact_term_person_year_group_mask,
    merge_person_life_year_columns,
    split_person_name_columns,
)
from neo4j_common import (
    print_summary,
    read_csv,
    read_optional_csv,
    resolve_import_dir,
    resolve_project_root,
    save_csv,
)


def build_default_paths(script_path):
    project_root = resolve_project_root(script_path)
    import_dir = resolve_import_dir(project_root)
    neo4j_dir = script_path.parents[1]

    return {
        "terms": import_dir / "nodes" / "terms.csv",
        "people": import_dir / "nodes" / "people.csv",
        "person_relations": neo4j_dir / "normalized" / "person_relations.csv",
        "term_in_period": import_dir / "relations" / "term_in_period.csv",
        "periods": import_dir / "nodes" / "periods.csv",
        "term_person_review_approved": (
            neo4j_dir / "seed" / "term_person_review_approved.csv"
        ),
        "output": neo4j_dir / "staging" / "term_person_review.csv",
    }


def parse_args(default_paths):
    parser = argparse.ArgumentParser(
        description="Term-Person 동명이인 검수 후보 CSV를 만든다."
    )
    parser.add_argument("--terms-path", type=Path, default=default_paths["terms"])
    parser.add_argument("--people-path", type=Path, default=default_paths["people"])
    parser.add_argument(
        "--person-relations-path",
        type=Path,
        default=default_paths["person_relations"],
    )
    parser.add_argument(
        "--term-in-period-path",
        type=Path,
        default=default_paths["term_in_period"],
    )
    parser.add_argument("--periods-path", type=Path, default=default_paths["periods"])
    parser.add_argument(
        "--term-person-review-approved-path",
        type=Path,
        default=default_paths["term_person_review_approved"],
    )
    parser.add_argument("--output-path", type=Path, default=default_paths["output"])
    parser.add_argument(
        "--save",
        action="store_true",
        help="CSV 파일을 저장한다. 지정하지 않으면 dry-run으로 요약만 출력한다.",
    )

    return parser.parse_args()


def build_review_columns():
    return [
        "review_type",
        "name",
        "term_id",
        "term_hanja",
        "term_description",
        "term_year_text",
        "term_start_year",
        "term_end_year",
        "person_id",
        "person_name",
        "person_hanja",
        "birth_year",
        "death_year",
        "review_status",
        "note",
    ]


def empty_review_candidates():
    return pd.DataFrame(columns=build_review_columns())


def build_term_review_data(terms):
    term_data = terms[["term_id", "name", "hanja", "description"]].copy()

    for column_name in ["year_text", "start_year", "end_year"]:
        if column_name in terms.columns:
            term_data[column_name] = terms[column_name]

        if column_name not in term_data.columns:
            term_data[column_name] = pd.NA

    term_data["name"] = term_data["name"].fillna("").str.strip()
    term_data["hanja"] = term_data["hanja"].fillna("").str.strip()
    term_data["description"] = term_data["description"].fillna("").astype(str)

    return term_data


def build_person_review_data(people):
    person_data = split_person_name_columns(people)
    return merge_person_life_year_columns(person_data, people)


def empty_term_period_ranges():
    return pd.DataFrame(
        columns=["term_id", "period_start_year", "period_end_year"]
    )


def build_term_period_ranges(term_in_period, periods):
    required_term_period_columns = ["start_term_id", "end_period_id"]
    required_period_columns = ["period_id", "start_year", "end_year"]
    missing_term_period_columns = [
        column_name
        for column_name in required_term_period_columns
        if column_name not in term_in_period.columns
    ]
    missing_period_columns = [
        column_name
        for column_name in required_period_columns
        if column_name not in periods.columns
    ]

    if missing_term_period_columns or missing_period_columns:
        return empty_term_period_ranges()

    period_data = term_in_period[required_term_period_columns].merge(
        periods[required_period_columns],
        left_on="end_period_id",
        right_on="period_id",
        how="left",
    )
    period_data["period_start_year"] = pd.to_numeric(
        period_data["start_year"],
        errors="coerce",
    )
    period_data["period_end_year"] = pd.to_numeric(
        period_data["end_year"],
        errors="coerce",
    )
    period_data = period_data.dropna(
        subset=["period_start_year", "period_end_year"]
    ).copy()

    if period_data.empty:
        return empty_term_period_ranges()

    period_ranges = period_data.groupby("start_term_id", as_index=False).agg(
        period_start_year=("period_start_year", "min"),
        period_end_year=("period_end_year", "max"),
    )
    period_ranges = period_ranges.rename(columns={"start_term_id": "term_id"})

    return period_ranges


def add_term_period_range_columns(candidates, term_in_period, periods):
    period_ranges = build_term_period_ranges(term_in_period, periods)
    enriched_candidates = candidates.copy()

    if period_ranges.empty:
        enriched_candidates["period_start_year"] = pd.NA
        enriched_candidates["period_end_year"] = pd.NA
        return enriched_candidates

    return enriched_candidates.merge(period_ranges, on="term_id", how="left")


def build_period_life_conflict_mask(candidates):
    required_columns = [
        "period_start_year",
        "period_end_year",
        "birth_year",
        "death_year",
    ]
    missing_columns = [
        column_name
        for column_name in required_columns
        if column_name not in candidates.columns
    ]

    if missing_columns:
        return pd.Series(False, index=candidates.index)

    period_start_year = pd.to_numeric(
        candidates["period_start_year"],
        errors="coerce",
    )
    period_end_year = pd.to_numeric(
        candidates["period_end_year"],
        errors="coerce",
    )
    birth_year = pd.to_numeric(candidates["birth_year"], errors="coerce")
    death_year = pd.to_numeric(candidates["death_year"], errors="coerce")
    has_complete_years = (
        period_start_year.notna()
        & period_end_year.notna()
        & birth_year.notna()
        & death_year.notna()
    )

    return has_complete_years & (
        death_year.lt(period_start_year) | birth_year.gt(period_end_year)
    )


def build_term_life_conflict_mask(candidates):
    required_columns = [
        "start_year",
        "end_year",
        "birth_year",
        "death_year",
    ]
    missing_columns = [
        column_name
        for column_name in required_columns
        if column_name not in candidates.columns
    ]

    if missing_columns:
        return pd.Series(False, index=candidates.index)

    term_start_year = pd.to_numeric(candidates["start_year"], errors="coerce")
    term_end_year = pd.to_numeric(candidates["end_year"], errors="coerce")
    birth_year = pd.to_numeric(candidates["birth_year"], errors="coerce")
    death_year = pd.to_numeric(candidates["death_year"], errors="coerce")
    has_complete_years = (
        term_start_year.notna()
        & term_end_year.notna()
        & birth_year.notna()
        & death_year.notna()
    )

    return has_complete_years & (
        death_year.lt(term_start_year) | birth_year.gt(term_end_year)
    )


def filter_supported_identity_candidates(candidates):
    description_match = build_description_context_match_mask(candidates)
    period_life_conflict = build_period_life_conflict_mask(candidates)
    term_life_conflict = build_term_life_conflict_mask(candidates)

    return candidates[
        description_match & ~period_life_conflict & ~term_life_conflict
    ].copy()


def add_review_type(candidates):
    group_columns = ["term_id", "name", "hanja_term", "term_description"]
    group_person_counts = candidates.groupby(group_columns)["person_id"].transform(
        "nunique"
    )

    candidates = candidates.copy()
    candidates["review_type"] = "TERM_PERSON"
    candidates.loc[group_person_counts.gt(1), "review_type"] = "PERSON_DUPLICATE"

    return candidates


def filter_approved_review_candidates(candidates, approved_links):
    if candidates.empty or approved_links.empty:
        return candidates

    required_columns = ["term_id", "person_id", "review_status"]
    missing_columns = [
        column_name
        for column_name in required_columns
        if column_name not in approved_links.columns
    ]

    if missing_columns:
        return candidates

    approved_data = approved_links[
        approved_links["review_status"].isin(["APPROVED", "AUTO_APPROVED"])
    ][["term_id", "person_id"]].drop_duplicates()

    if approved_data.empty:
        return candidates

    filtered_candidates = candidates.merge(
        approved_data,
        on=["term_id", "person_id"],
        how="left",
        indicator=True,
    )
    filtered_candidates = filtered_candidates[
        filtered_candidates["_merge"].eq("left_only")
    ].copy()
    filtered_candidates = filtered_candidates.drop(columns=["_merge"])

    return filtered_candidates


def build_review_candidates(terms, people, person_relations, term_in_period, periods):
    term_data = build_term_review_data(terms)
    person_data = build_person_review_data(people)

    candidates = term_data.merge(
        person_data,
        left_on="name",
        right_on="base_name",
        how="inner",
        suffixes=("_term", "_person"),
    )

    if candidates.empty:
        return empty_review_candidates()

    candidates["hanja_term"] = candidates["hanja_term"].fillna("").str.strip()
    candidates["hanja_person"] = candidates["hanja_person"].fillna("").str.strip()
    candidates = candidates[
        candidates["hanja_term"].ne("")
        & candidates["hanja_person"].ne("")
        & candidates["hanja_term"].eq(candidates["hanja_person"])
    ].copy()

    if candidates.empty:
        return empty_review_candidates()

    candidates = add_description_context_match_columns(candidates, person_relations)
    candidates = add_term_period_range_columns(candidates, term_in_period, periods)
    candidates = filter_supported_identity_candidates(candidates)

    if candidates.empty:
        return empty_review_candidates()

    auto_term_mask = build_unique_exact_term_person_year_group_mask(candidates)
    candidates = candidates[~auto_term_mask].copy()

    if candidates.empty:
        return empty_review_candidates()

    candidates["person_name"] = candidates["base_name"]
    candidates["term_description"] = candidates["description"]
    candidates = add_review_type(candidates)

    if candidates.empty:
        return empty_review_candidates()

    candidates["review_status"] = "PENDING"
    candidates["note"] = ""
    candidates = candidates.rename(
        columns={
            "hanja_term": "term_hanja",
            "hanja_person": "person_hanja",
            "year_text": "term_year_text",
            "start_year": "term_start_year",
            "end_year": "term_end_year",
        }
    )

    return candidates[build_review_columns()].sort_values(
        ["name", "term_id", "person_id"]
    )


def write_or_print_output(args, review_candidates):
    if args.save:
        save_csv(review_candidates, args.output_path)
        print_summary(args.output_path.name, review_candidates)
        print(f"output_path: {args.output_path}")

    if not args.save:
        print_summary(args.output_path.name, review_candidates)
        print(f"planned_path: {args.output_path}")
        print("dry_run: no files saved. Use --save to write CSV files.")


def main():
    script_path = Path(__file__).resolve()
    default_paths = build_default_paths(script_path)
    args = parse_args(default_paths)
    terms = read_csv(args.terms_path, "terms")
    people = read_csv(args.people_path, "people")
    person_relations = read_csv(args.person_relations_path, "person_relations")
    term_in_period = read_csv(args.term_in_period_path, "term_in_period")
    periods = read_csv(args.periods_path, "periods")
    approved_links = read_optional_csv(
        args.term_person_review_approved_path,
        "term_person_review_approved",
    )
    review_candidates = build_review_candidates(
        terms,
        people,
        person_relations,
        term_in_period,
        periods,
    )
    review_candidates = filter_approved_review_candidates(
        review_candidates,
        approved_links,
    )

    write_or_print_output(args, review_candidates)


if __name__ == "__main__":
    main()
