from __future__ import annotations

import json
from pathlib import Path


# 이 파일은 RunPod에서 실행할 학습 노트북을 생성하는 로컬 스크립트입니다.
# 실제 학습은 생성된 train_choice_quality_runpod_v5.ipynb 파일에서 진행합니다.
OUT = Path(__file__).resolve().parent / "train_choice_quality_runpod_v5.ipynb"


def md(source: str) -> dict:
    return {"cell_type": "markdown", "metadata": {}, "source": source.strip("\n").splitlines(keepends=True)}


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
# 선지 단위 문제 품질 검수 BERT 학습 v5

이 노트북은 **이진 분류 모델**을 학습합니다.

- 입력 X: 지문, 질문, 선지 1개, 정답 여부
- y: `label`
- `label=0`: 이상 있음
- `label=1`: 이상 없음

`error_codes`는 모델의 직접 학습 y가 아니라, 왜 이상인지 확인하기 위한 보조 정보입니다.
예측 결과에서는 BERT가 이상 여부를 판단하고, 오류코드는 규칙 기반 추정값 또는 정답 라벨의 참고값으로 함께 저장합니다.

RunPod 폴더 구조:

```text
/workspace/
├─ train_choice_quality_runpod_v5.ipynb
└─ common/
   ├─ choice_quality_train_v4.json
   └─ choice_quality_test_v4.json
```
"""
    ),
    md("## 1. 패키지 설치"),
    code(
        """
# RunPod 기본 이미지에 없을 수 있는 학습용 패키지를 설치합니다.
!pip -q install transformers accelerate scikit-learn tqdm
"""
    ),
    md("## 2. 라이브러리 불러오기"),
    code(
        """
from __future__ import annotations

# 기본 라이브러리
import csv
import json
import random
import re
from pathlib import Path
from typing import Any

# 학습/평가 라이브러리
import numpy as np
import torch
from sklearn.metrics import accuracy_score, confusion_matrix, precision_recall_fscore_support
from sklearn.model_selection import GroupShuffleSplit
from torch.utils.data import DataLoader, Dataset
from tqdm.auto import tqdm
from transformers import AutoModelForSequenceClassification, AutoTokenizer, get_linear_schedule_with_warmup


def set_seed(seed: int) -> None:
    # 같은 데이터와 파라미터로 실행했을 때 최대한 비슷한 결과가 나오도록 seed를 고정합니다.
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    torch.cuda.manual_seed_all(seed)


SEED = 42
set_seed(SEED)
"""
    ),
    md("## 3. 파라미터와 경로 설정"),
    code(
        """
# 기본 파라미터입니다.
# 처음에는 base 모델로 빠르게 확인하고, 결과가 부족하면 klue/roberta-large로 바꿔볼 수 있습니다.
MODEL_NAME = "klue/roberta-base"

# 입력 토큰 최대 길이입니다.
# 지문 + 질문 + 선지 + 정답 여부를 모두 넣기 때문에 512로 둡니다.
MAX_LENGTH = 512

# 최대 epoch입니다. Early stopping이 있으므로 항상 10번을 끝까지 도는 것은 아닙니다.
EPOCHS = 10

# GPU 메모리가 부족하면 8로 줄이고, 여유가 있으면 32까지 올려볼 수 있습니다.
BATCH_SIZE = 16

# BERT/RoBERTa fine-tuning에서 흔히 쓰는 시작값입니다.
LEARNING_RATE = 2e-5

# 과적합을 줄이기 위한 가중치 감쇠입니다.
WEIGHT_DECAY = 0.01

# 초반 학습률을 천천히 올리는 비율입니다.
WARMUP_RATIO = 0.1

# train 파일 안에서 validation으로 뗄 비율입니다.
VALID_SIZE = 0.2

# validation 성능이 좋아지지 않으면 몇 epoch 후 멈출지 정합니다.
PATIENCE = 3
MIN_DELTA = 0.0

# threshold 선택 시 이상 데이터 recall을 최소한 이 정도는 확보하려고 합니다.
# 2차 검수 모델은 이상을 놓치지 않는 것이 중요하기 때문입니다.
MIN_ABNORMAL_RECALL = 0.80

