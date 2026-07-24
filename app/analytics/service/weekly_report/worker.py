from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone
from typing import Mapping

from analytics.service.weekly_report.config import (
    WeeklyReportConfig,
    get_weekly_report_config,
)


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
