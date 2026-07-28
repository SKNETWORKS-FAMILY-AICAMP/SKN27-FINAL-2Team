from __future__ import annotations

import copy
import json
from datetime import date, datetime
from typing import Mapping, Sequence


def parse_plan_summary(summary: object) -> dict[str, object]:
    if isinstance(summary, Mapping):
        return dict(summary)
    elif not summary:
        return {}

    raw_summary = str(summary)
    try:
        parsed = json.loads(raw_summary)
    except (TypeError, json.JSONDecodeError):
        return {"summary": raw_summary}
    if isinstance(parsed, dict):
        return parsed
    return {"summary": raw_summary}


def parse_plan_items(plan_items: object) -> list[dict[str, object]]:
    if isinstance(plan_items, list):
        parsed_items = plan_items
    elif not plan_items:
        return []
    elif plan_items:
        try:
            parsed_items = json.loads(str(plan_items))
        except (TypeError, json.JSONDecodeError):
            return []
    elif not plan_items:
        return []
    if not isinstance(parsed_items, list):
        return []
    return copy.deepcopy(parsed_items)


def build_study_plan_dto(
    study_plan: object,
    today: date,
    completed_block_ids: set[str] | None = None,
    in_progress_block_ids: set[str] | None = None,
) -> dict[str, object]:
    completed_ids = completed_block_ids or set()
    in_progress_ids = in_progress_block_ids or set()
    plan_status = str(_get_value(study_plan, "status") or "active")
    plans = parse_plan_items(_get_value(study_plan, "study_plan_items"))
    summary_data = parse_plan_summary(_get_value(study_plan, "study_plans"))
    completed_count = 0
    total_count = 0

    for day_plan in plans:
        plan_date = _parse_date(day_plan.get("date"))
        for block in day_plan.get("blocks", []):
            block_id = str(block.get("blockId") or "")
            block_type = normalize_block_type(block)
            status = normalize_block_status(
                block,
                block_id,
                plan_date,
                today,
                completed_ids,
                in_progress_ids,
            )
            block["blockType"] = block_type
            block["status"] = status
            block["isCompleted"] = status == "completed"
            block["isAchieved"] = status == "completed"
            question_count = int(block.get("questionCount") or 0)
            achieved_count = 0
            remaining_count = question_count
            progress_percent = 0
            if status == "completed":
                achieved_count = question_count
                remaining_count = 0
                progress_percent = 100
            block["achievedCount"] = achieved_count
            block["remainingCount"] = remaining_count
            block["progressPercent"] = progress_percent
            block["progressMode"] = "question"
            block["statusLabel"] = status
            block["canStart"] = (
                plan_status == "active"
                and plan_date == today
                and status in ("scheduled", "in_progress")
            )
            if is_completion_block(block):
                total_count += 1
                if status == "completed":
                    completed_count += 1

    completion_rate = 0.0
    if total_count:
        completion_rate = completed_count / total_count
    normalized_plan_status = plan_status
    if plan_status == "deleted":
        normalized_plan_status = "legacy_deleted"
    start_date_text = _format_date(_get_value(study_plan, "start_date"))
    end_date_text = _format_date(_get_value(study_plan, "end_date"))
    return {
        "studyPlanId": _get_value(study_plan, "studyplan_id"),
        "status": normalized_plan_status,
        "planVersion": int(_get_value(study_plan, "plan_version") or 1),
        "summary": str(summary_data.get("summary") or ""),
        "startDate": start_date_text,
        "endDate": end_date_text,
        "completionRate": round(completion_rate, 4),
        # 마이페이지 플래너 상단의 "현재 계획 진행률" 이 이 값을 읽는다.
        # 완료율 정의(완료한 일반 학습 블록 ÷ 전체 일반 학습 블록)와 같다.
        "progress": {
            "targetCount": total_count,
            "achievedCount": completed_count,
            "remainingCount": max(total_count - completed_count, 0),
            "completionRate": round(completion_rate, 4),
            "completionPercent": round(completion_rate * 100),
            "periodLabel": _build_period_label(start_date_text, end_date_text),
        },
        "plans": plans,
    }


def _build_period_label(
    start_date_text: str | None,
    end_date_text: str | None,
) -> str:
    if not start_date_text or not end_date_text:
        return "기간 미정"
    return (
        f"{start_date_text[5:7]}.{start_date_text[8:10]}"
        f" ~ {end_date_text[5:7]}.{end_date_text[8:10]}"
    )


def normalize_block_type(block: Mapping[str, object]) -> str:
    block_type = str(block.get("blockType") or "")
    if block_type in ("newWeakness", "predictionFocus", "review"):
        return "practice"
    return block_type


def normalize_block_status(
    block: Mapping[str, object],
    block_id: str,
    plan_date: date | None,
    today: date,
    completed_block_ids: set[str],
    in_progress_block_ids: set[str],
) -> str:
    if block_id in completed_block_ids or block.get("isCompleted") is True:
        return "completed"
    elif block_id in in_progress_block_ids:
        return "in_progress"

    stored_status = str(block.get("status") or "scheduled")
    if stored_status in ("completed", "missed", "cancelled"):
        return stored_status
    elif normalize_block_type(block) == "weekly_review" and plan_date is not None and plan_date < today:
        return "missed"
    return "scheduled"


def is_completion_block(block: Mapping[str, object]) -> bool:
    if normalize_block_type(block) == "weekly_review":
        return False
    return not is_legacy_extra_block(block)


def is_legacy_extra_block(block: Mapping[str, object]) -> bool:
    return (
        block.get("blockType") == "extra"
        or block.get("focusKind") == "extra"
        or block.get("planSource") == "extra"
    )


def _get_value(instance: object, field_name: str) -> object:
    if isinstance(instance, Mapping):
        return instance.get(field_name)
    return getattr(instance, field_name, None)


def _parse_date(value: object) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    elif isinstance(value, date):
        return value
    elif value:
        try:
            return date.fromisoformat(str(value)[:10])
        except ValueError:
            return None
    return None


def _format_date(value: object) -> str | None:
    parsed = _parse_date(value)
    if parsed:
        return parsed.isoformat()
    return None
