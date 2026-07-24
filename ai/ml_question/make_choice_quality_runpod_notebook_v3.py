from __future__ import annotations

import json
from pathlib import Path


BASE = Path(__file__).resolve().parent / "train_choice_quality_runpod_v2.ipynb"
OUT = Path(__file__).resolve().parent / "train_choice_quality_runpod_v3.ipynb"


CELL8_NEW = r'''
valid_labels, valid_probs, valid_loss = predict_loader(valid_loader, desc="final valid")
test_labels, test_probs, test_loss = predict_loader(test_loader, desc="final generated holdout test")

SYNTHETIC_TEST_JSON = DATA_DIR / "choice_quality_synthetic_test_v3.json"
synthetic_test_rows = read_json(SYNTHETIC_TEST_JSON) if SYNTHETIC_TEST_JSON.exists() else []
synthetic_test_dataset = ChoiceQualityDataset(synthetic_test_rows, tokenizer, MAX_LENGTH) if synthetic_test_rows else None
synthetic_test_loader = DataLoader(synthetic_test_dataset, batch_size=BATCH_SIZE, shuffle=False) if synthetic_test_dataset else None

if synthetic_test_loader is not None:
    synthetic_labels, synthetic_probs, synthetic_loss = predict_loader(
        synthetic_test_loader,
        desc="final synthetic test",
    )
else:
    synthetic_labels = np.zeros((0, len(ERROR_LABELS)), dtype=np.float32)
    synthetic_probs = np.zeros((0, len(ERROR_LABELS)), dtype=np.float32)
    synthetic_loss = 0.0


def source_binary_metrics(rows: list[dict[str, Any]], label_matrix: np.ndarray, prob_matrix: np.ndarray, threshold: float) -> dict[str, Any]:
    metrics: dict[str, Any] = {}
    for source_type in sorted({row.get("source_type", "unknown") for row in rows}):
        indices = [idx for idx, row in enumerate(rows) if row.get("source_type", "unknown") == source_type]
        if not indices:
            continue
        source_labels = label_matrix[indices]
        source_probs = prob_matrix[indices]
        source_metrics = compute_binary_metrics(source_labels, source_probs, threshold)
        source_metrics["count"] = len(indices)
        source_metrics["true_error_count"] = int((labels_to_binary(source_labels) == 0).sum())
        source_metrics["true_ok_count"] = int((labels_to_binary(source_labels) == 1).sum())
        metrics[source_type] = source_metrics
    return metrics


valid_binary_metrics = compute_binary_metrics(valid_labels, valid_probs, best_threshold)
test_binary_metrics = compute_binary_metrics(test_labels, test_probs, best_threshold)
synthetic_binary_metrics = (
    compute_binary_metrics(synthetic_labels, synthetic_probs, best_threshold)
    if len(synthetic_labels)
    else {}
)

valid_code_metrics = compute_code_metrics(valid_labels, valid_probs, best_threshold)
test_code_metrics = compute_code_metrics(test_labels, test_probs, best_threshold)
synthetic_code_metrics = (
    compute_code_metrics(synthetic_labels, synthetic_probs, best_threshold)
    if len(synthetic_labels)
    else {}
)

result = {
    "model_name": MODEL_NAME,
    "max_length": MAX_LENGTH,
    "train_count": len(train_rows),
    "valid_count": len(valid_rows),
    "test_count": len(test_rows),
    "synthetic_test_count": len(synthetic_test_rows),
    "input_data": "passage + question + one choice + is_answer",
    "y_value": "multi-label error_codes. binary label is derived from whether error_codes is empty.",
    "test_purpose": "choice_quality_test_v3는 학습에 넣지 않은 팀원 생성 holdout 문제와 기출 정상 문제로 실전 검증한다. synthetic_test는 참고용 별도 평가이다.",
    "warning": "현재 generated holdout의 실제 오류 수가 매우 적으면 전체 점수는 실전 성능을 충분히 설명하지 못한다.",
    "error_labels": ERROR_LABELS,
    "best_threshold": round(float(best_threshold), 3),
    "best_valid_abnormal_f1": round(float(best_score), 6),
    "history": history,
    "valid_loss": round(valid_loss, 6),
    "test_loss": round(test_loss, 6),
    "synthetic_test_loss": round(synthetic_loss, 6),
    "valid_binary_metrics": valid_binary_metrics,
    "test_binary_metrics": test_binary_metrics,
    "synthetic_binary_metrics": synthetic_binary_metrics,
    "test_source_metrics": source_binary_metrics(test_rows, test_labels, test_probs, best_threshold),
    "synthetic_source_metrics": source_binary_metrics(synthetic_test_rows, synthetic_labels, synthetic_probs, best_threshold) if synthetic_test_rows else {},
    "valid_code_metrics": valid_code_metrics,
    "test_code_metrics": test_code_metrics,
    "synthetic_code_metrics": synthetic_code_metrics,
}

print(json.dumps(result, ensure_ascii=False, indent=2))
'''


