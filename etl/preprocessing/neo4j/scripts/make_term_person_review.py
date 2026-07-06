"""
Term-Person 동명이인 수동 검수 후보 CSV를 만든다.

이 스크립트는 기본 runner에 포함하지 않는다. 사람이 검수해야 하는 후보를
필요할 때만 생성하고, 승인 결과는 seed/term_person_review_approved.csv에 남긴다.
"""

import argparse
import re
from pathlib import Path

import pandas as pd

from make_graph_csv import find_unique_names, split_person_name_columns
from neo4j_common import print_summary, read_csv, resolve_project_root, save_csv


def build_default_paths(script_path):
    project_root = resolve_project_root(script_path)
    import_dir = project_root / "storage" / "neo4j" / "neo4j_import"
    neo4j_dir = script_path.parents[1]

    return {
        "terms": import_dir / "nodes" / "terms.csv",
        "people": import_dir / "nodes" / "people.csv",
        "output": neo4j_dir / "staging" / "term_person_review.csv",
    }


def parse_args(default_paths):
    parser = argparse.ArgumentParser(
        description="Term-Person 동명이인 검수 후보 CSV를 만든다."
    )
    parser.add_argument("--terms-path", type=Path, default=default_paths["terms"])
    parser.add_argument("--people-path", type=Path, default=default_paths["people"])
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
        "term_desc_preview",
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
    term_data["name"] = term_data["name"].fillna("").str.strip()
    term_data["hanja"] = term_data["hanja"].fillna("").str.strip()
    term_data["description"] = term_data["description"].fillna("").astype(str)

    return term_data


def build_person_review_data(people):
    person_data = split_person_name_columns(people)

    for column_name in ["birth_year", "death_year"]:
        if column_name in people.columns:
            person_data = person_data.merge(
                people[["person_id", column_name]],
                on="person_id",
                how="left",
            )

        if column_name not in person_data.columns:
            person_data[column_name] = pd.NA

    return person_data


def extract_reign_year_range(description):
    match = re.search(
        r"재위\s*(\d{2,4})\s*[-~∼－–—]\s*(\d{2,4})\s*년",
        str(description or ""),
    )

    if not match:
        return pd.Series([pd.NA, pd.NA])

    start_year = int(match.group(1))
    end_year = int(match.group(2))

    if start_year <= end_year:
        return pd.Series([start_year, end_year])

    return pd.Series([end_year, start_year])


def filter_candidates_by_reign_year(candidates):
    year_range_data = candidates["description"].apply(extract_reign_year_range)
    year_range_data.columns = ["term_reign_start", "term_reign_end"]
    candidates = candidates.join(year_range_data)
    candidates["term_reign_start_number"] = pd.to_numeric(
        candidates["term_reign_start"],
        errors="coerce",
    )
    candidates["term_reign_end_number"] = pd.to_numeric(
        candidates["term_reign_end"],
        errors="coerce",
    )
    candidates["birth_year_number"] = pd.to_numeric(
        candidates["birth_year"],
        errors="coerce",
    )
    candidates["death_year_number"] = pd.to_numeric(
        candidates["death_year"],
        errors="coerce",
    )
    has_reign_year = (
        candidates["term_reign_start_number"].notna()
        & candidates["term_reign_end_number"].notna()
    )
    has_person_year = (
        candidates["birth_year_number"].notna()
        & candidates["death_year_number"].notna()
    )
    overlaps_reign_year = (
        candidates["term_reign_start_number"].le(candidates["death_year_number"])
        & candidates["term_reign_end_number"].ge(candidates["birth_year_number"])
    )
    filtered_candidates = candidates[
        ~has_reign_year | (has_person_year & overlaps_reign_year)
    ].copy()

    return filtered_candidates.drop(
        columns=[
            "term_reign_start",
            "term_reign_end",
            "term_reign_start_number",
            "term_reign_end_number",
            "birth_year_number",
            "death_year_number",
        ],
    )


def filter_single_name_candidates(candidates):
    name_counts = candidates.groupby("name")["person_id"].transform("nunique")

    return candidates[name_counts.gt(1)].copy()


def filter_empty_year_candidates_with_complete_group_candidate(candidates):
    group_columns = ["term_id", "name", "hanja_term", "description"]
    has_complete_person_year = (
        candidates["birth_year"].fillna("").astype(str).str.strip().ne("")
        & candidates["death_year"].fillna("").astype(str).str.strip().ne("")
    )
    group_has_complete_person_year = has_complete_person_year.groupby(
        [candidates[column_name] for column_name in group_columns]
    ).transform("any")

    return candidates[
        has_complete_person_year | ~group_has_complete_person_year
    ].copy()


def add_review_type(candidates):
    group_columns = ["term_id", "name", "hanja_term", "term_desc_preview"]
    group_person_counts = candidates.groupby(group_columns)["person_id"].transform(
        "nunique"
    )

    candidates = candidates.copy()
    candidates["review_type"] = "TERM_PERSON"
    candidates.loc[group_person_counts.gt(1), "review_type"] = "PERSON_DUPLICATE"

    return candidates


def build_review_candidates(terms, people):
    term_data = build_term_review_data(terms)
    person_data = build_person_review_data(people)
    auto_names = find_unique_names(term_data["name"], person_data["base_name"])

    candidate_terms = term_data[~term_data["name"].isin(auto_names)].copy()
    candidate_people = person_data[~person_data["base_name"].isin(auto_names)].copy()
    candidates = candidate_terms.merge(
        candidate_people,
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

    candidates = filter_candidates_by_reign_year(candidates)

    if candidates.empty:
        return empty_review_candidates()

    candidates = filter_empty_year_candidates_with_complete_group_candidate(candidates)

    if candidates.empty:
        return empty_review_candidates()

    candidates = filter_single_name_candidates(candidates)

    if candidates.empty:
        return empty_review_candidates()

    candidates["person_name"] = candidates["base_name"]
    candidates["term_desc_preview"] = candidates["description"].str.slice(0, 50)
    candidates = add_review_type(candidates)
    candidates["review_status"] = "PENDING"
    candidates["note"] = ""
    candidates = candidates.rename(
        columns={
            "hanja_term": "term_hanja",
            "hanja_person": "person_hanja",
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
    review_candidates = build_review_candidates(terms, people)

    write_or_print_output(args, review_candidates)


if __name__ == "__main__":
    main()
