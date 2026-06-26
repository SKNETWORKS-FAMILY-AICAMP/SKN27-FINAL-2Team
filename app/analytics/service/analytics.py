from datetime import date, timedelta

from django.db import DatabaseError
from django.db.models import Avg, Count, Max, Min, Q
from django.utils import timezone
from analytics.models import Analytics
from question.models import SolveRecords, SolveSessions


def get_user_analytics(user_id):
    """
    사용자의 analytics 테이블 저장 분석 결과를 조회한다.

    확장된 analytics 구조에서는 session_id가 없는 주간/월간/전체/학습계획
    분석도 저장되므로 user_id 기준으로 직접 조회한다.
    """
    return Analytics.objects.filter(user_id=user_id)


def get_analysis_scope_overview(user_id):
    """
    오답률 상세 상단에 표시할 analytics 저장 범위별 최신 요약을 만든다.
    """
    scope_configs = [
        {"scope": "session", "label": "세션별", "description": "최근 완료 세션"},
        {"scope": "weekly", "label": "Weekly", "description": "최근 주간 분석"},
        {"scope": "monthly", "label": "Monthly", "description": "최근 월간 분석"},
        {"scope": "total", "label": "Total", "description": "누적 분석"},
        {
            "scope": "study_plan_base",
            "label": "계획 기준",
            "description": "학습계획 생성 시점",
        },
        {
            "scope": "study_plan_result",
            "label": "계획 결과",
            "description": "계획 종료 비교",
        },
    ]
    return [
        build_analysis_scope_overview_item(user_id, scope_config)
        for scope_config in scope_configs
    ]


def get_analysis_scope_chart_data(user_id, today=None):
    """
    오답률 상세 상단 그래프에 표시할 scope별 x축 막대 데이터를 만든다.
    """
    base_date = today or timezone.localdate()
    return [
        build_analysis_scope_chart_group(
            "session",
            "세션별",
            "세션별 오답률",
            "완료된 세션을 시간순으로 비교합니다.",
            get_session_scope_chart_bars(user_id),
        ),
        build_analysis_scope_chart_group(
            "weekly",
            "Weekly",
            "주별 오답률",
            "이번 주부터 이전 주까지의 오답률을 비교합니다.",
            get_weekly_scope_chart_bars(user_id, base_date),
        ),
        build_analysis_scope_chart_group(
            "monthly",
            "Monthly",
            "월별 오답률",
            "이번 달부터 이전 달까지의 오답률을 비교합니다.",
            get_monthly_scope_chart_bars(user_id, base_date),
        ),
        build_analysis_scope_chart_group(
            "total",
            "Total",
            "전체 누적 오답률",
            "완료된 전체 풀이 기록을 누적으로 표시합니다.",
            get_total_scope_chart_bars(user_id),
        ),
    ]


def build_analysis_scope_chart_group(scope, label, title, description, bars):
    """
    하나의 scope 탭에 필요한 그래프 그룹 데이터를 만든다.
    """
    return {
        "scope": scope,
        "label": label,
        "title": title,
        "description": description,
        "bars": bars,
        "hasRecords": bool(bars),
    }


def get_session_scope_chart_bars(user_id):
    """
    완료 세션별 오답률 막대를 만든다.
    """
    try:
        rows = list(
            SolveRecords.objects.filter(
                session__user_id=user_id,
                session__status="completed",
            )
            .values("session_id", "session__recorded_date", "session__session_type")
            .annotate(
                total_count=Count("record_id"),
                wrong_count=Count("record_id", filter=Q(is_correct=False)),
                average_time_ms=Avg("time_spent_ms"),
            )
            .order_by("-session__recorded_date", "-session_id")
        )
    except DatabaseError:
        return []

    bars = []
    for row in rows:
        recorded_date = row["session__recorded_date"]
        label = f"세션 {row['session_id']}"
        description = format_session_type_label(row["session__session_type"])
        period_label = format_period_label(recorded_date, recorded_date)
        bars.append(
            build_record_chart_bar(
                label,
                description,
                row["total_count"],
                row["wrong_count"],
                ms_to_sec(row["average_time_ms"]),
                period_label,
            )
        )

    return bars


