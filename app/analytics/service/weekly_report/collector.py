"""주간 리포트 근거 수집.

DB 에서 build_report_result 의 인자를 모아 AI 에게 줄 근거 뭉치를 만든다.
LLM 은 부르지 않는다. 선별·정렬·evidenceId 부여는 service.build_report_result 가 한다.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Mapping

from django.db import DatabaseError
from django.db.models import Count, F, Q, Sum
from django.utils import timezone

from analytics.models import StudyPlanMypage
from analytics.service.study_plan.config import get_study_plan_config
from analytics.service.exam_trend import get_recent_exam_trends
from analytics.service.study_plan.service import build_plan_targets
from analytics.service.weakness import build_weakness_rows, get_weakness_config
from analytics.service.weekly_report import repository
from analytics.service.weekly_report.config import (
    WeeklyReportConfig,
    get_weekly_report_config,
)
from analytics.service.weekly_report.relation_evidence import (
    ChoiceRelationResolver,
    build_weekly_confusion_patterns,
)
from analytics.service.weekly_report.service import (
    build_pending_report,
    build_report_result,
)
from question.models import SolveRecords, SolveSessions


logger = logging.getLogger(__name__)


class WeeklyReportSourceUnavailable(ValueError):
    """리포트를 만들 근거 세션이 없다."""


def enqueue_weekly_report(
    user_id: int,
    source_session_id: int,
    study_plan_id: int,
    today: date | None = None,
    resolver: ChoiceRelationResolver | None = None,
    config: WeeklyReportConfig | None = None,
) -> bool:
    """주간복습 완료 직후 pending 리포트를 저장한다. 새로 만들었으면 True.

    리포트 생성 실패가 평가 제출 응답을 깨면 안 되므로 예외를 삼킨다.
    실패한 건은 워커의 복구 스캔이 다시 줍는다.
    """
    try:
        existing_report = _load_plan_report(study_plan_id)
        if existing_report is not None:
            if existing_report.get("sourceSessionId") == source_session_id:
                return False

        resolved_config = config or get_weekly_report_config()
        base_date = today or timezone.localdate(
            timezone=_get_report_timezone(),
        )
        collected = collect_weekly_report_result(
            user_id,
            source_session_id,
            study_plan_id,
            base_date,
            resolver,
            resolved_config,
        )
        report = build_pending_report(
            source_session_id,
            str(collected["reportType"]),
            collected["result"],
            timezone.now(),
            config=resolved_config,
        )
        repository.save_report(study_plan_id, report)
        return True
    except Exception:
        logger.exception(
            "주간 리포트 생성 실패 user=%s session=%s plan=%s",
            user_id,
            source_session_id,
            study_plan_id,
        )
        return False


def collect_weekly_report_result(
    user_id: int,
    source_session_id: int,
    study_plan_id: int,
    today: date,
    resolver: ChoiceRelationResolver | None = None,
    config: WeeklyReportConfig | None = None,
) -> dict[str, object]:
    """build_report_result 인자를 모아 {reportType, result} 를 만든다."""
    resolved_config = config or get_weekly_report_config()
    plan_config = get_study_plan_config()
    period_start = today - timedelta(days=plan_config.weekly_plan_days - 1)

    priority_targets = _build_priority_targets_or_empty(user_id, today)
    return build_report_result(
        assessment=build_assessment(source_session_id),
        baseline=build_baseline(user_id, source_session_id),
        plan_progress=build_plan_progress(user_id, study_plan_id),
        weakness_rows=build_weakness_rows(
            _completed_records(user_id),
            ("era", "topic"),
            today,
        ),
        repeated_error_by_group={
            target.group_key_id: target.repeated_error for target in priority_targets
        },
        time_records=collect_time_records(user_id, period_start, today),
        priority_targets=priority_targets,
        concept_rows=collect_concept_weakness_rows(user_id, today),
        exam_trends=collect_exam_trends(),
        snapshot_at=timezone.now(),
        recovered_snapshot=False,
        generation_reason=_get_generation_reason(priority_targets),
        has_previous_weekly_review=has_previous_weekly_review(user_id, source_session_id),
        config=resolved_config,
        confusion_patterns=build_weekly_confusion_patterns(
            user_id,
            period_start,
            today,
            resolver,
            resolved_config,
        ),
    )


def build_assessment(session_id: int) -> dict[str, object]:
    """이번 세션의 평가 요약.

    score 는 문항 개수가 아니라 가중 점수다. SolveSessions.total_score 와 같은
    정의이고, 진단 결과 페이지가 보여주는 74/100 과 숫자가 일치해야 한다.
    render_report_dto 가 "N점 상승" 이라고 표기하기 때문이다.
    """
    summary = _score_summary(session_id)
    if summary is None:
        raise WeeklyReportSourceUnavailable(
            f"평가 기록이 없습니다. session_id={session_id}",
        )
    return summary


def build_baseline(user_id: int, source_session_id: int) -> dict[str, object] | None:
    """비교 기준 세션. 없으면 None.

    직전 주간복습을 우선하고, 없으면 직전 진단평가를 쓴다.
    같은 우선순위 규칙이 analytics.get_diagnosis_comparison_pair 에도 있다.
    그쪽은 마이페이지 화면용이라 반환 형태가 다르다. 규칙을 바꾸면 두 곳을 같이 고친다.
    """
    baseline_session = _find_baseline_session(user_id, source_session_id)
    if baseline_session is None:
        return None

    summary = _score_summary(baseline_session.session_id)
    if summary is None:
        return None
    summary["type"] = _get_review_type(baseline_session)
    return summary


def build_plan_progress(user_id: int, study_plan_id: int) -> dict[str, object]:
    """완료된 블록이 속한 계획의 진척도.

    활성 계획을 다시 조회하면 안 된다. 사용자가 그 사이 다음 주 계획을 만들었으면
    한 주를 다 끝냈는데도 완료율 0% 가 된다.
    """
    from analytics.service.studyplan import calculate_record_based_plan_progress

    study_plan = StudyPlanMypage.objects.filter(studyplan_id=study_plan_id).first()
    if study_plan is None:
        return {"targetCount": 0, "achievedCount": 0, "completionRate": 0.0}

    summary = dict(calculate_record_based_plan_progress(user_id, study_plan)["summary"])
    summary["completionRate"] = min(max(float(summary.get("completionRate") or 0.0), 0.0), 1.0)
    return summary


def collect_time_records(
    user_id: int,
    period_start: date,
    period_end: date,
) -> list[dict[str, object]]:
    """기간 내 풀이 시간 기록. _build_time_summary 가 유효값을 걸러낸다."""
    return list(
        SolveRecords.objects.filter(
            session__user_id=user_id,
            session__status="completed",
            session__recorded_date__gte=period_start,
            session__recorded_date__lte=period_end,
        ).values("time_spent_ms", "q_type")
    )


def collect_concept_weakness_rows(user_id: int, today: date) -> list[dict[str, object]]:
    """핵심 개념 단위 취약 판정 row.

    core_concept 은 SolveRecords 가 아니라 Questions 에 있어 조인이 필요하다.
    build_weakness_rows 에 QuerySet 을 그대로 넘기면 내부에서 values() 를 다시
    부르면서 별칭이 깨지므로, 여기서 목록으로 만들어 넘긴다.
    """
    weakness_config = get_weakness_config()
    period_start = today - timedelta(days=weakness_config.lookback_days - 1)
    records = list(
        SolveRecords.objects.filter(
            session__user_id=user_id,
            session__status="completed",
            session__recorded_date__gte=period_start,
            session__recorded_date__lte=today,
        )
        .exclude(question__core_concept="")
        .values(
            "is_correct",
            "time_spent_ms",
            "session__recorded_date",
            coreConcept=F("question__core_concept"),
        )
    )
    if not records:
        return []
    return build_weakness_rows(records, ("coreConcept",), today)


def collect_exam_trends() -> list[dict[str, object]]:
    """최근 출제 경향 TOP5. ml_trend_top5 가 비어 있으면 빈 목록."""
    try:
        return get_recent_exam_trends()
    except DatabaseError:
        logger.warning("출제 경향 조회에 실패했습니다. 리포트는 경향 없이 만듭니다.")
        return []


def has_previous_weekly_review(user_id: int, source_session_id: int) -> bool:
    """이번 세션 이전에 완료된 주간복습이 있으면 True. 첫 주 판정에 쓴다."""
    from analytics.service.analytics import get_completed_weekly_review_sessions

    source_session = _get_session(source_session_id)
    if source_session is None:
        return False
    return any(
        _is_session_before(session, source_session)
        for session in get_completed_weekly_review_sessions(user_id)
    )


def find_recoverable_sessions() -> list[dict[str, object]]:
    """리포트가 없는 활성 계획 중, 그 계획에서 실제로 주간복습을 마친 건을 돌려준다.

    워커의 복구 스캔이 쓴다. 트리거가 실패해 리포트가 누락된 건을 줍는 용도다.

    계획에 연결된 세션만 본다. 사용자의 최근 주간복습 세션을 그대로 가져다
    쓰면, 리포트가 없는 것이 정상인 갓 만든 계획에 지난주 리포트가 얹힌다.
    """
    recoverable: list[dict[str, object]] = []
    for plan in repository.find_plans_without_report():
        study_plan_id = int(plan["studyplan_id"])
        source_session_id = _find_plan_weekly_review_session(study_plan_id)
        if source_session_id is None:
            continue
        recoverable.append(
            {
                "userId": int(plan["user_id"]),
                "studyPlanId": study_plan_id,
                "sourceSessionId": source_session_id,
            }
        )
    return recoverable


def _find_plan_weekly_review_session(study_plan_id: int) -> int | None:
    """이 계획의 주간복습 블록으로 완료된 세션. 없으면 None."""
    from analytics.serializers import parse_study_plan_items
    from analytics.service.studyplan import is_weekly_review_plan_block

    study_plan = StudyPlanMypage.objects.filter(studyplan_id=study_plan_id).first()
    if study_plan is None:
        return None

    block_ids = [
        str(block.get("blockId"))
        for day_plan in parse_study_plan_items(study_plan.study_plan_items)
        for block in day_plan.get("blocks", [])
        if block.get("blockId") and is_weekly_review_plan_block(block)
    ]
    if not block_ids:
        return None

    session_id = (
        SolveRecords.objects.filter(
            studyplan_id=study_plan_id,
            study_plan_block_id__in=block_ids,
            session__status="completed",
        )
        .order_by("-session_id")
        .values_list("session_id", flat=True)
        .first()
    )
    if session_id is None:
        return None
    return int(session_id)


def _build_priority_targets_or_empty(user_id: int, today: date) -> list[object]:
    """학습 기록이나 문제은행이 부족해도 리포트는 만든다. nextPlanTargets 만 비워진다."""
    from django.core.exceptions import ObjectDoesNotExist

    try:
        priority_targets, _ = build_plan_targets(user_id, today)
        return priority_targets
    except (ValueError, ObjectDoesNotExist):
        logger.info("우선순위 학습 대상을 만들지 못했습니다. user=%s", user_id)
        return []


def _score_summary(session_id: int) -> dict[str, object] | None:
    session = _get_session(session_id)
    if session is None:
        return None

    totals = SolveRecords.objects.filter(session_id=session_id).aggregate(
        record_count=Count("record_id"),
        max_score=Sum("q_score"),
        earned_score=Sum("q_score", filter=Q(is_correct=True)),
    )
    if not totals["record_count"]:
        return None
    return {
        "sessionId": session.session_id,
        "score": int(totals["earned_score"] or 0),
        "totalScore": int(totals["max_score"] or 0),
        "questionCount": int(totals["record_count"]),
        "recordedDate": session.recorded_date.isoformat(),
    }


def _find_baseline_session(user_id: int, source_session_id: int):
    from analytics.service.analytics import (
        get_completed_diagnostic_sessions,
        get_completed_weekly_review_sessions,
    )

    source_session = _get_session(source_session_id)
    if source_session is None:
        return None

    weekly_sessions = list(get_completed_weekly_review_sessions(user_id))
    previous_weekly = [
        session
        for session in weekly_sessions
        if _is_session_before(session, source_session)
    ]
    if previous_weekly:
        return previous_weekly[-1]

    weekly_session_ids = [session.session_id for session in weekly_sessions]
    previous_diagnostic = [
        session
        for session in get_completed_diagnostic_sessions(user_id).exclude(
            session_id__in=weekly_session_ids,
        )
        if _is_session_before(session, source_session)
    ]
    if previous_diagnostic:
        return previous_diagnostic[-1]
    return None


def _get_review_type(session) -> str:
    from analytics.service.analytics import get_completed_weekly_review_sessions

    weekly_session_ids = {
        weekly_session.session_id
        for weekly_session in get_completed_weekly_review_sessions(session.user_id)
    }
    if session.session_id in weekly_session_ids:
        return "weekly_review"
    return "diagnostic"


def _get_generation_reason(priority_targets) -> str | None:
    if not priority_targets:
        return None
    return priority_targets[0].generation_reason


def _completed_records(user_id: int):
    """전체 완료 기록. build_weakness_rows 가 자체 90일 필터를 걸므로 자르지 않는다."""
    return SolveRecords.objects.filter(
        session__user_id=user_id,
        session__status="completed",
    )


def _get_session(session_id: int):
    return SolveSessions.objects.filter(session_id=session_id).first()


def _is_session_before(candidate_session, reference_session) -> bool:
    candidate_key = (candidate_session.recorded_date, candidate_session.session_id)
    reference_key = (reference_session.recorded_date, reference_session.session_id)
    return candidate_key < reference_key


def _get_report_timezone():
    from zoneinfo import ZoneInfo

    return ZoneInfo(get_study_plan_config().timezone)


def _load_plan_report(study_plan_id: int) -> Mapping[str, object] | None:
    plan = StudyPlanMypage.objects.filter(studyplan_id=study_plan_id).first()
    if plan is None:
        return None
    elif not isinstance(plan.weekly_report_data, dict):
        return None
    return plan.weekly_report_data
