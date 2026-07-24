from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any


ROOT_DIR = Path(__file__).resolve().parents[2]
OUT_DIR = Path(__file__).resolve().parent

PAST_EXAM_SOURCE = ROOT_DIR / "ai" / "ml" / "ML_han_v1.json"
GENERATED_SOURCE = Path(r"C:\Users\Playdata\Downloads\yj_question.json")


ERROR_KO = {
    "ANSWER_IN_PASSAGE": "정답 노출",
    "ANSWER_LENGTH_BIAS": "정답 선지 길이 편향",
    "WEIRD_DISTRACTOR": "이상한 오답 선지",
    "CHOICE_STYLE_MISMATCH": "선지 형식 불일치",
    "QUESTION_CHOICE_MISMATCH": "질문-선지 불일치",
    "CHOICE_TOO_VAGUE": "선지 모호함",
    "CHOICE_GRAMMAR_ERROR": "선지 문장 오류",
    "DUPLICATE_OR_SIMILAR_CHOICE": "선지 중복/유사",
    "ANSWER_FORMAT_ERROR": "정답 형식 오류",
}


def read_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")


def split_name(group_id: str) -> str:
    digest = hashlib.md5(group_id.encode("utf-8")).hexdigest()
    bucket = int(digest[:8], 16) % 100
    return "train" if bucket < 80 else "test"


def normalize_text(text: Any) -> str:
    return re.sub(r"\s+", "", str(text or "")).lower()


def choice_text(choice: Any) -> str:
    if isinstance(choice, dict):
        return str(choice.get("content") or choice.get("text") or "").strip()
    return str(choice or "").strip()


def choice_is_answer(choice: Any, idx: int, answer_number: int | None = None) -> bool:
    if isinstance(choice, dict) and "is_answer" in choice:
        return bool(choice.get("is_answer"))
    if answer_number is not None:
        return idx == answer_number
    return False


def label_name(label: int) -> str:
    return "OK" if label == 1 else "ERROR"


def make_row(
    *,
    row_id: str,
    question_id: str,
    passage: str,
    question: str,
    choice_no: int,
    choice: str,
    is_answer: bool,
    label: int,
    error_codes: list[str],
    source_type: str,
    meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "id": row_id,
        "question_id": question_id,
        "passage": passage,
        "question": question,
        "choice_no": choice_no,
        "choice": choice,
        # 이 값은 입력 feature이다. 모델이 맞힐 값이 아니다.
        "is_answer": 1 if is_answer else 0,
        # 모델이 맞힐 값이다. 1=이상 없음, 0=이상 있음.
        "label": label,
        "label_name": label_name(label),
        "error_codes": error_codes,
        "error_names_ko": [ERROR_KO.get(code, code) for code in error_codes],
        "source_type": source_type,
        "meta": meta or {},
    }


