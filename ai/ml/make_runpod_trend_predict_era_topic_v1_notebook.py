from __future__ import annotations

import json
from pathlib import Path


OUT = Path(__file__).resolve().parent / "runpod_trend_predict_era_topic_v1.ipynb"


def lines(text: str) -> list[str]:
    return text.strip("\n").splitlines(keepends=True)


def md(text: str) -> dict:
    return {"cell_type": "markdown", "metadata": {}, "source": lines(text)}


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
# RunPod Trend Prediction v1

Purpose:

1. Train separate models for `era`, `topic_train`, and `topic`.
2. Predict each latest round with cumulative training data.
3. Build trend summaries from `pred_era + pred_topic_train`.
4. Keep `topic` as an auxiliary detail label.

Main evaluation flow:

```text
47~70 -> 71
47~71 -> 72
47~72 -> 73
...
47~77 -> 78
```

Optional rolling mode is also supported by changing `TRAIN_MODE`.
"""
    ),
    md("## 1. Config"),
    code(
        """
from pathlib import Path

BASE_DIR = Path('/workspace')
COMMON_DIR = BASE_DIR / 'common'
OUTPUT_ROOT = BASE_DIR / 'output' / 'trend_predict_era_topic_v1'

FEATURE_CSV_CANDIDATES = [
    COMMON_DIR / 'split_v2' / 'full_features_v2.csv',
    COMMON_DIR / 'full_features_v2.csv',
    COMMON_DIR / 'ml_han_features_v2.csv',
]

MODEL_NAME = 'klue/roberta-base'
INPUT_TEXT_FIELD = 'text'
TARGETS = ['era', 'topic_train', 'topic']

BASE_MIN_ROUND = 47
BASE_MAX_ROUND = 70
PREDICT_ROUNDS = list(range(71, 79))

# cumulative: 47~previous round -> target round
# rolling: recent ROLLING_WINDOW rounds -> target round
TRAIN_MODE = 'cumulative'
ROLLING_WINDOW = 5

RANDOM_STATE = 42
SAVE_MODEL = False

# Grid-search best params by target.
# Folder names were used as the source of truth because inner output file names
# may still say topic_train after manual RunPod folder renaming.
TARGET_CONFIG = {
    'era': {
        'max_length': 512,
        'learning_rate': 5e-6,
        'batch_size': 16,
        'max_epochs': 17,
        'use_class_weight': True,
    },
    'topic_train': {
        'max_length': 512,
        'learning_rate': 1e-5,
        'batch_size': 16,
        'max_epochs': 5,
        'use_class_weight': True,
    },
    'topic': {
        'max_length': 512,
        'learning_rate': 1e-5,
        'batch_size': 8,
        'max_epochs': 6,
        'use_class_weight': True,
    },
}

print('OUTPUT_ROOT =', OUTPUT_ROOT)
print('TRAIN_MODE =', TRAIN_MODE)
print('PREDICT_ROUNDS =', PREDICT_ROUNDS)
print('TARGET_CONFIG =')
for target, config in TARGET_CONFIG.items():
    print(target, config)
"""
    ),
    md("## 2. Install Libraries"),
    code("!pip install -q transformers accelerate scikit-learn matplotlib koreanize-matplotlib pandas tabulate"),
    md("## 3. GPU Check"),
    code(
        """
import torch

device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
print('device:', device)
if device.type == 'cuda':
    print('gpu:', torch.cuda.get_device_name(0))
    print('vram GB:', round(torch.cuda.get_device_properties(0).total_memory / 1024**3, 2))
else:
    print('WARNING: GPU is not available.')
"""
    ),
    md("## 4. Load Data"),
    code(
        """
import gc
import json
import random
from collections import Counter
from statistics import mean

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import torch
from sklearn.metrics import accuracy_score, classification_report, confusion_matrix, f1_score, precision_score, recall_score
from torch.utils.data import DataLoader, Dataset
from transformers import AutoModelForSequenceClassification, AutoTokenizer, get_linear_schedule_with_warmup

try:
    import koreanize_matplotlib  # noqa: F401
except Exception:
    print('koreanize_matplotlib unavailable; Korean labels may not render in plots.')


