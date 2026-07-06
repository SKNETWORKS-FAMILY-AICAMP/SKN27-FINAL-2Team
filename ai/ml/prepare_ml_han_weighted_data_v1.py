# v1 피처 데이터에 class weight와 sample weight를 붙이는 준비 파일입니다.
# era/topic/question_type 3개 모델의 라벨 불균형 보정값을 계산합니다.
# 실제 학습 전 train/test JSONL과 가중치 리포트를 ai/ml/output에 저장합니다.
"""
Prepare weighted training assets for ML_han v1.

This script does not train a model. It prepares the imbalance-handling values
that the actual model training code should use.

Input:
  ai/ml/output/ml_han_features_v1.json

Outputs:
  ai/ml/output/ml_han_class_weights_v1.json
  ai/ml/output/ml_han_weighted_train_v1.jsonl
  ai/ml/output/ml_han_weighted_test_v1.jsonl
  ai/ml/output/ml_han_class_weights_v1.md

Run:
  python ai/ml/prepare_ml_han_weighted_data_v1.py
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any


# ai/ml 안에서 바로 작업할 수 있도록 현재 파일 위치를 ML 작업 폴더로 사용합니다.
ROOT_DIR = Path(__file__).resolve().parents[2]
ML_DIR = Path(__file__).resolve().parent
OUT_DIR = ML_DIR / "output"

FEATURE_JSON = OUT_DIR / "ml_han_features_v1.json"
CLASS_WEIGHT_JSON = OUT_DIR / "ml_han_class_weights_v1.json"
TRAIN_JSONL = OUT_DIR / "ml_han_weighted_train_v1.jsonl"
TEST_JSONL = OUT_DIR / "ml_han_weighted_test_v1.jsonl"
REPORT_MD = OUT_DIR / "ml_han_class_weights_v1.md"

# 실제 학습할 3개 모델의 타깃 라벨입니다.
# question_subtype은 인밸런스가 너무 커서 v1 학습 타깃에서는 제외합니다.
TARGET_COLUMNS = ["era", "topic", "question_type"]


# JSON 파일을 UTF-8로 읽어 Python 객체로 변환합니다.
# 피처 데이터와 class weight 산출 결과를 로드할 때 사용하는 기본 함수입니다.
# 파일이 없으면 예외가 발생해 잘못된 경로를 빠르게 확인할 수 있습니다.
def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


# None이나 빈 값을 안전한 문자열로 변환합니다.
# 라벨명과 입력 텍스트의 앞뒤 공백을 제거해 비교 오류를 줄입니다.
# class weight 계산 전 라벨 값을 일관되게 만드는 데 사용합니다.
def normalize_text(value: Any) -> str:
    return str(value or "").strip()


# 모델이 실제로 볼 입력 문장을 구성합니다.
# input_text에 keywords만 붙이고 정답 라벨은 절대 포함하지 않습니다.
# 이렇게 해야 학습 시 데이터 누수를 막고 실사용 조건과 맞출 수 있습니다.
def build_model_input(row: dict) -> str:
    """모델 입력은 문제 본문과 키워드만 사용합니다.

    정답 라벨인 era/topic/question_type을 입력에 넣으면 데이터 누수가 됩니다.
    """
    input_text = normalize_text(row.get("input_text"))
    keywords = normalize_text(row.get("keywords"))
    if keywords:
        return f"{input_text}\n\n[키워드] {keywords}"
    return input_text


# train 라벨 분포를 기준으로 balanced class weight를 계산합니다.
# 적게 나온 클래스일수록 큰 weight를 받아 loss에 더 크게 반영됩니다.
# sklearn과 PyTorch에서 모두 쓰기 쉬운 label -> weight 형태로 반환합니다.
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


# 문자열 라벨을 정수 id로 변환하는 매핑을 만듭니다.
# 모델 출력층과 loss 계산은 정수 라벨을 기준으로 동작합니다.
# 정렬된 라벨 순서를 사용해 실행할 때마다 같은 id가 나오게 합니다.
def build_label_map(labels: list[str]) -> dict[str, int]:
    """문자 라벨을 모델이 사용할 정수 id로 변환하기 위한 매핑입니다."""
    return {label: index for index, label in enumerate(sorted(set(labels)))}


# 원본 row에 모델 학습용 필드를 추가합니다.
# labels, label_ids, sample_weights를 target별로 붙입니다.
# baseline 학습과 딥러닝 학습 양쪽에서 바로 읽어 쓸 수 있는 JSONL을 만듭니다.
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


# row 목록을 JSONL 형식으로 저장합니다.
# 한 줄에 한 문항씩 저장되어 대용량 학습 데이터 처리에 편합니다.
# ensure_ascii=False로 한국어 라벨과 텍스트를 그대로 보존합니다.
def write_jsonl(path: Path, rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as file:
        for row in rows:
            file.write(json.dumps(row, ensure_ascii=False) + "\n")


# 건수 값을 퍼센트로 변환합니다.
# total이 0일 때 나눗셈 오류가 나지 않도록 0.0을 반환합니다.
# Markdown 리포트의 라벨 비율 표시에 사용합니다.
def pct(value: int, total: int) -> float:
    return 0.0 if total == 0 else value / total * 100


# class weight 계산 결과를 Markdown 문서로 만듭니다.
# 라벨별 train 건수, 비율, weight, label id를 표로 정리합니다.
# PyTorch에서 weight tensor를 만드는 예시 코드도 함께 제공합니다.
def build_report(train_rows: list[dict], assets: dict) -> str:
    lines: list[str] = []
    lines.append("# ML_han v1 Class Weight")
    lines.append("")
    lines.append("- 기준 파일: `ai/ml/output/ml_han_features_v1.json`")
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


# class weight 준비 작업 전체를 실행합니다.
# 피처 파일을 읽고 train/test를 나눈 뒤 target별 weight를 계산합니다.
# JSON, JSONL, Markdown 산출물을 output 폴더에 저장합니다.
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
