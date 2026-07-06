"""
Prepare weighted training assets for ML_han v1.

This script does not train a model. It prepares the imbalance-handling values
that the actual model training code should use.

Input:
  test/CJ/test_ml/output/ml_han_features_v1.json

Outputs:
  test/CJ/test_ml/output/ml_han_class_weights_v1.json
  test/CJ/test_ml/output/ml_han_weighted_train_v1.jsonl
  test/CJ/test_ml/output/ml_han_weighted_test_v1.jsonl
  test/CJ/test_ml/output/ml_han_class_weights_v1.md

Run:
  python test/CJ/test_ml/prepare_ml_han_weighted_data_v1.py
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any


ROOT_DIR = Path(__file__).resolve().parents[3]
ML_DIR = ROOT_DIR / "test" / "CJ" / "test_ml"
OUT_DIR = ML_DIR / "output"

FEATURE_JSON = OUT_DIR / "ml_han_features_v1.json"
CLASS_WEIGHT_JSON = OUT_DIR / "ml_han_class_weights_v1.json"
TRAIN_JSONL = OUT_DIR / "ml_han_weighted_train_v1.jsonl"
TEST_JSONL = OUT_DIR / "ml_han_weighted_test_v1.jsonl"
REPORT_MD = OUT_DIR / "ml_han_class_weights_v1.md"

# 실제 학습할 3개 모델의 타깃 라벨입니다.
# question_subtype은 인밸런스가 너무 커서 v1 학습 타깃에서는 제외합니다.
TARGET_COLUMNS = ["era", "topic", "question_type"]


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def normalize_text(value: Any) -> str:
    return str(value or "").strip()


def build_model_input(row: dict) -> str:
    """모델 입력은 문제 본문과 키워드만 사용합니다.

    정답 라벨인 era/topic/question_type을 입력에 넣으면 데이터 누수가 됩니다.
    """
    input_text = normalize_text(row.get("input_text"))
    keywords = normalize_text(row.get("keywords"))
    if keywords:
        return f"{input_text}\n\n[키워드] {keywords}"
    return input_text


def balanced_class_weights(labels: list[str]) -> dict[str, float]:
    """클래스 불균형 보정을 위한 balanced class weight를 계산합니다.

    공식:
      전체 학습 샘플 수 / (클래스 수 * 해당 클래스 샘플 수)

    의미:
      많이 나온 라벨은 weight가 작아지고,
      적게 나온 라벨은 weight가 커져서 loss에 더 크게 반영됩니다.
    """
    counts = Counter(labels)
    total = len(labels)
    class_count = len(counts)
    return {
        label: round(total / (class_count * count), 6)
        for label, count in sorted(counts.items())
    }


def build_label_map(labels: list[str]) -> dict[str, int]:
    """문자 라벨을 모델이 사용할 정수 id로 변환하기 위한 매핑입니다."""
    return {label: index for index, label in enumerate(sorted(set(labels)))}


def add_training_fields(rows: list[dict], assets: dict) -> list[dict]:
    """각 row에 label id와 sample weight를 붙입니다.

    sample_weight는 TF-IDF + LogisticRegression 같은 baseline에서 바로 사용할 수 있고,
    class_weights는 KLUE/RoBERTa의 CrossEntropyLoss(weight=...)에 사용할 수 있습니다.
    """
    weighted_rows: list[dict] = []
    for row in rows:
        output = {
            "ml_sequence_index": row["ml_sequence_index"],
            "split": row["split"],
            "round_no": row["round_no"],
            "question_no": row["question_no"],
            "problem_id": row["problem_id"],
            "text": build_model_input(row),
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


def write_jsonl(path: Path, rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as file:
        for row in rows:
            file.write(json.dumps(row, ensure_ascii=False) + "\n")


def pct(value: int, total: int) -> float:
    return 0.0 if total == 0 else value / total * 100


def build_report(train_rows: list[dict], assets: dict) -> str:
    lines: list[str] = []
    lines.append("# ML_han v1 Class Weight")
    lines.append("")
    lines.append("- 기준 파일: `test/CJ/test_ml/output/ml_han_features_v1.json`")
    lines.append("- train: 47~70회")
    lines.append("- test: 71~78회")
    lines.append("- 적용 대상 라벨: `era`, `topic`, `question_type`")
    lines.append("- 계산 공식: `전체 train 샘플 수 / (클래스 수 * 해당 클래스 샘플 수)`")
    lines.append("")
    lines.append("## 사용 방법")
    lines.append("")
    lines.append("- baseline 모델: 각 row의 `sample_weights[target]`를 학습 함수에 전달합니다.")
    lines.append("- KLUE/RoBERTa: `class_weights[target]`를 라벨 id 순서대로 tensor로 만든 뒤 `CrossEntropyLoss(weight=...)`에 전달합니다.")
    lines.append("- 평가는 Accuracy만 보지 말고 `Macro F1`, `Weighted F1`, `per-class F1`을 같이 봅니다.")
    lines.append("")

    for target in TARGET_COLUMNS:
        counts = Counter(normalize_text(row.get(target)) for row in train_rows)
        total = sum(counts.values())
        lines.append(f"## {target}")
        lines.append("")
        lines.append("| 라벨 | train 건수 | 비율 | class weight | label id |")
        lines.append("|---|---:|---:|---:|---:|")
        for label, count in counts.most_common():
            weight = assets[target]["class_weights"][label]
            label_id = assets[target]["label_to_id"][label]
            lines.append(f"| {label} | {count} | {pct(count, total):.1f}% | {weight:.6f} | {label_id} |")
        lines.append("")

    lines.append("## PyTorch 적용 예시")
    lines.append("")
    lines.append("```python")
    lines.append("# target = 'question_type' 예시")
    lines.append("label_to_id = assets[target]['label_to_id']")
    lines.append("class_weights = assets[target]['class_weights']")
    lines.append("weight_tensor = torch.tensor(")
    lines.append("    [class_weights[label] for label, _ in sorted(label_to_id.items(), key=lambda x: x[1])],")
    lines.append("    dtype=torch.float,")
    lines.append("    device=device,")
    lines.append(")")
    lines.append("loss_fn = torch.nn.CrossEntropyLoss(weight=weight_tensor)")
    lines.append("```")
    lines.append("")
    return "\n".join(lines)


def main() -> None:
    rows = read_json(FEATURE_JSON)
    train_rows = [row for row in rows if row.get("split") == "train"]
    test_rows = [row for row in rows if row.get("split") == "test"]

    assets: dict[str, dict] = {}
    for target in TARGET_COLUMNS:
        train_labels = [normalize_text(row.get(target)) for row in train_rows]
        assets[target] = {
            "label_to_id": build_label_map(train_labels),
            "class_weights": balanced_class_weights(train_labels),
            "train_counts": dict(Counter(train_labels).most_common()),
        }

    weighted_train_rows = add_training_fields(train_rows, assets)
    weighted_test_rows = add_training_fields(test_rows, assets)

    output = {
        "input": FEATURE_JSON.relative_to(ROOT_DIR).as_posix(),
        "target_columns": TARGET_COLUMNS,
        "split_counts": {
            "train": len(train_rows),
            "test": len(test_rows),
        },
        "assets": assets,
        "outputs": {
            "train_jsonl": TRAIN_JSONL.relative_to(ROOT_DIR).as_posix(),
            "test_jsonl": TEST_JSONL.relative_to(ROOT_DIR).as_posix(),
            "report_md": REPORT_MD.relative_to(ROOT_DIR).as_posix(),
        },
    }

    CLASS_WEIGHT_JSON.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    write_jsonl(TRAIN_JSONL, weighted_train_rows)
    write_jsonl(TEST_JSONL, weighted_test_rows)
    REPORT_MD.write_text(build_report(train_rows, assets), encoding="utf-8")

    print(
        json.dumps(
            {
                "class_weight_json": output["outputs"],
                "targets": TARGET_COLUMNS,
                "split_counts": output["split_counts"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
