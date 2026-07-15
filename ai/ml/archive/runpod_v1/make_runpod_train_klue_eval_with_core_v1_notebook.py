"""Create a RunPod notebook for KLUE/RoBERTa experiments with core_concept.

The notebook is derived from runpod_train_klue_eval_v1.ipynb.
It reads eval_splits_with_core_v1 and trains with text_with_core.
Use it to test whether core_concept improves topic_train classification.
"""

from __future__ import annotations

import json
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[2]
SOURCE_NOTEBOOK = ROOT_DIR / "ai" / "ml" / "runpod_train_klue_eval_v1.ipynb"
OUTPUT_NOTEBOOK = ROOT_DIR / "ai" / "ml" / "runpod_train_klue_eval_with_core_v1.ipynb"


# Apply the core-input changes to notebook code cells.
# The training flow stays identical to baseline for fair comparison.
# Only split root, output root, run name, and input field are changed.
def transform_cell_source(source: str) -> str:
    replacements = [
        (
            "OUTPUT_ROOT = BASE_DIR / 'output' / 'klue_eval_v1'",
            "OUTPUT_ROOT = BASE_DIR / 'output' / 'klue_eval_with_core_v1'",
        ),
        (
            "SPLIT_DIR = COMMON_DIR / 'eval_splits_v1' / SPLIT_NAME",
            "SPLIT_DIR = COMMON_DIR / 'eval_splits_with_core_v1' / SPLIT_NAME",
        ),
        (
            "TARGET = 'topic_train'",
            "TARGET = 'topic_train'\nINPUT_TEXT_FIELD = 'text_with_core'",
        ),
        (
            "RUN_NAME = f\"{TARGET}_{SPLIT_NAME.replace('split_', '')}\"",
            "RUN_NAME = f\"{TARGET}_with_core_{SPLIT_NAME.replace('split_', '')}\"",
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


# Update markdown labels so the notebook is easy to identify in RunPod.
# Markdown changes are cosmetic only.
# Keeping a separate notebook prevents overwriting baseline settings.
def transform_markdown_source(source: str) -> str:
    return source.replace("KLUE/RoBERTa Eval v1", "KLUE/RoBERTa Eval With Core v1")


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