# RunPod 기준 경로입니다.
WORKSPACE_DIR = Path("/workspace")
DATA_DIR = WORKSPACE_DIR / "common"
TRAIN_JSON = DATA_DIR / "choice_quality_train_v4.json"
TEST_JSON = DATA_DIR / "choice_quality_test_v4.json"
OUTPUT_DIR = WORKSPACE_DIR / "choice_quality_output_v5"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
print("device:", device)
if torch.cuda.is_available():
    print("gpu:", torch.cuda.get_device_name(0))
print("TRAIN_JSON exists:", TRAIN_JSON.exists())
print("TEST_JSON exists:", TEST_JSON.exists())
"""
    ),
    md("## 4. 데이터 로드와 train/validation 분리"),
    code(
        """
def read_json(path: Path) -> Any:
    # 전처리된 JSON 파일을 읽습니다.
    return json.loads(path.read_text(encoding="utf-8"))


all_train_rows = read_json(TRAIN_JSON)
test_rows = read_json(TEST_JSON)

# train 파일 안에서 validation을 나눕니다.
# 같은 문항의 5개 선지가 train/valid에 섞이면 데이터 누수가 생깁니다.
# 그래서 question_id 기준으로 묶어서 분리합니다.
groups = [row["question_id"] for row in all_train_rows]
splitter = GroupShuffleSplit(n_splits=1, test_size=VALID_SIZE, random_state=SEED)
train_idx, valid_idx = next(splitter.split(all_train_rows, groups=groups))
train_rows = [all_train_rows[idx] for idx in train_idx]
valid_rows = [all_train_rows[idx] for idx in valid_idx]


def count_binary(rows: list[dict[str, Any]]) -> dict[str, int]:
    # label=0은 이상, label=1은 정상입니다.
    return {
        "error_0": sum(1 for row in rows if int(row["label"]) == 0),
        "ok_1": sum(1 for row in rows if int(row["label"]) == 1),
    }


# 오류 코드는 학습 y가 아니라 결과 해석용 참고 정보입니다.
ERROR_CODES = sorted({code for row in (train_rows + valid_rows + test_rows) for code in row.get("error_codes", [])})

print("train:", len(train_rows), count_binary(train_rows))
print("valid:", len(valid_rows), count_binary(valid_rows))
print("test:", len(test_rows), count_binary(test_rows))
print("reference error codes:", ERROR_CODES)
"""
    ),
    md("## 5. 토크나이저와 Dataset 생성"),
    code(
        """
tokenizer = AutoTokenizer.from_pretrained(MODEL_NAME)


def make_input_text(row: dict[str, Any]) -> str:
    # BERT에 넣을 최종 입력 문장입니다.
    # 정답 여부는 맞히는 대상이 아니라 검수 판단을 돕는 입력 feature입니다.
    is_answer_text = "정답 선지" if int(row.get("is_answer", 0)) == 1 else "오답 선지"
    return (
        "[지문]\\n"
        + str(row.get("passage", ""))
        + "\\n\\n[질문]\\n"
        + str(row.get("question", ""))
        + "\\n\\n[선지]\\n"
        + str(row.get("choice", ""))
        + "\\n\\n[정답 여부]\\n"
        + is_answer_text
    )


class ChoiceQualityBinaryDataset(Dataset):
    # JSON row를 토큰화해서 PyTorch Dataset 형태로 바꿉니다.
    def __init__(self, rows: list[dict[str, Any]], tokenizer: Any, max_length: int):
        self.rows = rows
        self.tokenizer = tokenizer
        self.max_length = max_length

    def __len__(self) -> int:
        return len(self.rows)

    def __getitem__(self, idx: int) -> dict[str, torch.Tensor]:
        row = self.rows[idx]

        # padding=max_length를 사용해 모든 batch의 길이를 동일하게 맞춥니다.
        encoded = self.tokenizer(
            make_input_text(row),
            truncation=True,
            padding="max_length",
            max_length=self.max_length,
            return_tensors="pt",
        )
        item = {key: value.squeeze(0) for key, value in encoded.items()}

        # 이진 분류 y입니다. 0=이상, 1=정상.
        item["labels"] = torch.tensor(int(row["label"]), dtype=torch.long)
        return item


