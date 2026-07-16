"""RunPod에서 KLUE/RoBERTa 분류 모델을 학습하고 평가하는 스크립트입니다.
평가셋 split과 target을 인자로 받아 era, 원본 topic, 통합 topic_train을 같은 방식으로 평가합니다.
결과는 JSON, Markdown, predictions CSV, loss 그래프, confusion matrix, 저장 모델로 남깁니다.

Run example:
  python /workspace/code/runpod_train_klue_eval_v1.py \
    --train-json /workspace/common/eval_splits_v1/split_era_topic_train_stratified_v1/train.json \
    --test-json /workspace/common/eval_splits_v1/split_era_topic_train_stratified_v1/test.json \
    --target topic_train \
    --output-dir /workspace/output/klue_eval_v1/topic_train_stratified
"""

from __future__ import annotations

import argparse
import csv
import gc
import json
import random
from collections import Counter
from pathlib import Path
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


TARGET_COLUMNS = ("era", "topic", "topic_train")
PREDICTION_COLUMNS = [
    "ml_sequence_index",
    "round_no",
    "question_no",
    "problem_id",
    "target",
    "true_label",
    "pred_label",
    "is_correct",
    "era",
    "topic",
    "topic_train",
    "text",
]


# CLI 인자를 정의합니다.
# RunPod와 로컬 모두에서 경로만 바꿔 실행할 수 있도록 train/test/output을 인자로 받습니다.
# 기본 하이퍼파라미터는 기존 v5 실험값을 기준으로 둡니다.
def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Train and evaluate KLUE/RoBERTa classifier.")
    parser.add_argument("--train-json", type=Path, required=True, help="Train split JSON path.")
    parser.add_argument("--test-json", type=Path, required=True, help="Test split JSON path.")
    parser.add_argument("--target", choices=TARGET_COLUMNS, required=True, help="Prediction target column.")
    parser.add_argument("--output-dir", type=Path, required=True, help="Output directory.")
    parser.add_argument("--model-name", default="klue/roberta-base", help="Hugging Face model name.")
    parser.add_argument("--max-length", type=int, default=512, help="Tokenizer max length.")
    parser.add_argument("--max-epochs", type=int, default=8, help="Max epochs for CV folds.")
    parser.add_argument("--batch-size", type=int, default=8, help="Batch size.")
    parser.add_argument("--learning-rate", type=float, default=2e-5, help="Learning rate.")
    parser.add_argument("--patience", type=int, default=2, help="Early stopping patience.")
    parser.add_argument("--min-delta", type=float, default=0.0, help="Early stopping minimum delta.")
    parser.add_argument("--n-splits", type=int, default=3, help="Number of stratified CV folds.")
    parser.add_argument("--valid-size", type=float, default=0.2, help="Validation ratio when CV is disabled.")
    parser.add_argument("--random-state", type=int, default=42, help="Random seed.")
    parser.add_argument("--no-class-weight", action="store_true", help="Disable class weight.")
    parser.add_argument("--no-cv", action="store_true", help="Disable CV and use one validation split.")
    parser.add_argument("--no-save-model", action="store_true", help="Do not save final model.")
    return parser.parse_args()


# 재현 가능한 실험을 위해 random, numpy, torch seed를 고정합니다.
# GPU 학습에서도 같은 조건을 최대한 맞추기 위해 cuda seed도 함께 설정합니다.
# 완전 동일한 결과를 보장하지는 않지만 split과 학습 흔들림을 줄입니다.
def set_seed(seed: int) -> None:
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)


# JSON split 파일을 읽어 row 목록으로 반환합니다.
# 학습 target, text 컬럼이 비어 있으면 모델 학습이 불가능하므로 먼저 검사합니다.
# 평가 데이터 역시 target 라벨을 포함해야 정답 비교가 가능합니다.
def read_rows(path: Path, target: str) -> list[dict[str, Any]]:
    if not path.exists():
        raise FileNotFoundError(f"file not found: {path}")
    rows = json.loads(path.read_text(encoding="utf-8"))
    if not rows:
        raise ValueError(f"empty json file: {path}")

    missing_rows = [
        idx
        for idx, row in enumerate(rows, start=1)
        if not str(row.get("text") or "").strip() or not str(row.get(target) or "").strip()
    ]
    if missing_rows:
        raise ValueError(f"{path} has rows missing text or {target}: first indexes {missing_rows[:10]}")
    return rows