def past_exam_rows(raw_items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for raw in raw_items:
        choices = raw.get("choices") or []
        if len(choices) != 5:
            continue

        question_id = raw["problem_id"]
        for idx, choice in enumerate(choices, start=1):
            rows.append(
                make_row(
                    row_id=f"past_{question_id}_choice_{idx}",
                    question_id=f"past_{question_id}",
                    passage=str(raw.get("material", "")),
                    question=str(raw.get("question", "")),
                    choice_no=idx,
                    choice=choice_text(choice),
                    is_answer=choice_is_answer(choice, idx),
                    label=1,
                    error_codes=[],
                    source_type="past_exam",
                    meta={
                        "topic": raw.get("topic"),
                        "question_task": raw.get("question_task"),
                        "difficulty_label": raw.get("difficulty_label"),
                        "data_source": raw.get("data_source"),
                    },
                )
            )
    return rows


def duplicated_choice_numbers(choices: list[Any]) -> set[int]:
    seen: dict[str, int] = {}
    duplicated: set[int] = set()
    for idx, choice in enumerate(choices, start=1):
        norm = normalize_text(choice_text(choice))
        if not norm:
            continue
        if norm in seen:
            duplicated.add(seen[norm])
            duplicated.add(idx)
        else:
            seen[norm] = idx
    return duplicated


def generated_question_rows(q: dict[str, Any]) -> tuple[list[dict[str, Any]], bool]:
    choices = q.get("choices") or []
    if len(choices) != 5:
        return [], True

    question_id = str(q.get("seed_id") or q.get("id") or q.get("problem_id") or hashlib.md5(json.dumps(q, ensure_ascii=False).encode("utf-8")).hexdigest()[:12])
    answer_number = q.get("answer_number")
    try:
        answer_number = int(answer_number) if answer_number is not None else None
    except Exception:
        answer_number = None

    validation = q.get("validation") or {}
    failed_gates = set(validation.get("failed_gates") or [])
    gate_errors: list[str] = []
    gates = ((validation.get("gate") or {}).get("gates") or {})
    for gate_data in gates.values():
        gate_errors.extend(str(error) for error in gate_data.get("errors") or [])

    # 역사 사실성 gate는 현재 학습 목표에서 제외한다.
    if failed_gates.intersection({"G4", "G5"}):
        return [], True

    handled_problem = False
    abnormal_by_choice: dict[int, list[str]] = {}

    if "answer_choice_repeats_material" in gate_errors:
        handled_problem = True
        for idx, choice in enumerate(choices, start=1):
            if choice_is_answer(choice, idx, answer_number):
                abnormal_by_choice.setdefault(idx, []).append("ANSWER_IN_PASSAGE")

    if "choice_has_malformed_predicate" in gate_errors:
        handled_problem = True
        target_idx = answer_number if answer_number in {1, 2, 3, 4, 5} else 1
        for idx, choice in enumerate(choices, start=1):
            text = choice_text(choice)
            if "이며이다" in text or "이다." in text[-5:] or idx == target_idx:
                abnormal_by_choice.setdefault(idx, []).append("CHOICE_GRAMMAR_ERROR")
                break

    if "duplicate_choice" in gate_errors:
        handled_problem = True
        for idx in duplicated_choice_numbers(choices):
            abnormal_by_choice.setdefault(idx, []).append("DUPLICATE_OR_SIMILAR_CHOICE")

    answer_count = sum(1 for idx, choice in enumerate(choices, start=1) if choice_is_answer(choice, idx, answer_number))
    if answer_count != 1:
        handled_problem = True
        for idx in range(1, 6):
            abnormal_by_choice.setdefault(idx, []).append("ANSWER_FORMAT_ERROR")

    unhandled_failure = bool(failed_gates) and not handled_problem
    if unhandled_failure:
        # 예: runpod_generation_failed 단독은 선지별 이상 라벨로 해석하기 애매해서 제외한다.
        return [], True

    rows: list[dict[str, Any]] = []
    for idx, choice in enumerate(choices, start=1):
        errors = sorted(set(abnormal_by_choice.get(idx, [])))
        rows.append(
            make_row(
                row_id=f"generated_{question_id}_choice_{idx}",
                question_id=f"generated_{question_id}",
                passage=str(q.get("material", "")),
                question=str(q.get("question", "")),
                choice_no=idx,
                choice=choice_text(choice),
                is_answer=choice_is_answer(choice, idx, answer_number),
                label=0 if errors else 1,
                error_codes=errors,
                source_type="generated",
                meta={
                    "topic": q.get("topic"),
                    "question_task": q.get("question_task"),
                    "difficulty_label": q.get("difficulty_label"),
                    "validation_status": validation.get("status"),
                    "gate_result": validation.get("gate_result"),
                    "failed_gates": sorted(failed_gates),
                },
            )
        )
    return rows, False


def generated_rows(data: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, int]]:
    rows: list[dict[str, Any]] = []
    skipped = 0
    for q in data.get("questions", []):
        q_rows, was_skipped = generated_question_rows(q)
        rows.extend(q_rows)
        skipped += int(was_skipped)
    return rows, {"generated_questions": len(data.get("questions", [])), "generated_skipped_questions": skipped}


