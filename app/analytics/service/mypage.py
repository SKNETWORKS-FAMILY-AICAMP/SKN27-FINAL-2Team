from datetime import timedelta

from django.db.models import Count, Q
from django.utils import timezone

from analytics.service.analytics import (
    calculate_percent_rate,
    get_diagnosis_improvement_summary,
    get_completed_records,
    get_completed_sessions,
    get_completed_weekly_review_sessions,
    get_recent_wrong_rate_period,
    get_weekly_practice_summary,
)
from analytics.service.studyplan import get_user_study_info
from analytics.service.weakness import build_weakness_rows, get_status_class, get_weakness_config


def build_learning_summary(user, today=None):
    """
    마이페이지 상단 학습 요약 카드에 필요한 데이터를 만든다.

    이번 주 practice 기준 정답률과 풀이 수를 가져오고,
    완료 세션 날짜를 이용해 현재 연속 학습일을 계산한다.
    """
    base_date = today or timezone.localdate()
    completed_sessions = get_completed_sessions(user.user_id)
    weekly_summary = get_weekly_practice_summary(user.user_id, base_date)
    study_streak_days = calculate_current_study_streak(
        completed_sessions,
        base_date,
    )

    return {
        "answer_rate": weekly_summary["answerRate"],
        "solved_count": weekly_summary["solvedCount"],
        "study_streak_days": study_streak_days,
        "avg_question_time": _format_seconds(weekly_summary["averageQuestionTimeSec"]),
        "avg_session_time": _format_seconds(weekly_summary["averageSessionTimeSec"]),
    }


def calculate_current_study_streak(completed_sessions, today):
    solved_dates = {
        study_date
        for study_date in completed_sessions.values_list("recorded_date", flat=True)
        if study_date
    }
    if today not in solved_dates:
        return 0

    study_streak_days = 0
    expected_date = today
    while expected_date in solved_dates:
        study_streak_days += 1
        expected_date -= timedelta(days=1)

    return study_streak_days


def build_diagnosis_comparison_summary(user):
    """
    일반 진단평가와 주간평가 간 비교 요약을 만든다.

    첫 주는 직전 진단평가와 주간평가를, 이후에는 연속 주간평가를 비교한다.
    """
    comparison = get_diagnosis_improvement_summary(user.user_id)
    answer_display = _build_rate_change_display(comparison["answerRateChange"])
    time_display = _build_time_change_display(
        comparison["averageQuestionTimeChangeSec"],
    )
    has_comparison = comparison["hasComparison"]
    empty_display = _build_diagnosis_comparison_empty_display(comparison)

    return {
        "has_records": has_comparison,
        "has_diagnosis": comparison["hasDiagnosis"],
        "has_weekly_review_plan": comparison["hasWeeklyReviewPlan"],
        "has_post_diagnosis_practice": comparison["hasPostDiagnosisPractice"],
        "empty": empty_display,
        "answer": {
            "diagnosis_rate": comparison["diagnosis"]["answerRate"],
            "current_rate": comparison["current"]["answerRate"],
            "diagnosis_label": comparison["diagnosis"]["sessionLabel"],
            "current_label": comparison["current"]["sessionLabel"],
            "change_label": answer_display["label"],
            "tone": answer_display["tone"],
        },
        "time": {
            "diagnosis_label": comparison["diagnosis"]["sessionLabel"],
            "current_label": comparison["current"]["sessionLabel"],
            "diagnosis_time": _format_seconds(
                comparison["diagnosis"]["averageQuestionTimeSec"],
            ),
            "current_time": _format_seconds(
                comparison["current"]["averageQuestionTimeSec"],
            ),
            "change_label": time_display["label"],
            "tone": time_display["tone"],
        },
    }


def _build_diagnosis_comparison_empty_display(comparison):
    """
    진단평가 비교 카드의 대기 상태 문구를 만든다.
    """
    if not comparison["hasDiagnosis"]:
        return {
            "title": "진단평가 필요",
            "description": "첫 진단평가를 완료하면 다음 진단평가와 비교할 수 있습니다.",
        }

    if comparison["hasPostDiagnosisPractice"]:
        return {
            "title": "주간평가 대기",
            "description": "학습 기록은 쌓였고, 주간평가를 완료하면 직전 평가와 비교됩니다.",
        }

    if comparison["hasWeeklyReviewPlan"]:
        return {
            "title": "비교 기준 준비 중",
            "description": "7일차 주간평가를 완료하면 직전 평가 대비 개선도가 표시됩니다.",
        }

    return {
        "title": "주간평가 대기",
        "description": "학습계획을 진행하고 주간평가를 완료하면 정답률 변화를 볼 수 있습니다.",
    }


