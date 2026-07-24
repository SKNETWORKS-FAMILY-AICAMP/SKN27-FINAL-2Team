from __future__ import annotations

import html
import statistics
from copy import deepcopy
from datetime import datetime, timezone
from typing import Mapping, Sequence

from analytics.service.study_plan.planner import PriorityTarget
from analytics.service.weekly_report.config import (
    WeeklyReportConfig,
    get_weekly_report_config,
)


def build_report_result(
    assessment: Mapping[str, object],
    baseline: Mapping[str, object] | None,
    plan_progress: Mapping[str, object],
    weakness_rows: Sequence[Mapping[str, object]],
    repeated_error_by_group: Mapping[str, float],
    time_records: Sequence[Mapping[str, object]],
    priority_targets: Sequence[PriorityTarget],
    snapshot_at: datetime,
    recovered_snapshot: bool,
    generation_reason: str | None,
    has_previous_weekly_review: bool,
    config: WeeklyReportConfig | None = None,
    confusion_patterns: Sequence[Mapping[str, object]] | None = None,
) -> dict[str, object]:
    resolved_config = config or get_weekly_report_config()
    completion_rate = float(plan_progress.get("completionRate") or 0.0)
    if completion_rate < 0 or completion_rate > 1:
        raise ValueError("계획 완료율은 0과 1 사이여야 합니다.")
    strengths = _select_strengths(weakness_rows, resolved_config)
    improvements = _select_priority_improvements(
        weakness_rows,
        repeated_error_by_group,
        resolved_config,
    )
    time_summary = _build_time_summary(time_records, resolved_config)
    next_targets = _select_next_plan_targets(priority_targets, resolved_config)
    assessment_result = dict(assessment)
    assessment_result["evidenceId"] = "assessment-current"
    comparison_result = _build_comparison(assessment, baseline)
    comparison_result["evidenceId"] = "comparison-baseline"
    progress_result = dict(plan_progress)
    progress_result["evidenceId"] = "plan-progress"
    report_type = "weekly"
    if generation_reason == "diagnostic" or not has_previous_weekly_review:
        report_type = "first_week"
    return {
        "reportType": report_type,
        "result": {
            "snapshotAt": _format_utc(snapshot_at),
            "recoveredSnapshot": bool(recovered_snapshot),
            "assessment": assessment_result,
            "comparison": comparison_result,
            "planProgress": progress_result,
            "strengths": strengths,
            "priorityImprovements": improvements,
            "timeSummary": time_summary,
            "confusionPatterns": [
                deepcopy(dict(pattern))
                for pattern in (confusion_patterns or [])[
                    : resolved_config.maximum_confusion_pattern_count
                ]
            ],
            "nextPlanTargets": next_targets,
        },
    }


def build_pending_report(
    source_session_id: int,
    report_type: str,
    result: Mapping[str, object],
    created_at: datetime,
    recovered_snapshot: bool = False,
    config: WeeklyReportConfig | None = None,
) -> dict[str, object]:
    resolved_config = config or get_weekly_report_config()
    frozen_result = dict(result)
    frozen_result["recoveredSnapshot"] = bool(recovered_snapshot)
    created_at_text = _format_utc(created_at)
    return {
        "schemaVersion": resolved_config.schema_version,
        "status": "pending",
        "reportType": report_type,
        "sourceSessionId": source_session_id,
        "result": frozen_result,
        "content": {
            "comment": None,
            "tips": [],
            "fallbackUsed": False,
            "validation": None,
        },
        "worker": {
            "attemptCount": 0,
            "availableAt": created_at_text,
            "startedAt": None,
            "lastError": None,
        },
        "nextPlan": {
            "status": "pending",
            "studyPlanId": None,
            "blockedReason": None,
        },
        "version": resolved_config.version,
        "model": resolved_config.model,
        "createdAt": created_at_text,
        "readyAt": None,
    }


