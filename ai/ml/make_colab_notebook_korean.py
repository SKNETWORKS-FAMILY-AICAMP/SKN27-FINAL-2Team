# Colab baseline 노트북의 Markdown 설명을 한글로 복원하는 생성 스크립트입니다.
# PowerShell 인코딩 문제를 피하기 위해 한글 문구를 UTF-8 Python 파일 안에 직접 보관합니다.
# 실행하면 colab_train_baseline_tfidf_v1.ipynb를 Colab 호환 JSON 형식으로 다시 저장합니다.

from __future__ import annotations

import json
from pathlib import Path


OUT = Path(__file__).resolve().parent / "colab_train_baseline_tfidf_v1.ipynb"


def lines(text: str) -> list[str]:
    return text.strip("\n").splitlines(keepends=True)


def md(text: str) -> dict:
    return {"cell_type": "markdown", "metadata": {}, "source": lines(text)}


def code(text: str) -> dict:
    return {"cell_type": "code", "execution_count": None, "metadata": {}, "outputs": [], "source": lines(text)}


cells = [
    md(
        """
# TF-IDF Baseline v1

한능검 ML v1 데이터로 첫 baseline 성능을 확인하는 노트북입니다.

진행 흐름:
1. Google Drive 연결
2. 입력 파일 확인
3. train/test 데이터 로드
4. 라벨 분포 확인
5. TF-IDF baseline 함수 정의
6. `era`, `topic`, `question_type` 순서로 학습/평가
7. 결과 파일 저장

이 baseline은 딥러닝이 아니므로 CPU 런타임으로 충분합니다.
"""
    ),
    md(
        """
## 1. Google Drive 연결

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
## 2. 경로 설정 및 파일 확인

`common` 폴더 안의 세 파일이 모두 `exists = True`로 나와야 합니다.
"""
    ),
    code(
        """
from pathlib import Path

BASE_DIR = Path('/content/drive/MyDrive/Final_project')
COMMON_DIR = BASE_DIR / 'common'
RESULT_DIR = COMMON_DIR / 'baseline_tfidf_v1'

TRAIN_JSONL = COMMON_DIR / 'ml_han_weighted_train_v1.jsonl'
TEST_JSONL = COMMON_DIR / 'ml_han_weighted_test_v1.jsonl'
CLASS_WEIGHT_JSON = COMMON_DIR / 'ml_han_class_weights_v1.json'

RESULT_JSON = RESULT_DIR / 'baseline_tfidf_results_v1.json'
RESULT_MD = RESULT_DIR / 'baseline_tfidf_results_v1.md'

TARGET_COLUMNS = ['era', 'topic', 'question_type']

print('BASE_DIR:', BASE_DIR)
print('COMMON_DIR:', COMMON_DIR)
print()
for path in [TRAIN_JSONL, TEST_JSONL, CLASS_WEIGHT_JSON]:
    print(path.name, 'exists =', path.exists())
"""
    ),
    md(
        """
## 3. 라이브러리 불러오기

Colab에는 보통 `scikit-learn`이 기본 설치되어 있습니다. import 오류가 나면 주석 처리된 설치 명령을 실행하세요.
"""
    ),
    code(
        """
# import 오류가 날 때만 아래 주석을 풀고 실행하세요.
# !pip install -q scikit-learn

import json
from collections import Counter
from typing import Any

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, classification_report, f1_score
from sklearn.pipeline import Pipeline

print('libraries loaded')
"""
    ),
    md(
        """
## 4. 데이터 로드 함수 정의

JSON/JSONL 파일을 읽는 함수입니다. 이 단계에서는 아직 학습을 하지 않습니다.
"""
    ),
    code(
        """
def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding='utf-8'))


def read_jsonl(path: Path) -> list[dict]:
    rows = []
    with path.open('r', encoding='utf-8') as file:
        for line in file:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows
"""
    ),
    md(
        """
## 5. 데이터 로드

정상이라면 다음 행 수가 나와야 합니다.

- train: 1200
- test: 400
"""
    ),
    code(
        """
train_rows = read_jsonl(TRAIN_JSONL)
test_rows = read_jsonl(TEST_JSONL)
assets = read_json(CLASS_WEIGHT_JSON)

print('train rows:', len(train_rows))
print('test rows:', len(test_rows))
print('targets:', TARGET_COLUMNS)
print('asset targets:', assets['target_columns'])
"""
    ),
    md(
        """
## 6. 샘플 데이터 확인

모델 입력 `text`와 라벨 구조를 한 문항만 확인합니다. 정답 라벨이 `text` 안에 직접 들어가지 않았는지 확인하는 단계입니다.
"""
    ),
    code(
        """
sample = train_rows[0]
print('keys:', sample.keys())
print('\\n[text preview]')
print(sample['text'][:500])
print('\\n[labels]')
print(sample['labels'])
print('\\n[sample_weights]')
print(sample['sample_weights'])
"""
    ),
    md(
        """
## 7. Train/Test 라벨 분포 확인

학습 전에 각 타깃의 train/test 라벨 분포를 확인합니다.
"""
    ),
    code(
        """
def label_counts(rows: list[dict], target: str) -> Counter:
    return Counter(str(row['labels'][target]) for row in rows)

for target in TARGET_COLUMNS:
    print('\\n===', target, '===')
    print('[train]')
    for label, count in label_counts(train_rows, target).most_common():
        print(label, count)
    print('[test]')
    for label, count in label_counts(test_rows, target).most_common():
        print(label, count)
"""
    ),
    md(
        """
## 8. Class Weight 확인

소수 라벨일수록 weight가 크게 나와야 합니다. 이 weight는 Logistic Regression 학습에 사용됩니다.
"""
    ),
    code(
        """
for target in TARGET_COLUMNS:
    print('\\n===', target, 'class weights ===')
    weights = assets['assets'][target]['class_weights']
    for label, weight in sorted(weights.items(), key=lambda x: x[1], reverse=True):
        print(label, weight)
"""
    ),
    md(
        """
## 9. 모델 입력/라벨 추출 함수

각 모델은 같은 `text`를 입력으로 사용하고, 타깃 라벨만 바꿔서 학습합니다.
"""
    ),
    code(
        """
def get_texts(rows: list[dict]) -> list[str]:
    return [str(row.get('text') or '') for row in rows]


def get_labels(rows: list[dict], target: str) -> list[str]:
    return [str(row['labels'][target]) for row in rows]


def get_class_weight(assets: dict, target: str) -> dict[str, float]:
    return {
        label: float(weight)
        for label, weight in assets['assets'][target]['class_weights'].items()
    }
"""
    ),
    md(
        """
## 10. TF-IDF + Logistic Regression 모델 함수

- TF-IDF: 텍스트를 글자 n-gram 중요도 벡터로 변환합니다.
- Logistic Regression: 변환된 벡터로 라벨을 분류합니다.
- `class_weight`: 다수 라벨 쏠림을 줄이고 소수 라벨을 더 크게 반영합니다.
"""
    ),
    code(
        """
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
                    random_state=42,
                ),
            ),
        ]
    )
"""
    ),
    md(
        """
## 11. 평가 함수 정의

Accuracy는 참고용이고, 라벨 인밸런스가 있으므로 `Macro F1`을 중요하게 봅니다.
"""
    ),
    code(
        """
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


def train_one_target(train_rows: list[dict], test_rows: list[dict], assets: dict, target: str) -> dict:
    x_train = get_texts(train_rows)
    y_train = get_labels(train_rows, target)
    x_test = get_texts(test_rows)
    y_test = get_labels(test_rows, target)

    model = build_pipeline(get_class_weight(assets, target))
    model.fit(x_train, y_train)
    y_pred = model.predict(x_test).tolist()

    return {
        'target': target,
        'train_counts': dict(Counter(y_train).most_common()),
        'test_counts': dict(Counter(y_test).most_common()),
        'pred_counts': dict(Counter(y_pred).most_common()),
        'metrics': evaluate_predictions(y_test, y_pred),
    }
"""
    ),
    md(
        """
## 12. era 모델 학습/평가

먼저 시대 분류 모델만 실행합니다. 결과가 나오면 `accuracy`, `macro_f1`, `weighted_f1`을 확인하세요.
"""
    ),
    code(
        """
results = {
    'base_dir': str(BASE_DIR),
    'common_dir': str(COMMON_DIR),
    'train_rows': len(train_rows),
    'test_rows': len(test_rows),
    'targets': {},
}

results['targets']['era'] = train_one_target(train_rows, test_rows, assets, 'era')
results['targets']['era']['metrics']['accuracy'], results['targets']['era']['metrics']['macro_f1'], results['targets']['era']['metrics']['weighted_f1']
"""
    ),
    md(
        """
## 13. topic 모델 학습/평가

주제 분류 모델을 실행합니다.
"""
    ),
    code(
        """
results['targets']['topic'] = train_one_target(train_rows, test_rows, assets, 'topic')
results['targets']['topic']['metrics']['accuracy'], results['targets']['topic']['metrics']['macro_f1'], results['targets']['topic']['metrics']['weighted_f1']
"""
    ),
    md(
        """
## 14. question_type 모델 학습/평가

문항 유형은 인밸런스가 가장 큰 타깃입니다. Accuracy보다 Macro F1과 라벨별 성능을 더 중요하게 확인해야 합니다.
"""
    ),
    code(
        """
results['targets']['question_type'] = train_one_target(train_rows, test_rows, assets, 'question_type')
results['targets']['question_type']['metrics']['accuracy'], results['targets']['question_type']['metrics']['macro_f1'], results['targets']['question_type']['metrics']['weighted_f1']
"""
    ),
    md(
        """
## 15. 전체 요약 확인

세 모델의 핵심 지표를 한 번에 확인합니다.
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
## 16. Markdown 리포트 생성 함수

결과를 파일로 저장하기 전에 사람이 읽기 좋은 Markdown 리포트로 변환합니다.
"""
    ),
    code(
        """
def build_markdown(results: dict) -> str:
    lines = []
    lines.append('# TF-IDF Baseline Results v1')
    lines.append('')
    lines.append('- Input data: `ml_han_weighted_train_v1.jsonl`, `ml_han_weighted_test_v1.jsonl`')
    lines.append('- Model: `TfidfVectorizer(char_wb 2~5gram) + LogisticRegression`')
    lines.append('- Imbalance handling: train-based `class_weight`')
    lines.append('- Split: train 47~70, test 71~78')
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
## 17. 결과 저장

JSON과 Markdown 결과를 `common/baseline_tfidf_v1` 폴더에 저장합니다.
"""
    ),
    code(
        """
RESULT_DIR.mkdir(parents=True, exist_ok=True)
RESULT_JSON.write_text(json.dumps(results, ensure_ascii=False, indent=2) + '\\n', encoding='utf-8')
RESULT_MD.write_text(build_markdown(results), encoding='utf-8')

print('saved json:', RESULT_JSON)
print('saved md:', RESULT_MD)
"""
    ),
    md(
        """
## 18. 저장된 Markdown 결과 확인

저장된 리포트 앞부분을 출력합니다. 이 파일을 내려받아 공유하면 다음 평가 문서에 반영할 수 있습니다.
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
