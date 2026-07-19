from __future__ import annotations

import json
from pathlib import Path


OUT = Path(__file__).resolve().parent / "runpod_eval_fixed_walk_forward_v2.ipynb"


def lines(text: str) -> list[str]:
    return text.strip("\n").splitlines(keepends=True)


def md(text: str) -> dict:
    return {"cell_type": "markdown", "metadata": {}, "source": lines(text)}


def code(text: str) -> dict:
    return {"cell_type": "code", "execution_count": None, "metadata": {}, "outputs": [], "source": lines(text)}


cells = [
    md(
        """
# RunPod 고정 Holdout + Walk-Forward 평가 v2

이 노트북은 target별 최적 파라미터를 사용해서 최신 회차 검증 방식 2가지를 함께 실행합니다.

1. 고정 holdout: 47~70회차로 한 번 학습한 뒤 71~78회차 전체를 예측합니다.
2. Walk-forward: 47~70 -> 71, 47~71 -> 72, ... 47~77 -> 78 방식으로 순차 예측합니다.

`era`, `topic_train`, `topic`은 각각 별도 모델로 학습합니다.
최종 조합 성능은 `era + topic_train` 기준으로 확인합니다.
"""
    ),
    md("## 1. 설정"),
    code(
        """
from pathlib import Path

# RunPod 기본 경로입니다.
BASE_DIR = Path('/workspace')
COMMON_DIR = BASE_DIR / 'common'
OUTPUT_ROOT = BASE_DIR / 'output' / 'eval_fixed_walk_forward_v2'

# v2 전처리 결과 파일을 찾는 후보 경로입니다.
# 보통 /workspace/common/split_v2/full_features_v2.csv 를 사용합니다.
FEATURE_CSV_CANDIDATES = [
    COMMON_DIR / 'split_v2' / 'full_features_v2.csv',
    COMMON_DIR / 'full_features_v2.csv',
    COMMON_DIR / 'ml_han_features_v2.csv',
]

# 사용할 Hugging Face 모델과 입력 컬럼입니다.
MODEL_NAME = 'klue/roberta-base'

# text = 지문 + 질문 + 키워드
# input_text = 지문 + 질문만
INPUT_TEXT_FIELD = 'text'

# 각각 별도의 모델로 학습/예측할 target입니다.
TARGETS = ['era', 'topic_train', 'topic']

# 고정 holdout 평가 기준입니다.
# 47~70회차로 학습하고 71~78회차를 예측합니다.
BASE_MIN_ROUND = 47
BASE_MAX_ROUND = 70
PREDICT_ROUNDS = list(range(71, 79))

# 두 평가 방식을 실행할지 선택합니다.
# fixed holdout: 47~70회차 학습 -> 71~78회차 전체 예측
# walk-forward: 47~70 -> 71, 47~71 -> 72 ... 방식으로 순차 예측
RUN_FIXED_HOLDOUT = True
RUN_WALK_FORWARD = True

# 재현성을 위한 random seed입니다.
RANDOM_STATE = 42

# 모델 파일 저장 여부입니다.
# False면 예측 결과와 평가 지표만 저장합니다.
# True면 각 평가 단계에서 학습된 모델 가중치도 저장합니다.
SAVE_MODEL_FIXED = False
SAVE_MODEL_WALK_FORWARD = False

# grid search 결과로 확정한 target별 최적 파라미터입니다.
# era, topic_train, topic은 서로 다른 분류 문제라 파라미터를 따로 적용합니다.
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
print('RUN_FIXED_HOLDOUT =', RUN_FIXED_HOLDOUT)
print('RUN_WALK_FORWARD =', RUN_WALK_FORWARD)
print('PREDICT_ROUNDS =', PREDICT_ROUNDS)
print('TARGET_CONFIG =')
for target, config in TARGET_CONFIG.items():
    print(target, config)
"""
    ),
    md("## 2. 라이브러리 설치"),
    code("!pip install -q transformers accelerate scikit-learn matplotlib koreanize-matplotlib pandas tabulate"),
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
df = df.sort_values(['round_no', 'question_no', 'problem_id']).reset_index(drop=True)

print('FEATURE_CSV =', FEATURE_CSV)
print('rows =', len(df))
print('round range =', int(df['round_no'].min()), int(df['round_no'].max()))
display(df.groupby('round_no').size().rename('count').reset_index())
display(df[TARGETS].nunique().rename('nunique').reset_index().rename(columns={'index': 'target'}))
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


def compute_metrics(true_labels: list[str], pred_labels: list[str]) -> dict:
    return {
        'accuracy': float(accuracy_score(true_labels, pred_labels)),
        'precision_macro': float(precision_score(true_labels, pred_labels, average='macro', zero_division=0)),
        'recall_macro': float(recall_score(true_labels, pred_labels, average='macro', zero_division=0)),
        'f1_macro': float(f1_score(true_labels, pred_labels, average='macro', zero_division=0)),
        'f1_weighted': float(f1_score(true_labels, pred_labels, average='weighted', zero_division=0)),
        'classification_report': classification_report(true_labels, pred_labels, output_dict=True, zero_division=0),
    }


def base_prediction_columns() -> list[str]:
    preferred = ['ml_sequence_index', 'round_no', 'question_no', 'problem_id', INPUT_TEXT_FIELD, *TARGETS]
    return [column for column in preferred if column in df.columns]
"""
    ),
    md("## 6. Target별 학습/예측 함수"),
    code(
        """
def train_predict_target(
    train_df: pd.DataFrame,
    test_df: pd.DataFrame,
    target: str,
    output_dir: Path,
    run_label: str,
    save_model: bool,
) -> tuple[dict, pd.DataFrame]:
    set_seed(RANDOM_STATE)
    output_dir.mkdir(parents=True, exist_ok=True)
    config = TARGET_CONFIG[target]

    train_labels = train_df[target].astype(str).tolist()
    test_labels = test_df[target].astype(str).tolist()
    label2id, id2label = make_label_maps(train_labels + test_labels)
    unseen_labels = sorted(set(test_labels) - set(train_labels))
    if unseen_labels:
        print(f'WARNING: {run_label}/{target} train에 없던 test 라벨이 있습니다: {unseen_labels}')

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
        print(f'{run_label}/{target} epoch {epoch}/{config["max_epochs"]}: train_loss={avg_train_loss:.4f}')

    test_loss, pred_ids, true_ids = evaluate_model(model, test_loader, loss_fn)
    pred_labels = [id2label[pred_id] for pred_id in pred_ids]
    true_labels = [id2label[true_id] for true_id in true_ids]
    metrics = compute_metrics(true_labels, pred_labels)

    pred_df = test_df[base_prediction_columns()].copy()
    pred_df[f'true_{target}'] = true_labels
    pred_df[f'pred_{target}'] = pred_labels
    pred_df[f'is_correct_{target}'] = pred_df[f'true_{target}'] == pred_df[f'pred_{target}']
    pred_df.to_csv(output_dir / f'{target}_predictions.csv', index=False, encoding='utf-8-sig')

    labels_order = sorted(set(true_labels) | set(pred_labels))
    cm = confusion_matrix(true_labels, pred_labels, labels=labels_order)
    plt.figure(figsize=(max(7, len(labels_order) * 0.7), max(5, len(labels_order) * 0.55)))
    plt.imshow(cm, cmap='Blues')
    plt.title(f'{run_label} {target} confusion matrix')
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
        'run_label': run_label,
        'target': target,
        'input_text_field': INPUT_TEXT_FIELD,
        'train_size': int(len(train_df)),
        'test_size': int(len(test_df)),
        'train_round_min': int(train_df['round_no'].min()),
        'train_round_max': int(train_df['round_no'].max()),
        'test_rounds': sorted(int(v) for v in test_df['round_no'].unique()),
        'params': {
            'model_name': MODEL_NAME,
            **config,
        },
        'test_loss': float(test_loss),
        'test_metrics': metrics,
        'history': history,
        'label_counts_train': dict(Counter(train_labels)),
        'label_counts_test': dict(Counter(test_labels)),
        'label_counts_pred': dict(Counter(pred_labels)),
        'unseen_test_labels': unseen_labels,
    }
    (output_dir / f'{target}_results.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')

    if save_model:
        model.save_pretrained(output_dir / f'{target}_saved_model')
        tokenizer.save_pretrained(output_dir / f'{target}_saved_model')

    del model
    gc.collect()
    if torch.cuda.is_available():
        torch.cuda.empty_cache()

    return result, pred_df
