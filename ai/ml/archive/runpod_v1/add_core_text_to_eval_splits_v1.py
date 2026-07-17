"""Build evaluation split files that include core_concept in model input.

This script copies eval_splits_v1 and adds text_with_core.
It is intended to test whether core_concept improves topic_train classification.
The original text field is preserved for baseline comparison.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
from pathlib import Path
from typing import Any


ROOT_DIR = Path(__file__).resolve().parents[2]
DEFAULT_SPLIT_DIR = ROOT_DIR / "ai" / "ml" / "output" / "eval_splits_v1"
DEFAULT_OUTPUT_DIR = ROOT_DIR / "ai" / "ml" / "output" / "eval_splits_with_core_v1"


CSV_COLUMNS = [
    "ml_sequence_index",
    "split",
    "eval_split",
    "round_no",
    "question_no",
    "problem_id",
    "era",
    "topic",
    "topic_train",
    "question_type",
    "question_subtype",
    "core_concept",
    "text",
    "text_with_core",
]


# Parse input and output paths.
# Defaults follow the current ai/ml output layout.
# The generated folder is separate from baseline splits.
def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Add core_concept text to ML evaluation split JSON files.")
    parser.add_argument("--split-dir", type=Path, default=DEFAULT_SPLIT_DIR, help="Existing eval_splits_v1 directory.")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR, help="Output directory.")
    return parser.parse_args()


# Load a list-shaped JSON file from disk.
# Split files are expected to contain a list of row dictionaries.
# A clear error helps catch wrong RunPod/local paths early.
def read_json_rows(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        raise FileNotFoundError(f"file not found: {path}")
    rows = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(rows, list):
        raise ValueError(f"expected list json: {path}")
    return rows


# Normalize whitespace while keeping the original Korean text.
# Some extracted fields contain line breaks or repeated spaces.
# Model input becomes more consistent after this light cleanup.
def normalize_text(value: Any) -> str:
    text = "" if value is None else str(value)
    return re.sub(r"\s+", " ", text).strip()


# Compose the model input using question text plus core_concept.
# The core concept is placed after the main text as an explicit hint field.
# Empty or unclassified values are skipped to avoid adding noise.
def build_text_with_core(row: dict[str, Any]) -> str:
    base_text = normalize_text(row.get("text") or row.get("input_text") or row.get("question"))
    core_concept = normalize_text(row.get("core_concept"))

    if not core_concept or core_concept == "미분류":
        return base_text

    return f"{base_text}\n\n[Core Concept]\n{core_concept}"


# Write a CSV mirror for manual inspection.
# JSON is the training input; CSV is useful for quick QA in Excel.
# utf-8-sig keeps Korean readable in spreadsheet tools.
def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in CSV_COLUMNS})


# Process one split folder such as split_time_v1.
# Adds text_with_core to train.json and test.json.
# Summary values are returned for the final report.
def process_split_folder(split_folder: Path, output_folder: Path) -> dict[str, Any]:
    output_folder.mkdir(parents=True, exist_ok=True)
    summary: dict[str, Any] = {
        "split_name": split_folder.name,
        "files": {},
        "missing_core_concept": 0,
        "total_rows": 0,
    }

    for filename in ("train.json", "test.json"):
        input_path = split_folder / filename
        if not input_path.exists():
            continue

        rows = read_json_rows(input_path)
        output_rows: list[dict[str, Any]] = []
        missing_core_concept = 0

        for row in rows:
            core_concept = normalize_text(row.get("core_concept"))
            if not core_concept or core_concept == "미분류":
                missing_core_concept += 1

            output_row = dict(row)
            output_row["input_mode"] = "with_core"
            output_row["text_with_core"] = build_text_with_core(row)
            output_rows.append(output_row)

        output_json = output_folder / filename
        output_csv = output_folder / filename.replace(".json", ".csv")
        output_json.write_text(json.dumps(output_rows, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        write_csv(output_csv, output_rows)

        summary["files"][filename] = {
            "rows": len(output_rows),
            "missing_core_concept": missing_core_concept,
            "json": str(output_json),
            "csv": str(output_csv),
        }
        summary["missing_core_concept"] += missing_core_concept
        summary["total_rows"] += len(output_rows)

    return summary


# Write a Markdown report for the generated split folder.
# This gives the RunPod upload path and experiment purpose.
# It also records how many rows had no usable core_concept.
def write_report(path: Path, summaries: list[dict[str, Any]]) -> None:
    lines = [
        "# Eval Splits With Core v1",
        "",
        "## Purpose",
        "- Add `core_concept` to model input for topic/topic_train experiments.",
        "- Keep the original `text` field unchanged for baseline comparison.",
        "- New input field: `text_with_core`.",
        "",
        "## Files",
        "| split | file | rows | missing core_concept |",
        "|---|---:|---:|---:|",
    ]

    for summary in summaries:
        for filename, file_info in summary["files"].items():
            lines.append(
                f"| {summary['split_name']} | {filename} | {file_info['rows']} | "
                f"{file_info['missing_core_concept']} |"
            )

    lines.extend(
        [
            "",
            "## RunPod Use",
            "- Upload this folder as `/workspace/common/eval_splits_with_core_v1`.",
            "- In the core experiment notebook, use `INPUT_TEXT_FIELD = 'text_with_core'`.",
            "- Recommended first target: `TARGET = 'topic_train'`.",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    args = parse_args()
    if not args.split_dir.exists():
        raise FileNotFoundError(f"split dir not found: {args.split_dir}")

    args.output_dir.mkdir(parents=True, exist_ok=True)
    summaries: list[dict[str, Any]] = []
    for split_folder in sorted(path for path in args.split_dir.iterdir() if path.is_dir()):
        summaries.append(process_split_folder(split_folder, args.output_dir / split_folder.name))

    report_path = args.output_dir / "with_core_report_v1.md"
    write_report(report_path, summaries)

    print(f"output_dir: {args.output_dir}")
    print(f"report: {report_path}")
    for summary in summaries:
        print(
            f"{summary['split_name']}: rows={summary['total_rows']} "
            f"missing_core={summary['missing_core_concept']}"
        )


if __name__ == "__main__":
    main()
