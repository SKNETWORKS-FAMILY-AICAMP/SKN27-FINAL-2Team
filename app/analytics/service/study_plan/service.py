from __future__ import annotations

import copy
import json
from datetime import date, timedelta
from decimal import Decimal
from typing import Mapping, Sequence
from uuid import uuid4
from zoneinfo import ZoneInfo

from analytics.service.study_plan.dto import (
    build_study_plan_dto,
    is_legacy_extra_block,
    normalize_block_status,
    normalize_block_type,
    parse_plan_items,
)
from analytics.service.study_plan.config import get_study_plan_config
from analytics.service.study_plan.planner import calculate_daily_block_minutes
from analytics.service.study_plan.planner import (
    PlanTarget,
    PriorityTarget,
    QuestionPoolInsufficient,
    build_plan_draft,
    build_priority_targets,
    is_question_pool_available,
)
from analytics.service.taxonomy import (
    build_group_key_id,
    build_target_display_label,
)
from analytics.service.weakness import build_weakness_rows, get_weakness_config


class StudyPlanDataIntegrityError(ValueError):
    pass


class StudyPlanBlockNotFound(ValueError):
    pass


class StudyPlanBlockNotDue(ValueError):
    pass


class StudyPlanBlockRouteMismatch(ValueError):
    pass


class StudyPlanBlockTerminal(ValueError):
    pass


class InitialStudyPlanConfigUnavailable(ValueError):
    pass


def get_active_study_plan_dto(
    user_id: int,
    today: date | None = None,
) -> dict[str, object] | None:
    from analytics.models import StudyPlanMypage
    from django.utils import timezone

    study_plan = (
        StudyPlanMypage.objects.filter(user_id=user_id, status="active")
        .order_by("-plan_version", "-modified_at")
        .first()
    )
    if study_plan is None:
        return None
    completed_ids, in_progress_ids = _get_progress_block_ids(user_id, study_plan.studyplan_id)
    return build_study_plan_dto(
        study_plan,
        today
        or timezone.localdate(
            timezone=ZoneInfo(get_study_plan_config().timezone),
        ),
        completed_ids,
        in_progress_ids,
    )


def create_personalized_study_plan(
    user_id: int,
    source_study_plan_id: int | None = None,
    today: date | None = None,
) -> dict[str, object]:
    from django.utils import timezone

    base_date = today or timezone.localdate(
        timezone=ZoneInfo(get_study_plan_config().timezone),
    )
    active_plan = get_active_study_plan_dto(user_id, base_date)
    if (
        active_plan is not None
        and active_plan.get("studyPlanId") != source_study_plan_id
    ):
        return {"changed": False, "studyPlan": active_plan}
    draft = build_user_plan_draft(user_id, base_date)
    return finalize_plan_draft(user_id, draft, source_study_plan_id)


