from __future__ import annotations

import math
from dataclasses import dataclass, replace
from datetime import date, timedelta
from typing import Callable, Mapping, Sequence

from analytics.service.study_plan.config import StudyPlanConfig, get_study_plan_config
from analytics.service.taxonomy import build_group_key_id


class StudyPlanPeriodUnavailable(ValueError):
    pass


class QuestionPoolInsufficient(ValueError):
    pass


@dataclass(frozen=True)
class PlanPeriod:
    start_date: date
    end_date: date
    learning_dates: tuple[date, ...]
    weekly_review_date: date | None


@dataclass(frozen=True)
class PlanTarget:
    group_key_id: str
    label: str
    era: str = ""
    topic: str = ""
    q_type: str = ""
    weakness_score: float = 0.0
    weakness_status: str = "INSUFFICIENT"
    trend: str = "unknown"
    effective_total: float = 0.0
    exam_question_count: int = 0
    repeated_error: float = 0.0
    average_seconds_per_question: float | None = None


@dataclass(frozen=True)
class PriorityTarget:
    group_key_id: str
    label: str
    era: str
    topic: str
    q_type: str
    weakness_score: float
    weakness_status: str
    trend: str
    effective_total: float
    exam_weight: float
    repeated_error: float
    average_seconds_per_question: float | None
    priority_score: float
    generation_reason: str
    scope_relaxed: bool = False


PoolValidator = Callable[[PriorityTarget, int], bool]


def calculate_plan_period(
    start_date: date,
    exam_date: date | None,
    config: StudyPlanConfig | None = None,
) -> PlanPeriod:
    resolved_config = config or get_study_plan_config()
    regular_end = start_date + timedelta(days=resolved_config.weekly_plan_days - 1)
    exam_is_within_boundary = (
        exam_date is not None
        and exam_date <= start_date + timedelta(days=resolved_config.weekly_plan_days)
    )
    if exam_is_within_boundary:
        end_date = min(regular_end, exam_date - timedelta(days=1))
        if end_date < start_date:
            raise StudyPlanPeriodUnavailable("시험 전에 배정할 학습일이 없습니다.")
        learning_dates = tuple(
            start_date + timedelta(days=offset)
            for offset in range((end_date - start_date).days + 1)
        )
        return PlanPeriod(start_date, end_date, learning_dates, None)

    learning_dates = tuple(
        start_date + timedelta(days=offset)
        for offset in range(resolved_config.weekly_learning_days)
    )
    return PlanPeriod(start_date, regular_end, learning_dates, regular_end)


def calculate_daily_block_minutes(
    daily_available_minutes: int,
    config: StudyPlanConfig | None = None,
) -> tuple[int, ...]:
    resolved_config = config or get_study_plan_config()
    applied_minutes = min(
        max(int(daily_available_minutes), 1),
        resolved_config.maximum_daily_minutes,
    )
    block_count = min(
        math.ceil(applied_minutes / resolved_config.maximum_block_minutes),
        resolved_config.maximum_daily_blocks,
    )
    return tuple(
        min(
            resolved_config.maximum_block_minutes,
            applied_minutes - resolved_config.maximum_block_minutes * index,
        )
        for index in range(block_count)
    )


def calculate_score_counts(
    question_count: int,
    config: StudyPlanConfig | None = None,
) -> dict[int, int]:
    resolved_config = config or get_study_plan_config()
    if question_count <= 0:
        return {score: 0 for score in resolved_config.score_ratio}

    ratio_total = sum(resolved_config.score_ratio.values())
    counts = {
        score: question_count * ratio // ratio_total
        for score, ratio in resolved_config.score_ratio.items()
    }
    assigned_count = sum(counts.values())
    remainder_order = sorted(
        resolved_config.score_ratio,
        key=lambda score: (
            question_count * resolved_config.score_ratio[score] % ratio_total,
            resolved_config.score_ratio[score],
            score,
        ),
        reverse=True,
    )
    for score in remainder_order[: question_count - assigned_count]:
        counts[score] += 1
    return counts


