from __future__ import annotations

import json
from pathlib import Path


OUT = Path(__file__).resolve().parent / "runpod_grid_search_v2_final.ipynb"


def lines(text: str) -> list[str]:
    return text.strip("\n").splitlines(keepends=True)


def md(text: str) -> dict:
    return {"cell_type": "markdown", "metadata": {}, "source": lines(text)}


def code(text: str) -> dict:
    return {"cell_type": "code", "execution_count": None, "metadata": {}, "outputs": [], "source": lines(text)}


cells = [
    md(
        """
# RunPod Grid Search v2 Final Params

`ml_han_features_v2` 기반 `topic_train` 모델의 최종 하이퍼파라미터를 찾기 위한 노트북입니다.

흐름:

1. `split_era_topic_train_stratified_v1/train.json`으로 grid-search 3-fold CV 수행
2. macro F1 기준으로 best params 선택
3. best params로 train 전체 재학습 후 stratified test 평가
4. 같은 best params로 `split_time_v1` 평가 및 최신 트렌드 TOP5 확인

주의: 전체 grid는 32조합이고, 3-fold CV라 총 96회 학습합니다.
"""
    ),
    md("## 1. 설정"),
    code(
        """
from pathlib import Path

BASE_DIR = Path('/workspace')
COMMON_DIR = BASE_DIR / 'common'
OUTPUT_ROOT = BASE_DIR / 'output' / 'klue_grid_search_v2_final'

SPLIT_ROOT = COMMON_DIR / 'eval_splits_v2'
SEARCH_SPLIT_NAME = 'split_era_topic_train_stratified_v1'
TIME_SPLIT_NAME = 'split_time_v1'

TARGET = 'topic_train'
INPUT_TEXT_FIELD = 'text'
MODEL_NAME = 'klue/roberta-base'

MAX_LENGTHS = [512]
LEARNING_RATES = [1e-5, 2e-5, 5e-6]
BATCH_SIZES = [8, 16]
PATIENCES = [3]

MAX_EPOCHS = 30
MIN_DELTA = 0.0
N_SPLITS = 3
RANDOM_STATE = 42
USE_CLASS_WEIGHT = True
SAVE_MODEL = True

TRAIN_JSON = SPLIT_ROOT / SEARCH_SPLIT_NAME / 'train.json'
TEST_JSON = SPLIT_ROOT / SEARCH_SPLIT_NAME / 'test.json'
TIME_TRAIN_JSON = SPLIT_ROOT / TIME_SPLIT_NAME / 'train.json'
TIME_TEST_JSON = SPLIT_ROOT / TIME_SPLIT_NAME / 'test.json'

grid_size = len(MAX_LENGTHS) * len(LEARNING_RATES) * len(BATCH_SIZES) * len(PATIENCES)

print('BASE_DIR exists =', BASE_DIR.exists())
print('COMMON_DIR exists =', COMMON_DIR.exists())
print('TRAIN_JSON =', TRAIN_JSON, TRAIN_JSON.exists())
print('TEST_JSON =', TEST_JSON, TEST_JSON.exists())
print('TIME_TRAIN_JSON =', TIME_TRAIN_JSON, TIME_TRAIN_JSON.exists())
print('TIME_TEST_JSON =', TIME_TEST_JSON, TIME_TEST_JSON.exists())
print('OUTPUT_ROOT =', OUTPUT_ROOT)
print('grid size =', grid_size, 'combos,', grid_size * N_SPLITS, 'fold runs')
"""
    ),
    md("## 2. 라이브러리 설치"),
    code("!pip install -q transformers accelerate scikit-learn matplotlib koreanize-matplotlib pandas"),
    md("## 3. GPU 확인"),
    code(
        """
import torch

device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
print('device:', device)
if device.type == 'cuda':
    print('gpu:', torch.cuda.get_device_name(0))
    print('vram GB:', round(torch.cuda.get_device_properties(0).total_memory / 1024**3, 2))
else:
    print('WARNING: GPU가 보이지 않습니다. RunPod GPU x1 이상인지 확인하세요.')
"""
    ),
    md("## 4. 공통 함수"),
    code(
        """
import gc
import json
import random
import time
from collections import Counter
from itertools import product
from statistics import mean
from typing import Any

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import torch
from sklearn.metrics import accuracy_score, classification_report, confusion_matrix, f1_score, precision_score, recall_score
from sklearn.model_selection import StratifiedKFold
from torch.utils.data import DataLoader, Dataset
from transformers import AutoModelForSequenceClassification, AutoTokenizer, get_linear_schedule_with_warmup

try:
    import koreanize_matplotlib  # noqa: F401
except Exception:
    print('koreanize_matplotlib unavailable; Korean labels may not render in plots.')


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
    return str(row.get(INPUT_TEXT_FIELD) or row.get('text') or row.get('input_text') or '').strip()


def get_labels(rows: list[dict[str, Any]], target: str = TARGET) -> list[str]:
    labels = []
    for row in rows:
        value = row.get(target)
        if value is None or str(value).strip() == '':
            raise ValueError(f'{target} label is empty: {row.get(\"problem_id\")}')
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
        self.texts = [get_text(row) for row in rows]
        self.labels = labels
        self.tokenizer = tokenizer
        self.label2id = label2id
        self.max_length = max_length

    def __len__(self):
        return len(self.texts)

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


def make_loader(rows, labels, tokenizer, label2id, max_length, batch_size, shuffle):
    dataset = HistoryDataset(rows, labels, tokenizer, label2id, max_length)
    return DataLoader(dataset, batch_size=batch_size, shuffle=shuffle)


def compute_metrics(true_ids, pred_ids, id2label):
    labels = list(range(len(id2label)))
    true_labels = [id2label[i] for i in true_ids]
    pred_labels = [id2label[i] for i in pred_ids]
    return {
        'accuracy': float(accuracy_score(true_ids, pred_ids)),
        'precision_macro': float(precision_score(true_ids, pred_ids, average='macro', zero_division=0)),
        'recall_macro': float(recall_score(true_ids, pred_ids, average='macro', zero_division=0)),
        'f1_macro': float(f1_score(true_ids, pred_ids, average='macro', zero_division=0)),
        'f1_weighted': float(f1_score(true_ids, pred_ids, average='weighted', zero_division=0)),
        'classification_report': classification_report(true_labels, pred_labels, labels=[id2label[i] for i in labels], output_dict=True, zero_division=0),
        'confusion_matrix': confusion_matrix(true_labels, pred_labels, labels=[id2label[i] for i in labels]).tolist(),
    }
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


def train_one_fold(train_rows, valid_rows, params, tokenizer, label2id, id2label, class_weight, fold_label):
    set_seed(RANDOM_STATE)
    train_labels = get_labels(train_rows)
    valid_labels = get_labels(valid_rows)
    train_loader = make_loader(train_rows, train_labels, tokenizer, label2id, params['max_length'], params['batch_size'], shuffle=True)
    valid_loader = make_loader(valid_rows, valid_labels, tokenizer, label2id, params['max_length'], params['batch_size'], shuffle=False)

    model = AutoModelForSequenceClassification.from_pretrained(
        MODEL_NAME,
        num_labels=len(label2id),
        id2label=id2label,
        label2id=label2id,
    ).to(device)

    optimizer = torch.optim.AdamW(model.parameters(), lr=params['learning_rate'])
    total_steps = max(1, len(train_loader) * MAX_EPOCHS)
    scheduler = get_linear_schedule_with_warmup(
        optimizer,
        num_warmup_steps=int(total_steps * 0.1),
        num_training_steps=total_steps,
    )
    loss_fn = torch.nn.CrossEntropyLoss(weight=class_weight.to(device) if class_weight is not None else None)

    best_state = None
    best_validation_loss = float('inf')
    best_epoch = 0
    wait = 0
    history = []

    for epoch in range(1, MAX_EPOCHS + 1):
        model.train()
        train_losses = []
        for batch in train_loader:
            batch = {key: value.to(device) for key, value in batch.items()}
            labels = batch.pop('labels')
            optimizer.zero_grad()
            outputs = model(**batch)
            loss = loss_fn(outputs.logits, labels)
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            optimizer.step()
            scheduler.step()
            train_losses.append(loss.item())

        validation_loss, val_preds, val_true = evaluate_model(model, valid_loader, loss_fn)
        macro_f1 = f1_score(val_true, val_preds, average='macro', zero_division=0)
        row = {
            'epoch': epoch,
            'train_loss': float(mean(train_losses)) if train_losses else 0.0,
            'validation_loss': float(validation_loss),
            'macro_f1': float(macro_f1),
        }
        history.append(row)
        print(f\"{fold_label} epoch {epoch}: train={row['train_loss']:.4f} val={validation_loss:.4f} macro_f1={macro_f1:.4f}\")

        if validation_loss < best_validation_loss - MIN_DELTA:
            best_validation_loss = validation_loss
            best_epoch = epoch
            best_state = {key: value.detach().cpu().clone() for key, value in model.state_dict().items()}
            wait = 0
        else:
            wait += 1
            if wait >= params['patience']:
                break

    if best_state is not None:
        model.load_state_dict(best_state)

    validation_loss, val_preds, val_true = evaluate_model(model, valid_loader, loss_fn)
    metrics = compute_metrics(val_true, val_preds, id2label)

    del model
    gc.collect()
    if torch.cuda.is_available():
        torch.cuda.empty_cache()

    return {
        'best_epoch': best_epoch,
        'best_validation_loss': float(best_validation_loss),
        'final_validation_loss': float(validation_loss),
        'metrics': metrics,
        'history': history,
    }
"""
    ),
    md("## 6. Grid Search 실행"),
    code(
        """
OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)

rows = read_rows(TRAIN_JSON)
labels = get_labels(rows)
label2id, id2label = make_label_maps(labels)
class_weight = make_class_weight_tensor(labels, label2id) if USE_CLASS_WEIGHT else None
tokenizer = AutoTokenizer.from_pretrained(MODEL_NAME)

indices = np.arange(len(rows))
labels_array = np.array(labels)
splitter = StratifiedKFold(n_splits=N_SPLITS, shuffle=True, random_state=RANDOM_STATE)

grid_rows = []
all_results = []
combo_no = 0

for max_length, learning_rate, batch_size, patience in product(MAX_LENGTHS, LEARNING_RATES, BATCH_SIZES, PATIENCES):
    combo_no += 1
    params = {
        'max_length': max_length,
        'learning_rate': learning_rate,
        'batch_size': batch_size,
        'patience': patience,
    }
    print('\\n' + '=' * 80)
    print(f'combo {combo_no}/{grid_size}: {params}')

    fold_results = []
    for fold, (tr_idx, va_idx) in enumerate(splitter.split(indices, labels_array), start=1):
        train_rows = [rows[i] for i in tr_idx]
        valid_rows = [rows[i] for i in va_idx]
        result = train_one_fold(
            train_rows,
            valid_rows,
            params,
            tokenizer,
            label2id,
            id2label,
            class_weight,
            fold_label=f'combo {combo_no} fold {fold}',
        )
        result['fold'] = fold
        fold_results.append(result)

    summary = {
        **params,
        'combo_no': combo_no,
        'accuracy_mean': float(mean([r['metrics']['accuracy'] for r in fold_results])),
        'f1_macro_mean': float(mean([r['metrics']['f1_macro'] for r in fold_results])),
        'f1_weighted_mean': float(mean([r['metrics']['f1_weighted'] for r in fold_results])),
        'best_epoch_mean': float(mean([r['best_epoch'] for r in fold_results])),
        'validation_loss_mean': float(mean([r['final_validation_loss'] for r in fold_results])),
    }
    grid_rows.append(summary)
    all_results.append({'params': params, 'summary': summary, 'fold_results': fold_results})

    pd.DataFrame(grid_rows).sort_values(['f1_macro_mean', 'accuracy_mean'], ascending=False).to_csv(
        OUTPUT_ROOT / 'grid_search_summary.csv',
        index=False,
        encoding='utf-8-sig',
    )
    (OUTPUT_ROOT / 'grid_search_results.json').write_text(json.dumps(all_results, ensure_ascii=False, indent=2), encoding='utf-8')

summary_df = pd.DataFrame(grid_rows).sort_values(['f1_macro_mean', 'accuracy_mean'], ascending=False)
summary_df
"""
    ),
    md("## 7. Best Params 선택"),
    code(
        """
summary_df = pd.read_csv(OUTPUT_ROOT / 'grid_search_summary.csv')
summary_df = summary_df.sort_values(['f1_macro_mean', 'accuracy_mean'], ascending=False)
display(summary_df.head(10))

best = summary_df.iloc[0].to_dict()
BEST_PARAMS = {
    'max_length': int(best['max_length']),
    'learning_rate': float(best['learning_rate']),
    'batch_size': int(best['batch_size']),
    'patience': int(best['patience']),
}
FINAL_EPOCHS = max(1, int(round(float(best['best_epoch_mean']))))

print('BEST_PARAMS =', BEST_PARAMS)
print('FINAL_EPOCHS =', FINAL_EPOCHS)
"""
    ),
    md("## 8. Best Params로 최종 학습/평가 함수"),
    code(
        """
def train_final_and_predict(train_json: Path, test_json: Path, output_dir: Path, run_label: str):
    output_dir.mkdir(parents=True, exist_ok=True)
    train_rows = read_rows(train_json)
    test_rows = read_rows(test_json)
    train_labels = get_labels(train_rows)
    test_labels = get_labels(test_rows)
    label2id, id2label = make_label_maps(train_labels)
    class_weight = make_class_weight_tensor(train_labels, label2id) if USE_CLASS_WEIGHT else None
    tokenizer = AutoTokenizer.from_pretrained(MODEL_NAME)

    train_loader = make_loader(train_rows, train_labels, tokenizer, label2id, BEST_PARAMS['max_length'], BEST_PARAMS['batch_size'], shuffle=True)
    test_loader = make_loader(test_rows, test_labels, tokenizer, label2id, BEST_PARAMS['max_length'], BEST_PARAMS['batch_size'], shuffle=False)

    model = AutoModelForSequenceClassification.from_pretrained(
        MODEL_NAME,
        num_labels=len(label2id),
        id2label=id2label,
        label2id=label2id,
    ).to(device)

    optimizer = torch.optim.AdamW(model.parameters(), lr=BEST_PARAMS['learning_rate'])
    total_steps = max(1, len(train_loader) * FINAL_EPOCHS)
    scheduler = get_linear_schedule_with_warmup(
        optimizer,
        num_warmup_steps=int(total_steps * 0.1),
        num_training_steps=total_steps,
    )
    loss_fn = torch.nn.CrossEntropyLoss(weight=class_weight.to(device) if class_weight is not None else None)

    final_history = []
    for epoch in range(1, FINAL_EPOCHS + 1):
        model.train()
        train_losses = []
        for batch in train_loader:
            batch = {key: value.to(device) for key, value in batch.items()}
            labels = batch.pop('labels')
            optimizer.zero_grad()
            outputs = model(**batch)
            loss = loss_fn(outputs.logits, labels)
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            optimizer.step()
            scheduler.step()
            train_losses.append(loss.item())
        final_history.append({'epoch': epoch, 'train_loss': float(mean(train_losses)) if train_losses else 0.0})
        print(f'{run_label} final epoch {epoch}: train_loss={final_history[-1][\"train_loss\"]:.4f}')

    test_loss, pred_ids, true_ids = evaluate_model(model, test_loader, loss_fn)
    metrics = compute_metrics(true_ids, pred_ids, id2label)

    true_labels = [id2label[i] for i in true_ids]
    pred_labels = [id2label[i] for i in pred_ids]
    pred_rows = []
    for row, true_label, pred_label in zip(test_rows, true_labels, pred_labels):
        pred_rows.append({
            'ml_sequence_index': row.get('ml_sequence_index', ''),
            'round_no': row.get('round_no', ''),
            'question_no': row.get('question_no', ''),
            'problem_id': row.get('problem_id', ''),
            'target': TARGET,
            'true_label': true_label,
            'pred_label': pred_label,
            'is_correct': true_label == pred_label,
            'era': row.get('era', ''),
            'topic': row.get('topic', ''),
            'topic_train': row.get('topic_train', ''),
            'text': get_text(row),
        })

    pred_df = pd.DataFrame(pred_rows)
    pred_df.to_csv(output_dir / f'{run_label}_predictions.csv', index=False, encoding='utf-8-sig')

    result = {
        'run_label': run_label,
        'target': TARGET,
        'input_text_field': INPUT_TEXT_FIELD,
        'train_json': str(train_json),
        'test_json': str(test_json),
        'params': {
            **BEST_PARAMS,
            'final_epochs': FINAL_EPOCHS,
            'use_class_weight': USE_CLASS_WEIGHT,
        },
        'train_size': len(train_rows),
        'test_size': len(test_rows),
        'test_loss': float(test_loss),
        'test_metrics': metrics,
        'final_history': final_history,
    }
    (output_dir / f'{run_label}_results.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')

    labels_order = sorted(set(true_labels) | set(pred_labels))
    cm = confusion_matrix(true_labels, pred_labels, labels=labels_order)
    plt.figure(figsize=(8, 6))
    plt.imshow(cm, cmap='Blues')
    plt.title(f'{run_label} confusion matrix')
    plt.xticks(range(len(labels_order)), labels_order, rotation=45, ha='right')
    plt.yticks(range(len(labels_order)), labels_order)
    plt.xlabel('predicted')
    plt.ylabel('actual')
    for i in range(len(labels_order)):
        for j in range(len(labels_order)):
            plt.text(j, i, cm[i, j], ha='center', va='center')
    plt.tight_layout()
    plt.savefig(output_dir / f'{run_label}_confusion_matrix.png', dpi=150)
    plt.show()

    if SAVE_MODEL:
        model.save_pretrained(output_dir / 'saved_model')
        tokenizer.save_pretrained(output_dir / 'saved_model')

    del model
    gc.collect()
    if torch.cuda.is_available():
        torch.cuda.empty_cache()

    return result, pred_df
"""
    ),
    md("## 9. Stratified Test 최종 평가"),
    code(
        """
stratified_output = OUTPUT_ROOT / 'final_stratified'
stratified_result, stratified_pred = train_final_and_predict(
    TRAIN_JSON,
    TEST_JSON,
    stratified_output,
    run_label='topic_train_stratified_best',
)

print(json.dumps({
    'accuracy': stratified_result['test_metrics']['accuracy'],
    'f1_macro': stratified_result['test_metrics']['f1_macro'],
    'f1_weighted': stratified_result['test_metrics']['f1_weighted'],
}, ensure_ascii=False, indent=2))
"""
    ),
    md("## 10. split_time_v1 최신 트렌드 평가"),
    code(
        """
time_output = OUTPUT_ROOT / 'final_time'
time_result, time_pred = train_final_and_predict(
    TIME_TRAIN_JSON,
    TIME_TEST_JSON,
    time_output,
    run_label='topic_train_time_best',
)

print(json.dumps({
    'accuracy': time_result['test_metrics']['accuracy'],
    'f1_macro': time_result['test_metrics']['f1_macro'],
    'f1_weighted': time_result['test_metrics']['f1_weighted'],
}, ensure_ascii=False, indent=2))

trend = time_pred['pred_label'].value_counts().rename_axis('topic_train').reset_index(name='count')
trend['ratio'] = trend['count'] / trend['count'].sum()
trend.to_csv(time_output / 'topic_train_latest_trend_top5.csv', index=False, encoding='utf-8-sig')
display(trend.head(5))

round_trend = pd.crosstab(time_pred['round_no'], time_pred['pred_label'])
round_trend.to_csv(time_output / 'topic_train_latest_trend_by_round.csv', encoding='utf-8-sig')
display(round_trend)
"""
    ),
    md("## 11. 결과 요약 Markdown 저장"),
    code(
        """
best_table = summary_df.head(10).to_markdown(index=False)
trend_table = trend.head(5).to_markdown(index=False)

md_text = f'''# Grid Search v2 Final Result

## Search Space

- max_length: {MAX_LENGTHS}
- learning_rate: {LEARNING_RATES}
- batch_size: {BATCH_SIZES}
- patience: {PATIENCES}
- total combos: {grid_size}
- folds: {N_SPLITS}

## Best Params

```python
BEST_PARAMS = {BEST_PARAMS}
FINAL_EPOCHS = {FINAL_EPOCHS}
```

## Top 10 Grid Results

{best_table}

## Stratified Test

- accuracy: {stratified_result['test_metrics']['accuracy']:.4f}
- macro F1: {stratified_result['test_metrics']['f1_macro']:.4f}
- weighted F1: {stratified_result['test_metrics']['f1_weighted']:.4f}

## split_time_v1 Test

- accuracy: {time_result['test_metrics']['accuracy']:.4f}
- macro F1: {time_result['test_metrics']['f1_macro']:.4f}
- weighted F1: {time_result['test_metrics']['f1_weighted']:.4f}

## Latest Trend TOP5

{trend_table}
'''

(OUTPUT_ROOT / 'grid_search_v2_final_report.md').write_text(md_text, encoding='utf-8')
print(OUTPUT_ROOT / 'grid_search_v2_final_report.md')
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
    OUT.write_text(json.dumps(notebook, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"notebook: {OUT}")


if __name__ == "__main__":
    main()