def build_plan_targets(
    user_id: int,
    today: date,
) -> tuple[list[PriorityTarget], dict[str, dict[int, int]]]:
    """우선순위 학습 대상과 분류별 점수대 문항 수를 만든다.

    학습계획 생성과 주간 리포트가 같은 대상 목록을 써야 한다. 특히 리포트의
    repeated_error 는 여기서 만든 PriorityTarget.repeated_error 를 그대로
    쓰므로, 계산을 다시 구현하면 groupKeyId 규칙이 어긋날 수 있다.

    두 번째 반환값은 학습계획 생성이 문제 수 검증에 쓴다. 리포트는 쓰지 않는다.
    """
    from django.db.models import Count
    from question.models import Questions, SolveRecords, SolveSessions
    from user.models import UserAccounts

    weakness_config = get_weakness_config()
    period_start = today - timedelta(
        days=weakness_config.lookback_days - 1,
    )
    profile = UserAccounts.objects.get(user_id=user_id)
    record_rows = list(
        SolveRecords.objects.filter(
            session__user_id=user_id,
            session__status="completed",
            session__recorded_date__gte=period_start,
            session__recorded_date__lte=today,
        ).values(
            "session_id",
            "session__recorded_date",
            "is_correct",
            "time_spent_ms",
            "era",
            "topic",
        )
    )
    if not record_rows:
        raise InitialStudyPlanConfigUnavailable("첫 주 커리큘럼 설정값이 아직 확정되지 않았습니다.")

    weakness_rows = build_weakness_rows(record_rows, ("era", "topic"), today)
    weakness_by_group = {str(row["groupKeyId"]): row for row in weakness_rows}
    recent_session_ids = list(
        SolveSessions.objects.filter(user_id=user_id, status="completed")
        .order_by("-recorded_date", "-session_id")
        .values_list("session_id", flat=True)[:5]
    )
    eligible_sessions: dict[str, set[int]] = {}
    wrong_sessions: dict[str, set[int]] = {}
    for row in record_rows:
        if row["session_id"] not in recent_session_ids:
            continue
        group_key_id = build_group_key_id({"era": row["era"], "topic": row["topic"]})
        eligible_sessions.setdefault(group_key_id, set()).add(int(row["session_id"]))
        if row["is_correct"] is False:
            wrong_sessions.setdefault(group_key_id, set()).add(int(row["session_id"]))

    question_rows = list(
        Questions.objects.values("era", "topic", "q_score")
        .annotate(question_count=Count("question_id"))
        .order_by("era", "topic", "q_score")
    )
    pool_targets: dict[str, dict[str, object]] = {}
    counts_by_group: dict[str, dict[int, int]] = {}
    for row in question_rows:
        era = str(row["era"] or "").strip()
        topic = str(row["topic"] or "").strip()
        if not era or not topic:
            continue
        group_key_id = build_group_key_id({"era": era, "topic": topic})
        era_key_id = build_group_key_id({"era": era})
        pool_targets.setdefault(
            group_key_id,
            {"era": era, "topic": topic, "questionCount": 0},
        )
        pool_targets[group_key_id]["questionCount"] = (
            int(pool_targets[group_key_id]["questionCount"]) + int(row["question_count"])
        )
        # 별칭이 같은 분류로 합쳐질 수 있어 누적해야 한다.
        # (예: era "조선전기" 와 "조선" 이 같은 키로 정규화된다)
        group_counts = counts_by_group.setdefault(group_key_id, {})
        group_counts[int(row["q_score"])] = (
            int(group_counts.get(int(row["q_score"]), 0)) + int(row["question_count"])
        )
        era_counts = counts_by_group.setdefault(era_key_id, {})
        era_counts[int(row["q_score"])] = (
            int(era_counts.get(int(row["q_score"]), 0)) + int(row["question_count"])
        )
    if not pool_targets:
        raise QuestionPoolInsufficient("문제은행에 학습계획 후보가 없습니다.")

    targets: list[PlanTarget] = []
    for group_key_id, pool_target in pool_targets.items():
        weakness_row = weakness_by_group.get(group_key_id, {})
        effective = weakness_row.get("effective", {})
        raw = weakness_row.get("raw", {})
        eligible_count = len(eligible_sessions.get(group_key_id, set()))
        repeated_error = 0.0
        if eligible_count >= 3:
            repeated_error = len(wrong_sessions.get(group_key_id, set())) / eligible_count
        average_seconds = raw.get("averageTimeSec")
        if not isinstance(average_seconds, (int, float)):
            average_seconds = None
        targets.append(
            PlanTarget(
                group_key_id=group_key_id,
                label=build_target_display_label(pool_target["era"], pool_target["topic"]),
                era=str(pool_target["era"]),
                topic=str(pool_target["topic"]),
                weakness_score=float(weakness_row.get("weaknessScore") or 0.0),
                weakness_status=str(weakness_row.get("status") or "INSUFFICIENT"),
                trend=str(weakness_row.get("trend") or "unknown"),
                effective_total=float(effective.get("total") or 0.0),
                exam_question_count=int(pool_target["questionCount"]),
                repeated_error=repeated_error,
                average_seconds_per_question=average_seconds,
            )
        )

    days_until_exam = None
    if profile.exam_date is not None:
        days_until_exam = (profile.exam_date - today).days
    return build_priority_targets(targets, days_until_exam), counts_by_group