# Hugging Face tokenizer 출력을 PyTorch Dataset 형태로 감쌉니다.
# label_to_id가 있으면 학습/검증용 labels를 포함하고, 없으면 예측용 입력만 반환합니다.
# text는 전처리 단계에서 만든 통합 입력 문장을 그대로 사용합니다.
class HanExamDataset(Dataset):
    def __init__(
        self,
        rows: list[dict[str, Any]],
        tokenizer: AutoTokenizer,
        *,
        max_length: int,
        target: str | None = None,
        label_to_id: dict[str, int] | None = None,
    ) -> None:
        self.rows = rows
        self.tokenizer = tokenizer
        self.max_length = max_length
        self.target = target
        self.label_to_id = label_to_id

    def __len__(self) -> int:
        return len(self.rows)

    def __getitem__(self, index: int) -> dict[str, torch.Tensor]:
        row = self.rows[index]
        encoded = self.tokenizer(
            str(row.get("text") or ""),
            truncation=True,
            padding="max_length",
            max_length=self.max_length,
            return_tensors="pt",
        )
        item = {key: value.squeeze(0) for key, value in encoded.items()}
        if self.target and self.label_to_id is not None:
            item["labels"] = torch.tensor(self.label_to_id[str(row[self.target])], dtype=torch.long)
        return item


# 학습 데이터에 존재하는 라벨을 정렬해 label_to_id, id_to_label을 만듭니다.
# 저장된 모델 config에도 같은 매핑을 넣어 추론 시 라벨 해석이 꼬이지 않게 합니다.
# test에만 존재하는 라벨이 있으면 평가가 불가능하므로 별도로 검사합니다.
def build_label_maps(train_rows: list[dict[str, Any]], test_rows: list[dict[str, Any]], target: str) -> tuple[dict[str, int], dict[int, str]]:
    train_labels = sorted({str(row[target]) for row in train_rows})
    test_labels = sorted({str(row[target]) for row in test_rows})
    missing_in_train = sorted(set(test_labels) - set(train_labels))
    if missing_in_train:
        raise ValueError(f"test labels missing in train for {target}: {missing_in_train}")

    label_to_id = {label: idx for idx, label in enumerate(train_labels)}
    id_to_label = {idx: label for label, idx in label_to_id.items()}
    return label_to_id, id_to_label


# class weight 텐서를 계산합니다.
# 적은 라벨의 loss 비중을 높여 불균형 데이터에서 소수 클래스를 완전히 무시하지 않도록 합니다.
# --no-class-weight 옵션을 주면 모든 클래스 가중치를 1로 둡니다.
def make_class_weight_tensor(
    rows: list[dict[str, Any]],
    target: str,
    label_to_id: dict[str, int],
    *,
    use_class_weight: bool,
    device: torch.device,
) -> torch.Tensor:
    if not use_class_weight:
        return torch.ones(len(label_to_id), dtype=torch.float, device=device)

    counts = Counter(str(row[target]) for row in rows)
    total = sum(counts.values())
    num_classes = len(label_to_id)
    weights = [0.0] * num_classes
    for label, label_id in label_to_id.items():
        weights[label_id] = total / (num_classes * counts[label])
    return torch.tensor(weights, dtype=torch.float, device=device)


# 예측 결과의 주요 지표를 계산합니다.
# accuracy만 보면 불균형 데이터에서 착시가 생기므로 macro/weighted precision, recall, f1을 함께 저장합니다.
# classification_report는 클래스별 precision/recall/f1 확인용입니다.
def compute_metrics(y_true: list[str], y_pred: list[str]) -> dict[str, Any]:
    return {
        "accuracy": round(float(accuracy_score(y_true, y_pred)), 6),
        "precision_macro": round(float(precision_score(y_true, y_pred, average="macro", zero_division=0)), 6),
        "recall_macro": round(float(recall_score(y_true, y_pred, average="macro", zero_division=0)), 6),
        "macro_f1": round(float(f1_score(y_true, y_pred, average="macro", zero_division=0)), 6),
        "precision_weighted": round(float(precision_score(y_true, y_pred, average="weighted", zero_division=0)), 6),
        "recall_weighted": round(float(recall_score(y_true, y_pred, average="weighted", zero_division=0)), 6),
        "weighted_f1": round(float(f1_score(y_true, y_pred, average="weighted", zero_division=0)), 6),
        "classification_report": classification_report(y_true, y_pred, output_dict=True, zero_division=0),
    }