train_dataset = ChoiceQualityBinaryDataset(train_rows, tokenizer, MAX_LENGTH)
valid_dataset = ChoiceQualityBinaryDataset(valid_rows, tokenizer, MAX_LENGTH)
test_dataset = ChoiceQualityBinaryDataset(test_rows, tokenizer, MAX_LENGTH)

train_loader = DataLoader(train_dataset, batch_size=BATCH_SIZE, shuffle=True)
valid_loader = DataLoader(valid_dataset, batch_size=BATCH_SIZE, shuffle=False)
test_loader = DataLoader(test_dataset, batch_size=BATCH_SIZE, shuffle=False)
"""
    ),
    md("## 6. 모델과 Loss 설정"),
    code(
        """
# num_labels=2이므로 이진 분류 모델입니다.
model = AutoModelForSequenceClassification.from_pretrained(MODEL_NAME, num_labels=2)
model.to(device)

# label=0 이상 데이터가 정상 데이터보다 적으므로 class weight를 적용합니다.
# 이렇게 하면 모델이 전부 정상이라고 찍는 방향으로 치우치는 것을 줄일 수 있습니다.
label_counts = count_binary(train_rows)
count_error = max(label_counts["error_0"], 1)
count_ok = max(label_counts["ok_1"], 1)
total = count_error + count_ok
class_weights = torch.tensor(
    [total / (2 * count_error), total / (2 * count_ok)],
    dtype=torch.float32,
).to(device)

loss_fn = torch.nn.CrossEntropyLoss(weight=class_weights)
optimizer = torch.optim.AdamW(model.parameters(), lr=LEARNING_RATE, weight_decay=WEIGHT_DECAY)

total_steps = len(train_loader) * EPOCHS
warmup_steps = int(total_steps * WARMUP_RATIO)
scheduler = get_linear_schedule_with_warmup(
    optimizer,
    num_warmup_steps=warmup_steps,
    num_training_steps=total_steps,
)

print("class_weights [ERROR_0, OK_1]:", class_weights.detach().cpu().tolist())
"""
    ),
    md("## 7. 평가 함수와 Threshold 탐색"),
    code(
        """
def probs_to_label(error_probs: np.ndarray, threshold: float) -> np.ndarray:
    # error 확률이 threshold 이상이면 0=이상, 아니면 1=정상입니다.
    return np.where(error_probs >= threshold, 0, 1)


def compute_binary_metrics(true_labels: np.ndarray, error_probs: np.ndarray, threshold: float) -> dict[str, Any]:
    # 이 작업에서는 accuracy보다 abnormal_recall / abnormal_f1이 더 중요합니다.
    # 이상 문제를 놓치는 것이 2차 검수 모델에서 가장 큰 리스크이기 때문입니다.
    pred_labels = probs_to_label(error_probs, threshold)
    precision, recall, f1, _ = precision_recall_fscore_support(
        true_labels,
        pred_labels,
        labels=[0, 1],
        zero_division=0,
    )
    return {
        "threshold": round(float(threshold), 3),
        "accuracy": round(float(accuracy_score(true_labels, pred_labels)), 6),
        "abnormal_precision": round(float(precision[0]), 6),
        "abnormal_recall": round(float(recall[0]), 6),
        "abnormal_f1": round(float(f1[0]), 6),
        "ok_precision": round(float(precision[1]), 6),
        "ok_recall": round(float(recall[1]), 6),
        "ok_f1": round(float(f1[1]), 6),
        "confusion_matrix_labels": ["ERROR_0", "OK_1"],
        "confusion_matrix": confusion_matrix(true_labels, pred_labels, labels=[0, 1]).tolist(),
    }


def find_best_threshold(true_labels: np.ndarray, error_probs: np.ndarray) -> tuple[float, float, dict[str, Any]]:
    # 기본 0.5 threshold만 쓰지 않고 validation set에서 가장 적절한 threshold를 찾습니다.
    # 단, 이상 데이터 recall이 너무 낮은 threshold는 피합니다.
    best_threshold = 0.5
    best_score = -1.0
    best_metrics: dict[str, Any] = {}
    fallback_threshold = 0.5
    fallback_score = -1.0
    fallback_metrics: dict[str, Any] = {}

    for threshold in np.arange(0.05, 0.96, 0.05):
        metrics = compute_binary_metrics(true_labels, error_probs, float(threshold))
        score = metrics["abnormal_f1"]
        if score > fallback_score:
            fallback_threshold = float(threshold)
            fallback_score = score
            fallback_metrics = metrics
        if metrics["abnormal_recall"] >= MIN_ABNORMAL_RECALL and score > best_score:
            best_threshold = float(threshold)
            best_score = score
            best_metrics = metrics

    if best_score < 0:
        return fallback_threshold, fallback_score, fallback_metrics
    return best_threshold, best_score, best_metrics