def get_weekly_scope_chart_bars(user_id, base_date):
    """
    완료 풀이 기록을 주 단위로 묶어 오답률 막대를 만든다.
    """
    daily_rows = get_completed_record_daily_rows(user_id)
    grouped_rows = {}
    for row in daily_rows:
        recorded_date = row["session__recorded_date"]
        week_start = recorded_date - timedelta(days=recorded_date.weekday())
        add_record_group_summary(grouped_rows, week_start, row)

    base_week_start = base_date - timedelta(days=base_date.weekday())
    bars = []
    for week_start, summary in sorted(grouped_rows.items(), reverse=True):
        week_end = week_start + timedelta(days=6)
        bars.append(
            build_record_chart_bar(
                format_week_chart_label(week_start, base_week_start),
                "주간 분석",
                summary["total_count"],
                summary["wrong_count"],
                get_group_average_time_sec(summary),
                format_period_label(week_start, week_end),
            )
        )

    return bars


def get_monthly_scope_chart_bars(user_id, base_date):
    """
    완료 풀이 기록을 월 단위로 묶어 오답률 막대를 만든다.
    """
    daily_rows = get_completed_record_daily_rows(user_id)
    grouped_rows = {}
    for row in daily_rows:
        recorded_date = row["session__recorded_date"]
        month_start = recorded_date.replace(day=1)
        add_record_group_summary(grouped_rows, month_start, row)

    base_month_start = base_date.replace(day=1)
    bars = []
    for month_start, summary in sorted(grouped_rows.items(), reverse=True):
        bars.append(
            build_record_chart_bar(
                format_month_chart_label(month_start, base_month_start),
                "월간 분석",
                summary["total_count"],
                summary["wrong_count"],
                get_group_average_time_sec(summary),
                format_period_label(month_start, get_month_end(month_start)),
            )
        )

    return bars


def get_total_scope_chart_bars(user_id):
    """
    완료된 전체 풀이 기록의 누적 오답률 막대를 만든다.
    """
    try:
        stats = SolveRecords.objects.filter(
            session__user_id=user_id,
            session__status="completed",
        ).aggregate(
            total_count=Count("record_id"),
            wrong_count=Count("record_id", filter=Q(is_correct=False)),
            average_time_ms=Avg("time_spent_ms"),
            period_start=Min("session__recorded_date"),
            period_end=Max("session__recorded_date"),
        )
    except DatabaseError:
        return []

    total_count = stats["total_count"] or 0
    if not total_count:
        return []

    return [
        build_record_chart_bar(
            "전체",
            "누적 분석",
            total_count,
            stats["wrong_count"] or 0,
            ms_to_sec(stats["average_time_ms"]),
            format_period_label(stats["period_start"], stats["period_end"]),
        )
    ]


def get_analysis_run_scope_chart_bars(user_id, analysis_scope):
    """
    analytics에 저장된 실행 묶음을 x축 막대로 만든다.
    """
    run_groups = get_analysis_run_groups(user_id, analysis_scope)
    bars = []
    for index, rows in enumerate(run_groups, start=1):
        if not rows:
            continue
        summary_rows = get_scope_summary_rows(rows)
        summary = calculate_scope_row_summary(summary_rows)
        if not summary["total_count"]:
            continue
        representative_row = summary_rows[0] if summary_rows else rows[0]
        period_start = representative_row.period_start
        period_end = representative_row.period_end
        bars.append(
            build_record_chart_bar(
                format_analysis_run_chart_label(analysis_scope, representative_row, index),
                "저장 분석",
                summary["total_count"],
                summary["wrong_count"],
                summary["average_time_sec"],
                format_period_label(period_start, period_end),
                format_datetime_label(representative_row.created_at),
            )
        )

    return bars


def get_analysis_run_groups(user_id, analysis_scope):
    """
    analytics row를 analysis_run_id별 실행 묶음으로 나눈다.
    """
    try:
        rows = list(
            Analytics.objects.filter(
                user_id=user_id,
                analysis_scope=analysis_scope,
            ).order_by("-created_at", "-analytics_id")
        )
    except DatabaseError:
        return []

    groups = {}
    for row in rows:
        groups.setdefault(row.analysis_run_id, [])
        groups[row.analysis_run_id].append(row)

    return list(groups.values())