# 검증 데이터셋에서 loss와 평가 지표를 계산합니다.
# early stopping은 validation loss 기준으로 판단하고, macro f1은 성능 해석용으로 함께 기록합니다.
# 이 함수는 CV fold와 단일 validation split에서 공통 사용됩니다.
def evaluate_loader(
    model: AutoModelForSequenceClassification,
    loader: DataLoader,
    loss_fn: torch.nn.Module,
    id_to_label: dict[int, str],
    device: torch.device,
) -> tuple[float, dict[str, Any]]:
    model.eval()
    total_loss = 0.0
    y_true_ids: list[int] = []
    y_pred_ids: list[int] = []

    with torch.no_grad():
        for batch in loader:
            batch = {key: value.to(device) for key, value in batch.items()}
            labels = batch.pop("labels")
            outputs = model(**batch)
            loss = loss_fn(outputs.logits, labels)
            total_loss += float(loss.item())
            y_true_ids.extend(labels.detach().cpu().tolist())
            y_pred_ids.extend(outputs.logits.argmax(dim=-1).detach().cpu().tolist())

    y_true = [id_to_label[item] for item in y_true_ids]
    y_pred = [id_to_label[item] for item in y_pred_ids]
    return total_loss / max(1, len(loader)), compute_metrics(y_true, y_pred)


# 모델 객체를 정리하고 GPU 메모리를 비웁니다.
# 여러 target을 연속 실행할 때 CUDA 메모리 누수를 줄이기 위한 보조 함수입니다.
# RunPod에서 긴 실험을 돌릴 때 안정성을 조금 높여줍니다.
def cleanup_model(model: AutoModelForSequenceClassification) -> None:
    del model
    gc.collect()
    if torch.cuda.is_available():
        torch.cuda.empty_cache()


# 하나의 fold 또는 validation split을 학습합니다.
# validation loss가 개선되지 않으면 patience 기준으로 early stopping을 수행합니다.
# best epoch, best metrics, epoch별 loss history를 반환합니다.
def train_one_validation_run(
    *,
    run_name: str,
    fit_rows: list[dict[str, Any]],
    valid_rows: list[dict[str, Any]],
    tokenizer: AutoTokenizer,
    target: str,
    label_to_id: dict[str, int],
    id_to_label: dict[int, str],
    args: argparse.Namespace,
    device: torch.device,
) -> dict[str, Any]:
    print(f"\n[{target}] {run_name}")
    fit_dataset = HanExamDataset(fit_rows, tokenizer, max_length=args.max_length, target=target, label_to_id=label_to_id)
    valid_dataset = HanExamDataset(valid_rows, tokenizer, max_length=args.max_length, target=target, label_to_id=label_to_id)
    fit_loader = DataLoader(fit_dataset, batch_size=args.batch_size, shuffle=True)
    valid_loader = DataLoader(valid_dataset, batch_size=args.batch_size, shuffle=False)

    model = AutoModelForSequenceClassification.from_pretrained(
        args.model_name,
        num_labels=len(label_to_id),
        id2label=id_to_label,
        label2id=label_to_id,
    ).to(device)

    class_weights = make_class_weight_tensor(
        fit_rows,
        target,
        label_to_id,
        use_class_weight=not args.no_class_weight,
        device=device,
    )
    loss_fn = torch.nn.CrossEntropyLoss(weight=class_weights)
    optimizer = torch.optim.AdamW(model.parameters(), lr=args.learning_rate)
    total_steps = len(fit_loader) * args.max_epochs
    scheduler = get_linear_schedule_with_warmup(
        optimizer,
        num_warmup_steps=max(1, int(total_steps * 0.1)),
        num_training_steps=total_steps,
    )

    best_val_loss = float("inf")
    best_epoch = 0
    best_metrics: dict[str, Any] | None = None
    patience_count = 0
    history: list[dict[str, Any]] = []

    for epoch in range(args.max_epochs):
        model.train()
        train_loss = 0.0
        for batch in fit_loader:
            batch = {key: value.to(device) for key, value in batch.items()}
            labels = batch.pop("labels")
            optimizer.zero_grad()
            outputs = model(**batch)
            loss = loss_fn(outputs.logits, labels)
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            optimizer.step()
            scheduler.step()
            train_loss += float(loss.item())

        avg_train_loss = train_loss / max(1, len(fit_loader))
        val_loss, val_metrics = evaluate_loader(model, valid_loader, loss_fn, id_to_label, device)
        history.append(
            {
                "epoch": epoch + 1,
                "train_loss": round(avg_train_loss, 6),
                "val_loss": round(float(val_loss), 6),
                "val_macro_f1": val_metrics["macro_f1"],
                "val_weighted_f1": val_metrics["weighted_f1"],
            }
        )
        print(
            f"epoch {epoch + 1}/{args.max_epochs} "
            f"train_loss={avg_train_loss:.4f} val_loss={val_loss:.4f} "
            f"macro_f1={val_metrics['macro_f1']:.4f}"
        )

        if val_loss < best_val_loss - args.min_delta:
            best_val_loss = val_loss
            best_epoch = epoch + 1
            best_metrics = val_metrics
            patience_count = 0
        else:
            patience_count += 1
            print(f"no improvement: {patience_count}/{args.patience}")
            if patience_count >= args.patience:
                print(f"early stopping at epoch {epoch + 1}, best epoch = {best_epoch}")
                break

    cleanup_model(model)
    return {
        "run_name": run_name,
        "fit_size": len(fit_rows),
        "valid_size": len(valid_rows),
        "best_epoch": best_epoch,
        "best_val_loss": round(float(best_val_loss), 6),
        "best_metrics": best_metrics,
        "history": history,
    }


