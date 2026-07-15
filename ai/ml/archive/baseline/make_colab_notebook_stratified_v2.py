# 층화추출 baseline 평가용 Colab 노트북을 생성하는 스크립트입니다.
# v1 시간순 평가와 분리해 v2_stratified 실험으로 관리합니다.
# 각 target별로 해당 라벨 기준 stratified split을 수행합니다.

from __future__ import annotations

import json
from pathlib import Path


OUT = Path(__file__).resolve().parent / "colab_train_baseline_tfidf_v2_stratified.ipynb"


def lines(text: str) -> list[str]:
    return text.strip("\n").splitlines(keepends=True)


def md(text: str) -> dict:
    return {"cell_type": "markdown", "metadata": {}, "source": lines(text)}


def code(text: str) -> dict:
    return {"cell_type": "code", "execution_count": None, "metadata": {}, "outputs": [], "source": lines(text)}


cells = [
    md(
        """
# TF-IDF Baseline v2 - Stratified Evaluation

이 노트북은 v1 시간순 평가와 별도로, 층화추출 평가를 수행합니다.

핵심 차이:

```text
v1 시간순 평가:
47~70회 train -> 71~78회 test

v2 층화추출 평가:
47~78회 전체를 target 라벨 비율이 유지되도록 train/test 분리
```

각 모델은 서로 다른 target을 예측하므로, 층화 기준도 target별로 다르게 둡니다.

```text
era 모델           -> era 기준 stratified split
topic 모델         -> topic 기준 stratified split
question_type 모델 -> question_type 기준 stratified split
```

이 평가는 최신 회차 예측 평가가 아니라, 모델 자체의 일반 분류 성능을 확인하는 보조 평가입니다.
"""
    ),
    md(
        """
## 1. Google Drive 연결
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
## 2. 경로 설정 및 파일 확인

`common/split_v1/full_features_v1.json` 파일이 필요합니다.
"""
    ),
    code(
        """
from pathlib import Path

BASE_DIR = Path('/content/drive/MyDrive/Final_project')
COMMON_DIR = BASE_DIR / 'common'
SPLIT_DIR = COMMON_DIR / 'split_v1'
RESULT_DIR = COMMON_DIR / 'baseline_tfidf_v2_stratified'

FULL_JSON = SPLIT_DIR / 'full_features_v1.json'
RESULT_JSON = RESULT_DIR / 'baseline_tfidf_v2_stratified_results.json'
RESULT_MD = RESULT_DIR / 'baseline_tfidf_v2_stratified_results.md'

TARGET_COLUMNS = ['era', 'topic', 'question_type']
TEST_SIZE = 0.2
RANDOM_STATE = 42

print('BASE_DIR:', BASE_DIR)
print('FULL_JSON exists =', FULL_JSON.exists())
"""
    ),
    md(
        """
## 3. 라이브러리 불러오기

Colab에는 보통 `scikit-learn`이 기본 설치되어 있습니다.
"""
    ),
    code(
        """
# import 오류가 날 때만 아래 주석을 풀고 실행하세요.
# !pip install -q scikit-learn

import csv
import json
from collections import Counter
from typing import Any

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, classification_report, f1_score
from sklearn.model_selection import train_test_split
from sklearn.pipeline import Pipeline

print('libraries loaded')
"""
    ),
    md(
        """
## 4. 전체 피처 데이터 로드

전체 47~78회 1,600문항을 읽습니다. 층화추출은 이 전체 데이터에서 수행합니다.
"""
    ),
    code(
        """
def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding='utf-8'))


rows = read_json(FULL_JSON)
print('rows:', len(rows))
print('targets:', TARGET_COLUMNS)
"""
    ),
    md(
        """
## 5. 전체 라벨 분포 확인

층화추출 전 전체 데이터의 라벨 분포를 확인합니다.
"""
    ),
    code(
        """
def label_counts(rows: list[dict], target: str) -> Counter:
    return Counter(str(row.get(target) or '') for row in rows)


for target in TARGET_COLUMNS:
    print('\\n===', target, 'overall ===')
    for label, count in label_counts(rows, target).most_common():
        print(label, count)
"""
    ),
    md(
        """
## 6. Class Weight 계산 함수

층화추출에서는 train 데이터가 target별로 달라지므로, class weight도 각 target의 stratified train 기준으로 다시 계산합니다.
"""
    ),
    code(
        """
def balanced_class_weights(labels: list[str]) -> dict[str, float]:
    counts = Counter(labels)
    total = len(labels)
    class_count = len(counts)
    return {
        label: total / (class_count * count)
        for label, count in counts.items()
    }
"""
    ),
    md(
        """
## 7. 모델 입력/평가 함수 정의

모델 입력은 `text`만 사용합니다. 정답 라벨은 stratified split과 평가에만 사용합니다.
"""
    ),
    code(
        """
def get_texts(rows: list[dict]) -> list[str]:
    return [str(row.get('text') or '') for row in rows]


def get_labels(rows: list[dict], target: str) -> list[str]:
    return [str(row.get(target) or '') for row in rows]


def build_pipeline(class_weight: dict[str, float]):
    return Pipeline(
        steps=[
            (
                'tfidf',
                TfidfVectorizer(
                    analyzer='char_wb',
                    ngram_range=(2, 5),
                    min_df=2,
                    max_features=80000,
                    sublinear_tf=True,
                ),
            ),
            (
                'clf',
                LogisticRegression(
                    max_iter=2000,
                    class_weight=class_weight,
                    solver='liblinear',
                    random_state=RANDOM_STATE,
                ),
            ),
        ]
    )


def evaluate_predictions(y_true: list[str], y_pred: list[str]) -> dict:
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
"""
    ),
    md(
        """
## 8. Target별 층화추출 학습/평가 함수

각 target 라벨 비율이 유지되도록 train/test를 나눈 뒤, 해당 target 모델을 학습하고 평가합니다.
"""
    ),
    code(
        """
def train_one_target_stratified(rows: list[dict], target: str) -> dict:
    labels = get_labels(rows, target)
    train_rows, test_rows = train_test_split(
        rows,
        test_size=TEST_SIZE,
        random_state=RANDOM_STATE,
        stratify=labels,
    )

    x_train = get_texts(train_rows)
    y_train = get_labels(train_rows, target)
    x_test = get_texts(test_rows)
    y_test = get_labels(test_rows, target)

    class_weight = balanced_class_weights(y_train)
    model = build_pipeline(class_weight)
    model.fit(x_train, y_train)
    y_pred = model.predict(x_test).tolist()

    row_predictions = []
    for row, true_label, pred_label in zip(test_rows, y_test, y_pred):
        row_predictions.append(
            {
                'ml_sequence_index': row.get('ml_sequence_index'),
                'round_no': row.get('round_no'),
                'question_no': row.get('question_no'),
                'problem_id': row.get('problem_id'),
                'true_label': true_label,
                'pred_label': pred_label,
                'is_correct': true_label == pred_label,
                'text_preview': str(row.get('text') or '')[:160].replace('\\n', ' '),
            }
        )

    return {
        'target': target,
        'stratify_target': target,
        'train_rows': len(train_rows),
        'test_rows': len(test_rows),
        'train_counts': dict(Counter(y_train).most_common()),
        'test_counts': dict(Counter(y_test).most_common()),
        'pred_counts': dict(Counter(y_pred).most_common()),
        'class_weights': class_weight,
        'metrics': evaluate_predictions(y_test, y_pred),
        'row_predictions': row_predictions,
    }
"""
    ),
    md(
        """
## 9. era 층화추출 평가
"""
    ),
    code(
        """
results = {
    'experiment': 'baseline_tfidf_v2_stratified',
    'full_rows': len(rows),
    'test_size': TEST_SIZE,
    'random_state': RANDOM_STATE,
    'targets': {},
}

results['targets']['era'] = train_one_target_stratified(rows, 'era')
results['targets']['era']['metrics']
"""
    ),
    md(
        """
## 10. topic 층화추출 평가
"""
    ),
    code(
        """
results['targets']['topic'] = train_one_target_stratified(rows, 'topic')
results['targets']['topic']['metrics']
"""
    ),
    md(
        """
## 11. question_type 층화추출 평가
"""
    ),
    code(
        """
results['targets']['question_type'] = train_one_target_stratified(rows, 'question_type')
results['targets']['question_type']['metrics']
"""
    ),
    md(
        """
## 12. 전체 요약 확인
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
## 13. Markdown 리포트 생성 함수
"""
    ),
    code(
        """
def build_markdown(results: dict) -> str:
    lines = []
    lines.append('# TF-IDF Baseline v2 - Stratified Evaluation')
    lines.append('')
    lines.append('- Input data: `split_v1/full_features_v1.json`')
    lines.append('- Split method: target-wise stratified split')
    lines.append('- Model: `TfidfVectorizer(char_wb 2~5gram) + LogisticRegression`')
    lines.append('- Imbalance handling: class weight recalculated from each stratified train set')
    lines.append('- Test size: 20%')
    lines.append('')
    lines.append('## Summary')
    lines.append('')
    lines.append('| target | train rows | test rows | accuracy | macro_f1 | weighted_f1 |')
    lines.append('|---|---:|---:|---:|---:|---:|')
    for target in TARGET_COLUMNS:
        target_result = results['targets'][target]
        metrics = target_result['metrics']
        lines.append(
            f\"| {target} | {target_result['train_rows']} | {target_result['test_rows']} | \"
            f\"{metrics['accuracy']:.4f} | {metrics['macro_f1']:.4f} | {metrics['weighted_f1']:.4f} |\"
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
## 14. 결과 저장
"""
    ),
    code(
        """
RESULT_DIR.mkdir(parents=True, exist_ok=True)
RESULT_JSON.write_text(json.dumps(results, ensure_ascii=False, indent=2) + '\\n', encoding='utf-8')
RESULT_MD.write_text(build_markdown(results), encoding='utf-8')

for target in TARGET_COLUMNS:
    pred_csv = RESULT_DIR / f'{target}_stratified_predictions_v2.csv'
    with pred_csv.open('w', encoding='utf-8-sig', newline='') as file:
        writer = csv.DictWriter(
            file,
            fieldnames=[
                'ml_sequence_index',
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
## 15. 저장된 Markdown 결과 확인
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
    },
    "nbformat": 4,
    "nbformat_minor": 5,
}

OUT.write_text(json.dumps(notebook, ensure_ascii=True, indent=2) + "\n", encoding="utf-8")
print(OUT)
