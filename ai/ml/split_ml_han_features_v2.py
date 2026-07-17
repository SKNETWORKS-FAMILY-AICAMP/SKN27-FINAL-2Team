"""Split ml_han_features_v2 into train/predict/answer/full files.

Input:
  ai/ml/output/ml_han_features_v2.json

Outputs:
  ai/ml/output/split_v2/*.json
  ai/ml/output/split_v2/*.csv
  ai/ml/output/split_v2/split_features_v2_report.md
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any


ROOT_DIR = Path(__file__).resolve().parents[2]
ML_DIR = Path(__file__).resolve().parent
OUT_DIR = ML_DIR / "output"
SPLIT_DIR = OUT_DIR / "split_v2"

FEATURE_JSON = OUT_DIR / "ml_han_features_v2.json"

TRAIN_JSON = SPLIT_DIR / "train_features_v2.json"
TRAIN_CSV = SPLIT_DIR / "train_features_v2.csv"
PREDICT_JSON = SPLIT_DIR / "predict_input_v2.json"
PREDICT_CSV = SPLIT_DIR / "predict_input_v2.csv"
ANSWER_JSON = SPLIT_DIR / "test_answer_v2.json"
ANSWER_CSV = SPLIT_DIR / "test_answer_v2.csv"
FULL_JSON = SPLIT_DIR / "full_features_v2.json"
FULL_CSV = SPLIT_DIR / "full_features_v2.csv"
REPORT_MD = SPLIT_DIR / "split_features_v2_report.md"

LABEL_COLUMNS = ["era", "topic", "topic_train", "question_type"]

FULL_COLUMNS = [
    "ml_sequence_index",
    "split",
    "round_no",
    "question_no",
    "problem_id",
    "data_source",
    "input_text",
    "keywords",
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

PREDICT_COLUMNS = FULL_COLUMNS

ANSWER_COLUMNS = [
    "ml_sequence_index",
    "round_no",
    "question_no",
    "problem_id",
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


def write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows({field: row.get(field, "") for field in fieldnames} for row in rows)


def build_model_text(row: dict[str, Any]) -> str:
    input_text = str(row.get("input_text") or "").strip()
    keywords = str(row.get("keywords") or "").strip()
    if keywords:
        return f"{input_text}\n\n[키워드] {keywords}"
    return input_text


def select_columns(row: dict[str, Any], columns: list[str]) -> dict[str, Any]:
    return {field: row.get(field, "") for field in columns}


def build_train_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output_rows = []
    for row in rows:
        if row.get("split") != "train":
            continue
        output = select_columns(row, FULL_COLUMNS)
        output["text"] = build_model_text(row)
        output_rows.append(output)
    return output_rows


def build_predict_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output_rows = []
    for row in rows:
        if row.get("split") != "test":
            continue
        output = select_columns(row, PREDICT_COLUMNS)
        for label in LABEL_COLUMNS:
            output[label] = ""
        output["text"] = build_model_text(row)
        output_rows.append(output)
    return output_rows


def build_answer_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [select_columns(row, ANSWER_COLUMNS) for row in rows if row.get("split") == "test"]


def build_full_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output_rows = []
    for row in rows:
        output = select_columns(row, FULL_COLUMNS)
        output["text"] = build_model_text(row)
        output_rows.append(output)
    return output_rows


def build_report(train_rows: list[dict[str, Any]], predict_rows: list[dict[str, Any]], answer_rows: list[dict[str, Any]], full_rows: list[dict[str, Any]]) -> str:
    lines = [
        "# Split Features v2",
        "",
        "## 목적",
        "",
        "- `ml_han_features_v2`를 기존 v1 구조와 같은 train/predict/answer/full 파일로 나눕니다.",
        "- `predict_input_v2`에서는 `era`, `topic`, `topic_train`, `question_type` 정답 라벨을 제거합니다.",
        "- `topic_train`은 GPT 추천 통합 라벨인 `topic_train_v2`를 최종 학습 라벨로 사용합니다.",
        "",
        "## 파일별 행 수",
        "",
        "| 파일 | 역할 | 행 수 |",
        "|---|---|---:|",
        f"| train_features_v2 | 학습용 | {len(train_rows)} |",
        f"| predict_input_v2 | 예측 입력용 | {len(predict_rows)} |",
        f"| test_answer_v2 | 평가 정답용 | {len(answer_rows)} |",
        f"| full_features_v2 | 원본 보관용 | {len(full_rows)} |",
        "",
    ]
    return "\n".join(lines)


def main() -> None:
    rows = read_json(FEATURE_JSON)
    SPLIT_DIR.mkdir(parents=True, exist_ok=True)

    train_rows = build_train_rows(rows)
    predict_rows = build_predict_rows(rows)
    answer_rows = build_answer_rows(rows)
    full_rows = build_full_rows(rows)

    write_json(TRAIN_JSON, train_rows)
    write_csv(TRAIN_CSV, train_rows, [*FULL_COLUMNS, "text"])
    write_json(PREDICT_JSON, predict_rows)
    write_csv(PREDICT_CSV, predict_rows, [*PREDICT_COLUMNS, "text"])
    write_json(ANSWER_JSON, answer_rows)
    write_csv(ANSWER_CSV, answer_rows, ANSWER_COLUMNS)
    write_json(FULL_JSON, full_rows)
    write_csv(FULL_CSV, full_rows, [*FULL_COLUMNS, "text"])

    REPORT_MD.write_text(build_report(train_rows, predict_rows, answer_rows, full_rows), encoding="utf-8")

    print(
        json.dumps(
            {
                "train_rows": len(train_rows),
                "predict_rows": len(predict_rows),
                "answer_rows": len(answer_rows),
                "full_rows": len(full_rows),
                "output_dir": SPLIT_DIR.relative_to(ROOT_DIR).as_posix(),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
