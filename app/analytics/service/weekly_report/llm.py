from __future__ import annotations

import json
import re
from typing import Callable, Mapping

from analytics.service.weekly_report.config import (
    WeeklyReportConfig,
    get_weekly_report_config,
)
from analytics.service.weekly_report.service import build_fallback_content


Writer = Callable[[Mapping[str, object]], Mapping[str, object]]
Validator = Callable[[Mapping[str, object], Mapping[str, object]], bool]


def generate_report_content(
    result: Mapping[str, object],
    writer: Writer,
    validator: Validator,
    config: WeeklyReportConfig | None = None,
) -> dict[str, object]:
    resolved_config = config or get_weekly_report_config()
    try:
        candidate = dict(writer(result))
        guard_errors = validate_ai_content(candidate, result, resolved_config)
        if guard_errors:
            return build_fallback_content(result, resolved_config)
        if not validator(candidate, result):
            return build_fallback_content(result, resolved_config)
    except Exception:
        return build_fallback_content(result, resolved_config)

    return {
        "comment": dict(candidate["comment"]),
        "tips": [dict(item) for item in candidate["tips"]],
        "fallbackUsed": False,
        "validation": {"guard": "passed", "validator": "passed"},
    }


def generate_default_report_content(
    result: Mapping[str, object],
    config: WeeklyReportConfig | None = None,
    report_type: str | None = None,
) -> dict[str, object]:
    from analytics.service.weekly_report.graph import generate_graph_report_content

    resolved_config = config or get_weekly_report_config()
    return generate_graph_report_content(
        result,
        config=resolved_config,
        report_type=report_type,
    )


def call_writer(
    result: Mapping[str, object],
    config: WeeklyReportConfig | None = None,
) -> Mapping[str, object]:
    from openai import OpenAI

    resolved_config = config or get_weekly_report_config()
    client = OpenAI(timeout=resolved_config.llm_timeout_seconds)
    response = client.chat.completions.create(
        model=resolved_config.model,
        max_completion_tokens=resolved_config.writer_maximum_tokens,
        response_format={"type": "json_object"},
        messages=[
            {
                "role": "system",
                "content": (
                    "당신은 주간 학습 리포트 문장 작성자입니다. 제공된 facts만 사용하세요. "
                    "숫자와 분석 결과를 새로 만들지 말고, comment 1개와 tips 배열만 JSON으로 반환하세요. "
                    "각 항목은 text와 facts에 존재하는 evidenceIds만 포함해야 합니다."
                ),
            },
            {
                "role": "user",
                "content": json.dumps(result, ensure_ascii=False),
            },
        ],
    )
    raw_content = response.choices[0].message.content or ""
    parsed = json.loads(raw_content)
    if not isinstance(parsed, Mapping):
        raise ValueError("작성 AI 응답이 JSON 객체가 아닙니다.")
    return parsed


def call_validator(
    candidate: Mapping[str, object],
    result: Mapping[str, object],
    config: WeeklyReportConfig | None = None,
) -> bool:
    from openai import OpenAI

    resolved_config = config or get_weekly_report_config()
    client = OpenAI(timeout=resolved_config.llm_timeout_seconds)
    response = client.chat.completions.create(
        model=resolved_config.model,
        max_completion_tokens=resolved_config.validator_maximum_tokens,
        response_format={"type": "json_object"},
        messages=[
            {
                "role": "system",
                "content": (
                    "당신은 학습 리포트 검증자입니다. candidate가 facts를 벗어나지 않고 "
                    "과장·비난·합격 보장 없이 실행 가능한 문장인지 검사하세요. "
                    "JSON 객체 {\"passed\": true|false}만 반환하세요."
                ),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {"facts": result, "candidate": candidate},
                    ensure_ascii=False,
                ),
            },
        ],
    )
    raw_content = response.choices[0].message.content or ""
    parsed = json.loads(raw_content)
    return isinstance(parsed, Mapping) and parsed.get("passed") is True