def synthetic_abnormal_rows(raw_items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for raw in raw_items:
        choices = raw.get("choices") or []
        if len(choices) != 5:
            continue

        question_id = raw["problem_id"]
        passage = str(raw.get("material", ""))
        question = str(raw.get("question", ""))
        answer_choices = [choice for choice in choices if choice_is_answer(choice, 0)]
        distractor_choices = [choice for choice in choices if not choice_is_answer(choice, 0)]
        if not answer_choices or not distractor_choices:
            continue

        answer_text = choice_text(answer_choices[0])
        distractor_text = choice_text(distractor_choices[0])

        rows.append(
            make_row(
                row_id=f"synthetic_{question_id}_answer_in_passage",
                question_id=f"synthetic_{question_id}",
                passage=f"{passage} 정답 단서: {answer_text}",
                question=question,
                choice_no=1,
                choice=answer_text,
                is_answer=True,
                label=0,
                error_codes=["ANSWER_IN_PASSAGE"],
                source_type="synthetic",
                meta={"base_question_id": question_id},
            )
        )

        rows.append(
            make_row(
                row_id=f"synthetic_{question_id}_answer_length_bias",
                question_id=f"synthetic_{question_id}",
                passage=passage,
                question=question,
                choice_no=1,
                choice=f"{answer_text} 이는 지문에서 제시된 조건과 직접 연결되는 매우 구체적인 설명으로, 다른 선지보다 지나치게 자세하게 서술되어 정답 단서가 될 수 있다.",
                is_answer=True,
                label=0,
                error_codes=["ANSWER_LENGTH_BIAS"],
                source_type="synthetic",
                meta={"base_question_id": question_id},
            )
        )

        rows.append(
            make_row(
                row_id=f"synthetic_{question_id}_weird_distractor",
                question_id=f"synthetic_{question_id}",
                passage=passage,
                question=question,
                choice_no=2,
                choice=f"{distractor_text} 그리고 인터넷 방송으로 전국에 즉시 생중계되었다.",
                is_answer=False,
                label=0,
                error_codes=["WEIRD_DISTRACTOR"],
                source_type="synthetic",
                meta={"base_question_id": question_id},
            )
        )

        rows.append(
            make_row(
                row_id=f"synthetic_{question_id}_grammar_error",
                question_id=f"synthetic_{question_id}",
                passage=passage,
                question=question,
                choice_no=2,
                choice="고려는 하였다 왕권 강화 위해 제도.",
                is_answer=False,
                label=0,
                error_codes=["CHOICE_GRAMMAR_ERROR"],
                source_type="synthetic",
                meta={"base_question_id": question_id},
            )
        )

        rows.append(
            make_row(
                row_id=f"synthetic_{question_id}_style_mismatch",
                question_id=f"synthetic_{question_id}",
                passage=passage,
                question=question,
                choice_no=2,
                choice="정답은 위 자료에 모두 나와 있다.",
                is_answer=False,
                label=0,
                error_codes=["CHOICE_STYLE_MISMATCH"],
                source_type="synthetic",
                meta={"base_question_id": question_id},
            )
        )

        rows.append(
            make_row(
                row_id=f"synthetic_{question_id}_too_vague",
                question_id=f"synthetic_{question_id}",
                passage=passage,
                question=question,
                choice_no=2,
                choice="그것을 실시하였다.",
                is_answer=False,
                label=0,
                error_codes=["CHOICE_TOO_VAGUE"],
                source_type="synthetic",
                meta={"base_question_id": question_id},
            )
        )
    return rows


def main() -> None:
    past_raw = read_json(PAST_EXAM_SOURCE)
    generated_raw = read_json(GENERATED_SOURCE)

    past_rows = past_exam_rows(past_raw)
    yj_rows, generated_summary = generated_rows(generated_raw)
    synth_rows = synthetic_abnormal_rows(past_raw)

    rows = past_rows + yj_rows + synth_rows

    splits = {"train": [], "test": []}
    for row in rows:
        splits[split_name(row["question_id"])].append(row)

    write_json(OUT_DIR / "choice_quality_data.json", rows)
    write_json(OUT_DIR / "choice_quality_train.json", splits["train"])
    write_json(OUT_DIR / "choice_quality_test.json", splits["test"])

    summary = {
        "past_exam_source": str(PAST_EXAM_SOURCE),
        "generated_source": str(GENERATED_SOURCE),
        "past_exam_questions": len(past_raw),
        **generated_summary,
        "total_rows": len(rows),
        "source_type_count": {
            source: sum(1 for row in rows if row["source_type"] == source)
            for source in sorted({row["source_type"] for row in rows})
        },
        "label_count": {
            "0_error": sum(1 for row in rows if row["label"] == 0),
            "1_ok": sum(1 for row in rows if row["label"] == 1),
        },
        "error_code_count": {
            code: sum(1 for row in rows if code in row["error_codes"])
            for code in ERROR_KO
        },
        "split_count": {name: len(items) for name, items in splits.items()},
        "files": [
            "choice_quality_data.json",
            "choice_quality_train.json",
            "choice_quality_test.json",
        ],
    }
    write_json(OUT_DIR / "choice_quality_summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
