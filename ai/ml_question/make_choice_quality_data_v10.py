from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any


# v10 목적:
# - 문항 전체가 아니라 "선지 1개"의 오류 유무를 학습한다.
# - BERT가 선지 단위로 판단 가능한 오류만 label=0으로 사용한다.
# - 중복 선지/복수 정답처럼 선지 5개 비교가 필요한 오류는 규칙/후처리 대상으로 분리한다.
# - 운영 결과에서 문항 단위 규칙 검사를 함께 볼 수 있도록 선지 5개 요약 정보를 row에 포함한다.

ROOT_DIR = Path(__file__).resolve().parents[2]
OUT_DIR = Path(__file__).resolve().parent

PAST_EXAM_SOURCE = ROOT_DIR / "ai" / "ml" / "ML_han_v1.json"
AUDIT_DIR = Path(r"C:\Users\Playdata\Downloads\사용불가_문항_1144_API전수검사")
FULL_AUDIT_SOURCE = AUDIT_DIR / "전체_1144문항_API_전수검사.json"
ADDITIONAL_AUDIT_SOURCE = AUDIT_DIR / "08_추가분류_511문항" / "추가분류_511문항_전체.json"


BERT_ERROR_KO = {
    "ANSWER_IN_PASSAGE": "정답 선지가 지문/질문에 노출됨",
    "ANSWER_LENGTH_BIAS": "정답 선지가 유독 길거나 짧음",
    "CHOICE_FORMAT_ERROR": "선지 문장/형식 오류",
    "QUESTION_MARKER_MISMATCH": "지문에 없는 표식/밑줄/(가) 등을 참조함",
    "WEIRD_CHOICE": "선지가 문제 맥락상 너무 이상하거나 부적절함",
}

RULE_ONLY_ERROR_KO = {
    "DUPLICATE_OR_SIMILAR_CHOICE": "중복되거나 거의 같은 선지가 있음",
    "NO_OR_MULTI_ANSWER": "정답이 없거나 2개 이상임",
}

ALL_ERROR_KO = {**BERT_ERROR_KO, **RULE_ONLY_ERROR_KO}


# API 전수검사 원본 카테고리 중 BERT 선지 모델 학습에 사용할 것만 매핑한다.
BERT_CATEGORY_MAP = {
    "ANSWER_FACT_EXPOSED": "ANSWER_IN_PASSAGE",
    "CHOICE_FORM": "CHOICE_FORMAT_ERROR",
    "SCRIPT_META": "CHOICE_FORMAT_ERROR",
    "MISSING_MARKER": "QUESTION_MARKER_MISMATCH",
}

# BERT가 직접 학습하지 않고 규칙/후처리에서 처리할 카테고리다.
RULE_ONLY_CATEGORY_MAP = {
    "DUPLICATE_NONUNIQUE": ["DUPLICATE_OR_SIMILAR_CHOICE", "NO_OR_MULTI_ANSWER"],
}

# 선지 단위 오류로 보기 어려운 문항 단위/메타데이터 오류는 학습에서 제외한다.
EXCLUDED_CATEGORIES = {
    "RUNPOD_FAILURE",
    "MATERIAL_EVIDENCE_REVIEW",
    "NO_MATCH",
    "EXPERIMENT_VARIANT_DUPLICATE",
    "CURRENT_PROVENANCE_GAP",
    "DIFFICULTY_MISMATCH",
    "MISSING_DIFFICULTY_METADATA",
    "STEM_CONTENT_MISMATCH",
    "LOW_EXAM_QUALITY",
}


def read_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8-sig") as f:
        return json.load(f)


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")


def stable_hash(data: Any, length: int = 12) -> str:
    payload = json.dumps(data, ensure_ascii=False, sort_keys=True)
    return hashlib.md5(payload.encode("utf-8")).hexdigest()[:length]


