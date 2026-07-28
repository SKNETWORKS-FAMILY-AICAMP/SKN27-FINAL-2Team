from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone
from typing import Mapping

from analytics.service.weekly_report.config import (
    WeeklyReportConfig,
    get_weekly_report_config,
)


NEXT_PLAN_ACTIONABLE_STATUSES = ("pending", "blocked")


def claim_report(
    report: Mapping[str, object],
    now: datetime,
    config: WeeklyReportConfig | None = None,
) -> dict[str, object]:
    resolved_config = config or get_weekly_report_config()
    claimed_report = copy.deepcopy(dict(report))
    worker = dict(claimed_report.get("worker") or {})
    claimed_report["worker"] = worker
    status = str(claimed_report.get("status") or "")
    if status == "running" and _is_stuck(worker, now, resolved_config):
        return schedule_report_retry(
            claimed_report,
            int(worker.get("attemptCount") or 0),
            "WORKER_STUCK",
            now,
            resolved_config,
        )
    if status != "pending":
        return {"claimed": False, "changed": False, "report": claimed_report}

    available_at = _parse_datetime(worker.get("availableAt"))
    if available_at is not None and available_at > _as_utc(now):
        return {"claimed": False, "changed": False, "report": claimed_report}
    attempt_count = int(worker.get("attemptCount") or 0)
    if attempt_count >= resolved_config.maximum_attempt_count:
        claimed_report["status"] = "failed"
        worker["lastError"] = "MAX_ATTEMPTS_EXCEEDED"
        return {"claimed": False, "changed": True, "report": claimed_report}

    worker["attemptCount"] = attempt_count + 1
    worker["startedAt"] = _format_utc(now)
    worker["lastError"] = None
    claimed_report["status"] = "running"
    return {"claimed": True, "changed": True, "report": claimed_report}


def complete_report(
    report: Mapping[str, object],
    expected_attempt_count: int,
    content: Mapping[str, object],
    now: datetime,
) -> dict[str, object]:
    completed_report = copy.deepcopy(dict(report))
    worker = dict(completed_report.get("worker") or {})
    completed_report["worker"] = worker
    if (
        completed_report.get("status") != "running"
        or int(worker.get("attemptCount") or 0) != expected_attempt_count
    ):
        return {"changed": False, "report": completed_report}

    completed_report["status"] = "ready"
    completed_report["content"] = copy.deepcopy(dict(content))
    completed_report["readyAt"] = _format_utc(now)
    worker["lastError"] = None
    return {"changed": True, "report": completed_report}


def schedule_report_retry(
    report: Mapping[str, object],
    expected_attempt_count: int,
    error_code: str,
    now: datetime,
    config: WeeklyReportConfig | None = None,
) -> dict[str, object]:
    resolved_config = config or get_weekly_report_config()
    retry_report = copy.deepcopy(dict(report))
    worker = dict(retry_report.get("worker") or {})
    retry_report["worker"] = worker
    if (
        retry_report.get("status") != "running"
        or int(worker.get("attemptCount") or 0) != expected_attempt_count
    ):
        return {"claimed": False, "changed": False, "report": retry_report}

    attempt_count = int(worker.get("attemptCount") or 0)
    worker["lastError"] = error_code
    worker["startedAt"] = None
    if attempt_count >= resolved_config.maximum_attempt_count:
        retry_report["status"] = "failed"
        return {"claimed": False, "changed": True, "report": retry_report}

    delay_index = min(attempt_count - 1, len(resolved_config.retry_delays_seconds) - 1)
    delay_seconds = resolved_config.retry_delays_seconds[max(delay_index, 0)]
    worker["availableAt"] = _format_utc(now + timedelta(seconds=delay_seconds))
    retry_report["status"] = "pending"
    return {"claimed": False, "changed": True, "report": retry_report}