def build_wrong_type_summary(user, today=None):
    """
    유형별 오답률 요약 카드에 필요한 데이터를 만든다.

    최신 주간평가 기록을 우선해 q_type별 오답률을 계산하고,
    주간평가가 없을 때만 최근 7일 완료 기록을 사용한다.
    """
    unclassified_label = "미분류"
    period = get_recent_wrong_rate_period(today)
    completed_records = get_completed_records(user.user_id)
    latest_weekly_review = get_completed_weekly_review_sessions(user.user_id).last()
    record_scope = completed_records
    period_label = period["label"]
    source = "recent_learning"
    if latest_weekly_review is not None:
        record_scope = completed_records.filter(session=latest_weekly_review)
        period_label = f"{latest_weekly_review.recorded_date.strftime('%m.%d')} 주간평가"
        source = "weekly_review"
    elif latest_weekly_review is None:
        record_scope = completed_records.filter(
            session__recorded_date__gte=period["startDate"],
            session__recorded_date__lte=period["endDate"],
        )

    rows = (
        record_scope
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

    status_label = "기록 없음"
    if total_count > 0:
        status_label = "오답 비율"

    return {
        "overall_rate": calculate_percent_rate(wrong_count, total_count),
        "items": sorted_items,
        "has_records": total_count > 0,
        "status_label": status_label,
        "period_label": period_label,
        "source": source,
    }


def build_weakness_summary(user, today=None):
    """
    시대와 주제 조합 기준의 취약점 목록을 만든다.

    공용 취약 판정 기준에서 WEAK로 판단된 era/topic 조합만 노출한다.
    """
    config = get_weakness_config()
    records = get_completed_records(user.user_id)
    items = []
    for row in build_weakness_rows(records, ["era", "topic"], today):
        if row["status"] == config["status_weak"]:
            items.append(
                {
                    "label": row["label"],
                    "total": row["raw"]["total"],
                    "wrong": row["raw"]["wrong"],
                    "rate": round(row["raw"]["wrongRate"] * 100),
                    "weakness_score": row["weaknessScore"],
                    "status": row["status"],
                    "status_class": get_status_class(row["status"]),
                    "trend": row["trend"],
                    "trend_label": build_weakness_trend_label(row["trend"], config),
                }
            )

    display_limit = 10
    sorted_items = sorted(
        items,
        key=lambda item: (-item["weakness_score"], -item["wrong"], item["label"]),
    )
    visible_items = sorted_items[:display_limit]
    max_weakness_score = max(
        (item["weakness_score"] for item in visible_items),
        default=0,
    )
    for item in visible_items:
        item["bar_ratio"] = 0
        if max_weakness_score > 0:
            item["bar_ratio"] = round(item["weakness_score"] / max_weakness_score * 100)
    return {
        "items": visible_items,
        "has_records": bool(items),
        "period_label": "최근 학습 기준",
        "empty_title": "아직 취약으로 판단할 데이터가 부족해요",
        "empty_description": "문제를 더 풀면 분석이 정확해져요.",
    }


def build_weakness_trend_label(trend, config):
    trend_value = trend.get("value")
    if trend_value == config["trend_worsening"]:
        return "악화"
    elif trend_value == config["trend_improving"]:
        return "개선 중"

    return ""


def build_d_day_label(user, today):
    """
    사용자 학습 프로필의 시험일을 기준으로 D-day 라벨을 만든다.

    시험일이 없으면 미설정, 오늘이면 D-day,
    미래/과거 날짜는 각각 D - n, D + n 형태로 반환한다.
    """
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


def _build_rate_change_display(change):
    """
    정답률 변화값을 화면 표시용 라벨과 톤으로 변환한다.
    """
    label = "기록 부족"
    tone = "neutral"
    if change is not None:
        label = f"{change:+d}%"
        if change > 0:
            tone = "good"
        elif change < 0:
            tone = "warn"

    return {
        "label": label,
        "tone": tone,
    }


def _build_time_change_display(change):
    """
    문제당 평균 풀이시간 변화값을 화면 표시용 라벨과 톤으로 변환한다.
    """
    label = "기록 부족"
    tone = "neutral"
    if change is not None:
        label = "변화 없음"
        if change < 0:
            label = f"{abs(change)}초 단축"
            tone = "good"
        elif change > 0:
            label = f"{change}초 증가"
            tone = "warn"

    return {
        "label": label,
        "tone": tone,
    }


def _format_seconds(seconds):
    """
    초 단위 시간을 MM:SS 문자열로 변환한다.

    값이 없거나 음수로 들어올 수 있는 경우를 방어해
    화면에는 항상 00:00 이상의 형식으로 표시되게 한다.
    """
    if seconds is None:
        return "00:00"

    total_seconds = max(0, int(round(seconds)))
    minutes, remaining_seconds = divmod(total_seconds, 60)
    return f"{minutes:02d}:{remaining_seconds:02d}"
