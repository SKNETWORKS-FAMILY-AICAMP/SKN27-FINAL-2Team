"""Build evaluation split files that include answer choice text.

This script joins the current ML evaluation split rows with ML_han_v1.json.
It adds a new text_with_choices field for experiments that use choices as input.
Correct-answer metadata is never included in the generated model input.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
from pathlib import Path
from typing import Any


ROOT_DIR = Path(__file__).resolve().parents[2]
DEFAULT_RAW_JSON = ROOT_DIR / "ai" / "ml" / "ML_han_v1.json"
DEFAULT_SPLIT_DIR = ROOT_DIR / "ai" / "ml" / "output" / "eval_splits_v1"
DEFAULT_OUTPUT_DIR = ROOT_DIR / "ai" / "ml" / "output" / "eval_splits_with_choices_v1"


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
    "choice_count",
    "text",
    "text_with_choices",
]


# Parse CLI arguments for source paths and output path.
# Defaults are aligned with the current ai/ml folder layout.
# The generated files are separate from the existing split files.
def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Add choice text to ML evaluation split JSON files.")
    parser.add_argument("--raw-json", type=Path, default=DEFAULT_RAW_JSON, help="ML_han_v1.json path.")
    parser.add_argument("--split-dir", type=Path, default=DEFAULT_SPLIT_DIR, help="Existing eval_splits_v1 directory.")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR, help="Output directory.")
    return parser.parse_args()


# Read JSON from disk and validate that the top-level value is a list.
# The split and raw source files are both list-shaped JSON files.
# Returning a concrete list keeps later row processing simple.
def read_json_rows(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        raise FileNotFoundError(f"file not found: {path}")
    rows = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(rows, list):
        raise ValueError(f"expected list json: {path}")
    return rows


# Collapse repeated whitespace while preserving Korean and punctuation.
# OCR/extracted text can contain many line breaks and irregular spaces.
# The model input should stay readable without leaking answer metadata.
def normalize_text(value: Any) -> str:
    text = "" if value is None else str(value)
    return re.sub(r"\s+", " ", text).strip()


# Build lookup maps from the raw ML_han_v1 rows.
# problem_id is preferred because it is stable across split versions.
# The round/question fallback protects older rows that may not have problem_id.
def build_raw_index(raw_rows: list[dict[str, Any]]) -> tuple[dict[str, dict[str, Any]], dict[tuple[int, int], dict[str, Any]]]:
    by_problem_id: dict[str, dict[str, Any]] = {}
    by_round_question: dict[tuple[int, int], dict[str, Any]] = {}

    for row in raw_rows:
        problem_id = normalize_text(row.get("problem_id"))
        if problem_id:
            by_problem_id[problem_id] = row

        round_no = row.get("round_no")
        question_no = row.get("question_no")
        if round_no is not None and question_no is not None:
            by_round_question[(int(round_no), int(question_no))] = row

    return by_problem_id, by_round_question


# Find the matching raw source row for a split row.
# problem_id is used first, then round_no/question_no.
# Returning None lets the caller report missing rows without stopping immediately.
def find_raw_row(
    row: dict[str, Any],
    by_problem_id: dict[str, dict[str, Any]],
    by_round_question: dict[tuple[int, int], dict[str, Any]],
) -> dict[str, Any] | None:
    problem_id = normalize_text(row.get("problem_id"))
    if problem_id and problem_id in by_problem_id:
        return by_problem_id[problem_id]

    round_no = row.get("round_no")
    question_no = row.get("question_no")
    if round_no is not None and question_no is not None:
        return by_round_question.get((int(round_no), int(question_no)))

    return None


# Extract only the visible choice text from a raw row.
# is_answer, answer_no, answer_choice, and explanations are deliberately ignored.
# This prevents label leakage in the choice-included input experiment.
def extract_choice_texts(raw_row: dict[str, Any] | None) -> list[str]:
    if not raw_row:
        return []

    choices = raw_row.get("choices")
    if isinstance(choices, list):
        texts = []
        for choice in choices:
            if isinstance(choice, dict):
                text = normalize_text(choice.get("content"))
            else:
                text = normalize_text(choice)
            if text:
                texts.append(text)
        if texts:
            return texts

    distractors = raw_row.get("distractor_choices")
    if isinstance(distractors, list):
        return [normalize_text(choice) for choice in distractors if normalize_text(choice)]

    return []


# Compose the model input that includes question text and visible choices.
# The original text field remains unchanged for baseline comparison.
# Choice numbers are ordinal only and do not indicate correctness.
def build_text_with_choices(row: dict[str, Any], choice_texts: list[str]) -> str:
    base_text = normalize_text(row.get("text") or row.get("input_text") or row.get("question"))
    if not choice_texts:
        return base_text

    choice_lines = [f"{idx}. {text}" for idx, text in enumerate(choice_texts, start=1)]
    return f"{base_text}\n\n[Choices]\n" + "\n".join(choice_lines)


# Write a CSV mirror for quick inspection in Excel.
# JSON is the training source; CSV is mainly for manual QA.
# utf-8-sig is used so Korean opens cleanly in Excel.
def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in CSV_COLUMNS})


# Process one split folder such as split_time_v1.
# train.json and test.json are copied with the added text_with_choices field.
# A compact summary is returned for the final Markdown report.
def process_split_folder(
    split_folder: Path,
    output_folder: Path,
    by_problem_id: dict[str, dict[str, Any]],
    by_round_question: dict[tuple[int, int], dict[str, Any]],
) -> dict[str, Any]:
    output_folder.mkdir(parents=True, exist_ok=True)
    summary: dict[str, Any] = {
        "split_name": split_folder.name,
        "files": {},
        "missing_raw_rows": 0,
        "missing_choices": 0,
        "total_rows": 0,
    }

    for filename in ("train.json", "test.json"):
        input_path = split_folder / filename
        if not input_path.exists():
            continue

        rows = read_json_rows(input_path)
        output_rows: list[dict[str, Any]] = []
        missing_raw_rows = 0
        missing_choices = 0

        for row in rows:
            raw_row = find_raw_row(row, by_problem_id, by_round_question)
            if raw_row is None:
                missing_raw_rows += 1

            choice_texts = extract_choice_texts(raw_row)
            if not choice_texts:
                missing_choices += 1

            output_row = dict(row)
            output_row["input_mode"] = "with_choices"
            output_row["choice_count"] = len(choice_texts)
            output_row["text_with_choices"] = build_text_with_choices(row, choice_texts)
            output_rows.append(output_row)

        output_json = output_folder / filename
        output_csv = output_folder / filename.replace(".json", ".csv")
        output_json.write_text(json.dumps(output_rows, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        write_csv(output_csv, output_rows)

        summary["files"][filename] = {
            "rows": len(output_rows),
            "missing_raw_rows": missing_raw_rows,
            "missing_choices": missing_choices,
            "json": str(output_json),
            "csv": str(output_csv),
        }
        summary["missing_raw_rows"] += missing_raw_rows
        summary["missing_choices"] += missing_choices
        summary["total_rows"] += len(output_rows)

    return summary


# Create a Markdown report describing generated files and leakage safeguards.
# This gives a quick checklist before uploading the split folder to RunPod.
# The report is intentionally short enough to use as an experiment note.
def write_report(path: Path, summaries: list[dict[str, Any]]) -> None:
    lines = [
        "# Eval Splits With Choices v1",
        "",
        "## Purpose",
        "- Add visible answer-choice text to model input for topic/topic_train experiments.",
        "- Keep the original `text` field unchanged for baseline comparison.",
        "- Do not include `is_answer`, `answer_no`, `answer_choice`, or explanations in `text_with_choices`.",
        "",
        "## Files",
        "| split | file | rows | missing raw | missing choices |",
        "|---|---:|---:|---:|---:|",
    ]

    for summary in summaries:
        for filename, file_info in summary["files"].items():
            lines.append(
                f"| {summary['split_name']} | {filename} | {file_info['rows']} | "
                f"{file_info['missing_raw_rows']} | {file_info['missing_choices']} |"
            )

    lines.extend(
        [
            "",
            "## RunPod Use",
            "- Upload this folder as `/workspace/common/eval_splits_with_choices_v1`.",
            "- In the choice experiment notebook, use `INPUT_TEXT_FIELD = 'text_with_choices'`.",
            "- Recommended first target: `TARGET = 'topic_train'`.",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    args = parse_args()
    raw_rows = read_json_rows(args.raw_json)
    by_problem_id, by_round_question = build_raw_index(raw_rows)

    if not args.split_dir.exists():
        raise FileNotFoundError(f"split dir not found: {args.split_dir}")

    args.output_dir.mkdir(parents=True, exist_ok=True)
    summaries: list[dict[str, Any]] = []
    for split_folder in sorted(path for path in args.split_dir.iterdir() if path.is_dir()):
        summaries.append(
            process_split_folder(
                split_folder,
                args.output_dir / split_folder.name,
                by_problem_id,
                by_round_question,
            )
        )

    report_path = args.output_dir / "with_choices_report_v1.md"
    write_report(report_path, summaries)

    print(f"output_dir: {args.output_dir}")
    print(f"report: {report_path}")
    for summary in summaries:
        print(
            f"{summary['split_name']}: rows={summary['total_rows']} "
            f"missing_raw={summary['missing_raw_rows']} missing_choices={summary['missing_choices']}"
        )


if __name__ == "__main__":
    main()
