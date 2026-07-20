from __future__ import annotations

import argparse
import csv
import json
import random
from pathlib import Path
from typing import Any

import numpy as np
import torch
from sklearn.metrics import accuracy_score, classification_report, confusion_matrix, f1_score, precision_score, recall_score
from torch.utils.data import DataLoader, Dataset
from transformers import AutoModelForSequenceClassification, AutoTokenizer, get_linear_schedule_with_warmup


LABEL_NAMES = {
    0: "ABNORMAL",
    1: "NORMAL",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Train question review binary classifier on RunPod.")
    parser.add_argument("--train-json", type=Path, default=Path("train.json"))
    parser.add_argument("--valid-json", type=Path, default=Path("valid.json"))
    parser.add_argument("--test-json", type=Path, default=Path("test.json"))
    parser.add_argument("--output-dir", type=Path, default=Path("output"))
    parser.add_argument("--model-name", default="klue/roberta-base")
    parser.add_argument("--max-length", type=int, default=512)
    parser.add_argument("--epochs", type=int, default=5)
    parser.add_argument("--batch-size", type=int, default=8)
    parser.add_argument("--learning-rate", type=float, default=2e-5)
    parser.add_argument("--weight-decay", type=float, default=0.01)
    parser.add_argument("--warmup-ratio", type=float, default=0.1)
    parser.add_argument("--patience", type=int, default=2)
    parser.add_argument("--min-delta", type=float, default=0.0)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--min-abnormal-recall", type=float, default=0.9)
    parser.add_argument("--num-workers", type=int, default=0)
    return parser.parse_args()


def set_seed(seed: int) -> None:
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)


