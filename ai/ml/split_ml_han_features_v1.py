# v1 피처 데이터를 학습용/예측용/정답용 파일로 분리하는 스크립트입니다.
# test 입력 파일에서는 era/topic/question_type 정답 라벨을 빈칸으로 제거합니다.
# 모델이 정답 라벨을 보고 예측한다는 의심을 없애기 위한 데이터 분리 단계입니다.
"""
Split ML_han v1 features into clear train, prediction input, and answer files.

Input:
  ai/ml/output/ml_han_features_v1.json

Outputs:
  ai/ml/output/split_v1/train_features_v1.json
  ai/ml/output/split_v1/train_features_v1.csv
  ai/ml/output/split_v1/predict_input_v1.json
  ai/ml/output/split_v1/predict_input_v1.csv
  ai/ml/output/split_v1/test_answer_v1.json
  ai/ml/output/split_v1/test_answer_v1.csv
  ai/ml/output/split_v1/full_features_v1.json
  ai/ml/output/split_v1/full_features_v1.csv
  ai/ml/output/split_v1/split_features_v1_report.md
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any


ROOT_DIR = Path(__file__).resolve().parents[2]
ML_DIR = Path(__file__).resolve().parent
OUT_DIR = ML_DIR / "output"
SPLIT_DIR = OUT_DIR / "split_v1"

FEATURE_JSON = OUT_DIR / "ml_han_features_v1.json"

TRAIN_JSON = SPLIT_DIR / "train_features_v1.json"
TRAIN_CSV = SPLIT_DIR / "train_features_v1.csv"
PREDICT_JSON = SPLIT_DIR / "predict_input_v1.json"
PREDICT_CSV = SPLIT_DIR / "predict_input_v1.csv"
ANSWER_JSON = SPLIT_DIR / "test_answer_v1.json"
ANSWER_CSV = SPLIT_DIR / "test_answer_v1.csv"
FULL_JSON = SPLIT_DIR / "full_features_v1.json"
FULL_CSV = SPLIT_DIR / "full_features_v1.csv"
REPORT_MD = SPLIT_DIR / "split_features_v1_report.md"

LABEL_COLUMNS = ["era", "topic", "question_type"]

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
    "question_type",
    "question_subtype",
    "core_concept",
]

PREDICT_COLUMNS = [
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
    "question_type",
    "question_subtype",
    "core_concept",
]

ANSWER_COLUMNS = [
    "ml_sequence_index",
    "round_no",
    "question_no",
    "problem_id",
    "era",
    "topic",
    "question_type",
    "question_subtype",
    "core_concept",
]


# JSON 파일을 UTF-8로 읽어 Python 객체로 변환합니다.
# 전체 v1 피처 데이터를 로드할 때 사용합니다.
# 파일이 없으면 예외가 발생해 경로 문제를 바로 확인할 수 있습니다.
def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


# JSON 파일을 보기 좋게 들여쓰기해서 저장합니다.
# ensure_ascii=False로 한국어 라벨을 그대로 보존합니다.
# CSV와 함께 Colab 입력 파일로 사용할 수 있는 산출물입니다.
def write_json(path: Path, rows: list[dict]) -> None:
    path.write_text(json.dumps(rows, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


# row 목록을 지정한 컬럼 순서로 CSV 저장합니다.
# utf-8-sig를 사용해 Excel/Colab 양쪽에서 한글 컬럼을 안정적으로 열 수 있게 합니다.
# 누락된 컬럼은 빈칸으로 저장합니다.
def write_csv(path: Path, rows: list[dict], fieldnames: list[str]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows({field: row.get(field, "") for field in fieldnames} for row in rows)


# 모델 입력용 text 컬럼을 생성합니다.
# input_text에 keywords를 붙여 baseline/딥러닝 모델이 동일한 입력을 쓰게 합니다.
# 정답 라벨은 절대 text에 포함하지 않습니다.
def build_model_text(row: dict) -> str:
    input_text = str(row.get("input_text") or "").strip()
    keywords = str(row.get("keywords") or "").strip()
    if keywords:
        return f"{input_text}\n\n[키워드] {keywords}"
    return input_text


# train row에 모델 입력 text 컬럼을 추가합니다.
# train에는 정답 라벨 era/topic/question_type을 그대로 유지합니다.
# 이 파일은 모델 학습에 사용됩니다.
def build_train_rows(rows: list[dict]) -> list[dict]:
    train_rows = []
    for row in rows:
        if row.get("split") != "train":
            continue
        output = {field: row.get(field, "") for field in FULL_COLUMNS}
        output["text"] = build_model_text(row)
        train_rows.append(output)
    return train_rows


# test row에서 예측 대상 라벨을 빈칸으로 제거합니다.
# input_text, keywords, question_subtype 등 참고 컬럼은 유지합니다.
# 이 파일은 모델이 예측할 때 입력으로만 사용됩니다.
def build_predict_rows(rows: list[dict]) -> list[dict]:
    predict_rows = []
    for row in rows:
        if row.get("split") != "test":
            continue
        output = {field: row.get(field, "") for field in PREDICT_COLUMNS}
        for label in LABEL_COLUMNS:
            output[label] = ""
        output["text"] = build_model_text(row)
        predict_rows.append(output)
    return predict_rows


# test row에서 평가용 정답 라벨만 분리합니다.
# 모델 입력 문장은 포함하지 않아 예측 단계에서 사용할 수 없게 합니다.
# 예측 결과와 비교해 성능 지표를 계산할 때 사용됩니다.
def build_answer_rows(rows: list[dict]) -> list[dict]:
    answer_rows = []
    for row in rows:
        if row.get("split") != "test":
            continue
        answer_rows.append({field: row.get(field, "") for field in ANSWER_COLUMNS})
    return answer_rows


# 전체 피처 데이터를 보관용 파일로 복사합니다.
# 원본 역할을 하며 train/test 정답 라벨을 모두 포함합니다.
# 분석이나 검증이 필요할 때만 참고합니다.
def build_full_rows(rows: list[dict]) -> list[dict]:
    full_rows = []
    for row in rows:
        output = {field: row.get(field, "") for field in FULL_COLUMNS}
        output["text"] = build_model_text(row)
        full_rows.append(output)
    return full_rows


# 분리된 파일들의 목적과 행 수를 Markdown으로 기록합니다.
# 이후 Colab에서 어떤 파일을 학습/예측/평가에 쓰는지 헷갈리지 않게 합니다.
# 정답 라벨 제거 여부도 함께 명시합니다.
def build_report(train_rows: list[dict], predict_rows: list[dict], answer_rows: list[dict], full_rows: list[dict]) -> str:
    lines = []
    lines.append("# Split Features v1")
    lines.append("")
    lines.append("## 목적")
    lines.append("")
    lines.append("- `train_features_v1`: 모델 학습용 데이터입니다. 정답 라벨을 포함합니다.")
    lines.append("- `predict_input_v1`: 모델 예측용 test 입력 데이터입니다. `era`, `topic`, `question_type`은 빈칸입니다.")
    lines.append("- `test_answer_v1`: 평가용 정답 데이터입니다. 예측 단계에서는 사용하지 않습니다.")
    lines.append("- `full_features_v1`: 원본 확인용 전체 피처 데이터입니다.")
    lines.append("")
    lines.append("## 파일별 행 수")
    lines.append("")
    lines.append("| 파일 | 역할 | 행 수 | 정답 라벨 포함 |")
    lines.append("|---|---|---:|---|")
    lines.append(f"| train_features_v1 | 학습용 | {len(train_rows)} | 포함 |")
    lines.append(f"| predict_input_v1 | 예측 입력용 | {len(predict_rows)} | era/topic/question_type 빈칸 |")
    lines.append(f"| test_answer_v1 | 평가 정답용 | {len(answer_rows)} | 포함 |")
    lines.append(f"| full_features_v1 | 원본 보관용 | {len(full_rows)} | 포함 |")
    lines.append("")
    lines.append("## 평가 흐름")
    lines.append("")
    lines.append("```text")
    lines.append("train_features_v1")
    lines.append("-> 모델 학습")
    lines.append("")
    lines.append("predict_input_v1")
    lines.append("-> 모델 예측")
    lines.append("-> pred_era / pred_topic / pred_question_type 생성")
    lines.append("")
    lines.append("test_answer_v1")
    lines.append("-> 예측 결과와 실제 정답 비교")
    lines.append("-> Accuracy / Macro F1 / Weighted F1 계산")
    lines.append("```")
    lines.append("")
    return "\n".join(lines)


# 전체 분리 작업을 실행합니다.
# split_v1 폴더에 JSON/CSV/리포트 파일을 생성합니다.
# 생성 후 행 수와 라벨 제거 여부를 콘솔에 출력합니다.
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
                "predict_label_blank_check": {
                    label: all(not row.get(label) for row in predict_rows)
                    for label in LABEL_COLUMNS
                },
                "output_dir": SPLIT_DIR.relative_to(ROOT_DIR).as_posix(),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