def split_name(group_id: str) -> str:
    # question_id 기준으로 train/test를 고정 분리한다.
    digest = hashlib.md5(group_id.encode("utf-8")).hexdigest()
    return "train" if int(digest[:8], 16) % 100 < 80 else "test"


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


def choice_number(choice: Any, fallback: int) -> int:
    if isinstance(choice, dict):
        try:
            return int(choice.get("number"))
        except (TypeError, ValueError):
            return fallback
    return fallback


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


def choice_is_answer(choice: Any, number: int, answer_no: int | None = None) -> bool:
    if isinstance(choice, dict) and "is_answer" in choice:
        return bool(choice.get("is_answer"))
    return answer_no == number


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
    # 예: "3번 선택지", "4번의 표현" 같은 문장에서 선지 번호를 추출한다.
    numbers = {int(value) for value in re.findall(r"([1-5])\s*번", str(reason or ""))}
    return {number for number in numbers if 1 <= number <= 5}


def is_option_combo_text(text: Any) -> bool:
    # 보기형 문제의 "ㄱ, ㄴ", "ㄷ, ㄹ" 같은 조합 선지는 짧아도 정상일 수 있다.
    return bool(re.fullmatch(r"\s*[ㄱ-ㅎ](\s*[,·ㆍ]\s*[ㄱ-ㅎ])+\s*", str(text or "")))


def is_marker_only_text(text: Any) -> bool:
    # 연표/지도형 문제의 "(가)", "(나)", "㉠", "㉡" 같은 위치 선택 선지는 짧아도 정상일 수 있다.
    value = str(text or "").strip()
    return bool(re.fullmatch(r"\([가-힣A-Za-z]\)|[㉠-㉻]", value))


def allows_short_choice(q: dict[str, Any], choice: Any) -> bool:
    question = str(q.get("question") or "")
    text = choice_text(choice)
    if is_option_combo_text(text):
        return True
    if is_marker_only_text(text) and re.search(r"연표|시기|지도|지역|찾은|고른|위치", question):
        return True
    if re.search(r"보기.*고른|<보기>|＜보기＞|퀴즈|들어갈 내용", question) and len(text.strip()) <= 8:
        return True
    return False


def marker_choice_numbers(q: dict[str, Any]) -> set[int]:
    # 선지가 지문에 없는 (가), (나), 밑줄 등 표식을 참조하는지 찾는다.
    pattern = re.compile(r"\([가-힣A-Za-z]\)|밑줄|표식|표지")
    numbers: set[int] = set()
    for idx, choice in enumerate(q.get("choices") or [], start=1):
        if allows_short_choice(q, choice):
            continue
        if pattern.search(choice_text(choice)):
            numbers.add(choice_number(choice, idx))
    return numbers


def meta_choice_numbers(q: dict[str, Any]) -> set[int]:
    # 선지가 해설/풀이 문장처럼 보이는 표현을 포함하는지 찾는다.
    pattern = re.compile(r"근거로|풀이|정답|오답|선택지|자료를 보면|해야 한다")
    numbers: set[int] = set()
    for idx, choice in enumerate(q.get("choices") or [], start=1):
        if pattern.search(choice_text(choice)):
            numbers.add(choice_number(choice, idx))
    return numbers


def short_answer_choice_numbers(q: dict[str, Any]) -> set[int]:
    # 정답 선지가 지나치게 짧은 경우를 길이 편향 후보로 본다.
    # 다른 선지 평균과 비교하는 엄밀한 규칙은 후속 개선 대상이다.
    numbers: set[int] = set()
    answers = answer_choice_numbers(q)
    for idx, choice in enumerate(q.get("choices") or [], start=1):
        number = choice_number(choice, idx)
        if allows_short_choice(q, choice):
            continue
        if number in answers and len(choice_text(choice)) <= 8:
            numbers.add(number)
    return numbers


