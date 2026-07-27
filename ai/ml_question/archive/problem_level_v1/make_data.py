from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path
from typing import Any


ROOT_DIR = Path(__file__).resolve().parents[2]
DEFAULT_SOURCE = ROOT_DIR / "ai" / "ml" / "ML_han_v1.json"
DEFAULT_OUT_DIR = Path(__file__).resolve().parent

DIFFICULTY_TO_SCORE = {
    "쉬움": 1,
    "보통": 2,
    "어려움": 3,
}


def read_json(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, list):
        raise ValueError(f"Expected a list in {path}")
    return data


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")


def get_answer_number(choices: list[dict[str, Any]]) -> int:
    answer_numbers = [
        idx + 1
        for idx, choice in enumerate(choices)
        if bool(choice.get("is_answer"))
    ]
    if len(answer_numbers) != 1:
        raise ValueError(f"Expected exactly one answer, got {answer_numbers}")
    return answer_numbers[0]


def to_review_item(raw: dict[str, Any]) -> dict[str, Any]:
    choices = raw.get("choices") or []
    if len(choices) != 5:
        raise ValueError(f"{raw.get('problem_id')} has {len(choices)} choices")

    answer = get_answer_number(choices)
    difficulty_label = raw.get("difficulty_label")

    return {
        "id": raw["problem_id"],
        "source_id": raw["problem_id"],
        "passage": raw.get("material", ""),
        "question": raw.get("question", ""),
        "choices": [choice.get("content", "") for choice in choices],
        "answer": answer,
        "target_score": DIFFICULTY_TO_SCORE.get(difficulty_label, 2),
        "label": 1,
        "error_types": [],
        "review_memo": "원본 정상 문항",
        "data_type": "normal",
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


def make_answer_leakage(item: dict[str, Any]) -> dict[str, Any]:
    error = copy.deepcopy(item)
    answer_text = error["choices"][error["answer"] - 1]
    error["id"] = f"{item['id']}_err_leak"
    error["passage"] = f"{error['passage']} 정답 단서: {answer_text}"
    error["label"] = 0
    error["error_types"] = ["ANSWER_LEAKAGE"]
    error["review_memo"] = "정답 선지 원문을 지문에 직접 추가한 합성 오류"
    error["data_type"] = "synthetic_error"
    return error


def make_duplicate_answer(item: dict[str, Any]) -> dict[str, Any]:
    error = copy.deepcopy(item)
    answer_idx = error["answer"] - 1
    replace_idx = 0 if answer_idx != 0 else 1
    error["id"] = f"{item['id']}_err_duplicate"
    error["choices"][replace_idx] = error["choices"][answer_idx]
    error["label"] = 0
    error["error_types"] = ["ANSWER_UNIQUENESS_SUSPICIOUS"]
    error["review_memo"] = "정답 선지를 다른 보기에도 복제한 합성 오류"
    error["data_type"] = "synthetic_error"
    return error


def make_choice_bias(item: dict[str, Any]) -> dict[str, Any]:
    error = copy.deepcopy(item)
    answer_idx = error["answer"] - 1
    error["id"] = f"{item['id']}_err_bias"
    error["choices"][answer_idx] = (
        f"{error['choices'][answer_idx]} "
        "자료의 핵심 단서와 직접 연결되는 가장 구체적인 설명이다."
    )
    error["label"] = 0
    error["error_types"] = ["CHOICE_BIAS"]
    error["review_memo"] = "정답 선지만 길고 구체적으로 보이도록 만든 합성 오류"
    error["data_type"] = "synthetic_error"
    return error


def make_format_error(item: dict[str, Any]) -> dict[str, Any]:
    error = copy.deepcopy(item)
    remove_idx = next(
        idx for idx in range(len(error["choices"]) - 1, -1, -1)
        if idx != error["answer"] - 1
    )
    error["id"] = f"{item['id']}_err_format"
    del error["choices"][remove_idx]
    if remove_idx < error["answer"] - 1:
        error["answer"] -= 1
    error["label"] = 0
    error["error_types"] = ["FORMAT_ERROR"]
    error["review_memo"] = "선택지 1개를 제거해 5지선다 형식을 깨뜨린 합성 오류"
    error["data_type"] = "synthetic_error"
    return error


def make_synthetic_error(item: dict[str, Any], index: int) -> dict[str, Any]:
    makers = [
        make_answer_leakage,
        make_duplicate_answer,
        make_choice_bias,
        make_format_error,
    ]
    return makers[index % len(makers)](item)


def split_name(source_id: str) -> str:
    digest = hashlib.md5(source_id.encode("utf-8")).hexdigest()
    bucket = int(digest[:8], 16) % 100
    if bucket < 80:
        return "train"
    if bucket < 90:
        return "valid"
    return "test"


def build_model_text(item: dict[str, Any]) -> str:
    lines = [
        f"지문: {item['passage']}",
        f"질문: {item['question']}",
    ]
    lines.extend(
        f"선지{idx}: {choice}"
        for idx, choice in enumerate(item["choices"], start=1)
    )
    lines.extend([
        f"정답: {item['answer']}",
        f"목표배점: {item['target_score']}",
    ])
    return "\n".join(lines)


def build_dataset(source_path: Path) -> list[dict[str, Any]]:
    raw_items = read_json(source_path)
    normal_items = [to_review_item(raw) for raw in raw_items]

    dataset: list[dict[str, Any]] = []
    for idx, item in enumerate(normal_items):
        dataset.append(item)
        dataset.append(make_synthetic_error(item, idx))

    return dataset


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    args = parser.parse_args()

    dataset = build_dataset(args.source)
    splits = {"train": [], "valid": [], "test": []}
    for item in dataset:
        splits[split_name(item["source_id"])].append(item)

    write_json(args.out_dir / "review_data.json", dataset)
    write_json(args.out_dir / "train.json", splits["train"])
    write_json(args.out_dir / "valid.json", splits["valid"])
    write_json(args.out_dir / "test.json", splits["test"])

    summary = {
        "source": str(args.source),
        "total": len(dataset),
        "label_count": {
            "0": sum(1 for item in dataset if item["label"] == 0),
            "1": sum(1 for item in dataset if item["label"] == 1),
        },
        "split_count": {name: len(items) for name, items in splits.items()},
        "files": [
            "review_data.json",
            "train.json",
            "valid.json",
            "test.json",
        ],
    }
    write_json(args.out_dir / "summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
