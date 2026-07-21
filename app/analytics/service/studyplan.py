"""Temporary compatibility façade for existing analytics imports."""

from __future__ import annotations

from datetime import date
from typing import Mapping, Sequence

from analytics.service.study_plan.config import get_study_plan_config as get_versioned_config
from analytics.service.study_plan.dto import is_completion_block, parse_plan_items
from analytics.service.study_plan.service import (
    InitialStudyPlanConfigUnavailable,
    complete_study_plan_block_by_id as complete_block_by_id,
    create_personalized_study_plan,
    get_active_study_plan_dto,
    is_weekly_review_plan_block,
)


class StudyPlanBlockDeleteLimitExceeded(Exception):
    pass


class StudyPlanDateOutOfRange(Exception):
    pass


class StudyPlanExtraBlockUnavailable(Exception):
    pass


class StudyPlanExtraBlockCompletionRequired(Exception):
    pass


class StudyPlanGenerationUnavailable(Exception):
    pass


def get_user_study_info(user_id: int) -> object | None:
    from user.models import UserAccounts

    return UserAccounts.objects.filter(user_id=user_id).first()


def get_study_plan_info(user_id: int) -> list[dict[str, object]]:
    study_plan = get_active_study_plan_dto(user_id)
    if study_plan is None:
        return []
    return [study_plan]


def ensure_today_study_plan(
    user_id: int,
    today: date | None = None,
) -> list[dict[str, object]]:
    study_plan = get_active_study_plan_dto(user_id, today)
    if study_plan is None:
        return []
    return [study_plan]


def calculate_record_based_plan_progress(
    user_id: int,
    study_plan: object,
) -> dict[str, object]:
    plan_items = parse_plan_items(study_plan.study_plan_items)
    completion_rate = float(study_plan.completion_rate or 0.0)
    active_plan = get_active_study_plan_dto(user_id)
    if active_plan and active_plan.get("studyPlanId") == study_plan.studyplan_id:
        plan_items = active_plan.get("plans", [])
        completion_rate = float(active_plan.get("completionRate") or 0.0)

    target_count = sum(
        1
        for day_plan in plan_items
        for block in day_plan.get("blocks", [])
        if is_completion_block(block)
    )
    achieved_count = round(target_count * completion_rate)
    return {
        "summary": {
            "targetCount": target_count,
            "achievedCount": achieved_count,
            "remainingCount": max(target_count - achieved_count, 0),
            "completionRate": completion_rate,
            "completionPercent": round(completion_rate * 100),
            "periodLabel": "",
        },
        "block_progress": {},
    }


def complete_study_plan_block_by_id(
    user_id: int,
    study_plan_id: int,
    block_id: str,
    is_completed: bool = True,
) -> dict[str, object] | None:
    if not is_completed:
        return None
    return complete_block_by_id(user_id, study_plan_id, block_id)


def complete_study_plan_block(
    user_id: int,
    study_plan_id: int,
    day_index: int,
    block_index: int,
    is_completed: bool = True,
) -> dict[str, object] | None:
    study_plans = get_study_plan_info(user_id)
    if not study_plans or study_plans[0].get("studyPlanId") != study_plan_id:
        return None
    study_plan = study_plans[0]
    plans = study_plan.get("plans", [])
    if not isinstance(plans, list) or day_index < 0 or day_index >= len(plans):
        return None
    blocks = plans[day_index].get("blocks", [])
    if not isinstance(blocks, list) or block_index < 0 or block_index >= len(blocks):
        return None
    block_id = str(blocks[block_index].get("blockId") or "")
    if not block_id:
        return None
    return complete_study_plan_block_by_id(
        user_id,
        study_plan_id,
        block_id,
        is_completed,
    )


def create_study_plan(
    user_id: int,
    study_plans: str = "",
    study_plan_items: Sequence[Mapping[str, object]] | None = None,
    predicted_targets: Sequence[Mapping[str, object]] | None = None,
    source_study_plan_id: int | None = None,
) -> dict[str, object]:
    if study_plan_items is not None:
        raise StudyPlanGenerationUnavailable("레거시 계획 직접 저장은 지원하지 않습니다.")
    try:
        return create_personalized_study_plan(user_id, source_study_plan_id)
    except (InitialStudyPlanConfigUnavailable, ValueError) as error:
        raise StudyPlanGenerationUnavailable(str(error)) from error


def delete_study_plan_block(
    user_id: int,
    study_plan_id: int,
    day_index: int,
    block_index: int,
) -> None:
    return None


def add_extra_study_plan_block(
    user_id: int,
    target_date: str,
) -> None:
    raise StudyPlanExtraBlockUnavailable("추가 학습 기능은 이번 버전에서 제공하지 않습니다.")


def get_study_plan_config() -> dict[str, object]:
    config = get_versioned_config()
    return {
        "weekly_plan_days": config.weekly_plan_days,
        "weekly_learning_days": config.weekly_learning_days,
        "weekly_review_block_type": config.weekly_review_block_type,
        "weekly_review_question_count": config.weekly_review_question_count,
        "weekly_review_minutes": config.weekly_review_minutes,
        "fallback_daily_available_minutes": config.default_daily_minutes,
        "regeneration_overload_multiplier": 2,
        "history_display_limit": 3,
        "daily_delete_limit": 0,
        "daily_delete_count_key": "deletedBlockCount",
        "daily_delete_count_date_key": "deletedBlockCountDate",
    }
