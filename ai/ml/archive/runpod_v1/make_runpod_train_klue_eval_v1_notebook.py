from __future__ import annotations

import json
from pathlib import Path


OUT = Path(__file__).resolve().parent / "runpod_train_klue_eval_v1.ipynb"


def lines(text: str) -> list[str]:
    return text.strip("\n").splitlines(keepends=True)


def md(text: str) -> dict:
    return {"cell_type": "markdown", "metadata": {}, "source": lines(text)}


def code(text: str) -> dict:
    return {"cell_type": "code", "execution_count": None, "metadata": {}, "outputs": [], "source": lines(text)}


cells = [
    md(
        """
# RunPod KLUE/RoBERTa 평가 v1

RunPod Jupyter에서 바로 실행하는 노트북입니다.  
현재 목표는 **시대(era)**, **원래 주제(topic)**, **통합 주제(topic_train)** 를 같은 방식으로 학습/평가해서 비교하는 것입니다.

평가 split은 두 가지를 사용합니다.

1. `split_era_topic_train_stratified_v1`
   - 모델이 라벨을 잘 분류하는지 확인하는 평가입니다.
   - `era + topic_train` 조합을 최대한 유지해서 train/test를 나눴습니다.

2. `split_time_v1`
   - 최신 회차를 예측하는 목적의 평가입니다.
   - 47~70회차로 학습하고 71~78회차를 평가합니다.

실행 전 RunPod 파일 구조는 아래처럼 맞춰주세요.

```text
/workspace/
  common/
    eval_splits_v1/
      split_time_v1/
        train.json
        test.json
      split_era_topic_train_stratified_v1/
        train.json
        test.json
```
"""
    ),
    md("## 1. 실행 설정"),
    code(
        """
from pathlib import Path

# RunPod 기본 경로입니다. Jupyter 파일 브라우저에서 /workspace/common 폴더가 보여야 합니다.
BASE_DIR = Path('/workspace')
COMMON_DIR = BASE_DIR / 'common'
OUTPUT_ROOT = BASE_DIR / 'output' / 'klue_eval_v1'

# 사용할 평가 split을 선택합니다.
# - split_era_topic_train_stratified_v1: 모델 자체 분류 성능 평가
# - split_time_v1: 71~78회차 최신 트렌드 예측 평가
SPLIT_NAME = 'split_era_topic_train_stratified_v1'

# 예측할 라벨을 선택합니다.
# - era: 시대
# - topic: 원래 주제
# - topic_train: 통합 주제
TARGET = 'topic_train'

MODEL_NAME = 'klue/roberta-base'
MAX_LENGTH = 512
MAX_EPOCHS = 8
BATCH_SIZE = 8
LEARNING_RATE = 2e-5
PATIENCE = 2
MIN_DELTA = 0.0
N_SPLITS = 3
VALID_SIZE = 0.2
RANDOM_STATE = 42

# v3 이후 실험 흐름에 맞춰 class weight를 적용합니다.
# 비교 실험을 위해 끄고 싶으면 False로 바꾸면 됩니다.
USE_CLASS_WEIGHT = True

# True면 3-fold stratified cross validation을 먼저 실행하고,
# 이후 전체 train 데이터로 최종 모델을 다시 학습해 test를 예측합니다.
RUN_CV = True
SAVE_MODEL = True

TARGET_COLUMNS = ['era', 'topic', 'topic_train']
assert TARGET in TARGET_COLUMNS, f'TARGET은 {TARGET_COLUMNS} 중 하나여야 합니다.'

SPLIT_DIR = COMMON_DIR / 'eval_splits_v1' / SPLIT_NAME
TRAIN_JSON = SPLIT_DIR / 'train.json'
TEST_JSON = SPLIT_DIR / 'test.json'
RUN_NAME = f'{TARGET}_{SPLIT_NAME.replace(\"split_\", \"\")}'
OUTPUT_DIR = OUTPUT_ROOT / RUN_NAME

print('BASE_DIR exists =', BASE_DIR.exists())
print('COMMON_DIR exists =', COMMON_DIR.exists())
print('TRAIN_JSON =', TRAIN_JSON, TRAIN_JSON.exists())
print('TEST_JSON =', TEST_JSON, TEST_JSON.exists())
print('OUTPUT_DIR =', OUTPUT_DIR)
"""
    ),
    md("## 2. 라이브러리 설치"),
    code(
        """
!pip install -q transformers accelerate scikit-learn matplotlib koreanize-matplotlib
"""
    ),
    md("## 3. GPU 확인"),
    code(
        """
import torch

device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
print('device:', device)
if device.type == 'cuda':
    print('gpu:', torch.cuda.get_device_name(0))
    print('vram GB:', round(torch.cuda.get_device_properties(0).total_memory / 1024**3, 2))
"""
    ),
    md("## 4. 공통 함수 준비"),
    code(
        """
import csv
import gc
import json
import random
from collections import Counter
from statistics import mean
from typing import Any

import matplotlib.pyplot as plt
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
from sklearn.model_selection import StratifiedKFold, train_test_split
from torch.utils.data import DataLoader, Dataset
from transformers import AutoModelForSequenceClassification, AutoTokenizer, get_linear_schedule_with_warmup

try:
    import koreanize_matplotlib  # noqa: F401
except Exception:
    print('koreanize_matplotlib을 사용할 수 없습니다. 그래프 한글이 깨지면 설치 셀을 다시 실행하세요.')


PREDICTION_COLUMNS = [
    'ml_sequence_index',
    'round_no',
    'question_no',
    'problem_id',
    'target',
    'true_label',
    'pred_label',
    'is_correct',
    'era',
    'topic',
    'topic_train',
    'text',
]


def set_seed(seed: int) -> None:
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)


def read_rows(path: Path) -> list[dict[str, Any]]:
    with path.open('r', encoding='utf-8') as f:
        return json.load(f)


def get_text(row: dict[str, Any]) -> str:
    return str(row.get('text') or row.get('input_text') or row.get('question') or '').strip()


def get_labels(rows: list[dict[str, Any]], target: str) -> list[str]:
    labels = []
    for row in rows:
        value = row.get(target)
        if value is None or str(value).strip() == '':
            raise ValueError(f'{target} 라벨이 비어 있는 행이 있습니다: {row.get(\"problem_id\")}')
        labels.append(str(value))
    return labels


def make_label_maps(labels: list[str]) -> tuple[dict[str, int], dict[int, str]]:
    label_list = sorted(set(labels))
    label2id = {label: idx for idx, label in enumerate(label_list)}
    id2label = {idx: label for label, idx in label2id.items()}
    return label2id, id2label


def make_class_weight_tensor(labels: list[str], label2id: dict[str, int]) -> torch.Tensor:
    counts = Counter(labels)
    total = len(labels)
    class_count = len(label2id)
    weights = []
    for label, _idx in sorted(label2id.items(), key=lambda item: item[1]):
        weights.append(total / (class_count * counts[label]))
    return torch.tensor(weights, dtype=torch.float)


class HistoryDataset(Dataset):
    def __init__(self, rows: list[dict[str, Any]], labels: list[str] | None, tokenizer, label2id: dict[str, int] | None):
        self.rows = rows
        self.texts = [get_text(row) for row in rows]
        self.labels = labels
        self.tokenizer = tokenizer
        self.label2id = label2id

    def __len__(self) -> int:
        return len(self.rows)

    def __getitem__(self, idx: int) -> dict[str, torch.Tensor]:
        encoded = self.tokenizer(
            self.texts[idx],
            truncation=True,
            padding='max_length',
            max_length=MAX_LENGTH,
            return_tensors='pt',
        )
        item = {key: value.squeeze(0) for key, value in encoded.items()}
        if self.labels is not None and self.label2id is not None:
            item['labels'] = torch.tensor(self.label2id[self.labels[idx]], dtype=torch.long)
        return item


def make_loader(rows: list[dict[str, Any]], labels: list[str] | None, tokenizer, label2id: dict[str, int] | None, shuffle: bool) -> DataLoader:
    dataset = HistoryDataset(rows, labels, tokenizer, label2id)
    return DataLoader(dataset, batch_size=BATCH_SIZE, shuffle=shuffle)
"""
    ),
    md("## 5. 학습/평가 함수"),
    code(
        """
def evaluate_model(model, loader: DataLoader, loss_fn=None) -> tuple[float, list[int], list[int]]:
    model.eval()
    losses = []
    all_preds = []
    all_labels = []
    with torch.no_grad():
        for batch in loader:
            batch = {key: value.to(device) for key, value in batch.items()}
            labels = batch.pop('labels')
            outputs = model(**batch)
            logits = outputs.logits
            loss = loss_fn(logits, labels) if loss_fn is not None else torch.nn.functional.cross_entropy(logits, labels)
            losses.append(loss.item())
            preds = torch.argmax(logits, dim=-1)
            all_preds.extend(preds.cpu().tolist())
            all_labels.extend(labels.cpu().tolist())
    return mean(losses) if losses else 0.0, all_preds, all_labels


def predict_model(model, loader: DataLoader) -> list[int]:
    model.eval()
    all_preds = []
    with torch.no_grad():
        for batch in loader:
            batch = {key: value.to(device) for key, value in batch.items()}
            outputs = model(**batch)
            preds = torch.argmax(outputs.logits, dim=-1)
            all_preds.extend(preds.cpu().tolist())
    return all_preds


def train_one_run(
    train_rows: list[dict[str, Any]],
    valid_rows: list[dict[str, Any]],
    target: str,
    tokenizer,
    label2id: dict[str, int],
    id2label: dict[int, str],
    class_weight: torch.Tensor | None,
    run_label: str,
) -> tuple[dict[str, Any], Any]:
    train_labels = get_labels(train_rows, target)
    valid_labels = get_labels(valid_rows, target)
    train_loader = make_loader(train_rows, train_labels, tokenizer, label2id, shuffle=True)
    valid_loader = make_loader(valid_rows, valid_labels, tokenizer, label2id, shuffle=False)

    model = AutoModelForSequenceClassification.from_pretrained(
        MODEL_NAME,
        num_labels=len(label2id),
        id2label=id2label,
        label2id=label2id,
    ).to(device)

    optimizer = torch.optim.AdamW(model.parameters(), lr=LEARNING_RATE)
    total_steps = max(1, len(train_loader) * MAX_EPOCHS)
    scheduler = get_linear_schedule_with_warmup(
        optimizer,
        num_warmup_steps=int(total_steps * 0.1),
        num_training_steps=total_steps,
    )
    loss_fn = torch.nn.CrossEntropyLoss(weight=class_weight.to(device) if class_weight is not None else None)

    best_state = None
    best_val_loss = float('inf')
    best_epoch = 0
    bad_epochs = 0
    history = []

    for epoch in range(1, MAX_EPOCHS + 1):
        model.train()
        train_losses = []
        for batch in train_loader:
            batch = {key: value.to(device) for key, value in batch.items()}
            labels = batch.pop('labels')
            optimizer.zero_grad(set_to_none=True)
            outputs = model(**batch)
            loss = loss_fn(outputs.logits, labels)
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            optimizer.step()
            scheduler.step()
            train_losses.append(loss.item())

        train_loss = mean(train_losses) if train_losses else 0.0
        val_loss, val_preds, val_true = evaluate_model(model, valid_loader, loss_fn)
        macro_f1 = f1_score(val_true, val_preds, average='macro', zero_division=0)
        history.append(
            {
                'epoch': epoch,
                'train_loss': train_loss,
                'validation_loss': val_loss,
                'macro_f1': macro_f1,
            }
        )
        print(f'[{run_label}] epoch {epoch}/{MAX_EPOCHS} train_loss={train_loss:.4f} val_loss={val_loss:.4f} macro_f1={macro_f1:.4f}')

        if val_loss < best_val_loss - MIN_DELTA:
            best_val_loss = val_loss
            best_epoch = epoch
            best_state = {key: value.detach().cpu().clone() for key, value in model.state_dict().items()}
            bad_epochs = 0
        else:
            bad_epochs += 1
            if bad_epochs >= PATIENCE:
                print(f'[{run_label}] early stopping at epoch {epoch}')
                break

    if best_state is not None:
        model.load_state_dict(best_state)

    final_val_loss, final_preds, final_true = evaluate_model(model, valid_loader, loss_fn)
    metrics = make_metrics(final_true, final_preds, id2label)
    result = {
        'run_label': run_label,
        'best_epoch': best_epoch,
        'best_validation_loss': best_val_loss,
        'final_validation_loss': final_val_loss,
        'history': history,
        'metrics': metrics,
    }
    return result, model
"""
    ),
    md("## 6. 지표/저장 함수"),
    code(
        """
def make_metrics(true_ids: list[int], pred_ids: list[int], id2label: dict[int, str]) -> dict[str, Any]:
    labels = list(range(len(id2label)))
    target_names = [id2label[idx] for idx in labels]
    report = classification_report(
        true_ids,
        pred_ids,
        labels=labels,
        target_names=target_names,
        output_dict=True,
        zero_division=0,
    )
    return {
        'accuracy': accuracy_score(true_ids, pred_ids),
        'precision_macro': precision_score(true_ids, pred_ids, average='macro', zero_division=0),
        'recall_macro': recall_score(true_ids, pred_ids, average='macro', zero_division=0),
        'f1_macro': f1_score(true_ids, pred_ids, average='macro', zero_division=0),
        'f1_weighted': f1_score(true_ids, pred_ids, average='weighted', zero_division=0),
        'classification_report': report,
    }


def make_confusion(true_ids: list[int], pred_ids: list[int], id2label: dict[int, str]) -> dict[str, Any]:
    labels = list(range(len(id2label)))
    matrix = confusion_matrix(true_ids, pred_ids, labels=labels)
    return {'labels': [id2label[idx] for idx in labels], 'matrix': matrix.tolist()}


def save_confusion_png(confusion_payload: dict[str, Any], path: Path, title: str) -> None:
    labels = confusion_payload['labels']
    matrix = np.array(confusion_payload['matrix'])
    fig_width = max(8, len(labels) * 0.8)
    fig_height = max(6, len(labels) * 0.65)
    fig, ax = plt.subplots(figsize=(fig_width, fig_height))
    im = ax.imshow(matrix, cmap='Blues')
    ax.set_title(title)
    ax.set_xlabel('predicted')
    ax.set_ylabel('actual')
    ax.set_xticks(range(len(labels)))
    ax.set_yticks(range(len(labels)))
    ax.set_xticklabels(labels, rotation=45, ha='right')
    ax.set_yticklabels(labels)
    for i in range(matrix.shape[0]):
        for j in range(matrix.shape[1]):
            value = int(matrix[i, j])
            color = 'white' if value > matrix.max() * 0.55 else 'black'
            ax.text(j, i, str(value), ha='center', va='center', color=color, fontsize=9)
    fig.colorbar(im, ax=ax)
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.show()
    plt.close(fig)


def save_loss_png(histories: list[dict[str, Any]], path: Path, title: str) -> None:
    fig, ax = plt.subplots(figsize=(10, 6))
    if histories and 'fold' in histories[0]:
        for fold in sorted(set(row['fold'] for row in histories)):
            rows = [row for row in histories if row['fold'] == fold]
            epochs = [row['epoch'] for row in rows]
            ax.plot(epochs, [row['train_loss'] for row in rows], '--o', label=f'fold {fold} train', alpha=0.55)
            ax.plot(epochs, [row['validation_loss'] for row in rows], '-o', label=f'fold {fold} validation')
    else:
        epochs = [row['epoch'] for row in histories]
        ax.plot(epochs, [row['train_loss'] for row in histories], '-o', label='train loss')
        if histories and 'validation_loss' in histories[0]:
            ax.plot(epochs, [row['validation_loss'] for row in histories], '-o', label='validation loss')
    ax.set_title(title)
    ax.set_xlabel('epoch')
    ax.set_ylabel('loss')
    ax.grid(True, alpha=0.3)
    ax.legend()
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.show()
    plt.close(fig)


def save_predictions_csv(rows: list[dict[str, Any]], true_labels: list[str], pred_labels: list[str], target: str, path: Path) -> None:
    with path.open('w', encoding='utf-8-sig', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=PREDICTION_COLUMNS)
        writer.writeheader()
        for row, true_label, pred_label in zip(rows, true_labels, pred_labels):
            writer.writerow(
                {
                    'ml_sequence_index': row.get('ml_sequence_index', ''),
                    'round_no': row.get('round_no', ''),
                    'question_no': row.get('question_no', ''),
                    'problem_id': row.get('problem_id', ''),
                    'target': target,
                    'true_label': true_label,
                    'pred_label': pred_label,
                    'is_correct': true_label == pred_label,
                    'era': row.get('era', ''),
                    'topic': row.get('topic', ''),
                    'topic_train': row.get('topic_train', ''),
                    'text': get_text(row),
                }
            )


def write_markdown(results: dict[str, Any], path: Path) -> None:
    lines = []
    lines.append(f'# KLUE/RoBERTa 평가 결과 - {results[\"run_name\"]}\\n')
    lines.append('## 실험 설정\\n')
    lines.append(f'- split: `{results[\"split_name\"]}`')
    lines.append(f'- target: `{results[\"target\"]}`')
    lines.append(f'- model: `{results[\"model_name\"]}`')
    lines.append(f'- max_length: `{results[\"params\"][\"max_length\"]}`')
    lines.append(f'- max_epochs: `{results[\"params\"][\"max_epochs\"]}`')
    lines.append(f'- batch_size: `{results[\"params\"][\"batch_size\"]}`')
    lines.append(f'- learning_rate: `{results[\"params\"][\"learning_rate\"]}`')
    lines.append(f'- class_weight: `{results[\"params\"][\"use_class_weight\"]}`')
    lines.append(f'- cross_validation: `{results[\"params\"][\"run_cv\"]}`\\n')
    lines.append('## 최종 Test 지표\\n')
    m = results['test_metrics']
    lines.append(f'- accuracy: {m[\"accuracy\"]:.4f}')
    lines.append(f'- precision_macro: {m[\"precision_macro\"]:.4f}')
    lines.append(f'- recall_macro: {m[\"recall_macro\"]:.4f}')
    lines.append(f'- f1_macro: {m[\"f1_macro\"]:.4f}')
    lines.append(f'- f1_weighted: {m[\"f1_weighted\"]:.4f}\\n')
    if results.get('cv_summary'):
        lines.append('## Cross Validation 요약\\n')
        for key, value in results['cv_summary'].items():
            lines.append(f'- {key}: {value:.4f}')
        lines.append('')
    lines.append('## 산출 파일\\n')
    for key, value in results['artifacts'].items():
        lines.append(f'- {key}: `{value}`')
    path.write_text('\\n'.join(lines) + '\\n', encoding='utf-8')
"""
    ),
    md("## 7. 데이터 로드"),
    code(
        """
train_rows = read_rows(TRAIN_JSON)
test_rows = read_rows(TEST_JSON)
train_labels = get_labels(train_rows, TARGET)
test_labels = get_labels(test_rows, TARGET)
label2id, id2label = make_label_maps(train_labels + test_labels)

print('train rows:', len(train_rows))
print('test rows:', len(test_rows))
print('target:', TARGET)
print('labels:', list(label2id.keys()))
print('train distribution:', Counter(train_labels))
print('test distribution:', Counter(test_labels))
"""
    ),
    md("## 8. Cross Validation 실행"),
    code(
        """
set_seed(RANDOM_STATE)
tokenizer = AutoTokenizer.from_pretrained(MODEL_NAME)

class_weight = make_class_weight_tensor(train_labels, label2id) if USE_CLASS_WEIGHT else None
if class_weight is not None:
    print('class weights:')
    for label, idx in sorted(label2id.items(), key=lambda item: item[1]):
        print(f'  {label}: {class_weight[idx].item():.4f}')

cv_results = []
cv_histories = []

if RUN_CV:
    splitter = StratifiedKFold(n_splits=N_SPLITS, shuffle=True, random_state=RANDOM_STATE)
    indices = np.arange(len(train_rows))
    train_label_array = np.array(train_labels)
    for fold, (tr_idx, va_idx) in enumerate(splitter.split(indices, train_label_array), start=1):
        fold_train = [train_rows[i] for i in tr_idx]
        fold_valid = [train_rows[i] for i in va_idx]
        fold_result, fold_model = train_one_run(
            fold_train,
            fold_valid,
            TARGET,
            tokenizer,
            label2id,
            id2label,
            class_weight,
            run_label=f'fold {fold}',
        )
        cv_results.append(fold_result)
        for row in fold_result['history']:
            cv_histories.append({'fold': fold, **row})
        del fold_model
        gc.collect()
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
else:
    tr_rows, va_rows = train_test_split(
        train_rows,
        test_size=VALID_SIZE,
        random_state=RANDOM_STATE,
        stratify=train_labels,
    )
    fold_result, fold_model = train_one_run(
        tr_rows,
        va_rows,
        TARGET,
        tokenizer,
        label2id,
        id2label,
        class_weight,
        run_label='validation',
    )
    cv_results.append(fold_result)
    cv_histories = fold_result['history']
    del fold_model
    gc.collect()
    if torch.cuda.is_available():
        torch.cuda.empty_cache()

cv_summary = {}
if cv_results:
    cv_summary = {
        'accuracy_mean': mean([r['metrics']['accuracy'] for r in cv_results]),
        'f1_macro_mean': mean([r['metrics']['f1_macro'] for r in cv_results]),
        'f1_weighted_mean': mean([r['metrics']['f1_weighted'] for r in cv_results]),
        'best_epoch_mean': mean([r['best_epoch'] for r in cv_results]),
    }
    print(json.dumps(cv_summary, ensure_ascii=False, indent=2))
"""
    ),
    md("## 9. 최종 모델 학습 및 Test 예측"),
    code(
        """
# CV에서 평균 best_epoch를 참고해 최종 학습 epoch를 정합니다.
# 너무 낮게 잡히는 것을 막기 위해 최소 3 epoch는 학습합니다.
if cv_results:
    FINAL_EPOCHS = max(3, round(cv_summary['best_epoch_mean']))
else:
    FINAL_EPOCHS = 3
print('FINAL_EPOCHS:', FINAL_EPOCHS)

final_train_loader = make_loader(train_rows, train_labels, tokenizer, label2id, shuffle=True)
test_loader = make_loader(test_rows, test_labels, tokenizer, label2id, shuffle=False)
final_model = AutoModelForSequenceClassification.from_pretrained(
    MODEL_NAME,
    num_labels=len(label2id),
    id2label=id2label,
    label2id=label2id,
).to(device)

optimizer = torch.optim.AdamW(final_model.parameters(), lr=LEARNING_RATE)
total_steps = max(1, len(final_train_loader) * FINAL_EPOCHS)
scheduler = get_linear_schedule_with_warmup(
    optimizer,
    num_warmup_steps=int(total_steps * 0.1),
    num_training_steps=total_steps,
)
loss_fn = torch.nn.CrossEntropyLoss(weight=class_weight.to(device) if class_weight is not None else None)
final_history = []

for epoch in range(1, FINAL_EPOCHS + 1):
    final_model.train()
    train_losses = []
    for batch in final_train_loader:
        batch = {key: value.to(device) for key, value in batch.items()}
        labels = batch.pop('labels')
        optimizer.zero_grad(set_to_none=True)
        outputs = final_model(**batch)
        loss = loss_fn(outputs.logits, labels)
        loss.backward()
        torch.nn.utils.clip_grad_norm_(final_model.parameters(), 1.0)
        optimizer.step()
        scheduler.step()
        train_losses.append(loss.item())
    train_loss = mean(train_losses) if train_losses else 0.0
    final_history.append({'epoch': epoch, 'train_loss': train_loss})
    print(f'[final] epoch {epoch}/{FINAL_EPOCHS} train_loss={train_loss:.4f}')

test_loss, test_pred_ids, test_true_ids = evaluate_model(final_model, test_loader, loss_fn)
test_metrics = make_metrics(test_true_ids, test_pred_ids, id2label)
test_confusion = make_confusion(test_true_ids, test_pred_ids, id2label)
pred_labels = [id2label[idx] for idx in test_pred_ids]

print('test_loss:', round(test_loss, 4))
print('accuracy:', round(test_metrics['accuracy'], 4))
print('macro_f1:', round(test_metrics['f1_macro'], 4))
print('weighted_f1:', round(test_metrics['f1_weighted'], 4))
"""
    ),
    md("## 10. 결과 저장"),
    code(
        """
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

pred_csv = OUTPUT_DIR / f'{TARGET}_{SPLIT_NAME}_predictions.csv'
result_json = OUTPUT_DIR / f'{TARGET}_{SPLIT_NAME}_results.json'
result_md = OUTPUT_DIR / f'{TARGET}_{SPLIT_NAME}_results.md'
cv_loss_png = OUTPUT_DIR / f'{TARGET}_{SPLIT_NAME}_cv_loss.png'
final_loss_png = OUTPUT_DIR / f'{TARGET}_{SPLIT_NAME}_final_loss.png'
confusion_png = OUTPUT_DIR / f'{TARGET}_{SPLIT_NAME}_confusion_matrix.png'
model_dir = OUTPUT_DIR / 'saved_model'

save_predictions_csv(test_rows, test_labels, pred_labels, TARGET, pred_csv)
if cv_histories:
    save_loss_png(cv_histories, cv_loss_png, f'{TARGET} cross validation loss')
save_loss_png(final_history, final_loss_png, f'{TARGET} final train loss')
save_confusion_png(test_confusion, confusion_png, f'{TARGET} confusion matrix')

if SAVE_MODEL:
    model_dir.mkdir(parents=True, exist_ok=True)
    final_model.save_pretrained(model_dir)
    tokenizer.save_pretrained(model_dir)

results = {
    'run_name': RUN_NAME,
    'split_name': SPLIT_NAME,
    'target': TARGET,
    'model_name': MODEL_NAME,
    'params': {
        'max_length': MAX_LENGTH,
        'max_epochs': MAX_EPOCHS,
        'final_epochs': FINAL_EPOCHS,
        'batch_size': BATCH_SIZE,
        'learning_rate': LEARNING_RATE,
        'patience': PATIENCE,
        'n_splits': N_SPLITS,
        'use_class_weight': USE_CLASS_WEIGHT,
        'run_cv': RUN_CV,
    },
    'label2id': label2id,
    'train_size': len(train_rows),
    'test_size': len(test_rows),
    'train_distribution': dict(Counter(train_labels)),
    'test_distribution': dict(Counter(test_labels)),
    'cv_summary': cv_summary,
    'cv_results': cv_results,
    'test_loss': test_loss,
    'test_metrics': test_metrics,
    'test_confusion': test_confusion,
    'artifacts': {
        'predictions_csv': str(pred_csv),
        'result_json': str(result_json),
        'result_md': str(result_md),
        'cv_loss_png': str(cv_loss_png) if cv_histories else '',
        'final_loss_png': str(final_loss_png),
        'confusion_matrix_png': str(confusion_png),
        'saved_model_dir': str(model_dir) if SAVE_MODEL else '',
    },
}

result_json.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding='utf-8')
write_markdown(results, result_md)

print('saved:', OUTPUT_DIR)
print('result_json:', result_json)
print('result_md:', result_md)
print('predictions:', pred_csv)
"""
    ),
    md("## 11. 결과 요약 확인"),
    code(
        """
summary = {
    'split': SPLIT_NAME,
    'target': TARGET,
    'accuracy': test_metrics['accuracy'],
    'precision_macro': test_metrics['precision_macro'],
    'recall_macro': test_metrics['recall_macro'],
    'f1_macro': test_metrics['f1_macro'],
    'f1_weighted': test_metrics['f1_weighted'],
    'output_dir': str(OUTPUT_DIR),
}
print(json.dumps(summary, ensure_ascii=False, indent=2))
"""
    ),
]


notebook = {
    "cells": cells,
    "metadata": {
        "kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"},
        "language_info": {"name": "python", "pygments_lexer": "ipython3"},
    },
    "nbformat": 4,
    "nbformat_minor": 5,
}


OUT.write_text(json.dumps(notebook, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"notebook: {OUT}")
