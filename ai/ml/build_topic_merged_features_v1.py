# split_v1 데이터를 읽어 주제 통합 학습용 데이터를 만드는 파일입니다.
# 원본 topic은 보존하고, 모델 학습용 topic_train 컬럼만 추가합니다.
# 경제/사회/군사/외교는 정치로, 사상·종교는 문화로 통합합니다.
"""
Build topic-merged feature files for ML experiments.

Input:
  ai/ml/output/split_v1/*.json

Outputs:
  ai/ml/output/split_topic_merged_v1/train_features_topic_merged_v1.json
  ai/ml/output/split_topic_merged_v1/predict_input_topic_merged_v1.json
  ai/ml/output/split_topic_merged_v1/test_answer_topic_merged_v1.json
  ai/ml/output/split_topic_merged_v1/full_features_topic_merged_v1.json
  ai/ml/output/split_topic_merged_v1/topic_merge_report_v1.md

Run:
  python ai/ml/build_topic_merged_features_v1.py
"""

from __future__ import annotations

import csv
import json
from collections import Counter
from pathlib import Path
from typing import Any


ROOT_DIR = Path(__file__).resolve().parents[2]
ML_DIR = Path(__file__).resolve().parent
INPUT_DIR = ML_DIR / "output" / "split_v1"
OUTPUT_DIR = ML_DIR / "output" / "split_topic_merged_v1"

TRAIN_INPUT_JSON = INPUT_DIR / "train_features_v1.json"
PREDICT_INPUT_JSON = INPUT_DIR / "predict_input_v1.json"
ANSWER_INPUT_JSON = INPUT_DIR / "test_answer_v1.json"
FULL_INPUT_JSON = INPUT_DIR / "full_features_v1.json"

TRAIN_OUTPUT_JSON = OUTPUT_DIR / "train_features_topic_merged_v1.json"
TRAIN_OUTPUT_CSV = OUTPUT_DIR / "train_features_topic_merged_v1.csv"
PREDICT_OUTPUT_JSON = OUTPUT_DIR / "predict_input_topic_merged_v1.json"
PREDICT_OUTPUT_CSV = OUTPUT_DIR / "predict_input_topic_merged_v1.csv"
ANSWER_OUTPUT_JSON = OUTPUT_DIR / "test_answer_topic_merged_v1.json"
ANSWER_OUTPUT_CSV = OUTPUT_DIR / "test_answer_topic_merged_v1.csv"
FULL_OUTPUT_JSON = OUTPUT_DIR / "full_features_topic_merged_v1.json"
FULL_OUTPUT_CSV = OUTPUT_DIR / "full_features_topic_merged_v1.csv"
REPORT_MD = OUTPUT_DIR / "topic_merge_report_v1.md"

TOPIC_MERGE_MAP = {
    "정치": "정치",
    "경제": "정치",
    "사회": "정치",
    "군사": "정치",
    "외교": "정치",
    "문화": "문화",
    "사상·종교": "문화",
    "인물": "인물",
    "사건": "사건",
    "제도": "제도",
}

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
    "question_type",
    "question_subtype",
    "core_concept",
]


# JSON 파일을 UTF-8로 읽어 Python 객체로 변환합니다.
# split_v1의 train/predict/test_answer/full 파일을 로드할 때 사용합니다.
# 파일이 없거나 JSON 형식이 깨져 있으면 즉시 예외가 발생해 원인을 확인할 수 있습니다.
def read_json(path: Path) -> list[dict[str, Any]]:
    return json.loads(path.read_text(encoding="utf-8"))


