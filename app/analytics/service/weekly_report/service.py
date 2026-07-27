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
    concept_rows: Sequence[Mapping[str, object]] | None = None,
    exam_trends: Sequence[Mapping[str, object]] | None = None,
) -> dict[str, object]:
    resolved_config = config or get_weekly_report_config()
    completion_rate = float(plan_progress.get("completionRate") or 0.0)
    if completion_rate < 0 or completion_rate > 1:
        raise ValueError("계획 완료율은 0과 1 사이여야 합니다.")
    strengths = _select_strengths(weakness_rows, resolved_config)
    exam_trend_by_group = {
        str(trend.get("groupKeyId")): trend
        for trend in (exam_trends or [])
        if trend.get("groupKeyId")
    }
    improvements = _select_priority_improvements(
        weakness_rows,
        repeated_error_by_group,
        resolved_config,
        exam_trend_by_group,
    )
    time_summary = _build_time_summary(time_records, resolved_config)
    concept_weaknesses = _select_concept_weaknesses(concept_rows or [], resolved_config)
    next_targets = _select_next_plan_targets(priority_targets, resolved_config)
    assessment_result = dict(assessment)
    assessment_result["evidenceId"] = "assessment-current"
    comparison_result = _build_comparison(assessment, baseline)
    comparison_result["evidenceId"] = "comparison-baseline"
    progress_result = dict(plan_progress)
    if "completionPercent" not in progress_result:
        progress_result["completionPercent"] = _to_percent(completion_rate)
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
            "conceptWeaknesses": concept_weaknesses,
            "examTrends": _build_exam_trend_evidence(exam_trends or [], resolved_config),
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
    concept_weaknesses = result.get("conceptWeaknesses") or []
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
    elif concept_weaknesses:
        comment_text = resolved_config.fallback_concept_comment.format(
            label=concept_weaknesses[0]["label"],
        )
        comment_evidence_ids = [str(concept_weaknesses[0]["evidenceId"])]

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
    for item in concept_weaknesses:
        if len(tips) >= resolved_config.maximum_tip_count:
            break
        tips.append(
            {
                "text": resolved_config.fallback_concept_tip.format(label=item["label"]),
                "evidenceIds": [str(item["evidenceId"])],
            }
        )
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
        "conceptWeaknesses": _escape_evidence_items(result.get("conceptWeaknesses")),
        "examTrends": _escape_evidence_items(result.get("examTrends")),
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
    """개선 흐름이 보이는 영역.

    아직 취약 판정인 영역은 제외한다. 그러지 않으면 같은 영역이 강점과
    우선 보완 영역에 동시에 실려, 화면에서 "강점"과 "먼저 보완할 영역"에
    같은 이름이 나란히 나온다.
    """
    selected = [
        row
        for row in weakness_rows
        if row.get("trend") == "improving"
        and row.get("status") != "WEAK"
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
            "trend": row.get("trend"),
            "trendDelta": row.get("trendDelta"),
            "recentWeaknessScore": row.get("recentScore"),
            "previousWeaknessScore": row.get("previousScore"),
        }
        for index, row in enumerate(selected[: config.maximum_strength_count])
    ]


def _select_priority_improvements(
    weakness_rows: Sequence[Mapping[str, object]],
    repeated_error_by_group: Mapping[str, float],
    config: WeeklyReportConfig,
    exam_trend_by_group: Mapping[str, Mapping[str, object]] | None = None,
) -> list[dict[str, object]]:
    """취약 영역을 고른다.

    취약 정도가 먼저다. 다만 취약 점수는 근소한 차이로 갈리는 일이 많아
    그 순서를 그대로 믿기 어렵다. 그래서 점수를 구간으로 묶고, 같은 구간이면
    최근 출제 경향 TOP5 에 든 쪽을 먼저 둔다. 비슷하게 약하다면 시험에 자주
    나오는 쪽을 먼저 보완하는 편이 낫기 때문이다.
    """
    resolved_trends = exam_trend_by_group or {}
    selected = [row for row in weakness_rows if row.get("status") == "WEAK"]
    selected.sort(
        key=lambda row: (
            -_get_weakness_tier(row, config),
            _get_exam_trend_rank(resolved_trends, row),
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
            "wrongCount": (row.get("raw") or {}).get("wrong"),
            "wrongRate": (row.get("raw") or {}).get("wrongRate"),
            "wrongPercent": _to_percent((row.get("raw") or {}).get("wrongRate")),
            "effectiveTotal": (row.get("effective") or {}).get("total"),
            "weaknessScore": row.get("weaknessScore"),
            "trend": row.get("trend"),
            "trendDelta": row.get("trendDelta"),
            "recentWeaknessScore": row.get("recentScore"),
            "previousWeaknessScore": row.get("previousScore"),
            "repeatedError": repeated_error_by_group.get(
                str(row.get("groupKeyId")),
                0.0,
            ),
            **_build_exam_trend_fields(resolved_trends, row),
        }
        for index, row in enumerate(selected[: config.maximum_improvement_count])
    ]