def validate_ai_content(
    candidate: Mapping[str, object],
    result: Mapping[str, object],
    config: WeeklyReportConfig | None = None,
) -> list[str]:
    resolved_config = config or get_weekly_report_config()
    errors: list[str] = []
    if set(candidate) - {"comment", "tips"}:
        errors.append("UNKNOWN_FIELD")
    comment = candidate.get("comment")
    tips = candidate.get("tips")
    if not isinstance(comment, Mapping):
        errors.append("COMMENT_REQUIRED")
    if not isinstance(tips, list):
        errors.append("TIPS_REQUIRED")
        tips = []
    if len(tips) > resolved_config.maximum_tip_count:
        errors.append("TIP_COUNT_EXCEEDED")

    allowed_evidence_ids = _collect_evidence_ids(result)
    forbidden_user_text = (
        resolved_config.forbidden_phrases
        + resolved_config.forbidden_output_tokens
    )
    items: list[tuple[str, object, int]] = [("comment", comment, resolved_config.maximum_comment_length)]
    items.extend(("tip", item, resolved_config.maximum_tip_length) for item in tips)
    for item_type, item, maximum_length in items:
        if not isinstance(item, Mapping):
            errors.append(f"{item_type.upper()}_INVALID")
            continue
        text = item.get("text")
        evidence_ids = item.get("evidenceIds")
        if not isinstance(text, str) or not text.strip():
            errors.append(f"{item_type.upper()}_TEXT_REQUIRED")
            continue
        if len(text) > maximum_length:
            errors.append(f"{item_type.upper()}_TOO_LONG")
        if not isinstance(evidence_ids, list):
            errors.append(f"{item_type.upper()}_EVIDENCE_REQUIRED")
            continue
        if any(str(evidence_id) not in allowed_evidence_ids for evidence_id in evidence_ids):
            errors.append(f"{item_type.upper()}_UNKNOWN_EVIDENCE")
        text_numbers = _extract_text_numbers(text)
        allowed_numbers = _normalize_numbers(_collect_allowed_numbers(result, evidence_ids))
        if not text_numbers.issubset(allowed_numbers):
            errors.append(f"{item_type.upper()}_UNSUPPORTED_NUMBER")
        if any(phrase in text for phrase in forbidden_user_text):
            errors.append(f"{item_type.upper()}_FORBIDDEN_PHRASE")
    return list(dict.fromkeys(errors))


def validate_grounded_statements(
    statements: list[object],
    result: Mapping[str, object],
    config: WeeklyReportConfig | None = None,
    evidence_fields: tuple[str, ...] | None = None,
) -> list[str]:
    resolved_config = config or get_weekly_report_config()
    errors: list[str] = []
    allowed_evidence_ids = _collect_evidence_ids(result, evidence_fields)
    for index, statement in enumerate(statements):
        error_prefix = f"STATEMENT_{index + 1}"
        if not isinstance(statement, Mapping):
            errors.append(f"{error_prefix}_INVALID")
            continue
        text = statement.get("text")
        evidence_ids = statement.get("evidenceIds")
        if not isinstance(text, str) or not text.strip():
            errors.append(f"{error_prefix}_TEXT_REQUIRED")
            continue
        if not isinstance(evidence_ids, list):
            errors.append(f"{error_prefix}_EVIDENCE_REQUIRED")
            continue
        if any(str(evidence_id) not in allowed_evidence_ids for evidence_id in evidence_ids):
            errors.append(f"{error_prefix}_UNKNOWN_EVIDENCE")
        text_numbers = _extract_text_numbers(text)
        allowed_numbers = _normalize_numbers(
            _collect_allowed_numbers(result, evidence_ids, evidence_fields),
        )
        if not text_numbers.issubset(allowed_numbers):
            errors.append(f"{error_prefix}_UNSUPPORTED_NUMBER")
        if any(phrase in text for phrase in resolved_config.forbidden_phrases):
            errors.append(f"{error_prefix}_FORBIDDEN_PHRASE")
    return list(dict.fromkeys(errors))


def _extract_text_numbers(text: str) -> set[str]:
    """문장에서 숫자를 뽑는다.

    "1,200초" 가 1 과 200 으로 쪼개지지 않게 자릿수 구분 기호를 먼저 없앤다.
    부호는 근거 쪽과 맞추기 위해 버린다. "3-4문제" 의 하이픈을 음수로 읽는 것도 막는다.
    """
    return _normalize_numbers(re.findall(r"\d+(?:\.\d+)?", text.replace(",", "")))


