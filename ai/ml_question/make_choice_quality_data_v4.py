from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any


ROOT_DIR = Path(__file__).resolve().parents[2]
OUT_DIR = Path(__file__).resolve().parent

PAST_EXAM_SOURCE = ROOT_DIR / "ai" / "ml" / "ML_han_v1.json"
AUDIT_DIR = Path(r"C:\Users\Playdata\Downloads\사용불가_문항_1144_API전수검사")
FULL_AUDIT_SOURCE = AUDIT_DIR / "전체_1144문항_API_전수검사.json"
ADDITIONAL_AUDIT_SOURCE = AUDIT_DIR / "08_추가분류_511문항" / "추가분류_511문항_전체.json"

ERROR_KO = {
    "ANSWER_IN_PASSAGE": "정답 사실이 지문/질문에 노출됨",
    "ANSWER_LENGTH_BIAS": "정답 선지 길이 편향",
    "CHOICE_FORMAT_ERROR": "선지 형식/문장 완결성 오류",
    "DUPLICATE_OR_SIMILAR_CHOICE": "중복 또는 유사 선지",
    "NO_OR_MULTI_ANSWER": "정답이 없거나 복수 정답 가능",
    "QUESTION_FORMAT_ERROR": "발문/표식 형식 오류",
    "QUESTION_CHOICE_MISMATCH": "발문·지문·정답축 불일치",
    "WEIRD_CHOICE": "시험 문항 품질 부족 또는 부적절 선지",
}

FIRST_AUDIT_CATEGORY_MAP = {
    "ANSWER_FACT_EXPOSED": "ANSWER_IN_PASSAGE",
    "CHOICE_FORM": "CHOICE_FORMAT_ERROR",
    "SCRIPT_META": "CHOICE_FORMAT_ERROR",
    "MISSING_MARKER": "QUESTION_FORMAT_ERROR",
    "DUPLICATE_NONUNIQUE": "DUPLICATE_OR_SIMILAR_CHOICE",
}

ADDITIONAL_CATEGORY_MAP = {
    "STEM_CONTENT_MISMATCH": "QUESTION_CHOICE_MISMATCH",
    "LOW_EXAM_QUALITY": "WEIRD_CHOICE",
}

EXCLUDED_CATEGORIES = {
    "RUNPOD_FAILURE",
    "MATERIAL_EVIDENCE_REVIEW",
    "NO_MATCH",
    "EXPERIMENT_VARIANT_DUPLICATE",
    "CURRENT_PROVENANCE_GAP",
    "DIFFICULTY_MISMATCH",
    "MISSING_DIFFICULTY_METADATA",
}


def read_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8-sig") as f:
        return json.load(f)


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")


def split_name(group_id: str) -> str:
    digest = hashlib.md5(group_id.encode("utf-8")).hexdigest()
    return "train" if int(digest[:8], 16) % 100 < 80 else "test"


def stable_hash(data: Any, length: int = 12) -> str:
    payload = json.dumps(data, ensure_ascii=False, sort_keys=True)
    return hashlib.md5(payload.encode("utf-8")).hexdigest()[:length]


def safe_id(value: Any) -> str:
    text = str(value or "unknown")
    text = re.sub(r"[^0-9A-Za-z가-힣_.:-]+", "_", text)
    return text[:80] or "unknown"


def normalize_text(text: Any) -> str:
    return re.sub(r"\s+", "", str(text or "")).lower()


def choice_text(choice: Any) -> str:
    if isinstance(choice, dict):
        return str(choice.get("content") or choice.get("text") or "").strip()
    return str(choice or "").strip()


def answer_number(q: dict[str, Any]) -> int | None:
    for key in ("answer_number", "answer", "answer_index"):
        value = q.get(key)
        if value is None or value == "":
            continue
        try:
            number = int(value)
        except (TypeError, ValueError):
            continue
        if key == "answer_index" and 0 <= number <= 4:
            return number + 1
        return number
    return None


def choice_number(choice: Any, fallback: int) -> int:
    if isinstance(choice, dict):
        try:
            return int(choice.get("number"))
        except (TypeError, ValueError):
            return fallback
    return fallback


def choice_is_answer(choice: Any, idx: int, answer_no: int | None = None) -> bool:
    if isinstance(choice, dict) and "is_answer" in choice:
        return bool(choice.get("is_answer"))
    return answer_no == idx


def answer_choice_numbers(q: dict[str, Any]) -> set[int]:
    answer_no = answer_number(q)
    numbers: set[int] = set()
    for idx, choice in enumerate(q.get("choices") or [], start=1):
        number = choice_number(choice, idx)
        if choice_is_answer(choice, number, answer_no):
            numbers.add(number)
    if answer_no in {1, 2, 3, 4, 5}:
        numbers.add(int(answer_no))
    return numbers