def _to_percent(rate: object) -> int | None:
    """실제 비율을 백분율 정수로 바꾼다.

    근거에 0.75 만 있으면 AI 가 "75%" 라고 쓸 때 가드에 걸리므로,
    사람이 읽는 표기를 근거에 함께 담아 둔다.

    weaknessScore 처럼 윌슨 하한인 값에는 쓰지 않는다. 하한은 관측된
    오답률이 아니라 표본이 적을수록 보수적으로 낮아지는 지표라서,
    백분율로 바꿔 두면 오답률로 읽히기 쉽다.
    """
    if not isinstance(rate, (int, float)) or isinstance(rate, bool):
        return None
    return round(float(rate) * 100)


def _get_weakness_tier(row: Mapping[str, object], config: WeeklyReportConfig) -> int:
    """취약 점수를 구간으로 묶는다.

    0.71 과 0.69 를 다른 순위로 취급하면 표본이 조금만 흔들려도 순서가 바뀐다.
    구간이 같으면 "비슷하게 약하다"로 보고 다음 기준에 순서를 넘긴다.

    구간 경계에 걸친 두 값(예: 허용치 0.05 에서 0.699 와 0.701)은 여전히
    다른 구간으로 갈린다. 완벽한 근사 비교는 정렬 가능한 전순서가 아니어서
    단순한 구간 나눗셈을 택했다.
    """
    tolerance = config.weakness_tie_tolerance
    if tolerance <= 0:
        return 0
    # 0.05 는 이진수로 정확히 표현되지 않아 0.60 / 0.05 가 11.999... 가 된다.
    # 보정 없이 자르면 구간 경계값이 한 칸 아래로 떨어진다.
    return int(round(float(row.get("weaknessScore") or 0.0) / tolerance, 9))


def _get_exam_trend_rank(
    exam_trend_by_group: Mapping[str, Mapping[str, object]],
    row: Mapping[str, object],
) -> int:
    """출제 경향에 없으면 맨 뒤로 보낸다."""
    trend = exam_trend_by_group.get(str(row.get("groupKeyId")))
    if trend is None:
        return 99
    return int(trend.get("rank") or 99)


def _build_exam_trend_fields(
    exam_trend_by_group: Mapping[str, Mapping[str, object]],
    row: Mapping[str, object],
) -> dict[str, object]:
    trend = exam_trend_by_group.get(str(row.get("groupKeyId")))
    if trend is None:
        return {}
    return {
        "examTrendRank": trend.get("rank"),
        "examQuestionSharePercent": trend.get("ratioPercent"),
    }


def _build_exam_trend_evidence(
    exam_trends: Sequence[Mapping[str, object]],
    config: WeeklyReportConfig,
) -> list[dict[str, object]]:
    return [
        {
            "evidenceId": f"trend-{index + 1}",
            "groupKeyId": trend.get("groupKeyId"),
            "label": trend.get("label"),
            "rank": trend.get("rank"),
            "ratioPercent": trend.get("ratioPercent"),
            "questionCount": trend.get("questionCount"),
            "recentRounds": trend.get("recentRounds"),
        }
        for index, trend in enumerate(exam_trends[: config.maximum_exam_trend_count])
    ]


def _select_concept_weaknesses(
    concept_rows: Sequence[Mapping[str, object]],
    config: WeeklyReportConfig,
) -> list[dict[str, object]]:
    """핵심 개념 단위 취약점.

    시대·주제보다 좁은 단위라 표본이 쉽게 부족해진다. 취약 판정이 난 것만 쓰고,
    같은 점수면 표본이 많은 쪽을 먼저 둔다.
    """
    selected = [row for row in concept_rows if row.get("status") == "WEAK"]
    selected.sort(
        key=lambda row: (
            -float(row.get("weaknessScore") or 0.0),
            -float((row.get("effective") or {}).get("total") or 0.0),
            str(row.get("groupKeyId") or ""),
        )
    )
    return [
        {
            "evidenceId": f"concept-{index + 1}",
            "groupKeyId": row.get("groupKeyId"),
            "label": row.get("label"),
            "sampleCount": (row.get("raw") or {}).get("total"),
            "wrongCount": (row.get("raw") or {}).get("wrong"),
            "wrongRate": (row.get("raw") or {}).get("wrongRate"),
            "wrongPercent": _to_percent((row.get("raw") or {}).get("wrongRate")),
            "effectiveTotal": (row.get("effective") or {}).get("total"),
            "weaknessScore": row.get("weaknessScore"),
            "trend": row.get("trend"),
            "trendDelta": row.get("trendDelta"),
        }
        for index, row in enumerate(selected[: config.maximum_concept_weakness_count])
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