def find_feature_csv() -> Path:
    for path in FEATURE_CSV_CANDIDATES:
        if path.exists():
            return path
    raise FileNotFoundError('feature csv not found: ' + ', '.join(str(p) for p in FEATURE_CSV_CANDIDATES))


FEATURE_CSV = find_feature_csv()
df = pd.read_csv(FEATURE_CSV)
df['round_no'] = pd.to_numeric(df['round_no'], errors='raise').astype(int)
df['question_no'] = pd.to_numeric(df['question_no'], errors='coerce').astype('Int64')

required_columns = {'round_no', 'question_no', 'problem_id', INPUT_TEXT_FIELD, *TARGETS}
missing_columns = sorted(required_columns - set(df.columns))
if missing_columns:
    raise ValueError(f'missing columns: {missing_columns}')

for column in TARGETS + [INPUT_TEXT_FIELD]:
    df[column] = df[column].fillna('').astype(str)

df = df[(df['round_no'] >= BASE_MIN_ROUND) & (df['round_no'] <= max(PREDICT_ROUNDS))].copy()

print('FEATURE_CSV =', FEATURE_CSV)
print('rows =', len(df))
print('round range =', int(df['round_no'].min()), int(df['round_no'].max()))
display(df.groupby('round_no').size().rename('count').reset_index())
display(df[TARGETS].nunique().rename('nunique').reset_index().rename(columns={'index': 'target'}))
"""
    ),
    md("## 5. Helpers"),
    code(
        """
def set_seed(seed: int) -> None:
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)


def get_train_test_df(target_round: int) -> tuple[pd.DataFrame, pd.DataFrame]:
    if TRAIN_MODE == 'cumulative':
        train_df = df[(df['round_no'] >= BASE_MIN_ROUND) & (df['round_no'] < target_round)].copy()
    elif TRAIN_MODE == 'rolling':
        start_round = target_round - ROLLING_WINDOW
        train_df = df[(df['round_no'] >= start_round) & (df['round_no'] < target_round)].copy()
    else:
        raise ValueError(f'unknown TRAIN_MODE: {TRAIN_MODE}')

    test_df = df[df['round_no'] == target_round].copy()
    if train_df.empty or test_df.empty:
        raise ValueError(f'empty train/test for round {target_round}: train={len(train_df)}, test={len(test_df)}')
    return train_df, test_df


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
        weights.append(total / (class_count * counts[label]) if counts[label] else 0.0)
    return torch.tensor(weights, dtype=torch.float)


class HistoryDataset(Dataset):
    def __init__(self, rows: pd.DataFrame, labels: list[str] | None, tokenizer, label2id: dict[str, int] | None, max_length: int):
        self.rows = rows.reset_index(drop=True)
        self.texts = self.rows[INPUT_TEXT_FIELD].fillna('').astype(str).tolist()
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
        if self.labels is not None and self.label2id is not None:
            item['labels'] = torch.tensor(self.label2id[self.labels[idx]], dtype=torch.long)
        return item


def make_loader(rows: pd.DataFrame, labels: list[str] | None, tokenizer, label2id: dict[str, int] | None, max_length: int, batch_size: int, shuffle: bool):
    dataset = HistoryDataset(rows, labels, tokenizer, label2id, max_length)
    return DataLoader(dataset, batch_size=batch_size, shuffle=shuffle)


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


def predict_model(model, loader):
    model.eval()
    all_preds = []
    with torch.no_grad():
        for batch in loader:
            batch = {key: value.to(device) for key, value in batch.items()}
            outputs = model(**batch)
            preds = torch.argmax(outputs.logits, dim=-1)
            all_preds.extend(preds.cpu().tolist())
    return all_preds


def compute_metrics(true_labels: list[str], pred_labels: list[str]) -> dict:
    return {
        'accuracy': float(accuracy_score(true_labels, pred_labels)),
        'precision_macro': float(precision_score(true_labels, pred_labels, average='macro', zero_division=0)),
        'recall_macro': float(recall_score(true_labels, pred_labels, average='macro', zero_division=0)),
        'f1_macro': float(f1_score(true_labels, pred_labels, average='macro', zero_division=0)),
        'f1_weighted': float(f1_score(true_labels, pred_labels, average='weighted', zero_division=0)),
        'classification_report': classification_report(true_labels, pred_labels, output_dict=True, zero_division=0),
    }