"""
    ),
    md("## 7. Target별 예측 결과 결합"),
    code(
        """
def combine_predictions(test_df: pd.DataFrame, pred_parts: dict[str, pd.DataFrame], output_dir: Path, run_label: str) -> tuple[pd.DataFrame, dict]:
    combined = test_df[base_prediction_columns()].copy()
    for target in TARGETS:
        combined = combined.merge(
            pred_parts[target][['problem_id', f'true_{target}', f'pred_{target}', f'is_correct_{target}']],
            on='problem_id',
            how='left',
        )

    combined['true_era_topic_train'] = combined['true_era'] + ' + ' + combined['true_topic_train']
    combined['pred_era_topic_train'] = combined['pred_era'] + ' + ' + combined['pred_topic_train']
    combined['true_era_topic_train_topic'] = combined['true_era_topic_train'] + ' + ' + combined['true_topic']
    combined['pred_era_topic_train_topic'] = combined['pred_era_topic_train'] + ' + ' + combined['pred_topic']
    combined['is_correct_era_topic_train'] = combined['true_era_topic_train'] == combined['pred_era_topic_train']
    combined['is_correct_all_three'] = combined['true_era_topic_train_topic'] == combined['pred_era_topic_train_topic']

    combined.to_csv(output_dir / f'{run_label}_combined_predictions.csv', index=False, encoding='utf-8-sig')

    pred_combo_trend = (
        combined
        .groupby(['round_no', 'pred_era_topic_train'])
        .size()
        .rename('count')
        .reset_index()
    )
    pred_combo_trend['ratio'] = pred_combo_trend.groupby('round_no')['count'].transform(lambda s: s / s.sum())
    pred_combo_trend = pred_combo_trend.sort_values(['round_no', 'count'], ascending=[True, False])
    pred_combo_trend.to_csv(output_dir / f'{run_label}_pred_era_topic_train_trend_by_round.csv', index=False, encoding='utf-8-sig')

    actual_combo_trend = (
        combined
        .groupby(['round_no', 'true_era_topic_train'])
        .size()
        .rename('count')
        .reset_index()
    )
    actual_combo_trend['ratio'] = actual_combo_trend.groupby('round_no')['count'].transform(lambda s: s / s.sum())
    actual_combo_trend = actual_combo_trend.sort_values(['round_no', 'count'], ascending=[True, False])
    actual_combo_trend.to_csv(output_dir / f'{run_label}_actual_era_topic_train_trend_by_round.csv', index=False, encoding='utf-8-sig')

    combo_metrics = {
        'run_label': run_label,
        'test_size': int(len(combined)),
        'round_min': int(combined['round_no'].min()),
        'round_max': int(combined['round_no'].max()),
        'era_topic_train_accuracy': float(combined['is_correct_era_topic_train'].mean()),
        'all_three_accuracy': float(combined['is_correct_all_three'].mean()),
    }
    return combined, combo_metrics