def length_bias_choice_numbers(q: dict[str, Any]) -> set[int]:
    # 정답 선지가 다른 선지들보다 유독 짧거나 긴 경우를 찾는다.
    # 절대 길이가 아니라 정답을 제외한 다른 선지들의 평균/중앙값과 비교한다.
    choices = q.get("choices") or []
    answers = answer_choice_numbers(q)
    numbers: set[int] = set()
    for idx, choice in enumerate(choices, start=1):
        number = choice_number(choice, idx)
        if number not in answers:
            continue
        if allows_short_choice(q, choice):
            continue
        length = len(choice_text(choice).strip())
        other_lengths = [
            len(choice_text(other_choice).strip())
            for other_idx, other_choice in enumerate(choices, start=1)
            if choice_number(other_choice, other_idx) != number and len(choice_text(other_choice).strip()) > 0
        ]
        if len(other_lengths) < 4:
            continue
        avg_length = sum(other_lengths) / len(other_lengths)
        median_length = sorted(other_lengths)[len(other_lengths) // 2]
        if avg_length <= 0 or median_length <= 0:
            continue
        too_short = length <= median_length * 0.45 and avg_length - length >= 10
        too_long = length >= median_length * 2.2 and length - avg_length >= 25
        if too_short or too_long:
            numbers.add(number)
    return numbers


def duplicated_choice_numbers(q: dict[str, Any]) -> set[int]:
    # 규칙/후처리용: 완전히 같은 선지를 찾는다.
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


def weakly_duplicated_choice_numbers(q: dict[str, Any]) -> set[int]:
    # 완전 동일은 아니어도 한쪽 문장이 다른 쪽을 거의 포함하면 운영 규칙 후보로 잡는다.
    items: list[tuple[int, str]] = []
    for idx, choice in enumerate(q.get("choices") or [], start=1):
        number = choice_number(choice, idx)
        norm = normalize_text(choice_text(choice))
        if len(norm) >= 8:
            items.append((number, norm))

    duplicated: set[int] = set()
    for left_idx, (left_no, left_text) in enumerate(items):
        for right_no, right_text in items[left_idx + 1 :]:
            shorter, longer = sorted([left_text, right_text], key=len)
            if shorter and shorter in longer and len(shorter) / max(len(longer), 1) >= 0.72:
                duplicated.update({left_no, right_no})
    return duplicated


def question_rule_codes(q: dict[str, Any]) -> list[str]:
    # 선지 5개를 함께 봐야 하는 운영용 규칙 검사다. BERT 학습 y에는 직접 섞지 않는다.
    codes: set[str] = set()
    if len(answer_choice_numbers(q)) != 1:
        codes.add("NO_OR_MULTI_ANSWER")
    if duplicated_choice_numbers(q) or weakly_duplicated_choice_numbers(q):
        codes.add("DUPLICATE_OR_SIMILAR_CHOICE")
    return sorted(codes)


def choice_context(q: dict[str, Any]) -> dict[str, Any]:
    # 결과 CSV에서 문항 단위 허점을 같이 확인할 수 있도록 row마다 요약 정보를 넣는다.
    choices = q.get("choices") or []
    choice_items = [
        {
            "number": choice_number(choice, idx),
            "text": choice_text(choice),
            "is_answer": 1 if choice_is_answer(choice, choice_number(choice, idx), answer_number(q)) else 0,
            "length": len(choice_text(choice)),
        }
        for idx, choice in enumerate(choices, start=1)
    ]
    return {
        "all_choices": choice_items,
        "answer_numbers": sorted(answer_choice_numbers(q)),
        "answer_count": len(answer_choice_numbers(q)),
        "question_rule_codes": question_rule_codes(q),
    }


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
    context: dict[str, Any] | None = None,
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
        "error_names_ko": [ALL_ERROR_KO.get(code, code) for code in error_codes],
        "source_type": source_type,
        "meta": meta or {},
        "context": context or {},
    }