def get_completed_record_daily_rows(user_id):
    """
    완료 풀이 기록을 날짜별로 집계한다.
    """
    try:
        return list(
            SolveRecords.objects.filter(
                session__user_id=user_id,
                session__status="completed",
            )
            .values("session__recorded_date")
            .annotate(
                total_count=Count("record_id"),
                wrong_count=Count("record_id", filter=Q(is_correct=False)),
                average_time_ms=Avg("time_spent_ms"),
            )
            .order_by("session__recorded_date")
        )
    except DatabaseError:
        return []


def add_record_group_summary(grouped_rows, group_key, row):
    """
    날짜 단위 집계 row를 주/월 그룹 집계에 누적한다.
    """
    summary = grouped_rows.setdefault(
        group_key,
        {
            "total_count": 0,
            "wrong_count": 0,
            "weighted_time_ms": 0,
            "time_weight": 0,
        },
    )
    total_count = row["total_count"] or 0
    summary["total_count"] += total_count
    summary["wrong_count"] += row["wrong_count"] or 0

    average_time_ms = row["average_time_ms"]
    if average_time_ms is not None and total_count:
        summary["weighted_time_ms"] += average_time_ms * total_count
        summary["time_weight"] += total_count


def get_group_average_time_sec(summary):
    """
    누적된 그룹 집계에서 평균 풀이시간 초 값을 계산한다.
    """
    if not summary["time_weight"]:
        return None

    return ms_to_sec(summary["weighted_time_ms"] / summary["time_weight"])


def build_record_chart_bar(
    label,
    description,
    total_count,
    wrong_count,
    average_time_sec,
    period_label,
    created_label="-",
):
    """
    그래프 막대 하나에 필요한 표시 데이터를 만든다.
    """
    total_count = total_count or 0
    wrong_count = wrong_count or 0
    wrong_rate = calculate_percent_rate(wrong_count, total_count)
    return {
        "label": label,
        "description": description,
        "totalCount": total_count,
        "wrongCount": wrong_count,
        "wrongRate": wrong_rate,
        "wrongRateLabel": f"{wrong_rate}%",
        "averageTimeLabel": format_seconds(average_time_sec),
        "periodLabel": period_label,
        "createdLabel": created_label,
        "statusClass": get_scope_status_class(total_count, wrong_rate),
    }


def format_session_type_label(session_type):
    """
    세션 유형 코드를 화면 표시용 라벨로 바꾼다.
    """
    if session_type == "diagnosis":
        return "진단평가"
    elif session_type == "practice":
        return "문제풀이"
    elif session_type:
        return session_type

    return "세션"


def format_week_chart_label(week_start, base_week_start):
    """
    주 시작일을 이번 주, n주 전 형태의 x축 라벨로 변환한다.
    """
    week_diff = (base_week_start - week_start).days // 7
    if week_diff == 0:
        return "이번 주"
    elif week_diff > 0:
        return f"{week_diff}주 전"

    return f"{abs(week_diff)}주 후"


def format_month_chart_label(month_start, base_month_start):
    """
    월 시작일을 이번 달, n개월 전 형태의 x축 라벨로 변환한다.
    """
    month_diff = (
        (base_month_start.year - month_start.year) * 12
        + base_month_start.month
        - month_start.month
    )
    if month_diff == 0:
        return "이번 달"
    elif month_diff > 0:
        return f"{month_diff}개월 전"

    return f"{abs(month_diff)}개월 후"


def get_month_end(month_start):
    """
    월 시작일 기준 해당 월의 마지막 날짜를 반환한다.
    """
    if month_start.month == 12:
        next_month = date(month_start.year + 1, 1, 1)
    elif month_start.month < 12:
        next_month = date(month_start.year, month_start.month + 1, 1)

    return next_month - timedelta(days=1)


def format_analysis_run_chart_label(analysis_scope, row, index):
    """
    저장 분석 실행 묶음의 x축 라벨을 만든다.
    """
    if row.studyplan_id:
        return f"계획 {row.studyplan_id}"
    elif analysis_scope == "study_plan_base":
        return f"기준 {index}"
    elif analysis_scope == "study_plan_result":
        return f"결과 {index}"

    return f"실행 {index}"


