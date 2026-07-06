# TF-IDF + Logistic Regression으로 1차 기준 성능을 확인하는 baseline 학습 파일입니다.
# Colab의 Final_project/common 데이터 파일을 읽어 era/topic/question_type 모델 3개를 학습합니다.
# class weight를 적용하고 Accuracy, Macro F1, Weighted F1, 라벨별 성능 리포트를 저장합니다.
"""
Train TF-IDF baseline models for ML_han v1.

Colab folder structure:
  /content/drive/MyDrive/Final_project/
    common/
      ml_han_weighted_train_v1.jsonl
      ml_han_weighted_test_v1.jsonl
      ml_han_class_weights_v1.json
    code/
      train_baseline_tfidf_v1.py

Run in Colab:
  from google.colab import drive
  drive.mount('/content/drive')
  !python /content/drive/MyDrive/Final_project/code/train_baseline_tfidf_v1.py

Local fallback run:
  python ai/ml/train_baseline_tfidf_v1.py
"""

from __future__ import annotations

import json
import csv
from collections import Counter
from pathlib import Path
from typing import Any


# Colab 기본 경로입니다.
# 로컬에서 실행할 경우 아래 LOCAL_BASE_DIR로 자동 fallback됩니다.
# 사용자가 Colab에 올린 Final_project 폴더 구조와 맞춰져 있습니다.
COLAB_BASE_DIR = Path("/content/drive/MyDrive/Final_project")
LOCAL_BASE_DIR = Path(__file__).resolve().parent

BASE_DIR = COLAB_BASE_DIR if COLAB_BASE_DIR.exists() else LOCAL_BASE_DIR
COMMON_DIR = BASE_DIR / "common" if (BASE_DIR / "common").exists() else BASE_DIR / "output"
RESULT_DIR = COMMON_DIR / "baseline_tfidf_v1"

TRAIN_JSONL = COMMON_DIR / "ml_han_weighted_train_v1.jsonl"
TEST_JSONL = COMMON_DIR / "ml_han_weighted_test_v1.jsonl"
CLASS_WEIGHT_JSON = COMMON_DIR / "ml_han_class_weights_v1.json"

RESULT_JSON = RESULT_DIR / "baseline_tfidf_results_v1.json"
RESULT_MD = RESULT_DIR / "baseline_tfidf_results_v1.md"

TARGET_COLUMNS = ["era", "topic", "question_type"]


# JSON 파일을 UTF-8로 읽어 Python 객체로 변환합니다.
# class weight와 label map 정보가 담긴 JSON을 읽을 때 사용합니다.
# 파일이 없으면 경로 문제를 바로 확인할 수 있도록 예외를 그대로 발생시킵니다.
def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


# JSONL 파일을 한 줄씩 읽어 row 목록으로 변환합니다.
# train/test 데이터는 한 문항이 한 줄인 JSONL 형식으로 저장되어 있습니다.
# 빈 줄은 건너뛰고 UTF-8 기준으로 한국어 텍스트를 보존합니다.
def read_jsonl(path: Path) -> list[dict]:
    rows: list[dict] = []
    with path.open("r", encoding="utf-8") as file:
        for line in file:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


# baseline 모델 입력으로 사용할 텍스트 리스트를 만듭니다.
# weighted JSONL에는 이미 input_text + keywords가 합쳐진 text 필드가 있습니다.
# 정답 라벨은 입력에 포함하지 않아 데이터 누수를 방지합니다.
def get_texts(rows: list[dict]) -> list[str]:
    return [str(row.get("text") or "") for row in rows]


# target 이름에 해당하는 정답 라벨 리스트를 꺼냅니다.
# 각 row의 labels 딕셔너리에는 era/topic/question_type 정답이 들어 있습니다.
# 모델별로 같은 입력을 쓰고 y 라벨만 바꿔 3개 모델을 학습합니다.
def get_labels(rows: list[dict], target: str) -> list[str]:
    return [str(row["labels"][target]) for row in rows]


# class weight JSON에서 sklearn에 넣을 class_weight 딕셔너리를 만듭니다.
# LogisticRegression은 {라벨: weight} 형태의 class_weight를 받을 수 있습니다.
# train 분포 기준으로 계산된 값을 그대로 사용해 소수 라벨을 더 크게 반영합니다.
def get_class_weight(assets: dict, target: str) -> dict[str, float]:
    return {
        label: float(weight)
        for label, weight in assets["assets"][target]["class_weights"].items()
    }