"""
    ),
    md("## 8. 고정 Holdout 평가: 47~70 학습, 71~78 예측"),
    code(
        """
# 고정 holdout 평가:
# 47~70회차 데이터만 사용해 era/topic_train/topic 모델을 각각 한 번씩 학습합니다.
# 학습된 3개 모델로 71~78회차 전체를 예측합니다.
# 70회차까지만 알고 있을 때 이후 최신 회차를 얼마나 잘 맞히는지 확인하는 평가입니다.
fixed_target_rows = []
fixed_combo_rows = []
fixed_combined_df = None

if RUN_FIXED_HOLDOUT:
    fixed_dir = OUTPUT_ROOT / 'fixed_47_70_to_71_78'
    fixed_dir.mkdir(parents=True, exist_ok=True)

    train_df = df[(df['round_no'] >= BASE_MIN_ROUND) & (df['round_no'] <= BASE_MAX_ROUND)].copy()
    test_df = df[df['round_no'].isin(PREDICT_ROUNDS)].copy()
    print(f'고정 holdout: train {train_df.round_no.min()}~{train_df.round_no.max()} ({len(train_df)} rows), test {test_df.round_no.min()}~{test_df.round_no.max()} ({len(test_df)} rows)')

    pred_parts = {}
    for target in TARGETS:
        target_dir = fixed_dir / target
        result, pred_df = train_predict_target(train_df, test_df, target, target_dir, 'fixed_47_70_to_71_78', SAVE_MODEL_FIXED)
        pred_parts[target] = pred_df
        fixed_target_rows.append({
            'eval_mode': 'fixed_holdout',
            'target': target,
            'train_round_min': int(train_df['round_no'].min()),
            'train_round_max': int(train_df['round_no'].max()),
            'test_round_min': int(test_df['round_no'].min()),
            'test_round_max': int(test_df['round_no'].max()),
            'train_size': len(train_df),
            'test_size': len(test_df),
            'accuracy': result['test_metrics']['accuracy'],
            'f1_macro': result['test_metrics']['f1_macro'],
            'f1_weighted': result['test_metrics']['f1_weighted'],
            **{f'param_{k}': v for k, v in TARGET_CONFIG[target].items()},
        })

    fixed_combined_df, combo_metrics = combine_predictions(test_df, pred_parts, fixed_dir, 'fixed_47_70_to_71_78')
    fixed_combo_rows.append({'eval_mode': 'fixed_holdout', **combo_metrics})