def build_analysis_scope_overview_item(user_id, scope_config):
    """
    하나의 analysis_scope에 대한 최신 실행 요약 카드를 만든다.
    """
    latest_rows = get_latest_scope_rows(user_id, scope_config["scope"])
    if not latest_rows:
        return {
            "label": scope_config["label"],
            "description": scope_config["description"],
            "total_count": 0,
            "wrong_count": 0,
            "wrong_rate": 0,
            "wrong_rate_label": "기록 없음",
            "average_time_label": "00:00",
            "period_label": "분석 전",
            "created_label": "-",
            "status_class": "empty",
        }

    summary_rows = get_scope_summary_rows(latest_rows)
    summary = calculate_scope_row_summary(summary_rows)
    created_at = latest_rows[0].created_at
    period_start = summary_rows[0].period_start if summary_rows else None
    period_end = summary_rows[0].period_end if summary_rows else None
    return {
        "label": scope_config["label"],
        "description": scope_config["description"],
        "total_count": summary["total_count"],
        "wrong_count": summary["wrong_count"],
        "wrong_rate": summary["wrong_rate"],
        "wrong_rate_label": f"{summary['wrong_rate']}%",
        "average_time_label": format_seconds(summary["average_time_sec"]),
        "period_label": format_period_label(period_start, period_end),
        "created_label": format_datetime_label(created_at),
        "status_class": get_scope_status_class(summary["total_count"], summary["wrong_rate"]),
    }


def get_latest_scope_rows(user_id, analysis_scope):
    """
    주어진 scope의 최신 analysis_run_id에 속한 row 목록을 반환한다.
    """
    try:
        latest_run_id = (
            Analytics.objects.filter(
                user_id=user_id,
                analysis_scope=analysis_scope,
            )
            .order_by("-created_at", "-analytics_id")
            .values_list("analysis_run_id", flat=True)
            .first()
        )
    except DatabaseError:
        return []

    if latest_run_id is None:
        return []

    try:
        return list(
            Analytics.objects.filter(
                user_id=user_id,
                analysis_scope=analysis_scope,
                analysis_run_id=latest_run_id,
            ).order_by("-created_at", "analysis_unit", "key_concept")
        )
    except DatabaseError:
        return []


def get_scope_summary_rows(rows):
    """
    overall row가 있으면 우선 사용하고, 없으면 중복 집계를 피할 단위를 고른다.
    """
    overall_rows = [row for row in rows if row.analysis_unit == "overall"]
    if overall_rows:
        return overall_rows[:1]

    for analysis_unit in ["era", "type", "topic"]:
        unit_rows = [row for row in rows if row.analysis_unit == analysis_unit]
        if unit_rows:
            return unit_rows

    return []


def calculate_scope_row_summary(rows):
    """
    analytics row 목록을 전체 문항 수, 오답 수, 오답률, 평균 시간으로 집계한다.
    """
    total_count = sum(row.total_count or 0 for row in rows)
    wrong_count = sum(row.wrong_count or 0 for row in rows)
    weighted_time_sum = sum(
        (row.avg_time_sec or 0) * (row.total_count or 0)
        for row in rows
        if row.avg_time_sec is not None
    )
    time_weight = sum(
        row.total_count or 0
        for row in rows
        if row.avg_time_sec is not None
    )
    average_time_sec = None
    if time_weight:
        average_time_sec = int(round(weighted_time_sum / time_weight))

    return {
        "total_count": total_count,
        "wrong_count": wrong_count,
        "wrong_rate": calculate_percent_rate(wrong_count, total_count),
        "average_time_sec": average_time_sec,
    }


def get_scope_status_class(total_count, wrong_rate):
    """
    분석 요약 카드의 상태 클래스를 정한다.
    """
    weak_rate_threshold = 20
    status_class = "stable"
    if not total_count:
        status_class = "empty"
    elif wrong_rate >= weak_rate_threshold:
        status_class = "weak"

    return status_class


def format_period_label(period_start, period_end):
    """
    분석 기간을 카드 표시 문자열로 변환한다.
    """
    if period_start is None and period_end is None:
        return "최근 실행"
    if period_start == period_end:
        return period_start.strftime("%Y.%m.%d")
    if period_start is None:
        return f"~ {period_end.strftime('%Y.%m.%d')}"
    if period_end is None:
        return f"{period_start.strftime('%Y.%m.%d')} ~"

    return f"{period_start.strftime('%Y.%m.%d')} - {period_end.strftime('%m.%d')}"


