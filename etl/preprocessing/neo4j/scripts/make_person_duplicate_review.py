"""
Person ID 중복 의심 후보와 병합 seed 초안을 만든다.

기본 실행은 dry-run이다. CSV 저장이 필요할 때만 --save, --save-seed를 사용한다.
"""

import argparse
from pathlib import Path

import pandas as pd

from make_graph_csv import split_person_name_columns
from neo4j_common import print_summary, read_csv, resolve_project_root, save_csv


def build_default_paths(script_path):
    project_root = resolve_project_root(script_path)
    import_dir = project_root / "storage" / "neo4j" / "neo4j_import"
    neo4j_dir = script_path.parents[1]

    return {
        "people": import_dir / "nodes" / "people.csv",
        "output": neo4j_dir / "staging" / "person_duplicate_review.csv",
        "seed_output": neo4j_dir / "seed" / "person_duplicate_review_approved.csv",
    }


def parse_args(default_paths):
    parser = argparse.ArgumentParser(
        description="Person ID 중복 의심 후보와 병합 seed 초안을 만든다."
    )
    parser.add_argument("--people-path", type=Path, default=default_paths["people"])
    parser.add_argument("--output-path", type=Path, default=default_paths["output"])
    parser.add_argument(
        "--seed-output-path",
        type=Path,
        default=default_paths["seed_output"],
    )
    parser.add_argument(
        "--save",
        action="store_true",
        help="상세 검수 후보 CSV를 저장한다.",
    )
    parser.add_argument(
        "--save-seed",
        action="store_true",
        help="병합 seed 초안 CSV를 저장한다. review_status는 PENDING으로 생성된다.",
    )

    return parser.parse_args()


def build_review_columns():
    return [
        "name",
        "hanja",
        "canonical_person_id",
        "canonical_name",
        "canonical_birth_year",
        "canonical_death_year",
        "canonical_bonkwan",
        "canonical_father_name",
        "canonical_degree",
        "canonical_detail_urls",
        "duplicate_person_id",
        "duplicate_name",
        "duplicate_birth_year",
        "duplicate_death_year",
        "duplicate_bonkwan",
        "duplicate_father_name",
        "duplicate_degree",
        "duplicate_detail_urls",
        "review_status",
        "note",
    ]


def build_seed_columns():
    return [
        "name",
        "hanja",
        "duplicate_person_id",
        "canonical_person_id",
        "review_status",
        "note",
    ]


def empty_review_candidates():
    return pd.DataFrame(columns=build_review_columns())


def empty_seed_candidates():
    return pd.DataFrame(columns=build_seed_columns())


def normalize_text_series(series):
    return series.fillna("").astype(str).str.strip()


def build_person_alias_data(people):
    alias_data = split_person_name_columns(people)

    if alias_data.empty:
        return alias_data

    alias_data["base_name"] = normalize_text_series(alias_data["base_name"])
    alias_data["hanja"] = normalize_text_series(alias_data["hanja"])
    alias_data = alias_data[
        alias_data["base_name"].ne("")
        & alias_data["hanja"].ne("")
    ].copy()

    return alias_data.drop_duplicates(subset=["person_id", "base_name", "hanja"])


def build_person_detail_data(people):
    detail_columns = [
        "person_id",
        "name",
        "birth_year",
        "death_year",
        "bonkwan",
        "father_name",
        "detail_urls",
        "degree",
    ]
    detail_data = people[detail_columns].copy()

    for column_name in detail_columns:
        detail_data[column_name] = normalize_text_series(detail_data[column_name])

    detail_data["degree_number"] = pd.to_numeric(
        detail_data["degree"],
        errors="coerce",
    ).fillna(0)
    detail_data["info_score"] = (
        detail_data["birth_year"].ne("").astype(int)
        + detail_data["death_year"].ne("").astype(int)
        + detail_data["bonkwan"].ne("").astype(int)
        + detail_data["father_name"].ne("").astype(int)
        + detail_data["detail_urls"].ne("").astype(int)
        + detail_data["degree_number"].gt(0).astype(int)
    )

    return detail_data


def build_duplicate_person_groups(alias_data):
    person_counts = alias_data.groupby(["base_name", "hanja"])["person_id"].nunique()
    duplicate_keys = person_counts[person_counts.gt(1)].reset_index()[
        ["base_name", "hanja"]
    ]

    if duplicate_keys.empty:
        return alias_data.iloc[0:0].copy()

    return alias_data.merge(duplicate_keys, on=["base_name", "hanja"], how="inner")