# 학습 데이터 내부에서 CV 또는 단일 validation 평가를 수행합니다.
# 반환된 best epoch 평균을 최종 전체 train 학습 epoch로 사용합니다.
# 모델 자체의 학습 안정성을 확인하기 위한 내부 검증 단계입니다.
def run_internal_validation(
    train_rows: list[dict[str, Any]],
    tokenizer: AutoTokenizer,
    target: str,
    label_to_id: dict[str, int],
    id_to_label: dict[int, str],
    args: argparse.Namespace,
    device: torch.device,
) -> list[dict[str, Any]]:
    labels = [str(row[target]) for row in train_rows]
    runs: list[dict[str, Any]] = []

    if args.no_cv:
        fit_rows, valid_rows = train_test_split(
            train_rows,
            test_size=args.valid_size,
            random_state=args.random_state,
            stratify=labels,
        )
        runs.append(
            train_one_validation_run(
                run_name="valid_split",
                fit_rows=fit_rows,
                valid_rows=valid_rows,
                tokenizer=tokenizer,
                target=target,
                label_to_id=label_to_id,
                id_to_label=id_to_label,
                args=args,
                device=device,
            )
        )
        return runs

    splitter = StratifiedKFold(n_splits=args.n_splits, shuffle=True, random_state=args.random_state)
    for fold_no, (fit_idx, valid_idx) in enumerate(splitter.split(np.zeros(len(labels)), labels), start=1):
        fit_rows = [train_rows[idx] for idx in fit_idx]
        valid_rows = [train_rows[idx] for idx in valid_idx]
        runs.append(
            train_one_validation_run(
                run_name=f"fold_{fold_no}",
                fit_rows=fit_rows,
                valid_rows=valid_rows,
                tokenizer=tokenizer,
                target=target,
                label_to_id=label_to_id,
                id_to_label=id_to_label,
                args=args,
                device=device,
            )
        )
    return runs