# sklearn 기반 TF-IDF + LogisticRegression 파이프라인을 생성합니다.
# TF-IDF는 텍스트를 단어 중요도 벡터로 바꾸고 LogisticRegression은 라벨을 분류합니다.
# class_weight를 넣어 많은 라벨 쏠림으로 인한 학습 편향을 줄입니다.
def build_pipeline(class_weight: dict[str, float]):
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import Pipeline

    return Pipeline(
        steps=[
            (
                "tfidf",
                TfidfVectorizer(
                    analyzer="char_wb",
                    ngram_range=(2, 5),
                    min_df=2,
                    max_features=80000,
                    sublinear_tf=True,
                ),
            ),
            (
                "clf",
                LogisticRegression(
                    max_iter=2000,
                    class_weight=class_weight,
                    solver="liblinear",
                    random_state=42,
                ),
            ),
        ]
    )


# 예측 결과를 Accuracy, Macro F1, Weighted F1, 라벨별 리포트로 평가합니다.
# Macro F1은 소수 라벨 성능을 반영하므로 인밸런스 데이터에서 특히 중요합니다.
# zero_division=0으로 예측하지 못한 라벨이 있어도 리포트가 중단되지 않게 합니다.
def evaluate_predictions(y_true: list[str], y_pred: list[str]) -> dict:
    from sklearn.metrics import accuracy_score, classification_report, f1_score

    return {
        "accuracy": round(float(accuracy_score(y_true, y_pred)), 6),
        "macro_f1": round(float(f1_score(y_true, y_pred, average="macro", zero_division=0)), 6),
        "weighted_f1": round(float(f1_score(y_true, y_pred, average="weighted", zero_division=0)), 6),
        "classification_report": classification_report(
            y_true,
            y_pred,
            output_dict=True,
            zero_division=0,
        ),
    }


# 실제로 target 하나에 대한 baseline 모델을 학습하고 평가합니다.
# 같은 train/test 텍스트를 쓰되 target별 y 라벨과 class_weight만 바뀝니다.
# 평가 결과와 라벨 분포를 함께 반환해 리포트에서 바로 비교할 수 있게 합니다.
def train_one_target(train_rows: list[dict], test_rows: list[dict], assets: dict, target: str) -> dict:
    x_train = get_texts(train_rows)
    y_train = get_labels(train_rows, target)
    x_test = get_texts(test_rows)
    y_test = get_labels(test_rows, target)

    model = build_pipeline(get_class_weight(assets, target))
    model.fit(x_train, y_train)
    y_pred = model.predict(x_test).tolist()

    row_predictions = []
    for row, true_label, pred_label in zip(test_rows, y_test, y_pred):
        row_predictions.append(
            {
                "round_no": row.get("round_no"),
                "question_no": row.get("question_no"),
                "problem_id": row.get("problem_id"),
                "true_label": true_label,
                "pred_label": pred_label,
                "is_correct": true_label == pred_label,
                "text_preview": str(row.get("text") or "")[:160].replace("\n", " "),
            }
        )

    return {
        "target": target,
        "train_counts": dict(Counter(y_train).most_common()),
        "test_counts": dict(Counter(y_test).most_common()),
        "metrics": evaluate_predictions(y_test, y_pred),
        "pred_counts": dict(Counter(y_pred).most_common()),
        "row_predictions": row_predictions,
    }