def attach_person_details(duplicate_aliases, people):
    detail_data = build_person_detail_data(people)

    return duplicate_aliases.merge(detail_data, on="person_id", how="inner")


def build_canonical_person_rows(candidate_data):
    sorted_candidates = candidate_data.sort_values(
        [
            "base_name",
            "hanja",
            "info_score",
            "degree_number",
            "person_id",
        ],
        ascending=[True, True, False, False, True],
    )

    return (
        sorted_candidates
        .drop_duplicates(subset=["base_name", "hanja"], keep="first")
        [
            [
                "base_name",
                "hanja",
                "person_id",
                "name",
                "birth_year",
                "death_year",
                "bonkwan",
                "father_name",
                "detail_urls",
                "degree",
            ]
        ]
        .rename(
            columns={
                "person_id": "canonical_person_id",
                "name": "canonical_name",
                "birth_year": "canonical_birth_year",
                "death_year": "canonical_death_year",
                "bonkwan": "canonical_bonkwan",
                "father_name": "canonical_father_name",
                "detail_urls": "canonical_detail_urls",
                "degree": "canonical_degree",
            }
        )
    )


def build_review_candidates(people):
    alias_data = build_person_alias_data(people)

    if alias_data.empty:
        return empty_review_candidates()

    duplicate_aliases = build_duplicate_person_groups(alias_data)

    if duplicate_aliases.empty:
        return empty_review_candidates()

    candidate_data = attach_person_details(duplicate_aliases, people)
    canonical_data = build_canonical_person_rows(candidate_data)
    review_data = candidate_data.merge(
        canonical_data,
        on=["base_name", "hanja"],
        how="inner",
    )
    review_data = review_data[
        review_data["person_id"].ne(review_data["canonical_person_id"])
    ].copy()

    if review_data.empty:
        return empty_review_candidates()

    review_data["review_status"] = "PENDING"
    review_data["note"] = (
        "자동 후보: 같은 이름/한자 Person ID 중복. "
        "정보량/관계수 기준 canonical 추천, 승인 전 검수 필요"
    )
    review_data = review_data.rename(
        columns={
            "base_name": "name",
            "person_id": "duplicate_person_id",
            "name": "duplicate_name",
            "birth_year": "duplicate_birth_year",
            "death_year": "duplicate_death_year",
            "bonkwan": "duplicate_bonkwan",
            "father_name": "duplicate_father_name",
            "detail_urls": "duplicate_detail_urls",
            "degree": "duplicate_degree",
        }
    )

    return review_data[build_review_columns()].sort_values(
        ["name", "hanja", "canonical_person_id", "duplicate_person_id"]
    )


def build_seed_candidates(review_candidates):
    if review_candidates.empty:
        return empty_seed_candidates()

    seed_data = review_candidates[
        [
            "name",
            "hanja",
            "duplicate_person_id",
            "canonical_person_id",
            "review_status",
            "note",
        ]
    ].copy()

    return seed_data[build_seed_columns()].drop_duplicates()


def write_or_print_output(args, review_candidates, seed_candidates):
    if args.save:
        save_csv(review_candidates, args.output_path)
        print_summary(args.output_path.name, review_candidates)
        print(f"output_path: {args.output_path}")

    if args.save_seed:
        save_csv(seed_candidates, args.seed_output_path)
        print_summary(args.seed_output_path.name, seed_candidates)
        print(f"seed_output_path: {args.seed_output_path}")

    if not args.save:
        print_summary(args.output_path.name, review_candidates)
        print(f"planned_path: {args.output_path}")

    if not args.save_seed:
        print_summary(args.seed_output_path.name, seed_candidates)
        print(f"planned_seed_path: {args.seed_output_path}")

    if not args.save and not args.save_seed:
        print("dry_run: no files saved. Use --save and/or --save-seed to write CSV files.")


def main():
    script_path = Path(__file__).resolve()
    default_paths = build_default_paths(script_path)
    args = parse_args(default_paths)
    people = read_csv(args.people_path, "people")
    review_candidates = build_review_candidates(people)
    seed_candidates = build_seed_candidates(review_candidates)

    write_or_print_output(args, review_candidates, seed_candidates)


if __name__ == "__main__":
    main()
