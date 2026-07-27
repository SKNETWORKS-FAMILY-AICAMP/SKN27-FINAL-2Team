from __future__ import annotations

import json
from pathlib import Path


OUT = Path(__file__).resolve().parent / "train_choice_quality_runpod.ipynb"


def md(source: str) -> dict:
    return {
        "cell_type": "markdown",
        "metadata": {},
        "source": source.strip("\n").splitlines(keepends=True),
    }


def code(source: str) -> dict:
    return {
        "cell_type": "code",
        "execution_count": None,
        "metadata": {},
        "outputs": [],
        "source": source.strip("\n").splitlines(keepends=True),
    }


cells = [
    md(
        """
# 선지 이상 여부 BERT 학습

이 노트북은 정답 번호를 맞히는 모델이 아니다.

- 입력 X: 지문 + 질문 + 선지 1개 + 정답 여부
- 출력 y: 해당 선지에 이상이 있는지 여부와 오류 코드
- 정상: 오류 코드 없음
- 이상: 오류 코드 1개 이상

문제 1개를 검수할 때는 선지 5개를 각각 이 모델에 넣고, 선지별 이상 여부를 확인한다.
"""
    ),
    md("## 1. 라이브러리 설치"),
    code("!pip install -q torch transformers scikit-learn pandas numpy tqdm accelerate"),
    md("## 2. 기본 설정"),
    code(
        """
from __future__ import annotations

import csv
import json
import os
import random
from pathlib import Path
from statistics import mean
from typing import Any

import numpy as np
import pandas as pd
import torch
from sklearn.metrics import classification_report, confusion_matrix, precision_recall_fscore_support
from sklearn.model_selection import GroupShuffleSplit
from torch import nn
from torch.utils.data import DataLoader, Dataset
from tqdm.auto import tqdm
from transformers import AutoModelForSequenceClassification, AutoTokenizer, get_linear_schedule_with_warmup


WORKSPACE_DIR = Path.cwd()
DATA_DIR = WORKSPACE_DIR / "common"
TRAIN_JSON = DATA_DIR / "choice_quality_train.json"
TEST_JSON = DATA_DIR / "choice_quality_test.json"
OUTPUT_DIR = WORKSPACE_DIR / "choice_quality_output"

MODEL_NAME = "klue/roberta-base"
MAX_LENGTH = 384
EPOCHS = 8
BATCH_SIZE = 16
LEARNING_RATE = 2e-5
WEIGHT_DECAY = 0.01
WARMUP_RATIO = 0.1
VALID_SIZE = 0.2
PATIENCE = 3
MIN_DELTA = 0.001
SEED = 42

# 오류 코드 확률이 이 값 이상이면 해당 오류가 있다고 본다.
# 학습 후 validation에서 더 좋은 threshold를 자동으로 다시 찾는다.
DEFAULT_THRESHOLD = 0.5

ERROR_TYPE_KO = {
    "ANSWER_IN_PASSAGE": "정답 노출",
    "ANSWER_LENGTH_BIAS": "정답 선지 길이 편향",
    "WEIRD_DISTRACTOR": "이상한 오답 선지",
    "CHOICE_STYLE_MISMATCH": "선지 형식 불일치",
    "CHOICE_TOO_VAGUE": "선지 모호함",
    "CHOICE_GRAMMAR_ERROR": "선지 문장 오류",
    "DUPLICATE_OR_SIMILAR_CHOICE": "선지 중복/유사",
    "ANSWER_FORMAT_ERROR": "정답 형식 오류",
}


def set_seed(seed: int) -> None:
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
print("TRAIN_JSON exists:", TRAIN_JSON.exists())
print("TEST_JSON exists:", TEST_JSON.exists())
"""
    ),
    md("## 3. 데이터 로드 및 validation 분리"),
    code(
        """
def read_json(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        raise FileNotFoundError(f"파일이 없습니다: {path}")
    rows = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(rows, list) or not rows:
        raise ValueError(f"비어 있거나 list가 아닌 JSON입니다: {path}")
    return rows


all_train_rows = read_json(TRAIN_JSON)
test_rows = read_json(TEST_JSON)

# 실제 train 파일 안에서 validation을 나눈다.
groups = [row["question_id"] for row in all_train_rows]
splitter = GroupShuffleSplit(n_splits=1, test_size=VALID_SIZE, random_state=SEED)
train_idx, valid_idx = next(splitter.split(all_train_rows, groups=groups))
train_rows = [all_train_rows[idx] for idx in train_idx]
valid_rows = [all_train_rows[idx] for idx in valid_idx]

# train/test 전체에 등장한 오류 코드 중, 실제 예시가 있는 코드만 학습한다.
ERROR_LABELS = sorted(
    {
        code
        for row in all_train_rows + test_rows
        for code in row.get("error_codes", [])
        if code in ERROR_TYPE_KO
    }
)
ERROR_TO_ID = {code: idx for idx, code in enumerate(ERROR_LABELS)}

print("train:", len(train_rows))
print("valid:", len(valid_rows))
print("test:", len(test_rows))
print("ERROR_LABELS:", ERROR_LABELS)


def label_count(rows: list[dict[str, Any]]) -> dict[str, int]:
    error = sum(1 for row in rows if row.get("label") == 0)
    ok = sum(1 for row in rows if row.get("label") == 1)
    return {"error_0": error, "ok_1": ok}


def error_code_count(rows: list[dict[str, Any]]) -> dict[str, int]:
    return {
        code: sum(1 for row in rows if code in row.get("error_codes", []))
        for code in ERROR_LABELS
    }


print("train label:", label_count(train_rows))
print("valid label:", label_count(valid_rows))
print("test label:", label_count(test_rows))
print("train error codes:", error_code_count(train_rows))
print("sample:", train_rows[0])
"""
    ),
    md("## 4. Dataset 구성"),
    code(
        """
def build_text(row: dict[str, Any]) -> str:
    # 정답 여부는 모델이 맞히는 값이 아니라, 선지를 평가하기 위한 입력 조건이다.
    answer_flag = "정답 선지" if int(row.get("is_answer", 0)) == 1 else "오답 선지"
    return "\\n".join(
        [
            f"정답 여부: {answer_flag}",
            f"지문: {row.get('passage', '')}",
            f"질문: {row.get('question', '')}",
            f"선지: {row.get('choice', '')}",
        ]
    )


def make_error_vector(row: dict[str, Any]) -> torch.Tensor:
    vector = torch.zeros(len(ERROR_LABELS), dtype=torch.float32)
    for code in row.get("error_codes", []):
        if code in ERROR_TO_ID:
            vector[ERROR_TO_ID[code]] = 1.0
    return vector


class ChoiceQualityDataset(Dataset):
    def __init__(self, rows: list[dict[str, Any]], tokenizer: AutoTokenizer, max_length: int) -> None:
        self.rows = rows
        self.tokenizer = tokenizer
        self.max_length = max_length

    def __len__(self) -> int:
        return len(self.rows)

    def __getitem__(self, idx: int) -> dict[str, torch.Tensor]:
        row = self.rows[idx]
        encoded = self.tokenizer(
            build_text(row),
            truncation=True,
            padding="max_length",
            max_length=self.max_length,
            return_tensors="pt",
        )
        item = {key: value.squeeze(0) for key, value in encoded.items()}
        # KLUE RoBERTa 계열은 token_type_ids 때문에 CUDA index 오류가 날 수 있어 제거한다.
        item.pop("token_type_ids", None)
        item["labels"] = make_error_vector(row)
        return item


tokenizer = AutoTokenizer.from_pretrained(MODEL_NAME)

train_dataset = ChoiceQualityDataset(train_rows, tokenizer, MAX_LENGTH)
valid_dataset = ChoiceQualityDataset(valid_rows, tokenizer, MAX_LENGTH)
test_dataset = ChoiceQualityDataset(test_rows, tokenizer, MAX_LENGTH)

train_loader = DataLoader(train_dataset, batch_size=BATCH_SIZE, shuffle=True)
valid_loader = DataLoader(valid_dataset, batch_size=BATCH_SIZE, shuffle=False)
test_loader = DataLoader(test_dataset, batch_size=BATCH_SIZE, shuffle=False)

debug_batch = next(iter(train_loader))
print({key: tuple(value.shape) for key, value in debug_batch.items()})
print("debug labels:", debug_batch["labels"][:2])
"""
    ),
    md("## 5. 모델 준비"),
    code(
        """
model = AutoModelForSequenceClassification.from_pretrained(
    MODEL_NAME,
    num_labels=len(ERROR_LABELS),
    problem_type="multi_label_classification",
)
model.to(device)

optimizer = torch.optim.AdamW(model.parameters(), lr=LEARNING_RATE, weight_decay=WEIGHT_DECAY)
total_steps = len(train_loader) * EPOCHS
warmup_steps = int(total_steps * WARMUP_RATIO)
scheduler = get_linear_schedule_with_warmup(
    optimizer,
    num_warmup_steps=warmup_steps,
    num_training_steps=total_steps,
)

# 오류 코드별 positive가 적기 때문에 pos_weight를 둔다.
train_label_matrix = torch.stack([make_error_vector(row) for row in train_rows])
positive_counts = train_label_matrix.sum(dim=0)
negative_counts = train_label_matrix.shape[0] - positive_counts
pos_weight = negative_counts / torch.clamp(positive_counts, min=1.0)
pos_weight = torch.clamp(pos_weight, min=1.0, max=30.0).to(device)
loss_fn = nn.BCEWithLogitsLoss(pos_weight=pos_weight)

print("positive_counts:", {code: int(positive_counts[idx].item()) for code, idx in ERROR_TO_ID.items()})
print("pos_weight:", {code: round(float(pos_weight[idx].item()), 3) for code, idx in ERROR_TO_ID.items()})
"""
    ),
    md("## 6. 평가 함수"),
    code(
        """
def labels_to_binary(label_matrix: np.ndarray) -> np.ndarray:
    # binary 기준: 오류 코드가 하나라도 있으면 이상(0), 없으면 정상(1)
    has_error = label_matrix.sum(axis=1) > 0
    return np.where(has_error, 0, 1)


def probs_to_binary(prob_matrix: np.ndarray, threshold: float) -> np.ndarray:
    has_error = (prob_matrix >= threshold).any(axis=1)
    return np.where(has_error, 0, 1)


def predict_loader(loader: DataLoader, *, desc: str = "predict") -> tuple[np.ndarray, np.ndarray, float]:
    model.eval()
    all_labels: list[np.ndarray] = []
    all_probs: list[np.ndarray] = []
    losses: list[float] = []

    with torch.no_grad():
        progress = tqdm(loader, desc=desc, leave=False)
        for batch in progress:
            labels = batch.pop("labels").to(device)
            inputs = {key: value.to(device) for key, value in batch.items()}
            outputs = model(**inputs)
            loss = loss_fn(outputs.logits, labels)
            probs = torch.sigmoid(outputs.logits)
            losses.append(float(loss.item()))
            progress.set_postfix(loss=f"{mean(losses):.4f}")

            all_labels.append(labels.detach().cpu().numpy())
            all_probs.append(probs.detach().cpu().numpy())

    return np.vstack(all_labels), np.vstack(all_probs), float(mean(losses)) if losses else 0.0


def compute_binary_metrics(label_matrix: np.ndarray, prob_matrix: np.ndarray, threshold: float) -> dict[str, Any]:
    y_true = labels_to_binary(label_matrix)
    y_pred = probs_to_binary(prob_matrix, threshold)
    report = classification_report(
        y_true,
        y_pred,
        labels=[0, 1],
        target_names=["ERROR", "OK"],
        output_dict=True,
        zero_division=0,
    )
    cm = confusion_matrix(y_true, y_pred, labels=[0, 1])
    return {
        "threshold": threshold,
        "accuracy": round(float((y_true == y_pred).mean()), 6),
        "abnormal_precision": round(float(report["ERROR"]["precision"]), 6),
        "abnormal_recall": round(float(report["ERROR"]["recall"]), 6),
        "abnormal_f1": round(float(report["ERROR"]["f1-score"]), 6),
        "ok_precision": round(float(report["OK"]["precision"]), 6),
        "ok_recall": round(float(report["OK"]["recall"]), 6),
        "ok_f1": round(float(report["OK"]["f1-score"]), 6),
        "confusion_matrix": {
            "labels": ["ERROR", "OK"],
            "matrix": cm.tolist(),
        },
    }


def compute_code_metrics(label_matrix: np.ndarray, prob_matrix: np.ndarray, threshold: float) -> dict[str, Any]:
    metrics: dict[str, Any] = {}
    pred_matrix = (prob_matrix >= threshold).astype(int)
    true_matrix = label_matrix.astype(int)

    for code, idx in ERROR_TO_ID.items():
        precision, recall, f1, support = precision_recall_fscore_support(
            true_matrix[:, idx],
            pred_matrix[:, idx],
            average="binary",
            zero_division=0,
        )
        metrics[code] = {
            "name_ko": ERROR_TYPE_KO.get(code, code),
            "precision": round(float(precision), 6),
            "recall": round(float(recall), 6),
            "f1": round(float(f1), 6),
            "support": int(true_matrix[:, idx].sum()),
        }
    return metrics


def find_best_threshold(label_matrix: np.ndarray, prob_matrix: np.ndarray) -> tuple[float, dict[str, Any]]:
    best_threshold = DEFAULT_THRESHOLD
    best_metrics = compute_binary_metrics(label_matrix, prob_matrix, DEFAULT_THRESHOLD)
    for threshold in np.arange(0.15, 0.86, 0.05):
        metrics = compute_binary_metrics(label_matrix, prob_matrix, float(threshold))
        if metrics["abnormal_f1"] > best_metrics["abnormal_f1"]:
            best_threshold = float(threshold)
            best_metrics = metrics
    return best_threshold, best_metrics
"""
    ),
    md("## 7. 학습"),
    code(
        """
def train_epoch(epoch: int) -> float:
    model.train()
    losses: list[float] = []
    progress = tqdm(train_loader, desc=f"epoch {epoch}/{EPOCHS} train", leave=True)

    for batch in progress:
        labels = batch.pop("labels").to(device)
        inputs = {key: value.to(device) for key, value in batch.items()}

        optimizer.zero_grad(set_to_none=True)
        outputs = model(**inputs)
        loss = loss_fn(outputs.logits, labels)
        loss.backward()
        torch.nn.utils.clip_grad_norm_(model.parameters(), max_norm=1.0)
        optimizer.step()
        scheduler.step()

        losses.append(float(loss.item()))
        progress.set_postfix(loss=f"{mean(losses):.4f}", lr=f"{scheduler.get_last_lr()[0]:.2e}")

    return float(mean(losses)) if losses else 0.0


best_score = -1.0
best_state = None
best_threshold = DEFAULT_THRESHOLD
patience_count = 0
history: list[dict[str, Any]] = []

for epoch in range(1, EPOCHS + 1):
    print(f"\\n===== epoch {epoch}/{EPOCHS} =====")
    train_loss = train_epoch(epoch)
    valid_labels, valid_probs, valid_loss = predict_loader(valid_loader, desc=f"epoch {epoch}/{EPOCHS} valid")
    epoch_threshold, valid_metrics = find_best_threshold(valid_labels, valid_probs)

    row = {
        "epoch": epoch,
        "train_loss": round(train_loss, 6),
        "valid_loss": round(valid_loss, 6),
        "threshold": round(epoch_threshold, 3),
        **valid_metrics,
    }
    history.append(row)
    print(json.dumps(row, ensure_ascii=False, indent=2))

    score = valid_metrics["abnormal_f1"]
    if score > best_score + MIN_DELTA:
        best_score = score
        best_threshold = epoch_threshold
        best_state = {key: value.detach().cpu().clone() for key, value in model.state_dict().items()}
        patience_count = 0
    else:
        patience_count += 1
        if patience_count >= PATIENCE:
            print(f"early stopping: epoch {epoch}")
            break

if best_state is not None:
    model.load_state_dict(best_state)

print("best_threshold:", best_threshold)
print("best_abnormal_f1:", best_score)
"""
    ),
    md("## 8. 테스트 평가 및 저장"),
    code(
        """
valid_labels, valid_probs, valid_loss = predict_loader(valid_loader, desc="final valid")
test_labels, test_probs, test_loss = predict_loader(test_loader, desc="final test")

valid_binary_metrics = compute_binary_metrics(valid_labels, valid_probs, best_threshold)
test_binary_metrics = compute_binary_metrics(test_labels, test_probs, best_threshold)
valid_code_metrics = compute_code_metrics(valid_labels, valid_probs, best_threshold)
test_code_metrics = compute_code_metrics(test_labels, test_probs, best_threshold)

result = {
    "model_name": MODEL_NAME,
    "max_length": MAX_LENGTH,
    "train_count": len(train_rows),
    "valid_count": len(valid_rows),
    "test_count": len(test_rows),
    "error_labels": ERROR_LABELS,
    "best_threshold": round(float(best_threshold), 3),
    "best_valid_abnormal_f1": round(float(best_score), 6),
    "history": history,
    "valid_loss": round(valid_loss, 6),
    "test_loss": round(test_loss, 6),
    "valid_binary_metrics": valid_binary_metrics,
    "test_binary_metrics": test_binary_metrics,
    "valid_code_metrics": valid_code_metrics,
    "test_code_metrics": test_code_metrics,
}

print(json.dumps(result, ensure_ascii=False, indent=2))
"""
    ),
    md("## 9. 예측 파일 저장"),
    code(
        """
def predicted_codes(probs: np.ndarray, threshold: float) -> list[str]:
    return [code for code, idx in ERROR_TO_ID.items() if probs[idx] >= threshold]


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\\n", encoding="utf-8")


def write_predictions(path: Path, rows: list[dict[str, Any]], labels: np.ndarray, probs: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        fieldnames = [
            "id",
            "question_id",
            "source_type",
            "is_answer",
            "true_label",
            "pred_label",
            "true_error_codes",
            "pred_error_codes",
            "max_error_prob",
            "question",
            "choice",
        ] + [f"prob_{code}" for code in ERROR_LABELS]
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()

        true_binary = labels_to_binary(labels)
        pred_binary = probs_to_binary(probs, best_threshold)
        for row, true_label, pred_label, prob in zip(rows, true_binary, pred_binary, probs):
            pred_codes = predicted_codes(prob, best_threshold)
            item = {
                "id": row.get("id"),
                "question_id": row.get("question_id"),
                "source_type": row.get("source_type"),
                "is_answer": row.get("is_answer"),
                "true_label": int(true_label),
                "pred_label": int(pred_label),
                "true_error_codes": "|".join(row.get("error_codes", [])),
                "pred_error_codes": "|".join(pred_codes),
                "max_error_prob": round(float(prob.max()), 6),
                "question": row.get("question", ""),
                "choice": row.get("choice", ""),
            }
            for code, idx in ERROR_TO_ID.items():
                item[f"prob_{code}"] = round(float(prob[idx]), 6)
            writer.writerow(item)


MODEL_DIR = OUTPUT_DIR / "model"
model.save_pretrained(MODEL_DIR)
tokenizer.save_pretrained(MODEL_DIR)

write_json(OUTPUT_DIR / "results.json", result)
write_predictions(OUTPUT_DIR / "valid_predictions.csv", valid_rows, valid_labels, valid_probs)
write_predictions(OUTPUT_DIR / "test_predictions.csv", test_rows, test_labels, test_probs)

print("저장 완료:", OUTPUT_DIR)
print("모델:", MODEL_DIR)
"""
    ),
    md("## 10. 생성 문제 검수 함수"),
    code(
        """
def review_choice(passage: str, question: str, choice: str, is_answer: int) -> dict[str, Any]:
    row = {
        "passage": passage,
        "question": question,
        "choice": choice,
        "is_answer": int(is_answer),
        "error_codes": [],
    }
    dataset = ChoiceQualityDataset([row], tokenizer, MAX_LENGTH)
    batch = next(iter(DataLoader(dataset, batch_size=1, shuffle=False)))
    batch.pop("labels", None)
    inputs = {key: value.to(device) for key, value in batch.items()}

    model.eval()
    with torch.no_grad():
        outputs = model(**inputs)
        probs = torch.sigmoid(outputs.logits)[0].detach().cpu().numpy()

    codes = predicted_codes(probs, best_threshold)
    return {
        "label": 0 if codes else 1,
        "error_codes": codes,
        "error_names_ko": [ERROR_TYPE_KO.get(code, code) for code in codes],
        "max_error_prob": round(float(probs.max()), 6),
        "error_probs": {code: round(float(probs[idx]), 6) for code, idx in ERROR_TO_ID.items()},
    }


def review_question(row: dict[str, Any]) -> dict[str, Any]:
    choices = row.get("choices") or []
    answer = int(row.get("answer") or row.get("answer_number") or 0)
    choice_results = []
    for idx, choice in enumerate(choices, start=1):
        text = choice.get("text") or choice.get("content") if isinstance(choice, dict) else str(choice)
        result = review_choice(
            passage=row.get("passage") or row.get("material") or "",
            question=row.get("question") or "",
            choice=text,
            is_answer=1 if idx == answer else 0,
        )
        result["choice_no"] = idx
        result["choice"] = text
        choice_results.append(result)

    has_error = any(item["label"] == 0 for item in choice_results)
    return {
        "id": row.get("id") or row.get("question_id") or row.get("seed_id"),
        "label": 0 if has_error else 1,
        "choice_results": choice_results,
        "error_codes": sorted({code for item in choice_results for code in item["error_codes"]}),
    }


print("검수 함수 준비 완료")
"""
    ),
]


nb = {
    "cells": cells,
    "metadata": {
        "kernelspec": {
            "display_name": "Python 3 (ipykernel)",
            "language": "python",
            "name": "python3",
        },
        "language_info": {
            "name": "python",
            "version": "3.12.0",
        },
    },
    "nbformat": 4,
    "nbformat_minor": 5,
}

OUT.write_text(json.dumps(nb, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(f"created: {OUT}")
