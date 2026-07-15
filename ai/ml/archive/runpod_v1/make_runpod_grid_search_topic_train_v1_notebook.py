from __future__ import annotations

import json
from pathlib import Path


OUT = Path(__file__).resolve().parent / "runpod_grid_search_topic_train_v1.ipynb"


def lines(text: str) -> list[str]:
    return text.strip("\n").splitlines(keepends=True)


def md(text: str) -> dict:
    return {"cell_type": "markdown", "metadata": {}, "source": lines(text)}


def code(text: str) -> dict:
    return {"cell_type": "code", "execution_count": None, "metadata": {}, "outputs": [], "source": lines(text)}


cells = [
    md(
        """
# RunPod Grid Search - topic_train v1

통합 주제(`topic_train`) 성능 개선을 위한 작은 grid search 노트북입니다.

이번 노트북은 최종 모델 저장보다 **좋은 하이퍼파라미터 후보를 찾는 것**이 목적입니다.

기본 탐색 범위:

| parameter | candidates |
|---|---|
| learning_rate | `1e-5`, `2e-5`, `3e-5` |
| max_length | `384`, `512` |

총 6개 조합을 `3-fold Stratified CV`로 평가합니다.  
우선순위 지표는 `macro_f1`입니다.

RunPod 파일 구조는 아래처럼 맞춰주세요.

```text
/workspace/
  common/
    eval_splits_v1/
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

BASE_DIR = Path('/workspace')
COMMON_DIR = BASE_DIR / 'common'
OUTPUT_DIR = BASE_DIR / 'output' / 'klue_grid_search_v1' / 'topic_train'

SPLIT_NAME = 'split_era_topic_train_stratified_v1'
SPLIT_DIR = COMMON_DIR / 'eval_splits_v1' / SPLIT_NAME
TRAIN_JSON = SPLIT_DIR / 'train.json'

MODEL_NAME = 'klue/roberta-base'
TARGET = 'topic_train'

LEARNING_RATES = [1e-5, 2e-5, 3e-5]
MAX_LENGTHS = [384, 512]

BATCH_SIZE = 8
MAX_EPOCHS = 8
PATIENCE = 2
MIN_DELTA = 0.0
N_SPLITS = 3
RANDOM_STATE = 42
USE_CLASS_WEIGHT = True

print('BASE_DIR exists =', BASE_DIR.exists())
print('COMMON_DIR exists =', COMMON_DIR.exists())
print('TRAIN_JSON =', TRAIN_JSON, TRAIN_JSON.exists())
print('OUTPUT_DIR =', OUTPUT_DIR)
print('grid size =', len(LEARNING_RATES) * len(MAX_LENGTHS))
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
    md("## 4. 공통 함수"),
    code(
        """
import csv
import gc
import json
import random
import time
from collections import Counter
from itertools import product
from statistics import mean, stdev
from typing import Any

import matplotlib.pyplot as plt
import numpy as np
import torch
from sklearn.metrics import accuracy_score, f1_score, precision_score, recall_score
from sklearn.model_selection import StratifiedKFold
from torch.utils.data import DataLoader, Dataset
from transformers import AutoModelForSequenceClassification, AutoTokenizer, get_linear_schedule_with_warmup

try:
    import koreanize_matplotlib  # noqa: F401
except Exception:
    print('koreanize_matplotlib을 사용할 수 없습니다. 그래프 한글이 깨질 수 있습니다.')


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
            raise ValueError(f'{target} 라벨이 비어 있습니다: {row.get(\"problem_id\")}')
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
    def __init__(self, rows, labels, tokenizer, label2id, max_length):
        self.rows = rows
        self.texts = [get_text(row) for row in rows]
        self.labels = labels
        self.tokenizer = tokenizer
        self.label2id = label2id
        self.max_length = max_length

    def __len__(self):
        return len(self.rows)

    def __getitem__(self, idx):
        encoded = self.tokenizer(
            self.texts[idx],
            truncation=True,
            padding='max_length',
            max_length=self.max_length,
            return_tensors='pt',
        )
        item = {key: value.squeeze(0) for key, value in encoded.items()}
        item['labels'] = torch.tensor(self.label2id[self.labels[idx]], dtype=torch.long)
        return item


def make_loader(rows, labels, tokenizer, label2id, max_length, shuffle):
    dataset = HistoryDataset(rows, labels, tokenizer, label2id, max_length)
    return DataLoader(dataset, batch_size=BATCH_SIZE, shuffle=shuffle)
"""
    ),
    md("## 5. 학습/평가 함수"),
    code(
        """
def evaluate_model(model, loader, loss_fn):
    model.eval()
    losses = []
    all_preds = []
    all_labels = []
    with torch.no_grad():
        for batch in loader:
            batch = {key: value.to(device) for key, value in batch.items()}
            labels = batch.pop('labels')
            outputs = model(**batch)
            loss = loss_fn(outputs.logits, labels)
            losses.append(loss.item())
            preds = torch.argmax(outputs.logits, dim=-1)
            all_preds.extend(preds.cpu().tolist())
            all_labels.extend(labels.cpu().tolist())
    return mean(losses) if losses else 0.0, all_preds, all_labels


def train_fold(
    train_rows,
    valid_rows,
    tokenizer,
    label2id,
    id2label,
    class_weight,
    learning_rate,
    max_length,
    combo_id,
    fold,
):
    train_labels = get_labels(train_rows, TARGET)
    valid_labels = get_labels(valid_rows, TARGET)
    train_loader = make_loader(train_rows, train_labels, tokenizer, label2id, max_length, shuffle=True)
    valid_loader = make_loader(valid_rows, valid_labels, tokenizer, label2id, max_length, shuffle=False)

    model = AutoModelForSequenceClassification.from_pretrained(
        MODEL_NAME,
        num_labels=len(label2id),
        id2label=id2label,
        label2id=label2id,
    ).to(device)

    optimizer = torch.optim.AdamW(model.parameters(), lr=learning_rate)
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
        weighted_f1 = f1_score(val_true, val_preds, average='weighted', zero_division=0)
        history.append(
            {
                'combo_id': combo_id,
                'fold': fold,
                'epoch': epoch,
                'train_loss': train_loss,
                'validation_loss': val_loss,
                'macro_f1': macro_f1,
                'weighted_f1': weighted_f1,
            }
        )
        print(
            f'[combo {combo_id} fold {fold}] '
            f'epoch {epoch}/{MAX_EPOCHS} '
            f'train_loss={train_loss:.4f} val_loss={val_loss:.4f} macro_f1={macro_f1:.4f}'
        )

        if val_loss < best_val_loss - MIN_DELTA:
            best_val_loss = val_loss
            best_epoch = epoch
            best_state = {key: value.detach().cpu().clone() for key, value in model.state_dict().items()}
            bad_epochs = 0
        else:
            bad_epochs += 1
            if bad_epochs >= PATIENCE:
                print(f'[combo {combo_id} fold {fold}] early stopping at epoch {epoch}')
                break

    if best_state is not None:
        model.load_state_dict(best_state)

    val_loss, val_preds, val_true = evaluate_model(model, valid_loader, loss_fn)
    metrics = {
        'accuracy': accuracy_score(val_true, val_preds),
        'precision_macro': precision_score(val_true, val_preds, average='macro', zero_division=0),
        'recall_macro': recall_score(val_true, val_preds, average='macro', zero_division=0),
        'f1_macro': f1_score(val_true, val_preds, average='macro', zero_division=0),
        'f1_weighted': f1_score(val_true, val_preds, average='weighted', zero_division=0),
        'validation_loss': val_loss,
        'best_epoch': best_epoch,
    }

    del model
    gc.collect()
    if torch.cuda.is_available():
        torch.cuda.empty_cache()

    return metrics, history
"""
    ),
    md("## 6. Grid Search 실행"),
    code(
        """
set_seed(RANDOM_STATE)
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

rows = read_rows(TRAIN_JSON)
labels = get_labels(rows, TARGET)
label2id, id2label = make_label_maps(labels)
class_weight = make_class_weight_tensor(labels, label2id) if USE_CLASS_WEIGHT else None
tokenizer = AutoTokenizer.from_pretrained(MODEL_NAME)

print('rows:', len(rows))
print('labels:', list(label2id.keys()))
print('distribution:', Counter(labels))
if class_weight is not None:
    print('class weights:')
    for label, idx in sorted(label2id.items(), key=lambda item: item[1]):
        print(f'  {label}: {class_weight[idx].item():.4f}')

grid = [
    {'learning_rate': lr, 'max_length': ml}
    for lr, ml in product(LEARNING_RATES, MAX_LENGTHS)
]

all_fold_results = []
all_histories = []
combo_summaries = []
splitter = StratifiedKFold(n_splits=N_SPLITS, shuffle=True, random_state=RANDOM_STATE)
indices = np.arange(len(rows))
label_array = np.array(labels)

for combo_idx, params in enumerate(grid, start=1):
    started_at = time.time()
    print('\\n' + '=' * 80)
    print(f'combo {combo_idx}/{len(grid)}:', params)
    fold_results = []

    for fold, (tr_idx, va_idx) in enumerate(splitter.split(indices, label_array), start=1):
        fold_train = [rows[i] for i in tr_idx]
        fold_valid = [rows[i] for i in va_idx]
        metrics, history = train_fold(
            fold_train,
            fold_valid,
            tokenizer,
            label2id,
            id2label,
            class_weight,
            learning_rate=params['learning_rate'],
            max_length=params['max_length'],
            combo_id=combo_idx,
            fold=fold,
        )
        fold_row = {'combo_id': combo_idx, 'fold': fold, **params, **metrics}
        fold_results.append(fold_row)
        all_fold_results.append(fold_row)
        all_histories.extend(history)

    elapsed_sec = time.time() - started_at
    summary = {
        'combo_id': combo_idx,
        **params,
        'accuracy_mean': mean([r['accuracy'] for r in fold_results]),
        'precision_macro_mean': mean([r['precision_macro'] for r in fold_results]),
        'recall_macro_mean': mean([r['recall_macro'] for r in fold_results]),
        'f1_macro_mean': mean([r['f1_macro'] for r in fold_results]),
        'f1_macro_std': stdev([r['f1_macro'] for r in fold_results]) if len(fold_results) > 1 else 0.0,
        'f1_weighted_mean': mean([r['f1_weighted'] for r in fold_results]),
        'validation_loss_mean': mean([r['validation_loss'] for r in fold_results]),
        'best_epoch_mean': mean([r['best_epoch'] for r in fold_results]),
        'elapsed_sec': elapsed_sec,
    }
    combo_summaries.append(summary)
    print('summary:', json.dumps(summary, ensure_ascii=False, indent=2))

combo_summaries = sorted(combo_summaries, key=lambda row: row['f1_macro_mean'], reverse=True)
best = combo_summaries[0]
print('\\nBEST PARAMS')
print(json.dumps(best, ensure_ascii=False, indent=2))
"""
    ),
    md("## 7. 결과 저장 및 시각화"),
    code(
        """
summary_json = OUTPUT_DIR / 'topic_train_grid_search_v1_results.json'
summary_csv = OUTPUT_DIR / 'topic_train_grid_search_v1_summary.csv'
fold_csv = OUTPUT_DIR / 'topic_train_grid_search_v1_folds.csv'
history_csv = OUTPUT_DIR / 'topic_train_grid_search_v1_history.csv'
result_md = OUTPUT_DIR / 'topic_train_grid_search_v1_results.md'
plot_png = OUTPUT_DIR / 'topic_train_grid_search_v1_macro_f1.png'

payload = {
    'target': TARGET,
    'split_name': SPLIT_NAME,
    'model_name': MODEL_NAME,
    'params': {
        'learning_rates': LEARNING_RATES,
        'max_lengths': MAX_LENGTHS,
        'batch_size': BATCH_SIZE,
        'max_epochs': MAX_EPOCHS,
        'patience': PATIENCE,
        'n_splits': N_SPLITS,
        'use_class_weight': USE_CLASS_WEIGHT,
    },
    'label2id': label2id,
    'train_size': len(rows),
    'train_distribution': dict(Counter(labels)),
    'best': best,
    'summaries': combo_summaries,
    'fold_results': all_fold_results,
}
summary_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')

def write_csv(path, rows):
    if not rows:
        return
    with path.open('w', encoding='utf-8-sig', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

write_csv(summary_csv, combo_summaries)
write_csv(fold_csv, all_fold_results)
write_csv(history_csv, all_histories)

md_lines = []
md_lines.append('# topic_train Grid Search v1\\n')
md_lines.append('## 목적\\n')
md_lines.append('- 통합 주제(topic_train)의 하이퍼파라미터 후보를 찾기 위한 작은 grid search입니다.\\n')
md_lines.append('## Best 조합\\n')
md_lines.append(f'- learning_rate: `{best[\"learning_rate\"]}`')
md_lines.append(f'- max_length: `{best[\"max_length\"]}`')
md_lines.append(f'- macro_f1_mean: `{best[\"f1_macro_mean\"]:.4f}`')
md_lines.append(f'- weighted_f1_mean: `{best[\"f1_weighted_mean\"]:.4f}`')
md_lines.append(f'- validation_loss_mean: `{best[\"validation_loss_mean\"]:.4f}`')
md_lines.append(f'- best_epoch_mean: `{best[\"best_epoch_mean\"]:.2f}`\\n')
md_lines.append('## 전체 결과\\n')
md_lines.append('| rank | learning_rate | max_length | macro_f1 | weighted_f1 | val_loss | best_epoch |')
md_lines.append('|---:|---:|---:|---:|---:|---:|---:|')
for rank, row in enumerate(combo_summaries, start=1):
    md_lines.append(
        f'| {rank} | {row[\"learning_rate\"]} | {row[\"max_length\"]} | '
        f'{row[\"f1_macro_mean\"]:.4f} | {row[\"f1_weighted_mean\"]:.4f} | '
        f'{row[\"validation_loss_mean\"]:.4f} | {row[\"best_epoch_mean\"]:.2f} |'
    )
md_lines.append('\\n## 산출 파일\\n')
md_lines.append(f'- summary_json: `{summary_json}`')
md_lines.append(f'- summary_csv: `{summary_csv}`')
md_lines.append(f'- fold_csv: `{fold_csv}`')
md_lines.append(f'- history_csv: `{history_csv}`')
md_lines.append(f'- plot_png: `{plot_png}`')
result_md.write_text('\\n'.join(md_lines) + '\\n', encoding='utf-8')

labels_for_plot = [f\"lr={row['learning_rate']}\\nlen={row['max_length']}\" for row in combo_summaries]
scores = [row['f1_macro_mean'] for row in combo_summaries]
plt.figure(figsize=(10, 5))
bars = plt.bar(labels_for_plot, scores)
plt.title('topic_train grid search macro F1')
plt.ylabel('macro F1')
plt.ylim(0, max(scores) + 0.08)
for bar, score in zip(bars, scores):
    plt.text(bar.get_x() + bar.get_width() / 2, bar.get_height(), f'{score:.4f}', ha='center', va='bottom')
plt.tight_layout()
plt.savefig(plot_png, dpi=150)
plt.show()

print('saved:', OUTPUT_DIR)
print('best:', json.dumps(best, ensure_ascii=False, indent=2))
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