def build_user_plan_draft(
    user_id: int,
    today: date,
) -> dict[str, object]:
    from user.models import UserAccounts

    priority_targets, counts_by_group = build_plan_targets(user_id, today)
    profile = UserAccounts.objects.get(user_id=user_id)
    config = get_study_plan_config()
    daily_minutes = int(
        (profile.daily_available_hours or Decimal(0)) * config.minutes_per_hour,
    )
    if daily_minutes <= 0:
        daily_minutes = config.default_daily_minutes

    def pool_validator(target: PriorityTarget, question_count: int) -> bool:
        return is_question_pool_available(target, question_count, counts_by_group, config)

    return build_plan_draft(
        priority_targets,
        today,
        profile.exam_date,
        daily_minutes,
        pool_validator,
        config,
    )


def finalize_plan_draft(
    user_id: int,
    draft: Mapping[str, object],
    source_study_plan_id: int | None,
) -> dict[str, object]:
    from analytics.models import StudyPlanMypage
    from django.db import transaction
    from django.db.models import Max
    from django.utils import timezone
    from user.models import UserAccounts

    prepared_plans = parse_plan_items(draft.get("plans"))
    for day_plan in prepared_plans:
        if not isinstance(day_plan, Mapping):
            raise StudyPlanDataIntegrityError("계획 일자 형식이 올바르지 않습니다.")
        blocks = day_plan.get("blocks", [])
        if not isinstance(blocks, list):
            raise StudyPlanDataIntegrityError("계획 블록 목록 형식이 올바르지 않습니다.")
        for block in blocks:
            if not isinstance(block, dict):
                raise StudyPlanDataIntegrityError("계획 블록 형식이 올바르지 않습니다.")
            if not block.get("blockId"):
                block["blockId"] = str(uuid4())
    validate_plan_items_for_mutation(prepared_plans)
    if not any(
        normalize_block_type(block) == "practice"
        for day_plan in prepared_plans
        for block in day_plan.get("blocks", [])
    ):
        raise StudyPlanDataIntegrityError("일반 학습 블록이 없는 계획은 저장할 수 없습니다.")

    summary = dict(draft.get("summary") or {})
    summary["sourceStudyPlanId"] = source_study_plan_id
    current_time = timezone.now()
    today = timezone.localdate(
        current_time,
        timezone=ZoneInfo(get_study_plan_config().timezone),
    )
    with transaction.atomic():
        UserAccounts.objects.select_for_update().only("user_id").get(user_id=user_id)
        active_plans = list(
            StudyPlanMypage.objects.select_for_update()
            .filter(user_id=user_id, status="active")
            .order_by("-plan_version", "-modified_at")
        )
        if len(active_plans) > 1:
            raise StudyPlanDataIntegrityError("활성 학습계획이 두 개 이상입니다.")
        active_plan = None
        if active_plans:
            active_plan = active_plans[0]
        if active_plan is not None and active_plan.studyplan_id != source_study_plan_id:
            completed_ids, in_progress_ids = _get_progress_block_ids(
                user_id,
                active_plan.studyplan_id,
            )
            return {
                "changed": False,
                "studyPlan": build_study_plan_dto(
                    active_plan,
                    today,
                    completed_ids,
                    in_progress_ids,
                ),
            }

        if active_plan is not None:
            completed_ids, in_progress_ids = _get_progress_block_ids(
                user_id,
                active_plan.studyplan_id,
            )
            archived_dto = build_study_plan_dto(
                active_plan,
                today,
                completed_ids,
                in_progress_ids,
            )
            active_plan.status = "archived"
            active_plan.archived_at = current_time
            active_plan.modified_at = current_time
            active_plan.completion_rate = archived_dto["completionRate"]
            active_plan.save(
                update_fields=("status", "archived_at", "modified_at", "completion_rate"),
            )

        maximum_version = StudyPlanMypage.objects.filter(user_id=user_id).aggregate(
            maximum_version=Max("plan_version"),
        )["maximum_version"]
        next_version = int(maximum_version or 0) + 1
        new_plan = StudyPlanMypage.objects.create(
            user_id=user_id,
            study_plans=json.dumps(summary, ensure_ascii=False),
            study_plan_items=json.dumps(prepared_plans, ensure_ascii=False),
            created_at=current_time,
            modified_at=current_time,
            status="active",
            plan_version=next_version,
            start_date=date.fromisoformat(str(draft["startDate"])[:10]),
            end_date=date.fromisoformat(str(draft["endDate"])[:10]),
            completion_rate=0.0,
            archived_at=None,
            deleted_at=None,
        )
    return {
        "changed": True,
        "studyPlan": build_study_plan_dto(new_plan, today),
    }