# 전체 train 데이터로 최종 모델을 학습하고 test 데이터에 대해 예측합니다.
# 내부 validation에서 얻은 평균 best epoch를 사용해 과학적인 epoch 선택 근거를 남깁니다.
# 옵션에 따라 최종 모델과 tokenizer를 output_dir/saved_model에 저장합니다.
def train_final_model_and_predict(
    train_rows: list[dict[str, Any]],
    test_rows: list[dict[str, Any]],
    tokenizer: AutoTokenizer,
    target: str,
    label_to_id: dict[str, int],
    id_to_label: dict[int, str],
    final_epochs: int,
    args: argparse.Namespace,
    device: torch.device,
) -> dict[str, Any]:
    print(f"\n[{target}] final train epochs = {final_epochs}")
    train_dataset = HanExamDataset(train_rows, tokenizer, max_length=args.max_length, target=target, label_to_id=label_to_id)
    test_dataset = HanExamDataset(test_rows, tokenizer, max_length=args.max_length)
    train_loader = DataLoader(train_dataset, batch_size=args.batch_size, shuffle=True)
    test_loader = DataLoader(test_dataset, batch_size=args.batch_size, shuffle=False)

    model = AutoModelForSequenceClassification.from_pretrained(
        args.model_name,
        num_labels=len(label_to_id),
        id2label=id_to_label,
        label2id=label_to_id,
    ).to(device)

    class_weights = make_class_weight_tensor(
        train_rows,
        target,
        label_to_id,
        use_class_weight=not args.no_class_weight,
        device=device,
    )
    loss_fn = torch.nn.CrossEntropyLoss(weight=class_weights)
    optimizer = torch.optim.AdamW(model.parameters(), lr=args.learning_rate)
    total_steps = len(train_loader) * final_epochs
    scheduler = get_linear_schedule_with_warmup(
        optimizer,
        num_warmup_steps=max(1, int(total_steps * 0.1)),
        num_training_steps=total_steps,
    )

    final_history: list[dict[str, Any]] = []
    for epoch in range(final_epochs):
        model.train()
        train_loss = 0.0
        for batch in train_loader:
            batch = {key: value.to(device) for key, value in batch.items()}
            labels = batch.pop("labels")
            optimizer.zero_grad()
            outputs = model(**batch)
            loss = loss_fn(outputs.logits, labels)
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            optimizer.step()
            scheduler.step()
            train_loss += float(loss.item())

        avg_train_loss = train_loss / max(1, len(train_loader))
        final_history.append({"epoch": epoch + 1, "train_loss": round(avg_train_loss, 6)})
        print(f"final epoch {epoch + 1}/{final_epochs} train_loss={avg_train_loss:.4f}")

    pred_ids: list[int] = []
    model.eval()
    with torch.no_grad():
        for batch in test_loader:
            batch = {key: value.to(device) for key, value in batch.items()}
            outputs = model(**batch)
            pred_ids.extend(outputs.logits.argmax(dim=-1).detach().cpu().tolist())

    y_pred = [id_to_label[pred_id] for pred_id in pred_ids]
    y_true = [str(row[target]) for row in test_rows]
    metrics = compute_metrics(y_true, y_pred)

    if not args.no_save_model:
        model_dir = args.output_dir / "saved_model"
        model.save_pretrained(model_dir)
        tokenizer.save_pretrained(model_dir)

    cleanup_model(model)
    return {
        "final_epochs": final_epochs,
        "final_history": final_history,
        "test_metrics": metrics,
        "confusion_matrix": make_confusion_payload(y_true, y_pred),
        "predictions": build_prediction_rows(test_rows, target, y_true, y_pred),
        "test_counts": dict(Counter(y_true)),
        "pred_counts": dict(Counter(y_pred)),
    }


# confusion matrix를 JSON 저장 가능한 형태로 만듭니다.
# labels 순서를 함께 저장해야 png와 json의 행/열 의미를 정확히 해석할 수 있습니다.
# y_true와 y_pred 중 한쪽에만 있는 라벨도 모두 포함합니다.
def make_confusion_payload(y_true: list[str], y_pred: list[str]) -> dict[str, Any]:
    labels = sorted(set(y_true) | set(y_pred))
    matrix = confusion_matrix(y_true, y_pred, labels=labels)
    return {"labels": labels, "matrix": matrix.astype(int).tolist()}


# 예측 결과를 행 단위 CSV로 저장하기 위한 리스트를 만듭니다.
# 문제 번호, 실제 라벨, 예측 라벨, 정오 여부를 함께 남겨 오분류 분석에 사용합니다.
# 원본 era/topic/topic_train도 같이 저장해 교차 해석이 가능하게 합니다.
def build_prediction_rows(
    test_rows: list[dict[str, Any]],
    target: str,
    y_true: list[str],
    y_pred: list[str],
) -> list[dict[str, Any]]:
    output_rows: list[dict[str, Any]] = []
    for row, true_label, pred_label in zip(test_rows, y_true, y_pred):
        output_rows.append(
            {
                "ml_sequence_index": row.get("ml_sequence_index", ""),
                "round_no": row.get("round_no", ""),
                "question_no": row.get("question_no", ""),
                "problem_id": row.get("problem_id", ""),
                "target": target,
                "true_label": true_label,
                "pred_label": pred_label,
                "is_correct": true_label == pred_label,
                "era": row.get("era", ""),
                "topic": row.get("topic", ""),
                "topic_train": row.get("topic_train", ""),
                "text": row.get("text", ""),
            }
        )
    return output_rows


