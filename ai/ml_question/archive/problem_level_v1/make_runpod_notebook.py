from __future__ import annotations

import json
from pathlib import Path


OUT = Path(__file__).resolve().parent / "train_runpod.ipynb"


def lines(text: str) -> list[str]:
    return text.strip("\n").splitlines(keepends=True)


def md(text: str) -> dict:
    return {
        "cell_type": "markdown",
        "metadata": {},
        "source": lines(text),
    }


def code(text: str) -> dict:
    return {
        "cell_type": "code",
        "execution_count": None,
        "metadata": {},
        "outputs": [],
        "source": lines(text),
    }


cells = [
    md(
        """
# 문제 2차 검수 BERT 학습

이 노트북은 RunPod에서 `klue/roberta-base`를 파인튜닝해 문제 2차 검수용 이진 분류 모델을 학습한다.

- `0`: 이상 있음 / 재검수 필요
- `1`: 이상 없음 / 통과 가능

2차 검수 목적이므로 전체 정확도보다 `label=0` 재현율, 즉 `abnormal_recall`을 우선 확인한다.
"""
    ),
    md("## 1. 라이브러리 설치"),
    code(
        """
!pip install -q torch transformers scikit-learn numpy accelerate
"""
    ),
    md("## 2. 기본 설정"),
    code(
        """
from __future__ import annotations

import csv
import json
import random
from pathlib import Path
from typing import Any

import numpy as np
import torch
from sklearn.metrics import (
    accuracy_score,
    classification_report,
    confusion_matrix,
    f1_score,
    precision_score,
    recall_score,
)
from torch.utils.data import DataLoader, Dataset
from transformers import (
    AutoModelForSequenceClassification,
    AutoTokenizer,
    get_linear_schedule_with_warmup,
)


# RunPod에서는 노트북을 /workspace에서 실행하고,
# 데이터 파일은 /workspace/common 폴더에 둔다.
WORKSPACE_DIR = Path.cwd()
DATA_DIR = WORKSPACE_DIR / "common"
TRAIN_JSON = DATA_DIR / "train.json"
VALID_JSON = DATA_DIR / "valid.json"
TEST_JSON = DATA_DIR / "test.json"
OUTPUT_DIR = WORKSPACE_DIR / "output"

# 학습 설정이다. GPU 메모리가 부족하면 BATCH_SIZE를 4로 낮춘다.
MODEL_NAME = "klue/roberta-base"
MAX_LENGTH = 512
EPOCHS = 5
BATCH_SIZE = 8
LEARNING_RATE = 2e-5
WEIGHT_DECAY = 0.01
WARMUP_RATIO = 0.1
PATIENCE = 2
MIN_DELTA = 0.0
SEED = 42

# 2차 검수에서는 이상 문항(label=0)을 놓치지 않는 것이 중요하다.
MIN_ABNORMAL_RECALL = 0.90

LABEL_NAMES = {
    0: "ABNORMAL",
    1: "NORMAL",
}


def set_seed(seed: int) -> None:
    # 실험 재현성을 위해 random, numpy, torch seed를 고정한다.
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)


set_seed(SEED)
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
print("device:", device)
if device.type == "cuda":
    print("gpu:", torch.cuda.get_device_name(0))

print("WORKSPACE_DIR:", WORKSPACE_DIR)
print("DATA_DIR:", DATA_DIR)
print("TRAIN_JSON exists:", TRAIN_JSON.exists())
print("VALID_JSON exists:", VALID_JSON.exists())
print("TEST_JSON exists:", TEST_JSON.exists())
"""
    ),
    md("## 3. 데이터 로드"),
    code(
        """
def read_json(path: Path) -> list[dict[str, Any]]:
    # 전처리된 구조화 JSON을 읽는다.
    if not path.exists():
        raise FileNotFoundError(f"파일이 없습니다: {path}")
    rows = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(rows, list) or not rows:
        raise ValueError(f"비어 있거나 리스트가 아닌 JSON입니다: {path}")
    return rows


def validate_rows(rows: list[dict[str, Any]], split_name: str) -> None:
    # 학습에 꼭 필요한 필드가 있는지 확인한다.
    for idx, row in enumerate(rows, start=1):
        if row.get("label") not in (0, 1):
            raise ValueError(f"{split_name}[{idx}] label 오류: {row.get('label')}")
        if not row.get("question"):
            raise ValueError(f"{split_name}[{idx}] question 누락")
        if not isinstance(row.get("choices"), list) or not row["choices"]:
            raise ValueError(f"{split_name}[{idx}] choices 누락")


train_rows = read_json(TRAIN_JSON)
valid_rows = read_json(VALID_JSON)
test_rows = read_json(TEST_JSON)

validate_rows(train_rows, "train")
validate_rows(valid_rows, "valid")
validate_rows(test_rows, "test")

print("train:", len(train_rows))
print("valid:", len(valid_rows))
print("test:", len(test_rows))
print("sample:", train_rows[0])
"""
    ),
    md("## 4. BERT 입력 문장 생성"),
    code(
        """
def build_model_text(row: dict[str, Any]) -> str:
    # 데이터 파일은 사람이 보기 좋게 구조화해두고,
    # 학습할 때만 지문/질문/선지/정답을 하나의 텍스트로 합친다.
    choices = row.get("choices") or []
    lines = [
        f"지문: {row.get('passage', '')}",
        f"질문: {row.get('question', '')}",
    ]
    lines.extend(
        f"선지{idx}: {choice}"
        for idx, choice in enumerate(choices, start=1)
    )
    lines.extend(
        [
            f"정답: {row.get('answer', '')}",
            f"목표배점: {row.get('target_score', '')}",
        ]
    )
    return "\\n".join(lines)


print(build_model_text(train_rows[0]))
"""
    ),
    md("## 5. Dataset과 DataLoader"),
    code(
        """
class ReviewDataset(Dataset):
    def __init__(self, rows: list[dict[str, Any]], tokenizer: AutoTokenizer, max_length: int) -> None:
        self.rows = rows
        self.tokenizer = tokenizer
        self.max_length = max_length

    def __len__(self) -> int:
        return len(self.rows)

    def __getitem__(self, index: int) -> dict[str, torch.Tensor]:
        row = self.rows[index]
        text = build_model_text(row)

        # tokenizer가 BERT 입력 토큰으로 변환한다.
        encoded = self.tokenizer(
            text,
            truncation=True,
            padding="max_length",
            max_length=self.max_length,
            return_tensors="pt",
        )
        item = {key: value.squeeze(0) for key, value in encoded.items()}
        item["labels"] = torch.tensor(int(row["label"]), dtype=torch.long)
        return item


def make_loader(rows: list[dict[str, Any]], shuffle: bool) -> DataLoader:
    dataset = ReviewDataset(rows, tokenizer, MAX_LENGTH)
    return DataLoader(dataset, batch_size=BATCH_SIZE, shuffle=shuffle, num_workers=0)


tokenizer = AutoTokenizer.from_pretrained(MODEL_NAME)

train_loader = make_loader(train_rows, shuffle=True)
valid_loader = make_loader(valid_rows, shuffle=False)
test_loader = make_loader(test_rows, shuffle=False)

print("train batches:", len(train_loader))
print("valid batches:", len(valid_loader))
print("test batches:", len(test_loader))
"""
    ),
    md("## 6. 평가 함수"),
    code(
        """
def compute_class_weights(rows: list[dict[str, Any]]) -> torch.Tensor:
    # 현재는 0/1 균형 데이터지만, 나중에 불균형이 생길 수 있어 class weight를 유지한다.
    counts = {0: 0, 1: 0}
    for row in rows:
        counts[int(row["label"])] += 1

    total = counts[0] + counts[1]
    weights = [
        total / (2 * max(1, counts[0])),
        total / (2 * max(1, counts[1])),
    ]
    return torch.tensor(weights, dtype=torch.float, device=device)


def compute_metrics(y_true: list[int], y_pred: list[int]) -> dict[str, Any]:
    # 0번 라벨이 이상 문항이므로 abnormal_recall을 별도로 계산한다.
    labels = [0, 1]
    return {
        "accuracy": round(float(accuracy_score(y_true, y_pred)), 6),
        "macro_f1": round(float(f1_score(y_true, y_pred, average="macro", zero_division=0)), 6),
        "abnormal_precision": round(float(precision_score(y_true, y_pred, labels=labels, pos_label=0, average="binary", zero_division=0)), 6),
        "abnormal_recall": round(float(recall_score(y_true, y_pred, labels=labels, pos_label=0, average="binary", zero_division=0)), 6),
        "normal_precision": round(float(precision_score(y_true, y_pred, labels=labels, pos_label=1, average="binary", zero_division=0)), 6),
        "normal_recall": round(float(recall_score(y_true, y_pred, labels=labels, pos_label=1, average="binary", zero_division=0)), 6),
        "classification_report": classification_report(
            y_true,
            y_pred,
            labels=labels,
            target_names=[LABEL_NAMES[0], LABEL_NAMES[1]],
            output_dict=True,
            zero_division=0,
        ),
        "confusion_matrix": {
            "labels": [LABEL_NAMES[0], LABEL_NAMES[1]],
            "matrix": confusion_matrix(y_true, y_pred, labels=labels).astype(int).tolist(),
        },
    }


def predict_loader(model: AutoModelForSequenceClassification, loader: DataLoader) -> tuple[list[int], list[int], list[float]]:
    # argmax 예측값과 함께 label=0일 확률도 반환한다.
    model.eval()
    y_true: list[int] = []
    y_pred: list[int] = []
    abnormal_probs: list[float] = []

    with torch.no_grad():
        for batch in loader:
            batch = {key: value.to(device) for key, value in batch.items()}
            labels = batch.pop("labels")
            outputs = model(**batch)
            probs = torch.softmax(outputs.logits, dim=-1)

            y_true.extend(labels.detach().cpu().tolist())
            y_pred.extend(outputs.logits.argmax(dim=-1).detach().cpu().tolist())
            abnormal_probs.extend(probs[:, 0].detach().cpu().tolist())

    return y_true, y_pred, abnormal_probs


def threshold_predictions(abnormal_probs: list[float], threshold: float) -> list[int]:
    # label=0 확률이 threshold 이상이면 재검수 대상으로 보낸다.
    return [0 if prob >= threshold else 1 for prob in abnormal_probs]


def choose_threshold(y_true: list[int], abnormal_probs: list[float], min_abnormal_recall: float) -> dict[str, Any]:
    # 검증셋에서 abnormal_recall을 우선 만족하는 threshold를 고른다.
    candidates = [round(i / 100, 2) for i in range(5, 96)]
    scored: list[dict[str, Any]] = []

    for threshold in candidates:
        y_pred = threshold_predictions(abnormal_probs, threshold)
        metric = compute_metrics(y_true, y_pred)
        scored.append(
            {
                "threshold": threshold,
                "macro_f1": metric["macro_f1"],
                "abnormal_recall": metric["abnormal_recall"],
                "abnormal_precision": metric["abnormal_precision"],
                "normal_recall": metric["normal_recall"],
            }
        )

    enough_recall = [
        item for item in scored
        if item["abnormal_recall"] >= min_abnormal_recall
    ]

    if enough_recall:
        return max(enough_recall, key=lambda item: (item["macro_f1"], item["abnormal_precision"], item["threshold"]))
    return max(scored, key=lambda item: (item["abnormal_recall"], item["macro_f1"], item["abnormal_precision"]))
"""
    ),
    md("## 7. 모델 생성"),
    code(
        """
model = AutoModelForSequenceClassification.from_pretrained(
    MODEL_NAME,
    num_labels=2,
    id2label={0: LABEL_NAMES[0], 1: LABEL_NAMES[1]},
    label2id={LABEL_NAMES[0]: 0, LABEL_NAMES[1]: 1},
)
model.to(device)

optimizer = torch.optim.AdamW(
    model.parameters(),
    lr=LEARNING_RATE,
    weight_decay=WEIGHT_DECAY,
)

total_steps = len(train_loader) * EPOCHS
warmup_steps = int(total_steps * WARMUP_RATIO)
scheduler = get_linear_schedule_with_warmup(
    optimizer,
    num_warmup_steps=warmup_steps,
    num_training_steps=total_steps,
)

loss_fn = torch.nn.CrossEntropyLoss(weight=compute_class_weights(train_rows))

print("model loaded:", MODEL_NAME)
print("total_steps:", total_steps)
print("warmup_steps:", warmup_steps)
"""
    ),
    md("## 8. 학습"),
    code(
        """
def train_epoch() -> float:
    # train split 한 epoch 학습
    model.train()
    total_loss = 0.0

    for batch in train_loader:
        batch = {key: value.to(device) for key, value in batch.items()}
        labels = batch.pop("labels")

        optimizer.zero_grad(set_to_none=True)
        outputs = model(**batch)
        loss = loss_fn(outputs.logits, labels)
        loss.backward()

        # gradient 폭주 방지
        torch.nn.utils.clip_grad_norm_(model.parameters(), max_norm=1.0)
        optimizer.step()
        scheduler.step()

        total_loss += float(loss.item())

    return total_loss / max(1, len(train_loader))


def evaluate_loss(loader: DataLoader) -> float:
    # validation loss 계산
    model.eval()
    total_loss = 0.0

    with torch.no_grad():
        for batch in loader:
            batch = {key: value.to(device) for key, value in batch.items()}
            labels = batch.pop("labels")
            outputs = model(**batch)
            loss = loss_fn(outputs.logits, labels)
            total_loss += float(loss.item())

    return total_loss / max(1, len(loader))


best_valid_loss = float("inf")
best_state = None
patience_count = 0
history: list[dict[str, Any]] = []

for epoch in range(1, EPOCHS + 1):
    train_loss = train_epoch()
    valid_loss = evaluate_loss(valid_loader)
    valid_true, valid_pred, _ = predict_loader(model, valid_loader)
    valid_metrics = compute_metrics(valid_true, valid_pred)

    row = {
        "epoch": epoch,
        "train_loss": round(train_loss, 6),
        "valid_loss": round(valid_loss, 6),
        "valid_macro_f1": valid_metrics["macro_f1"],
        "valid_abnormal_recall": valid_metrics["abnormal_recall"],
    }
    history.append(row)
    print(row)

    # validation loss 기준 early stopping
    # MIN_DELTA보다 크게 좋아진 경우만 개선으로 인정한다.
    if valid_loss < best_valid_loss - MIN_DELTA:
        best_valid_loss = valid_loss
        best_state = {
            key: value.detach().cpu().clone()
            for key, value in model.state_dict().items()
        }
        patience_count = 0
    else:
        patience_count += 1
        if patience_count >= PATIENCE:
            print(f"early stopping: epoch {epoch}")
            break

if best_state is not None:
    model.load_state_dict(best_state)
"""
    ),
    md("## 9. Threshold 선택 및 테스트 평가"),
    code(
        """
# 검증셋에서 label=0 확률을 기반으로 threshold를 선택한다.
valid_true, valid_argmax_pred, valid_abnormal_probs = predict_loader(model, valid_loader)
threshold_info = choose_threshold(valid_true, valid_abnormal_probs, MIN_ABNORMAL_RECALL)
threshold = float(threshold_info["threshold"])
valid_threshold_pred = threshold_predictions(valid_abnormal_probs, threshold)

test_true, test_argmax_pred, test_abnormal_probs = predict_loader(model, test_loader)
test_threshold_pred = threshold_predictions(test_abnormal_probs, threshold)

result = {
    "model_name": MODEL_NAME,
    "label_names": LABEL_NAMES,
    "train_count": len(train_rows),
    "valid_count": len(valid_rows),
    "test_count": len(test_rows),
    "best_valid_loss": round(float(best_valid_loss), 6),
    "history": history,
    "threshold": threshold_info,
    "valid_argmax_metrics": compute_metrics(valid_true, valid_argmax_pred),
    "valid_threshold_metrics": compute_metrics(valid_true, valid_threshold_pred),
    "test_argmax_metrics": compute_metrics(test_true, test_argmax_pred),
    "test_threshold_metrics": compute_metrics(test_true, test_threshold_pred),
}

print(json.dumps(result, ensure_ascii=False, indent=2))
"""
    ),
    md("## 10. 모델과 결과 저장"),
    code(
        """
def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\\n",
        encoding="utf-8",
    )


def write_predictions(path: Path, rows: list[dict[str, Any]], y_pred: list[int], abnormal_probs: list[float]) -> None:
    # 엑셀에서 바로 열 수 있도록 utf-8-sig로 저장한다.
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "id",
                "source_id",
                "true_label",
                "pred_label",
                "abnormal_prob",
                "error_types",
                "review_memo",
                "question",
            ],
        )
        writer.writeheader()
        for row, pred, prob in zip(rows, y_pred, abnormal_probs):
            writer.writerow(
                {
                    "id": row.get("id"),
                    "source_id": row.get("source_id"),
                    "true_label": row.get("label"),
                    "pred_label": pred,
                    "abnormal_prob": round(float(prob), 6),
                    "error_types": "|".join(row.get("error_types") or []),
                    "review_memo": row.get("review_memo", ""),
                    "question": row.get("question", ""),
                }
            )


MODEL_DIR = OUTPUT_DIR / "model"
model.save_pretrained(MODEL_DIR)
tokenizer.save_pretrained(MODEL_DIR)

write_json(OUTPUT_DIR / "results.json", result)
write_predictions(OUTPUT_DIR / "valid_predictions.csv", valid_rows, valid_threshold_pred, valid_abnormal_probs)
write_predictions(OUTPUT_DIR / "test_predictions.csv", test_rows, test_threshold_pred, test_abnormal_probs)

print("저장 완료:", OUTPUT_DIR)
print("모델:", MODEL_DIR)
"""
    ),
]


notebook = {
    "cells": cells,
    "metadata": {
        "kernelspec": {
            "display_name": "Python 3",
            "language": "python",
            "name": "python3",
        },
        "language_info": {
            "name": "python",
            "pygments_lexer": "ipython3",
        },
    },
    "nbformat": 4,
    "nbformat_minor": 5,
}


OUT.write_text(json.dumps(notebook, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(f"created: {OUT}")
