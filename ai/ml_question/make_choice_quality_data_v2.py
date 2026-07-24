from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from make_choice_quality_data import (
    ERROR_KO,
    GENERATED_SOURCE,
    OUT_DIR,
    PAST_EXAM_SOURCE,
    choice_is_answer,
    choice_text,
    duplicated_choice_numbers,
    make_row,
    normalize_text,
    past_exam_rows,
    read_json,
    split_name,
    synthetic_abnormal_rows,
    write_json,
)


RULE_ONLY_CODES = {
    "ANSWER_FORMAT_ERROR",
    "DUPLICATE_OR_SIMILAR_CHOICE",
}


def stable_question_id(q: dict[str, Any]) -> str:
    raw_id = q.get("seed_id") or q.get("id") or q.get("problem_id")
    if raw_id:
        return str(raw_id)
    payload = json.dumps(q, ensure_ascii=False, sort_keys=True)
    return hashlib.md5(payload.encode("utf-8")).hexdigest()[:12]


def extract_gate_errors(q: dict[str, Any]) -> tuple[set[str], list[str]]:
    validation = q.get("validation") or {}
    failed_gates = set(validation.get("failed_gates") or [])
    gate_errors: list[str] = []
    gates = ((validation.get("gate") or {}).get("gates") or {})
    for gate_data in gates.values():
        gate_errors.extend(str(error) for error in gate_data.get("errors") or [])
    return failed_gates, gate_errors


def generated_question_rows_v2(q: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, int]]:
    choices = q.get("choices") or []
    if len(choices) != 5:
        return [], {"skipped": 1, "skipped_choice_count": 1}

    question_id = stable_question_id(q)
    validation = q.get("validation") or {}
    failed_gates, gate_errors = extract_gate_errors(q)

    # 역사 사실성 gate는 현재 학습 목표에서 제외한다.
    if failed_gates.intersection({"G4", "G5"}):
        return [], {"skipped": 1, "skipped_history_gate": 1}

    answer_number = q.get("answer_number")
    try:
        answer_number = int(answer_number) if answer_number is not None else None
    except Exception:
        answer_number = None

    abnormal_by_choice: dict[int, list[str]] = {}
    rule_only_problem = False
    handled_gate_error = False

    if "answer_choice_repeats_material" in gate_errors:
        handled_gate_error = True
        for idx, choice in enumerate(choices, start=1):
            if choice_is_answer(choice, idx, answer_number):
                abnormal_by_choice.setdefault(idx, []).append("ANSWER_IN_PASSAGE")

    if "choice_has_malformed_predicate" in gate_errors:
        handled_gate_error = True
        marked = False
        for idx, choice in enumerate(choices, start=1):
            text = choice_text(choice)
            if "이며이다" in text or "했다이며" in text or "이다이며" in text:
                abnormal_by_choice.setdefault(idx, []).append("CHOICE_GRAMMAR_ERROR")
                marked = True
        if not marked and answer_number in {1, 2, 3, 4, 5}:
            abnormal_by_choice.setdefault(answer_number, []).append("CHOICE_GRAMMAR_ERROR")

    if "duplicate_choice" in gate_errors:
        handled_gate_error = True
        rule_only_problem = True
        for idx in duplicated_choice_numbers(choices):
            abnormal_by_choice.setdefault(idx, []).append("DUPLICATE_OR_SIMILAR_CHOICE")

    answer_count = sum(1 for idx, choice in enumerate(choices, start=1) if choice_is_answer(choice, idx, answer_number))
    if answer_count != 1:
        # 정답 표시 오류는 선지 문장만 보고 배우는 문제가 아니라 문제 단위 규칙으로 처리한다.
        rule_only_problem = True

    # runpod_generation_failed만 있는 경우는 선지별 학습 라벨로 해석하기 애매해서 제외한다.
    unhandled_failure = bool(failed_gates) and not handled_gate_error and not rule_only_problem
    if unhandled_failure:
        return [], {"skipped": 1, "skipped_unhandled_failure": 1}

    rows: list[dict[str, Any]] = []
    for idx, choice in enumerate(choices, start=1):
        errors = sorted(set(abnormal_by_choice.get(idx, [])))
        rows.append(
            make_row(
                row_id=f"generated_v2_{question_id}_choice_{idx}",
                question_id=f"generated_v2_{question_id}",
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
                    "gate_errors": gate_errors,
                    "rule_only_problem": rule_only_problem,
                },
            )
        )

    return rows, {"skipped": 0, "rule_only_problem": int(rule_only_problem)}


def generated_rows_v2(data: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, int]]:
    rows: list[dict[str, Any]] = []
    summary = {
        "generated_questions": len(data.get("questions", [])),
        "generated_skipped_questions": 0,
        "generated_rule_only_questions": 0,
        "generated_real_error_rows": 0,
    }
    for q in data.get("questions", []):
        q_rows, stats = generated_question_rows_v2(q)
        rows.extend(q_rows)
        summary["generated_skipped_questions"] += int(stats.get("skipped", 0))
        summary["generated_rule_only_questions"] += int(stats.get("rule_only_problem", 0))
    summary["generated_real_error_rows"] = sum(1 for row in rows if row["source_type"] == "generated" and row["label"] == 0)
    return rows, summary


def row_goes_to_train(row: dict[str, Any]) -> bool:
    # 실제 팀원 생성 오류는 수가 너무 적으므로 우선 학습에 포함한다.
    # 진짜 일반화 성능은 다음 팀원 생성 파일을 별도 테스트셋으로 받아 확인해야 한다.
    if row["source_type"] == "generated" and row["label"] == 0:
        return True
    return split_name(row["question_id"]) == "train"


def main() -> None:
    past_raw = read_json(PAST_EXAM_SOURCE)
    generated_raw = read_json(GENERATED_SOURCE)

    past_rows = past_exam_rows(past_raw)
    yj_rows, generated_summary = generated_rows_v2(generated_raw)
    synth_rows = synthetic_abnormal_rows(past_raw)

    rows = past_rows + yj_rows + synth_rows

    splits = {"train": [], "test": []}
    for row in rows:
        splits["train" if row_goes_to_train(row) else "test"].append(row)

    write_json(OUT_DIR / "choice_quality_data_v2.json", rows)
    write_json(OUT_DIR / "choice_quality_train_v2.json", splits["train"])
    write_json(OUT_DIR / "choice_quality_test_v2.json", splits["test"])

    error_codes = sorted({code for row in rows for code in row["error_codes"]})
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
            for code in error_codes
        },
        "rule_only_codes": sorted(RULE_ONLY_CODES),
        "split_count": {name: len(items) for name, items in splits.items()},
        "generated_error_rows_forced_to_train": sum(
            1 for row in splits["train"] if row["source_type"] == "generated" and row["label"] == 0
        ),
        "files": [
            "choice_quality_data_v2.json",
            "choice_quality_train_v2.json",
            "choice_quality_test_v2.json",
        ],
    }
    write_json(OUT_DIR / "choice_quality_summary_v2.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