# 예측 결과 CSV를 저장합니다.
# Excel에서 열기 쉽도록 utf-8-sig 인코딩을 사용합니다.
# 이 파일은 오분류 문항을 사람이 직접 확인할 때 가장 많이 쓰게 됩니다.
def write_predictions_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=PREDICTION_COLUMNS)
        writer.writeheader()
        writer.writerows({field: row.get(field, "") for field in PREDICTION_COLUMNS} for row in rows)


# validation/fold loss와 final train loss를 png로 저장합니다.
# validation loss가 올라가는 시점과 final 학습 epoch 선택 근거를 시각적으로 확인할 수 있습니다.
# 한글 폰트 패키지가 있으면 사용하고, 없으면 기본 폰트로 저장합니다.
def save_loss_plot(path: Path, validation_runs: list[dict[str, Any]], final_history: list[dict[str, Any]], target: str) -> None:
    try:
        import koreanize_matplotlib  # noqa: F401
    except Exception:
        pass

    plt.figure(figsize=(10, 5.5))
    has_validation = False
    for run in validation_runs:
        history = run.get("history", [])
        if not history:
            continue
        has_validation = True
        epochs = [item["epoch"] for item in history]
        plt.plot(epochs, [item["train_loss"] for item in history], marker="o", linestyle="--", alpha=0.5, label=f"{run['run_name']} train")
        plt.plot(epochs, [item["val_loss"] for item in history], marker="o", alpha=0.85, label=f"{run['run_name']} validation")

    if final_history:
        epochs = [item["epoch"] for item in final_history]
        plt.plot(epochs, [item["train_loss"] for item in final_history], marker="s", linewidth=2.5, label="final train")

    plt.title(f"{target} loss")
    plt.xlabel("epoch")
    plt.ylabel("loss")
    if has_validation or final_history:
        plt.legend()
    plt.grid(alpha=0.3)
    plt.tight_layout()
    plt.savefig(path, dpi=150)
    plt.close()


# confusion matrix를 png로 저장합니다.
# 클래스가 많아도 확인 가능하도록 figure 크기를 라벨 수에 따라 조절합니다.
# 원본 topic처럼 10개 라벨인 경우에도 발표/보고서에 바로 붙일 수 있습니다.
def save_confusion_matrix_plot(path: Path, confusion_payload: dict[str, Any], target: str) -> None:
    try:
        import koreanize_matplotlib  # noqa: F401
    except Exception:
        pass

    labels = confusion_payload["labels"]
    matrix = np.array(confusion_payload["matrix"], dtype=int)
    size = max(7, min(14, len(labels) * 0.8))
    plt.figure(figsize=(size, size))
    plt.imshow(matrix, cmap="Blues")
    plt.title(f"{target} confusion matrix")
    plt.xlabel("predicted")
    plt.ylabel("actual")
    plt.xticks(range(len(labels)), labels, rotation=45, ha="right")
    plt.yticks(range(len(labels)), labels)
    plt.colorbar()

    max_value = matrix.max() if matrix.size else 0
    threshold = max_value / 2 if max_value else 0
    for row_idx in range(matrix.shape[0]):
        for col_idx in range(matrix.shape[1]):
            value = matrix[row_idx, col_idx]
            color = "white" if value > threshold else "black"
            plt.text(col_idx, row_idx, str(value), ha="center", va="center", color=color, fontsize=9)

    plt.tight_layout()
    plt.savefig(path, dpi=150)
    plt.close()


# 결과 JSON에 들어갈 값을 파일 저장 가능한 형태로 정리합니다.
# predictions는 CSV로 따로 저장하므로 JSON에는 핵심 요약과 metric 중심으로 남깁니다.
# 전체 결과 파일이 너무 커지지 않도록 예측 행은 제외합니다.
def compact_results(results: dict[str, Any]) -> dict[str, Any]:
    compact = dict(results)
    compact.pop("predictions", None)
    return compact