fixed_target_df = pd.DataFrame(fixed_target_rows)
fixed_combo_df = pd.DataFrame(fixed_combo_rows)
if RUN_FIXED_HOLDOUT:
    fixed_target_df.to_csv(OUTPUT_ROOT / 'fixed_target_metrics.csv', index=False, encoding='utf-8-sig')
    fixed_combo_df.to_csv(OUTPUT_ROOT / 'fixed_combo_metrics.csv', index=False, encoding='utf-8-sig')
    display(fixed_target_df)
    display(fixed_combo_df)
"""
    ),
    md("## 9. Walk-Forward 평가: 회차별 누적 학습"),
    code(
        """
# walk-forward 평가:
# 실제 운영 상황처럼 회차가 하나씩 공개된다고 가정합니다.
# 71회차 예측은 47~70회차로 학습하고,
# 72회차 예측은 47~71회차로 학습합니다.
# 이런 식으로 78회차까지 순차적으로 평가합니다.
walk_target_rows = []
walk_combo_rows = []
walk_combined_parts = []

if RUN_WALK_FORWARD:
    walk_root = OUTPUT_ROOT / 'walk_forward_47_prev_to_next'
    walk_root.mkdir(parents=True, exist_ok=True)

    for target_round in PREDICT_ROUNDS:
        train_df = df[(df['round_no'] >= BASE_MIN_ROUND) & (df['round_no'] < target_round)].copy()
        test_df = df[df['round_no'] == target_round].copy()
        round_dir = walk_root / f'round_{target_round:02d}'
        round_dir.mkdir(parents=True, exist_ok=True)

        print('\\n' + '=' * 90)
        print(f'walk-forward {target_round}회차: train {train_df.round_no.min()}~{train_df.round_no.max()} ({len(train_df)} rows), test {len(test_df)} rows')

        pred_parts = {}
        for target in TARGETS:
            target_dir = round_dir / target
            result, pred_df = train_predict_target(train_df, test_df, target, target_dir, f'walk_forward_round_{target_round:02d}', SAVE_MODEL_WALK_FORWARD)
            pred_parts[target] = pred_df
            walk_target_rows.append({
                'eval_mode': 'walk_forward',
                'target_round': target_round,
                'target': target,
                'train_round_min': int(train_df['round_no'].min()),
                'train_round_max': int(train_df['round_no'].max()),
                'test_round_min': target_round,
                'test_round_max': target_round,
                'train_size': len(train_df),
                'test_size': len(test_df),
                'accuracy': result['test_metrics']['accuracy'],
                'f1_macro': result['test_metrics']['f1_macro'],
                'f1_weighted': result['test_metrics']['f1_weighted'],
                **{f'param_{k}': v for k, v in TARGET_CONFIG[target].items()},
            })

        combined, combo_metrics = combine_predictions(test_df, pred_parts, round_dir, f'walk_forward_round_{target_round:02d}')
        walk_combined_parts.append(combined)
        walk_combo_rows.append({'eval_mode': 'walk_forward', 'target_round': target_round, **combo_metrics})

walk_target_df = pd.DataFrame(walk_target_rows)
walk_combo_df = pd.DataFrame(walk_combo_rows)
walk_combined_df = pd.concat(walk_combined_parts, ignore_index=True) if walk_combined_parts else pd.DataFrame()

if RUN_WALK_FORWARD:
    walk_target_df.to_csv(OUTPUT_ROOT / 'walk_forward_target_metrics_by_round.csv', index=False, encoding='utf-8-sig')
    walk_combo_df.to_csv(OUTPUT_ROOT / 'walk_forward_combo_metrics_by_round.csv', index=False, encoding='utf-8-sig')
    walk_combined_df.to_csv(OUTPUT_ROOT / 'walk_forward_all_rounds_combined_predictions.csv', index=False, encoding='utf-8-sig')
    display(walk_target_df)
    display(walk_combo_df)
