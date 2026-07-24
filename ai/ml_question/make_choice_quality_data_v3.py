from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from make_choice_quality_data import (
    OUT_DIR,
    PAST_EXAM_SOURCE,
    ERROR_KO,
    past_exam_rows,
    read_json,
    split_name,
    synthetic_abnormal_rows,
    write_json,
)
from make_choice_quality_data_v2 import GENERATED_SOURCE, generated_question_rows_v2


def generated_question_sort_key(q: dict[str, Any]) -> str:
    return str(q.get("seed_id") or q.get("id") or q.get("problem_id") or "")


def split_generated_questions(questions: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    # 팀원 생성 문제는 문항 단위로 holdout test를 만든다.
    # 같은 문항의 선지가 train/test에 섞이면 실전 검증이 되지 않는다.
    train_questions: list[dict[str, Any]] = []
    holdout_questions: list[dict[str, Any]] = []

    for q in sorted(questions, key=generated_question_sort_key):
        qid = generated_question_sort_key(q)
        if split_name(f"generated_holdout_{qid}") == "test":
            holdout_questions.append(q)
        else:
            train_questions.append(q)

    return train_questions, holdout_questions


def rows_from_generated_questions(questions: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, int]]:
    rows: list[dict[str, Any]] = []
    summary = {
        "questions": len(questions),
        "skipped_questions": 0,
        "real_error_rows": 0,
        "rule_only_questions": 0,
    }
    for q in questions:
        q_rows, stats = generated_question_rows_v2(q)
        rows.extend(q_rows)
        summary["skipped_questions"] += int(stats.get("skipped", 0))
        summary["rule_only_questions"] += int(stats.get("rule_only_problem", 0))
    summary["real_error_rows"] = sum(1 for row in rows if row["label"] == 0)
    return rows, summary


def main() -> None:
    past_raw = read_json(PAST_EXAM_SOURCE)
    generated_raw = read_json(GENERATED_SOURCE)
    generated_questions = generated_raw.get("questions", [])

    past_rows = past_exam_rows(past_raw)
    synthetic_rows = synthetic_abnormal_rows(past_raw)
    generated_train_questions, generated_holdout_questions = split_generated_questions(generated_questions)
    generated_train_rows, generated_train_summary = rows_from_generated_questions(generated_train_questions)
    generated_holdout_rows, generated_holdout_summary = rows_from_generated_questions(generated_holdout_questions)

    train_rows: list[dict[str, Any]] = []
    test_rows: list[dict[str, Any]] = []
    synthetic_test_rows: list[dict[str, Any]] = []

    for row in past_rows:
        (train_rows if split_name(row["question_id"]) == "train" else test_rows).append(row)

    for row in synthetic_rows:
        if split_name(row["question_id"]) == "train":
            train_rows.append(row)
        else:
            synthetic_test_rows.append(row)

    # 팀원 생성 holdout은 학습에 넣지 않는다. 이게 v3 테스트의 핵심이다.
    train_rows.extend(generated_train_rows)
    test_rows.extend(generated_holdout_rows)

    all_rows = train_rows + test_rows + synthetic_test_rows
    error_codes = sorted({code for row in all_rows for code in row.get("error_codes", [])})

    write_json(OUT_DIR / "choice_quality_train_v3.json", train_rows)
    write_json(OUT_DIR / "choice_quality_test_v3.json", test_rows)
    write_json(OUT_DIR / "choice_quality_synthetic_test_v3.json", synthetic_test_rows)
    write_json(OUT_DIR / "choice_quality_data_v3.json", all_rows)

    summary = {
        "version": "v3_holdout_generated_test",
        "past_exam_source": str(PAST_EXAM_SOURCE),
        "generated_source": str(GENERATED_SOURCE),
        "input_data": "passage + question + one choice + is_answer",
        "y_value": "multi-label error_codes. binary label is derived: error_codes empty => label=1 OK, non-empty => label=0 ERROR",
        "test_purpose": "학습에 넣지 않은 팀원 생성 문제 holdout에서 실제 오류 탐지 가능성을 확인한다.",
        "past_exam_questions": len(past_raw),
        "generated_questions": len(generated_questions),
        "generated_train_questions": generated_train_summary,
        "generated_holdout_questions": generated_holdout_summary,
        "total_rows": len(all_rows),
        "train_count": len(train_rows),
        "test_count": len(test_rows),
        "synthetic_test_count": len(synthetic_test_rows),
        "label_count": {
            "train_error_0": sum(1 for row in train_rows if row["label"] == 0),
            "train_ok_1": sum(1 for row in train_rows if row["label"] == 1),
            "test_error_0": sum(1 for row in test_rows if row["label"] == 0),
            "test_ok_1": sum(1 for row in test_rows if row["label"] == 1),
            "synthetic_test_error_0": sum(1 for row in synthetic_test_rows if row["label"] == 0),
            "synthetic_test_ok_1": sum(1 for row in synthetic_test_rows if row["label"] == 1),
        },
        "error_code_count": {
            code: {
                "train": sum(1 for row in train_rows if code in row.get("error_codes", [])),
                "test": sum(1 for row in test_rows if code in row.get("error_codes", [])),
                "synthetic_test": sum(1 for row in synthetic_test_rows if code in row.get("error_codes", [])),
            }
            for code in error_codes
        },
        "error_code_names_ko": {code: ERROR_KO.get(code, code) for code in error_codes},
        "files": [
            "choice_quality_train_v3.json",
            "choice_quality_test_v3.json",
            "choice_quality_synthetic_test_v3.json",
            "choice_quality_data_v3.json",
        ],
    }
    write_json(OUT_DIR / "choice_quality_summary_v3.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