def synchronize_active_study_plan(
    user_id: int,
    study_plan_id: int,
    today: date | None = None,
) -> dict[str, object] | None:
    from analytics.models import StudyPlanMypage
    from django.db import transaction
    from django.utils import timezone
    from user.models import UserAccounts

    config = get_study_plan_config()
    base_date = today or timezone.localdate(
        timezone=ZoneInfo(config.timezone),
    )
    with transaction.atomic():
        user = (
            UserAccounts.objects.select_for_update()
            .only("user_id", "daily_available_hours")
            .get(user_id=user_id)
        )
        active_plans = list(
            StudyPlanMypage.objects.select_for_update()
            .filter(user_id=user_id, status="active")
            .order_by("-plan_version", "-modified_at")
        )
        if len(active_plans) > 1:
            raise StudyPlanDataIntegrityError("활성 학습계획이 두 개 이상입니다.")
        active_plan = None
        if active_plans:
            active_plan = active_plans[0]
        if active_plan is None:
            return None
        completed_ids, in_progress_ids = _get_progress_block_ids(
            user_id,
            active_plan.studyplan_id,
        )
        if active_plan.studyplan_id != study_plan_id:
            return {
                "changed": False,
                "studyPlan": build_study_plan_dto(
                    active_plan,
                    base_date,
                    completed_ids,
                    in_progress_ids,
                ),
            }

        daily_hours = user.daily_available_hours or Decimal(0)
        daily_minutes = int(daily_hours * config.minutes_per_hour)
        if daily_minutes <= 0:
            daily_minutes = config.default_daily_minutes
        daily_block_limit = len(calculate_daily_block_minutes(daily_minutes, config))
        synchronized = synchronize_plan_items(
            parse_plan_items(active_plan.study_plan_items),
            base_date,
            daily_block_limit,
            completed_ids,
            in_progress_ids,
        )
        active_plan.study_plan_items = json.dumps(synchronized["plans"], ensure_ascii=False)
        dto = build_study_plan_dto(
            active_plan,
            base_date,
            completed_ids,
            in_progress_ids,
        )
        projection_changed = active_plan.completion_rate != dto["completionRate"]
        if synchronized["changed"] or projection_changed:
            active_plan.completion_rate = dto["completionRate"]
            active_plan.modified_at = timezone.now()
            active_plan.save(
                update_fields=("study_plan_items", "completion_rate", "modified_at"),
            )
        return {
            "changed": bool(synchronized["changed"] or projection_changed),
            "studyPlan": dto,
        }