"""
    ),
    md("## 8. 학습/예측 함수"),
    code(
        """
def train_one_epoch() -> float:
    # 한 epoch 동안 train_loader 전체를 학습합니다.
    model.train()
    total_loss = 0.0
    progress = tqdm(train_loader, desc="train", leave=False)
    for batch in progress:
        # labels는 loss 계산용 y이고, 나머지는 모델 입력입니다.
        labels = batch.pop("labels").to(device)
        inputs = {key: value.to(device) for key, value in batch.items()}

        optimizer.zero_grad(set_to_none=True)
        outputs = model(**inputs)
        loss = loss_fn(outputs.logits, labels)
        loss.backward()
        torch.nn.utils.clip_grad_norm_(model.parameters(), max_norm=1.0)
        optimizer.step()
        scheduler.step()

        total_loss += float(loss.item())
        progress.set_postfix(loss=round(float(loss.item()), 4))

    return total_loss / max(len(train_loader), 1)


@torch.no_grad()
def predict_loader(loader: DataLoader, desc: str) -> tuple[np.ndarray, np.ndarray, float]:
    # validation/test 데이터에 대해 예측 확률을 계산합니다.
    # softmax 결과 중 class 0, 즉 이상 확률만 따로 저장합니다.
    model.eval()
    labels_all = []
    error_probs_all = []
    total_loss = 0.0
    progress = tqdm(loader, desc=desc, leave=False)
    for batch in progress:
        labels = batch.pop("labels").to(device)
        inputs = {key: value.to(device) for key, value in batch.items()}
        outputs = model(**inputs)
        loss = loss_fn(outputs.logits, labels)
        probs = torch.softmax(outputs.logits, dim=-1)

        total_loss += float(loss.item())
        labels_all.append(labels.detach().cpu().numpy())
        error_probs_all.append(probs[:, 0].detach().cpu().numpy())

    labels_np = np.concatenate(labels_all, axis=0) if labels_all else np.zeros((0,), dtype=np.int64)
    error_probs_np = np.concatenate(error_probs_all, axis=0) if error_probs_all else np.zeros((0,), dtype=np.float32)
    return labels_np, error_probs_np, total_loss / max(len(loader), 1)
"""
    ),
    md("## 9. 학습"),
    code(
        """
best_state = None
best_score = -1.0
best_threshold = 0.5
best_metrics = {}
bad_epochs = 0
history = []

for epoch in range(1, EPOCHS + 1):
    # 학습 후 validation 성능을 보고 가장 좋은 모델 가중치를 저장합니다.
    train_loss = train_one_epoch()
    valid_labels, valid_error_probs, valid_loss = predict_loader(valid_loader, desc=f"valid epoch {epoch}")
    threshold, score, metrics = find_best_threshold(valid_labels, valid_error_probs)

    item = {
        "epoch": epoch,
        "train_loss": round(train_loss, 6),
        "valid_loss": round(valid_loss, 6),
        "threshold": round(float(threshold), 3),
        "valid_abnormal_f1": round(float(metrics.get("abnormal_f1", 0)), 6),
        "valid_abnormal_recall": round(float(metrics.get("abnormal_recall", 0)), 6),
    }
    history.append(item)
    print(json.dumps(item, ensure_ascii=False))

    if score > best_score + MIN_DELTA:
        # 현재 epoch가 가장 좋으면 모델 상태를 CPU 메모리에 보관합니다.
        best_score = score
        best_threshold = threshold
        best_metrics = metrics
        best_state = {key: value.detach().cpu().clone() for key, value in model.state_dict().items()}
        bad_epochs = 0
    else:
        bad_epochs += 1
        if bad_epochs >= PATIENCE:
            # 성능 개선이 PATIENCE만큼 없으면 조기 종료합니다.
            print(f"early stopping: epoch {epoch}")
            break

