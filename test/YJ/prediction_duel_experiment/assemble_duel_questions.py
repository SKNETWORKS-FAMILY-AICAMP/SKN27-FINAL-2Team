from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any


DEFAULT_INPUT = Path(__file__).resolve().parent / "results" / "duel_first50_20260630_143539.json"
DEFAULT_OUT = Path(__file__).resolve().parent / "data" / "assembled_first10_for_rubric_eval.jsonl"

DIFFICULTY_TO_SCORE = {
    "쉬움": 1,
    "보통": 2,
    "어려움": 3,
}


def parse_json_text(text: str) -> dict[str, Any]:
    cleaned = str(text or "").strip()
    cleaned = re.sub(r"^```(?:json)?", "", cleaned).strip()
    cleaned = re.sub(r"```$", "", cleaned).strip()
    try:
        value = json.loads(cleaned)
    except json.JSONDecodeError:
        decoder = json.JSONDecoder()
        start = cleaned.find("{")
        if start < 0:
            return {"_raw": cleaned}
        value, _ = decoder.raw_decode(cleaned[start:])
    return value if isinstance(value, dict) else {"_raw": cleaned}


def choice(label: str, text: str) -> dict[str, Any]:
    number_map = {"①": 1, "②": 2, "③": 3, "④": 4, "⑤": 5}
    return {"number": number_map[label], "label": label, "text": str(text or "").strip()}


def build_record(group_no: int, owner: str, group: list[dict[str, Any]]) -> dict[str, Any]:
    correct_item = group[0]
    request = correct_item["request"]
    correct_output = parse_json_text(correct_item["yj_output"] if owner == "SLLM" else correct_item["gpt_output"])
    distractor_outputs = [
        parse_json_text(item["yj_output"] if owner == "SLLM" else item["gpt_output"])
        for item in group[1:]
    ]

    question = str(correct_output.get("question") or "").strip()
    answer_choice = str(correct_output.get("answer_choice") or "").strip()
    distractors = [str(item.get("distractor_choice") or item.get("_raw") or "").strip() for item in distractor_outputs]
    material = str(request.get("material") or "").strip()
    difficulty = str(request.get("difficulty_label") or "보통").strip()

    return {
        "question_id": (100 + group_no) if owner == "SLLM" else (200 + group_no),
        "source_group": group_no,
        "generator": owner,
        "target_score": DIFFICULTY_TO_SCORE.get(difficulty, 2),
        "difficulty_label": difficulty,
        "topic": request.get("topic"),
        "stem": f"{question}\n\n[자료]\n{material}".strip(),
        "choices": [
            choice("①", answer_choice),
            choice("②", distractors[0] if len(distractors) > 0 else ""),
            choice("③", distractors[1] if len(distractors) > 1 else ""),
            choice("④", distractors[2] if len(distractors) > 2 else ""),
            choice("⑤", distractors[3] if len(distractors) > 3 else ""),
        ],
        "answer_label": "①",
        "answer": 1,
        "explanation": "",
        "generation_request": request,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Assemble duel generation outputs into full items for rubric evaluation.")
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()

    items = json.loads(args.input.read_text(encoding="utf-8"))
    groups = [items[index : index + 5] for index in range(0, len(items), 5)]
    records: list[dict[str, Any]] = []
    for group_no, group in enumerate(groups, start=1):
        if len(group) != 5:
            continue
        records.append(build_record(group_no, "SLLM", group))
        records.append(build_record(group_no, "GPT", group))

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8") as out:
        for record in records:
            out.write(json.dumps(record, ensure_ascii=False) + "\n")
    print(f"wrote {len(records)} records: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
