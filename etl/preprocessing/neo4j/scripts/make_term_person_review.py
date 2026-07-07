"""
Term-Person 동명이인 수동 검수 후보 CSV를 만든다.

이 스크립트는 기본 runner에 포함하지 않는다. 사람이 검수해야 하는 후보를
필요할 때만 생성하고, 승인 결과는 seed/term_person_review_approved.csv에 남긴다.
"""

import argparse
from pathlib import Path

import pandas as pd

from make_graph_csv import (
    build_unique_exact_term_person_year_group_mask,
    merge_person_life_year_columns,
    split_person_name_columns,
)
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

    auto_term_mask = build_unique_exact_term_person_year_group_mask(candidates)
    candidates = candidates[~auto_term_mask].copy()

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
    review_candidates = build_review_candidates(terms, people)

    write_or_print_output(args, review_candidates)


if __name__ == "__main__":
    main()
