from __future__ import annotations

import json
from pathlib import Path


SOURCE = Path(r"C:\Users\Playdata\Downloads\train_mc_runpod_v2.ipynb")
OUT = Path(__file__).resolve().parent / "train_mc_runpod_v2_progress.ipynb"


def replace_once(text: str, old: str, new: str) -> str:
    if old not in text:
        return text
    return text.replace(old, new, 1)


def main() -> None:
    nb = json.loads(SOURCE.read_text(encoding="utf-8"))

    for cell in nb["cells"]:
        if cell.get("cell_type") == "code":
            cell["execution_count"] = None
            cell["outputs"] = []

        src = "".join(cell.get("source", []))

        if "klue/roberta-base" in src:
            src = src.replace("klue/roberta-base", "klue/roberta-large")
            cell["source"] = src.splitlines(keepends=True)

        if "!pip install" in src and "tqdm" not in src:
            src = src.replace("accelerate", "accelerate tqdm")
            cell["source"] = src.splitlines(keepends=True)

        src = "".join(cell.get("source", []))
        if "import numpy as np" in src and "from tqdm.auto import tqdm" not in src:
            src = replace_once(src, "import torch\n", "import torch\nfrom tqdm.auto import tqdm\n")
            cell["source"] = src.splitlines(keepends=True)

        src = "".join(cell.get("source", []))
        if "def predict_loader" in src and "progress = tqdm(loader, desc=desc" not in src:
            src = replace_once(
                src,
                "def predict_loader(model: AutoModelForMultipleChoice, loader: DataLoader) -> tuple[list[int], list[int], list[list[float]]]:\n"
                "    model.eval()\n",
                "def predict_loader(model: AutoModelForMultipleChoice, loader: DataLoader, *, desc: str = \"predict\") -> tuple[list[int], list[int], list[list[float]]]:\n"
                "    model.eval()\n",
            )
            src = replace_once(
                src,
                "    with torch.no_grad():\n"
                "        for batch in loader:\n",
                "    with torch.no_grad():\n"
                "        progress = tqdm(loader, desc=desc, leave=False)\n"
                "        for batch in progress:\n",
            )
            src = replace_once(
                src,
                "def evaluate_loss(model: AutoModelForMultipleChoice, loader: DataLoader) -> float:\n"
                "    model.eval()\n"
                "    losses: list[float] = []\n\n"
                "    with torch.no_grad():\n"
                "        for batch in loader:\n",
                "def evaluate_loss(model: AutoModelForMultipleChoice, loader: DataLoader, *, desc: str = \"valid loss\") -> float:\n"
                "    model.eval()\n"
                "    losses: list[float] = []\n\n"
                "    with torch.no_grad():\n"
                "        progress = tqdm(loader, desc=desc, leave=False)\n"
                "        for batch in progress:\n",
            )
            src = replace_once(
                src,
                "            outputs = model(**batch)\n"
                "            losses.append(float(outputs.loss.item()))\n\n"
                "    return float(mean(losses)) if losses else 0.0\n",
                "            outputs = model(**batch)\n"
                "            losses.append(float(outputs.loss.item()))\n"
                "            progress.set_postfix(loss=f\"{mean(losses):.4f}\")\n\n"
                "    return float(mean(losses)) if losses else 0.0\n",
            )
            cell["source"] = src.splitlines(keepends=True)

        src = "".join(cell.get("source", []))
        if "def train_epoch" in src and "def train_epoch(epoch: int)" not in src:
            src = replace_once(src, "def train_epoch() -> float:\n", "def train_epoch(epoch: int) -> float:\n")
            src = replace_once(
                src,
                "    for batch in train_loader:\n",
                "    progress = tqdm(train_loader, desc=f\"epoch {epoch}/{EPOCHS} train\", leave=True)\n"
                "    for step, batch in enumerate(progress, start=1):\n",
            )
            src = replace_once(
                src,
                "        losses.append(float(loss.item()))\n\n"
                "    return float(mean(losses)) if losses else 0.0\n",
                "        losses.append(float(loss.item()))\n"
                "        progress.set_postfix(loss=f\"{mean(losses):.4f}\", lr=f\"{scheduler.get_last_lr()[0]:.2e}\")\n\n"
                "    return float(mean(losses)) if losses else 0.0\n",
            )
            src = replace_once(
                src,
                "    train_loss = train_epoch()\n",
                "    print(f\"\\n===== epoch {epoch}/{EPOCHS} =====\")\n"
                "    train_loss = train_epoch(epoch)\n",
            )
            src = src.replace(
                "    valid_loss = evaluate_loss(model, valid_loader)\n",
                "    valid_loss = evaluate_loss(model, valid_loader, desc=f\"epoch {epoch}/{EPOCHS} valid loss\")\n",
            )
            src = src.replace(
                "    valid_true, valid_pred, _ = predict_loader(model, valid_loader)\n",
                "    valid_true, valid_pred, _ = predict_loader(model, valid_loader, desc=f\"epoch {epoch}/{EPOCHS} valid pred\")\n",
            )
            cell["source"] = src.splitlines(keepends=True)

        src = "".join(cell.get("source", []))
        if "valid_true, valid_pred, valid_probs = predict_loader" in src:
            src = src.replace(
                "valid_true, valid_pred, valid_probs = predict_loader(model, valid_loader)\n",
                "valid_true, valid_pred, valid_probs = predict_loader(model, valid_loader, desc=\"final valid pred\")\n",
            )
            src = src.replace(
                "test_true, test_pred, test_probs = predict_loader(model, test_loader)\n",
                "test_true, test_pred, test_probs = predict_loader(model, test_loader, desc=\"final test pred\")\n",
            )
            cell["source"] = src.splitlines(keepends=True)

    OUT.write_text(json.dumps(nb, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"created: {OUT}")


if __name__ == "__main__":
    main()
