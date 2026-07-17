from __future__ import annotations

import json
from pathlib import Path


OUT = Path(__file__).resolve().parent / "runpod_train_model_sets_70_78_v2.ipynb"


def lines(text: str) -> list[str]:
    return text.strip("\n").splitlines(keepends=True)


def md(text: str) -> dict:
    return {"cell_type": "markdown", "metadata": {}, "source": lines(text)}


def code(text: str) -> dict:
    return {"cell_type": "code", "execution_count": None, "metadata": {}, "outputs": [], "source": lines(text)}


cells = [
    md(
        """
# RunPod 70회차/78회차 기준 모델 세트 저장 v2

이 노트북은 모델 세트 2개를 저장합니다.

1. `through_70`: 47~70회차로 학습한 평가 재현용 모델 3개
2. `through_78`: 47~78회차로 학습한 최종 운영용 모델 3개

각 모델 세트에는 `era`, `topic_train`, `topic` 모델이 각각 저장됩니다.
"""
    ),
    md("## 1. 설정"),
    code(
        """
from pathlib import Path

# RunPod 기본 경로입니다.
BASE_DIR = Path('/workspace')
COMMON_DIR = BASE_DIR / 'common'
OUTPUT_ROOT = BASE_DIR / 'output' / 'model_sets_70_78_v2'

# v2 전처리 결과 파일을 찾는 후보 경로입니다.
FEATURE_CSV_CANDIDATES = [
    COMMON_DIR / 'split_v2' / 'full_features_v2.csv',
    COMMON_DIR / 'full_features_v2.csv',
    COMMON_DIR / 'ml_han_features_v2.csv',
]

MODEL_NAME = 'klue/roberta-base'

# text = 지문 + 질문 + 키워드
INPUT_TEXT_FIELD = 'text'

TARGETS = ['era', 'topic_train', 'topic']

# 저장할 모델 세트입니다.
# through_70: 71~78 평가 재현용
# through_78: 최종 운영용
MODEL_SETS = {
    'through_70': {
        'train_round_min': 47,
        'train_round_max': 70,
        'description': '47~70회차 학습, 71~78회차 평가 재현용',
    },
    'through_78': {
        'train_round_min': 47,
        'train_round_max': 78,
        'description': '47~78회차 전체 학습, 최종 운영용',
    },
}

RANDOM_STATE = 42
SAVE_MODEL = True

# target별 최적 파라미터입니다.
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
print('MODEL_SETS =')
for name, config in MODEL_SETS.items():
    print(name, config)
"""
    ),
    md("## 2. 라이브러리 설치"),
    code("!pip install -q transformers accelerate pandas"),
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
    print('WARNING: GPU를 사용할 수 없습니다. RunPod GPU Pod인지 확인하세요.')
"""
    ),
    md("## 4. 데이터 로드"),
    code(
        """
import gc
import json
import random
from collections import Counter
from statistics import mean

import numpy as np
import pandas as pd
import torch
from torch.utils.data import DataLoader, Dataset
from transformers import AutoModelForSequenceClassification, AutoTokenizer, get_linear_schedule_with_warmup


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

df = df.sort_values(['round_no', 'question_no', 'problem_id']).reset_index(drop=True)

print('FEATURE_CSV =', FEATURE_CSV)
print('rows =', len(df))
display(df.groupby('round_no').size().rename('count').reset_index())
"""
    ),
    md("## 5. 공통 함수"),
    code(
        """
def set_seed(seed: int) -> None:
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)


def make_label_maps(labels: list[str]) -> tuple[dict[str, int], dict[int, str]]:
    label_list = sorted(set(labels))
    label2id = {label: idx for idx, label in enumerate(label_list)}
    id2label = {idx: label for label, idx in label2id.items()}
    return label2id, id2label


def make_class_weight_tensor(train_labels: list[str], label2id: dict[str, int]) -> torch.Tensor:
    counts = Counter(train_labels)
    total = len(train_labels)
    class_count = len(label2id)
    weights = []
    for label, _idx in sorted(label2id.items(), key=lambda item: item[1]):
        weights.append(total / (class_count * counts[label]) if counts[label] else 0.0)
    return torch.tensor(weights, dtype=torch.float)


class HistoryDataset(Dataset):
    def __init__(self, rows: pd.DataFrame, labels: list[str], tokenizer, label2id: dict[str, int], max_length: int):
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
        item['labels'] = torch.tensor(self.label2id[self.labels[idx]], dtype=torch.long)
        return item


def make_loader(rows: pd.DataFrame, labels: list[str], tokenizer, label2id: dict[str, int], max_length: int, batch_size: int):
    dataset = HistoryDataset(rows, labels, tokenizer, label2id, max_length)
    return DataLoader(dataset, batch_size=batch_size, shuffle=True)
"""
    ),
    md("## 6. 모델 학습/저장 함수"),
    code(
        """