def format_datetime_label(value):
    """
    분석 생성 시각을 카드 표시 문자열로 변환한다.
    """
    if value is None:
        return "-"
    if timezone.is_naive(value):
        value = timezone.make_aware(value, timezone.get_current_timezone())

    return timezone.localtime(value).strftime("%m.%d %H:%M")


def format_seconds(seconds):
    """
    초 단위 시간을 MM:SS 문자열로 변환한다.
    """
    if seconds is None:
        return "00:00"

    total_seconds = max(0, int(round(seconds)))
    minutes, remaining_seconds = divmod(total_seconds, 60)
    return f"{minutes:02d}:{remaining_seconds:02d}"


def analytics_summary(user_id):
    """
    마이페이지와 분석 화면에 필요한 전체 분석 응답 묶음을 생성한다.

    전체 요약, 시대/유형/주제별 통계, 점수 추이,
    취약 항목, 추천 학습 대상을 한 번에 구성한다.
    """
    completed_sessions = get_completed_sessions(user_id)
    completed_records = get_completed_records(user_id)

    return {
        "analyticsSummary": build_analytics_summary(
            completed_sessions,
            completed_records,
        ),
        "analyticsByEra": build_group_stats(completed_records, "era", "era"),
        "analyticsByType": build_group_stats(
            completed_records,
            "q_type",
            "questionType",
        ),
        "analyticsByTopic": build_group_stats(completed_records, "topic", "topic"),
        "analyticsScoreTrend": build_score_trend(completed_sessions),
        "weakTargets": build_weak_targets(completed_records),
        "recommendedStudyTargets": build_recommended_study_targets(completed_records),
    }


def get_weak_targets(user_id):
    """
    학습계획에서 사용할 시대/유형/주제별 취약 항목을 생성한다.

    완료된 풀이 기록을 기준으로 분류별 오답률과 평균 풀이시간을 계산하고,
    오답률이 높고 오래 걸린 항목이 먼저 오도록 정렬한다.
    """
    completed_records = get_completed_records(user_id)
    return build_weak_targets(completed_records)


def get_weekly_practice_summary(user_id, today=None):
    """
    이번 주 practice 세션의 학습 요약을 계산한다.

    주간 범위는 월요일부터 일요일까지로 잡는다.
    정답률은 미응답을 오답으로 포함한 전체 문항 기준으로 계산하고,
    풀이 수는 실제 답을 고른 문항 수로 반환한다.
    """
    base_date = today or timezone.localdate()
    week_start = base_date - timedelta(days=base_date.weekday())
    next_week_start = week_start + timedelta(days=7)
    weekly_sessions = get_practice_sessions(user_id).filter(
        recorded_date__gte=week_start,
        recorded_date__lt=next_week_start,
    )
    return build_practice_session_summary(
        weekly_sessions,
        period_start=week_start,
        period_end=next_week_start - timedelta(days=1),
    )


def get_first_diagnosis_summary(user_id):
    """
    사용자의 첫 completed 진단평가 결과를 요약한다.

    가장 오래된 diagnostic 세션을 기준으로 정답률, 풀이 수,
    평균 문제 풀이시간과 진단 일자를 반환한다.
    """
    completed_status = "completed"
    diagnostic_type = "diagnostic"
    session = (
        SolveSessions.objects.filter(
            user_id=user_id,
            status=completed_status,
            session_type=diagnostic_type,
        )
        .order_by("recorded_date", "session_id")
        .first()
    )
    if not session:
        return {
            "answerRate": 0,
            "solvedCount": 0,
            "totalQuestionCount": 0,
            "averageQuestionTimeSec": None,
            "hasRecords": False,
            "recordedDate": None,
            "sessionId": None,
        }

    records = SolveRecords.objects.filter(session=session)
    summary = build_record_summary(records)

    summary["recordedDate"] = session.recorded_date
    summary["sessionId"] = session.session_id
    return summary