"""
    ),
    md("## 6. Train/Predict One Target"),
    code(
        """
def train_predict_target(train_df: pd.DataFrame, test_df: pd.DataFrame, target: str, output_dir: Path) -> tuple[dict, pd.DataFrame]:
    set_seed(RANDOM_STATE)
    output_dir.mkdir(parents=True, exist_ok=True)
    config = TARGET_CONFIG[target]

    train_labels = train_df[target].astype(str).tolist()
    test_labels = test_df[target].astype(str).tolist()
    label2id, id2label = make_label_maps(train_labels + test_labels)

    unseen_labels = sorted(set(test_labels) - set(label2id))
    if unseen_labels:
        print(f'WARNING: {target} has unseen test labels: {unseen_labels}')

    tokenizer = AutoTokenizer.from_pretrained(MODEL_NAME)
    train_loader = make_loader(train_df, train_labels, tokenizer, label2id, config['max_length'], config['batch_size'], shuffle=True)
    test_loader = make_loader(test_df, test_labels, tokenizer, label2id, config['max_length'], config['batch_size'], shuffle=False)

    model = AutoModelForSequenceClassification.from_pretrained(
        MODEL_NAME,
        num_labels=len(label2id),
        id2label=id2label,
        label2id=label2id,
    ).to(device)

    class_weight = make_class_weight_tensor(train_labels, label2id) if config['use_class_weight'] else None
    loss_fn = torch.nn.CrossEntropyLoss(weight=class_weight.to(device) if class_weight is not None else None)
    optimizer = torch.optim.AdamW(model.parameters(), lr=config['learning_rate'])
    total_steps = max(1, len(train_loader) * config['max_epochs'])
    scheduler = get_linear_schedule_with_warmup(
        optimizer,
        num_warmup_steps=int(total_steps * 0.1),
        num_training_steps=total_steps,
    )

    history = []
    for epoch in range(1, config['max_epochs'] + 1):
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

        avg_train_loss = float(mean(train_losses)) if train_losses else 0.0
        history.append({'epoch': epoch, 'train_loss': avg_train_loss})
        print(f'{target} epoch {epoch}/{config["max_epochs"]}: train_loss={avg_train_loss:.4f}')

    test_loss, pred_ids, true_ids = evaluate_model(model, test_loader, loss_fn)
    pred_labels = [id2label[pred_id] for pred_id in pred_ids]
    true_labels = [id2label[true_id] for true_id in true_ids]
    metrics = compute_metrics(true_labels, pred_labels)

    pred_df = test_df[['ml_sequence_index', 'round_no', 'question_no', 'problem_id', INPUT_TEXT_FIELD, *TARGETS]].copy()
    pred_df[f'true_{target}'] = true_labels
    pred_df[f'pred_{target}'] = pred_labels
    pred_df[f'is_correct_{target}'] = pred_df[f'true_{target}'] == pred_df[f'pred_{target}']
    pred_df.to_csv(output_dir / f'{target}_predictions.csv', index=False, encoding='utf-8-sig')

    labels_order = sorted(set(true_labels) | set(pred_labels))
    cm = confusion_matrix(true_labels, pred_labels, labels=labels_order)
    plt.figure(figsize=(max(7, len(labels_order) * 0.7), max(5, len(labels_order) * 0.55)))
    plt.imshow(cm, cmap='Blues')
    plt.title(f'{target} confusion matrix')
    plt.xticks(range(len(labels_order)), labels_order, rotation=45, ha='right')
    plt.yticks(range(len(labels_order)), labels_order)
    plt.xlabel('predicted')
    plt.ylabel('actual')
    for i in range(len(labels_order)):
        for j in range(len(labels_order)):
            plt.text(j, i, cm[i, j], ha='center', va='center', fontsize=8)
    plt.tight_layout()
    plt.savefig(output_dir / f'{target}_confusion_matrix.png', dpi=150)
    plt.close()

    result = {
        'target': target,
        'train_size': int(len(train_df)),
        'test_size': int(len(test_df)),
        'train_round_min': int(train_df['round_no'].min()),
        'train_round_max': int(train_df['round_no'].max()),
        'test_round': int(test_df['round_no'].iloc[0]),
        'params': {
            'model_name': MODEL_NAME,
            'max_length': config['max_length'],
            'batch_size': config['batch_size'],
            'learning_rate': config['learning_rate'],
            'max_epochs': config['max_epochs'],
            'use_class_weight': config['use_class_weight'],
        },
        'test_loss': float(test_loss),
        'metrics': metrics,
        'history': history,
        'label_counts_train': dict(Counter(train_labels)),
        'label_counts_test': dict(Counter(test_labels)),
        'label_counts_pred': dict(Counter(pred_labels)),
        'unseen_test_labels': unseen_labels,
    }
    (output_dir / f'{target}_results.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')

    if SAVE_MODEL:
        model.save_pretrained(output_dir / f'{target}_saved_model')
        tokenizer.save_pretrained(output_dir / f'{target}_saved_model')

    del model
    gc.collect()
    if torch.cuda.is_available():
        torch.cuda.empty_cache()

    return result, pred_df