def mentioned_choice_numbers(reason: str) -> set[int]:
    numbers = {int(value) for value in re.findall(r"([1-5])\s*번", str(reason or ""))}
    return {number for number in numbers if 1 <= number <= 5}


def marker_choice_numbers(q: dict[str, Any]) -> set[int]:
    pattern = re.compile(r"\([가-힣A-Za-z]\)|밑줄|표지|자료\s*[가-힣A-Za-z]")
    numbers: set[int] = set()
    for idx, choice in enumerate(q.get("choices") or [], start=1):
        if pattern.search(choice_text(choice)):
            numbers.add(choice_number(choice, idx))
    return numbers


def duplicated_choice_numbers(q: dict[str, Any]) -> set[int]:
    seen: dict[str, int] = {}
    duplicated: set[int] = set()
    for idx, choice in enumerate(q.get("choices") or [], start=1):
        number = choice_number(choice, idx)
        norm = normalize_text(choice_text(choice))
        if not norm:
            continue
        if norm in seen:
            duplicated.add(seen[norm])
            duplicated.add(number)
        else:
            seen[norm] = number
    return duplicated


def question_fingerprint(q: dict[str, Any]) -> str:
    compact = {
        "seed_id": q.get("seed_id"),
        "_source_file": q.get("_source_file"),
        "material": q.get("material"),
        "question": q.get("question"),
        "answer_number": q.get("answer_number"),
        "choices": [choice_text(choice) for choice in q.get("choices") or []],
    }
    return stable_hash(compact, length=16)


def label_name(label: int) -> str:
    return "OK" if label == 1 else "ERROR"