def complete_study_plan_block_by_id(
    user_id: int,
    study_plan_id: int,
    block_id: str,
) -> dict[str, object] | None:
    from analytics.models import StudyPlanMypage
    from django.db import transaction
    from django.utils import timezone

    with transaction.atomic():
        study_plan = (
            StudyPlanMypage.objects.select_for_update()
            .filter(
                user_id=user_id,
                studyplan_id=study_plan_id,
                status__in=("active", "archived"),
            )
            .first()
        )
        if study_plan is None:
            return None
        valid_session_ids = _get_valid_completed_session_ids(
            user_id,
            study_plan_id,
            block_id,
        )
        if not valid_session_ids:
            return None
        elif len(valid_session_ids) > 1:
            raise StudyPlanDataIntegrityError("블록에 유효한 완료 세션이 두 개 이상입니다.")

        plan_items = parse_plan_items(study_plan.study_plan_items)
        validate_plan_items_for_mutation(plan_items)
        matched_block = None
        for day_plan in plan_items:
            for block in day_plan.get("blocks", []):
                if str(block.get("blockId")) == str(block_id):
                    matched_block = block
                    break
            if matched_block is not None:
                break
        if matched_block is None:
            raise StudyPlanDataIntegrityError("풀이 기록과 연결된 블록이 계획에 없습니다.")

        matched_block["status"] = "completed"
        if "isCompleted" in matched_block:
            matched_block["isCompleted"] = True
        study_plan.study_plan_items = json.dumps(plan_items, ensure_ascii=False)
        completed_ids, in_progress_ids = _get_progress_block_ids(user_id, study_plan_id)
        dto = build_study_plan_dto(
            study_plan,
            timezone.localdate(
                timezone=ZoneInfo(get_study_plan_config().timezone),
            ),
            completed_ids,
            in_progress_ids,
        )
        study_plan.completion_rate = dto["completionRate"]
        study_plan.modified_at = timezone.now()
        study_plan.save(
            update_fields=("study_plan_items", "completion_rate", "modified_at"),
        )
        if is_weekly_review_plan_block(matched_block):
            # 커밋 뒤에 불러야 한다. 근거 수집이 방금 완료된 블록을 읽어야 하고,
            # 트랜잭션이 열려 있는 동안 스레드를 띄우면 그 스레드가 미완료 상태를 본다.
            _schedule_weekly_report(user_id, study_plan_id, valid_session_ids[0])
        return dto


def _schedule_weekly_report(
    user_id: int,
    study_plan_id: int,
    source_session_id: int,
) -> None:
    """주간복습이 끝난 직후 주간 리포트 생성을 예약한다.

    diagnosis 앱이 이 함수를 직접 부르지는 않는다. 주간복습 제출이 결국
    complete_study_plan_block_by_id 를 타므로, 트리거를 analytics 안에 둔다.
    """
    from django.db import transaction

    from analytics.service.weekly_report.dispatcher import dispatch_weekly_report

    transaction.on_commit(
        lambda: dispatch_weekly_report(user_id, int(source_session_id), study_plan_id),
    )


def validate_study_plan_block_start(
    user_id: int,
    study_plan_id: int,
    block_id: str,
    route: str,
    today: date | None = None,
) -> dict[str, object]:
    from analytics.models import StudyPlanMypage
    from django.utils import timezone

    study_plan = StudyPlanMypage.objects.filter(
        user_id=user_id,
        studyplan_id=study_plan_id,
        status="active",
    ).first()
    if study_plan is None:
        raise StudyPlanBlockNotFound("활성 학습계획을 찾을 수 없습니다.")
    return validate_block_start(
        study_plan.status,
        parse_plan_items(study_plan.study_plan_items),
        block_id,
        today
        or timezone.localdate(
            timezone=ZoneInfo(get_study_plan_config().timezone),
        ),
        route,
    )


def is_weekly_review_plan_block(block: Mapping[str, object]) -> bool:
    return normalize_block_type(block) == "weekly_review"