def train_and_save_model(model_set_name: str, model_set_config: dict, target: str) -> dict:
    set_seed(RANDOM_STATE)
    target_config = TARGET_CONFIG[target]
    train_round_min = model_set_config['train_round_min']
    train_round_max = model_set_config['train_round_max']

    train_df = (
        df[(df['round_no'] >= train_round_min) & (df['round_no'] <= train_round_max)]
        .copy()
        .reset_index(drop=True)
    )
    output_dir = OUTPUT_ROOT / model_set_name / target
    model_dir = output_dir / 'saved_model'
    output_dir.mkdir(parents=True, exist_ok=True)

    train_labels = train_df[target].astype(str).tolist()
    label2id, id2label = make_label_maps(train_labels)

    tokenizer = AutoTokenizer.from_pretrained(MODEL_NAME)
    train_loader = make_loader(
        train_df,
        train_labels,
        tokenizer,
        label2id,
        target_config['max_length'],
        target_config['batch_size'],
    )

    model = AutoModelForSequenceClassification.from_pretrained(
        MODEL_NAME,
        num_labels=len(label2id),
        id2label=id2label,
        label2id=label2id,
    ).to(device)

    class_weight = make_class_weight_tensor(train_labels, label2id) if target_config['use_class_weight'] else None
    loss_fn = torch.nn.CrossEntropyLoss(weight=class_weight.to(device) if class_weight is not None else None)
    optimizer = torch.optim.AdamW(model.parameters(), lr=target_config['learning_rate'])
    total_steps = max(1, len(train_loader) * target_config['max_epochs'])
    scheduler = get_linear_schedule_with_warmup(
        optimizer,
        num_warmup_steps=int(total_steps * 0.1),
        num_training_steps=total_steps,
    )

    history = []
    for epoch in range(1, target_config['max_epochs'] + 1):
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
        print(f'{model_set_name}/{target} epoch {epoch}/{target_config["max_epochs"]}: train_loss={avg_train_loss:.4f}')

    if SAVE_MODEL:
        model.save_pretrained(model_dir)
        tokenizer.save_pretrained(model_dir)

    label_counts = dict(Counter(train_labels))
    pd.DataFrame(history).to_csv(output_dir / 'train_history.csv', index=False, encoding='utf-8-sig')
    pd.DataFrame(
        [{'label': label, 'count': count} for label, count in sorted(label_counts.items(), key=lambda item: item[0])]
    ).to_csv(output_dir / 'label_counts.csv', index=False, encoding='utf-8-sig')

    metadata = {
        'model_set': model_set_name,
        'description': model_set_config['description'],
        'target': target,
        'input_text_field': INPUT_TEXT_FIELD,
        'feature_csv': str(FEATURE_CSV),
        'train_round_min': train_round_min,
        'train_round_max': train_round_max,
        'train_size': int(len(train_df)),
        'model_name': MODEL_NAME,
        'params': target_config,
        'labels': sorted(label2id.keys()),
        'label2id': label2id,
        'id2label': {str(k): v for k, v in id2label.items()},
        'label_counts': label_counts,
        'history': history,
        'saved_model_dir': str(model_dir),
    }
    (output_dir / 'model_metadata.json').write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding='utf-8')

    del model
    gc.collect()
    if torch.cuda.is_available():
        torch.cuda.empty_cache()

    return metadata
"""
    ),
    md("## 7. 6개 모델 학습/저장"),
    code(
        """
OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)

all_metadata = {}
summary_rows = []

for model_set_name, model_set_config in MODEL_SETS.items():
    all_metadata[model_set_name] = {}
    for target in TARGETS:
        print('\\n' + '=' * 90)
        print(f'모델 학습 시작: {model_set_name} / {target}')
        metadata = train_and_save_model(model_set_name, model_set_config, target)
        all_metadata[model_set_name][target] = metadata
        summary_rows.append({
            'model_set': model_set_name,
            'description': model_set_config['description'],
            'target': target,
            'train_round_min': metadata['train_round_min'],
            'train_round_max': metadata['train_round_max'],
            'train_size': metadata['train_size'],
            'label_count': len(metadata['labels']),
            'max_length': metadata['params']['max_length'],
            'learning_rate': metadata['params']['learning_rate'],
            'batch_size': metadata['params']['batch_size'],
            'max_epochs': metadata['params']['max_epochs'],
            'saved_model_dir': metadata['saved_model_dir'],
        })

(OUTPUT_ROOT / 'model_sets_metadata.json').write_text(
    json.dumps(all_metadata, ensure_ascii=False, indent=2),
    encoding='utf-8',
)

summary_df = pd.DataFrame(summary_rows)
summary_df.to_csv(OUTPUT_ROOT / 'model_sets_summary.csv', index=False, encoding='utf-8-sig')
display(summary_df)
"""
    ),
    md("## 8. 저장 결과 확인"),
    code(
        """
for model_set_name in MODEL_SETS:
    for target in TARGETS:
        model_dir = OUTPUT_ROOT / model_set_name / target / 'saved_model'
        print('\\n', model_set_name, target, model_dir)
        print('exists =', model_dir.exists())
        if model_dir.exists():
            for path in sorted(model_dir.iterdir()):
                print(' -', path.name)
"""
    ),
    md("## 9. 리포트 저장"),
    code(
        """
summary_table = summary_df.to_markdown(index=False)
target_config_text = json.dumps(TARGET_CONFIG, ensure_ascii=False, indent=2)
model_sets_text = json.dumps(MODEL_SETS, ensure_ascii=False, indent=2)

md_text = f'''# 70회차/78회차 기준 모델 세트 저장 v2

## 목적

모델 세트 2개를 저장했다.

1. `through_70`: 47~70회차 학습, 71~78회차 평가 재현용
2. `through_78`: 47~78회차 전체 학습, 최종 운영용

각 세트는 `era`, `topic_train`, `topic` 3개 모델로 구성된다.

## 모델 세트

```json
{model_sets_text}
```

## Target별 파라미터

```json
{target_config_text}
```

## 저장 모델 요약

{summary_table}

## 산출물

- `model_sets_summary.csv`
- `model_sets_metadata.json`
- `through_70/era/saved_model/`
- `through_70/topic_train/saved_model/`
- `through_70/topic/saved_model/`
- `through_78/era/saved_model/`
- `through_78/topic_train/saved_model/`
- `through_78/topic/saved_model/`
'''

report_path = OUTPUT_ROOT / 'model_sets_70_78_v2_report.md'
report_path.write_text(md_text, encoding='utf-8')
print(report_path)
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
