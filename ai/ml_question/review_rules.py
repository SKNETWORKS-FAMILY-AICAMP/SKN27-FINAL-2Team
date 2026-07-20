from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from statistics import mean
from typing import Any


def normalize_text(text: str) -> str:
    return re.sub(r"\s+", "", str(text or "")).lower()


def get_answer_index(row: dict[str, Any]) -> int | None:
    answer = row.get("answer")
    try:
        answer_index = int(answer) - 1
    except (TypeError, ValueError):
        return None
    choices = row.get("choices") or []
    if answer_index < 0 or answer_index >= len(choices):
        return None
    return answer_index


def check_answer_length_bias(
    row: dict[str, Any],
    *,
    ratio_threshold: float = 1.5,
    diff_threshold: int = 12,
) -> dict[str, Any] | None:
    choices = row.get("choices") or []
    answer_index = get_answer_index(row)
    if answer_index is None or len(choices) < 2:
        return None

    answer_len = len(str(choices[answer_index]))
    other_lengths = [
        len(str(choice))
        for idx, choice in enumerate(choices)
        if idx != answer_index
    ]
    avg_other_len = mean(other_lengths)
    diff = answer_len - avg_other_len

    too_long = answer_len >= avg_other_len * ratio_threshold and diff >= diff_threshold
    too_short = answer_len * ratio_threshold <= avg_other_len and abs(diff) >= diff_threshold
    if not (too_long or too_short):
        return None

    return {
        "type": "ANSWER_LENGTH_BIAS",
        "message": "정답 선지가 다른 선지에 비해 유독 길거나 짧음",
        "answer_length": answer_len,
        "other_avg_length": round(avg_other_len, 2),
        "diff": round(diff, 2),
    }


def check_answer_in_passage(row: dict[str, Any]) -> dict[str, Any] | None:
    choices = row.get("choices") or []
    answer_index = get_answer_index(row)
    if answer_index is None:
        return None

    passage = normalize_text(row.get("passage", ""))
    question = normalize_text(row.get("question", ""))
    answer_text = normalize_text(choices[answer_index])

    if not answer_text:
        return None

    found_in: list[str] = []
    if answer_text in passage:
        found_in.append("passage")
    if answer_text in question:
        found_in.append("question")

    if not found_in:
        return None

    return {
        "type": "ANSWER_IN_PASSAGE",
        "message": "정답 선지 원문이 지문 또는 질문에 포함됨",
        "found_in": found_in,
    }


def check_answer_candidate_count(
    row: dict[str, Any],
    *,
    threshold: float = 0.5,
) -> dict[str, Any] | None:
    probs = row.get("answer_probs")
    if not isinstance(probs, list) or not probs:
        return None

    candidate_numbers = [
        idx + 1
        for idx, prob in enumerate(probs)
        if float(prob) >= threshold
    ]

    if len(candidate_numbers) == 1:
        return None

    return {
        "type": "ANSWER_CANDIDATE_COUNT_ERROR",
        "message": "정답 후보가 0개이거나 2개 이상임",
        "threshold": threshold,
        "candidate_count": len(candidate_numbers),
        "candidate_numbers": candidate_numbers,
        "answer_probs": probs,
    }


def check_answer_key_mismatch(
    row: dict[str, Any],
    *,
    threshold: float = 0.5,
) -> dict[str, Any] | None:
    probs = row.get("answer_probs")
    answer_index = get_answer_index(row)
    if answer_index is None or not isinstance(probs, list) or len(probs) <= answer_index:
        return None

    candidate_numbers = [
        idx + 1
        for idx, prob in enumerate(probs)
        if float(prob) >= threshold
    ]
    if len(candidate_numbers) != 1:
        return None

    predicted_answer = candidate_numbers[0]
    given_answer = answer_index + 1
    if predicted_answer == given_answer:
        return None

    return {
        "type": "ANSWER_KEY_MISMATCH",
        "message": "정답 후보는 1개지만 표시 정답과 다름",
        "given_answer": given_answer,
        "predicted_answer": predicted_answer,
        "answer_probs": probs,
    }


def review_question(row: dict[str, Any], *, threshold: float = 0.5) -> dict[str, Any]:
    issues = [
        issue
        for issue in [
            check_answer_length_bias(row),
            check_answer_in_passage(row),
            check_answer_candidate_count(row, threshold=threshold),
            check_answer_key_mismatch(row, threshold=threshold),
        ]
        if issue is not None
    ]

    return {
        "id": row.get("id") or row.get("question_id"),
        "label": 0 if issues else 1,
        "issues": issues,
    }


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run simple rule checks for generated questions.")
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--threshold", type=float, default=0.5)
    args = parser.parse_args()

    data = read_json(args.input)
    rows = data if isinstance(data, list) else [data]
    results = [review_question(row, threshold=args.threshold) for row in rows]
    write_json(args.output, results)
    print(json.dumps({"total": len(results), "abnormal": sum(item["label"] == 0 for item in results)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
