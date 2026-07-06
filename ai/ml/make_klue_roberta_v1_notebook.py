# KLUE/RoBERTa 기본 모델 평가용 Colab 노트북을 생성하는 스크립트입니다.
# class weight를 적용하지 않은 v1 BERT 실험을 구성합니다.
# 실행하면 ai/ml/klue_roberta_v1.ipynb 파일을 생성합니다.

from __future__ import annotations

import json
from pathlib import Path


OUT = Path(__file__).resolve().parent / "klue_roberta_v1.ipynb"


def lines(text: str) -> list[str]:
    return text.strip("\n").splitlines(keepends=True)


def md(text: str) -> dict:
    return {"cell_type": "markdown", "metadata": {}, "source": lines(text)}


def code(text: str) -> dict:
    return {"cell_type": "code", "execution_count": None, "metadata": {}, "outputs": [], "source": lines(text)}


cells = [
    md(
        """
# KLUE/RoBERTa v1

이 노트북은 `klue/roberta-base`를 사용해 한능검 ML v1 데이터를 분류합니다.

이번 v1 실험의 목적:

- TF-IDF baseline 이후 BERT 계열 기본 성능 확인
- class weight 미적용
- 이후 v2에서 class weight를 추가해 성능 변화를 비교

평가 구조:

```text
train_features_v1      -> 학습용, 정답 라벨 있음
predict_input_v1       -> 예측용, era/topic/question_type 빈칸
test_answer_v1         -> 채점용, 정답 라벨 있음
```

Colab 런타임은 GPU 권장입니다.
"""
    ),
    md(
        """
## 1. GPU 확인

`cuda`가 출력되면 GPU를 사용 중입니다. `cpu`가 나오면 Colab 런타임 유형을 GPU로 변경하세요.
"""
    ),
    code(
        """
import torch

device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
print('device:', device)
if device.type == 'cuda':
    print('gpu:', torch.cuda.get_device_name(0))
"""
    ),
    md(
        """
## 2. Google Drive 연결

`Final_project` 폴더가 있는 Google Drive를 Colab에 연결합니다.
"""
    ),
    code(
        """
from google.colab import drive
drive.mount('/content/drive')
"""
    ),
    md(
        """
## 3. 라이브러리 설치

처음 실행 시 `transformers`, `accelerate`, `scikit-learn`을 설치합니다.

Hugging Face 키값은 필요 없습니다. `klue/roberta-base`는 공개 모델이라 로그인 없이 다운로드할 수 있습니다.
"""
    ),
    code(
        """
!pip install -q transformers accelerate scikit-learn
"""
    ),
    md(
        """
## 4. 경로 설정 및 파일 확인

`common/split_v1` 폴더에 세 파일이 있어야 합니다.
"""
    ),
    code(
        """
from pathlib import Path

BASE_DIR = Path('/content/drive/MyDrive/Final_project')
COMMON_DIR = BASE_DIR / 'common'
SPLIT_DIR = COMMON_DIR / 'split_v1'
RESULT_DIR = COMMON_DIR / 'klue_roberta_v1'

TRAIN_JSON = SPLIT_DIR / 'train_features_v1.json'
PREDICT_JSON = SPLIT_DIR / 'predict_input_v1.json'
ANSWER_JSON = SPLIT_DIR / 'test_answer_v1.json'

RESULT_JSON = RESULT_DIR / 'klue_roberta_v1_results.json'
RESULT_MD = RESULT_DIR / 'klue_roberta_v1_results.md'

MODEL_NAME = 'klue/roberta-base'
TARGET_COLUMNS = ['era', 'topic', 'question_type']

print('BASE_DIR:', BASE_DIR)
for path in [TRAIN_JSON, PREDICT_JSON, ANSWER_JSON]:
    print(path.name, 'exists =', path.exists())
"""
    ),
    md(
        """
## 5. 기본 설정

처음 실험이므로 보수적인 값으로 시작합니다.

- `MAX_LENGTH = 256`
- `EPOCHS = 3`
- `BATCH_SIZE = 8`
- `LEARNING_RATE = 2e-5`
- class weight 미적용
"""
    ),
    code(
        """
import csv
import json
import random
from collections import Counter
from typing import Any

import numpy as np
import torch
from sklearn.metrics import accuracy_score, classification_report, f1_score
from torch.utils.data import DataLoader, Dataset
from transformers import AutoModelForSequenceClassification, AutoTokenizer, get_linear_schedule_with_warmup

MAX_LENGTH = 256
EPOCHS = 3
BATCH_SIZE = 8
LEARNING_RATE = 2e-5
RANDOM_STATE = 42

random.seed(RANDOM_STATE)
np.random.seed(RANDOM_STATE)
torch.manual_seed(RANDOM_STATE)
if torch.cuda.is_available():
    torch.cuda.manual_seed_all(RANDOM_STATE)
"""
    ),
    md(
        """
## 6. 데이터 로드

정상이라면 train 1200, predict 400, answer 400이 나와야 합니다.
"""
    ),
    code(
        """
def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding='utf-8'))


train_rows = read_json(TRAIN_JSON)
predict_rows = read_json(PREDICT_JSON)
answer_rows = read_json(ANSWER_JSON)

print('train rows:', len(train_rows))
print('predict rows:', len(predict_rows))
print('answer rows:', len(answer_rows))
"""
    ),
    md(
        """
## 7. 라벨 제거 여부 확인

`predict_input_v1`의 `era`, `topic`, `question_type`은 빈칸이어야 합니다.
"""
    ),
    code(
        """
print('[predict_input label blank check]')
for target in TARGET_COLUMNS:
    blank_count = sum(1 for row in predict_rows if not row.get(target))
    print(target, blank_count)

print('\\n[train sample labels]')
print({target: train_rows[0].get(target) for target in TARGET_COLUMNS})

print('\\n[predict sample labels]')
print({target: predict_rows[0].get(target) for target in TARGET_COLUMNS})

print('\\n[answer sample labels]')
print({target: answer_rows[0].get(target) for target in TARGET_COLUMNS})
"""
    ),
    md(
        """
## 8. Dataset 정의

모델 입력은 `text`만 사용합니다. 정답 라벨은 학습/평가에만 사용합니다.
"""
    ),
    code(
        """
class HanDataset(Dataset):
    def __init__(self, rows, tokenizer, label_to_id=None, target=None, max_length=256):
        self.rows = rows
        self.tokenizer = tokenizer
        self.label_to_id = label_to_id
        self.target = target
        self.max_length = max_length

    def __len__(self):
        return len(self.rows)

    def __getitem__(self, idx):
        row = self.rows[idx]
        text = str(row.get('text') or '')
        encoded = self.tokenizer(
            text,
            truncation=True,
            padding='max_length',
            max_length=self.max_length,
            return_tensors='pt',
        )
        item = {key: value.squeeze(0) for key, value in encoded.items()}
        if self.label_to_id is not None and self.target is not None:
            item['labels'] = torch.tensor(self.label_to_id[str(row[self.target])], dtype=torch.long)
        return item
"""
    ),
    md(
        """
## 9. 평가 함수 정의

Accuracy는 참고용이고, 라벨 인밸런스가 있으므로 Macro F1을 중요하게 봅니다.
"""
    ),
    code(
        """
def evaluate_predictions(y_true, y_pred):
    return {
        'accuracy': round(float(accuracy_score(y_true, y_pred)), 6),
        'macro_f1': round(float(f1_score(y_true, y_pred, average='macro', zero_division=0)), 6),
        'weighted_f1': round(float(f1_score(y_true, y_pred, average='weighted', zero_division=0)), 6),
        'classification_report': classification_report(
            y_true,
            y_pred,
            output_dict=True,
            zero_division=0,
        ),
    }


def make_label_maps(rows, target):
    labels = sorted(set(str(row[target]) for row in rows))
    label_to_id = {label: idx for idx, label in enumerate(labels)}
    id_to_label = {idx: label for label, idx in label_to_id.items()}
    return label_to_id, id_to_label
"""
    ),
    md(
        """
## 10. 학습/예측 함수 정의

이번 v1에서는 class weight를 적용하지 않습니다.
"""
    ),
    code(
        """
def train_and_predict_target(target):
    print(f'\\n===== target: {target} =====')
    tokenizer = AutoTokenizer.from_pretrained(MODEL_NAME)
    label_to_id, id_to_label = make_label_maps(train_rows, target)

    train_dataset = HanDataset(
        train_rows,
        tokenizer,
        label_to_id=label_to_id,
        target=target,
        max_length=MAX_LENGTH,
    )
    predict_dataset = HanDataset(
        predict_rows,
        tokenizer,
        label_to_id=None,
        target=None,
        max_length=MAX_LENGTH,
    )

    train_loader = DataLoader(train_dataset, batch_size=BATCH_SIZE, shuffle=True)
    predict_loader = DataLoader(predict_dataset, batch_size=BATCH_SIZE, shuffle=False)

    model = AutoModelForSequenceClassification.from_pretrained(
        MODEL_NAME,
        num_labels=len(label_to_id),
        id2label=id_to_label,
        label2id=label_to_id,
    ).to(device)

    optimizer = torch.optim.AdamW(model.parameters(), lr=LEARNING_RATE)
    total_steps = len(train_loader) * EPOCHS
    scheduler = get_linear_schedule_with_warmup(
        optimizer,
        num_warmup_steps=max(1, int(total_steps * 0.1)),
        num_training_steps=total_steps,
    )

    model.train()
    for epoch in range(EPOCHS):
        total_loss = 0.0
        for batch in train_loader:
            batch = {key: value.to(device) for key, value in batch.items()}
            optimizer.zero_grad()
            outputs = model(**batch)
            loss = outputs.loss
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            optimizer.step()
            scheduler.step()
            total_loss += loss.item()
        avg_loss = total_loss / max(1, len(train_loader))
        print(f'epoch {epoch + 1}/{EPOCHS} loss = {avg_loss:.4f}')

    model.eval()
    pred_ids = []
    with torch.no_grad():
        for batch in predict_loader:
            batch = {key: value.to(device) for key, value in batch.items()}
            outputs = model(**batch)
            pred_ids.extend(outputs.logits.argmax(dim=-1).cpu().tolist())

    y_pred = [id_to_label[pred_id] for pred_id in pred_ids]
    y_true = [str(row[target]) for row in answer_rows]
    metrics = evaluate_predictions(y_true, y_pred)

    row_predictions = []
    for pred_row, answer_row, true_label, pred_label in zip(predict_rows, answer_rows, y_true, y_pred):
        row_predictions.append(
            {
                'round_no': pred_row.get('round_no'),
                'question_no': pred_row.get('question_no'),
                'problem_id': pred_row.get('problem_id'),
                'true_label': true_label,
                'pred_label': pred_label,
                'is_correct': true_label == pred_label,
                'text_preview': str(pred_row.get('text') or '')[:160].replace('\\n', ' '),
            }
        )

    del model
    if torch.cuda.is_available():
        torch.cuda.empty_cache()

    return {
        'target': target,
        'label_to_id': label_to_id,
        'train_counts': dict(Counter(str(row[target]) for row in train_rows).most_common()),
        'test_counts': dict(Counter(y_true).most_common()),
        'pred_counts': dict(Counter(y_pred).most_common()),
        'metrics': metrics,
        'row_predictions': row_predictions,
    }
"""
    ),
    md(
        """
## 11. era 모델 학습/평가
"""
    ),
    code(
        """
results = {
    'experiment': 'klue_roberta_v1',
    'model_name': MODEL_NAME,
    'class_weight': False,
    'max_length': MAX_LENGTH,
    'epochs': EPOCHS,
    'batch_size': BATCH_SIZE,
    'learning_rate': LEARNING_RATE,
    'targets': {},
}

results['targets']['era'] = train_and_predict_target('era')
results['targets']['era']['metrics']
"""
    ),
    md(
        """
## 12. topic 모델 학습/평가
"""
    ),
    code(
        """
results['targets']['topic'] = train_and_predict_target('topic')
results['targets']['topic']['metrics']
"""
    ),
    md(
        """
## 13. question_type 모델 학습/평가
"""
    ),
    code(
        """
results['targets']['question_type'] = train_and_predict_target('question_type')
results['targets']['question_type']['metrics']
"""
    ),
    md(
        """
## 14. 전체 요약 확인
"""
    ),
    code(
        """
summary = {
    target: {
        key: results['targets'][target]['metrics'][key]
        for key in ['accuracy', 'macro_f1', 'weighted_f1']
    }
    for target in TARGET_COLUMNS
}
summary
"""
    ),
    md(
        """
## 15. Markdown 리포트 생성 함수
"""
    ),
    code(
        """
def build_markdown(results):
    lines = []
    lines.append('# KLUE/RoBERTa v1 Results')
    lines.append('')
    lines.append('- Model: `klue/roberta-base`')
    lines.append('- Class weight: not applied')
    lines.append('- Train: `split_v1/train_features_v1.json`')
    lines.append('- Predict input: `split_v1/predict_input_v1.json`')
    lines.append('- Test answer: `split_v1/test_answer_v1.json`')
    lines.append('')
    lines.append('## Summary')
    lines.append('')
    lines.append('| target | accuracy | macro_f1 | weighted_f1 |')
    lines.append('|---|---:|---:|---:|')
    for target in TARGET_COLUMNS:
        metrics = results['targets'][target]['metrics']
        lines.append(
            f\"| {target} | {metrics['accuracy']:.4f} | \"
            f\"{metrics['macro_f1']:.4f} | {metrics['weighted_f1']:.4f} |\"
        )
    lines.append('')

    for target in TARGET_COLUMNS:
        target_result = results['targets'][target]
        report = target_result['metrics']['classification_report']
        labels = sorted(
            set(target_result['train_counts'])
            | set(target_result['test_counts'])
            | set(target_result['pred_counts'])
        )
        lines.append(f'## {target}')
        lines.append('')
        lines.append('### Label Distribution')
        lines.append('')
        lines.append('| label | train | test | pred |')
        lines.append('|---|---:|---:|---:|')
        for label in labels:
            lines.append(
                f\"| {label} | {target_result['train_counts'].get(label, 0)} | \"
                f\"{target_result['test_counts'].get(label, 0)} | \"
                f\"{target_result['pred_counts'].get(label, 0)} |\"
            )
        lines.append('')
        lines.append('### Per-class Metrics')
        lines.append('')
        lines.append('| label | precision | recall | f1-score | support |')
        lines.append('|---|---:|---:|---:|---:|')
        for label in labels:
            values = report.get(label, {})
            lines.append(
                f\"| {label} | {values.get('precision', 0):.4f} | \"
                f\"{values.get('recall', 0):.4f} | \"
                f\"{values.get('f1-score', 0):.4f} | \"
                f\"{int(values.get('support', 0))} |\"
            )
        lines.append('')
    return '\\n'.join(lines) + '\\n'
"""
    ),
    md(
        """
## 16. 결과 저장
"""
    ),
    code(
        """
RESULT_DIR.mkdir(parents=True, exist_ok=True)
RESULT_JSON.write_text(json.dumps(results, ensure_ascii=False, indent=2) + '\\n', encoding='utf-8')
RESULT_MD.write_text(build_markdown(results), encoding='utf-8')

for target in TARGET_COLUMNS:
    pred_csv = RESULT_DIR / f'{target}_klue_roberta_v1_predictions.csv'
    with pred_csv.open('w', encoding='utf-8-sig', newline='') as file:
        writer = csv.DictWriter(
            file,
            fieldnames=[
                'round_no',
                'question_no',
                'problem_id',
                'true_label',
                'pred_label',
                'is_correct',
                'text_preview',
            ],
        )
        writer.writeheader()
        writer.writerows(results['targets'][target]['row_predictions'])
    print('saved predictions:', pred_csv)

print('saved json:', RESULT_JSON)
print('saved md:', RESULT_MD)
"""
    ),
    md(
        """
## 17. 저장된 결과 확인
"""
    ),
    code(
        """
print(RESULT_MD.read_text(encoding='utf-8')[:4000])
"""
    ),
]


notebook = {
    "cells": cells,
    "metadata": {
        "colab": {"provenance": []},
        "kernelspec": {"display_name": "Python 3", "name": "python3"},
        "language_info": {"name": "python"},
        "accelerator": "GPU",
    },
    "nbformat": 4,
    "nbformat_minor": 5,
}

OUT.write_text(json.dumps(notebook, ensure_ascii=True, indent=2) + "\n", encoding="utf-8")
print(OUT)
