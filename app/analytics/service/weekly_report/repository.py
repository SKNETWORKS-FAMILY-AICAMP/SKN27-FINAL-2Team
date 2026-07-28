"""주간 리포트 저장소.

StudyPlanMypage.weekly_report_data (JSONB) 한 칸을 읽고 쓴다.
리포트 내용의 해석은 service.py, 상태 전이 판단은 worker.py 가 맡는다.
이 파일이 아는 것은 어느 행을 잠그고 언제 커밋하느냐다.
"""

from __future__ import annotations

from datetime import datetime
from typing import Mapping

from django.db import transaction
from django.utils import timezone

from analytics.models import StudyPlanMypage
from analytics.service.weekly_report.config import WeeklyReportConfig
from analytics.service.weekly_report.worker import (
    claim_report,
    complete_report,
    schedule_report_retry,
)


class StudyPlanNotFound(LookupError):
    """리포트를 저장할 학습계획이 없다."""


def load_report(user_id: int) -> dict[str, object] | None:
    """화면에 보여줄 주간 리포트를 읽는다. 없으면 None.

    활성 계획의 리포트를 우선하고, 없으면 리포트를 가진 가장 최근 계획으로
    폴백한다. 새 계획을 만들었다고 지난주 리포트가 사라지지 않게 하기 위해서다.
    정렬에 modified_at 을 쓰지 않는 이유는 그 값이 리포트와 무관한 갱신으로도
    바뀌기 때문이다. plan_version 은 생성 시점에 정해지고 변하지 않는다.
    """
    plans = StudyPlanMypage.objects.filter(user_id=user_id).exclude(status="deleted")
    active_plan = plans.filter(status="active").first()
    if active_plan is not None and isinstance(active_plan.weekly_report_data, dict):
        return active_plan.weekly_report_data

    fallback_plans = plans.filter(weekly_report_data__isnull=False).order_by(
        "-plan_version",
        "-studyplan_id",
    )
    # JSONB 는 'null' 이나 배열도 담을 수 있어 isnull 만으로는 걸러지지 않는다.
    # 하나가 이상하다고 멈추면 멀쩡한 옛 리포트까지 가려진다.
    for plan in fallback_plans:
        if isinstance(plan.weekly_report_data, dict):
            return plan.weekly_report_data
    return None


def save_report(study_plan_id: int, report: Mapping[str, object]) -> None:
    """리포트를 통째로 덮어쓴다.

    부분 갱신은 제공하지 않는다. worker.py 의 함수들이 전체 dict 를 돌려주므로
    부분 갱신을 섞으면 어느 쪽이 최신인지 알 수 없게 된다.
    """
    if not isinstance(report, Mapping):
        raise TypeError("주간 리포트는 JSON 객체여야 합니다.")

    updated_count = StudyPlanMypage.objects.filter(studyplan_id=study_plan_id).update(
        weekly_report_data=dict(report),
        modified_at=timezone.now(),
    )
    if updated_count == 0:
        raise StudyPlanNotFound(f"학습계획을 찾을 수 없습니다. studyplan_id={study_plan_id}")


def claim_next_report(
    now: datetime,
    config: WeeklyReportConfig,
) -> dict[str, object] | None:
    """처리할 리포트 1건을 잡는다. {studyPlanId, report} 또는 None.

    반환 후 트랜잭션이 닫힌 상태여야 한다. startedAt 이 커밋되어 있어야
    프로세스가 죽었을 때 다음 워커가 stuck 판정을 할 수 있다.
    LLM 호출은 반드시 이 함수 밖에서 한다.
    """
    with transaction.atomic():
        candidates = (
            StudyPlanMypage.objects.filter(
                weekly_report_data__status__in=("pending", "running"),
            )
            .order_by("studyplan_id")
            .select_for_update(skip_locked=True)[:config.claim_candidate_count]
        )
        # 후보를 하나만 보면, 백오프 대기 중이거나 다른 워커가 처리 중인 건
        # 하나가 큐 전체를 막는다. 잡을 수 있는 것이 나올 때까지 훑는다.
        for plan in candidates:
            if not isinstance(plan.weekly_report_data, dict):
                continue
            claim_result = claim_report(plan.weekly_report_data, now, config)
            if claim_result["changed"]:
                save_report(plan.studyplan_id, claim_result["report"])
            if claim_result["claimed"]:
                return {
                    "studyPlanId": plan.studyplan_id,
                    "report": claim_result["report"],
                }
        return None


