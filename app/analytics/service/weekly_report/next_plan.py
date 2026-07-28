"""리포트 확정 뒤 다음 계획 자동 생성.

설계(학습계획_AI주간리포트_통합설계.md 3.4)의 미구현 구간을 담당한다.

- 리포트가 ready 로 확정되면 같은 작업이 process_next_plan 을 불러
  다음 계획을 자동 생성한다. 별도 생성 버튼은 없다.
- 진행 중 풀이 세션이 있으면 blocked 로 보류한다. 마이페이지 동기화가
  recheck_user_next_plan 으로 다시 확인해 생성한다.
- 워커가 생성 직전에 죽어도 run_weekly_report_worker 의 복구 스캔이
  ready + nextPlan pending 행을 다시 집는다. AI 는 다시 부르지 않는다.
- 원본 계획 보관과 새 계획 활성화, nextPlan 상태 기록은 한 트랜잭션이다.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Callable, Mapping

from django.db import DatabaseError, transaction
from django.utils import timezone

from analytics.models import StudyPlanMypage
from analytics.service.weekly_report import repository
from analytics.service.weekly_report.config import (
    WeeklyReportConfig,
    get_weekly_report_config,
)
from analytics.service.weekly_report.worker import (
    NEXT_PLAN_ACTIONABLE_STATUSES,
    defer_next_plan,
    mark_next_plan_blocked,
    mark_next_plan_failed,
    mark_next_plan_succeeded,
)


logger = logging.getLogger(__name__)

NEXT_PLAN_IDLE = "idle"
NEXT_PLAN_SUCCEEDED = "succeeded"
NEXT_PLAN_BLOCKED = "blocked"
NEXT_PLAN_FAILED = "failed"
NEXT_PLAN_DEFERRED = "deferred"

IN_PROGRESS_SESSION_REASON = "IN_PROGRESS_SESSION"
GENERATION_UNAVAILABLE_REASON = "PLAN_GENERATION_UNAVAILABLE"


def process_next_plan(
    study_plan_id: int,
    config: WeeklyReportConfig | None = None,
    clock: Callable[[], datetime] = timezone.now,
) -> str:
    """원본 계획의 다음 계획을 생성·보류·확정하고 결과 코드를 돌려준다.

    설계의 잠금 순서(사용자 행 → 계획 행)를 지켜 생성·동기화와의 교착을
    피한다. create_personalized_study_plan 이 안에서 같은 행을 다시 잠그지만
    같은 트랜잭션이라 문제가 없다.
    """
    from analytics.service.study_plan.planner import QuestionPoolInsufficient
    from analytics.service.study_plan.service import (
        InitialStudyPlanConfigUnavailable,
        create_personalized_study_plan,
    )

    resolved_config = config or get_weekly_report_config()
    try:
        with transaction.atomic():
            plan = _lock_source_plan(study_plan_id)
            if plan is None:
                return NEXT_PLAN_IDLE
            report = plan.weekly_report_data
            if not _is_processable_report(report):
                return NEXT_PLAN_IDLE

            user_id = int(plan.user_id)
            existing_next_plan_id = _find_existing_next_plan_id(plan, report)
            if existing_next_plan_id is not None:
                return _save_transition(
                    study_plan_id,
                    mark_next_plan_succeeded(report, existing_next_plan_id),
                    NEXT_PLAN_SUCCEEDED,
                )
            if _has_in_progress_session(user_id, resolved_config):
                return _save_transition(
                    study_plan_id,
                    mark_next_plan_blocked(report, IN_PROGRESS_SESSION_REASON),
                    NEXT_PLAN_BLOCKED,
                )

            try:
                created = create_personalized_study_plan(
                    user_id,
                    source_study_plan_id=study_plan_id,
                )
            except (InitialStudyPlanConfigUnavailable, QuestionPoolInsufficient, ValueError) as error:
                # 후보 부족 같은 영구 오류다. pending 으로 두면 워커가 같은
                # 실패를 영원히 반복하므로 failed 로 닫는다.
                logger.warning(
                    "다음 계획 생성 불가 plan=%s 사유=%s", study_plan_id, error,
                )
                return _save_transition(
                    study_plan_id,
                    mark_next_plan_failed(report, GENERATION_UNAVAILABLE_REASON),
                    NEXT_PLAN_FAILED,
                )

            created_plan_id = _extract_created_plan_id(created, study_plan_id)
            if created_plan_id is None:
                return _save_transition(
                    study_plan_id,
                    mark_next_plan_failed(report, GENERATION_UNAVAILABLE_REASON),
                    NEXT_PLAN_FAILED,
                )
            return _save_transition(
                study_plan_id,
                mark_next_plan_succeeded(report, created_plan_id),
                NEXT_PLAN_SUCCEEDED,
            )
    except DatabaseError:
        # 일시적 인프라 오류다. pending 을 유지하고 재시도 시각만 미룬다.
        logger.exception("다음 계획 처리 중 DB 오류 plan=%s", study_plan_id)
        _defer_after_infra_error(study_plan_id, resolved_config, clock)
        return NEXT_PLAN_DEFERRED


def recheck_user_next_plan(
    user_id: int,
    config: WeeklyReportConfig | None = None,
) -> str:
    """마이페이지 동기화가 대기·보류 중 다음 계획을 다시 확인한다.

    풀이 세션이 끝난 뒤 마이페이지에 들어오면 이 경로가 보류를 풀고
    다음 계획을 생성한다. 확인할 건이 없으면 idle 을 돌려준다.
    """
    candidate_id = (
        StudyPlanMypage.objects.filter(
            user_id=user_id,
            weekly_report_data__status="ready",
            weekly_report_data__nextPlan__status__in=list(
                NEXT_PLAN_ACTIONABLE_STATUSES,
            ),
        )
        .order_by("-plan_version", "-studyplan_id")
        .values_list("studyplan_id", flat=True)
        .first()
    )
    if candidate_id is None:
        return NEXT_PLAN_IDLE
    return process_next_plan(int(candidate_id), config)


def _lock_source_plan(study_plan_id: int) -> StudyPlanMypage | None:
    """사용자 행을 먼저 잠근 뒤 원본 계획 행을 잠근다."""
    from user.models import UserAccounts

    plan_user_id = (
        StudyPlanMypage.objects.filter(studyplan_id=study_plan_id)
        .values_list("user_id", flat=True)
        .first()
    )
    if plan_user_id is None:
        return None
    UserAccounts.objects.select_for_update().only("user_id").get(user_id=plan_user_id)
    return (
        StudyPlanMypage.objects.select_for_update()
        .filter(studyplan_id=study_plan_id)
        .first()
    )


def _is_processable_report(report: object) -> bool:
    if not isinstance(report, Mapping):
        return False
    if report.get("status") != "ready":
        return False
    next_plan = report.get("nextPlan")
    if not isinstance(next_plan, Mapping):
        return False
    return str(next_plan.get("status") or "") in NEXT_PLAN_ACTIONABLE_STATUSES


def _find_existing_next_plan_id(
    plan: StudyPlanMypage,
    report: Mapping[str, object],
) -> int | None:
    """새로 만들지 않아도 되는 다음 계획 번호를 찾는다.

    이미 기록된 번호가 있으면 그대로 쓰고, 원본이 보관된 상태에서 다른
    활성 계획이 있으면 사용자가 먼저 만든 그 계획을 다음 계획으로 인정한다.
    """
    next_plan = dict(report.get("nextPlan") or {})
    recorded_id = next_plan.get("studyPlanId")
    if recorded_id:
        return int(recorded_id)
    if plan.status == "active":
        return None

    other_active_id = (
        StudyPlanMypage.objects.filter(user_id=plan.user_id, status="active")
        .exclude(studyplan_id=plan.studyplan_id)
        .values_list("studyplan_id", flat=True)
        .first()
    )
    if other_active_id is None:
        return None
    return int(other_active_id)


def _has_in_progress_session(user_id: int, config: WeeklyReportConfig) -> bool:
    """진행 중인 일반 문제풀이·진단평가 세션이 있는지 확인한다."""
    from question.models import SolveSessions

    return SolveSessions.objects.filter(
        user_id=user_id,
        status=config.in_progress_session_status,
    ).exists()


def _extract_created_plan_id(
    created: Mapping[str, object],
    source_study_plan_id: int,
) -> int | None:
    created_plan = dict(created.get("studyPlan") or {})
    created_plan_id = created_plan.get("studyPlanId")
    if not created_plan_id:
        return None
    if int(created_plan_id) == source_study_plan_id:
        # 생성 서비스가 원본을 그대로 돌려줬다면 새 계획이 만들어지지 않은 것이다.
        return None
    return int(created_plan_id)


def _save_transition(
    study_plan_id: int,
    transition: Mapping[str, object],
    result_code: str,
) -> str:
    if not transition["changed"]:
        return NEXT_PLAN_IDLE
    repository.save_report(study_plan_id, transition["report"])
    logger.info("다음 계획 상태 저장 plan=%s 결과=%s", study_plan_id, result_code)
    return result_code


def _defer_after_infra_error(
    study_plan_id: int,
    config: WeeklyReportConfig,
    clock: Callable[[], datetime],
) -> None:
    """새 트랜잭션에서 재시도 시각을 미룬다. 이마저 실패하면 로그만 남긴다.

    DB 오류 직후라 이 저장도 실패할 수 있다. 실패해도 pending 은 그대로라
    워커 복구 스캔이 다음 주기에 다시 시도한다.
    """
    try:
        with transaction.atomic():
            plan = (
                StudyPlanMypage.objects.select_for_update()
                .filter(studyplan_id=study_plan_id)
                .first()
            )
            if plan is None or not isinstance(plan.weekly_report_data, dict):
                return
            deferred = defer_next_plan(plan.weekly_report_data, clock(), config)
            if deferred["changed"]:
                repository.save_report(study_plan_id, deferred["report"])
    except DatabaseError:
        logger.exception("다음 계획 재시도 예약 실패 plan=%s", study_plan_id)
