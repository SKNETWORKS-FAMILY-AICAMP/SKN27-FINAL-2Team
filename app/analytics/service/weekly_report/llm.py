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
) -> dict[str, object]:
    resolved_config = config or get_weekly_report_config()
    return generate_report_content(
        result,
        lambda facts: call_writer(facts, resolved_config),
        lambda candidate, facts: call_validator(candidate, facts, resolved_config),
        resolved_config,
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
    allowed_numbers = set(re.findall(r"-?\d+(?:\.\d+)?", json.dumps(result, ensure_ascii=False)))
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
        text_numbers = set(re.findall(r"-?\d+(?:\.\d+)?", text))
        if not text_numbers.issubset(allowed_numbers):
            errors.append(f"{item_type.upper()}_UNSUPPORTED_NUMBER")
        if any(phrase in text for phrase in resolved_config.forbidden_phrases):
            errors.append(f"{item_type.upper()}_FORBIDDEN_PHRASE")
    return list(dict.fromkeys(errors))


def _collect_evidence_ids(result: Mapping[str, object]) -> set[str]:
    evidence_ids: set[str] = set()
    for field_name in (
        "strengths",
        "priorityImprovements",
        "timeSummary",
        "nextPlanTargets",
    ):
        for item in result.get(field_name) or []:
            if isinstance(item, Mapping) and item.get("evidenceId"):
                evidence_ids.add(str(item["evidenceId"]))
    return evidence_ids
