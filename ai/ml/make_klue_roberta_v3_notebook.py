# KLUE/RoBERTa v3 Colab 노트북을 생성하는 스크립트입니다.
# v2 구조를 유지하고 class weight만 추가합니다.
# 실행하면 ai/ml/klue_roberta_v3.ipynb 파일이 생성됩니다.

from __future__ import annotations

import json
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent
V2_NOTEBOOK = BASE_DIR / "klue_roberta_v2.ipynb"
OUT = BASE_DIR / "klue_roberta_v3.ipynb"


def code(text: str) -> dict:
    return {
        "cell_type": "code",
        "execution_count": None,
        "metadata": {},
        "outputs": [],
        "source": text.strip("\n").splitlines(keepends=True),
    }


def markdown(text: str) -> dict:
    return {
        "cell_type": "markdown",
        "metadata": {},
        "source": text.strip("\n").splitlines(keepends=True),
    }


def replace_source(cell: dict, pairs: list[tuple[str, str]]) -> None:
    source = "".join(cell.get("source", []))
    for old, new in pairs:
        source = source.replace(old, new)
    cell["source"] = source.splitlines(keepends=True)


def insert_after(cells: list[dict], marker: str, new_cell: dict) -> None:
    for index, cell in enumerate(cells):
        if marker in "".join(cell.get("source", [])):
            cells.insert(index + 1, new_cell)
            return
    raise ValueError(f"marker not found: {marker}")