def build_fallback_content(
    result: Mapping[str, object],
    config: WeeklyReportConfig | None = None,
) -> dict[str, object]:
    resolved_config = config or get_weekly_report_config()
    strengths = result.get("strengths") or []
    improvements = result.get("priorityImprovements") or []
    time_summary = result.get("timeSummary") or []
    confusion_patterns = result.get("confusionPatterns") or []
    comment_text = resolved_config.fallback_neutral_comment
    comment_evidence_ids: list[str] = []
    confusion_pattern = confusion_patterns[0] if confusion_patterns else {}
    correct_fact = confusion_pattern.get("correctFact") or {}
    selected_fact = confusion_pattern.get("selectedFact") or {}
    has_confusion_labels = (
        isinstance(correct_fact, Mapping)
        and isinstance(selected_fact, Mapping)
        and bool(correct_fact.get("subjectLabel"))
        and bool(correct_fact.get("objectLabel"))
        and bool(selected_fact.get("subjectLabel"))
        and bool(selected_fact.get("objectLabel"))
    )
    if has_confusion_labels:
        comment_text = resolved_config.fallback_confusion_comment.format(
            correct_subject=correct_fact["subjectLabel"],
            correct_object=correct_fact["objectLabel"],
            selected_subject=selected_fact["subjectLabel"],
            selected_object=selected_fact["objectLabel"],
        )
        comment_evidence_ids = [str(confusion_pattern["evidenceId"])]
    elif strengths:
        comment_text = resolved_config.fallback_improving_comment.format(
            label=strengths[0]["label"],
        )
        comment_evidence_ids = [str(strengths[0]["evidenceId"])]
    elif improvements:
        comment_text = resolved_config.fallback_priority_comment.format(
            label=improvements[0]["label"],
        )
        comment_evidence_ids = [str(improvements[0]["evidenceId"])]

    tips: list[dict[str, object]] = []
    if has_confusion_labels:
        comparison_dimensions = [
            str(item)
            for item in confusion_pattern.get("comparisonDimensions") or []
            if str(item).strip()
        ]
        confusion_tip_text = resolved_config.fallback_confusion_general_tip
        if comparison_dimensions:
            confusion_tip_text = resolved_config.fallback_confusion_tip.format(
                dimensions="·".join(comparison_dimensions),
            )
        tips.append(
            {
                "text": confusion_tip_text,
                "evidenceIds": [str(confusion_pattern["evidenceId"])],
            }
        )
    for item in improvements:
        if len(tips) >= resolved_config.maximum_tip_count:
            break
        tips.append(
            {
                "text": resolved_config.fallback_priority_tip.format(label=item["label"]),
                "evidenceIds": [str(item["evidenceId"])],
            }
        )
        if len(tips) >= resolved_config.maximum_tip_count:
            break
    if len(tips) < resolved_config.maximum_tip_count:
        for item in time_summary:
            tips.append(
                {
                    "text": resolved_config.fallback_time_tip.format(label=item["label"]),
                    "evidenceIds": [str(item["evidenceId"])],
                }
            )
            if len(tips) >= resolved_config.maximum_tip_count:
                break
    if not tips:
        tips.append({"text": resolved_config.fallback_general_tip, "evidenceIds": []})
    return {
        "comment": {"text": comment_text, "evidenceIds": comment_evidence_ids},
        "tips": tips,
        "fallbackUsed": True,
        "validation": {"guard": "fallback", "validator": "fallback"},
    }


def render_report_dto(report: Mapping[str, object]) -> dict[str, object]:
    result = report.get("result") or {}
    content = report.get("content") or {}
    assessment = result.get("assessment") or {}
    comparison = result.get("comparison") or {}
    progress = result.get("planProgress") or {}
    score = assessment.get("score")
    total_score = assessment.get("totalScore")
    comparison_text = "비교할 이전 평가가 없습니다."
    if comparison.get("status") == "AVAILABLE":
        score_change = float(comparison.get("scoreChange") or 0)
        direction = "변화 없음"
        if score_change > 0:
            direction = f"{score_change:g}점 상승"
        elif score_change < 0:
            direction = f"{abs(score_change):g}점 하락"
        comparison_text = direction
    completion_rate = float(progress.get("completionRate") or 0.0)
    return {
        "status": report.get("status"),
        "reportType": report.get("reportType"),
        "scoreSummary": f"{score}/{total_score}",
        "comparisonSummary": comparison_text,
        "completionSummary": f"{completion_rate * 100:.1f}%",
        "comment": _escape_content_item(content.get("comment")),
        "tips": [_escape_content_item(item) for item in content.get("tips") or []],
        "strengths": _escape_evidence_items(result.get("strengths")),
        "priorityImprovements": _escape_evidence_items(
            result.get("priorityImprovements"),
        ),
        "timeSummary": _escape_evidence_items(result.get("timeSummary")),
        "nextPlan": dict(report.get("nextPlan") or {}),
    }


def _build_comparison(
    assessment: Mapping[str, object],
    baseline: Mapping[str, object] | None,
) -> dict[str, object]:
    if baseline is None:
        return {
            "status": "INSUFFICIENT_BASELINE",
            "baselineType": None,
            "baselineSessionId": None,
            "previousScore": None,
            "scoreChange": None,
        }
    current_score = float(assessment.get("score") or 0)
    previous_score = float(baseline.get("score") or 0)
    return {
        "status": "AVAILABLE",
        "baselineType": baseline.get("type"),
        "baselineSessionId": baseline.get("sessionId"),
        "previousScore": previous_score,
        "scoreChange": current_score - previous_score,
    }


