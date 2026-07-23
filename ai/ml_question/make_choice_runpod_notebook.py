from __future__ import annotations

import json
from pathlib import Path


OUT = Path(__file__).resolve().parent / "train_choice_runpod.ipynb"


def lines(text: str) -> list[str]:
    return text.strip("\n").splitlines(keepends=True)


def md(text: str) -> dict:
    return {"cell_type": "markdown", "metadata": {}, "source": lines(text)}


def code(text: str) -> dict:
    return {"cell_type": "code", "execution_count": None, "metadata": {}, "outputs": [], "source": lines(text)}


cells = [
    md(
        """
# 선지별 정답 판별 BERT 학습

이 노트북은 RunPod에서 선지별 데이터를 학습한다.

- `0`: 오답 선지
- `1`: 정답 선지

1600개 문항의 5개 선지를 각각 데이터로 사용하므로 총 8000개가 된다.
이 모델은 문제 전체 품질 검수라기보다, G3 정답 유일성 검수를 보조하기 위한 정답 후보 판별 모델이다.
"""
    ),
    md("## 1. 라이브러리 설치"),
    code("!pip install -q torch transformers scikit-learn numpy accelerate"),
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
from sklearn.model_selection import GroupShuffleSplit
from torch.utils.data import DataLoader, Dataset
from transformers import AutoModelForSequenceClassification, AutoTokenizer, get_linear_schedule_with_warmup


# RunPod에서는 노트북을 /workspace에서 실행하고,
# 데이터 파일은 /workspace/common 폴더에 둔다.
WORKSPACE_DIR = Path.cwd()
DATA_DIR = WORKSPACE_DIR / "common"
TRAIN_JSON = DATA_DIR / "choice_train.json"
TEST_JSON = DATA_DIR / "choice_test.json"
OUTPUT_DIR = WORKSPACE_DIR / "choice_output"

MODEL_NAME = "klue/roberta-base"
MAX_LENGTH = 384
EPOCHS = 5
BATCH_SIZE = 16
LEARNING_RATE = 2e-5
WEIGHT_DECAY = 0.01
WARMUP_RATIO = 0.1
PATIENCE = 2
MIN_DELTA = 0.0
VALID_SIZE = 0.2
SEED = 42

# 선지별 모델에서는 정답 선지(label=1)를 놓치지 않는 것이 중요하다.
MIN_ANSWER_RECALL = 0.90

LABEL_NAMES = {
    0: "DISTRACTOR",
    1: "ANSWER",
}


def set_seed(seed: int) -> None:
    # 실험 재현성을 위해 seed를 고정한다.
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
print("TEST_JSON exists:", TEST_JSON.exists())
"""
    ),
    md("## 3. 데이터 로드"),
    code(
        """
def read_json(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        raise FileNotFoundError(f"파일이 없습니다: {path}")
    rows = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(rows, list) or not rows:
        raise ValueError(f"비어 있거나 리스트가 아닌 JSON입니다: {path}")
    return rows


def validate_rows(rows: list[dict[str, Any]], split_name: str) -> None:
    for idx, row in enumerate(rows, start=1):
        if row.get("label") not in (0, 1):
            raise ValueError(f"{split_name}[{idx}] label 오류: {row.get('label')}")
        if not row.get("question"):
            raise ValueError(f"{split_name}[{idx}] question 누락")
        if not row.get("choice"):
            raise ValueError(f"{split_name}[{idx}] choice 누락")


all_train_rows = read_json(TRAIN_JSON)
test_rows = read_json(TEST_JSON)

validate_rows(all_train_rows, "train")
validate_rows(test_rows, "test")

# train 파일 안에서 validation을 나눈다.
# 같은 question_id의 선지 5개가 train/valid에 흩어지면 성능이 과대평가될 수 있으므로
# GroupShuffleSplit으로 question_id 기준 분리를 유지한다.
groups = [row["question_id"] for row in all_train_rows]
splitter = GroupShuffleSplit(n_splits=1, test_size=VALID_SIZE, random_state=SEED)
train_idx, valid_idx = next(splitter.split(all_train_rows, groups=groups))
train_rows = [all_train_rows[idx] for idx in train_idx]
valid_rows = [all_train_rows[idx] for idx in valid_idx]

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
    # 선지 1개가 지문/질문 조건에 맞는 정답인지 판단하도록 입력을 만든다.
    return "\\n".join(
        [
            f"지문: {row.get('passage', '')}",
            f"질문: {row.get('question', '')}",
            f"선지: {row.get('choice', '')}",
            f"목표배점: {row.get('target_score', '')}",
        ]
    )


print(build_model_text(train_rows[0]))
"""
    ),
    md("## 5. Dataset과 DataLoader"),
    code(
        """
class ChoiceDataset(Dataset):
    def __init__(self, rows: list[dict[str, Any]], tokenizer: AutoTokenizer, max_length: int) -> None:
        self.rows = rows
        self.tokenizer = tokenizer
        self.max_length = max_length

    def __len__(self) -> int:
        return len(self.rows)

    def __getitem__(self, index: int) -> dict[str, torch.Tensor]:
        row = self.rows[index]
        encoded = self.tokenizer(
            build_model_text(row),
            truncation=True,
            padding="max_length",
            max_length=self.max_length,
            return_tensors="pt",
        )
        item = {key: value.squeeze(0) for key, value in encoded.items()}
        item["labels"] = torch.tensor(int(row["label"]), dtype=torch.long)
        return item


def make_loader(rows: list[dict[str, Any]], shuffle: bool) -> DataLoader:
    dataset = ChoiceDataset(rows, tokenizer, MAX_LENGTH)
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
    # 정답 선지는 1600개, 오답 선지는 6400개라 불균형이 있다.
    # class weight로 정답 선지 손실 비중을 높인다.
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
    labels = [0, 1]
    return {
        "accuracy": round(float(accuracy_score(y_true, y_pred)), 6),
        "macro_f1": round(float(f1_score(y_true, y_pred, average="macro", zero_division=0)), 6),
        "answer_precision": round(float(precision_score(y_true, y_pred, labels=labels, pos_label=1, average="binary", zero_division=0)), 6),
        "answer_recall": round(float(recall_score(y_true, y_pred, labels=labels, pos_label=1, average="binary", zero_division=0)), 6),
        "distractor_precision": round(float(precision_score(y_true, y_pred, labels=labels, pos_label=0, average="binary", zero_division=0)), 6),
        "distractor_recall": round(float(recall_score(y_true, y_pred, labels=labels, pos_label=0, average="binary", zero_division=0)), 6),
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
    model.eval()
    y_true: list[int] = []
    y_pred: list[int] = []
    answer_probs: list[float] = []

    with torch.no_grad():
        for batch in loader:
            batch = {key: value.to(device) for key, value in batch.items()}
            labels = batch.pop("labels")
            outputs = model(**batch)
            probs = torch.softmax(outputs.logits, dim=-1)

            y_true.extend(labels.detach().cpu().tolist())
            y_pred.extend(outputs.logits.argmax(dim=-1).detach().cpu().tolist())
            answer_probs.extend(probs[:, 1].detach().cpu().tolist())

    return y_true, y_pred, answer_probs


def threshold_predictions(answer_probs: list[float], threshold: float) -> list[int]:
    return [1 if prob >= threshold else 0 for prob in answer_probs]


def choose_threshold(y_true: list[int], answer_probs: list[float], min_answer_recall: float) -> dict[str, Any]:
    candidates = [round(i / 100, 2) for i in range(5, 96)]
    scored: list[dict[str, Any]] = []

    for threshold in candidates:
        y_pred = threshold_predictions(answer_probs, threshold)
        metric = compute_metrics(y_true, y_pred)
        scored.append(
            {
                "threshold": threshold,
                "macro_f1": metric["macro_f1"],
                "answer_recall": metric["answer_recall"],
                "answer_precision": metric["answer_precision"],
                "distractor_recall": metric["distractor_recall"],
            }
        )

    enough_recall = [item for item in scored if item["answer_recall"] >= min_answer_recall]
    if enough_recall:
        return max(enough_recall, key=lambda item: (item["macro_f1"], item["answer_precision"], item["threshold"]))
    return max(scored, key=lambda item: (item["answer_recall"], item["macro_f1"], item["answer_precision"]))
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

optimizer = torch.optim.AdamW(model.parameters(), lr=LEARNING_RATE, weight_decay=WEIGHT_DECAY)
total_steps = len(train_loader) * EPOCHS
warmup_steps = int(total_steps * WARMUP_RATIO)
scheduler = get_linear_schedule_with_warmup(
    optimizer,
    num_warmup_steps=warmup_steps,
    num_training_steps=total_steps,
)
loss_fn = torch.nn.CrossEntropyLoss(weight=compute_class_weights(train_rows))

print("model loaded:", MODEL_NAME)
print("class weights:", compute_class_weights(train_rows).detach().cpu().tolist())
"""
    ),
    md("## 8. 학습"),
    code(
        """
def train_epoch() -> float:
    model.train()
    total_loss = 0.0

    for batch in train_loader:
        batch = {key: value.to(device) for key, value in batch.items()}
        labels = batch.pop("labels")

        optimizer.zero_grad(set_to_none=True)
        outputs = model(**batch)
        loss = loss_fn(outputs.logits, labels)
        loss.backward()
        torch.nn.utils.clip_grad_norm_(model.parameters(), max_norm=1.0)
        optimizer.step()
        scheduler.step()

        total_loss += float(loss.item())

    return total_loss / max(1, len(train_loader))


def evaluate_loss(loader: DataLoader) -> float:
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
        "valid_answer_recall": valid_metrics["answer_recall"],
    }
    history.append(row)
    print(row)

    if valid_loss < best_valid_loss - MIN_DELTA:
        best_valid_loss = valid_loss
        best_state = {key: value.detach().cpu().clone() for key, value in model.state_dict().items()}
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
valid_true, valid_argmax_pred, valid_answer_probs = predict_loader(model, valid_loader)
threshold_info = choose_threshold(valid_true, valid_answer_probs, MIN_ANSWER_RECALL)
threshold = float(threshold_info["threshold"])
valid_threshold_pred = threshold_predictions(valid_answer_probs, threshold)

test_true, test_argmax_pred, test_answer_probs = predict_loader(model, test_loader)
test_threshold_pred = threshold_predictions(test_answer_probs, threshold)

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
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\\n", encoding="utf-8")


def write_predictions(path: Path, rows: list[dict[str, Any]], y_pred: list[int], answer_probs: list[float]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "id",
                "question_id",
                "choice_no",
                "true_label",
                "pred_label",
                "answer_prob",
                "choice",
                "question",
            ],
        )
        writer.writeheader()
        for row, pred, prob in zip(rows, y_pred, answer_probs):
            writer.writerow(
                {
                    "id": row.get("id"),
                    "question_id": row.get("question_id"),
                    "choice_no": row.get("choice_no"),
                    "true_label": row.get("label"),
                    "pred_label": pred,
                    "answer_prob": round(float(prob), 6),
                    "choice": row.get("choice", ""),
                    "question": row.get("question", ""),
                }
            )


MODEL_DIR = OUTPUT_DIR / "model"
model.save_pretrained(MODEL_DIR)
tokenizer.save_pretrained(MODEL_DIR)

write_json(OUTPUT_DIR / "results.json", result)
write_predictions(OUTPUT_DIR / "valid_predictions.csv", valid_rows, valid_threshold_pred, valid_answer_probs)
write_predictions(OUTPUT_DIR / "test_predictions.csv", test_rows, test_threshold_pred, test_answer_probs)

print("저장 완료:", OUTPUT_DIR)
print("모델:", MODEL_DIR)
"""
    ),
    md("## 11. 생성 문제 검수 함수"),
    code(
        """
import re
from statistics import mean


def normalize_text(text: str) -> str:
    # 공백 차이 때문에 포함 여부를 놓치지 않도록 공백을 제거한다.
    return re.sub(r"\\s+", "", str(text or "")).lower()


def predict_answer_probs_for_question(row: dict[str, Any]) -> list[float]:
    # 생성 문제 1개를 선지 5개로 나누어 각각 정답 확률을 계산한다.
    model.eval()
    probs: list[float] = []

    choices = row.get("choices") or []
    for idx, choice in enumerate(choices, start=1):
        choice_row = {
            "passage": row.get("passage", ""),
            "question": row.get("question", ""),
            "choice": choice,
            "choice_no": idx,
            "target_score": row.get("target_score", ""),
        }
        encoded = tokenizer(
            build_model_text(choice_row),
            truncation=True,
            padding="max_length",
            max_length=MAX_LENGTH,
            return_tensors="pt",
        )
        encoded = {key: value.to(device) for key, value in encoded.items()}
        with torch.no_grad():
            outputs = model(**encoded)
            prob = torch.softmax(outputs.logits, dim=-1)[0, 1].item()
        probs.append(float(prob))

    return probs


def check_answer_length_bias(row: dict[str, Any], *, ratio_threshold: float = 1.5, diff_threshold: int = 12) -> dict[str, Any] | None:
    # 정답 선지만 다른 선지보다 지나치게 길거나 짧으면 외형 편향으로 본다.
    choices = row.get("choices") or []
    answer = row.get("answer")
    if not answer or len(choices) < 2:
        return None

    answer_idx = int(answer) - 1
    if answer_idx < 0 or answer_idx >= len(choices):
        return {
            "type": "ANSWER_FORMAT_ERROR",
            "message": "정답 번호가 선택지 범위를 벗어남",
        }

    answer_len = len(str(choices[answer_idx]))
    other_lengths = [
        len(str(choice))
        for idx, choice in enumerate(choices)
        if idx != answer_idx
    ]
    avg_other_len = mean(other_lengths)
    diff = answer_len - avg_other_len

    too_long = answer_len >= avg_other_len * ratio_threshold and diff >= diff_threshold
    too_short = answer_len * ratio_threshold <= avg_other_len and abs(diff) >= diff_threshold
    if not (too_long or too_short):
        return None

    return {
        "type": "ANSWER_LENGTH_BIAS",
        "message": "정답 선지가 다른 선지들에 비해 유독 길거나 짧음",
        "answer_length": answer_len,
        "other_avg_length": round(avg_other_len, 2),
        "diff": round(diff, 2),
    }


def check_answer_in_passage(row: dict[str, Any]) -> dict[str, Any] | None:
    # 정답 선지 원문이 지문 또는 질문에 그대로 포함되면 정답 노출로 본다.
    choices = row.get("choices") or []
    answer = row.get("answer")
    if not answer:
        return None

    answer_idx = int(answer) - 1
    if answer_idx < 0 or answer_idx >= len(choices):
        return None

    answer_text = normalize_text(choices[answer_idx])
    passage = normalize_text(row.get("passage", ""))
    question = normalize_text(row.get("question", ""))
    if not answer_text:
        return None

    found_in = []
    if answer_text in passage:
        found_in.append("passage")
    if answer_text in question:
        found_in.append("question")

    if not found_in:
        return None

    return {
        "type": "ANSWER_IN_PASSAGE",
        "message": "정답 선지가 지문 또는 질문에 포함되어 있음",
        "found_in": found_in,
    }


def check_answer_candidate_count(answer_probs: list[float], *, threshold: float) -> dict[str, Any] | None:
    # threshold 이상인 선지를 정답 후보로 보고, 후보가 1개가 아니면 이상으로 본다.
    candidate_numbers = [
        idx + 1
        for idx, prob in enumerate(answer_probs)
        if float(prob) >= threshold
    ]

    if len(candidate_numbers) == 1:
        return None

    if len(candidate_numbers) == 0:
        issue_type = "NO_ANSWER_CANDIDATE"
        message = "정답 후보가 0개임"
    else:
        issue_type = "MULTIPLE_ANSWER_CANDIDATES"
        message = "정답 후보가 2개 이상임"

    return {
        "type": issue_type,
        "message": message,
        "threshold": threshold,
        "candidate_count": len(candidate_numbers),
        "candidate_numbers": candidate_numbers,
        "answer_probs": [round(float(prob), 6) for prob in answer_probs],
    }


def review_generated_question(row: dict[str, Any], *, threshold: float | None = None) -> dict[str, Any]:
    # 생성 문제 1개에 대해 최종 이상 여부와 오류 유형을 반환한다.
    if threshold is None:
        threshold = float(result["threshold"]["threshold"])

    answer_probs = predict_answer_probs_for_question(row)
    issues = [
        issue
        for issue in [
            check_answer_length_bias(row),
            check_answer_in_passage(row),
            check_answer_candidate_count(answer_probs, threshold=threshold),
        ]
        if issue is not None
    ]

    return {
        "id": row.get("id") or row.get("question_id"),
        "label": 0 if issues else 1,
        "issue_types": [issue["type"] for issue in issues],
        "issues": issues,
        "answer_probs": [round(float(prob), 6) for prob in answer_probs],
        "threshold": threshold,
    }


print("검수 함수 준비 완료")
"""
    ),
    md("## 12. 생성 문제 파일 검수 예시"),
    code(
        """
# 생성 문제 파일을 검수할 때 사용하는 예시 코드이다.
# 파일 형식은 아래 필드를 가진 JSON 리스트를 권장한다.
# id, passage, question, choices, answer, target_score

GENERATED_JSON = DATA_DIR / "generated_questions.json"

if GENERATED_JSON.exists():
    generated_rows = read_json(GENERATED_JSON)
    review_results = [
        review_generated_question(row)
        for row in generated_rows
    ]
    write_json(OUTPUT_DIR / "generated_review_results.json", review_results)

    abnormal_count = sum(item["label"] == 0 for item in review_results)
    print("검수 대상:", len(review_results))
    print("이상 문제:", abnormal_count)
    print("저장:", OUTPUT_DIR / "generated_review_results.json")
else:
    print("생성 문제 파일이 없어서 예시 셀은 건너뜁니다:", GENERATED_JSON)
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
