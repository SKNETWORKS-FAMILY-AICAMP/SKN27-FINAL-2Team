from __future__ import annotations

import _bootstrap

from argparse import ArgumentParser, Namespace
from json import dumps, loads
from pathlib import Path

import pandas as pd


def parse_arguments() -> Namespace:
    """Read paths used to consolidate source-specific relation candidates."""
    neo4j_root = Path(__file__).resolve().parent.parent
    parser = ArgumentParser(
        description=(
            "Merge source-specific NLP relation candidate CSV files without "
            "using an LLM or loading Neo4j."
        )
    )
    parser.add_argument(
        "--input-root",
        default=str(neo4j_root / "output"),
    )
    parser.add_argument(
        "--input-directory-pattern",
        default="exam_term_nlp_relations_full_*",
    )
    parser.add_argument(
        "--candidate-filename",
        default="exam_term_nlp_relation_candidates.csv",
    )
    parser.add_argument(
        "--output-dir",
        default=str(
            neo4j_root
            / "output"
            / "exam_term_nlp_relations_full"
        ),
    )
    parser.add_argument(
        "--output-filename",
        default="exam_term_nlp_relation_full_candidates.csv",
    )
    parser.add_argument(
        "--summary-filename",
        default="exam_term_nlp_relation_full_candidate_summary.csv",
    )
    return parser.parse_args()


def merge_candidate_tables(
    candidate_tables: list[pd.DataFrame],
) -> tuple[pd.DataFrame, dict[str, int]]:
    """Merge identical relations and union their source and evidence lists."""
    if not candidate_tables:
        raise ValueError("No relation candidate table was provided.")

    combined = pd.concat(
        candidate_tables,
        ignore_index=True,
    )
    identifier_column = "nlp_relation_candidate_id"
    required_columns = {
        identifier_column,
        "start_node_id",
        "end_node_id",
        "relation_family",
        "relation_type",
        "evidence_count",
        "anchor_exam_term_count",
        "anchor_exam_term_ids_json",
        "candidate_statuses_json",
        "maximum_candidate_score",
        "source_datasets_json",
        "evidence_ids_json",
        "touches_open_entity",
    }
    missing_columns = required_columns.difference(combined.columns)
    if missing_columns:
        raise ValueError(
            "Missing relation candidate columns: "
            + ", ".join(sorted(missing_columns))
        )

    duplicate_mask = combined.duplicated(
        subset=[identifier_column],
        keep=False,
    )
    unique_rows = combined.loc[~duplicate_mask].copy()
    duplicate_rows = combined.loc[duplicate_mask]
    merged_duplicate_rows: list[dict[str, object]] = []
    identity_columns = [
        "start_node_id",
        "end_node_id",
        "relation_family",
        "relation_type",
        "policy_version",
    ]
    list_columns = [
        "anchor_exam_term_ids_json",
        "candidate_statuses_json",
        "source_datasets_json",
        "evidence_ids_json",
    ]

    for relation_id, group in duplicate_rows.groupby(
        identifier_column,
        sort=False,
    ):
        for column in identity_columns:
            if group[column].nunique(dropna=False) != 1:
                raise ValueError(
                    f"Relation {relation_id} has conflicting {column} values."
                )

        merged_row = group.iloc[0].to_dict()
        merged_lists: dict[str, list[str]] = {}
        for column in list_columns:
            values: set[str] = set()
            for serialized_values in group[column]:
                values.update(
                    str(value)
                    for value in loads(str(serialized_values))
                )
            merged_lists[column] = sorted(values)
            merged_row[column] = dumps(
                merged_lists[column],
                ensure_ascii=False,
            )

        merged_row["evidence_count"] = int(
            group["evidence_count"].astype(int).sum()
        )
        merged_row["anchor_exam_term_count"] = len(
            merged_lists["anchor_exam_term_ids_json"]
        )
        merged_row["maximum_candidate_score"] = int(
            group["maximum_candidate_score"].astype(int).max()
        )
        merged_row["touches_open_entity"] = bool(
            group["touches_open_entity"]
            .astype(str)
            .str.casefold()
            .eq("true")
            .any()
        )
        merged_duplicate_rows.append(merged_row)

    merged_duplicates = pd.DataFrame(
        merged_duplicate_rows,
        columns=combined.columns,
    )
    merged = pd.concat(
        [unique_rows, merged_duplicates],
        ignore_index=True,
    )
    merged = merged.sort_values(
        by=[
            "relation_family",
            "relation_type",
            "start_display_name",
            "end_display_name",
            identifier_column,
        ],
        kind="stable",
    ).reset_index(drop=True)
    relation_type_index = (
        int(merged.columns.get_loc("relation_type")) + 1
    )
    merged.insert(
        relation_type_index,
        "relation_display",
        (
            merged["start_display_name"]
            + " -["
            + merged["relation_type"]
            + "]-> "
            + merged["end_display_name"]
        ),
    )
    statistics = {
        "input_relation_candidate_count": len(combined),
        "unique_relation_candidate_count": len(merged),
        "merged_duplicate_row_count": len(combined) - len(merged),
        "total_evidence_count": int(
            merged["evidence_count"].astype(int).sum()
        ),
        "open_endpoint_relation_count": int(
            merged["touches_open_entity"]
            .astype(str)
            .str.casefold()
            .eq("true")
            .sum()
        ),
    }
    return merged, statistics


def run_merge_exam_term_nlp_relations(
    cli_args: Namespace,
) -> dict[str, object]:
    """Discover completed source outputs, merge them, and write CSV files."""
    input_root = Path(cli_args.input_root)
    output_directory = Path(cli_args.output_dir).resolve()
    candidate_paths: list[Path] = []
    for directory in sorted(
        input_root.glob(cli_args.input_directory_pattern)
    ):
        if directory.resolve() == output_directory:
            continue
        candidate_path = directory / cli_args.candidate_filename
        if candidate_path.is_file():
            candidate_paths.append(candidate_path)
    if not candidate_paths:
        raise FileNotFoundError(
            "No source-specific relation candidate CSV was found."
        )

    candidate_tables = [
        pd.read_csv(
            path,
            dtype=str,
            keep_default_na=False,
        )
        for path in candidate_paths
    ]
    merged, statistics = merge_candidate_tables(candidate_tables)
    output_directory.mkdir(parents=True, exist_ok=True)
    output_path = output_directory / cli_args.output_filename
    merged.to_csv(
        output_path,
        index=False,
        encoding="utf-8-sig",
    )

    summary_rows = [
        {
            "metric": "SOURCE_CANDIDATE_FILE_COUNT",
            "count": len(candidate_paths),
        },
        *[
            {
                "metric": metric.upper(),
                "count": value,
            }
            for metric, value in statistics.items()
        ],
    ]
    summary_path = (
        output_directory / cli_args.summary_filename
    )
    pd.DataFrame(summary_rows).to_csv(
        summary_path,
        index=False,
        encoding="utf-8-sig",
    )
    return {
        "status": "COMPLETED",
        "llm_used": False,
        "neo4j_load": False,
        "input_paths": [str(path) for path in candidate_paths],
        "output_path": str(output_path),
        "summary_path": str(summary_path),
        "statistics": statistics,
    }


def main() -> None:
    """Run the source relation candidate consolidation."""
    result = run_merge_exam_term_nlp_relations(parse_arguments())
    print(dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