def synchronize_plan_items(
    plan_items: Sequence[Mapping[str, object]],
    today: date,
    daily_block_limit: int,
    completed_block_ids: set[str] | None = None,
    in_progress_block_ids: set[str] | None = None,
) -> dict[str, object]:
    completed_ids = completed_block_ids or set()
    in_progress_ids = in_progress_block_ids or set()
    synchronized_items = parse_plan_items(list(plan_items))
    validate_plan_items_for_mutation(synchronized_items)
    before_items = copy.deepcopy(synchronized_items)
    rollover_candidates: list[tuple[dict[str, object], dict[str, object], date]] = []
    today_plan = None
    plan_dates = [_parse_required_date(day_plan.get("date")) for day_plan in synchronized_items]
    plan_start = min(plan_dates)
    plan_end = max(plan_dates)

    for day_plan in synchronized_items:
        plan_date = _parse_required_date(day_plan.get("date"))
        if plan_date == today:
            today_plan = day_plan
        for block in day_plan.get("blocks", []):
            block_id = str(block["blockId"])
            block["blockType"] = normalize_block_type(block)
            block["status"] = normalize_block_status(
                block,
                block_id,
                plan_date,
                today,
                completed_ids,
                in_progress_ids,
            )
            if is_legacy_extra_block(block) and block["status"] != "completed":
                block["status"] = "cancelled"
                continue
            if (
                plan_date < today
                and block["blockType"] == "practice"
                and block["status"] in ("scheduled", "in_progress")
            ):
                rollover_candidates.append((day_plan, block, plan_date))

    if today_plan is None and plan_start <= today <= plan_end:
        today_plan = {"date": today.isoformat(), "blocks": []}
        synchronized_items.append(today_plan)

    today_blocks: list[dict[str, object]] = []
    if today_plan is not None:
        today_blocks = today_plan.get("blocks", [])
    today_has_weekly_review = any(
        normalize_block_type(block) == "weekly_review" for block in today_blocks
    )
    used_capacity = sum(
        1
        for block in today_blocks
        if normalize_block_type(block) == "practice"
        and not is_legacy_extra_block(block)
        and str(block.get("status") or "scheduled") in ("scheduled", "in_progress", "completed")
    )
    remaining_capacity = 0
    if today_plan is not None and not today_has_weekly_review:
        remaining_capacity = max(daily_block_limit - used_capacity, 0)
    rollover_candidates.sort(
        key=lambda item: (
            -float(item[1].get("priorityScore") or 0.0),
            str(item[1].get("originalDate") or item[2].isoformat()),
            str(item[1]["blockId"]),
        )
    )

    rolled_over_block_ids: list[str] = []
    cancelled_block_ids: list[str] = []
    for source_day, block, source_date in rollover_candidates:
        rollover_count = int(block.get("rolloverCount") or 0)
        if rollover_count >= 2:
            block["status"] = "cancelled"
            cancelled_block_ids.append(str(block["blockId"]))
        elif remaining_capacity > 0:
            source_day["blocks"].remove(block)
            block["originalDate"] = str(block.get("originalDate") or source_date.isoformat())
            block["rolloverCount"] = rollover_count + 1
            today_plan.setdefault("blocks", []).append(block)
            remaining_capacity -= 1
            rolled_over_block_ids.append(str(block["blockId"]))

    synchronized_items.sort(key=lambda item: str(item.get("date") or ""))
    return {
        "changed": synchronized_items != before_items,
        "plans": synchronized_items,
        "rolledOverBlockIds": rolled_over_block_ids,
        "cancelledBlockIds": cancelled_block_ids,
    }


