"""Build topic_train-ready v2 feature files.

Unlike v1, ml_han_features_v2 already contains a GPT-recommended
`topic_train_v2`. This script keeps both `topic_train_v1` and
`topic_train_v2`, while setting `topic_train` to the v2 recommendation for
downstream training.
"""

from __future__ import annotations

import csv
import json
from collections import Counter
from pathlib import Path
from typing import Any


ROOT_DIR = Path(__file__).resolve().parents[2]
ML_DIR = Path(__file__).resolve().parent
INPUT_DIR = ML_DIR / "output" / "split_v2"
OUTPUT_DIR = ML_DIR / "output" / "split_topic_merged_v2"

TRAIN_INPUT_JSON = INPUT_DIR / "train_features_v2.json"
PREDICT_INPUT_JSON = INPUT_DIR / "predict_input_v2.json"
ANSWER_INPUT_JSON = INPUT_DIR / "test_answer_v2.json"
FULL_INPUT_JSON = INPUT_DIR / "full_features_v2.json"

TRAIN_OUTPUT_JSON = OUTPUT_DIR / "train_features_topic_merged_v2.json"
TRAIN_OUTPUT_CSV = OUTPUT_DIR / "train_features_topic_merged_v2.csv"
PREDICT_OUTPUT_JSON = OUTPUT_DIR / "predict_input_topic_merged_v2.json"
PREDICT_OUTPUT_CSV = OUTPUT_DIR / "predict_input_topic_merged_v2.csv"
ANSWER_OUTPUT_JSON = OUTPUT_DIR / "test_answer_topic_merged_v2.json"
ANSWER_OUTPUT_CSV = OUTPUT_DIR / "test_answer_topic_merged_v2.csv"
FULL_OUTPUT_JSON = OUTPUT_DIR / "full_features_topic_merged_v2.json"
FULL_OUTPUT_CSV = OUTPUT_DIR / "full_features_topic_merged_v2.csv"
REPORT_MD = OUTPUT_DIR / "topic_merge_report_v2.md"

OUTPUT_COLUMNS = [
    "ml_sequence_index",
    "split",
    "round_no",
    "question_no",
    "problem_id",
    "data_source",
    "input_text",
    "keywords",
    "text",
    "era",
    "topic",
    "topic_train",
    "topic_train_v1",
    "topic_train_v2",
    "question_type",
    "question_subtype",
    "core_concept",
    "label_confidence",
    "ambiguous_flag",
    "label_reason",
    "review_model",
]


def read_json(path: Path) -> list[dict[str, Any]]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, rows: list[dict[str, Any]]) -> None:
    path.write_text(json.dumps(rows, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=OUTPUT_COLUMNS)
        writer.writeheader()
        writer.writerows({field: row.get(field, "") for field in OUTPUT_COLUMNS} for row in rows)


def transform_row(row: dict[str, Any], *, blank_topic_train: bool = False) -> dict[str, Any]:
    output = dict(row)
    output["topic_train"] = "" if blank_topic_train else str(row.get("topic_train_v2") or row.get("topic_train") or "").strip()
    return output


def transform_rows(rows: list[dict[str, Any]], *, blank_topic_train: bool = False) -> list[dict[str, Any]]:
    return [transform_row(row, blank_topic_train=blank_topic_train) for row in rows]


def count_values(rows: list[dict[str, Any]], key: str) -> Counter[str]:
    return Counter(str(row.get(key) or "").strip() for row in rows)


def count_table_lines(counts: Counter[str]) -> list[str]:
    total = sum(counts.values())
    lines = ["| 라벨 | 건수 | 비율 |", "|---|---:|---:|"]
    for label, count in counts.most_common():
        pct = 0 if total == 0 else count / total * 100
        lines.append(f"| {label} | {count} | {pct:.1f}% |")
    return lines


def build_report(train_rows: list[dict[str, Any]], answer_rows: list[dict[str, Any]], full_rows: list[dict[str, Any]]) -> str:
    lines = [
        "# Topic Merge v2 Report",
        "",
        "## 목적",
        "",
        "- GPT 재분류 결과의 `topic_train_v2`를 최종 학습용 `topic_train`으로 사용합니다.",
        "- 기존 매핑 기준 `topic_train_v1`은 비교용으로 보존합니다.",
        "- `label_reason`, `label_confidence`, `ambiguous_flag`는 라벨 품질 검토용 메타데이터입니다.",
        "",
        "## Train topic_train_v2 분포",
        "",
        *count_table_lines(count_values(train_rows, "topic_train_v2")),
        "",
        "## Test 정답 topic_train_v2 분포",
        "",
        *count_table_lines(count_values(answer_rows, "topic_train_v2")),
        "",
        "## 전체 topic_train_v2 분포",
        "",
        *count_table_lines(count_values(full_rows, "topic_train_v2")),
        "",
        "## 전체 confidence 분포",
        "",
        *count_table_lines(count_values(full_rows, "label_confidence")),
        "",
        "## 전체 ambiguous 분포",
        "",
        *count_table_lines(count_values(full_rows, "ambiguous_flag")),
        "",
    ]
    return "\n".join(lines)


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    train_rows = transform_rows(read_json(TRAIN_INPUT_JSON))
    predict_rows = transform_rows(read_json(PREDICT_INPUT_JSON), blank_topic_train=True)
    answer_rows = transform_rows(read_json(ANSWER_INPUT_JSON))
    full_rows = transform_rows(read_json(FULL_INPUT_JSON))

    write_json(TRAIN_OUTPUT_JSON, train_rows)
    write_csv(TRAIN_OUTPUT_CSV, train_rows)
    write_json(PREDICT_OUTPUT_JSON, predict_rows)
    write_csv(PREDICT_OUTPUT_CSV, predict_rows)
    write_json(ANSWER_OUTPUT_JSON, answer_rows)
    write_csv(ANSWER_OUTPUT_CSV, answer_rows)
    write_json(FULL_OUTPUT_JSON, full_rows)
    write_csv(FULL_OUTPUT_CSV, full_rows)
    REPORT_MD.write_text(build_report(train_rows, answer_rows, full_rows), encoding="utf-8")

    print(f"saved: {OUTPUT_DIR.relative_to(ROOT_DIR)}")
    print(f"train rows: {len(train_rows)}")
    print(f"predict rows: {len(predict_rows)}")
    print(f"answer rows: {len(answer_rows)}")
    print("topic_train_v2 train counts:", dict(count_values(train_rows, "topic_train_v2").most_common()))


if __name__ == "__main__":
    main()