def main() -> None:
    nb = json.loads(V2_NOTEBOOK.read_text(encoding="utf-8"))
    cells = nb["cells"]

    for cell in cells:
        replace_source(
            cell,
            [
                ("KLUE/RoBERTa v2", "KLUE/RoBERTa v3"),
                ("klue_roberta_v2", "klue_roberta_v3"),
                ("v2", "v3"),
                ("Class weight: not applied", "Class weight: applied"),
                ("class weight 미적용", "class weight 적용"),
                (
                    "이번 v3에서는 class weight를 아직 적용하지 않습니다.",
                    "이번 v3에서는 class weight를 적용합니다.",
                ),
                (
                    "성능 개선 과정을 분리해서 기록하기 위해 early stopping 효과만 먼저 확인합니다.",
                    "성능 개선 과정을 분리해서 기록하기 위해 class weight 효과만 확인합니다.",
                ),
            ],
        )

    for cell in cells:
        source = "".join(cell.get("source", []))

        if "from sklearn.metrics import accuracy_score, classification_report, f1_score" in source:
            replace_source(
                cell,
                [
                    (
                        "from sklearn.metrics import accuracy_score, classification_report, f1_score",
                        "from sklearn.metrics import accuracy_score, classification_report, confusion_matrix, f1_score",
                    )
                ],
            )

        if "'early_stopping': True," in source:
            replace_source(cell, [("'early_stopping': True,", "'early_stopping': True,\n    'class_weight': True,")])

        if "def run_validation_loss(model, valid_loader):" in source:
            replace_source(
                cell,
                [
                    (
                        "def run_validation_loss(model, valid_loader):\n"
                        "    model.eval()\n"
                        "    total_loss = 0.0\n"
                        "    with torch.no_grad():\n"
                        "        for batch in valid_loader:\n"
                        "            batch = {key: value.to(device) for key, value in batch.items()}\n"
                        "            outputs = model(**batch)\n"
                        "            total_loss += outputs.loss.item()\n"
                        "    return total_loss / max(1, len(valid_loader))",
                        "def run_validation_loss(model, valid_loader, loss_fn):\n"
                        "    model.eval()\n"
                        "    total_loss = 0.0\n"
                        "    with torch.no_grad():\n"
                        "        for batch in valid_loader:\n"
                        "            batch = {key: value.to(device) for key, value in batch.items()}\n"
                        "            labels = batch.pop('labels')\n"
                        "            outputs = model(**batch)\n"
                        "            loss = loss_fn(outputs.logits, labels)\n"
                        "            total_loss += loss.item()\n"
                        "    return total_loss / max(1, len(valid_loader))",
                    )
                ],
            )

        if "def train_and_predict_target(target):" in source:
            replace_source(
                cell,
                [
                    (
                        "    optimizer = torch.optim.AdamW(model.parameters(), lr=LEARNING_RATE)",
                        "    class_weights = make_class_weight_tensor(fit_rows, target, label_to_id)\n"
                        "    print('class weights:', {id_to_label[i]: round(float(class_weights[i].cpu()), 4) for i in range(len(id_to_label))})\n"
                        "    loss_fn = torch.nn.CrossEntropyLoss(weight=class_weights)\n\n"
                        "    optimizer = torch.optim.AdamW(model.parameters(), lr=LEARNING_RATE)",
                    ),
                    (
                        "            outputs = model(**batch)\n"
                        "            loss = outputs.loss",
                        "            labels = batch.pop('labels')\n"
                        "            outputs = model(**batch)\n"
                        "            loss = loss_fn(outputs.logits, labels)",
                    ),
                    (
                        "        val_loss = run_validation_loss(model, valid_loader)",
                        "        val_loss = run_validation_loss(model, valid_loader, loss_fn)",
                    ),
                    (
                        "            'history': history,",
                        "            'history': history,\n"
                        "            'class_weights': {id_to_label[i]: round(float(class_weights[i].cpu()), 6) for i in range(len(id_to_label))},",
                    ),
                    (
                        "        'row_predictions': row_predictions,",
                        "        'confusion_matrix': build_confusion_matrix(y_true, y_pred),\n"
                        "        'row_predictions': row_predictions,",
                    ),
                ],
            )

        if "def build_markdown(results):" in source:
            replace_source(
                cell,
                [
                    (
                        "        lines.append('### Per-class Metrics')",
                        "        lines.append('### Class Weights')\n"
                        "        lines.append('')\n"
                        "        lines.append('| label | weight |')\n"
                        "        lines.append('|---|---:|')\n"
                        "        class_weights = target_result.get('class_weights', {})\n"
                        "        if class_weights:\n"
                        "            for label, weight in class_weights.items():\n"
                        "                lines.append(f'| {label} | {weight:.4f} |')\n"
                        "        else:\n"
                        "            lines.append('| class weight values are not available. Re-run target training cells. | 0.0000 |')\n"
                        "        lines.append('')\n\n"
                        "        lines.append('### Per-class Metrics')",
                    ),
                    (
                        "        lines.append('')\n"
                        "    return '\\n'.join(lines) + '\\n'",
                        "        lines.append('')\n"
                        "        lines.append('### Confusion Matrix')\n"
                        "        lines.append('')\n"
                        "        cm = target_result.get('confusion_matrix')\n"
                        "        if cm is None and target_result.get('row_predictions'):\n"
                        "            y_true_for_cm = [row['true_label'] for row in target_result['row_predictions']]\n"
                        "            y_pred_for_cm = [row['pred_label'] for row in target_result['row_predictions']]\n"
                        "            cm = build_confusion_matrix(y_true_for_cm, y_pred_for_cm)\n"
                        "        if cm:\n"
                        "            cm_labels = cm['labels']\n"
                        "            lines.append('| actual \\\\ predicted | ' + ' | '.join(cm_labels) + ' |')\n"
                        "            lines.append('|---|' + '|'.join(['---:'] * len(cm_labels)) + '|')\n"
                        "            for label, row in zip(cm_labels, cm['matrix']):\n"
                        "                lines.append(f'| {label} | ' + ' | '.join(str(value) for value in row) + ' |')\n"
                        "        else:\n"
                        "            lines.append('Confusion matrix is not available. Re-run target training cells.')\n"
                        "        lines.append('')\n"
                        "    return '\\n'.join(lines) + '\\n'",
                    )
                ],
            )

    insert_after(
        cells,
        "def evaluate_predictions(y_true, y_pred):",
        code(
            """
def make_class_weight_tensor(rows, target, label_to_id):
    counts = Counter(str(row[target]) for row in rows)
    total = sum(counts.values())
    num_classes = len(label_to_id)
    weights = [0.0] * num_classes
    for label, label_id in label_to_id.items():
        weights[label_id] = total / (num_classes * counts[label])
    return torch.tensor(weights, dtype=torch.float, device=device)


def build_confusion_matrix(y_true, y_pred):
    labels = sorted(set(y_true) | set(y_pred))
    matrix = confusion_matrix(y_true, y_pred, labels=labels)
    return {
        'labels': labels,
        'matrix': matrix.astype(int).tolist(),
    }
"""
        ),
    )

    insert_after(
        cells,
        "## 15. Loss 그래프 확인",
        markdown(
            """
## 16. Confusion Matrix 확인

class weight 적용 후 실제 라벨이 어떤 라벨로 예측되는지 확인합니다.
"""
        ),
    )

    insert_after(
        cells,
        "## 16. Confusion Matrix 확인",
        code(
            """
import matplotlib.pyplot as plt
import subprocess
from matplotlib import font_manager

RESULT_DIR.mkdir(parents=True, exist_ok=True)

try:
    import koreanize_matplotlib
except ImportError:
    subprocess.run(['pip', 'install', '-q', 'koreanize-matplotlib'], check=False)
    import koreanize_matplotlib

font_path = '/usr/share/fonts/truetype/nanum/NanumGothic.ttf'
if 'NanumGothic' not in plt.rcParams.get('font.family', []):
    if not Path(font_path).exists():
        subprocess.run(['apt-get', 'update', '-qq'], check=False)
        subprocess.run(['apt-get', 'install', '-y', 'fonts-nanum'], check=False)
    if Path(font_path).exists():
        font_manager.fontManager.addfont(font_path)
        plt.rcParams['font.family'] = 'NanumGothic'
plt.rcParams['axes.unicode_minus'] = False

for target in TARGET_COLUMNS:
    target_result = results['targets'][target]
    if 'confusion_matrix' not in target_result and target_result.get('row_predictions'):
        y_true_for_cm = [row['true_label'] for row in target_result['row_predictions']]
        y_pred_for_cm = [row['pred_label'] for row in target_result['row_predictions']]
        target_result['confusion_matrix'] = build_confusion_matrix(y_true_for_cm, y_pred_for_cm)

    if 'confusion_matrix' not in target_result:
        print('skip confusion matrix because row predictions are not available:', target)
        continue

    cm = target_result['confusion_matrix']
    labels = cm['labels']
    matrix = np.array(cm['matrix'])

    plt.figure(figsize=(max(7, len(labels) * 0.7), max(5, len(labels) * 0.55)))
    plt.imshow(matrix, cmap='Blues')
    plt.title(f'{target} confusion matrix')
    plt.xlabel('predicted')
    plt.ylabel('actual')
    plt.xticks(range(len(labels)), labels, rotation=45, ha='right')
    plt.yticks(range(len(labels)), labels)
    plt.colorbar()

    max_value = matrix.max() if matrix.size else 0
    threshold = max_value / 2 if max_value else 0
    for row_idx in range(matrix.shape[0]):
        for col_idx in range(matrix.shape[1]):
            value = matrix[row_idx, col_idx]
            color = 'white' if value > threshold else 'black'
            plt.text(col_idx, row_idx, str(value), ha='center', va='center', color=color, fontsize=8)

    plt.tight_layout()
    png_path = RESULT_DIR / f'{target}_klue_roberta_v3_confusion_matrix.png'
    plt.savefig(png_path, dpi=150)
    plt.show()
    print('saved confusion matrix:', png_path)
"""
        ),
    )

    for cell in cells:
        replace_source(
            cell,
            [
                ("## 16. Markdown 리포트 생성 함수", "## 17. Markdown 리포트 생성 함수"),
                ("## 16. 결과 저장", "## 18. 결과 저장"),
                ("## 17. 저장된 결과 확인", "## 19. 저장된 결과 확인"),
            ],
        )

    OUT.write_text(json.dumps(nb, ensure_ascii=False, indent=2), encoding="utf-8")
    print(OUT)


if __name__ == "__main__":
    main()