def read_json(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        raise FileNotFoundError(f"file not found: {path}")
    rows = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(rows, list) or not rows:
        raise ValueError(f"expected non-empty list: {path}")
    return rows


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def build_model_text(row: dict[str, Any]) -> str:
    choices = row.get("choices") or []
    lines = [
        f"지문: {row.get('passage', '')}",
        f"질문: {row.get('question', '')}",
    ]
    lines.extend(f"선지{idx}: {choice}" for idx, choice in enumerate(choices, start=1))
    lines.extend(
        [
            f"정답: {row.get('answer', '')}",
            f"목표배점: {row.get('target_score', '')}",
        ]
    )
    return "\n".join(lines)


def validate_rows(rows: list[dict[str, Any]], split_name: str) -> None:
    for idx, row in enumerate(rows, start=1):
        label = row.get("label")
        if label not in (0, 1):
            raise ValueError(f"{split_name}[{idx}] invalid label: {label}")
        if not row.get("question"):
            raise ValueError(f"{split_name}[{idx}] missing question")
        if not isinstance(row.get("choices"), list) or not row["choices"]:
            raise ValueError(f"{split_name}[{idx}] missing choices")


class ReviewDataset(Dataset):
    def __init__(self, rows: list[dict[str, Any]], tokenizer: AutoTokenizer, max_length: int) -> None:
        self.rows = rows
        self.tokenizer = tokenizer
        self.max_length = max_length

    def __len__(self) -> int:
        return len(self.rows)

    def __getitem__(self, index: int) -> dict[str, torch.Tensor]:
        row = self.rows[index]
        encoded = self.tokenizer(
            build_model_text(row),
            truncation=True,
            padding="max_length",
            max_length=self.max_length,
            return_tensors="pt",
        )
        item = {key: value.squeeze(0) for key, value in encoded.items()}
        item["labels"] = torch.tensor(int(row["label"]), dtype=torch.long)
        return item


def make_loader(
    rows: list[dict[str, Any]],
    tokenizer: AutoTokenizer,
    max_length: int,
    batch_size: int,
    shuffle: bool,
    num_workers: int,
) -> DataLoader:
    dataset = ReviewDataset(rows, tokenizer, max_length)
    return DataLoader(dataset, batch_size=batch_size, shuffle=shuffle, num_workers=num_workers)


def class_weights(rows: list[dict[str, Any]], device: torch.device) -> torch.Tensor:
    counts = {0: 0, 1: 0}
    for row in rows:
        counts[int(row["label"])] += 1
    total = counts[0] + counts[1]
    weights = [
        total / (2 * max(1, counts[0])),
        total / (2 * max(1, counts[1])),
    ]
    return torch.tensor(weights, dtype=torch.float, device=device)


def predict_loader(
    model: AutoModelForSequenceClassification,
    loader: DataLoader,
    device: torch.device,
) -> tuple[list[int], list[int], list[float]]:
    model.eval()
    y_true: list[int] = []
    y_pred: list[int] = []
    abnormal_probs: list[float] = []

    with torch.no_grad():
        for batch in loader:
            batch = {key: value.to(device) for key, value in batch.items()}
            labels = batch.pop("labels")
            outputs = model(**batch)
            probs = torch.softmax(outputs.logits, dim=-1)
            y_true.extend(labels.detach().cpu().tolist())
            y_pred.extend(outputs.logits.argmax(dim=-1).detach().cpu().tolist())
            abnormal_probs.extend(probs[:, 0].detach().cpu().tolist())

    return y_true, y_pred, abnormal_probs


def metrics(y_true: list[int], y_pred: list[int]) -> dict[str, Any]:
    labels = [0, 1]
    return {
        "accuracy": round(float(accuracy_score(y_true, y_pred)), 6),
        "macro_f1": round(float(f1_score(y_true, y_pred, average="macro", zero_division=0)), 6),
        "abnormal_precision": round(float(precision_score(y_true, y_pred, labels=labels, pos_label=0, average="binary", zero_division=0)), 6),
        "abnormal_recall": round(float(recall_score(y_true, y_pred, labels=labels, pos_label=0, average="binary", zero_division=0)), 6),
        "normal_precision": round(float(precision_score(y_true, y_pred, labels=labels, pos_label=1, average="binary", zero_division=0)), 6),
        "normal_recall": round(float(recall_score(y_true, y_pred, labels=labels, pos_label=1, average="binary", zero_division=0)), 6),
        "classification_report": classification_report(
            y_true,
            y_pred,
            labels=labels,
            target_names=[LABEL_NAMES[0], LABEL_NAMES[1]],
            output_dict=True,
            zero_division=0,
        ),
        "confusion_matrix": {
            "labels": [LABEL_NAMES[0], LABEL_NAMES[1]],
            "matrix": confusion_matrix(y_true, y_pred, labels=labels).astype(int).tolist(),
        },
    }


def threshold_predictions(abnormal_probs: list[float], threshold: float) -> list[int]:
    return [0 if prob >= threshold else 1 for prob in abnormal_probs]


def choose_threshold(
    y_true: list[int],
    abnormal_probs: list[float],
    min_abnormal_recall: float,
) -> dict[str, Any]:
    candidates = [round(i / 100, 2) for i in range(5, 96)]
    scored: list[dict[str, Any]] = []
    for threshold in candidates:
        y_pred = threshold_predictions(abnormal_probs, threshold)
        m = metrics(y_true, y_pred)
        scored.append(
            {
                "threshold": threshold,
                "macro_f1": m["macro_f1"],
                "abnormal_recall": m["abnormal_recall"],
                "abnormal_precision": m["abnormal_precision"],
                "normal_recall": m["normal_recall"],
            }
        )

    enough_recall = [item for item in scored if item["abnormal_recall"] >= min_abnormal_recall]
    if enough_recall:
        best = max(enough_recall, key=lambda item: (item["macro_f1"], item["abnormal_precision"], item["threshold"]))
    else:
        best = max(scored, key=lambda item: (item["abnormal_recall"], item["macro_f1"], item["abnormal_precision"]))
    return best


def train_epoch(
    model: AutoModelForSequenceClassification,
    loader: DataLoader,
    optimizer: torch.optim.Optimizer,
    scheduler: torch.optim.lr_scheduler.LambdaLR,
    loss_fn: torch.nn.Module,
    device: torch.device,
) -> float:
    model.train()
    total_loss = 0.0

    for batch in loader:
        batch = {key: value.to(device) for key, value in batch.items()}
        labels = batch.pop("labels")

        optimizer.zero_grad(set_to_none=True)
        outputs = model(**batch)
        loss = loss_fn(outputs.logits, labels)
        loss.backward()
        torch.nn.utils.clip_grad_norm_(model.parameters(), max_norm=1.0)
        optimizer.step()
        scheduler.step()

        total_loss += float(loss.item())

    return total_loss / max(1, len(loader))


def evaluate_loss(
    model: AutoModelForSequenceClassification,
    loader: DataLoader,
    loss_fn: torch.nn.Module,
    device: torch.device,
) -> float:
    model.eval()
    total_loss = 0.0

    with torch.no_grad():
        for batch in loader:
            batch = {key: value.to(device) for key, value in batch.items()}
            labels = batch.pop("labels")
            outputs = model(**batch)
            loss = loss_fn(outputs.logits, labels)
            total_loss += float(loss.item())

    return total_loss / max(1, len(loader))


def write_predictions(path: Path, rows: list[dict[str, Any]], y_pred: list[int], abnormal_probs: list[float]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "id",
                "source_id",
                "true_label",
                "pred_label",
                "abnormal_prob",
                "error_types",
                "review_memo",
                "question",
            ],
        )
        writer.writeheader()
        for row, pred, prob in zip(rows, y_pred, abnormal_probs):
            writer.writerow(
                {
                    "id": row.get("id"),
                    "source_id": row.get("source_id"),
                    "true_label": row.get("label"),
                    "pred_label": pred,
                    "abnormal_prob": round(float(prob), 6),
                    "error_types": "|".join(row.get("error_types") or []),
                    "review_memo": row.get("review_memo", ""),
                    "question": row.get("question", ""),
                }
            )