def make_row(
    *,
    row_id: str,
    question_id: str,
    split_group: str,
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
        "split_group": split_group,
        "passage": passage,
        "question": question,
        "choice_no": choice_no,
        "choice": choice,
        "is_answer": 1 if is_answer else 0,
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
        problem_id = str(raw.get("problem_id") or stable_hash(raw))
        question_id = f"past_{problem_id}"
        answer_no = answer_number(raw)
        for idx, choice in enumerate(choices, start=1):
            number = choice_number(choice, idx)
            rows.append(
                make_row(
                    row_id=f"{question_id}_choice_{number}",
                    question_id=question_id,
                    split_group=question_id,
                    passage=str(raw.get("material") or ""),
                    question=str(raw.get("question") or ""),
                    choice_no=number,
                    choice=choice_text(choice),
                    is_answer=choice_is_answer(choice, number, answer_no),
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


def add_error(abnormal_by_choice: dict[int, set[str]], numbers: set[int], code: str) -> None:
    for number in numbers:
        if 1 <= number <= 5:
            abnormal_by_choice.setdefault(number, set()).add(code)


def target_numbers_for_first_category(category: str, reason: str, q: dict[str, Any]) -> set[int]:
    mentioned = mentioned_choice_numbers(reason)
    answers = answer_choice_numbers(q)

    if category == "ANSWER_FACT_EXPOSED":
        return answers
    if category in {"CHOICE_FORM", "SCRIPT_META"}:
        return mentioned or answers
    if category == "MISSING_MARKER":
        return mentioned or marker_choice_numbers(q) or set(range(1, 6))
    if category == "DUPLICATE_NONUNIQUE":
        return mentioned or duplicated_choice_numbers(q) or set(range(1, 6))
    return set()


def target_numbers_for_additional_category(category: str, reason: str, q: dict[str, Any]) -> set[int]:
    mentioned = mentioned_choice_numbers(reason)
    answers = answer_choice_numbers(q)

    if category == "STEM_CONTENT_MISMATCH":
        return mentioned or answers or set(range(1, 6))
    if category == "LOW_EXAM_QUALITY":
        # 변별력 부족은 특정 선지 하나보다 문항 전체 품질 문제인 경우가 많아 약한 라벨로 전체 선지에 부여한다.
        return mentioned or set(range(1, 6))
    return set()


def generated_question_rows(
    item: dict[str, Any],
    additional_item: dict[str, Any] | None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    q = item.get("question") or {}
    audit = item.get("audit") or {}
    choices = q.get("choices") or []
    if len(choices) != 5:
        return [], {"skipped": 1, "reason": "choice_count"}

    categories = list(audit.get("categories") or [])
    reasons = audit.get("reasons") or {}
    additional_category = None
    additional_reason = ""
    if additional_item:
        additional_audit = additional_item.get("audit") or {}
        additional_category = additional_audit.get("category")
        additional_reason = str(additional_audit.get("reason") or "")

    abnormal_by_choice: dict[int, set[str]] = {}
    used_categories: list[str] = []
    excluded_categories: list[str] = []
    weak_label = False

    answer_count = len(answer_choice_numbers(q))
    if answer_count != 1:
        add_error(abnormal_by_choice, set(range(1, 6)), "NO_OR_MULTI_ANSWER")
        used_categories.append("answer_count_rule")

    for category in categories:
        if category in FIRST_AUDIT_CATEGORY_MAP:
            code = FIRST_AUDIT_CATEGORY_MAP[category]
            reason = str(reasons.get(category) or "")
            targets = target_numbers_for_first_category(category, reason, q)
            add_error(abnormal_by_choice, targets, code)
            if category == "DUPLICATE_NONUNIQUE":
                add_error(abnormal_by_choice, targets, "NO_OR_MULTI_ANSWER")
            used_categories.append(category)
        elif category in EXCLUDED_CATEGORIES:
            excluded_categories.append(category)

    if additional_category in ADDITIONAL_CATEGORY_MAP:
        code = ADDITIONAL_CATEGORY_MAP[str(additional_category)]
        targets = target_numbers_for_additional_category(str(additional_category), additional_reason, q)
        add_error(abnormal_by_choice, targets, code)
        used_categories.append(str(additional_category))
        weak_label = str(additional_category) == "LOW_EXAM_QUALITY"
    elif additional_category in EXCLUDED_CATEGORIES:
        excluded_categories.append(str(additional_category))

    if not abnormal_by_choice:
        return [], {
            "skipped": 1,
            "reason": "no_choice_supervision",
            "categories": categories,
            "additional_category": additional_category,
        }

    fp = question_fingerprint(q)
    seed_id = q.get("seed_id") or fp
    source_hash = stable_hash(q.get("_source_file") or "", length=8)
    question_id = f"generated_v4_{safe_id(seed_id)}_{source_hash}_{fp[:8]}"
    split_group = f"generated_seed_{safe_id(seed_id)}"
    answer_no = answer_number(q)

    rows: list[dict[str, Any]] = []
    for idx, choice in enumerate(choices, start=1):
        number = choice_number(choice, idx)
        error_codes = sorted(abnormal_by_choice.get(number, set()))
        rows.append(
            make_row(
                row_id=f"{question_id}_choice_{number}",
                question_id=question_id,
                split_group=split_group,
                passage=str(q.get("material") or ""),
                question=str(q.get("question") or ""),
                choice_no=number,
                choice=choice_text(choice),
                is_answer=choice_is_answer(choice, number, answer_no),
                label=0 if error_codes else 1,
                error_codes=error_codes,
                source_type="generated_audit",
                meta={
                    "seed_id": q.get("seed_id"),
                    "topic": q.get("topic"),
                    "question_task": q.get("question_task"),
                    "difficulty_label": q.get("difficulty_label"),
                    "audit_categories": categories,
                    "audit_reasons": reasons,
                    "additional_category": additional_category,
                    "additional_reason": additional_reason,
                    "used_categories": used_categories,
                    "excluded_categories": sorted(set(excluded_categories)),
                    "weak_label": weak_label,
                    "source_file": q.get("_source_file"),
                    "audit_confidence": audit.get("confidence"),
                },
            )
        )

    return rows, {
        "skipped": 0,
        "used_categories": used_categories,
        "excluded_categories": excluded_categories,
        "weak_label": int(weak_label),
    }


def build_additional_lookup(additional_raw: dict[str, Any]) -> dict[str, dict[str, Any]]:
    lookup: dict[str, dict[str, Any]] = {}
    for item in additional_raw.get("results") or []:
        q = item.get("question") or {}
        lookup[question_fingerprint(q)] = item
    return lookup


def generated_rows(
    full_raw: dict[str, Any],
    additional_raw: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    skipped_items: list[dict[str, Any]] = []
    additional_lookup = build_additional_lookup(additional_raw)

    summary: dict[str, Any] = {
        "source_questions": len(full_raw.get("results") or []),
        "supervised_questions": 0,
        "skipped_questions": 0,
        "weak_label_questions": 0,
        "used_category_count": {},
        "excluded_category_count": {},
        "skip_reason_count": {},
    }

    for item in full_raw.get("results") or []:
        q = item.get("question") or {}
        additional_item = additional_lookup.get(question_fingerprint(q))
        q_rows, stats = generated_question_rows(item, additional_item)
        if stats.get("skipped"):
            summary["skipped_questions"] += 1
            reason = str(stats.get("reason") or "unknown")
            summary["skip_reason_count"][reason] = summary["skip_reason_count"].get(reason, 0) + 1
            skipped_items.append({"audit": item.get("audit"), "additional_audit": (additional_item or {}).get("audit"), "question": q, "skip": stats})
            continue

        rows.extend(q_rows)
        summary["supervised_questions"] += 1
        summary["weak_label_questions"] += int(stats.get("weak_label", 0))
        for category in stats.get("used_categories", []):
            summary["used_category_count"][category] = summary["used_category_count"].get(category, 0) + 1
        for category in stats.get("excluded_categories", []):
            summary["excluded_category_count"][category] = summary["excluded_category_count"].get(category, 0) + 1

    return rows, skipped_items, summary


def split_rows(rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    splits = {"train": [], "test": []}
    for row in rows:
        group = str(row.get("split_group") or row["question_id"])
        splits[split_name(group)].append(row)
    return splits


def count_by(rows: list[dict[str, Any]], key: str) -> dict[str, int]:
    result: dict[str, int] = {}
    for row in rows:
        value = str(row.get(key, "unknown"))
        result[value] = result.get(value, 0) + 1
    return dict(sorted(result.items()))


def error_code_count(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        for code in row.get("error_codes") or []:
            counts[code] = counts.get(code, 0) + 1
    return dict(sorted(counts.items()))


def raw_full_category_count(full_raw: dict[str, Any]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in full_raw.get("results") or []:
        for category in (item.get("audit") or {}).get("categories") or []:
            counts[str(category)] = counts.get(str(category), 0) + 1
    return dict(sorted(counts.items()))


def raw_additional_category_count(additional_raw: dict[str, Any]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in additional_raw.get("results") or []:
        category = (item.get("audit") or {}).get("category")
        if category:
            counts[str(category)] = counts.get(str(category), 0) + 1
    return dict(sorted(counts.items()))


def label_count(rows: list[dict[str, Any]]) -> dict[str, int]:
    return {
        "0_error": sum(1 for row in rows if row["label"] == 0),
        "1_ok": sum(1 for row in rows if row["label"] == 1),
    }


def main() -> None:
    past_raw = read_json(PAST_EXAM_SOURCE)
    full_raw = read_json(FULL_AUDIT_SOURCE)
    additional_raw = read_json(ADDITIONAL_AUDIT_SOURCE)

    past_rows = past_exam_rows(past_raw)
    audit_rows, skipped_items, generated_summary = generated_rows(full_raw, additional_raw)
    all_rows = past_rows + audit_rows
    splits = split_rows(all_rows)

    write_json(OUT_DIR / "choice_quality_data_v4.json", all_rows)
    write_json(OUT_DIR / "choice_quality_train_v4.json", splits["train"])
    write_json(OUT_DIR / "choice_quality_test_v4.json", splits["test"])
    write_json(OUT_DIR / "choice_quality_skipped_v4.json", skipped_items)

    error_codes = sorted({code for row in all_rows for code in row.get("error_codes", [])})
    summary = {
        "version": "v4_api_audit_1144",
        "purpose": "기출 정상 선지와 API 전수검사로 사용불가 판정된 팀원 생성 문항을 이용해 선지 단위 2차 검수 모델을 학습한다.",
        "input_data": "passage/material + question + one choice + is_answer",
        "y_value": "binary label. label=0이면 이상, label=1이면 정상. error_codes는 오류 사유 표시용 보조 정보다.",
        "why": "문제 생성 파이프라인이 통과시킨 문항 중 정답 노출, 선지 형식 오류, 정답 유일성 오류 등을 2차 보안 장치로 잡기 위함.",
        "past_exam_source": str(PAST_EXAM_SOURCE),
        "full_audit_source": str(FULL_AUDIT_SOURCE),
        "additional_audit_source": str(ADDITIONAL_AUDIT_SOURCE),
        "raw_full_category_count": raw_full_category_count(full_raw),
        "raw_additional_category_count": raw_additional_category_count(additional_raw),
        "past_exam_questions": len(past_raw),
        "past_exam_rows": len(past_rows),
        "generated_audit_summary": generated_summary,
        "total_rows": len(all_rows),
        "split_count": {name: len(items) for name, items in splits.items()},
        "label_count": label_count(all_rows),
        "split_label_count": {name: label_count(items) for name, items in splits.items()},
        "source_type_count": count_by(all_rows, "source_type"),
        "error_code_count": error_code_count(all_rows),
        "split_error_code_count": {name: error_code_count(items) for name, items in splits.items()},
        "error_code_names_ko": {code: ERROR_KO.get(code, code) for code in error_codes},
        "excluded_categories": sorted(EXCLUDED_CATEGORIES),
        "files": [
            "choice_quality_train_v4.json",
            "choice_quality_test_v4.json",
            "choice_quality_data_v4.json",
            "choice_quality_skipped_v4.json",
        ],
    }
    write_json(OUT_DIR / "choice_quality_summary_v4.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