# Markdown 결과 요약을 생성합니다.
# 평가 항목별 핵심 지표, CV 요약, 클래스별 성능을 한 파일에서 볼 수 있게 합니다.
# 실험 비교표에 옮겨 적기 쉬운 형태로 구성합니다.
def build_markdown(results: dict[str, Any]) -> str:
    metrics = results["test_metrics"]
    cv_summary = results["cv_summary"]
    lines: list[str] = []
    lines.append(f"# KLUE/RoBERTa Eval - {results['target']}")
    lines.append("")
    lines.append("## Experiment")
    lines.append("")
    lines.append(f"- model: `{results['model_name']}`")
    lines.append(f"- target: `{results['target']}`")
    lines.append(f"- train_json: `{results['train_json']}`")
    lines.append(f"- test_json: `{results['test_json']}`")
    lines.append(f"- class_weight: {results['class_weight']}")
    lines.append(f"- max_length: {results['max_length']}")
    lines.append(f"- max_epochs: {results['max_epochs']}")
    lines.append(f"- final_epochs: {results['final_epochs']}")
    lines.append(f"- batch_size: {results['batch_size']}")
    lines.append(f"- learning_rate: {results['learning_rate']}")
    lines.append("")
    lines.append("## Test Metrics")
    lines.append("")
    lines.append("| metric | value |")
    lines.append("|---|---:|")
    for key in [
        "accuracy",
        "precision_macro",
        "recall_macro",
        "macro_f1",
        "precision_weighted",
        "recall_weighted",
        "weighted_f1",
    ]:
        lines.append(f"| {key} | {metrics[key]:.6f} |")
    lines.append("")
    lines.append("## Internal Validation Summary")
    lines.append("")
    lines.append("| metric | value |")
    lines.append("|---|---:|")
    for key, value in cv_summary.items():
        if isinstance(value, float):
            lines.append(f"| {key} | {value:.6f} |")
        else:
            lines.append(f"| {key} | {value} |")
    lines.append("")
    lines.append("## Class Metrics")
    lines.append("")
    lines.append("| label | precision | recall | f1-score | support |")
    lines.append("|---|---:|---:|---:|---:|")
    report = metrics["classification_report"]
    for label in results["labels"]:
        item = report.get(label, {})
        lines.append(
            f"| {label} | {float(item.get('precision', 0)):.4f} | "
            f"{float(item.get('recall', 0)):.4f} | {float(item.get('f1-score', 0)):.4f} | "
            f"{int(item.get('support', 0))} |"
        )
    lines.append("")
    lines.append("## Output Files")
    lines.append("")
    lines.append(f"- results_json: `{results['output_files']['results_json']}`")
    lines.append(f"- predictions_csv: `{results['output_files']['predictions_csv']}`")
    lines.append(f"- loss_png: `{results['output_files']['loss_png']}`")
    lines.append(f"- confusion_matrix_png: `{results['output_files']['confusion_matrix_png']}`")
    if results["output_files"].get("saved_model"):
        lines.append(f"- saved_model: `{results['output_files']['saved_model']}`")
    lines.append("")
    return "\n".join(lines)


# 내부 validation 결과를 요약합니다.
# CV를 사용하면 fold 평균, no-cv면 단일 validation 결과를 같은 구조로 저장합니다.
# 최종 epoch는 best_epoch 평균을 반올림해 결정합니다.
def summarize_validation_runs(validation_runs: list[dict[str, Any]]) -> dict[str, Any]:
    best_epochs = [int(run["best_epoch"]) for run in validation_runs if run.get("best_epoch")]
    best_metrics = [run["best_metrics"] for run in validation_runs if run.get("best_metrics")]
    return {
        "mean_accuracy": round(float(mean(item["accuracy"] for item in best_metrics)), 6),
        "mean_macro_f1": round(float(mean(item["macro_f1"] for item in best_metrics)), 6),
        "mean_weighted_f1": round(float(mean(item["weighted_f1"] for item in best_metrics)), 6),
        "mean_best_epoch": round(float(mean(best_epochs)), 3) if best_epochs else 1.0,
        "final_epochs": max(1, round(mean(best_epochs))) if best_epochs else 1,
    }