def validate_plan_items_for_mutation(
    plan_items: Sequence[Mapping[str, object]],
) -> None:
    block_ids: set[str] = set()
    for day_plan in plan_items:
        if not isinstance(day_plan, Mapping):
            raise StudyPlanDataIntegrityError("계획 일자 형식이 올바르지 않습니다.")
        _parse_required_date(day_plan.get("date"))
        blocks = day_plan.get("blocks", [])
        if not isinstance(blocks, list):
            raise StudyPlanDataIntegrityError("계획 블록 목록 형식이 올바르지 않습니다.")
        for block in blocks:
            if not isinstance(block, Mapping):
                raise StudyPlanDataIntegrityError("계획 블록 형식이 올바르지 않습니다.")
            block_id = str(block.get("blockId") or "").strip()
            if not block_id:
                raise StudyPlanDataIntegrityError("블록 번호가 누락되었습니다.")
            elif block_id in block_ids:
                raise StudyPlanDataIntegrityError("중복 블록 번호가 있습니다.")
            block_ids.add(block_id)


def validate_block_start(
    plan_status: str,
    plan_items: Sequence[Mapping[str, object]],
    block_id: str,
    today: date,
    route: str,
) -> dict[str, object]:
    if plan_status != "active":
        raise StudyPlanBlockTerminal("활성 계획의 블록만 새로 시작할 수 있습니다.")

    for day_plan in plan_items:
        plan_date = _parse_required_date(day_plan.get("date"))
        for block in day_plan.get("blocks", []):
            if str(block.get("blockId") or "") != str(block_id):
                continue
            block_type = normalize_block_type(block)
            expected_route = "question"
            if block_type == "weekly_review":
                expected_route = "diagnosis"
            status = normalize_block_status(
                block,
                str(block_id),
                plan_date,
                today,
                set(),
                set(),
            )
            if status in ("completed", "missed", "cancelled"):
                raise StudyPlanBlockTerminal("종료된 블록은 새로 시작할 수 없습니다.")
            elif plan_date != today:
                raise StudyPlanBlockNotDue("오늘 예정된 블록만 시작할 수 있습니다.")
            elif route != expected_route:
                raise StudyPlanBlockRouteMismatch("블록 실행 경로가 일치하지 않습니다.")
            return dict(block)
    raise StudyPlanBlockNotFound("학습계획 블록을 찾을 수 없습니다.")


def _parse_required_date(value: object) -> date:
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError) as error:
        raise StudyPlanDataIntegrityError("계획 날짜 형식이 올바르지 않습니다.") from error


def _get_progress_block_ids(
    user_id: int,
    study_plan_id: int,
) -> tuple[set[str], set[str]]:
    from django.db.models import Count
    from question.models import SolveRecords

    session_rows = (
        SolveRecords.objects.filter(
            session__user_id=user_id,
            studyplan_id=study_plan_id,
        )
        .exclude(study_plan_block_id__isnull=True)
        .exclude(study_plan_block_id="")
        .values(
            "session_id",
            "study_plan_block_id",
            "session__status",
            "session__total_count",
        )
        .annotate(record_count=Count("record_id"))
    )
    completed_ids: set[str] = set()
    in_progress_ids: set[str] = set()
    for row in session_rows:
        block_id = str(row["study_plan_block_id"])
        if (
            row["session__status"] == "completed"
            and row["record_count"] == row["session__total_count"]
        ):
            completed_ids.add(block_id)
        elif row["session__status"] == "in_progress":
            in_progress_ids.add(block_id)
    return completed_ids, in_progress_ids - completed_ids


def _get_valid_completed_session_ids(
    user_id: int,
    study_plan_id: int,
    block_id: str,
) -> list[int]:
    from django.db.models import Count
    from question.models import SolveRecords

    rows = (
        SolveRecords.objects.filter(
            session__user_id=user_id,
            session__status="completed",
            studyplan_id=study_plan_id,
            study_plan_block_id=str(block_id),
        )
        .values("session_id", "session__total_count")
        .annotate(record_count=Count("record_id"))
        .order_by("session_id")
    )
    return [
        int(row["session_id"])
        for row in rows
        if row["record_count"] == row["session__total_count"]
    ]