# 평가 결과 JSON을 사람이 읽기 쉬운 Markdown 리포트로 변환합니다.
# target별 핵심 지표와 라벨별 precision/recall/f1을 표로 정리합니다.
# 발표나 회의에서 baseline 성능을 빠르게 확인하는 용도입니다.
def build_markdown(results: dict) -> str:
    lines: list[str] = []
    lines.append("# TF-IDF Baseline Results v1")
    lines.append("")
    lines.append("- 입력 데이터: `ml_han_weighted_train_v1.jsonl`, `ml_han_weighted_test_v1.jsonl`")
    lines.append("- 모델: `TfidfVectorizer(char_wb 2~5gram) + LogisticRegression`")
    lines.append("- 보정: train 기준 `class_weight` 적용")
    lines.append("- 평가: 47~70회 train, 71~78회 test")
    lines.append("")
    lines.append("## Summary")
    lines.append("")
    lines.append("| target | accuracy | macro_f1 | weighted_f1 |")
    lines.append("|---|---:|---:|---:|")
    for target in TARGET_COLUMNS:
        metrics = results["targets"][target]["metrics"]
        lines.append(
            f"| {target} | {metrics['accuracy']:.4f} | "
            f"{metrics['macro_f1']:.4f} | {metrics['weighted_f1']:.4f} |"
        )
    lines.append("")

    for target in TARGET_COLUMNS:
        target_result = results["targets"][target]
        report = target_result["metrics"]["classification_report"]
        lines.append(f"## {target}")
        lines.append("")
        lines.append("### Label Distribution")
        lines.append("")
        lines.append("| label | train | test | pred |")
        lines.append("|---|---:|---:|---:|")
        labels = sorted(
            set(target_result["train_counts"])
            | set(target_result["test_counts"])
            | set(target_result["pred_counts"])
        )
        for label in labels:
            lines.append(
                f"| {label} | {target_result['train_counts'].get(label, 0)} | "
                f"{target_result['test_counts'].get(label, 0)} | "
                f"{target_result['pred_counts'].get(label, 0)} |"
            )
        lines.append("")
        lines.append("### Per-class Metrics")
        lines.append("")
        lines.append("| label | precision | recall | f1-score | support |")
        lines.append("|---|---:|---:|---:|---:|")
        for label in labels:
            values = report.get(label, {})
            lines.append(
                f"| {label} | {values.get('precision', 0):.4f} | "
                f"{values.get('recall', 0):.4f} | "
                f"{values.get('f1-score', 0):.4f} | "
                f"{int(values.get('support', 0))} |"
            )
        lines.append("")
    return "\n".join(lines) + "\n"


# 학습 결과를 JSON과 Markdown으로 저장합니다.
# JSON은 후속 분석용, Markdown은 사람이 읽는 리포트용입니다.
# RESULT_DIR이 없으면 자동으로 생성합니다.
def save_results(results: dict) -> None:
    RESULT_DIR.mkdir(parents=True, exist_ok=True)
    RESULT_JSON.write_text(json.dumps(results, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    RESULT_MD.write_text(build_markdown(results), encoding="utf-8")

    for target in TARGET_COLUMNS:
        pred_csv = RESULT_DIR / f"{target}_predictions_v1.csv"
        with pred_csv.open("w", encoding="utf-8-sig", newline="") as file:
            writer = csv.DictWriter(
                file,
                fieldnames=[
                    "round_no",
                    "question_no",
                    "problem_id",
                    "true_label",
                    "pred_label",
                    "is_correct",
                    "text_preview",
                ],
            )
            writer.writeheader()
            writer.writerows(results["targets"][target]["row_predictions"])


# 필요한 라이브러리가 설치되어 있는지 확인합니다.
# Colab에는 보통 sklearn이 기본 설치되어 있지만, 없으면 설치 안내를 띄웁니다.
# 로컬 환경에서 실행할 때 의존성 문제를 빠르게 파악하기 위한 함수입니다.
def check_dependencies() -> None:
    try:
        import sklearn  # noqa: F401
    except ModuleNotFoundError as exc:
        raise SystemExit(
            "scikit-learn이 설치되어 있지 않습니다. Colab에서 `!pip install scikit-learn` 실행 후 다시 시도하세요."
        ) from exc


# baseline 학습 전체를 실행합니다.
# 데이터 로드, target별 모델 학습, 평가, 결과 저장을 순서대로 수행합니다.
# 마지막에 저장 경로와 핵심 지표를 콘솔에 출력합니다.
def main() -> None:
    check_dependencies()

    train_rows = read_jsonl(TRAIN_JSONL)
    test_rows = read_jsonl(TEST_JSONL)
    assets = read_json(CLASS_WEIGHT_JSON)

    results = {
        "base_dir": str(BASE_DIR),
        "common_dir": str(COMMON_DIR),
        "train_rows": len(train_rows),
        "test_rows": len(test_rows),
        "targets": {},
    }

    for target in TARGET_COLUMNS:
        print(f"[train] {target}")
        results["targets"][target] = train_one_target(train_rows, test_rows, assets, target)

    save_results(results)

    print(json.dumps(
        {
            "result_json": str(RESULT_JSON),
            "result_md": str(RESULT_MD),
            "summary": {
                target: {
                    key: results["targets"][target]["metrics"][key]
                    for key in ["accuracy", "macro_f1", "weighted_f1"]
                }
                for target in TARGET_COLUMNS
            },
        },
        ensure_ascii=False,
        indent=2,
    ))


if __name__ == "__main__":
    main()