"""
    ),
    md("## 10. 두 평가 방식 비교"),
    code(
        """
comparison_rows = []

if RUN_FIXED_HOLDOUT and not fixed_target_df.empty:
    for row in fixed_target_df.to_dict('records'):
        comparison_rows.append(row)

if RUN_WALK_FORWARD and not walk_target_df.empty:
    for target, group in walk_target_df.groupby('target'):
        comparison_rows.append({
            'eval_mode': 'walk_forward_mean',
            'target': target,
            'train_round_min': int(group['train_round_min'].min()),
            'train_round_max': int(group['train_round_max'].max()),
            'test_round_min': int(group['test_round_min'].min()),
            'test_round_max': int(group['test_round_max'].max()),
            'train_size': None,
            'test_size': int(group['test_size'].sum()),
            'accuracy': float(np.average(group['accuracy'], weights=group['test_size'])),
            'f1_macro': float(np.average(group['f1_macro'], weights=group['test_size'])),
            'f1_weighted': float(np.average(group['f1_weighted'], weights=group['test_size'])),
        })

comparison_df = pd.DataFrame(comparison_rows)
comparison_df.to_csv(OUTPUT_ROOT / 'fixed_vs_walk_forward_target_comparison.csv', index=False, encoding='utf-8-sig')

combo_comparison_rows = []
if RUN_FIXED_HOLDOUT and not fixed_combo_df.empty:
    combo_comparison_rows.extend(fixed_combo_df.to_dict('records'))
if RUN_WALK_FORWARD and not walk_combo_df.empty:
    combo_comparison_rows.append({
        'eval_mode': 'walk_forward_mean',
        'test_size': int(walk_combo_df['test_size'].sum()),
        'round_min': int(walk_combo_df['round_min'].min()),
        'round_max': int(walk_combo_df['round_max'].max()),
        'era_topic_train_accuracy': float(np.average(walk_combo_df['era_topic_train_accuracy'], weights=walk_combo_df['test_size'])),
        'all_three_accuracy': float(np.average(walk_combo_df['all_three_accuracy'], weights=walk_combo_df['test_size'])),
    })

combo_comparison_df = pd.DataFrame(combo_comparison_rows)
combo_comparison_df.to_csv(OUTPUT_ROOT / 'fixed_vs_walk_forward_combo_comparison.csv', index=False, encoding='utf-8-sig')

display(comparison_df)
display(combo_comparison_df)
"""
    ),
    md("## 11. Markdown 리포트 저장"),
    code(
        """
target_config_text = json.dumps(TARGET_CONFIG, ensure_ascii=False, indent=2)
target_table = comparison_df.to_markdown(index=False) if not comparison_df.empty else '(no target metrics)'
combo_table = combo_comparison_df.to_markdown(index=False) if not combo_comparison_df.empty else '(no combo metrics)'

fixed_table = fixed_target_df.to_markdown(index=False) if RUN_FIXED_HOLDOUT and not fixed_target_df.empty else '(고정 holdout 평가를 실행하지 않았습니다.)'
walk_table = walk_target_df.to_markdown(index=False) if RUN_WALK_FORWARD and not walk_target_df.empty else '(walk-forward 평가를 실행하지 않았습니다.)'

md_text = f'''# 고정 Holdout + Walk-Forward 평가 v2

## 설정

- feature_csv: {FEATURE_CSV}
- 입력 컬럼: {INPUT_TEXT_FIELD}
- 모델: {MODEL_NAME}
- 고정 holdout 학습 회차: {BASE_MIN_ROUND}~{BASE_MAX_ROUND}
- 예측 회차: {PREDICT_ROUNDS}
- 고정 holdout 실행 여부: {RUN_FIXED_HOLDOUT}
- walk-forward 실행 여부: {RUN_WALK_FORWARD}

```json
{target_config_text}
```

## Target별 평가 방식 비교

{target_table}

## era + topic_train 조합 평가 방식 비교

{combo_table}

## 고정 Holdout Target별 성능

{fixed_table}

## Walk-Forward 회차별 Target 성능

{walk_table}
'''

report_path = OUTPUT_ROOT / 'eval_fixed_walk_forward_v2_report.md'
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