def past_exam_rows(raw_items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    # 기출 선지는 모두 정상(label=1) 데이터로 사용한다.
    rows: list[dict[str, Any]] = []
    for raw in raw_items:
        choices = raw.get("choices") or []
        if len(choices) != 5:
            continue
        problem_id = str(raw.get("problem_id") or stable_hash(raw))
        question_id = f"past_{problem_id}"
        answer_no = answer_number(raw)
        context = choice_context(raw)
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
                    context=context,
                )
            )
    return rows


def add_error(abnormal_by_choice: dict[int, set[str]], numbers: set[int], code: str) -> None:
    for number in numbers:
        if 1 <= number <= 5:
            abnormal_by_choice.setdefault(number, set()).add(code)


def target_numbers_for_category(category: str, reason: str, q: dict[str, Any]) -> set[int]:
    mentioned = mentioned_choice_numbers(reason)
    answers = answer_choice_numbers(q)

    if category == "ANSWER_FACT_EXPOSED":
        return answers
    if category == "CHOICE_FORM":
        return mentioned or short_answer_choice_numbers(q)
    if category == "SCRIPT_META":
        return mentioned or meta_choice_numbers(q)
    if category == "MISSING_MARKER":
        return mentioned or marker_choice_numbers(q)
    return set()


def collect_rule_only(q: dict[str, Any], categories: list[str], reasons: dict[str, Any]) -> dict[str, Any] | None:
    # BERT 학습에는 넣지 않지만 운영에서 규칙으로 처리할 수 있는 정보를 별도로 저장한다.
    rule_codes: set[str] = set()
    if "DUPLICATE_NONUNIQUE" in categories:
        rule_codes.update(RULE_ONLY_CATEGORY_MAP["DUPLICATE_NONUNIQUE"])
    duplicate_numbers = duplicated_choice_numbers(q) | weakly_duplicated_choice_numbers(q)
    if duplicate_numbers:
        rule_codes.add("DUPLICATE_OR_SIMILAR_CHOICE")

    answer_count = len(answer_choice_numbers(q))
    if answer_count != 1:
        rule_codes.add("NO_OR_MULTI_ANSWER")

    if not rule_codes:
        return None

    return {
        "question_id": str(q.get("seed_id") or question_fingerprint(q)),
        "error_codes": sorted(rule_codes),
        "duplicate_choice_numbers": sorted(duplicate_numbers),
        "answer_count": answer_count,
        "audit_categories": categories,
        "audit_reasons": reasons,
    }


def generated_question_rows(q: dict[str, Any], audit: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    choices = q.get("choices") or []
    if len(choices) != 5:
        return [], {"skipped": 1, "reason": "choice_count"}

    categories = list(audit.get("categories") or [])
    reasons = audit.get("reasons") or {}

    abnormal_by_choice: dict[int, set[str]] = {}
    used_categories: list[str] = []
    excluded_categories: list[str] = []
    rule_only_categories: list[str] = []

    for category in categories:
        if category in BERT_CATEGORY_MAP:
            code = BERT_CATEGORY_MAP[category]
            targets = target_numbers_for_category(category, str(reasons.get(category) or ""), q)
            if targets:
                add_error(abnormal_by_choice, targets, code)
                used_categories.append(category)
            else:
                excluded_categories.append(f"{category}:no_choice_target")
        elif category in RULE_ONLY_CATEGORY_MAP:
            rule_only_categories.append(category)
        elif category in EXCLUDED_CATEGORIES:
            excluded_categories.append(category)

    # API 카테고리에 없더라도 정답 선지가 다른 선지보다 유독 짧거나 길면 선지 단위 오류 후보로 추가한다.
    biased_answers = length_bias_choice_numbers(q)
    if biased_answers:
        add_error(abnormal_by_choice, biased_answers, "ANSWER_LENGTH_BIAS")
        used_categories.append("local_answer_length_bias")

    # 학습 가능한 선지 오류가 하나도 없으면, 이 문항은 BERT 선지 학습에서 제외한다.
    if not abnormal_by_choice:
        return [], {
            "skipped": 1,
            "reason": "no_bert_choice_error",
            "categories": categories,
            "rule_only_categories": rule_only_categories,
            "excluded_categories": excluded_categories,
        }

    fp = question_fingerprint(q)
    seed_id = q.get("seed_id") or fp
    source_hash = stable_hash(q.get("_source_file") or "", length=8)
    question_id = f"generated_v10_{safe_id(seed_id)}_{source_hash}_{fp[:8]}"
    answer_no = answer_number(q)
    context = choice_context(q)

    rows: list[dict[str, Any]] = []
    for idx, choice in enumerate(choices, start=1):
        number = choice_number(choice, idx)
        error_codes = sorted(abnormal_by_choice.get(number, set()))
        rows.append(
            make_row(
                row_id=f"{question_id}_choice_{number}",
                question_id=question_id,
                split_group=f"generated_seed_{safe_id(seed_id)}",
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
                    "used_categories": sorted(set(used_categories)),
                    "rule_only_categories": sorted(set(rule_only_categories)),
                    "excluded_categories": sorted(set(excluded_categories)),
                    "source_file": q.get("_source_file"),
                    "audit_confidence": audit.get("confidence"),
                    "question_rule_codes": context.get("question_rule_codes", []),
                },
                context=context,
            )
        )

    return rows, {
        "skipped": 0,
        "used_categories": used_categories,
        "rule_only_categories": rule_only_categories,
        "excluded_categories": excluded_categories,
    }


