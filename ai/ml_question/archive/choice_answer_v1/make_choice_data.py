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


def split_name(source_id: str) -> str:
    digest = hashlib.md5(source_id.encode("utf-8")).hexdigest()
    bucket = int(digest[:8], 16) % 100
    if bucket < 80:
        return "train"
    return "test"


def build_choice_rows(raw: dict[str, Any]) -> list[dict[str, Any]]:
    choices = raw.get("choices") or []
    if len(choices) != 5:
        raise ValueError(f"{raw.get('problem_id')} has {len(choices)} choices")

    answer_numbers = [
        idx + 1
        for idx, choice in enumerate(choices)
        if bool(choice.get("is_answer"))
    ]
    if len(answer_numbers) != 1:
        raise ValueError(f"{raw.get('problem_id')} answer count: {answer_numbers}")

    answer = answer_numbers[0]
    difficulty_label = raw.get("difficulty_label")
    rows: list[dict[str, Any]] = []

    for idx, choice in enumerate(choices, start=1):
        is_answer = bool(choice.get("is_answer"))
        rows.append(
            {
                "id": f"{raw['problem_id']}_choice_{idx}",
                "question_id": raw["problem_id"],
                "passage": raw.get("material", ""),
                "question": raw.get("question", ""),
                "choice_no": idx,
                "choice": choice.get("content", ""),
                "answer": answer,
                "label": 1 if is_answer else 0,
                "label_name": "ANSWER" if is_answer else "DISTRACTOR",
                "meta": {
                    "difficulty_label": difficulty_label,
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
        )

    return rows


def main() -> None:
    raw_items = read_json(SOURCE)
    rows: list[dict[str, Any]] = []
    for raw in raw_items:
        rows.extend(build_choice_rows(raw))

    splits = {"train": [], "test": []}
    for row in rows:
        splits[split_name(row["question_id"])].append(row)

    write_json(OUT_DIR / "choice_data.json", rows)
    write_json(OUT_DIR / "choice_train.json", splits["train"])
    write_json(OUT_DIR / "choice_test.json", splits["test"])

    summary = {
        "source": str(SOURCE),
        "question_count": len(raw_items),
        "total": len(rows),
        "label_count": {
            "0": sum(1 for row in rows if row["label"] == 0),
            "1": sum(1 for row in rows if row["label"] == 1),
        },
        "split_count": {name: len(items) for name, items in splits.items()},
        "files": [
            "choice_data.json",
            "choice_train.json",
            "choice_test.json",
        ],
    }
    write_json(OUT_DIR / "choice_summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
