from __future__ import annotations

import json
from pathlib import Path


# v15 선지 단위 이진 분류 학습 노트북 생성기입니다.
OUT = Path(__file__).resolve().parent / "train_choice_quality_runpod_v15.ipynb"


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
# 선지 단위 오류 유무 BERT 학습 v15

이 노트북은 **선지 1개 단위 이진 분류 모델**을 학습합니다.

- 입력 X: 지문, 질문, 선지 1개, 정답 여부
- y: `label`
- `label=0`: 선지 오류 있음
- `label=1`: 선지 오류 없음

v15는 문항 전체 사용불가 여부가 아니라 **선지 자체의 오류 유무**만 학습합니다.  
중복 선지/복수 정답처럼 선지 5개 비교가 필요한 오류는 BERT 학습과 분리하고, 결과 저장 단계에서 규칙 검사로 함께 확인합니다.

v15 변경점:

- 모델이 오류라고 봤지만 기존 규칙으로 설명되지 않던 `WEIRD_CHOICE`를 세부 원인으로 재분류합니다.
- 정답 선지가 지문 내용을 의미상 재진술한 의심은 `ANSWER_RESTATEMENT_SUSPECT`로 분리합니다.
- `ㄱ, ㄴ` 조합형 선지와 `(가) - (나) - (다)` 순서형 선지는 선지 단위 오류 검수 대상에서 제외합니다.
- 문장 종결이 어색한 생성 선지는 `CHOICE_FORMAT_ERROR`로 더 적극 분류합니다.
- `QUESTION_CHOICE_MISMATCH`는 오탐이 많아 최종 오류 판정에서는 제외하고 참고코드로만 저장합니다.
- 그래도 설명되지 않는 경우에만 마지막 fallback으로 `WEIRD_CHOICE`를 남깁니다.

RunPod 폴더 구조:

```text
/workspace/
├─ train_choice_quality_runpod_v15.ipynb
└─ common/
   ├─ choice_quality_train_v10.json
   └─ choice_quality_test_v10.json
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
    # 재실행 시 최대한 비슷한 결과가 나오도록 seed를 고정합니다.
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
# 처음에는 base 모델로 확인하고, 부족하면 klue/roberta-large로 확장합니다.
MODEL_NAME = "klue/roberta-base"

# 지문 + 질문 + 선지 1개 + 정답 여부를 넣습니다.
MAX_LENGTH = 512

EPOCHS = 30
BATCH_SIZE = 16
LEARNING_RATE = 2e-5
WEIGHT_DECAY = 0.01
WARMUP_RATIO = 0.1
VALID_SIZE = 0.2
PATIENCE = 3
MIN_DELTA = 0.0

# 선지 오류 모델은 오류 선지를 놓치지 않는 것이 중요하므로 recall 기준을 둡니다.
MIN_ABNORMAL_RECALL = 0.80

# 운영/비교 기준을 흔들지 않기 위해 v15에서는 threshold를 0.1로 고정합니다.
# validation에서 찾은 best_threshold는 참고용으로만 results.json에 남깁니다.
USE_FIXED_THRESHOLD = True
FIXED_THRESHOLD = 0.10

# 이 값은 "높은 확신으로 먼저 볼 후보"를 표시하기 위한 검수 우선순위 기준입니다.
HIGH_CONFIDENCE_ERROR_PROB = 0.80

WORKSPACE_DIR = Path("/workspace")
DATA_DIR = WORKSPACE_DIR / "common"
TRAIN_JSON = DATA_DIR / "choice_quality_train_v10.json"
TEST_JSON = DATA_DIR / "choice_quality_test_v10.json"
OUTPUT_DIR = WORKSPACE_DIR / "choice_quality_output_v15"
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


raw_train_rows = read_json(TRAIN_JSON)
raw_test_rows = read_json(TEST_JSON)


def is_option_combo_value(text: Any) -> bool:
    # "ㄱ, ㄴ", "ㄷ, ㄹ" 같은 보기 조합형 선지입니다.
    return bool(re.fullmatch(r"\\s*[ㄱ-ㅎ](\\s*[,·ㆍ]\\s*[ㄱ-ㅎ])+\\s*", str(text or "")))


def is_order_combo_value(text: Any) -> bool:
    # "(가) - (나) - (다)"처럼 순서 자체를 고르는 선지입니다.
    value = str(text or "").strip()
    marker = r"(?:\\([가-힣A-Za-z]\\)|[가-힣A-Za-z]|[ㄱ-ㅎ])"
    return bool(re.fullmatch(rf"{marker}\\s*[-~→>]\\s*{marker}(?:\\s*[-~→>]\\s*{marker})+", value))


def is_order_question_value(question: Any) -> bool:
    return bool(re.search(r"순서|나열|먼저|이후|이전|전개된 시기|시기를.*고른", str(question or "")))


def is_excluded_choice_type(row: dict[str, Any]) -> bool:
    # 이 유형은 선지 하나만으로 오류 여부를 판단하기 어렵기 때문에 학습/평가에서 제외합니다.
    text = str(row.get("choice", ""))
    question = str(row.get("question", ""))
    return is_option_combo_value(text) or (is_order_combo_value(text) and is_order_question_value(question))


def split_excluded_rows(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    included = []
    excluded = []
    for row in rows:
        if is_excluded_choice_type(row):
            excluded.append(row)
        else:
            included.append(row)
    return included, excluded


all_train_rows, excluded_train_rows = split_excluded_rows(raw_train_rows)
test_rows, excluded_test_rows = split_excluded_rows(raw_test_rows)

# train 파일 안에서 validation을 나눕니다.
# 같은 문항의 5개 선지가 train/valid에 섞이면 데이터 누수가 생기므로 question_id 기준으로 묶습니다.
groups = [row["question_id"] for row in all_train_rows]
splitter = GroupShuffleSplit(n_splits=1, test_size=VALID_SIZE, random_state=SEED)
train_idx, valid_idx = next(splitter.split(all_train_rows, groups=groups))
train_rows = [all_train_rows[idx] for idx in train_idx]
valid_rows = [all_train_rows[idx] for idx in valid_idx]


def count_binary(rows: list[dict[str, Any]]) -> dict[str, int]:
    # label=0은 오류 선지, label=1은 정상 선지입니다.
    return {
        "error_0": sum(1 for row in rows if int(row["label"]) == 0),
        "ok_1": sum(1 for row in rows if int(row["label"]) == 1),
    }


REFERENCE_ERROR_CODES = {
    "ANSWER_IN_PASSAGE": "정답 선지가 지문/질문에 노출됨",
    "ANSWER_RESTATEMENT_SUSPECT": "정답 선지가 지문 내용을 의미상 재진술한 의심",
    "ANSWER_LENGTH_BIAS": "정답 선지가 유독 길거나 짧음",
    "CHOICE_FORMAT_ERROR": "선지 문장/형식 오류",
    "QUESTION_CHOICE_MISMATCH": "질문 요구와 선지 내용/형식이 맞지 않음",
    "QUESTION_MARKER_MISMATCH": "지문에 없는 표식/밑줄/(가) 등을 참조함",
    "EXCLUDED_COMBO_OR_ORDER_CHOICE": "조합형/순서형 선지라 선지 단위 검수 제외",
    "ORDER_CHOICE_CONTEXT_REQUIRED": "순서형/기호형 선지라 문항 단위 확인 필요",
    "ODD_DISTRACTOR": "오답 선지가 너무 어색하거나 쉽게 제거되는 의심",
    "WEIRD_CHOICE": "문제 맥락상 너무 이상하거나 부적절한 선지",
    "DUPLICATE_OR_SIMILAR_CHOICE": "중복되거나 거의 같은 선지가 있음",
    "NO_OR_MULTI_ANSWER": "정답이 없거나 2개 이상임",
}

ERROR_CODES = sorted(set(REFERENCE_ERROR_CODES) | {code for row in (train_rows + valid_rows + test_rows) for code in row.get("error_codes", [])})

# 참고용 코드입니다.
# 이 코드만 단독으로 잡힌 경우에는 최종 오류(final_label=0)로 만들지 않습니다.
ADVISORY_ONLY_CODES = {"QUESTION_CHOICE_MISMATCH"}


def split_codes(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value if str(item)]
    return [code for code in str(value or "").split("|") if code]


def blocking_codes(codes: list[str]) -> list[str]:
    # 최종 오류 판정에 직접 반영할 코드만 남깁니다.
    return sorted(code for code in set(codes) if code not in ADVISORY_ONLY_CODES)


def advisory_codes(codes: list[str]) -> list[str]:
    # 사람이 볼 때 참고할 수 있지만, 단독으로는 오류 판정을 만들지 않는 코드입니다.
    return sorted(code for code in set(codes) if code in ADVISORY_ONLY_CODES)


print("train:", len(train_rows), count_binary(train_rows))
print("valid:", len(valid_rows), count_binary(valid_rows))
print("test:", len(test_rows), count_binary(test_rows))
print("excluded_train:", len(excluded_train_rows), count_binary(excluded_train_rows))
print("excluded_test:", len(excluded_test_rows), count_binary(excluded_test_rows))
print("reference error codes:", ERROR_CODES)
"""
    ),
    md("## 5. 토크나이저와 Dataset 생성"),
    code(
        """
tokenizer = AutoTokenizer.from_pretrained(MODEL_NAME)


def make_input_text(row: dict[str, Any]) -> str:
    # BERT에 넣을 최종 입력 문장입니다.
    # 정답 여부는 정답을 맞히기 위한 값이 아니라, 정답 선지에서만 발생하는 오류 판단을 돕는 입력 feature입니다.
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


class ChoiceQualityDataset(Dataset):
    # JSON row를 토큰화해서 PyTorch Dataset 형태로 바꿉니다.
    def __init__(self, rows: list[dict[str, Any]], tokenizer: Any, max_length: int):
        self.rows = rows
        self.tokenizer = tokenizer
        self.max_length = max_length

    def __len__(self) -> int:
        return len(self.rows)

    def __getitem__(self, idx: int) -> dict[str, torch.Tensor]:
        row = self.rows[idx]
        encoded = self.tokenizer(
            make_input_text(row),
            truncation=True,
            padding="max_length",
            max_length=self.max_length,
            return_tensors="pt",
        )
        item = {key: value.squeeze(0) for key, value in encoded.items()}

        # 이진 분류 y입니다. 0=오류 있음, 1=오류 없음.
        item["labels"] = torch.tensor(int(row["label"]), dtype=torch.long)
        return item


train_dataset = ChoiceQualityDataset(train_rows, tokenizer, MAX_LENGTH)
valid_dataset = ChoiceQualityDataset(valid_rows, tokenizer, MAX_LENGTH)
test_dataset = ChoiceQualityDataset(test_rows, tokenizer, MAX_LENGTH)

train_loader = DataLoader(train_dataset, batch_size=BATCH_SIZE, shuffle=True)
valid_loader = DataLoader(valid_dataset, batch_size=BATCH_SIZE, shuffle=False)
test_loader = DataLoader(test_dataset, batch_size=BATCH_SIZE, shuffle=False)
"""
    ),
    md("## 6. 모델과 Loss 설정"),
    code(
        """
model = AutoModelForSequenceClassification.from_pretrained(MODEL_NAME, num_labels=2)
model.to(device)

# 오류 선지가 정상 선지보다 적으므로 class weight를 적용합니다.
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
    # error 확률이 threshold 이상이면 0=오류 있음, 아니면 1=오류 없음입니다.
    return np.where(error_probs >= threshold, 0, 1)


def compute_binary_metrics(true_labels: np.ndarray, error_probs: np.ndarray, threshold: float) -> dict[str, Any]:
    # 선지 오류 모델에서는 abnormal_recall과 abnormal_f1이 중요합니다.
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
    model.train()
    total_loss = 0.0
    progress = tqdm(train_loader, desc="train", leave=False)
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

        total_loss += float(loss.item())
        progress.set_postfix(loss=round(float(loss.item()), 4))

    return total_loss / max(len(train_loader), 1)


@torch.no_grad()
def predict_loader(loader: DataLoader, desc: str) -> tuple[np.ndarray, np.ndarray, float]:
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
        best_score = score
        best_threshold = threshold
        best_metrics = metrics
        best_state = {key: value.detach().cpu().clone() for key, value in model.state_dict().items()}
        bad_epochs = 0
    else:
        bad_epochs += 1
        if bad_epochs >= PATIENCE:
            print(f"early stopping: epoch {epoch}")
            break

if best_state is not None:
    model.load_state_dict(best_state)

auto_best_threshold = best_threshold
if USE_FIXED_THRESHOLD:
    best_threshold = FIXED_THRESHOLD

print("auto best threshold:", auto_best_threshold)
print("used threshold:", best_threshold)
print("best valid metrics:", json.dumps(best_metrics, ensure_ascii=False, indent=2))
"""
    ),
    md("## 10. 최종 평가"),
    code(
        """
valid_labels, valid_error_probs, valid_loss = predict_loader(valid_loader, desc="final valid")
test_labels, test_error_probs, test_loss = predict_loader(test_loader, desc="final test")

valid_binary_metrics = compute_binary_metrics(valid_labels, valid_error_probs, best_threshold)
test_binary_metrics = compute_binary_metrics(test_labels, test_error_probs, best_threshold)


def source_binary_metrics(rows: list[dict[str, Any]], labels: np.ndarray, error_probs: np.ndarray) -> dict[str, Any]:
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


def build_threshold_report(labels: np.ndarray, error_probs: np.ndarray) -> list[dict[str, Any]]:
    report = []
    for threshold in [0.05, 0.1, 0.15, 0.2, 0.3, 0.5, 0.7]:
        report.append(compute_binary_metrics(labels, error_probs, threshold))
    return report


threshold_report = build_threshold_report(test_labels, test_error_probs)

result = {
    "model_name": MODEL_NAME,
    "task": "choice_level_binary_classification",
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
    "excluded_policy": "ㄱ, ㄴ 조합형 선지와 (가) - (나) - (다) 순서형 선지는 선지 단위 오류 검수 대상에서 제외",
    "excluded_train_count": len(excluded_train_rows),
    "excluded_test_count": len(excluded_test_rows),
    "reference_error_codes": ERROR_CODES,
    "reference_error_code_names": REFERENCE_ERROR_CODES,
    "threshold_mode": "fixed" if USE_FIXED_THRESHOLD else "auto",
    "auto_best_threshold": round(float(auto_best_threshold), 3),
    "best_threshold": round(float(best_threshold), 3),
    "history": history,
    "valid_loss": round(float(valid_loss), 6),
    "test_loss": round(float(test_loss), 6),
    "valid_binary_metrics": valid_binary_metrics,
    "test_binary_metrics": test_binary_metrics,
    "test_source_metrics": source_binary_metrics(test_rows, test_labels, test_error_probs),
    "threshold_report": threshold_report,
}

print(json.dumps(result, ensure_ascii=False, indent=2))
"""
    ),
    md("## 11. 모델과 예측 결과 저장"),
    code(
        """
def infer_error_codes(row: dict[str, Any]) -> list[str]:
    # BERT는 label=0/1만 예측하므로, 예측 결과 설명용 오류 코드는 규칙으로 보조 추정합니다.
    codes = []
    text = str(row.get("choice", ""))
    passage_question = re.sub(r"\\s+", "", str(row.get("passage", "")) + " " + str(row.get("question", "")))
    choice_norm = re.sub(r"\\s+", "", text)
    if int(row.get("is_answer", 0)) == 1 and choice_norm and choice_norm in passage_question and not is_normal_short_choice(row):
        codes.append("ANSWER_IN_PASSAGE")
    if int(row.get("is_answer", 0)) == 1 and is_answer_mostly_exposed(row):
        codes.append("ANSWER_IN_PASSAGE")
    if int(row.get("is_answer", 0)) == 1 and has_answer_length_bias(row):
        codes.append("ANSWER_LENGTH_BIAS")
    if has_choice_format_error(row):
        codes.append("CHOICE_FORMAT_ERROR")
    if has_question_choice_mismatch(row):
        codes.append("QUESTION_CHOICE_MISMATCH")
    if (
        re.search(r"\\([가-힣A-Za-z]\\)|밑줄|표식|표지", text)
        and not re.search(r"\\([가-힣A-Za-z]\\)|밑줄|표식|표지", str(row.get("passage", "")))
        and not is_normal_short_choice(row)
    ):
        codes.append("QUESTION_MARKER_MISMATCH")
    return sorted(set(codes))


APPROVED_ACRONYMS = {"FTA", "IMF", "APEC", "UN", "OECD", "WHO", "GDP", "GNP", "WTO"}


def is_option_combo_text(text: Any) -> bool:
    # 보기형 문제의 "ㄱ, ㄴ", "ㄷ, ㄹ" 같은 조합 선지는 짧아도 정상일 수 있다.
    return bool(re.fullmatch(r"\\s*[ㄱ-ㅎ](\\s*[,·ㆍ]\\s*[ㄱ-ㅎ])+\\s*", str(text or "")))


def is_marker_only_text(text: Any) -> bool:
    # 연표/지도형 문제의 "(가)", "(나)", "㉠", "㉡" 같은 위치 선택 선지는 짧아도 정상일 수 있다.
    value = str(text or "").strip()
    return bool(re.fullmatch(r"\\([가-힣A-Za-z]\\)|[㉠-㉻]", value))


def is_order_combo_text(text: Any) -> bool:
    # "(가) - (나) - (다)", "ㄱ-ㄴ-ㄷ"처럼 순서 자체를 고르는 선지입니다.
    value = str(text or "").strip()
    marker = r"(?:\\([가-힣A-Za-z]\\)|[가-힣A-Za-z]|[ㄱ-ㅎ])"
    return bool(re.fullmatch(rf"{marker}\\s*[-~→>]\\s*{marker}(?:\\s*[-~→>]\\s*{marker})+", value))


def is_order_question(question: Any) -> bool:
    # 순서형 문항은 선지 하나만 보면 품질 판단이 불안정하므로 별도 표시합니다.
    return bool(re.search(r"순서|나열|먼저|이후|이전|전개된 시기|시기를.*고른", str(question or "")))


def is_normal_short_choice(row: dict[str, Any]) -> bool:
    question = str(row.get("question", ""))
    text = str(row.get("choice", ""))
    if is_option_combo_text(text):
        return True
    if is_order_combo_text(text) and is_order_question(question):
        return True
    if is_marker_only_text(text) and re.search(r"연표|시기|지도|지역|찾은|고른|위치", question):
        return True
    if re.search(r"보기.*고른|<보기>|＜보기＞|퀴즈|들어갈 내용", question) and len(text.strip()) <= 8:
        return True
    return False


def has_disallowed_foreign_text(text: str) -> bool:
    # 한국사 기출에 자주 쓰이는 정상 약어는 형식 오류로 보지 않는다.
    alpha_tokens = re.findall(r"[A-Za-z]+", str(text or ""))
    if alpha_tokens and all(token.upper() in APPROVED_ACRONYMS for token in alpha_tokens):
        return False
    return bool(re.search(r"[A-Za-zА-Яа-я]", str(text or "")))


def compact_text(value: Any) -> str:
    return re.sub(r"\\s+", "", str(value or "")).lower()


def has_choice_format_error(row: dict[str, Any]) -> bool:
    # 생성 선지에서 자주 보이는 문장 종결/문자/메타 표현 오류를 잡습니다.
    text = str(row.get("choice", "")).strip()
    if not text:
        return True
    if has_disallowed_foreign_text(text):
        return True
    if re.search(r"[一-龥]", text) and not re.search(r"[가-힣]", text):
        return True
    if re.search(r"근거로|풀이|정답|오답|선택지|자료를 보면|해야 한다", text):
        return True
    if re.search(r"[\\[\\]{}]", text):
        return True
    unnatural_patterns = [
        r"착수이다\\.$",
        r"수록이다\\.$",
        r"배향이다\\.$",
        r"바뀜이다\\.$",
        r"지칭이다\\.$",
        r"조약임이다\\.$",
        r"경부터이다\\.$",
        r"하나이었다\\.$",
        r"칭호이었다\\.$",
        r"국가로이다\\.$",
        r"나라로이다\\.$",
        r"체에 참여하였다\\.$",
        r"사회주의로대치",
    ]
    return any(re.search(pattern, text) for pattern in unnatural_patterns)


def has_question_choice_mismatch(row: dict[str, Any]) -> bool:
    # 질문이 요구하는 응답 형식과 선지가 맞지 않는 대표 패턴을 잡습니다.
    question = str(row.get("question", ""))
    text = str(row.get("choice", "")).strip()
    if is_order_question(question) and not is_order_combo_text(text):
        if re.search(r"순서대로|나열", question):
            return True
    if re.search(r"시기에 볼 수 있는 모습|시기에 있었던 사실", question):
        definition_like = bool(re.search(r"은 |는 |이다\\.|이었다\\.|단체|제도|법전|군대|문화유산", text))
        scene_like = bool(re.search(r"공포|실시|설치|반포|전개|출범|창설|활동|볼 수|시행|파견", text))
        if definition_like and not scene_like:
            return True
    return False


def keyword_terms(value: Any) -> list[str]:
    # 지문 노출 여부를 보조 판단하기 위해 짧은 조사/일반어를 제외한 핵심어만 뽑습니다.
    stopwords = {
        "다음", "자료", "설명", "옳은", "것은", "대한", "으로", "에서", "이다", "있다",
        "하였다", "되었다", "통해", "관련", "대표적", "대상", "문화유산", "제도",
    }
    terms = re.findall(r"[가-힣0-9]{2,}", str(value or ""))
    return [term for term in terms if term not in stopwords]


def is_answer_mostly_exposed(row: dict[str, Any]) -> bool:
    # 정답 선지의 핵심어 대부분이 지문/질문에 이미 있으면 정답 노출 후보로 봅니다.
    if int(row.get("is_answer", 0)) != 1:
        return False
    if is_normal_short_choice(row):
        return False
    text = str(row.get("choice", ""))
    if len(text.strip()) > 45:
        return False
    terms = keyword_terms(text)
    if len(terms) < 2:
        return False
    passage_question = str(row.get("passage", "")) + " " + str(row.get("question", ""))
    hit_count = sum(1 for term in terms if term in passage_question)
    return hit_count / max(len(terms), 1) >= 0.75


def has_answer_length_bias(row: dict[str, Any]) -> bool:
    # 정답 선지가 다른 선지들보다 유독 짧거나 긴지 확인합니다.
    # 절대 길이가 아니라 정답을 제외한 다른 선지들의 평균/중앙값과 비교합니다.
    if int(row.get("is_answer", 0)) != 1:
        return False
    if is_normal_short_choice(row):
        return False
    stats = choice_length_stats(row)
    text_length = stats["choice_length"]
    avg_length = stats["other_avg_length"]
    median_length = stats["other_median_length"]
    if avg_length <= 0 or median_length <= 0:
        return False
    too_short = text_length <= median_length * 0.60 and avg_length - text_length >= 15
    too_long = text_length >= median_length * 1.80 and text_length - avg_length >= 12
    return bool(too_short or too_long)


def choice_length_stats(row: dict[str, Any]) -> dict[str, float]:
    # 결과 CSV에서 길이 편향 판단 근거를 확인할 수 있도록 길이 통계를 계산합니다.
    choice_no = int(row.get("choice_no") or 0)
    choice_length = len(str(row.get("choice", "")).strip())
    all_choices = ((row.get("context") or {}).get("all_choices") or [])
    other_lengths = [
        int(item.get("length") or len(str(item.get("text") or "")))
        for item in all_choices
        if int(item.get("number") or 0) != choice_no and item.get("text")
    ]
    if not other_lengths:
        return {
            "choice_length": float(choice_length),
            "other_avg_length": 0.0,
            "other_median_length": 0.0,
            "avg_length_diff": 0.0,
            "median_length_ratio": 0.0,
        }
    avg_length = sum(other_lengths) / len(other_lengths)
    median_length = sorted(other_lengths)[len(other_lengths) // 2]
    ratio = choice_length / median_length if median_length else 0.0
    return {
        "choice_length": float(choice_length),
        "other_avg_length": float(avg_length),
        "other_median_length": float(median_length),
        "avg_length_diff": float(choice_length - avg_length),
        "median_length_ratio": float(ratio),
    }


def infer_rule_error_codes(row: dict[str, Any]) -> list[str]:
    # 문항 전체를 봐야 하는 규칙형 오류를 같이 반환합니다.
    codes = set(infer_error_codes(row))
    context = row.get("context") or {}
    codes.update(context.get("question_rule_codes") or [])
    return sorted(codes)


def explain_model_only_error(row: dict[str, Any], model_codes: list[str]) -> list[str]:
    # 다른 오류 규칙으로 설명되지 않는 경우에만 마지막 fallback으로 WEIRD_CHOICE를 둡니다.
    # 즉 WEIRD_CHOICE는 확정 오류명이 아니라 사람이 재검토할 맥락 이상 후보입니다.
    if model_codes:
        return model_codes
    text = str(row.get("choice", ""))
    question = str(row.get("question", ""))
    if is_order_combo_text(text) and is_order_question(question):
        return ["ORDER_CHOICE_CONTEXT_REQUIRED"]
    if has_choice_format_error(row):
        return ["CHOICE_FORMAT_ERROR"]
    if has_question_choice_mismatch(row):
        return ["QUESTION_CHOICE_MISMATCH"]
    if int(row.get("is_answer", 0)) == 1:
        return ["ANSWER_RESTATEMENT_SUSPECT"]
    if int(row.get("is_answer", 0)) == 0:
        return ["ODD_DISTRACTOR"]
    return ["WEIRD_CHOICE"]


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\\n", encoding="utf-8")


def write_threshold_report(path: Path, report: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        fieldnames = [
            "threshold",
            "accuracy",
            "abnormal_precision",
            "abnormal_recall",
            "abnormal_f1",
            "ok_precision",
            "ok_recall",
            "ok_f1",
        ]
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        # CSV는 표 형태라서 혼동행렬 같은 중첩 값은 제외하고, 지정한 컬럼만 저장합니다.
        for row in report:
            writer.writerow({field: row.get(field) for field in fieldnames})


def write_predictions(path: Path, rows: list[dict[str, Any]], labels: np.ndarray, error_probs: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    model_pred_labels = probs_to_label(error_probs, best_threshold)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        fieldnames = [
            "id",
            "question_id",
            "source_type",
            "choice_no",
            "is_answer",
            "true_label",
            "model_pred_label",
            "rule_label",
            "final_label",
            "decision_source",
            "error_prob",
            "true_error_codes",
            "model_error_codes",
            "rule_error_codes",
            "blocking_rule_codes",
            "advisory_rule_codes",
            "final_error_codes",
            "review_priority",
            "choice_length",
            "other_avg_length",
            "other_median_length",
            "avg_length_diff",
            "median_length_ratio",
            "passage",
            "question",
            "choice",
            "input_text",
        ]
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row, true_label, model_pred_label, error_prob in zip(rows, labels, model_pred_labels, error_probs):
            length_stats = choice_length_stats(row)
            model_codes = explain_model_only_error(row, infer_error_codes(row)) if int(model_pred_label) == 0 else []
            rule_codes = infer_rule_error_codes(row)
            blocking_rule_codes = blocking_codes(rule_codes)
            advisory_rule_codes = advisory_codes(rule_codes)
            rule_label = 0 if blocking_rule_codes else 1
            final_label = 0 if int(model_pred_label) == 0 or blocking_rule_codes else 1
            final_codes = sorted(set(model_codes) | set(blocking_rule_codes)) if final_label == 0 else []
            if int(model_pred_label) == 0 and blocking_rule_codes:
                decision_source = "model+rule"
            elif int(model_pred_label) == 0:
                decision_source = "model"
            elif blocking_rule_codes:
                decision_source = "rule"
            elif advisory_rule_codes:
                decision_source = "advisory"
            else:
                decision_source = "none"
            if int(model_pred_label) == 0 and float(error_prob) >= HIGH_CONFIDENCE_ERROR_PROB:
                review_priority = "HIGH"
            elif int(model_pred_label) == 0:
                review_priority = "MEDIUM"
            elif blocking_rule_codes:
                review_priority = "LOW"
            else:
                review_priority = "LOW"
            writer.writerow(
                {
                    "id": row.get("id"),
                    "question_id": row.get("question_id"),
                    "source_type": row.get("source_type"),
                    "choice_no": row.get("choice_no"),
                    "is_answer": row.get("is_answer"),
                    "true_label": int(true_label),
                    "model_pred_label": int(model_pred_label),
                    "rule_label": int(rule_label),
                    "final_label": int(final_label),
                    "decision_source": decision_source,
                    "error_prob": round(float(error_prob), 6),
                    "true_error_codes": "|".join(row.get("error_codes", [])),
                    "model_error_codes": "|".join(model_codes),
                    "rule_error_codes": "|".join(rule_codes),
                    "blocking_rule_codes": "|".join(blocking_rule_codes),
                    "advisory_rule_codes": "|".join(advisory_rule_codes),
                    "final_error_codes": "|".join(final_codes),
                    "review_priority": review_priority,
                    "choice_length": int(length_stats["choice_length"]),
                    "other_avg_length": round(float(length_stats["other_avg_length"]), 2),
                    "other_median_length": round(float(length_stats["other_median_length"]), 2),
                    "avg_length_diff": round(float(length_stats["avg_length_diff"]), 2),
                    "median_length_ratio": round(float(length_stats["median_length_ratio"]), 3),
                    "passage": row.get("passage", ""),
                    "question": row.get("question", ""),
                    "choice": row.get("choice", ""),
                    "input_text": make_input_text(row),
                }
            )


def write_review_file(path: Path, rows: list[dict[str, Any]], error_probs: np.ndarray) -> None:
    # 팀원이 실제 검수할 때 보는 간단 버전 CSV입니다.
    # 상세 분석용 컬럼은 test_predictions.csv에 남기고, 여기서는 꼭 필요한 정보만 저장합니다.
    path.parent.mkdir(parents=True, exist_ok=True)
    model_pred_labels = probs_to_label(error_probs, best_threshold)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        fieldnames = [
            "검수상태",
            "우선순위",
            "오류확률",
            "판단근거",
            "오류코드",
            "참고코드",
            "문항ID",
            "선지번호",
            "정답여부",
            "선지길이",
            "다른선지평균길이",
            "다른선지중앙값길이",
            "평균대비길이차이",
            "중앙값대비길이비율",
            "지문",
            "질문",
            "선지",
        ]
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row, model_pred_label, error_prob in zip(rows, model_pred_labels, error_probs):
            length_stats = choice_length_stats(row)
            model_codes = explain_model_only_error(row, infer_error_codes(row)) if int(model_pred_label) == 0 else []
            rule_codes = infer_rule_error_codes(row)
            blocking_rule_codes = blocking_codes(rule_codes)
            advisory_rule_codes = advisory_codes(rule_codes)

            if int(model_pred_label) == 0:
                review_status = "검수필요"
                decision_source = "model+rule" if blocking_rule_codes else "model"
                codes = sorted(set(model_codes) | set(blocking_rule_codes))
                priority = "HIGH" if float(error_prob) >= HIGH_CONFIDENCE_ERROR_PROB else "MEDIUM"
            elif blocking_rule_codes:
                review_status = "참고검수"
                decision_source = "rule"
                codes = blocking_rule_codes
                priority = "LOW"
            else:
                review_status = "통과"
                decision_source = "advisory" if advisory_rule_codes else "none"
                codes = []
                priority = "LOW"

            writer.writerow(
                {
                    "검수상태": review_status,
                    "우선순위": priority,
                    "오류확률": round(float(error_prob), 6),
                    "판단근거": decision_source,
                    "오류코드": "|".join(codes),
                    "참고코드": "|".join(advisory_rule_codes),
                    "문항ID": row.get("question_id"),
                    "선지번호": row.get("choice_no"),
                    "정답여부": "정답" if int(row.get("is_answer", 0)) == 1 else "오답",
                    "선지길이": int(length_stats["choice_length"]),
                    "다른선지평균길이": round(float(length_stats["other_avg_length"]), 2),
                    "다른선지중앙값길이": round(float(length_stats["other_median_length"]), 2),
                    "평균대비길이차이": round(float(length_stats["avg_length_diff"]), 2),
                    "중앙값대비길이비율": round(float(length_stats["median_length_ratio"]), 3),
                    "지문": row.get("passage", ""),
                    "질문": row.get("question", ""),
                    "선지": row.get("choice", ""),
                }
            )


def write_excluded_choices_file(path: Path, rows: list[dict[str, Any]]) -> None:
    # 선지 단위 오류 검수에서 제외한 조합형/순서형 선지를 따로 저장합니다.
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        fieldnames = [
            "검수상태",
            "판단근거",
            "참고코드",
            "문항ID",
            "선지번호",
            "정답여부",
            "실제라벨",
            "기존오류코드",
            "지문",
            "질문",
            "선지",
        ]
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    "검수상태": "검수제외",
                    "판단근거": "excluded_choice_type",
                    "참고코드": "EXCLUDED_COMBO_OR_ORDER_CHOICE",
                    "문항ID": row.get("question_id"),
                    "선지번호": row.get("choice_no"),
                    "정답여부": "정답" if int(row.get("is_answer", 0)) == 1 else "오답",
                    "실제라벨": row.get("label", ""),
                    "기존오류코드": "|".join(row.get("error_codes", [])),
                    "지문": row.get("passage", ""),
                    "질문": row.get("question", ""),
                    "선지": row.get("choice", ""),
                }
            )


def build_final_metrics(rows: list[dict[str, Any]], labels: np.ndarray, error_probs: np.ndarray) -> dict[str, Any]:
    # 모델 단독 성능과 규칙까지 합친 최종 판정 성능을 따로 봅니다.
    model_pred_labels = probs_to_label(error_probs, best_threshold)
    final_labels = []
    decision_sources = {"model": 0, "rule": 0, "model+rule": 0, "advisory": 0, "none": 0}
    for row, model_pred_label in zip(rows, model_pred_labels):
        rule_codes = infer_rule_error_codes(row)
        blocking_rule_codes = blocking_codes(rule_codes)
        advisory_rule_codes = advisory_codes(rule_codes)
        final_label = 0 if int(model_pred_label) == 0 or blocking_rule_codes else 1
        final_labels.append(final_label)
        if int(model_pred_label) == 0 and blocking_rule_codes:
            decision_sources["model+rule"] += 1
        elif int(model_pred_label) == 0:
            decision_sources["model"] += 1
        elif blocking_rule_codes:
            decision_sources["rule"] += 1
        elif advisory_rule_codes:
            decision_sources["advisory"] += 1
        else:
            decision_sources["none"] += 1

    model_metrics = compute_binary_metrics(labels, error_probs, best_threshold)
    precision, recall, f1, _ = precision_recall_fscore_support(labels, final_labels, labels=[0, 1], zero_division=0)
    return {
        "model_only": model_metrics,
        "final_with_rules": {
            "accuracy": round(float(accuracy_score(labels, final_labels)), 6),
            "abnormal_precision": round(float(precision[0]), 6),
            "abnormal_recall": round(float(recall[0]), 6),
            "abnormal_f1": round(float(f1[0]), 6),
            "ok_precision": round(float(precision[1]), 6),
            "ok_recall": round(float(recall[1]), 6),
            "ok_f1": round(float(f1[1]), 6),
            "confusion_matrix_labels": ["ERROR_0", "OK_1"],
            "confusion_matrix": confusion_matrix(labels, final_labels, labels=[0, 1]).tolist(),
        },
        "decision_source_count": decision_sources,
        "known_holes": [
            "역사적 사실성 자체는 BERT/규칙만으로 안정 검증하기 어렵다.",
            "WEIRD_CHOICE는 새 보조 규칙으로도 설명되지 않는 마지막 fallback이다.",
            "ANSWER_RESTATEMENT_SUSPECT는 모델이 오류로 본 정답 선지 중 지문 재진술 의심을 표시하는 보조 코드다.",
            "ㄱ, ㄴ 조합형과 (가) - (나) - (다) 순서형은 선지 단위 오류 검수 대상에서 제외한다.",
            "QUESTION_CHOICE_MISMATCH는 오탐이 많아 최종 오류 판정에서는 제외하고 참고코드로만 저장한다.",
            "기출 정상 데이터 비중이 커서 팀원 생성 문제 문체와 분포 차이가 남아 있다.",
            "오류 유형별 라벨 수가 적은 항목은 오류 코드 설명 성능이 제한된다.",
        ],
    }


def collect_prediction_rows(rows: list[dict[str, Any]], labels: np.ndarray, error_probs: np.ndarray) -> list[dict[str, Any]]:
    # CSV 저장과 리포트 저장에서 같은 판정 로직을 쓰기 위해 예측 row를 한 번 구성합니다.
    model_pred_labels = probs_to_label(error_probs, best_threshold)
    records = []
    for row, true_label, model_pred_label, error_prob in zip(rows, labels, model_pred_labels, error_probs):
        length_stats = choice_length_stats(row)
        model_codes = explain_model_only_error(row, infer_error_codes(row)) if int(model_pred_label) == 0 else []
        rule_codes = infer_rule_error_codes(row)
        blocking_rule_codes = blocking_codes(rule_codes)
        advisory_rule_codes = advisory_codes(rule_codes)
        rule_label = 0 if blocking_rule_codes else 1
        final_label = 0 if int(model_pred_label) == 0 or blocking_rule_codes else 1
        final_codes = sorted(set(model_codes) | set(blocking_rule_codes)) if final_label == 0 else []
        if int(model_pred_label) == 0 and blocking_rule_codes:
            decision_source = "model+rule"
        elif int(model_pred_label) == 0:
            decision_source = "model"
        elif blocking_rule_codes:
            decision_source = "rule"
        elif advisory_rule_codes:
            decision_source = "advisory"
        else:
            decision_source = "none"
        records.append(
            {
                "question_id": row.get("question_id"),
                "source_type": row.get("source_type"),
                "choice_no": row.get("choice_no"),
                "is_answer": row.get("is_answer"),
                "true_label": int(true_label),
                "model_pred_label": int(model_pred_label),
                "rule_label": int(rule_label),
                "final_label": int(final_label),
                "decision_source": decision_source,
                "error_prob": round(float(error_prob), 6),
                "true_error_codes": "|".join(row.get("error_codes", [])),
                "model_error_codes": "|".join(model_codes),
                "rule_error_codes": "|".join(rule_codes),
                "blocking_rule_codes": "|".join(blocking_rule_codes),
                "advisory_rule_codes": "|".join(advisory_rule_codes),
                "final_error_codes": "|".join(final_codes),
                "choice_length": int(length_stats["choice_length"]),
                "other_avg_length": round(float(length_stats["other_avg_length"]), 2),
                "avg_length_diff": round(float(length_stats["avg_length_diff"]), 2),
                "question": row.get("question", ""),
                "choice": row.get("choice", ""),
                "passage": row.get("passage", ""),
            }
        )
    return records


def write_error_code_summary(path: Path, rows: list[dict[str, Any]], labels: np.ndarray, error_probs: np.ndarray) -> None:
    # 최종 오류 코드가 얼마나 나왔는지 한눈에 보는 요약 파일입니다.
    records = collect_prediction_rows(rows, labels, error_probs)
    counter: dict[str, dict[str, int]] = {}
    for record in records:
        codes = split_codes(record["final_error_codes"])
        if not codes:
            codes = ["PASS"]
        for code_name in codes:
            bucket = counter.setdefault(code_name, {"total": 0, "generated_audit": 0, "past_exam": 0, "answer": 0, "distractor": 0})
            bucket["total"] += 1
            bucket[str(record["source_type"])] = bucket.get(str(record["source_type"]), 0) + 1
            if int(record["is_answer"]) == 1:
                bucket["answer"] += 1
            else:
                bucket["distractor"] += 1
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        fieldnames = ["error_code", "name_ko", "total", "generated_audit", "past_exam", "answer", "distractor"]
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for code_name, counts in sorted(counter.items(), key=lambda item: item[1]["total"], reverse=True):
            writer.writerow(
                {
                    "error_code": code_name,
                    "name_ko": REFERENCE_ERROR_CODES.get(code_name, "통과" if code_name == "PASS" else code_name),
                    "total": counts.get("total", 0),
                    "generated_audit": counts.get("generated_audit", 0),
                    "past_exam": counts.get("past_exam", 0),
                    "answer": counts.get("answer", 0),
                    "distractor": counts.get("distractor", 0),
                }
            )


def write_remaining_weird_file(path: Path, rows: list[dict[str, Any]], labels: np.ndarray, error_probs: np.ndarray) -> None:
    # v15 보강 후에도 WEIRD_CHOICE로 남은 케이스만 저장합니다.
    records = [
        record
        for record in collect_prediction_rows(rows, labels, error_probs)
        if "WEIRD_CHOICE" in split_codes(record["final_error_codes"])
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        fieldnames = [
            "source_type",
            "question_id",
            "choice_no",
            "is_answer",
            "true_label",
            "error_prob",
            "true_error_codes",
            "model_error_codes",
            "rule_error_codes",
            "final_error_codes",
            "question",
            "choice",
            "passage",
        ]
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for record in records:
            writer.writerow({field: record.get(field) for field in fieldnames})


def write_question_rule_report(path: Path, rows: list[dict[str, Any]]) -> None:
    # 선지 5개를 함께 봐야 하는 규칙형 오류를 문항 단위로 따로 저장합니다.
    seen = set()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        fieldnames = ["question_id", "source_type", "answer_count", "answer_numbers", "question_rule_codes", "question"]
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            question_id = row.get("question_id")
            if question_id in seen:
                continue
            seen.add(question_id)
            context = row.get("context") or {}
            codes = context.get("question_rule_codes") or []
            if not codes:
                continue
            writer.writerow(
                {
                    "question_id": question_id,
                    "source_type": row.get("source_type"),
                    "answer_count": context.get("answer_count"),
                    "answer_numbers": "|".join(map(str, context.get("answer_numbers") or [])),
                    "question_rule_codes": "|".join(codes),
                    "question": row.get("question", ""),
                }
            )


MODEL_DIR = OUTPUT_DIR / "model"
model.save_pretrained(MODEL_DIR)
tokenizer.save_pretrained(MODEL_DIR)

result["valid_final_metrics"] = build_final_metrics(valid_rows, valid_labels, valid_error_probs)
result["test_final_metrics"] = build_final_metrics(test_rows, test_labels, test_error_probs)

write_json(OUTPUT_DIR / "results.json", result)
write_json(OUTPUT_DIR / "reference_error_codes.json", REFERENCE_ERROR_CODES)
write_json(OUTPUT_DIR / "threshold_report.json", threshold_report)
write_predictions(OUTPUT_DIR / "valid_predictions.csv", valid_rows, valid_labels, valid_error_probs)
write_predictions(OUTPUT_DIR / "test_predictions.csv", test_rows, test_labels, test_error_probs)
write_review_file(OUTPUT_DIR / "valid_review.csv", valid_rows, valid_error_probs)
write_review_file(OUTPUT_DIR / "test_review.csv", test_rows, test_error_probs)
write_excluded_choices_file(OUTPUT_DIR / "train_valid_excluded_choices.csv", excluded_train_rows)
write_excluded_choices_file(OUTPUT_DIR / "test_excluded_choices.csv", excluded_test_rows)
write_threshold_report(OUTPUT_DIR / "threshold_report.csv", threshold_report)
write_error_code_summary(OUTPUT_DIR / "test_error_code_summary.csv", test_rows, test_labels, test_error_probs)
write_remaining_weird_file(OUTPUT_DIR / "test_remaining_weird_choices.csv", test_rows, test_labels, test_error_probs)
write_question_rule_report(OUTPUT_DIR / "test_question_rule_report.csv", test_rows)

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