"""
    ),
    md("## 7. Run Cumulative Trend Prediction"),
    code(
        """
OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)

summary_rows = []
combo_rows = []
all_combined = []

for target_round in PREDICT_ROUNDS:
    train_df, test_df = get_train_test_df(target_round)
    round_dir = OUTPUT_ROOT / f'round_{target_round:02d}'
    round_dir.mkdir(parents=True, exist_ok=True)

    print('\\n' + '=' * 90)
    print(f'round {target_round}: train rounds {train_df.round_no.min()}~{train_df.round_no.max()} ({len(train_df)} rows), test {len(test_df)} rows')

    pred_parts = {}
    for target in TARGETS:
        target_dir = round_dir / target
        result, pred_df = train_predict_target(train_df, test_df, target, target_dir)
        pred_parts[target] = pred_df[['problem_id', f'true_{target}', f'pred_{target}', f'is_correct_{target}']]

        summary_rows.append({
            'train_mode': TRAIN_MODE,
            'target_round': target_round,
            'target': target,
            'max_length': result['params']['max_length'],
            'learning_rate': result['params']['learning_rate'],
            'batch_size': result['params']['batch_size'],
            'max_epochs': result['params']['max_epochs'],
            'use_class_weight': result['params']['use_class_weight'],
            'train_round_min': int(train_df['round_no'].min()),
            'train_round_max': int(train_df['round_no'].max()),
            'train_size': len(train_df),
            'test_size': len(test_df),
            'accuracy': result['metrics']['accuracy'],
            'f1_macro': result['metrics']['f1_macro'],
            'f1_weighted': result['metrics']['f1_weighted'],
        })

    combined = test_df[['ml_sequence_index', 'round_no', 'question_no', 'problem_id', INPUT_TEXT_FIELD, *TARGETS]].copy()
    for target in TARGETS:
        combined = combined.merge(pred_parts[target], on='problem_id', how='left')

    combined['true_era_topic_train'] = combined['true_era'] + ' + ' + combined['true_topic_train']
    combined['pred_era_topic_train'] = combined['pred_era'] + ' + ' + combined['pred_topic_train']
    combined['true_era_topic_train_topic'] = combined['true_era_topic_train'] + ' + ' + combined['true_topic']
    combined['pred_era_topic_train_topic'] = combined['pred_era_topic_train'] + ' + ' + combined['pred_topic']
    combined['is_correct_era_topic_train'] = combined['true_era_topic_train'] == combined['pred_era_topic_train']
    combined['is_correct_all_three'] = combined['true_era_topic_train_topic'] == combined['pred_era_topic_train_topic']

    combined.to_csv(round_dir / f'round_{target_round:02d}_combined_predictions.csv', index=False, encoding='utf-8-sig')
    all_combined.append(combined)

    combo_rows.append({
        'train_mode': TRAIN_MODE,
        'target_round': target_round,
        'train_round_min': int(train_df['round_no'].min()),
        'train_round_max': int(train_df['round_no'].max()),
        'test_size': len(test_df),
        'era_topic_train_accuracy': float(combined['is_correct_era_topic_train'].mean()),
        'all_three_accuracy': float(combined['is_correct_all_three'].mean()),
    })

    pred_trend = combined['pred_era_topic_train'].value_counts().rename_axis('era_topic_train').reset_index(name='count')
    pred_trend['ratio'] = pred_trend['count'] / pred_trend['count'].sum()
    pred_trend.to_csv(round_dir / f'round_{target_round:02d}_pred_era_topic_train_trend.csv', index=False, encoding='utf-8-sig')

    actual_trend = combined['true_era_topic_train'].value_counts().rename_axis('era_topic_train').reset_index(name='count')
    actual_trend['ratio'] = actual_trend['count'] / actual_trend['count'].sum()
    actual_trend.to_csv(round_dir / f'round_{target_round:02d}_actual_era_topic_train_trend.csv', index=False, encoding='utf-8-sig')