def is_next_plan_recovery_candidate(
    report: Mapping[str, object],
    now: datetime,
) -> bool:
    if report.get("status") != "ready":
        return False
    next_plan = report.get("nextPlan") or {}
    if not isinstance(next_plan, Mapping) or next_plan.get("status") != "pending":
        return False
    worker = report.get("worker") or {}
    if not isinstance(worker, Mapping):
        return False
    available_at = _parse_datetime(worker.get("availableAt"))
    return available_at is None or available_at <= _as_utc(now)


def mark_next_plan_succeeded(
    report: Mapping[str, object],
    next_study_plan_id: int,
) -> dict[str, object]:
    """다음 계획 번호를 기록하고 succeeded 로 확정한다."""
    return _update_next_plan(report, "succeeded", next_study_plan_id, None)


def mark_next_plan_blocked(
    report: Mapping[str, object],
    blocked_reason: str,
) -> dict[str, object]:
    """진행 중 세션 때문에 다음 계획 생성을 보류한다."""
    return _update_next_plan(report, "blocked", None, blocked_reason)


def mark_next_plan_failed(
    report: Mapping[str, object],
    error_code: str,
) -> dict[str, object]:
    """후보 부족 같은 영구 오류로 다음 계획 생성을 닫는다."""
    return _update_next_plan(report, "failed", None, error_code)


def defer_next_plan(
    report: Mapping[str, object],
    now: datetime,
    config: WeeklyReportConfig | None = None,
) -> dict[str, object]:
    """일시적 인프라 오류다. pending 을 유지하고 실행 가능 시각만 미룬다.

    availableAt 을 미루지 않으면 워커 복구 스캔이 같은 건을 곧바로 다시 잡아
    오류가 반복되는 동안 루프가 공회전한다.
    """
    resolved_config = config or get_weekly_report_config()
    deferred_report = copy.deepcopy(dict(report))
    if not _is_next_plan_actionable(deferred_report):
        return {"changed": False, "report": deferred_report}

    worker = dict(deferred_report.get("worker") or {})
    worker["availableAt"] = _format_utc(
        now + timedelta(seconds=resolved_config.next_plan_retry_delay_seconds),
    )
    deferred_report["worker"] = worker
    return {"changed": True, "report": deferred_report}


def _update_next_plan(
    report: Mapping[str, object],
    status: str,
    next_study_plan_id: int | None,
    blocked_reason: str | None,
) -> dict[str, object]:
    updated_report = copy.deepcopy(dict(report))
    if not _is_next_plan_actionable(updated_report):
        return {"changed": False, "report": updated_report}

    next_plan = dict(updated_report.get("nextPlan") or {})
    next_plan["status"] = status
    next_plan["studyPlanId"] = next_study_plan_id
    next_plan["blockedReason"] = blocked_reason
    updated_report["nextPlan"] = next_plan
    return {"changed": True, "report": updated_report}


def _is_next_plan_actionable(report: Mapping[str, object]) -> bool:
    """ready 리포트의 대기·보류 다음 계획만 상태를 바꿀 수 있다.

    succeeded·failed 는 종결 상태다. 여기서 거르지 않으면 늦게 도착한
    워커·동기화 경합이 이미 만든 계획 번호를 덮어쓴다.
    """
    if report.get("status") != "ready":
        return False
    next_plan = report.get("nextPlan")
    if not isinstance(next_plan, Mapping):
        return False
    return str(next_plan.get("status") or "") in NEXT_PLAN_ACTIONABLE_STATUSES


def _is_stuck(
    worker: Mapping[str, object],
    now: datetime,
    config: WeeklyReportConfig,
) -> bool:
    started_at = _parse_datetime(worker.get("startedAt"))
    if started_at is None:
        return True
    return started_at + timedelta(seconds=config.stuck_after_seconds) <= _as_utc(now)


def _parse_datetime(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return _as_utc(value)
    elif not value:
        return None
    try:
        return _as_utc(datetime.fromisoformat(str(value).replace("Z", "+00:00")))
    except ValueError:
        return None


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _format_utc(value: datetime) -> str:
    return _as_utc(value).isoformat().replace("+00:00", "Z")