CELL9_NEW = r'''
def predicted_codes(probs: np.ndarray, threshold: float) -> list[str]:
    return [code for code, idx in ERROR_TO_ID.items() if probs[idx] >= threshold]


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_predictions(path: Path, rows: list[dict[str, Any]], labels: np.ndarray, probs: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        fieldnames = [
            "id",
            "question_id",
            "source_type",
            "is_answer",
            "true_label",
            "pred_label",
            "true_error_codes",
            "pred_error_codes",
            "max_error_prob",
            "question",
            "choice",
        ] + [f"prob_{code}" for code in ERROR_LABELS]
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()

        true_binary = labels_to_binary(labels)
        pred_binary = probs_to_binary(probs, best_threshold)
        for row, true_label, pred_label, prob in zip(rows, true_binary, pred_binary, probs):
            pred_codes = predicted_codes(prob, best_threshold)
            item = {
                "id": row.get("id"),
                "question_id": row.get("question_id"),
                "source_type": row.get("source_type"),
                "is_answer": row.get("is_answer"),
                "true_label": int(true_label),
                "pred_label": int(pred_label),
                "true_error_codes": "|".join(row.get("error_codes", [])),
                "pred_error_codes": "|".join(pred_codes),
                "max_error_prob": round(float(prob.max()), 6),
                "question": row.get("question", ""),
                "choice": row.get("choice", ""),
            }
            for code, idx in ERROR_TO_ID.items():
                item[f"prob_{code}"] = round(float(prob[idx]), 6)
            writer.writerow(item)


MODEL_DIR = OUTPUT_DIR / "model"
model.save_pretrained(MODEL_DIR)
tokenizer.save_pretrained(MODEL_DIR)

write_json(OUTPUT_DIR / "results.json", result)
write_predictions(OUTPUT_DIR / "valid_predictions.csv", valid_rows, valid_labels, valid_probs)
write_predictions(OUTPUT_DIR / "test_predictions.csv", test_rows, test_labels, test_probs)
if len(synthetic_test_rows):
    write_predictions(OUTPUT_DIR / "synthetic_test_predictions.csv", synthetic_test_rows, synthetic_labels, synthetic_probs)

print("저장 완료:", OUTPUT_DIR)
print("모델:", MODEL_DIR)
'''


def main() -> None:
    nb = json.loads(BASE.read_text(encoding="utf-8"))

    for idx, cell in enumerate(nb["cells"]):
        src = "".join(cell.get("source", []))

        if idx == 0 and cell.get("cell_type") == "markdown":
            src = src.replace("# 선지 이상 여부 BERT 학습 v2", "# 선지 이상 여부 BERT 학습 v3")
            src = src.replace(
                "규칙 검사로 형식/중복 오류를 먼저 잡고, 선지 5개를 각각 BERT에 넣어 선지별 이상 여부를 확인한다.",
                "규칙 검사로 형식/중복 오류를 먼저 잡고, 선지 5개를 각각 BERT에 넣어 선지별 이상 여부를 확인한다. v3는 팀원 생성 문제 holdout test를 분리해 실전 검증을 더 정직하게 본다.",
            )
            cell["source"] = src.splitlines(keepends=True)

        if "TRAIN_JSON = DATA_DIR" in src:
            src = src.replace('TRAIN_JSON = DATA_DIR / "choice_quality_train_v2.json"', 'TRAIN_JSON = DATA_DIR / "choice_quality_train_v3.json"')
            src = src.replace('TEST_JSON = DATA_DIR / "choice_quality_test_v2.json"', 'TEST_JSON = DATA_DIR / "choice_quality_test_v3.json"')
            src = src.replace('OUTPUT_DIR = WORKSPACE_DIR / "choice_quality_output_v2"', 'OUTPUT_DIR = WORKSPACE_DIR / "choice_quality_output_v3"')
            cell["source"] = src.splitlines(keepends=True)

        if "force_train_rows" in src and "generated" in src:
            # v3 데이터 자체에서 holdout을 분리하므로 노트북 안에서는 다시 강제 분리하지 않는다.
            old = '''# 실제 팀원 생성 오류는 수가 적어서 validation으로 빠지지 않게 train에 고정한다.
# 진짜 일반화 성능은 다음 팀원 생성 파일을 별도 test로 받아 확인해야 한다.
force_train_rows = [
    row
    for row in all_train_rows
    if row.get("source_type") == "generated" and row.get("label") == 0
]
split_candidate_rows = [
    row
    for row in all_train_rows
    if not (row.get("source_type") == "generated" and row.get("label") == 0)
]

groups = [row["question_id"] for row in split_candidate_rows]
splitter = GroupShuffleSplit(n_splits=1, test_size=VALID_SIZE, random_state=SEED)
train_idx, valid_idx = next(splitter.split(split_candidate_rows, groups=groups))
train_rows = force_train_rows + [split_candidate_rows[idx] for idx in train_idx]
valid_rows = [split_candidate_rows[idx] for idx in valid_idx]
'''
            new = '''# train 파일 안에서 validation을 나눈다.
# test 파일은 학습에 넣지 않은 팀원 생성 holdout과 기출 정상 test로 유지한다.
groups = [row["question_id"] for row in all_train_rows]
splitter = GroupShuffleSplit(n_splits=1, test_size=VALID_SIZE, random_state=SEED)
train_idx, valid_idx = next(splitter.split(all_train_rows, groups=groups))
train_rows = [all_train_rows[idx] for idx in train_idx]
valid_rows = [all_train_rows[idx] for idx in valid_idx]
'''
            src = src.replace(old, new)
            src = src.replace('print("forced generated error train:", len(force_train_rows))\n', "")
            cell["source"] = src.splitlines(keepends=True)

        if idx == 16:
            cell["source"] = CELL8_NEW.strip("\n").splitlines(keepends=True)

        if idx == 18:
            cell["source"] = CELL9_NEW.strip("\n").splitlines(keepends=True)

    OUT.write_text(json.dumps(nb, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"created: {OUT}")


if __name__ == "__main__":
    main()