def _normalize_numbers(numbers) -> set[str]:
    """부호와 소수점 표기를 통일한다. 0.20 과 0.2, -0.2 와 0.2 를 같게 본다."""
    normalized: set[str] = set()
    for number in numbers:
        try:
            value = abs(float(number))
        except ValueError:
            continue
        normalized.add(f"{value:g}")
    return normalized


def _collect_evidence_ids(
    result: Mapping[str, object],
    evidence_fields: tuple[str, ...] | None = None,
) -> set[str]:
    return set(_collect_evidence_items(result, evidence_fields))


def _collect_evidence_items(
    result: Mapping[str, object],
    evidence_fields: tuple[str, ...] | None = None,
) -> dict[str, Mapping[str, object]]:
    evidence_items: dict[str, Mapping[str, object]] = {}
    resolved_fields = evidence_fields
    if resolved_fields is None:
        resolved_fields = (
            "assessment",
            "comparison",
            "planProgress",
            "strengths",
            "priorityImprovements",
            "conceptWeaknesses",
            "examTrends",
            "timeSummary",
            "confusionPatterns",
            "nextPlanTargets",
        )
    for field_name in resolved_fields:
        field_value = result.get(field_name)
        items: list[object] = []
        if isinstance(field_value, Mapping):
            items = [field_value]
        elif isinstance(field_value, list):
            items = field_value
        for item in items:
            if isinstance(item, Mapping) and item.get("evidenceId"):
                evidence_items[str(item["evidenceId"])] = item
    return evidence_items


# 식별자는 사람이 인용할 수치가 아니다. 세션 번호를 점수로 쓰는 문장을 막는다.
IDENTIFIER_KEYS = frozenset(
    {
        "sessionId",
        "baselineSessionId",
        "studyPlanId",
        "sourceSessionId",
        "sourceQuestionIds",
        "questionId",
        "questionIds",
        "targetRound",
    }
)


def _collect_allowed_numbers(
    result: Mapping[str, object],
    evidence_ids: list[object],
    evidence_fields: tuple[str, ...] | None = None,
) -> set[str]:
    numbers: set[str] = set()
    evidence_items = _collect_evidence_items(result, evidence_fields)
    for evidence_id in evidence_ids:
        evidence_item = evidence_items.get(str(evidence_id))
        if evidence_item is not None:
            numbers.update(_collect_quotable_numbers(evidence_item))
    return numbers


def _collect_quotable_numbers(value: object, key: str | None = None) -> set[str]:
    """인용해도 되는 수치만 모은다.

    식별자는 제외하고, 라벨 문자열에 든 숫자는 포함한다. "3·1 운동" 같은
    개념명을 그대로 쓰지 못하면 한국사 리포트에서 쓸 수 없는 문장이 너무 많아진다.
    """
    if key in IDENTIFIER_KEYS:
        return set()
    if isinstance(value, str):
        return set(re.findall(r"-?\d+(?:\.\d+)?", value))
    if isinstance(value, Mapping):
        numbers: set[str] = set()
        for nested_key, nested_value in value.items():
            numbers.update(_collect_quotable_numbers(nested_value, str(nested_key)))
        return numbers
    if isinstance(value, (list, tuple)):
        numbers = set()
        for nested_value in value:
            numbers.update(_collect_quotable_numbers(nested_value, key))
        return numbers
    return _collect_numeric_literals(value)


def _collect_numeric_literals(value: object) -> set[str]:
    if isinstance(value, bool) or value is None:
        return set()
    if isinstance(value, (int, float)):
        literals = set(re.findall(r"-?\d+(?:\.\d+)?", str(value)))
        # 62.0 과 62 는 같은 값이다. 근거가 float 로 저장돼 있다는 이유로
        # "62점" 같은 자연스러운 문장을 거절하면 안 된다.
        if isinstance(value, float) and value.is_integer():
            literals.add(str(int(value)))
        return literals
    if isinstance(value, Mapping):
        numbers: set[str] = set()
        for nested_value in value.values():
            numbers.update(_collect_numeric_literals(nested_value))
        return numbers
    if isinstance(value, (list, tuple)):
        numbers = set()
        for nested_value in value:
            numbers.update(_collect_numeric_literals(nested_value))
        return numbers
    return set()
