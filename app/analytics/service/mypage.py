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
    """
    마이페이지 상단 학습 요약 카드에 필요한 데이터를 만든다.

    이번 주 practice 기준 정답률과 풀이 수를 가져오고,
    완료 세션 날짜를 이용해 현재 연속 학습일을 계산한다.
    """
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
    """
    첫 진단평가 기준의 비교 요약을 만든다.

    진단 이후 이번 주 연습과의 비교를 화면 표시용 데이터로 변환한다.
    """
    comparison = get_diagnosis_improvement_summary(user.user_id)
    answer_display = _build_rate_change_display(comparison["answerRateChange"])
    time_display = _build_time_change_display(
        comparison["averageQuestionTimeChangeSec"],
    )
    has_post_records = comparison["hasComparison"]

    return {
        "has_records": has_post_records,
        "answer": {
            "diagnosis_rate": comparison["diagnosis"]["answerRate"],
            "current_rate": comparison["current"]["answerRate"],
            "change_label": answer_display["label"],
            "tone": answer_display["tone"],
        },
        "time": {
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


def build_wrong_type_summary(user):
    """
    유형별 오답률 요약 카드에 필요한 데이터를 만든다.

    완료된 풀이 기록을 q_type 기준으로 묶어 오답률을 계산하고,
    오답률이 높은 상위 항목에 강조용 CSS 클래스를 부여한다.
    """
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
    """
    시대와 주제 조합 기준의 취약점 목록을 만든다.

    오답이 1건 이상 발생한 era/topic 조합만 추려서
    오답률과 오답 수 기준으로 정렬한다.
    """
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

    display_limit = 10
    sorted_items = sorted(
        items,
        key=lambda item: (-item["rate"], -item["wrong"], item["label"]),
    )
    return {
        "items": sorted_items[:display_limit],
        "has_records": bool(items),
    }


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
        label = f"{change:+d}%p"
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