if best_state is not None:
    model.load_state_dict(best_state)

print("best threshold:", best_threshold)
print("best valid metrics:", json.dumps(best_metrics, ensure_ascii=False, indent=2))
"""
    ),
    md("## 10. 최종 평가"),
    code(
        """
def normalize(text: Any) -> str:
    # 간단한 규칙 기반 오류 코드 추정에 쓰는 정규화 함수입니다.
    return re.sub(r"\\s+", "", str(text or "")).lower()


def infer_error_codes(row: dict[str, Any]) -> list[str]:
    # 운영 결과에 표시할 규칙 기반 오류 코드 추정입니다.
    # BERT는 label=0/1만 예측하고, 이 함수는 설명 보조용입니다.
    codes = []
    passage_question = normalize(str(row.get("passage", "")) + " " + str(row.get("question", "")))
    choice = str(row.get("choice", ""))
    choice_norm = normalize(choice)

    if int(row.get("is_answer", 0)) == 1 and choice_norm and choice_norm in passage_question:
        codes.append("ANSWER_IN_PASSAGE")
    if re.search(r"[A-Za-zА-Яа-я一-龥]", choice):
        codes.append("CHOICE_FORMAT_ERROR")
    if re.search(r"근거로|풀이|정답|오답|선택지|자료를 보면", choice):
        codes.append("CHOICE_FORMAT_ERROR")
    if re.search(r"\\([가-힣A-Za-z]\\)", choice) and not re.search(r"\\([가-힣A-Za-z]\\)", str(row.get("passage", ""))):
        codes.append("QUESTION_FORMAT_ERROR")
    if len(choice.strip()) <= 8:
        codes.append("ANSWER_LENGTH_BIAS" if int(row.get("is_answer", 0)) == 1 else "WEIRD_CHOICE")

    return sorted(set(codes)) or ["UNKNOWN_ERROR_TYPE"]


valid_labels, valid_error_probs, valid_loss = predict_loader(valid_loader, desc="final valid")
test_labels, test_error_probs, test_loss = predict_loader(test_loader, desc="final test")

valid_binary_metrics = compute_binary_metrics(valid_labels, valid_error_probs, best_threshold)
test_binary_metrics = compute_binary_metrics(test_labels, test_error_probs, best_threshold)


def source_binary_metrics(rows: list[dict[str, Any]], labels: np.ndarray, error_probs: np.ndarray) -> dict[str, Any]:
    # 기출/팀원 생성 데이터처럼 source_type별 성능을 따로 확인합니다.
    result = {}
    for source_type in sorted({row.get("source_type", "unknown") for row in rows}):
        indices = [idx for idx, row in enumerate(rows) if row.get("source_type", "unknown") == source_type]
        if not indices:
            continue
        source_labels = labels[indices]
        source_probs = error_probs[indices]
        metrics = compute_binary_metrics(source_labels, source_probs, best_threshold)
        metrics["count"] = len(indices)
        metrics["true_error_count"] = int((source_labels == 0).sum())
        metrics["true_ok_count"] = int((source_labels == 1).sum())
        result[source_type] = metrics
    return result


def question_binary_metrics(rows: list[dict[str, Any]], labels: np.ndarray, error_probs: np.ndarray, threshold: float) -> dict[str, Any]:
    # 운영 관점의 문항 단위 성능입니다.
    # 선지 하나라도 이상이면 해당 문항은 재검토/수정 대상으로 봅니다.
    question_map: dict[str, list[int]] = {}
    for idx, row in enumerate(rows):
        question_map.setdefault(str(row.get("question_id")), []).append(idx)

    true_question_labels = []
    pred_question_labels = []
    for indices in question_map.values():
        true_has_error = any(int(labels[idx]) == 0 for idx in indices)
        pred_has_error = any(float(error_probs[idx]) >= threshold for idx in indices)
        true_question_labels.append(0 if true_has_error else 1)
        pred_question_labels.append(0 if pred_has_error else 1)

    metrics = compute_binary_metrics(
        np.array(true_question_labels, dtype=np.int64),
        np.where(np.array(pred_question_labels, dtype=np.int64) == 0, 1.0, 0.0),
        0.5,
    )
    metrics["threshold"] = round(float(threshold), 3)
    return metrics


