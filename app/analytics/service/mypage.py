from datetime import timedelta

from django.db.models import Count, Q

from analytics.service.analytics import (
    calculate_percent_rate,
    get_diagnosis_improvement_summary,
    get_completed_records,
    get_completed_sessions,
    get_weekly_practice_summary,
)
from analytics.service.studyplan import get_user_study_info


def build_learning_summary(user):
    completed_sessions = get_completed_sessions(user.user_id)
    weekly_summary = get_weekly_practice_summary(user.user_id)

    solved_dates = completed_sessions.values_list("recorded_date", flat=True)
    ordered_dates = sorted(
        {study_date for study_date in solved_dates if study_date},
        reverse=True,
    )
    study_streak_days = 0
    if ordered_dates:
        expected_date = ordered_dates[0]
        for study_date in ordered_dates:
            if study_date == expected_date:
                study_streak_days += 1
                expected_date -= timedelta(days=1)
            elif study_date != expected_date:
                break

    return {
        "answer_rate": weekly_summary["answerRate"],
        "solved_count": weekly_summary["solvedCount"],
        "study_streak_days": study_streak_days,
        "avg_question_time": _format_seconds(weekly_summary["averageQuestionTimeSec"]),
        "avg_session_time": _format_seconds(weekly_summary["averageSessionTimeSec"]),
    }


def build_diagnosis_comparison_summary(user):
    comparison = get_diagnosis_improvement_summary(user.user_id)
    answer_change = comparison["answerRateChange"]
    time_change = comparison["averageQuestionTimeChangeSec"]
    answer_tone = "neutral"
    answer_change_label = "기록 부족"
    if answer_change is not None:
        answer_change_label = f"{answer_change:+d}%p"
        if answer_change > 0:
            answer_tone = "good"
        elif answer_change < 0:
            answer_tone = "warn"

    time_tone = "neutral"
    time_change_label = "기록 부족"
    if time_change is not None:
        time_change_label = "변화 없음"
        if time_change < 0:
            time_change_label = f"{abs(time_change)}초 단축"
            time_tone = "good"
        elif time_change > 0:
            time_change_label = f"{time_change}초 증가"
            time_tone = "warn"

    return {
        "has_records": comparison["hasComparison"],
        "answer": {
            "diagnosis_rate": comparison["diagnosis"]["answerRate"],
            "current_rate": comparison["current"]["answerRate"],
            "change_label": answer_change_label,
            "tone": answer_tone,
        },
        "time": {
            "diagnosis_time": _format_seconds(
                comparison["diagnosis"]["averageQuestionTimeSec"],
            ),
            "current_time": _format_seconds(
                comparison["current"]["averageQuestionTimeSec"],
            ),
            "change_label": time_change_label,
            "tone": time_tone,
        },
    }


def build_wrong_type_summary(user):
    unclassified_label = "미분류"
    rows = (
        get_completed_records(user.user_id)
        .values("q_type")
        .annotate(
            total=Count("record_id"),
            wrong=Count("record_id", filter=Q(is_correct=False)),
        )
    )

    items = []
    total_count = 0
    wrong_count = 0
    for row in rows:
        total = row["total"] or 0
        wrong = row["wrong"] or 0

        total_count += total
        wrong_count += wrong
        items.append(
            {
                "label": row["q_type"] or unclassified_label,
                "total": total,
                "wrong": wrong,
                "rate": calculate_percent_rate(wrong, total),
            }
        )

    sorted_items = sorted(
        items,
        key=lambda item: (-item["rate"], -item["total"], item["label"]),
    )
    for index, item in enumerate(sorted_items, start=1):
        item["tone_class"] = "good"
        if index == 1:
            item["tone_class"] = "danger"
        elif index == 2:
            item["tone_class"] = "warn"

    status_label = "기록 없음"
    if total_count > 0:
        status_label = "오답 비율"

    return {
        "overall_rate": calculate_percent_rate(wrong_count, total_count),
        "items": sorted_items,
        "has_records": total_count > 0,
        "status_label": status_label,
    }


def build_weakness_summary(user):
    unclassified_label = "미분류"
    rows = (
        get_completed_records(user.user_id)
        .values("era", "topic")
        .annotate(
            total=Count("record_id"),
            wrong=Count("record_id", filter=Q(is_correct=False)),
        )
        .filter(wrong__gt=0)
    )

    items = []
    for row in rows:
        total = row["total"] or 0
        wrong = row["wrong"] or 0

        label_parts = [row["era"], row["topic"]]
        valid_labels = [label for label in label_parts if label]
        label = unclassified_label
        if valid_labels:
            label = " / ".join(valid_labels)

        items.append(
            {
                "label": label,
                "total": total,
                "wrong": wrong,
                "rate": calculate_percent_rate(wrong, total),
            }
        )

    return {
        "items": sorted(
            items,
            key=lambda item: (-item["rate"], -item["wrong"], item["label"]),
        ),
        "has_records": bool(items),
    }


def build_d_day_label(user, today):
    profile = get_user_study_info(user.user_id)
    if not profile or not profile.exam_date:
        return "미설정"

    d_day = (profile.exam_date - today).days
    if d_day > 0:
        return f"D - {d_day}"
    elif d_day == 0:
        return "D-day"
    elif d_day < 0:
        return f"D + {abs(d_day)}"

    return "미설정"


def _format_seconds(seconds):
    if seconds is None:
        return "00:00"

    total_seconds = max(0, int(round(seconds)))
    minutes, remaining_seconds = divmod(total_seconds, 60)
    return f"{minutes:02d}:{remaining_seconds:02d}"