def finish_report(
    study_plan_id: int,
    expected_attempt_count: int,
    content: Mapping[str, object],
    now: datetime,
) -> bool:
    """LLM 결과를 확정한다. 반영됐으면 True.

    False 는 그동안 다른 워커가 이 건을 다시 시도했다는 뜻이고 정상 상황이다.
    """
    with transaction.atomic():
        report = _lock_report(study_plan_id)
        if report is None:
            return False

        finish_result = complete_report(report, expected_attempt_count, content, now)
        if not finish_result["changed"]:
            return False
        save_report(study_plan_id, finish_result["report"])
        return True


def retry_report(
    study_plan_id: int,
    expected_attempt_count: int,
    error_code: str,
    now: datetime,
    config: WeeklyReportConfig,
) -> bool:
    """실패를 기록하고 백오프 후 재시도를 예약한다. 반영됐으면 True.

    마지막 시도에서는 호출하지 않는다. schedule_report_retry 가 시도 횟수를
    초과한 리포트를 failed 로 만들어 사용자에게 빈 리포트가 나간다.
    마지막 시도는 finish_report 로 기본 문구를 확정한다.
    """
    with transaction.atomic():
        report = _lock_report(study_plan_id)
        if report is None:
            return False

        retry_result = schedule_report_retry(
            report,
            expected_attempt_count,
            error_code,
            now,
            config,
        )
        if not retry_result["changed"]:
            return False
        save_report(study_plan_id, retry_result["report"])
        return True


def find_plans_without_report() -> list[dict[str, object]]:
    """리포트가 없는 활성 계획 목록. 복구 스캔이 쓴다."""
    return list(
        StudyPlanMypage.objects.filter(
            status="active",
            weekly_report_data__isnull=True,
        )
        .order_by("studyplan_id")
        .values("studyplan_id", "user_id")
    )


def find_next_plan_candidates() -> list[dict[str, object]]:
    """리포트는 ready 인데 다음 계획이 pending 으로 남은 행. 복구 스캔이 쓴다.

    다음 계획 생성 직전에 워커가 죽은 건을 다시 집기 위한 조회다.
    blocked 는 여기서 다루지 않는다. 진행 세션 재확인은 마이페이지 동기화가
    recheck_user_next_plan 으로 수행한다.
    """
    rows = (
        StudyPlanMypage.objects.filter(
            weekly_report_data__status="ready",
            weekly_report_data__nextPlan__status="pending",
        )
        .order_by("studyplan_id")
        .values("studyplan_id", "weekly_report_data")
    )
    return [
        {"studyPlanId": row["studyplan_id"], "report": row["weekly_report_data"]}
        for row in rows
    ]


def _lock_report(study_plan_id: int) -> dict[str, object] | None:
    """행을 잠그고 최신 리포트를 읽는다.

    claim 시점에 읽은 리포트를 재사용하면 안 된다. 그 사이 LLM 이 수십 초 돌고,
    그동안 stuck 판정으로 다른 워커가 attemptCount 를 올렸을 수 있다.
    """
    plan = (
        StudyPlanMypage.objects.select_for_update()
        .filter(studyplan_id=study_plan_id)
        .first()
    )
    if plan is None:
        return None
    elif not isinstance(plan.weekly_report_data, dict):
        return None
    return plan.weekly_report_data