def _select_strengths(
    weakness_rows: Sequence[Mapping[str, object]],
    config: WeeklyReportConfig,
) -> list[dict[str, object]]:
    selected = [
        row
        for row in weakness_rows
        if row.get("trend") == "improving"
        and row.get("trendDelta") is not None
        and row.get("recentScore") is not None
        and row.get("previousScore") is not None
    ]
    selected.sort(
        key=lambda row: (
            float(row["trendDelta"]),
            -float((row.get("effective") or {}).get("total") or 0.0),
            str(row.get("groupKeyId") or ""),
        )
    )
    return [
        {
            "evidenceId": f"strength-{index + 1}",
            "groupKeyId": row.get("groupKeyId"),
            "label": row.get("label"),
            "sampleCount": (row.get("raw") or {}).get("total"),
            "effectiveTotal": (row.get("effective") or {}).get("total"),
            "trendDelta": row.get("trendDelta"),
        }
        for index, row in enumerate(selected[: config.maximum_strength_count])
    ]


def _select_priority_improvements(
    weakness_rows: Sequence[Mapping[str, object]],
    repeated_error_by_group: Mapping[str, float],
    config: WeeklyReportConfig,
) -> list[dict[str, object]]:
    selected = [row for row in weakness_rows if row.get("status") == "WEAK"]
    selected.sort(
        key=lambda row: (
            -float(row.get("weaknessScore") or 0.0),
            -float(repeated_error_by_group.get(str(row.get("groupKeyId")), 0.0)),
            -float((row.get("effective") or {}).get("total") or 0.0),
            str(row.get("groupKeyId") or ""),
        )
    )
    return [
        {
            "evidenceId": f"priority-{index + 1}",
            "groupKeyId": row.get("groupKeyId"),
            "label": row.get("label"),
            "sampleCount": (row.get("raw") or {}).get("total"),
            "effectiveTotal": (row.get("effective") or {}).get("total"),
            "weaknessScore": row.get("weaknessScore"),
            "repeatedError": repeated_error_by_group.get(
                str(row.get("groupKeyId")),
                0.0,
            ),
        }
        for index, row in enumerate(selected[: config.maximum_improvement_count])
    ]


def _build_time_summary(
    records: Sequence[Mapping[str, object]],
    config: WeeklyReportConfig,
) -> list[dict[str, object]]:
    valid_times: list[float] = []
    times_by_type: dict[str, list[float]] = {}
    for record in records:
        raw_time = record.get("timeSpentMs")
        if raw_time is None:
            raw_time = record.get("time_spent_ms")
        if not isinstance(raw_time, (int, float)) or raw_time <= 0:
            continue
        q_type = str(
            record.get("qType")
            or record.get("q_type")
            or record.get("question_type")
            or ""
        ).strip()
        if not q_type:
            continue
        seconds = raw_time / 1000
        valid_times.append(seconds)
        times_by_type.setdefault(q_type, []).append(seconds)
    if len(valid_times) < config.minimum_reference_time_sample:
        return []

    reference_median = statistics.median(valid_times)
    summaries: list[dict[str, object]] = []
    for q_type, type_times in times_by_type.items():
        if len(type_times) < config.minimum_type_time_sample:
            continue
        type_median = statistics.median(type_times)
        ratio = type_median / reference_median
        if ratio < config.slow_time_ratio:
            continue
        summaries.append(
            {
                "qType": q_type,
                "label": q_type,
                "sampleCount": len(type_times),
                "userMedianSeconds": round(type_median, 2),
                "referenceMedianSeconds": round(reference_median, 2),
                "timeRatio": round(ratio, 4),
            }
        )
    summaries.sort(key=lambda item: (-float(item["timeRatio"]), str(item["qType"])))
    selected = summaries[: config.maximum_time_summary_count]
    for index, item in enumerate(selected):
        item["evidenceId"] = f"time-{index + 1}"
    return selected


def _select_next_plan_targets(
    priority_targets: Sequence[PriorityTarget],
    config: WeeklyReportConfig,
) -> list[dict[str, object]]:
    ordered = sorted(
        priority_targets,
        key=lambda target: (-target.priority_score, target.group_key_id),
    )
    return [
        {
            "evidenceId": f"target-{index + 1}",
            "groupKeyId": target.group_key_id,
            "label": target.label,
            "priorityScore": round(target.priority_score, 4),
        }
        for index, target in enumerate(ordered[: config.maximum_next_target_count])
    ]


def _format_utc(value: datetime) -> str:
    resolved_value = value
    if value.tzinfo is None:
        resolved_value = value.replace(tzinfo=timezone.utc)
    return resolved_value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _escape_content_item(item: object) -> dict[str, object] | None:
    if not isinstance(item, Mapping):
        return None
    return {
        "text": html.escape(str(item.get("text") or "")),
        "evidenceIds": list(item.get("evidenceIds") or []),
    }


def _escape_evidence_items(items: object) -> list[dict[str, object]]:
    if not isinstance(items, list):
        return []
    escaped_items: list[dict[str, object]] = []
    for item in items:
        if not isinstance(item, Mapping):
            continue
        escaped_item = dict(item)
        if "label" in escaped_item:
            escaped_item["label"] = html.escape(str(escaped_item["label"] or ""))
        escaped_items.append(escaped_item)
    return escaped_items