def is_question_pool_available(
    target: PriorityTarget,
    question_count: int,
    counts_by_group: Mapping[str, Mapping[int, int]],
    config: StudyPlanConfig | None = None,
) -> bool:
    available_counts = counts_by_group.get(target.group_key_id)
    if available_counts is None:
        return False
    required_counts = calculate_score_counts(question_count, config)
    if sum(available_counts.values()) < question_count:
        return False
    return all(
        int(available_counts.get(score, 0)) >= required_count
        for score, required_count in required_counts.items()
    )


def build_priority_targets(
    targets: Sequence[PlanTarget],
    days_until_exam: int | None,
    config: StudyPlanConfig | None = None,
) -> list[PriorityTarget]:
    resolved_config = config or get_study_plan_config()
    reliable_targets = [
        target
        for target in targets
        if target.weakness_status in ("WEAK", "NEUTRAL")
        and target.weakness_score > resolved_config.stable_weakness_threshold
    ]
    selected_targets = reliable_targets or [
        target for target in targets if target.exam_question_count > 0
    ]
    if not selected_targets:
        return []

    generation_reason = "fallback_prediction_only"
    if reliable_targets:
        generation_reason = "personalized"
    maximum_question_count = max(target.exam_question_count for target in selected_targets)
    strategy = _get_study_strategy(days_until_exam, resolved_config)
    weights = resolved_config.strategy_weights[strategy]
    priority_targets: list[PriorityTarget] = []
    for target in selected_targets:
        exam_weight = 0.0
        if maximum_question_count > 0:
            exam_weight = target.exam_question_count / maximum_question_count
        weakness_score = 0.0
        repeated_error = 0.0
        if reliable_targets:
            weakness_score = target.weakness_score
            repeated_error = target.repeated_error
        priority_score = min(
            max(
                weakness_score * weights.weakness
                + exam_weight * weights.exam
                + repeated_error * weights.repeated_error,
                0.0,
            ),
            1.0,
        )
        priority_targets.append(
            PriorityTarget(
                group_key_id=target.group_key_id,
                label=target.label,
                era=target.era,
                topic=target.topic,
                q_type=target.q_type,
                weakness_score=weakness_score,
                weakness_status=target.weakness_status,
                trend=target.trend,
                effective_total=target.effective_total,
                exam_weight=exam_weight,
                repeated_error=repeated_error,
                average_seconds_per_question=target.average_seconds_per_question,
                priority_score=priority_score,
                generation_reason=generation_reason,
            )
        )

    return sorted(
        priority_targets,
        key=lambda target: (
            -target.priority_score,
            resolved_config.trend_order.get(target.trend, len(resolved_config.trend_order)),
            -target.effective_total,
            target.group_key_id,
        ),
    )


def build_plan_draft(
    priority_targets: Sequence[PriorityTarget],
    start_date: date,
    exam_date: date | None,
    daily_available_minutes: int,
    pool_validator: PoolValidator | None = None,
    config: StudyPlanConfig | None = None,
) -> dict[str, object]:
    resolved_config = config or get_study_plan_config()
    if not priority_targets:
        raise QuestionPoolInsufficient("학습계획 후보가 없습니다.")

    period = calculate_plan_period(start_date, exam_date, resolved_config)
    block_minutes = calculate_daily_block_minutes(
        daily_available_minutes or resolved_config.default_daily_minutes,
        resolved_config,
    )
    validator = pool_validator or (lambda target, question_count: True)
    days: list[dict[str, object]] = []
    target_cursor = 0
    practice_block_count = 0

    for learning_date in period.learning_dates:
        blocks: list[dict[str, object]] = []
        for planned_minutes in block_minutes:
            selected_target, target_cursor = _select_available_target(
                priority_targets,
                target_cursor,
                planned_minutes,
                validator,
                resolved_config,
            )
            if selected_target is None:
                continue
            blocks.append(
                _build_practice_block(
                    selected_target,
                    learning_date,
                    planned_minutes,
                    resolved_config,
                )
            )
            practice_block_count += 1
        days.append({"date": learning_date.isoformat(), "blocks": blocks})

    if practice_block_count == 0:
        raise QuestionPoolInsufficient("문제 후보가 부족해 일반 학습 블록을 만들 수 없습니다.")

    if period.weekly_review_date is not None:
        days.append(
            {
                "date": period.weekly_review_date.isoformat(),
                "blocks": [_build_weekly_review_block(period.weekly_review_date, resolved_config)],
            }
        )

    generation_reason = priority_targets[0].generation_reason
    return {
        "summary": {
            "schemaVersion": "2",
            "summary": f"{len(days)}일 학습계획",
            "configVersion": resolved_config.version,
            "generationReason": generation_reason,
            "sourceStudyPlanId": None,
            "timezone": resolved_config.timezone,
        },
        "startDate": period.start_date.isoformat(),
        "endDate": period.end_date.isoformat(),
        "dailyAvailableMinutes": min(
            max(int(daily_available_minutes or resolved_config.default_daily_minutes), 1),
            resolved_config.maximum_daily_minutes,
        ),
        "plans": days,
    }