def get_diagnosis_improvement_summary(user_id, today=None):
    """
    첫 진단평가 기준의 개선 정도를 계산한다.

    진단평가 이후의 이번 주 practice를 첫 진단평가 대비 향상도로 비교한다.
    """
    diagnosis_summary = get_first_diagnosis_summary(user_id)
    post_diagnosis_summary = get_post_diagnosis_weekly_practice_summary(
        user_id,
        diagnosis_summary,
        today,
    )
    has_comparison = (
        diagnosis_summary["hasRecords"]
        and post_diagnosis_summary["hasRecords"]
    )
    answer_rate_change = None
    average_question_time_change_sec = None
    if has_comparison:
        answer_rate_change = (
            post_diagnosis_summary["answerRate"] - diagnosis_summary["answerRate"]
        )
        if (
            diagnosis_summary["averageQuestionTimeSec"] is not None
            and post_diagnosis_summary["averageQuestionTimeSec"] is not None
        ):
            average_question_time_change_sec = (
                post_diagnosis_summary["averageQuestionTimeSec"]
                - diagnosis_summary["averageQuestionTimeSec"]
            )

    return {
        "diagnosis": diagnosis_summary,
        "current": post_diagnosis_summary,
        "answerRateChange": answer_rate_change,
        "averageQuestionTimeChangeSec": average_question_time_change_sec,
        "hasComparison": has_comparison,
    }


def get_post_diagnosis_weekly_practice_summary(user_id, diagnosis_summary, today=None):
    """
    첫 진단평가 이후에 완료한 이번 주 practice 세션 요약을 반환한다.
    """
    base_date = today or timezone.localdate()
    week_start = base_date - timedelta(days=base_date.weekday())
    next_week_start = week_start + timedelta(days=7)
    sessions = get_practice_sessions(user_id).filter(
        recorded_date__gte=week_start,
        recorded_date__lt=next_week_start,
    )
    if diagnosis_summary["recordedDate"] is not None:
        sessions = filter_sessions_after(
            sessions,
            diagnosis_summary["recordedDate"],
            diagnosis_summary["sessionId"],
        )

    return build_practice_session_summary(
        sessions,
        period_start=week_start,
        period_end=next_week_start - timedelta(days=1),
    )


def get_completed_sessions(user_id):
    """
    사용자의 완료된 풀이 세션 QuerySet을 반환한다.

    여러 분석 함수에서 공통으로 사용하는 기본 세션 필터다.
    """
    return SolveSessions.objects.filter(
        user_id=user_id,
        status="completed",
    )


def get_completed_records(user_id):
    """
    사용자의 완료된 세션에 속한 풀이 기록 QuerySet을 반환한다.

    미응답 문항도 오답 분석 기준에 포함하기 위해 selected_no가 없는 row도 유지한다.
    """
    return SolveRecords.objects.filter(
        session__user_id=user_id,
        session__status="completed",
    )


def get_practice_sessions(user_id):
    """
    사용자의 completed practice 세션 QuerySet을 반환한다.
    """
    completed_status = "completed"
    practice_type = "practice"
    return SolveSessions.objects.filter(
        user_id=user_id,
        status=completed_status,
        session_type=practice_type,
    )


def filter_sessions_after(sessions, recorded_date, session_id):
    """
    기준 세션 이후에 완료된 세션만 남긴다.
    """
    return sessions.filter(
        Q(recorded_date__gt=recorded_date)
        | Q(recorded_date=recorded_date, session_id__gt=session_id)
    )


def build_practice_session_summary(sessions, period_start=None, period_end=None):
    """
    practice 세션 목록을 정답률, 풀이 수, 평균 시간 요약으로 만든다.
    """
    records = SolveRecords.objects.filter(session__in=sessions)
    summary = build_record_summary(records)
    session_stats = sessions.aggregate(
        average_session_time_sec=Avg("elapsed_sec"),
    )
    average_session_time_sec = None
    if session_stats["average_session_time_sec"] is not None:
        average_session_time_sec = int(round(session_stats["average_session_time_sec"]))

    summary["averageSessionTimeSec"] = average_session_time_sec
    summary["periodStart"] = period_start
    summary["periodEnd"] = period_end
    return summary


def build_record_summary(records):
    """
    풀이 기록 QuerySet을 정답률, 실제 풀이 수, 평균 시간 요약으로 만든다.
    """
    record_stats = records.aggregate(
        total_count=Count("record_id"),
        solved_count=Count("record_id", filter=Q(selected_no__isnull=False)),
        correct_count=Count("record_id", filter=Q(is_correct=True)),
        average_time_ms=Avg("time_spent_ms"),
    )
    total_count = record_stats["total_count"] or 0
    solved_count = record_stats["solved_count"] or 0
    correct_count = record_stats["correct_count"] or 0

    return {
        "answerRate": calculate_percent_rate(correct_count, total_count),
        "solvedCount": solved_count,
        "totalQuestionCount": total_count,
        "averageQuestionTimeSec": ms_to_sec(record_stats["average_time_ms"]),
        "averageSessionTimeSec": None,
        "hasRecords": total_count > 0,
    }