def build_threshold_report(rows: list[dict[str, Any]], labels: np.ndarray, error_probs: np.ndarray) -> list[dict[str, Any]]:
    # threshold별 선지 단위/문항 단위 성능을 비교합니다.
    # 운영 threshold를 정할 때 이 표를 먼저 봅니다.
    report = []
    for threshold in [0.05, 0.1, 0.15, 0.2, 0.3, 0.5, 0.7]:
        choice_metrics = compute_binary_metrics(labels, error_probs, threshold)
        question_metrics = question_binary_metrics(rows, labels, error_probs, threshold)
        report.append(
            {
                "threshold": threshold,
                "choice_accuracy": choice_metrics["accuracy"],
                "choice_abnormal_precision": choice_metrics["abnormal_precision"],
                "choice_abnormal_recall": choice_metrics["abnormal_recall"],
                "choice_abnormal_f1": choice_metrics["abnormal_f1"],
                "question_accuracy": question_metrics["accuracy"],
                "question_abnormal_precision": question_metrics["abnormal_precision"],
                "question_abnormal_recall": question_metrics["abnormal_recall"],
                "question_abnormal_f1": question_metrics["abnormal_f1"],
            }
        )
    return report


threshold_report = build_threshold_report(test_rows, test_labels, test_error_probs)


# results.json에 저장할 최종 학습/평가 요약입니다.
result = {
    "model_name": MODEL_NAME,
    "task": "binary_classification",
    "label_definition": {"0": "ERROR", "1": "OK"},
    "max_length": MAX_LENGTH,
    "epochs": EPOCHS,
    "batch_size": BATCH_SIZE,
    "learning_rate": LEARNING_RATE,
    "train_count": len(train_rows),
    "valid_count": len(valid_rows),
    "test_count": len(test_rows),
    "input_data": "passage/material + question + one choice + is_answer",
    "y_value": "binary label only. label=0 ERROR, label=1 OK.",
    "error_code_note": "error_codes are auxiliary explanations, not the BERT training target.",
    "reference_error_codes": ERROR_CODES,
    "best_threshold": round(float(best_threshold), 3),
    "history": history,
    "valid_loss": round(float(valid_loss), 6),
    "test_loss": round(float(test_loss), 6),
    "valid_binary_metrics": valid_binary_metrics,
    "test_binary_metrics": test_binary_metrics,
    "test_question_metrics": question_binary_metrics(test_rows, test_labels, test_error_probs, best_threshold),
    "test_source_metrics": source_binary_metrics(test_rows, test_labels, test_error_probs),
    "threshold_report": threshold_report,
}

print(json.dumps(result, ensure_ascii=False, indent=2))
"""
    ),
    md("## 11. 모델과 예측 결과 저장"),
    code(
        """
def write_json(path: Path, data: Any) -> None:
    # 결과 JSON 저장 함수입니다.
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\\n", encoding="utf-8")


def write_predictions(path: Path, rows: list[dict[str, Any]], labels: np.ndarray, error_probs: np.ndarray) -> None:
    # 각 선지별 예측 결과를 CSV로 저장합니다.
    # true_error_codes는 정답 라벨의 참고 사유이고,
    # pred_error_codes는 모델이 이상이라고 판단한 경우 규칙으로 추정한 참고 사유입니다.
    path.parent.mkdir(parents=True, exist_ok=True)
    pred_labels = probs_to_label(error_probs, best_threshold)

    with path.open("w", encoding="utf-8-sig", newline="") as f:
        fieldnames = [
            "id",
            "question_id",
            "source_type",
            "is_answer",
            "true_label",
            "pred_label",
            "error_prob",
            "true_error_codes",
            "pred_error_codes",
            "question",
            "choice",
        ]
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()

        for row, true_label, pred_label, error_prob in zip(rows, labels, pred_labels, error_probs):
            pred_codes = infer_error_codes(row) if int(pred_label) == 0 else []
            writer.writerow(
                {
                    "id": row.get("id"),
                    "question_id": row.get("question_id"),
                    "source_type": row.get("source_type"),
                    "is_answer": row.get("is_answer"),
                    "true_label": int(true_label),
                    "pred_label": int(pred_label),
                    "error_prob": round(float(error_prob), 6),
                    "true_error_codes": "|".join(row.get("error_codes", [])),
                    "pred_error_codes": "|".join(pred_codes),
                    "question": row.get("question", ""),
                    "choice": row.get("choice", ""),
                }
            )