summary_df = pd.DataFrame(summary_rows)
combo_df = pd.DataFrame(combo_rows)
all_combined_df = pd.concat(all_combined, ignore_index=True)

summary_df.to_csv(OUTPUT_ROOT / 'target_metrics_by_round.csv', index=False, encoding='utf-8-sig')
combo_df.to_csv(OUTPUT_ROOT / 'combo_metrics_by_round.csv', index=False, encoding='utf-8-sig')
all_combined_df.to_csv(OUTPUT_ROOT / 'all_rounds_combined_predictions.csv', index=False, encoding='utf-8-sig')

display(summary_df)
display(combo_df)
"""
    ),
    md("## 8. Aggregate Trend Summary"),
    code(
        """
pred_combo_trend = (
    all_combined_df
    .groupby(['round_no', 'pred_era_topic_train'])
    .size()
    .rename('count')
    .reset_index()
)
pred_combo_trend['ratio'] = pred_combo_trend.groupby('round_no')['count'].transform(lambda s: s / s.sum())
pred_combo_trend = pred_combo_trend.sort_values(['round_no', 'count'], ascending=[True, False])
pred_combo_trend.to_csv(OUTPUT_ROOT / 'pred_era_topic_train_trend_by_round.csv', index=False, encoding='utf-8-sig')

actual_combo_trend = (
    all_combined_df
    .groupby(['round_no', 'true_era_topic_train'])
    .size()
    .rename('count')
    .reset_index()
)
actual_combo_trend['ratio'] = actual_combo_trend.groupby('round_no')['count'].transform(lambda s: s / s.sum())
actual_combo_trend = actual_combo_trend.sort_values(['round_no', 'count'], ascending=[True, False])
actual_combo_trend.to_csv(OUTPUT_ROOT / 'actual_era_topic_train_trend_by_round.csv', index=False, encoding='utf-8-sig')

pred_detail_trend = (
    all_combined_df
    .groupby(['round_no', 'pred_era_topic_train_topic'])
    .size()
    .rename('count')
    .reset_index()
)
pred_detail_trend['ratio'] = pred_detail_trend.groupby('round_no')['count'].transform(lambda s: s / s.sum())
pred_detail_trend = pred_detail_trend.sort_values(['round_no', 'count'], ascending=[True, False])
pred_detail_trend.to_csv(OUTPUT_ROOT / 'pred_era_topic_train_topic_detail_by_round.csv', index=False, encoding='utf-8-sig')

display(pred_combo_trend.groupby('round_no').head(10))
"""
    ),
    md("## 9. Save Markdown Report"),
    code(
        """
target_table = summary_df.to_markdown(index=False)
combo_table = combo_df.to_markdown(index=False)
trend_table = pred_combo_trend.groupby('round_no').head(5).to_markdown(index=False)
target_config_text = json.dumps(TARGET_CONFIG, ensure_ascii=False, indent=2)

md_text = f'''# Trend Prediction era + topic_train v1

## Config

- train_mode: {TRAIN_MODE}
- base_min_round: {BASE_MIN_ROUND}
- base_max_round: {BASE_MAX_ROUND}
- predict_rounds: {PREDICT_ROUNDS}
- targets: {TARGETS}
- model: {MODEL_NAME}
- input_text_field: {INPUT_TEXT_FIELD}

```json
{target_config_text}
```

## Target Metrics By Round

{target_table}

## Combination Metrics By Round

{combo_table}

## Predicted era + topic_train Trend Top5 By Round

{trend_table}
'''

(OUTPUT_ROOT / 'trend_predict_era_topic_v1_report.md').write_text(md_text, encoding='utf-8')
print(OUTPUT_ROOT / 'trend_predict_era_topic_v1_report.md')
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