def generated_rows(full_raw: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    skipped_items: list[dict[str, Any]] = []
    rule_only_items: list[dict[str, Any]] = []
    summary: dict[str, Any] = {
        "source_questions": len(full_raw.get("results") or []),
        "bert_supervised_questions": 0,
        "skipped_questions": 0,
        "rule_only_questions": 0,
        "used_category_count": {},
        "rule_only_category_count": {},
        "excluded_category_count": {},
        "skip_reason_count": {},
    }

    for item in full_raw.get("results") or []:
        q = item.get("question") or {}
        audit = item.get("audit") or {}
        q_rows, stats = generated_question_rows(q, audit)
        rule_only = collect_rule_only(q, list(audit.get("categories") or []), audit.get("reasons") or {})
        if rule_only:
            rule_only_items.append(rule_only)
            summary["rule_only_questions"] += 1

        if stats.get("skipped"):
            summary["skipped_questions"] += 1
            reason = str(stats.get("reason") or "unknown")
            summary["skip_reason_count"][reason] = summary["skip_reason_count"].get(reason, 0) + 1
            skipped_items.append({"audit": audit, "question": q, "skip": stats, "rule_only": rule_only})
            continue

        rows.extend(q_rows)
        summary["bert_supervised_questions"] += 1
        for category in stats.get("used_categories", []):
            summary["used_category_count"][category] = summary["used_category_count"].get(category, 0) + 1
        for category in stats.get("rule_only_categories", []):
            summary["rule_only_category_count"][category] = summary["rule_only_category_count"].get(category, 0) + 1
        for category in stats.get("excluded_categories", []):
            summary["excluded_category_count"][category] = summary["excluded_category_count"].get(category, 0) + 1

    return rows, skipped_items, rule_only_items, summary


def split_rows(rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    splits = {"train": [], "test": []}
    for row in rows:
        group = str(row.get("split_group") or row["question_id"])
        splits[split_name(group)].append(row)
    return splits


def label_count(rows: list[dict[str, Any]]) -> dict[str, int]:
    return {
        "0_error": sum(1 for row in rows if row["label"] == 0),
        "1_ok": sum(1 for row in rows if row["label"] == 1),
    }


def source_type_count(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        source_type = str(row.get("source_type") or "unknown")
        counts[source_type] = counts.get(source_type, 0) + 1
    return dict(sorted(counts.items()))


def error_code_count(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        for code in row.get("error_codes") or []:
            counts[code] = counts.get(code, 0) + 1
    return dict(sorted(counts.items()))


def main() -> None:
    past_raw = read_json(PAST_EXAM_SOURCE)
    full_raw = read_json(FULL_AUDIT_SOURCE)
    additional_raw = read_json(ADDITIONAL_AUDIT_SOURCE)

    past_rows = past_exam_rows(past_raw)
    generated_bert_rows, skipped_items, rule_only_items, generated_summary = generated_rows(full_raw)
    all_rows = past_rows + generated_bert_rows
    splits = split_rows(all_rows)

    write_json(OUT_DIR / "choice_quality_data_v10.json", all_rows)
    write_json(OUT_DIR / "choice_quality_train_v10.json", splits["train"])
    write_json(OUT_DIR / "choice_quality_test_v10.json", splits["test"])
    write_json(OUT_DIR / "choice_quality_skipped_v10.json", skipped_items)
    write_json(OUT_DIR / "choice_quality_rule_only_v10.json", rule_only_items)

    error_codes = sorted({code for row in all_rows for code in row.get("error_codes", [])})
    summary = {
        "version": "v10_choice_level_refined_with_rule_context",
        "purpose": "선지 1개 단위로 오류 유무를 분류한다. BERT가 판단 가능한 선지 자체 오류만 학습하고, 중복/복수정답/선지 간 길이 편향은 규칙/후처리로 함께 점검한다.",
        "input_data": "passage/material + question + one choice + is_answer",
        "y_value": "binary label. label=0이면 선지 오류 있음, label=1이면 선지 오류 없음. error_codes는 보조 설명 정보다.",
        "why": "사용자의 역할은 문항 전체 폐기가 아니라 선지의 오류 유무 판단이므로, 문항 전체 오류를 선지 오류로 억지 라벨링하지 않는다.",
        "past_exam_source": str(PAST_EXAM_SOURCE),
        "full_audit_source": str(FULL_AUDIT_SOURCE),
        "additional_audit_source": str(ADDITIONAL_AUDIT_SOURCE),
        "additional_note": "추가분류 511문항 파일은 v10 BERT 학습에는 직접 사용하지 않는다. 대부분 문항/메타데이터 단위 사유이기 때문이다.",
        "past_exam_questions": len(past_raw),
        "past_exam_rows": len(past_rows),
        "generated_audit_summary": generated_summary,
        "rule_only_question_count": len(rule_only_items),
        "total_rows": len(all_rows),
        "split_count": {name: len(items) for name, items in splits.items()},
        "label_count": label_count(all_rows),
        "split_label_count": {name: label_count(items) for name, items in splits.items()},
        "source_type_count": source_type_count(all_rows),
        "error_code_count": error_code_count(all_rows),
        "split_error_code_count": {name: error_code_count(items) for name, items in splits.items()},
        "bert_error_code_names_ko": {code: BERT_ERROR_KO.get(code, code) for code in error_codes},
        "rule_only_error_code_names_ko": RULE_ONLY_ERROR_KO,
        "bert_target_codes": sorted(BERT_ERROR_KO),
        "rule_only_codes": sorted(RULE_ONLY_ERROR_KO),
        "excluded_categories": sorted(EXCLUDED_CATEGORIES),
        "operational_improvements": [
            "row마다 all_choices, answer_numbers, answer_count, question_rule_codes를 포함한다.",
            "정답 선지 길이 편향은 절대 길이와 다른 선지 대비 상대 길이를 함께 본다.",
            "완전 동일 중복뿐 아니라 한 선지가 다른 선지를 거의 포함하는 약한 중복도 규칙 후보로 잡는다.",
        ],
        "files": [
            "choice_quality_train_v10.json",
            "choice_quality_test_v10.json",
            "choice_quality_data_v10.json",
            "choice_quality_skipped_v10.json",
            "choice_quality_rule_only_v10.json",
        ],
    }
    write_json(OUT_DIR / "choice_quality_summary_v10.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