def build_analytics_summary(sessions, records):
    """
    전체 풀이 요약 통계를 만든다.

    세션에서는 평균 점수와 평균 정답률을 계산하고,
    풀이 기록에서는 총 풀이 수와 평균 문제 풀이시간을 계산한다.
    """
    session_stats = sessions.aggregate(
        average_score=Avg("total_score"),
        average_answer_rate=Avg("answer_rate"),
    )
    record_stats = records.aggregate(
        total_solve_count=Count("record_id"),
        average_time_ms=Avg("time_spent_ms"),
    )

    return {
        "totalSolveCount": record_stats["total_solve_count"] or 0,
        "averageScore": round_float(session_stats["average_score"]),
        "averageAnswerRate": round_float(session_stats["average_answer_rate"]),
        "averageTimeSec": ms_to_sec(record_stats["average_time_ms"]),
    }


def build_group_stats(records, field_name, response_key):
    """
    지정한 SolveRecords 컬럼 기준으로 분류별 통계를 집계한다.

    시대, 유형, 주제처럼 하나의 컬럼으로 묶을 수 있는 분석에 사용하며,
    화면 응답 키는 response_key로 맞춰서 반환한다.
    """
    rows = (
        records.values(field_name)
        .annotate(
            total_count=Count("record_id"),
            correct_count=Count("record_id", filter=Q(is_correct=True)),
            average_time_ms=Avg("time_spent_ms"),
        )
        .order_by(field_name)
    )

    stats = []
    for row in rows:
        total_count = row["total_count"] or 0
        correct_count = row["correct_count"] or 0
        wrong_count = total_count - correct_count
        stats.append(
            {
                response_key: row[field_name] or get_unclassified_label(),
                "totalCount": total_count,
                "answerRate": calculate_rate(correct_count, total_count),
                "wrongRate": calculate_rate(wrong_count, total_count),
                "averageTimeSec": ms_to_sec(row["average_time_ms"]),
            }
        )

    return stats


def build_score_trend(sessions):
    """
    날짜별 평균 점수와 평균 정답률 추이를 만든다.

    completed 세션을 recorded_date 기준으로 묶어
    학습 성과가 날짜별로 어떻게 변했는지 표시하는 데 사용한다.
    """
    rows = (
        sessions.values("recorded_date")
        .annotate(
            average_score=Avg("total_score"),
            average_answer_rate=Avg("answer_rate"),
        )
        .order_by("recorded_date")
    )

    return [
        {
            "date": row["recorded_date"],
            "averageScore": round_float(row["average_score"]),
            "averageAnswerRate": round_float(row["average_answer_rate"]),
        }
        for row in rows
    ]


def build_weak_targets(records):
    """
    시대, 유형, 주제별 통계를 하나의 취약 항목 목록으로 합친다.

    오답률을 1순위, 평균 풀이시간을 2순위로 두고 정렬해
    학습계획에서 우선 보완할 대상을 선택할 수 있게 한다.
    """
    weak_targets = []
    for classification, field_name in get_classification_fields():
        stats = build_group_stats(records, field_name, "label")
        for stat in stats:
            weak_targets.append(
                {
                    "classification": classification,
                    "label": stat["label"],
                    "wrongRate": stat["wrongRate"],
                    "averageTimeSec": stat["averageTimeSec"] or 0,
                }
            )

    return sorted(
        weak_targets,
        key=lambda item: (
            -item["wrongRate"],
            -item["averageTimeSec"],
            item["classification"],
            item["label"],
        ),
    )


def build_recommended_study_targets(records):
    """
    시대+주제 조합에서 오답이 발생한 항목을 추천 학습 대상으로 만든다.

    오답 수가 있는 조합만 남기고,
    오답 수와 전체 풀이 수가 많은 항목을 우선순위 상위에 둔다.
    """
    rows = (
        records.values("era", "topic")
        .annotate(
            total_count=Count("record_id"),
            wrong_count=Count("record_id", filter=Q(is_correct=False)),
        )
        .filter(wrong_count__gt=0)
        .order_by("-wrong_count", "-total_count", "era", "topic")
    )

    targets = []
    for priority, row in enumerate(rows, start=1):
        targets.append(
            {
                "era": row["era"] or get_unclassified_label(),
                "topic": row["topic"] or get_unclassified_label(),
                "reason": make_recommendation_reason(row),
                "priority": priority,
                "recommendedQuestionCount": row["wrong_count"] or 0,
            }
        )

    return targets