def write_threshold_report(path: Path, report: list[dict[str, Any]]) -> None:
    # threshold별 성능 비교표를 CSV로 저장합니다.
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        fieldnames = [
            "threshold",
            "choice_accuracy",
            "choice_abnormal_precision",
            "choice_abnormal_recall",
            "choice_abnormal_f1",
            "question_accuracy",
            "question_abnormal_precision",
            "question_abnormal_recall",
            "question_abnormal_f1",
        ]
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(report)


def write_question_predictions(path: Path, rows: list[dict[str, Any]], labels: np.ndarray, error_probs: np.ndarray) -> None:
    # 문항 단위 예측 결과를 저장합니다.
    # 운영에서는 이 파일을 보고 어떤 문항의 몇 번 선지를 교체할지 판단하면 됩니다.
    path.parent.mkdir(parents=True, exist_ok=True)
    pred_labels = probs_to_label(error_probs, best_threshold)

    question_map: dict[str, list[int]] = {}
    for idx, row in enumerate(rows):
        question_map.setdefault(str(row.get("question_id")), []).append(idx)

    with path.open("w", encoding="utf-8-sig", newline="") as f:
        fieldnames = [
            "question_id",
            "source_type",
            "true_question_label",
            "pred_question_label",
            "true_error_choice_numbers",
            "pred_error_choice_numbers",
            "max_error_prob",
            "question",
            "true_error_codes",
            "pred_error_codes",
        ]
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()

        for question_id, indices in question_map.items():
            question_rows = [rows[idx] for idx in indices]
            true_error_numbers = [str(rows[idx].get("choice_no")) for idx in indices if int(labels[idx]) == 0]
            pred_error_numbers = [str(rows[idx].get("choice_no")) for idx in indices if int(pred_labels[idx]) == 0]
            true_codes = sorted({code for idx in indices for code in rows[idx].get("error_codes", [])})
            pred_codes = sorted({code for idx in indices if int(pred_labels[idx]) == 0 for code in infer_error_codes(rows[idx])})

            writer.writerow(
                {
                    "question_id": question_id,
                    "source_type": question_rows[0].get("source_type"),
                    "true_question_label": 0 if true_error_numbers else 1,
                    "pred_question_label": 0 if pred_error_numbers else 1,
                    "true_error_choice_numbers": "|".join(true_error_numbers),
                    "pred_error_choice_numbers": "|".join(pred_error_numbers),
                    "max_error_prob": round(float(max(error_probs[idx] for idx in indices)), 6),
                    "question": question_rows[0].get("question", ""),
                    "true_error_codes": "|".join(true_codes),
                    "pred_error_codes": "|".join(pred_codes),
                }
            )


MODEL_DIR = OUTPUT_DIR / "model"

# 학습된 모델과 tokenizer를 저장합니다.
model.save_pretrained(MODEL_DIR)
tokenizer.save_pretrained(MODEL_DIR)

write_json(OUTPUT_DIR / "results.json", result)
write_json(OUTPUT_DIR / "reference_error_codes.json", ERROR_CODES)
write_json(OUTPUT_DIR / "threshold_report.json", threshold_report)
write_predictions(OUTPUT_DIR / "valid_predictions.csv", valid_rows, valid_labels, valid_error_probs)
write_predictions(OUTPUT_DIR / "test_predictions.csv", test_rows, test_labels, test_error_probs)
write_threshold_report(OUTPUT_DIR / "threshold_report.csv", threshold_report)
write_question_predictions(OUTPUT_DIR / "test_question_predictions.csv", test_rows, test_labels, test_error_probs)

print("저장 완료:", OUTPUT_DIR)
print("모델:", MODEL_DIR)
"""
    ),
]


def main() -> None:
    notebook = {
        "cells": cells,
        "metadata": {
            "kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"},
            "language_info": {"name": "python", "pygments_lexer": "ipython3"},
        },
        "nbformat": 4,
        "nbformat_minor": 5,
    }
    OUT.write_text(json.dumps(notebook, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(OUT)


if __name__ == "__main__":
    main()
