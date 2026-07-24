from __future__ import annotations

import json
from pathlib import Path


OUT = Path(__file__).resolve().parent / "train_mc_runpod.ipynb"


def lines(text: str) -> list[str]:
    return text.strip("\n").splitlines(keepends=True)


def md(text: str) -> dict:
    return {"cell_type": "markdown", "metadata": {}, "source": lines(text)}


def code(text: str) -> dict:
    return {"cell_type": "code", "execution_count": None, "metadata": {}, "outputs": [], "source": lines(text)}


cells = [
    md(
        """
# 5지선다 Multiple Choice BERT 학습

이 노트북은 RunPod에서 `klue/roberta-base`를 5지선다 문제 풀이 구조로 학습한다.

기존 선지별 이진 분류와 달리, 문항 1개와 선지 5개를 한 묶음으로 넣고 정답 번호를 예측한다.

- 입력: 지문 + 질문 + 선지 5개
- 출력: ①~⑤ 중 정답 번호
- 목적: 정답 유일성 검수 보조
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
import os
import random
import re
from pathlib import Path
from statistics import mean
from typing import Any

# CUDA 오류가 비동기로 엉뚱한 줄에 찍히는 것을 줄이기 위한 디버그 설정이다.
# 원인을 찾은 뒤에는 주석 처리해도 된다.
os.environ["CUDA_LAUNCH_BLOCKING"] = "1"

import numpy as np
import torch
from sklearn.metrics import accuracy_score, classification_report, confusion_matrix
from sklearn.model_selection import GroupShuffleSplit
from torch.utils.data import DataLoader, Dataset
from transformers import AutoModelForMultipleChoice, AutoTokenizer, get_linear_schedule_with_warmup


WORKSPACE_DIR = Path.cwd()
DATA_DIR = WORKSPACE_DIR / "common"
TRAIN_JSON = DATA_DIR / "mc_train.json"
TEST_JSON = DATA_DIR / "mc_test.json"
OUTPUT_DIR = WORKSPACE_DIR / "mc_output"

MODEL_NAME = "klue/roberta-base"
MAX_LENGTH = 512
EPOCHS = 10
BATCH_SIZE = 4
LEARNING_RATE = 2e-5
WEIGHT_DECAY = 0.01
WARMUP_RATIO = 0.1
PATIENCE = 2
MIN_DELTA = 0.001
VALID_SIZE = 0.2
SEED = 42

# top1과 top2 확률 차이가 이 값보다 작으면 복수 정답 후보 의심으로 본다.
MARGIN_THRESHOLD = 0.10

# 가장 높은 정답 확률이 이 값보다 낮으면 정답 후보 없음 의심으로 본다.
NO_ANSWER_THRESHOLD = 0.35

ERROR_TYPE_KO = {
    "ANSWER_LENGTH_BIAS": "정답 길이 편향",
    "ANSWER_IN_PASSAGE": "정답 지문/질문 포함",
    "NO_ANSWER_CANDIDATE": "정답 후보 없음",
    "MULTIPLE_ANSWER_CANDIDATES": "복수 정답 후보",
    "ANSWER_KEY_MISMATCH": "표시 정답 불일치",
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
print("DATA_DIR:", DATA_DIR)
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
        raise ValueError(f"비어 있거나 리스트가 아닌 JSON입니다: {path}")
    return rows


def validate_rows(rows: list[dict[str, Any]], split_name: str) -> None:
    for idx, row in enumerate(rows, start=1):
        if not row.get("passage") and not row.get("question"):
            raise ValueError(f"{split_name}[{idx}] 지문/질문 누락")
        if not isinstance(row.get("choices"), list) or len(row["choices"]) != 5:
            raise ValueError(f"{split_name}[{idx}] choices는 5개여야 함")
        get_answer_index(row, split_name=split_name, row_idx=idx)


def get_answer_index(row: dict[str, Any], *, split_name: str = "data", row_idx: int = 0) -> int:
    # Multiple Choice 모델의 labels는 반드시 0~4여야 한다.
    # 사람이 보는 answer(1~5)가 있으면 이것을 기준으로 0~4 label을 만든다.
    # answer_index가 예전 파일에서 1~5로 잘못 들어온 경우를 막기 위해 answer를 우선한다.
    if "answer" in row:
        answer_index = int(row["answer"]) - 1
    elif "answer_index" in row:
        answer_index = int(row["answer_index"])
    else:
        raise ValueError(f"{split_name}[{row_idx}] answer_index/answer 누락")

    if answer_index not in (0, 1, 2, 3, 4):
        raise ValueError(
            f"{split_name}[{row_idx}] label 범위 오류: {answer_index}. "
            "Multiple Choice label은 0~4여야 합니다."
        )
    return answer_index


def print_label_debug(rows: list[dict[str, Any]], split_name: str) -> None:
    # CUDA로 넘기기 전에 전체 label 분포를 확인한다.
    labels = [
        get_answer_index(row, split_name=split_name, row_idx=idx)
        for idx, row in enumerate(rows, start=1)
    ]
    counts = {label: labels.count(label) for label in range(5)}
    print(f"{split_name} label min/max:", min(labels), max(labels))
    print(f"{split_name} label counts:", counts)


all_train_rows = read_json(TRAIN_JSON)
test_rows = read_json(TEST_JSON)

validate_rows(all_train_rows, "train")
validate_rows(test_rows, "test")
print_label_debug(all_train_rows, "all_train")
print_label_debug(test_rows, "test")

# train 파일 안에서 validation을 나눈다.
# 문항 단위 데이터라 id 기준 group split을 사용한다.
groups = [row["id"] for row in all_train_rows]
splitter = GroupShuffleSplit(n_splits=1, test_size=VALID_SIZE, random_state=SEED)
train_idx, valid_idx = next(splitter.split(all_train_rows, groups=groups))

train_rows = [all_train_rows[idx] for idx in train_idx]
valid_rows = [all_train_rows[idx] for idx in valid_idx]

print("train:", len(train_rows))
print("valid:", len(valid_rows))
print("test:", len(test_rows))
print_label_debug(train_rows, "train")
print_label_debug(valid_rows, "valid")
print("sample:", train_rows[0])
"""
    ),
    md("## 4. Multiple Choice Dataset"),
    code(
        """
def build_context(row: dict[str, Any]) -> str:
    return "\\n".join(
        [
            f"지문: {row.get('passage', '')}",
            f"질문: {row.get('question', '')}",
        ]
    )


class MultipleChoiceDataset(Dataset):
    def __init__(self, rows: list[dict[str, Any]], tokenizer: AutoTokenizer, max_length: int) -> None:
        self.rows = rows
        self.tokenizer = tokenizer
        self.max_length = max_length

    def __len__(self) -> int:
        return len(self.rows)

    def __getitem__(self, index: int) -> dict[str, torch.Tensor]:
        row = self.rows[index]
        context = build_context(row)
        choices = row["choices"]

        # AutoModelForMultipleChoice 입력 형태:
        # input_ids shape = [num_choices, max_length]
        first_sentences = [context] * len(choices)
        second_sentences = [f"선지: {choice}" for choice in choices]

        encoded = self.tokenizer(
            first_sentences,
            second_sentences,
            truncation=True,
            padding="max_length",
            max_length=self.max_length,
            return_tensors="pt",
        )
        # KLUE/RoBERTa는 token_type_ids를 사용하지 않는다.
        # 일부 tokenizer가 0/1 segment id를 만들면 RoBERTa의 token_type embedding 범위를 벗어나
        # CUDA index out of bounds가 발생할 수 있으므로 제거한다.
        encoded.pop("token_type_ids", None)
        item = {key: value for key, value in encoded.items()}
        item["labels"] = torch.tensor(get_answer_index(row), dtype=torch.long)
        return item


def make_loader(rows: list[dict[str, Any]], shuffle: bool) -> DataLoader:
    dataset = MultipleChoiceDataset(rows, tokenizer, MAX_LENGTH)
    return DataLoader(dataset, batch_size=BATCH_SIZE, shuffle=shuffle, num_workers=0)


tokenizer = AutoTokenizer.from_pretrained(MODEL_NAME)

train_loader = make_loader(train_rows, shuffle=True)
valid_loader = make_loader(valid_rows, shuffle=False)
test_loader = make_loader(test_rows, shuffle=False)

batch = next(iter(train_loader))
print({key: tuple(value.shape) for key, value in batch.items()})
print("batch labels:", batch["labels"].tolist())
assert int(batch["labels"].min()) >= 0
assert int(batch["labels"].max()) <= 4
"""
    ),
    md("## 5. 평가 함수"),
    code(
        """
def predict_loader(model: AutoModelForMultipleChoice, loader: DataLoader) -> tuple[list[int], list[int], list[list[float]]]:
    model.eval()
    y_true: list[int] = []
    y_pred: list[int] = []
    all_probs: list[list[float]] = []

    with torch.no_grad():
        for batch in loader:
            batch = {key: value.to(device) for key, value in batch.items()}
            labels = batch.pop("labels")
            outputs = model(**batch)
            probs = torch.softmax(outputs.logits, dim=-1)

            y_true.extend(labels.detach().cpu().tolist())
            y_pred.extend(outputs.logits.argmax(dim=-1).detach().cpu().tolist())
            all_probs.extend(probs.detach().cpu().tolist())

    return y_true, y_pred, all_probs


def compute_metrics(y_true: list[int], y_pred: list[int]) -> dict[str, Any]:
    labels = [0, 1, 2, 3, 4]
    return {
        "accuracy": round(float(accuracy_score(y_true, y_pred)), 6),
        "classification_report": classification_report(
            y_true,
            y_pred,
            labels=labels,
            target_names=["1", "2", "3", "4", "5"],
            output_dict=True,
            zero_division=0,
        ),
        "confusion_matrix": {
            "labels": ["1", "2", "3", "4", "5"],
            "matrix": confusion_matrix(y_true, y_pred, labels=labels).astype(int).tolist(),
        },
    }


def evaluate_loss(model: AutoModelForMultipleChoice, loader: DataLoader) -> float:
    model.eval()
    losses: list[float] = []

    with torch.no_grad():
        for batch in loader:
            batch = {key: value.to(device) for key, value in batch.items()}
            outputs = model(**batch)
            losses.append(float(outputs.loss.item()))

    return float(mean(losses)) if losses else 0.0
"""
    ),
    md("## 6. 모델 생성"),
    code(
        """
model = AutoModelForMultipleChoice.from_pretrained(MODEL_NAME)
model.to(device)

optimizer = torch.optim.AdamW(model.parameters(), lr=LEARNING_RATE, weight_decay=WEIGHT_DECAY)
total_steps = len(train_loader) * EPOCHS
warmup_steps = int(total_steps * WARMUP_RATIO)
scheduler = get_linear_schedule_with_warmup(
    optimizer,
    num_warmup_steps=warmup_steps,
    num_training_steps=total_steps,
)

print("model loaded:", MODEL_NAME)
print("total_steps:", total_steps)
print("warmup_steps:", warmup_steps)
"""
    ),
    md("## 7. 학습 전 안전 검사"),
    code(
        """
# CUDA device-side assert는 원인이 뒤늦게 보고될 수 있다.
# 그래서 실제 학습 전에 전체 DataLoader label과 tensor shape만 먼저 검사한다.
# 여기서는 모델을 새로 로드하지 않는다. 모델 재로드가 오래 걸리거나 캐시 lock을 만들 수 있기 때문이다.

def scan_loader_labels(loader: DataLoader, name: str) -> None:
    label_counts = {idx: 0 for idx in range(5)}
    batch_count = 0
    first_shape = None

    for batch in loader:
        labels = batch["labels"]
        if first_shape is None:
            first_shape = tuple(batch["input_ids"].shape)
            print(f"{name} first input_ids shape:", first_shape)
            print(f"{name} first labels:", labels.tolist())

        min_label = int(labels.min())
        max_label = int(labels.max())
        if min_label < 0 or max_label > 4:
            raise ValueError(f"{name} label 범위 오류: {labels.tolist()}. labels는 0~4여야 합니다.")

        for label in labels.tolist():
            label_counts[int(label)] += 1
        batch_count += 1

    print(f"{name} batch_count:", batch_count)
    print(f"{name} label_counts:", label_counts)


scan_loader_labels(train_loader, "train_loader")
scan_loader_labels(valid_loader, "valid_loader")
scan_loader_labels(test_loader, "test_loader")
print("학습 전 안전 검사 OK")
"""
    ),
    md("## 8. CUDA forward 디버그"),
    code(
        """
# 여기서 CUDA forward를 3단계로 나눠 확인한다.
# 1) labels 없이 forward
# 2) fake labels로 loss 계산
# 3) 실제 labels로 loss 계산
# 어느 단계에서 터지는지 보면 원인이 좁혀진다.

debug_batch = next(iter(train_loader))
debug_labels = debug_batch["labels"]
print("debug labels:", debug_labels.tolist())
print("debug label min/max:", int(debug_labels.min()), int(debug_labels.max()))
print("debug input_ids shape:", tuple(debug_batch["input_ids"].shape))
print("debug attention_mask shape:", tuple(debug_batch["attention_mask"].shape))

if int(debug_labels.min()) < 0 or int(debug_labels.max()) > 4:
    raise ValueError(f"label 범위 오류: {debug_labels.tolist()}. labels는 0~4여야 합니다.")

model.eval()
debug_inputs = {
    key: value.to(device)
    for key, value in debug_batch.items()
    if key != "labels"
}

with torch.no_grad():
    no_label_outputs = model(**debug_inputs)
    if torch.cuda.is_available():
        torch.cuda.synchronize()
print("CUDA forward without labels OK:", tuple(no_label_outputs.logits.shape))

fake_labels = torch.zeros_like(debug_labels).to(device)
with torch.no_grad():
    fake_outputs = model(**debug_inputs, labels=fake_labels)
    if torch.cuda.is_available():
        torch.cuda.synchronize()
print("CUDA forward with fake labels OK:", float(fake_outputs.loss.item()))

real_labels = debug_labels.to(device)
with torch.no_grad():
    real_outputs = model(**debug_inputs, labels=real_labels)
    if torch.cuda.is_available():
        torch.cuda.synchronize()
print("CUDA forward with real labels OK:", float(real_outputs.loss.item()))
"""
    ),
    md("## 9. 학습"),
    code(
        """
def train_epoch() -> float:
    model.train()
    losses: list[float] = []

    for batch in train_loader:
        labels = batch["labels"]
        if int(labels.min()) < 0 or int(labels.max()) > 4:
            raise ValueError(f"label 범위 오류: {labels.tolist()}. labels는 0~4여야 합니다.")

        batch = {key: value.to(device) for key, value in batch.items()}

        optimizer.zero_grad(set_to_none=True)
        outputs = model(**batch)
        loss = outputs.loss
        loss.backward()
        torch.nn.utils.clip_grad_norm_(model.parameters(), max_norm=1.0)
        optimizer.step()
        scheduler.step()

        losses.append(float(loss.item()))

    return float(mean(losses)) if losses else 0.0


best_valid_loss = float("inf")
best_state = None
patience_count = 0
history: list[dict[str, Any]] = []

for epoch in range(1, EPOCHS + 1):
    train_loss = train_epoch()
    valid_loss = evaluate_loss(model, valid_loader)
    valid_true, valid_pred, _ = predict_loader(model, valid_loader)
    valid_metrics = compute_metrics(valid_true, valid_pred)

    row = {
        "epoch": epoch,
        "train_loss": round(train_loss, 6),
        "valid_loss": round(valid_loss, 6),
        "valid_accuracy": valid_metrics["accuracy"],
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
    md("## 10. 테스트 평가"),
    code(
        """
valid_true, valid_pred, valid_probs = predict_loader(model, valid_loader)
test_true, test_pred, test_probs = predict_loader(model, test_loader)

result = {
    "model_name": MODEL_NAME,
    "train_count": len(train_rows),
    "valid_count": len(valid_rows),
    "test_count": len(test_rows),
    "best_valid_loss": round(float(best_valid_loss), 6),
    "history": history,
    "valid_metrics": compute_metrics(valid_true, valid_pred),
    "test_metrics": compute_metrics(test_true, test_pred),
}

print(json.dumps(result, ensure_ascii=False, indent=2))
"""
    ),
    md("## 11. 생성 문제 검수 함수"),
    code(
        """
def normalize_text(text: str) -> str:
    return re.sub(r"\\s+", "", str(text or "")).lower()


def issue_label(issue_type: str) -> str:
    return f"{issue_type} ({ERROR_TYPE_KO.get(issue_type, '한글 설명 없음')})"


def predict_question_probs(row: dict[str, Any]) -> list[float]:
    model.eval()
    dataset = MultipleChoiceDataset([row], tokenizer, MAX_LENGTH)
    batch = next(iter(DataLoader(dataset, batch_size=1, shuffle=False)))
    batch = {key: value.to(device) for key, value in batch.items()}
    batch.pop("labels", None)

    with torch.no_grad():
        outputs = model(**batch)
        probs = torch.softmax(outputs.logits, dim=-1)[0].detach().cpu().tolist()
    return [float(prob) for prob in probs]


def check_answer_length_bias(row: dict[str, Any], *, ratio_threshold: float = 1.5, diff_threshold: int = 12) -> dict[str, Any] | None:
    choices = row.get("choices") or []
    answer = row.get("answer")
    if not answer or len(choices) < 2:
        return None

    answer_idx = int(answer) - 1
    if answer_idx < 0 or answer_idx >= len(choices):
        return {
            "type": "ANSWER_FORMAT_ERROR",
            "type_ko": ERROR_TYPE_KO["ANSWER_FORMAT_ERROR"],
            "message": "정답 번호가 선택지 범위를 벗어남",
        }

    answer_len = len(str(choices[answer_idx]))
    other_lengths = [len(str(choice)) for idx, choice in enumerate(choices) if idx != answer_idx]
    avg_other_len = mean(other_lengths)
    diff = answer_len - avg_other_len

    too_long = answer_len >= avg_other_len * ratio_threshold and diff >= diff_threshold
    too_short = answer_len * ratio_threshold <= avg_other_len and abs(diff) >= diff_threshold
    if not (too_long or too_short):
        return None

    return {
        "type": "ANSWER_LENGTH_BIAS",
        "type_ko": ERROR_TYPE_KO["ANSWER_LENGTH_BIAS"],
        "message": "정답 선지가 다른 선지들에 비해 유독 길거나 짧음",
        "answer_length": answer_len,
        "other_avg_length": round(avg_other_len, 2),
        "diff": round(diff, 2),
    }


def check_answer_in_passage(row: dict[str, Any]) -> dict[str, Any] | None:
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
        "type_ko": ERROR_TYPE_KO["ANSWER_IN_PASSAGE"],
        "message": "정답 선지가 지문 또는 질문에 포함되어 있음",
        "found_in": found_in,
    }


def check_mc_uncertainty(row: dict[str, Any], probs: list[float]) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    ranked = sorted(enumerate(probs), key=lambda item: item[1], reverse=True)
    top_idx, top_prob = ranked[0]
    second_idx, second_prob = ranked[1]
    margin = top_prob - second_prob

    if top_prob < NO_ANSWER_THRESHOLD:
        issues.append(
            {
                "type": "NO_ANSWER_CANDIDATE",
                "type_ko": ERROR_TYPE_KO["NO_ANSWER_CANDIDATE"],
                "message": "가장 높은 정답 확률이 낮아 정답 후보 없음으로 의심됨",
                "top_choice": top_idx + 1,
                "top_prob": round(float(top_prob), 6),
                "threshold": NO_ANSWER_THRESHOLD,
            }
        )

    if margin < MARGIN_THRESHOLD:
        issues.append(
            {
                "type": "MULTIPLE_ANSWER_CANDIDATES",
                "type_ko": ERROR_TYPE_KO["MULTIPLE_ANSWER_CANDIDATES"],
                "message": "상위 두 선지의 확률 차이가 작아 복수 정답 후보로 의심됨",
                "top_choice": top_idx + 1,
                "second_choice": second_idx + 1,
                "top_prob": round(float(top_prob), 6),
                "second_prob": round(float(second_prob), 6),
                "margin": round(float(margin), 6),
                "threshold": MARGIN_THRESHOLD,
            }
        )

    answer = row.get("answer")
    if answer:
        given_answer_idx = int(answer) - 1
        if 0 <= given_answer_idx < 5 and top_idx != given_answer_idx:
            issues.append(
                {
                    "type": "ANSWER_KEY_MISMATCH",
                    "type_ko": ERROR_TYPE_KO["ANSWER_KEY_MISMATCH"],
                    "message": "모델의 최상위 정답 후보가 표시 정답과 다름",
                    "given_answer": given_answer_idx + 1,
                    "predicted_answer": top_idx + 1,
                }
            )

    return issues


def review_generated_question(row: dict[str, Any]) -> dict[str, Any]:
    probs = predict_question_probs(row)
    issues = [
        issue
        for issue in [
            check_answer_length_bias(row),
            check_answer_in_passage(row),
        ]
        if issue is not None
    ]
    issues.extend(check_mc_uncertainty(row, probs))

    return {
        "id": row.get("id") or row.get("question_id"),
        "label": 0 if issues else 1,
        "issue_types": [issue["type"] for issue in issues],
        "issue_labels": [issue_label(issue["type"]) for issue in issues],
        "issues": issues,
        "choice_probs": [round(float(prob), 6) for prob in probs],
        "predicted_answer": int(np.argmax(probs)) + 1,
    }


print("생성 문제 검수 함수 준비 완료")
"""
    ),
    md("## 12. 모델과 결과 저장"),
    code(
        """
def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\\n", encoding="utf-8")


def write_predictions(path: Path, rows: list[dict[str, Any]], y_pred: list[int], probs: list[list[float]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "id",
                "true_answer",
                "pred_answer",
                "is_correct",
                "prob_1",
                "prob_2",
                "prob_3",
                "prob_4",
                "prob_5",
                "question",
            ],
        )
        writer.writeheader()
        for row, pred, prob in zip(rows, y_pred, probs):
            writer.writerow(
                {
                    "id": row.get("id"),
                    "true_answer": int(row["answer_index"]) + 1,
                    "pred_answer": int(pred) + 1,
                    "is_correct": int(row["answer_index"]) == int(pred),
                    "prob_1": round(float(prob[0]), 6),
                    "prob_2": round(float(prob[1]), 6),
                    "prob_3": round(float(prob[2]), 6),
                    "prob_4": round(float(prob[3]), 6),
                    "prob_5": round(float(prob[4]), 6),
                    "question": row.get("question", ""),
                }
            )


MODEL_DIR = OUTPUT_DIR / "model"
model.save_pretrained(MODEL_DIR)
tokenizer.save_pretrained(MODEL_DIR)

write_json(OUTPUT_DIR / "results.json", result)
write_predictions(OUTPUT_DIR / "valid_predictions.csv", valid_rows, valid_pred, valid_probs)
write_predictions(OUTPUT_DIR / "test_predictions.csv", test_rows, test_pred, test_probs)

GENERATED_JSON = DATA_DIR / "generated_questions.json"
if GENERATED_JSON.exists():
    generated_rows = read_json(GENERATED_JSON)
    generated_results = [review_generated_question(row) for row in generated_rows]
    write_json(OUTPUT_DIR / "generated_review_results.json", generated_results)
    print("생성 문제 검수 결과:", OUTPUT_DIR / "generated_review_results.json")
else:
    print("생성 문제 파일이 없어 검수는 건너뜁니다:", GENERATED_JSON)

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