def get_wrong_rate_group_stats(user, field_name):
    """
    오답률 상세 페이지에서 사용할 단일 분류 기준 통계를 조회한다.

    field_name으로 전달된 era/q_type/topic 기준으로 전체 수,
    오답 수, 오답률, 평균 풀이시간을 계산한다.
    """
    rows = (
        SolveRecords.objects.filter(
            session__user=user,
            session__status="completed",
        )
        .values(field_name)
        .annotate(
            total=Count("record_id"),
            wrong=Count("record_id", filter=Q(is_correct=False)),
            average_time_ms=Avg("time_spent_ms"),
        )
        .order_by(field_name)
    )

    stats = []
    for row in rows:
        total = row["total"] or 0
        wrong = row["wrong"] or 0
        stats.append(
            {
                "label": row[field_name],
                "total": total,
                "wrong": wrong,
                "rate": calculate_percent_rate(wrong, total),
                "averageTimeSec": ms_to_sec(row["average_time_ms"]),
            }
        )

    return stats


def make_recommendation_reason(row):
    """
    추천 학습 대상에 표시할 사유 문장을 만든다.

    시대와 주제 라벨, 오답 수를 조합해
    사용자가 왜 추천되었는지 이해할 수 있는 문장을 반환한다.
    """
    era = row["era"] or get_unclassified_label()
    topic = row["topic"] or get_unclassified_label()
    wrong_count = row["wrong_count"] or 0
    return f"{era} / {topic}에서 오답 {wrong_count}건이 발생했습니다."


def get_classification_fields():
    """
    취약점 분석에 사용할 분류명과 SolveRecords 컬럼 매핑을 반환한다.

    시대, 유형, 주제 분석에서 같은 집계 함수를 재사용하기 위한 기준 목록이다.
    """
    return [
        ("시대", "era"),
        ("유형", "q_type"),
        ("주제", "topic"),
    ]


def get_unclassified_label():
    """
    분류값이 비어 있을 때 사용할 기본 라벨을 반환한다.

    era, q_type, topic 값이 없을 때 화면에 빈 문자열이 노출되지 않게 한다.
    """
    return "미분류"


def calculate_rate(count, total):
    """
    count / total 비율을 0~1 사이 소수 4자리로 계산한다.

    total이 0이면 ZeroDivisionError를 피하기 위해 0.0을 반환한다.
    """
    if total:
        return round(count / total, 4)

    return 0.0


def calculate_percent_rate(count, total):
    """
    count / total 비율을 화면 표시용 0~100 정수 퍼센트로 계산한다.

    반올림 후에도 안전하게 0 이상 100 이하 값으로 제한한다.
    """
    if not total:
        return 0

    rate = round((count / total) * 100)
    return max(0, min(100, rate))


def round_float(value):
    """
    None 값을 0.0으로 처리하고 실수를 소수 4자리로 반올림한다.

    DB aggregate 결과가 None일 수 있는 평균 점수/정답률 처리에 사용한다.
    """
    if value is None:
        return 0.0

    return round(float(value), 4)


def ms_to_sec(value):
    """
    밀리초 단위 풀이 시간을 초 단위 정수로 변환한다.

    값이 없으면 None을 유지해 화면에서 기록 없음 상태를 구분할 수 있게 한다.
    """
    if value is None:
        return None

    return int(round(value / 1000))


def create_analytics(user_id):
    """
    완료된 세션별 분석 결과를 analytics 테이블에 재생성한다.

    제출 직후 세션 단위 분석 저장과 같은 create_session_snapshot 경로를 사용해
    overall/시대/유형/주제 row 형식을 일관되게 유지한다.
    """
    from analytics.service.analysis_snapshot import create_session_snapshot

    sessions = get_completed_sessions(user_id)
    created_rows = []
    for session in sessions:
        created_rows.extend(create_session_snapshot(session.session_id))

    return created_rows