def main() -> None:
    args = parse_args()
    set_seed(args.seed)
    args.output_dir.mkdir(parents=True, exist_ok=True)

    train_rows = read_json(args.train_json)
    valid_rows = read_json(args.valid_json)
    test_rows = read_json(args.test_json)
    validate_rows(train_rows, "train")
    validate_rows(valid_rows, "valid")
    validate_rows(test_rows, "test")

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    print("device:", device)
    if device.type == "cuda":
        print("gpu:", torch.cuda.get_device_name(0))

    tokenizer = AutoTokenizer.from_pretrained(args.model_name)
    model = AutoModelForSequenceClassification.from_pretrained(
        args.model_name,
        num_labels=2,
        id2label={0: LABEL_NAMES[0], 1: LABEL_NAMES[1]},
        label2id={LABEL_NAMES[0]: 0, LABEL_NAMES[1]: 1},
    )
    model.to(device)

    train_loader = make_loader(train_rows, tokenizer, args.max_length, args.batch_size, True, args.num_workers)
    valid_loader = make_loader(valid_rows, tokenizer, args.max_length, args.batch_size, False, args.num_workers)
    test_loader = make_loader(test_rows, tokenizer, args.max_length, args.batch_size, False, args.num_workers)

    optimizer = torch.optim.AdamW(model.parameters(), lr=args.learning_rate, weight_decay=args.weight_decay)
    total_steps = len(train_loader) * args.epochs
    warmup_steps = int(total_steps * args.warmup_ratio)
    scheduler = get_linear_schedule_with_warmup(optimizer, num_warmup_steps=warmup_steps, num_training_steps=total_steps)
    loss_fn = torch.nn.CrossEntropyLoss(weight=class_weights(train_rows, device))

    best_valid_loss = float("inf")
    best_state = None
    patience_count = 0
    history: list[dict[str, Any]] = []

    for epoch in range(1, args.epochs + 1):
        train_loss = train_epoch(model, train_loader, optimizer, scheduler, loss_fn, device)
        valid_loss = evaluate_loss(model, valid_loader, loss_fn, device)
        valid_true, valid_pred, _ = predict_loader(model, valid_loader, device)
        valid_metrics = metrics(valid_true, valid_pred)

        row = {
            "epoch": epoch,
            "train_loss": round(train_loss, 6),
            "valid_loss": round(valid_loss, 6),
            "valid_macro_f1": valid_metrics["macro_f1"],
            "valid_abnormal_recall": valid_metrics["abnormal_recall"],
        }
        history.append(row)
        print(row)

        if valid_loss < best_valid_loss - args.min_delta:
            best_valid_loss = valid_loss
            best_state = {key: value.detach().cpu().clone() for key, value in model.state_dict().items()}
            patience_count = 0
        else:
            patience_count += 1
            if patience_count >= args.patience:
                print(f"early stopping at epoch {epoch}")
                break

    if best_state is not None:
        model.load_state_dict(best_state)

    valid_true, valid_argmax_pred, valid_abnormal_probs = predict_loader(model, valid_loader, device)
    threshold_info = choose_threshold(valid_true, valid_abnormal_probs, args.min_abnormal_recall)
    threshold = float(threshold_info["threshold"])
    valid_threshold_pred = threshold_predictions(valid_abnormal_probs, threshold)

    test_true, test_argmax_pred, test_abnormal_probs = predict_loader(model, test_loader, device)
    test_threshold_pred = threshold_predictions(test_abnormal_probs, threshold)

    model_dir = args.output_dir / "model"
    model.save_pretrained(model_dir)
    tokenizer.save_pretrained(model_dir)

    result = {
        "model_name": args.model_name,
        "label_names": LABEL_NAMES,
        "train_count": len(train_rows),
        "valid_count": len(valid_rows),
        "test_count": len(test_rows),
        "best_valid_loss": round(float(best_valid_loss), 6),
        "history": history,
        "threshold": threshold_info,
        "valid_argmax_metrics": metrics(valid_true, valid_argmax_pred),
        "valid_threshold_metrics": metrics(valid_true, valid_threshold_pred),
        "test_argmax_metrics": metrics(test_true, test_argmax_pred),
        "test_threshold_metrics": metrics(test_true, test_threshold_pred),
    }
    write_json(args.output_dir / "results.json", result)
    write_predictions(args.output_dir / "valid_predictions.csv", valid_rows, valid_threshold_pred, valid_abnormal_probs)
    write_predictions(args.output_dir / "test_predictions.csv", test_rows, test_threshold_pred, test_abnormal_probs)

    print(json.dumps(result, ensure_ascii=False, indent=2))
    print("saved:", args.output_dir)


if __name__ == "__main__":
    main()