def _get_study_strategy(
    days_until_exam: int | None,
    config: StudyPlanConfig,
) -> str:
    if days_until_exam is None:
        return "medium"
    elif days_until_exam <= config.short_term_days:
        return "short"
    elif days_until_exam <= config.medium_term_days:
        return "medium"
    return "long"


def _select_available_target(
    targets: Sequence[PriorityTarget],
    start_index: int,
    block_minutes: int,
    pool_validator: PoolValidator,
    config: StudyPlanConfig,
) -> tuple[PriorityTarget | None, int]:
    for offset in range(len(targets)):
        target_index = (start_index + offset) % len(targets)
        target = targets[target_index]
        question_count = _calculate_question_count(target, block_minutes, config)
        if pool_validator(target, question_count):
            return target, target_index + 1

    era_targets: list[PriorityTarget] = []
    used_eras: set[str] = set()
    for target in targets:
        if not target.era or target.era in used_eras:
            continue
        used_eras.add(target.era)
        era_targets.append(
            replace(
                target,
                group_key_id=build_group_key_id({"era": target.era}),
                label=target.era,
                topic="",
                q_type="",
                scope_relaxed=True,
            )
        )
    for era_target in era_targets:
        question_count = _calculate_question_count(era_target, block_minutes, config)
        if pool_validator(era_target, question_count):
            return era_target, start_index + 1
    return None, start_index + 1


def _calculate_question_count(
    target: PriorityTarget,
    block_minutes: int,
    config: StudyPlanConfig,
) -> int:
    average_seconds = (
        target.average_seconds_per_question
        or config.default_average_seconds_per_question
    )
    if average_seconds <= 0:
        average_seconds = config.default_average_seconds_per_question
    question_count = math.floor(block_minutes * 60 / average_seconds)
    return min(
        max(question_count, config.minimum_question_count),
        config.maximum_question_count,
    )


def _build_practice_block(
    target: PriorityTarget,
    learning_date: date,
    planned_minutes: int,
    config: StudyPlanConfig,
) -> dict[str, object]:
    reason = config.prediction_fallback_reason
    if target.generation_reason == "personalized":
        reason = config.personalized_reason
    if target.scope_relaxed:
        reason = f"{reason} / {config.scope_relaxed_reason}"
    classification = "시대"
    if target.topic:
        classification = "복합"
    return {
        "groupKeyId": target.group_key_id,
        "blockType": config.practice_block_type,
        "classification": classification,
        "label": target.label,
        "era": target.era,
        "topic": target.topic,
        "qType": target.q_type,
        "activity": config.practice_activity,
        "questionCount": _calculate_question_count(target, planned_minutes, config),
        "estimatedMinutes": planned_minutes,
        "priorityScore": round(target.priority_score, 4),
        "reason": reason,
        "status": "scheduled",
        "trend": target.trend,
        "originalDate": learning_date.isoformat(),
        "rolloverCount": 0,
    }


def _build_weekly_review_block(
    review_date: date,
    config: StudyPlanConfig,
) -> dict[str, object]:
    return {
        "groupKeyId": "",
        "blockType": config.weekly_review_block_type,
        "classification": "",
        "label": config.weekly_review_activity,
        "era": "",
        "topic": "",
        "qType": "",
        "activity": config.weekly_review_activity,
        "questionCount": config.weekly_review_question_count,
        "estimatedMinutes": config.weekly_review_minutes,
        "priorityScore": 0.0,
        "reason": config.weekly_review_activity,
        "status": "scheduled",
        "trend": "unknown",
        "originalDate": review_date.isoformat(),
        "rolloverCount": 0,
    }