# JSON 파일을 사람이 확인하기 쉬운 들여쓰기 형식으로 저장합니다.
# ensure_ascii=False를 사용해 한국어 라벨이 깨지지 않게 보존합니다.
# Colab과 로컬 스크립트에서 모두 같은 입력 파일로 사용할 수 있습니다.
def write_json(path: Path, rows: list[dict[str, Any]]) -> None:
    path.write_text(json.dumps(rows, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


# CSV 파일을 Excel에서 바로 열기 좋게 utf-8-sig로 저장합니다.
# 지정한 fieldnames 순서대로 컬럼을 고정해 train/predict/answer 파일 구조를 맞춥니다.
# 없는 값은 빈 문자열로 채워 파일 간 컬럼 누락을 방지합니다.
def write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows({field: row.get(field, "") for field in fieldnames} for row in rows)


# 원본 topic을 학습용 통합 topic_train으로 변환합니다.
# 기존 topic 라벨 자체는 변경하지 않고, 통합된 결과만 별도 컬럼으로 만듭니다.
# 매핑표에 없는 라벨은 원본 값을 유지해 예기치 않은 라벨 손실을 막습니다.
def merge_topic(topic: Any) -> str:
    topic_text = str(topic or "").strip()
    return TOPIC_MERGE_MAP.get(topic_text, topic_text)


# row 하나에 topic_original 보존과 topic_train 추가 처리를 적용합니다.
# topic 컬럼은 원본 그대로 두고, 모델 학습/평가용 target으로 topic_train을 사용합니다.
# predict 입력 파일은 정답 topic_train을 빈칸으로 비워 데이터 누수를 막습니다.
def transform_row(row: dict[str, Any], *, blank_topic_train: bool = False) -> dict[str, Any]:
    output = dict(row)
    output["topic_train"] = "" if blank_topic_train else merge_topic(row.get("topic"))
    return output


# row 목록 전체에 topic_train 변환을 적용합니다.
# train/full/test_answer에는 정답 topic_train을 포함하고 predict_input에는 빈칸을 넣습니다.
# 이렇게 해야 학습과 평가는 통합 라벨로 하되 예측 단계에서는 정답 라벨을 보지 않습니다.
def transform_rows(rows: list[dict[str, Any]], *, blank_topic_train: bool = False) -> list[dict[str, Any]]:
    return [transform_row(row, blank_topic_train=blank_topic_train) for row in rows]


# split별 원본 topic과 통합 topic_train 분포를 계산합니다.
# 통합 전후 클래스 수와 비율 변화를 확인하기 위한 보고서에 사용합니다.
# train/test 분포를 함께 봐야 평가셋에서 특정 라벨이 사라지는지 확인할 수 있습니다.
def count_topics(rows: list[dict[str, Any]], key: str) -> Counter[str]:
    return Counter(str(row.get(key) or "").strip() for row in rows)


# 카운트 값을 Markdown 표 행으로 변환합니다.
# 전체 건수 대비 비율을 함께 보여 통합 후 5% 미만 라벨이 남는지 확인합니다.
# 빈 데이터가 들어오면 비율은 0으로 처리합니다.
def count_table_lines(counts: Counter[str]) -> list[str]:
    total = sum(counts.values())
    lines = ["| 라벨 | 건수 | 비율 |", "|---|---:|---:|"]
    for label, count in counts.most_common():
        pct = 0 if total == 0 else count / total * 100
        lines.append(f"| {label} | {count} | {pct:.1f}% |")
    return lines


# topic 통합 규칙과 통합 전후 분포를 Markdown 보고서로 작성합니다.
# 회의/발표에서 라벨 의미 왜곡을 설명할 수 있도록 매핑표를 명시합니다.
# 모델 target은 topic이 아니라 topic_train이라는 점을 분명히 남깁니다.
def build_report(
    train_rows: list[dict[str, Any]],
    answer_rows: list[dict[str, Any]],
    full_rows: list[dict[str, Any]],
) -> str:
    lines: list[str] = []
    lines.append("# Topic Merge v1 Report")
    lines.append("")
    lines.append("## 목적")
    lines.append("")
    lines.append("- 원본 `topic`은 보존합니다.")
    lines.append("- 모델 학습용 통합 라벨 `topic_train`을 추가합니다.")
    lines.append("- `question_type`은 이번 통합 대상이 아니며, 이후 학습 target에서 제외하는 방향으로 검토합니다.")
    lines.append("")
    lines.append("## 통합 규칙")
    lines.append("")
    lines.append("| 원본 topic | 학습용 topic_train |")
    lines.append("|---|---|")
    for source, target in TOPIC_MERGE_MAP.items():
        lines.append(f"| {source} | {target} |")
    lines.append("")
    lines.append("## Train 원본 topic 분포")
    lines.append("")
    lines.extend(count_table_lines(count_topics(train_rows, "topic")))
    lines.append("")
    lines.append("## Train 통합 topic_train 분포")
    lines.append("")
    lines.extend(count_table_lines(count_topics(train_rows, "topic_train")))
    lines.append("")
    lines.append("## Test 정답 원본 topic 분포")
    lines.append("")
    lines.extend(count_table_lines(count_topics(answer_rows, "topic")))
    lines.append("")
    lines.append("## Test 정답 통합 topic_train 분포")
    lines.append("")
    lines.extend(count_table_lines(count_topics(answer_rows, "topic_train")))
    lines.append("")
    lines.append("## 전체 통합 topic_train 분포")
    lines.append("")
    lines.extend(count_table_lines(count_topics(full_rows, "topic_train")))
    lines.append("")
    lines.append("## 사용 방법")
    lines.append("")
    lines.append("```text")
    lines.append("era 모델: era 사용")
    lines.append("topic 모델: topic 대신 topic_train 사용")
    lines.append("question_type 모델: 현재 성능/라벨 쏠림 문제로 제외 검토")
    lines.append("```")
    lines.append("")
    lines.append("## 주의")
    lines.append("")
    lines.append("- 예측 결과가 `정치`라면 원본의 정치/경제/사회/군사/외교가 포함된 학습용 라벨입니다.")
    lines.append("- 예측 결과가 `문화`라면 원본의 문화/사상·종교가 포함된 학습용 라벨입니다.")
    lines.append("- 원본 세부 topic 해석이 필요하면 `topic` 컬럼을 함께 확인해야 합니다.")
    lines.append("")
    return "\n".join(lines)


# split_v1 파일들을 읽고 topic_train이 추가된 새 split 파일들을 생성합니다.
# 원본 split_v1은 수정하지 않고 split_topic_merged_v1 폴더에 새 버전을 저장합니다.
# predict_input 파일의 topic_train은 빈칸으로 둬 실제 예측 입력 조건을 유지합니다.
def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    train_rows = transform_rows(read_json(TRAIN_INPUT_JSON))
    predict_rows = transform_rows(read_json(PREDICT_INPUT_JSON), blank_topic_train=True)
    answer_rows = transform_rows(read_json(ANSWER_INPUT_JSON))
    full_rows = transform_rows(read_json(FULL_INPUT_JSON))

    write_json(TRAIN_OUTPUT_JSON, train_rows)
    write_csv(TRAIN_OUTPUT_CSV, train_rows, OUTPUT_COLUMNS)
    write_json(PREDICT_OUTPUT_JSON, predict_rows)
    write_csv(PREDICT_OUTPUT_CSV, predict_rows, OUTPUT_COLUMNS)
    write_json(ANSWER_OUTPUT_JSON, answer_rows)
    write_csv(ANSWER_OUTPUT_CSV, answer_rows, OUTPUT_COLUMNS)
    write_json(FULL_OUTPUT_JSON, full_rows)
    write_csv(FULL_OUTPUT_CSV, full_rows, OUTPUT_COLUMNS)

    REPORT_MD.write_text(
        build_report(train_rows, answer_rows, full_rows),
        encoding="utf-8",
    )

    print(f"saved: {OUTPUT_DIR.relative_to(ROOT_DIR)}")
    print(f"train rows: {len(train_rows)}")
    print(f"predict rows: {len(predict_rows)}")
    print(f"answer rows: {len(answer_rows)}")
    print("topic_train train counts:", dict(count_topics(train_rows, "topic_train").most_common()))


if __name__ == "__main__":
    main()