# 전체 학습/평가 파이프라인을 실행합니다.
# 하나의 target에 대해 내부 검증, 최종 학습, test 평가, 결과 저장까지 수행합니다.
# RunPod에서는 이 스크립트를 target과 split만 바꿔 여러 번 실행하면 됩니다.
def main() -> None:
    args = parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    set_seed(args.random_state)

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    print("device:", device)
    if device.type == "cuda":
        print("gpu:", torch.cuda.get_device_name(0))

    train_rows = read_rows(args.train_json, args.target)
    test_rows = read_rows(args.test_json, args.target)
    label_to_id, id_to_label = build_label_maps(train_rows, test_rows, args.target)
    tokenizer = AutoTokenizer.from_pretrained(args.model_name)

    validation_runs = run_internal_validation(train_rows, tokenizer, args.target, label_to_id, id_to_label, args, device)
    cv_summary = summarize_validation_runs(validation_runs)
    final_result = train_final_model_and_predict(
        train_rows,
        test_rows,
        tokenizer,
        args.target,
        label_to_id,
        id_to_label,
        int(cv_summary["final_epochs"]),
        args,
        device,
    )

    class_weights = make_class_weight_tensor(
        train_rows,
        args.target,
        label_to_id,
        use_class_weight=not args.no_class_weight,
        device=torch.device("cpu"),
    ).detach().cpu().tolist()

    output_files = {
        "results_json": str(args.output_dir / "results.json"),
        "results_md": str(args.output_dir / "results.md"),
        "predictions_csv": str(args.output_dir / "predictions.csv"),
        "loss_png": str(args.output_dir / "loss.png"),
        "confusion_matrix_png": str(args.output_dir / "confusion_matrix.png"),
    }
    if not args.no_save_model:
        output_files["saved_model"] = str(args.output_dir / "saved_model")

    results: dict[str, Any] = {
        "experiment": "runpod_klue_eval_v1",
        "model_name": args.model_name,
        "target": args.target,
        "train_json": str(args.train_json),
        "test_json": str(args.test_json),
        "train_count": len(train_rows),
        "test_count": len(test_rows),
        "labels": list(label_to_id.keys()),
        "label_to_id": label_to_id,
        "train_counts": dict(Counter(str(row[args.target]) for row in train_rows)),
        "test_counts": final_result["test_counts"],
        "pred_counts": final_result["pred_counts"],
        "class_weight": not args.no_class_weight,
        "class_weights": {label: round(float(class_weights[label_id]), 6) for label, label_id in label_to_id.items()},
        "cross_validation": not args.no_cv,
        "n_splits": args.n_splits if not args.no_cv else 0,
        "max_length": args.max_length,
        "max_epochs": args.max_epochs,
        "final_epochs": final_result["final_epochs"],
        "batch_size": args.batch_size,
        "learning_rate": args.learning_rate,
        "patience": args.patience,
        "min_delta": args.min_delta,
        "random_state": args.random_state,
        "validation_runs": validation_runs,
        "cv_summary": cv_summary,
        "final_history": final_result["final_history"],
        "test_metrics": final_result["test_metrics"],
        "confusion_matrix": final_result["confusion_matrix"],
        "predictions": final_result["predictions"],
        "output_files": output_files,
    }

    write_predictions_csv(args.output_dir / "predictions.csv", results["predictions"])
    save_loss_plot(args.output_dir / "loss.png", validation_runs, final_result["final_history"], args.target)
    save_confusion_matrix_plot(args.output_dir / "confusion_matrix.png", final_result["confusion_matrix"], args.target)
    (args.output_dir / "results.json").write_text(
        json.dumps(compact_results(results), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (args.output_dir / "results.md").write_text(build_markdown(results), encoding="utf-8")

    print("\ncompleted")
    print("results_json:", args.output_dir / "results.json")
    print("results_md:", args.output_dir / "results.md")
    print("predictions_csv:", args.output_dir / "predictions.csv")
    print("loss_png:", args.output_dir / "loss.png")
    print("confusion_matrix_png:", args.output_dir / "confusion_matrix.png")
    if not args.no_save_model:
        print("saved_model:", args.output_dir / "saved_model")


if __name__ == "__main__":
    main()
