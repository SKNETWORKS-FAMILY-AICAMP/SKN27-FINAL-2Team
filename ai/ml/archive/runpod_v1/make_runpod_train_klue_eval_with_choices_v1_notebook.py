"""Create a RunPod notebook for KLUE/RoBERTa experiments with choices.

The notebook is derived from runpod_train_klue_eval_v1.ipynb.
It reads eval_splits_with_choices_v1 and trains with text_with_choices.
Use it when testing whether visible answer choices improve topic classification.
"""

from __future__ import annotations

import json
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[2]
SOURCE_NOTEBOOK = ROOT_DIR / "ai" / "ml" / "runpod_train_klue_eval_v1.ipynb"
OUTPUT_NOTEBOOK = ROOT_DIR / "ai" / "ml" / "runpod_train_klue_eval_with_choices_v1.ipynb"


# Replace exact code fragments in a notebook cell source.
# Failing fast is useful because notebook code changes can silently break experiments.
# The generated notebook keeps the old baseline notebook untouched.
def replace_or_fail(source: str, old: str, new: str) -> str:
    if old not in source:
        raise ValueError(f"source fragment not found:\n{old}")
    return source.replace(old, new)


# Apply the choice-input changes to notebook code cells.
# The core training logic stays the same; only split root, output name, and input field change.
# This keeps results comparable with the baseline experiment.
def transform_cell_source(source: str) -> str:
    replacements = [
        (
            "OUTPUT_ROOT = BASE_DIR / 'output' / 'klue_eval_v1'",
            "OUTPUT_ROOT = BASE_DIR / 'output' / 'klue_eval_with_choices_v1'",
        ),
        (
            "SPLIT_DIR = COMMON_DIR / 'eval_splits_v1' / SPLIT_NAME",
            "SPLIT_DIR = COMMON_DIR / 'eval_splits_with_choices_v1' / SPLIT_NAME",
        ),
        (
            "TARGET = 'topic_train'",
            "TARGET = 'topic_train'\nINPUT_TEXT_FIELD = 'text_with_choices'",
        ),
        (
            "RUN_NAME = f\"{TARGET}_{SPLIT_NAME.replace('split_', '')}\"",
            "RUN_NAME = f\"{TARGET}_with_choices_{SPLIT_NAME.replace('split_', '')}\"",
        ),
        (
            "def get_text(row: dict[str, Any]) -> str:\n    return str(row.get('text') or row.get('input_text') or row.get('question') or '').strip()",
            "def get_text(row: dict[str, Any]) -> str:\n    return str(row.get(INPUT_TEXT_FIELD) or row.get('text') or row.get('input_text') or row.get('question') or '').strip()",
        ),
        (
            "print('TARGET =', TARGET)",
            "print('TARGET =', TARGET)\nprint('INPUT_TEXT_FIELD =', INPUT_TEXT_FIELD)",
        ),
    ]

    for old, new in replacements:
        if old in source:
            source = source.replace(old, new)
    return source


# Update markdown headings so the RunPod tab is easy to distinguish.
# Only user-facing labels are changed; no training logic lives in markdown cells.
# This avoids accidentally overwriting baseline results.
def transform_markdown_source(source: str) -> str:
    return source.replace("KLUE/RoBERTa Eval v1", "KLUE/RoBERTa Eval With Choices v1")


def main() -> None:
    if not SOURCE_NOTEBOOK.exists():
        raise FileNotFoundError(f"source notebook not found: {SOURCE_NOTEBOOK}")

    notebook = json.loads(SOURCE_NOTEBOOK.read_text(encoding="utf-8"))
    for cell in notebook.get("cells", []):
        source = "".join(cell.get("source", []))
        if cell.get("cell_type") == "code":
            source = transform_cell_source(source)
        elif cell.get("cell_type") == "markdown":
            source = transform_markdown_source(source)
        cell["source"] = source.splitlines(keepends=True)

    OUTPUT_NOTEBOOK.write_text(json.dumps(notebook, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"notebook: {OUTPUT_NOTEBOOK}")


if __name__ == "__main__":
    main()
