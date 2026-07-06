# topic_train 통합 라벨 기준 class weight와 JSONL 학습 보조 데이터를 생성합니다.
# 기존 topic 기준 weight를 재사용하지 않고, 통합 후 분포로 다시 계산합니다.
# 학습 대상은 era와 topic_train이며 question_type은 제외합니다.
"""
Prepare class weights for topic-merged ML data.

Input:
  ai/ml/output/split_topic_merged_v1/train_features_topic_merged_v1.json
  ai/ml/output/split_topic_merged_v1/test_answer_topic_merged_v1.json

Outputs:
  ai/ml/output/split_topic_merged_v1/ml_han_topic_merged_class_weights_v1.json
  ai/ml/output/split_topic_merged_v1/ml_han_topic_merged_class_weights_v1.md
  ai/ml/output/split_topic_merged_v1/ml_han_topic_merged_weighted_train_v1.jsonl
  ai/ml/output/split_topic_merged_v1/ml_han_topic_merged_weighted_test_v1.jsonl

Run:
  python ai/ml/prepare_topic_merged_weighted_data_v1.py
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any


ROOT_DIR = Path(__file__).resolve().parents[2]
ML_DIR = Path(__file__).resolve().parent
DATA_DIR = ML_DIR / "output" / "split_topic_merged_v1"

TRAIN_JSON = DATA_DIR / "train_features_topic_merged_v1.json"
TEST_JSON = DATA_DIR / "test_answer_topic_merged_v1.json"

CLASS_WEIGHT_JSON = DATA_DIR / "ml_han_topic_merged_class_weights_v1.json"
CLASS_WEIGHT_MD = DATA_DIR / "ml_han_topic_merged_class_weights_v1.md"
TRAIN_JSONL = DATA_DIR / "ml_han_topic_merged_weighted_train_v1.jsonl"
TEST_JSONL = DATA_DIR / "ml_han_topic_merged_weighted_test_v1.jsonl"

TARGET_COLUMNS = ["era", "topic_train"]


# JSON 파일을 UTF-8로 읽어 Python 객체로 변환합니다.
# topic_train이 추가된 train/test_answer 파일을 로드할 때 사용합니다.
# 파일이 없거나 JSON 구조가 깨져 있으면 이후 계산을 멈춰 문제를 빠르게 드러냅니다.
def read_json(path: Path) -> list[dict[str, Any]]:
    return json.loads(path.read_text(encoding="utf-8"))


# 라벨 값을 문자열로 정리합니다.
# None이나 빈 값이 섞여 있을 때 class weight 계산이 흔들리지 않도록 합니다.
# topic_train 학습에서는 빈 라벨이 있으면 안 되므로 검증에도 사용됩니다.
def normalize_text(value: Any) -> str:
    return str(value or "").strip()


# train 라벨 분포를 기준으로 balanced class weight를 계산합니다.
# 적게 나온 라벨은 더 큰 weight를 받고 많이 나온 라벨은 더 작은 weight를 받습니다.
# KLUE/RoBERTa 학습 시 CrossEntropyLoss(weight=...)에 넣을 수 있는 값입니다.
def balanced_class_weights(labels: list[str]) -> dict[str, float]:
    counts = Counter(labels)
    total = len(labels)
    class_count = len(counts)
    return {
        label: round(total / (class_count * count), 6)
        for label, count in sorted(counts.items())
    }


# 라벨 문자열을 모델 출력 id로 바꾸기 위한 매핑을 만듭니다.
# 정렬된 라벨 순서를 사용해 Colab 실행마다 같은 label id가 나오게 합니다.
# id_to_label은 예측 결과를 다시 문자열 라벨로 바꿀 때 사용합니다.
def build_label_maps(labels: list[str]) -> tuple[dict[str, int], dict[int, str]]:
    label_to_id = {label: index for index, label in enumerate(sorted(set(labels)))}
    id_to_label = {index: label for label, index in label_to_id.items()}
    return label_to_id, id_to_label


# row에 labels, label_ids, sample_weights를 추가합니다.
# baseline이나 딥러닝 실험에서 동일한 보조 데이터를 재사용할 수 있게 JSONL로 저장합니다.
# text가 없는 test_answer 파일은 입력 누수를 막기 위해 text를 빈 문자열로 둡니다.
def add_training_fields(rows: list[dict[str, Any]], assets: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    weighted_rows: list[dict[str, Any]] = []
    for row in rows:
        output = {
            "ml_sequence_index": row.get("ml_sequence_index"),
            "split": row.get("split"),
            "round_no": row.get("round_no"),
            "question_no": row.get("question_no"),
            "problem_id": row.get("problem_id"),
            "text": row.get("text", ""),
            "topic_original": row.get("topic", ""),
            "labels": {},
            "label_ids": {},
            "sample_weights": {},
        }
        for target in TARGET_COLUMNS:
            label = normalize_text(row.get(target))
            output["labels"][target] = label
            output["label_ids"][target] = assets[target]["label_to_id"][label]
            output["sample_weights"][target] = assets[target]["class_weights"][label]
        weighted_rows.append(output)
    return weighted_rows


# JSONL 파일을 한 줄에 한 row씩 저장합니다.
# 대량 학습 데이터 처리와 Colab 로딩에 편한 형식입니다.
# ensure_ascii=False를 사용해 한국어 라벨을 그대로 보존합니다.
def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as file:
        for row in rows:
            file.write(json.dumps(row, ensure_ascii=False) + "\n")


# 라벨 카운트를 Markdown 표로 변환합니다.
# 통합 후 라벨별 건수, 비율, class weight, label id를 한 번에 확인할 수 있습니다.
# 모델 성능 기록 문서에 붙여 넣기 쉽게 단순 표 형태로 작성합니다.
def class_weight_table(target: str, train_rows: list[dict[str, Any]], assets: dict[str, Any]) -> list[str]:
    counts = Counter(normalize_text(row.get(target)) for row in train_rows)
    total = sum(counts.values())
    lines = ["| 라벨 | train 건수 | 비율 | class weight | label id |", "|---|---:|---:|---:|---:|"]
    for label, count in counts.most_common():
        pct = 0 if total == 0 else count / total * 100
        lines.append(
            f"| {label} | {count} | {pct:.1f}% | "
            f"{assets[target]['class_weights'][label]:.6f} | {assets[target]['label_to_id'][label]} |"
        )
    return lines


# class weight 계산 결과를 Markdown 보고서로 작성합니다.
# topic_train 기준으로 다시 계산했다는 점과 question_type 제외를 명시합니다.
# 이후 실험 결과 해석에서 통합 라벨임을 잊지 않도록 주의 문구를 남깁니다.
def build_report(train_rows: list[dict[str, Any]], assets: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append("# Topic Merged Class Weight v1")
    lines.append("")
    lines.append("- 기준 데이터: `split_topic_merged_v1/train_features_topic_merged_v1.json`")
    lines.append("- 학습 target: `era`, `topic_train`")
    lines.append("- 제외 target: `question_type`")
    lines.append("- `topic_train`은 원본 `topic`을 통합한 학습용 라벨입니다.")
    lines.append("")
    for target in TARGET_COLUMNS:
        lines.append(f"## {target}")
        lines.append("")
        lines.extend(class_weight_table(target, train_rows, assets))
        lines.append("")
    lines.append("## 주의")
    lines.append("")
    lines.append("- `topic_train=정치`는 원본 정치/경제/사회/군사/외교를 포함합니다.")
    lines.append("- `topic_train=문화`는 원본 문화/사상·종교를 포함합니다.")
    lines.append("- 원본 주제 확인이 필요하면 `topic` 컬럼을 함께 보아야 합니다.")
    lines.append("")
    return "\n".join(lines)


# 통합 라벨 기준 class weight와 JSONL 보조 파일을 생성합니다.
# 기존 v1 class weight 파일은 건드리지 않고 split_topic_merged_v1 아래에 새로 저장합니다.
# 빈 topic_train 라벨이 발견되면 잘못된 입력이므로 예외를 발생시킵니다.
def main() -> None:
    train_rows = read_json(TRAIN_JSON)
    test_rows = read_json(TEST_JSON)

    assets: dict[str, dict[str, Any]] = {}
    for target in TARGET_COLUMNS:
        labels = [normalize_text(row.get(target)) for row in train_rows]
        if any(not label for label in labels):
            raise ValueError(f"{target} has blank labels in train data")
        label_to_id, id_to_label = build_label_maps(labels)
        assets[target] = {
            "label_to_id": label_to_id,
            "id_to_label": id_to_label,
            "class_weights": balanced_class_weights(labels),
            "train_counts": dict(Counter(labels).most_common()),
        }

    weighted_train_rows = add_training_fields(train_rows, assets)
    weighted_test_rows = add_training_fields(test_rows, assets)

    output = {
        "input": {
            "train": TRAIN_JSON.relative_to(ROOT_DIR).as_posix(),
            "test_answer": TEST_JSON.relative_to(ROOT_DIR).as_posix(),
        },
        "target_columns": TARGET_COLUMNS,
        "split_counts": {
            "train": len(train_rows),
            "test": len(test_rows),
        },
        "assets": assets,
        "outputs": {
            "train_jsonl": TRAIN_JSONL.relative_to(ROOT_DIR).as_posix(),
            "test_jsonl": TEST_JSONL.relative_to(ROOT_DIR).as_posix(),
            "report_md": CLASS_WEIGHT_MD.relative_to(ROOT_DIR).as_posix(),
        },
    }

    CLASS_WEIGHT_JSON.write_text(
        json.dumps(output, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    CLASS_WEIGHT_MD.write_text(build_report(train_rows, assets), encoding="utf-8")
    write_jsonl(TRAIN_JSONL, weighted_train_rows)
    write_jsonl(TEST_JSONL, weighted_test_rows)

    print(f"saved: {CLASS_WEIGHT_JSON.relative_to(ROOT_DIR)}")
    print("target columns:", TARGET_COLUMNS)
    print("topic_train counts:", assets["topic_train"]["train_counts"])


if __name__ == "__main__":
    main()
