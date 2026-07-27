from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


ROOT_DIR = Path(__file__).resolve().parents[2]
SOURCE = ROOT_DIR / "ai" / "ml" / "ML_han_v1.json"
OUT_DIR = Path(__file__).resolve().parent


def read_json(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, list):
        raise ValueError(f"Expected list: {path}")
    return data


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")


def split_name(problem_id: str) -> str:
    digest = hashlib.md5(problem_id.encode("utf-8")).hexdigest()
    bucket = int(digest[:8], 16) % 100
    if bucket < 80:
        return "train"
    return "test"


def to_mc_row(raw: dict[str, Any]) -> dict[str, Any]:
    choices = raw.get("choices") or []
    if len(choices) != 5:
        raise ValueError(f"{raw.get('problem_id')} has {len(choices)} choices")

    answer_indices = [
        idx
        for idx, choice in enumerate(choices)
        if bool(choice.get("is_answer"))
    ]
    if len(answer_indices) != 1:
        raise ValueError(f"{raw.get('problem_id')} answer count: {answer_indices}")

    return {
        "id": raw["problem_id"],
        "passage": raw.get("material", ""),
        "question": raw.get("question", ""),
        "choices": [choice.get("content", "") for choice in choices],
        "answer_index": answer_indices[0],
        "answer": answer_indices[0] + 1,
        "meta": {
            "difficulty_label": raw.get("difficulty_label"),
            "topic": raw.get("topic"),
            "topic_type": raw.get("topic_type"),
            "major_type": raw.get("major_type"),
            "minor_type": raw.get("minor_type"),
            "question_task": raw.get("question_task"),
            "round_no": raw.get("round_no"),
            "question_no": raw.get("question_no"),
            "data_source": raw.get("data_source"),
        },
    }


def main() -> None:
    raw_items = read_json(SOURCE)
    rows = [to_mc_row(raw) for raw in raw_items]

    splits = {"train": [], "test": []}
    for row in rows:
        splits[split_name(row["id"])].append(row)

    write_json(OUT_DIR / "mc_data.json", rows)
    write_json(OUT_DIR / "mc_train.json", splits["train"])
    write_json(OUT_DIR / "mc_test.json", splits["test"])

    summary = {
        "source": str(SOURCE),
        "total": len(rows),
        "split_count": {name: len(items) for name, items in splits.items()},
        "files": [
            "mc_data.json",
            "mc_train.json",
            "mc_test.json",
        ],
    }
    write_json(OUT_DIR / "mc_summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
