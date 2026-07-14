from datetime import date, timedelta

from django.db import DatabaseError
from django.db.models import Avg, Count, Max, Min, Q, Sum
from django.utils import timezone
from analytics.models import Analytics, StudyPlanMypage
from analytics.serializers import parse_study_plan_items
from analytics.service.classification import (
    normalize_classification_value,
    should_normalize_classification,
)
from analytics.service.taxonomy import (
    build_target_display_label,
    get_display_label as get_taxonomy_display_label,
    get_unclassified_label as get_taxonomy_unclassified_label,
)
from analytics.service.weakness import (
    build_group_key_id,
    build_weakness_rows,
    get_weakness_config,
)
from question.models import QuestionOptions, SolveRecords, SolveSessions


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
    오답률 상세 상단 그래프에 표시할 scope별 라인차트 데이터를 만든다.
    """
    return [
        build_analysis_scope_chart_group(
            "session",
            "세션별",
            "세션별 오답률",
            "완료된 세션을 시간순으로 비교합니다.",
            get_session_scope_chart_bars(user_id),
        ),
        build_analysis_scope_chart_group(
            "period",
            "기간별",
            "기간별 오답률",
            "달력에서 선택한 기간의 일별 오답률을 비교합니다.",
            get_period_scope_chart_bars(user_id),
            count_unit="일자",
        ),
    ]


def build_wrong_rate_detail_validation(user_id, chart_groups):
    """
    임시 진단용 검증이다.

    사용자 입력 검증이 아니라, 상세 분석 그래프에 현재 사용자가 아닌
    세션이 섞이는지 확인하기 위한 안전망이다. 원인 확인 후 테스트로
    대체하고 제거할 수 있다.
    """
    session_ids = []
    for group in chart_groups:
        if group.get("scope") == "session":
            for bar in group.get("bars", []):
                session_id = bar.get("sessionId")
                if session_id:
                    session_ids.append(session_id)

    unique_session_ids = sorted(set(session_ids))
    owned_session_ids = set()
    if unique_session_ids:
        owned_session_ids = set(
            SolveSessions.objects.filter(
                user_id=user_id,
                session_id__in=unique_session_ids,
            ).values_list("session_id", flat=True)
        )

    invalid_session_ids = [
        session_id
        for session_id in unique_session_ids
        if session_id not in owned_session_ids
    ]

    return {
        "userId": user_id,
        "sessionChartCount": len(session_ids),
        "uniqueSessionCount": len(unique_session_ids),
        "invalidSessionIds": invalid_session_ids,
        "isValid": not invalid_session_ids,
    }


def get_recent_wrong_rate_period(today=None, day_count=7):
    """
    최근 N일 기준 분석 기간을 만든다.
    """
    end_date = today or timezone.localdate()
    normalized_day_count = max(day_count, 1)
    start_date = end_date - timedelta(days=normalized_day_count - 1)
    return {
        "startDate": start_date,
        "endDate": end_date,
        "label": format_period_label(start_date, end_date),
        "dayCount": normalized_day_count,
    }


def get_plan_completion_chart_data(user_id):
    """
    오답률 흐름과 분리해 표시할 계획별 달성도 라인차트 데이터를 만든다.
    """
    return build_analysis_scope_chart_group(
        "plan",
        "계획별",
        "계획별 달성도",
        "학습계획 블록에서 시작된 풀이 기록 기준으로 달성률을 표시합니다.",
        get_plan_scope_chart_bars(user_id),
        metric_label="달성률",
        count_unit="계획",
    )


def build_analysis_scope_chart_group(
    scope,
    label,
    title,
    description,
    bars,
    metric_label="오답률",
    count_unit="구간",
):
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
        "metricLabel": metric_label,
        "countUnit": count_unit,
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
        session_type_label = format_session_type_label(row["session__session_type"])
        label = format_wrong_rate_session_chart_date(recorded_date)
        description = f"세션 #{row['session_id']} · {session_type_label}"
        period_label = format_period_label(recorded_date, recorded_date)
        bars.append(
            build_record_chart_bar(
                label,
                description,
                row["total_count"],
                row["wrong_count"],
                ms_to_sec(row["average_time_ms"]),
                period_label,
                extra_data={
                    "sessionId": row["session_id"],
                    "detailLine": "",
                    "subDetailLine": "",
                },
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


def get_period_scope_chart_bars(user_id):
    """
    완료 풀이 기록을 일자별로 묶어 기간 선택용 오답률 막대를 만든다.
    """
    daily_rows = get_completed_record_daily_rows(user_id)
    bars = []
    for row in daily_rows:
        recorded_date = row["session__recorded_date"]
        if not recorded_date:
            continue
        bars.append(
            build_record_chart_bar(
                recorded_date.strftime("%m.%d"),
                "일별 분석",
                row["total_count"],
                row["wrong_count"],
                ms_to_sec(row["average_time_ms"]),
                format_period_label(recorded_date, recorded_date),
                extra_data={"dateKey": recorded_date.isoformat()},
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


def get_plan_scope_chart_bars(user_id):
    """
    학습계획별 실제 풀이 기록 기반 달성률 라인차트 데이터를 만든다.
    """
    from analytics.service.studyplan import (
        calculate_record_based_plan_progress,
        get_study_plan_config,
    )

    try:
        config = get_study_plan_config()
        active_plans = list(
            StudyPlanMypage.objects.filter(user_id=user_id, status="active")
            .order_by("-plan_version", "-modified_at")[:1]
        )
        archived_plans = list(
            StudyPlanMypage.objects.filter(user_id=user_id, status="archived")
            .order_by("-plan_version", "-modified_at")[:config["history_display_limit"]]
        )
    except DatabaseError:
        return []

    study_plans = sorted(
        archived_plans + active_plans,
        key=lambda study_plan: (
            study_plan.plan_version or 0,
            study_plan.created_at or timezone.now(),
        ),
    )
    bars = []
    for study_plan in study_plans:
        progress = calculate_record_based_plan_progress(user_id, study_plan)["summary"]
        if not progress["targetCount"]:
            continue
        bars.append(build_plan_progress_chart_point(study_plan, progress))

    return bars


def build_plan_progress_chart_point(study_plan, progress):
    """
    학습계획 달성도 라인차트 점 하나에 필요한 표시 데이터를 만든다.
    """
    target_count = progress["targetCount"] or 0
    completion_percent = progress["completionPercent"] or 0
    status_label = format_plan_status_label(study_plan.status)
    period_label = progress.get("periodLabel") or format_period_label(
        study_plan.start_date,
        study_plan.end_date,
    )

    return {
        "label": format_plan_progress_chart_label(study_plan),
        "description": status_label,
        "totalCount": target_count,
        "wrongCount": 0,
        "wrongRate": completion_percent,
        "wrongRateLabel": f"{completion_percent}%",
        "chartValue": completion_percent,
        "chartValueLabel": f"{completion_percent}%",
        "metricLabel": "달성률",
        "averageTimeLabel": "",
        "periodLabel": period_label,
        "createdLabel": status_label,
        "detailLine": "",
        "subDetailLine": "",
        "statusClass": get_plan_progress_status_class(target_count, completion_percent),
        "planScope": "progress",
    }


def format_plan_progress_chart_label(study_plan):
    """
    계획별 달성도 차트의 x축 라벨을 만든다.
    """
    start_date = study_plan.start_date
    end_date = study_plan.end_date
    if start_date and end_date and start_date == end_date:
        return f"{start_date.strftime('%m.%d')} 계획"
    elif start_date and end_date:
        return f"{start_date.strftime('%m.%d')} - {end_date.strftime('%m.%d')}"
    elif start_date:
        return f"{start_date.strftime('%m.%d')} 시작"

    return f"계획 {study_plan.plan_version}"


def format_plan_status_label(status):
    """
    학습계획 상태 코드를 화면 표시용 라벨로 바꾼다.
    """
    if status == "active":
        return "진행 중"
    elif status == "archived":
        return "종료"
    elif status == "deleted":
        return "삭제"

    return status or "계획"


def get_plan_progress_status_class(target_count, completion_percent):
    """
    계획 달성도 점의 상태 클래스를 정한다.
    """
    low_progress_threshold = 50
    if not target_count:
        return "empty"
    elif completion_percent < low_progress_threshold:
        return "weak"

    return "stable"


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
    extra_data=None,
):
    """
    그래프 막대 하나에 필요한 표시 데이터를 만든다.
    """
    total_count = total_count or 0
    wrong_count = wrong_count or 0
    wrong_rate = calculate_percent_rate(wrong_count, total_count)
    chart_bar = {
        "label": label,
        "description": description,
        "totalCount": total_count,
        "wrongCount": wrong_count,
        "wrongRate": wrong_rate,
        "wrongRateLabel": f"{wrong_rate}%",
        "chartValue": wrong_rate,
        "chartValueLabel": f"{wrong_rate}%",
        "metricLabel": "오답률",
        "averageTimeLabel": format_seconds(average_time_sec),
        "periodLabel": period_label,
        "createdLabel": created_label,
        "detailLine": f"{total_count}문제 · 문제당 {format_seconds(average_time_sec)}",
        "subDetailLine": f"{period_label} · {created_label}",
        "rawRateClass": get_raw_rate_class(total_count, wrong_rate),
    }
    if extra_data:
        chart_bar.update(extra_data)

    return chart_bar


def format_session_type_label(session_type):
    """
    세션 유형 코드를 화면 표시용 라벨로 바꾼다.
    """
    if session_type in ["diagnosis", "diagnostic"]:
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
        "status_class": get_raw_rate_class(summary["total_count"], summary["wrong_rate"]),
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


def get_raw_rate_class(total_count, wrong_rate):
    """
    raw 오답률 표시용 상태 클래스를 정한다.
    """
    stable_rate_threshold = get_wrong_rate_stable_threshold()
    weak_rate_threshold = get_wrong_rate_weak_threshold()
    status_class = "neutral"
    if not total_count:
        status_class = "empty"
    elif wrong_rate >= weak_rate_threshold:
        status_class = "weak"
    elif wrong_rate <= stable_rate_threshold:
        status_class = "stable"

    return status_class


def get_scope_status_class(total_count, wrong_rate):
    return get_raw_rate_class(total_count, wrong_rate)


def get_wrong_rate_stable_threshold():
    return 30


def get_wrong_rate_weak_threshold():
    return 70


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


def get_composite_weak_targets(user_id):
    """
    학습계획에서 사용할 시대+주제+유형 복합 취약 항목을 생성한다.
    """
    completed_records = get_completed_records(user_id)
    return build_composite_weak_targets(completed_records)


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
    직전 평가와 최신 주간평가 사이의 개선 정도를 계산한다.

    첫 주간평가는 직전 일반 진단평가와 비교하고, 이후 주간평가는
    바로 전 주간평가와 정답률·풀이시간을 비교한다. 주간평가가 없으면
    기존 일반 진단평가 회차 비교를 유지한다.
    """
    diagnosis_pair = get_diagnosis_comparison_pair(user_id)
    diagnosis_summary = diagnosis_pair["diagnosis"]
    post_diagnosis_summary = diagnosis_pair["current"]
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
        "hasDiagnosis": diagnosis_summary["hasRecords"],
        "hasWeeklyReviewPlan": bool(get_weekly_review_block_refs(user_id)),
        "hasPostDiagnosisPractice": has_post_diagnosis_practice_records(
            user_id,
            diagnosis_summary,
            today,
        ),
        "diagnosisSessionCount": diagnosis_pair["diagnosisSessionCount"],
    }


def get_diagnosis_comparison_pair(user_id):
    """
    일반 진단평가와 주간평가를 구분해 마이페이지 비교 세션 쌍을 선택한다.
    """
    weekly_sessions = list(get_completed_weekly_review_sessions(user_id))
    weekly_session_ids = [session.session_id for session in weekly_sessions]
    diagnosis_sessions = list(
        get_completed_diagnostic_sessions(user_id).exclude(
            session_id__in=weekly_session_ids,
        )
    )
    if len(weekly_sessions) >= 2:
        previous_session = weekly_sessions[-2]
        current_session = weekly_sessions[-1]
        return {
            "diagnosis": build_evaluation_session_summary(
                previous_session,
                f"{len(weekly_sessions) - 1}주차 주간평가",
                len(weekly_sessions) - 1,
            ),
            "current": build_evaluation_session_summary(
                current_session,
                f"{len(weekly_sessions)}주차 주간평가",
                len(weekly_sessions),
            ),
            "diagnosisSessionCount": len(diagnosis_sessions) + len(weekly_sessions),
        }

    if weekly_sessions:
        current_session = weekly_sessions[-1]
        baseline_sessions = [
            session
            for session in diagnosis_sessions
            if is_session_before(session, current_session)
        ]
        if baseline_sessions:
            return {
                "diagnosis": build_evaluation_session_summary(
                    baseline_sessions[-1],
                    "직전 진단평가",
                    len(diagnosis_sessions),
                ),
                "current": build_evaluation_session_summary(
                    current_session,
                    "1주차 주간평가",
                    1,
                ),
                "diagnosisSessionCount": len(diagnosis_sessions) + 1,
            }

        return {
            "diagnosis": build_empty_diagnosis_session_summary(),
            "current": build_evaluation_session_summary(
                current_session,
                "1주차 주간평가",
                1,
            ),
            "diagnosisSessionCount": 1,
        }

    if len(diagnosis_sessions) >= 2:
        previous_session = diagnosis_sessions[-2]
        current_session = diagnosis_sessions[-1]
        return {
            "diagnosis": build_diagnosis_session_summary(
                previous_session,
                len(diagnosis_sessions) - 1,
            ),
            "current": build_diagnosis_session_summary(
                current_session,
                len(diagnosis_sessions),
            ),
            "diagnosisSessionCount": len(diagnosis_sessions),
        }

    if diagnosis_sessions:
        return {
            "diagnosis": build_diagnosis_session_summary(diagnosis_sessions[0], 1),
            "current": build_empty_diagnosis_session_summary(),
            "diagnosisSessionCount": 1,
        }

    return {
        "diagnosis": build_empty_diagnosis_session_summary(),
        "current": build_empty_diagnosis_session_summary(),
        "diagnosisSessionCount": 0,
    }


def get_completed_diagnostic_sessions(user_id):
    completed_status = "completed"
    diagnostic_type = "diagnostic"
    return SolveSessions.objects.filter(
        user_id=user_id,
        status=completed_status,
        session_type=diagnostic_type,
    ).order_by("recorded_date", "session_id")


def get_completed_weekly_review_sessions(user_id):
    """
    review_type 도입 전 fallback으로 계획 블록 연결값이 있는 주간평가를 찾는다.
    """
    block_refs = get_weekly_review_block_refs(user_id)
    records = get_weekly_review_records(user_id, block_refs)
    session_ids = records.values_list("session_id", flat=True).distinct()
    return SolveSessions.objects.filter(
        user_id=user_id,
        status="completed",
        session_type="diagnostic",
        session_id__in=session_ids,
    ).order_by("recorded_date", "session_id")


def is_session_before(candidate_session, reference_session):
    candidate_key = (candidate_session.recorded_date, candidate_session.session_id)
    reference_key = (reference_session.recorded_date, reference_session.session_id)
    return candidate_key < reference_key


def build_diagnosis_session_summary(session, session_number):
    return build_evaluation_session_summary(
        session,
        f"{session_number}회차 진단평가",
        session_number,
    )


def build_evaluation_session_summary(session, session_label, session_number):
    records = SolveRecords.objects.filter(session=session)
    summary = build_record_summary(records)
    summary["recordedDate"] = session.recorded_date
    summary["sessionId"] = session.session_id
    summary["sessionNumber"] = session_number
    summary["sessionLabel"] = session_label
    summary["averageSessionTimeSec"] = session.elapsed_sec
    summary["periodStart"] = session.recorded_date
    summary["periodEnd"] = session.recorded_date
    return summary


def build_empty_diagnosis_session_summary():
    summary = build_record_summary(SolveRecords.objects.none())
    summary["recordedDate"] = None
    summary["sessionId"] = None
    summary["sessionNumber"] = None
    summary["sessionLabel"] = "진단평가"
    summary["averageSessionTimeSec"] = None
    summary["periodStart"] = None
    summary["periodEnd"] = None
    return summary


def get_post_diagnosis_weekly_review_summary(user_id, diagnosis_summary):
    """
    첫 진단평가 이후 완료한 주간평가 기록 요약을 반환한다.
    """
    block_refs = get_weekly_review_block_refs(user_id)
    summary = build_record_summary(SolveRecords.objects.none())
    summary["averageSessionTimeSec"] = None
    summary["hasWeeklyReviewPlan"] = bool(block_refs)
    summary["periodStart"] = None
    summary["periodEnd"] = None
    if not block_refs:
        fallback_sessions = get_post_diagnosis_diagnostic_sessions(
            user_id,
            diagnosis_summary,
        )
        records = SolveRecords.objects.filter(session__in=fallback_sessions)
        if not records.exists():
            return summary

        return build_post_diagnosis_review_summary(records, bool(block_refs))

    records = get_weekly_review_records(user_id, block_refs)
    if diagnosis_summary["recordedDate"] is not None:
        records = filter_records_after(
            records,
            diagnosis_summary["recordedDate"],
            diagnosis_summary["sessionId"],
        )
    if not records.exists():
        fallback_sessions = get_post_diagnosis_diagnostic_sessions(
            user_id,
            diagnosis_summary,
        )
        records = SolveRecords.objects.filter(session__in=fallback_sessions)

    return build_post_diagnosis_review_summary(records, bool(block_refs))


def build_post_diagnosis_review_summary(records, has_weekly_review_plan):
    sessions = SolveSessions.objects.filter(
        session_id__in=records.values_list("session_id", flat=True).distinct(),
    )
    summary = build_record_summary(records)
    session_stats = sessions.aggregate(
        average_session_time_sec=Avg("elapsed_sec"),
    )
    average_session_time_sec = None
    if session_stats["average_session_time_sec"] is not None:
        average_session_time_sec = int(round(session_stats["average_session_time_sec"]))

    period_bounds = sessions.aggregate(
        period_start=Min("recorded_date"),
        period_end=Max("recorded_date"),
    )
    summary["averageSessionTimeSec"] = average_session_time_sec
    summary["periodStart"] = period_bounds["period_start"]
    summary["periodEnd"] = period_bounds["period_end"]
    summary["hasWeeklyReviewPlan"] = has_weekly_review_plan
    return summary


def get_post_diagnosis_diagnostic_sessions(user_id, diagnosis_summary):
    completed_status = "completed"
    diagnostic_type = "diagnostic"
    sessions = SolveSessions.objects.filter(
        user_id=user_id,
        status=completed_status,
        session_type=diagnostic_type,
    )
    if diagnosis_summary["recordedDate"] is not None:
        sessions = filter_sessions_after(
            sessions,
            diagnosis_summary["recordedDate"],
            diagnosis_summary["sessionId"],
        )

    return sessions


def get_weekly_review_block_refs(user_id):
    """
    사용자의 학습계획 중 주간평가 블록 식별자를 모은다.
    """
    from analytics.service.studyplan import get_study_plan_config

    weekly_review_type = get_study_plan_config()["weekly_review_block_type"]
    block_refs = []
    study_plans = StudyPlanMypage.objects.filter(user_id=user_id).exclude(status="deleted")
    for study_plan in study_plans:
        plan_items = parse_study_plan_items(study_plan.study_plan_items)
        for day_plan in plan_items:
            for block in day_plan.get("blocks", []):
                block_id = block.get("blockId")
                if block.get("blockType") == weekly_review_type and block_id:
                    block_refs.append(
                        {
                            "studyplan_id": study_plan.studyplan_id,
                            "block_id": str(block_id),
                        }
                    )

    return block_refs


def get_weekly_review_records(user_id, block_refs):
    """
    주간평가 블록과 연결된 완료 풀이 기록만 조회한다.
    """
    block_query = Q()
    for block_ref in block_refs:
        block_query |= Q(
            studyplan_id=block_ref["studyplan_id"],
            study_plan_block_id=block_ref["block_id"],
        )

    if not block_query:
        return SolveRecords.objects.none()

    return SolveRecords.objects.filter(
        block_query,
        session__user_id=user_id,
        session__status="completed",
    )


def filter_records_after(records, recorded_date, session_id):
    """
    기준 세션 이후에 완료된 기록만 남긴다.
    """
    return records.filter(
        Q(session__recorded_date__gt=recorded_date)
        | Q(session__recorded_date=recorded_date, session_id__gt=session_id)
    )


def has_post_diagnosis_practice_records(user_id, diagnosis_summary, today=None):
    """
    진단평가 이후 일반 문제풀이 기록이 있는지 확인한다.
    """
    if diagnosis_summary["recordedDate"] is None:
        return False

    sessions = filter_sessions_after(
        get_practice_sessions(user_id),
        diagnosis_summary["recordedDate"],
        diagnosis_summary["sessionId"],
    )
    return SolveRecords.objects.filter(session__in=sessions).exists()


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
            wrong_count=Count("record_id", filter=Q(is_correct=False)),
            total_time_ms=Sum("time_spent_ms"),
            time_count=Count("time_spent_ms"),
        )
        .order_by(field_name)
    )

    return [
        {
            response_key: summary["label"],
            "totalCount": summary["totalCount"],
            "answerRate": calculate_rate(summary["correctCount"], summary["totalCount"]),
            "wrongRate": calculate_rate(summary["wrongCount"], summary["totalCount"]),
            "averageTimeSec": get_group_average_time_from_summary(summary),
        }
        for summary in build_classification_group_summaries(rows, field_name)
    ]


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


def build_composite_weak_targets(records):
    """
    시대, 주제, 유형 조합별 취약 항목 목록을 만든다.

    단일 분류가 아니라 실제 학습계획에서 사용할 복합 조건을 기준으로
    보정 취약 점수와 평균 풀이시간을 계산한다.
    """
    config = get_weakness_config()
    weakness_rows = build_weakness_rows(records, ["era", "topic", "q_type"])
    weak_targets = []
    for row in weakness_rows:
        group = row["group"]
        era = group.get("era") or ""
        topic = group.get("topic") or ""
        q_type = group.get("qType") or ""
        if config["unclassified_label"] in (era, topic, q_type):
            continue

        weak_targets.append(
            {
                "classification": "복합",
                "label": build_composite_target_label(era, topic, q_type),
                "era": era,
                "topic": topic,
                "qType": q_type,
                "wrongRate": row["raw"]["wrongRate"],
                "wrongCount": row["raw"]["wrong"],
                "totalCount": row["raw"]["total"],
                "weaknessScore": row["weaknessScore"],
                "weaknessStatus": row["status"],
                "trend": row["trend"],
                "averageTimeSec": row["raw"]["avgTimeSec"] or 0,
            }
        )

    return sorted(
        weak_targets,
        key=lambda item: (
            -item["weaknessScore"],
            -item["wrongCount"],
            -item["averageTimeSec"],
            item["era"],
            item["topic"],
            item["qType"],
        ),
    )


def build_composite_target_label(era, topic, q_type):
    """
    복합 취약 항목의 화면 표시 라벨을 만든다.
    """
    return build_target_display_label(era, topic, q_type)


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

    target_map = {}
    for row in rows:
        era = normalize_classification_value("era", row["era"])
        topic = normalize_classification_value("topic", row["topic"])
        if era and topic:
            key = (era, topic)
            if key not in target_map:
                target_map[key] = {
                    "era": era,
                    "topic": topic,
                    "total_count": 0,
                    "wrong_count": 0,
                }
            target_map[key]["total_count"] += row["total_count"] or 0
            target_map[key]["wrong_count"] += row["wrong_count"] or 0

    sorted_targets = sorted(
        target_map.values(),
        key=lambda item: (
            -item["wrong_count"],
            -item["total_count"],
            item["era"],
            item["topic"],
        ),
    )

    targets = []
    for priority, row in enumerate(sorted_targets, start=1):
        targets.append(
            {
                "era": row["era"],
                "topic": row["topic"],
                "reason": make_recommendation_reason(row),
                "priority": priority,
                "recommendedQuestionCount": row["wrong_count"] or 0,
            }
        )

    return targets


def get_wrong_rate_group_stats(user, field_name, start_date=None, end_date=None):
    """
    오답률 상세 페이지에서 사용할 단일 분류 기준 통계를 조회한다.

    field_name으로 전달된 era/q_type/topic 기준으로 전체 수,
    오답 수, 오답률, 평균 풀이시간을 계산한다.
    """
    queryset = SolveRecords.objects.filter(
        session__user=user,
        session__status="completed",
    )
    if start_date:
        queryset = queryset.filter(session__recorded_date__gte=start_date)
    if end_date:
        queryset = queryset.filter(session__recorded_date__lte=end_date)

    rows = (
        queryset
        .values(field_name)
        .annotate(
            total_count=Count("record_id"),
            correct_count=Count("record_id", filter=Q(is_correct=True)),
            wrong_count=Count("record_id", filter=Q(is_correct=False)),
            total_time_ms=Sum("time_spent_ms"),
            time_count=Count("time_spent_ms"),
        )
        .order_by(field_name)
    )

    return [
        {
            "label": summary["label"],
            "groupKeyId": build_group_key_id([field_name], [summary["label"]]),
            "groupKey": [[field_name, summary["label"]]],
            "total": summary["totalCount"],
            "wrong": summary["wrongCount"],
            "rate": calculate_percent_rate(summary["wrongCount"], summary["totalCount"]),
            "averageTimeSec": get_group_average_time_from_summary(summary),
        }
        for summary in build_classification_group_summaries(rows, field_name)
    ]


def get_wrong_rate_weakness_rows(user, field_name, today=None):
    """
    오답률 상세 페이지의 배지·정렬에 사용할 공용 취약 판정 row를 만든다.
    """
    queryset = SolveRecords.objects.filter(
        session__user=user,
        session__status="completed",
    )
    return build_weakness_rows(queryset, [field_name], today)


def get_wrong_rate_item_session_details(user, category, label):
    """
    시대/유형/주제 항목 하나를 완료 세션별 오답률로 다시 집계한다.
    """
    category_config = get_wrong_rate_detail_category_config(category)
    if category_config is None:
        return None

    field_name = category_config["field"]
    display_label = label or get_unclassified_label()
    rows = get_wrong_rate_session_rows(user, field_name, display_label)
    return {
        "categoryLabel": category_config["label"],
        "itemLabel": display_label,
        "title": f"{category_config['label']} · {display_label}",
        "hasRecords": bool(rows),
        "sessions": [
            build_wrong_rate_session_detail(row)
            for row in rows
        ],
    }


def get_wrong_rate_session_analysis_detail(user, session_id):
    """
    한 세션의 전체 오답률과 시대/유형/주제별 취약 항목을 함께 반환한다.
    """
    try:
        normalized_session_id = int(session_id)
        session = SolveSessions.objects.filter(
            session_id=normalized_session_id,
            user=user,
            status="completed",
        ).first()
    except (TypeError, ValueError, DatabaseError):
        return None

    if session is None:
        return None

    records = SolveRecords.objects.filter(session=session)
    session_type_label = format_session_type_label(session.session_type)
    groups = [
        {
            "title": "시대별 오답률",
            "items": build_session_group_analysis(records, "era", "시대", "era"),
        },
        {
            "title": "주제별 오답률",
            "items": build_session_group_analysis(records, "topic", "주제", "topic"),
        },
        {
            "title": "유형별 오답률",
            "items": build_session_group_analysis(records, "q_type", "유형", "type"),
        },
    ]
    return {
        "categoryLabel": "세션 상세 분석",
        "title": format_session_analysis_title(session, session_type_label),
        "overview": build_session_analysis_overview(session, records, session_type_label),
        "topWeakTitle": "세션 오답 TOP",
        "topWeakItems": build_session_top_weak_items(groups),
        "groups": groups,
    }


def get_wrong_rate_session_item_questions(user, session_id, category, label):
    """
    선택한 세션 안에서 시대/유형/주제 항목에 해당하는 문제 목록을 반환한다.
    """
    category_config = get_wrong_rate_detail_category_config(category)
    if category_config is None:
        return None

    try:
        normalized_session_id = int(session_id)
        session = SolveSessions.objects.filter(
            session_id=normalized_session_id,
            user=user,
            status="completed",
        ).first()
    except (TypeError, ValueError, DatabaseError):
        return None

    if session is None:
        return None

    display_label = label or get_unclassified_label()
    field_name = category_config["field"]
    records = (
        filter_records_by_classification_label(
            SolveRecords.objects.filter(session=session).select_related("question"),
            field_name,
            display_label,
        )
        .filter(is_correct=False)
        .order_by("question_id")
    )
    record_list = list(records)
    option_content_map = get_question_option_content_map(record_list)
    session_type_label = format_session_type_label(session.session_type)
    return {
        "categoryLabel": category_config["label"],
        "itemLabel": display_label,
        "sessionLabel": format_session_analysis_title(session, session_type_label),
        "title": f"{display_label} 문제 목록",
        "hasRecords": bool(record_list),
        "questions": [
            build_wrong_rate_question_detail(record, option_content_map)
            for record in record_list
        ],
    }


def get_wrong_rate_period_analysis_detail(user, start_date, end_date):
    """
    선택 기간의 전체 오답률과 시대/주제/유형별 취약 항목을 함께 반환한다.
    """
    if start_date is None or end_date is None:
        return None
    if start_date > end_date:
        start_date, end_date = end_date, start_date

    records = get_period_records(user, start_date, end_date)
    groups = [
        {
            "title": "시대별 오답률",
            "items": build_session_group_analysis(records, "era", "시대", "era"),
        },
        {
            "title": "주제별 오답률",
            "items": build_session_group_analysis(records, "topic", "주제", "topic"),
        },
        {
            "title": "유형별 오답률",
            "items": build_session_group_analysis(records, "q_type", "유형", "type"),
        },
    ]
    return {
        "categoryLabel": "기간 상세 분석",
        "title": format_period_label(start_date, end_date),
        "period": {
            "startDate": start_date.isoformat(),
            "endDate": end_date.isoformat(),
            "label": format_period_label(start_date, end_date),
        },
        "overview": build_period_analysis_overview(records, start_date, end_date),
        "change": build_period_change_summary(records),
        "topWeakTitle": "기간 오답 TOP",
        "topWeakItems": build_session_top_weak_items(groups),
        "groups": groups,
    }


def get_wrong_rate_period_item_questions(user, start_date, end_date, category, label):
    """
    선택 기간 안에서 시대/유형/주제 항목에 해당하는 오답 문제 목록을 반환한다.
    """
    category_config = get_wrong_rate_detail_category_config(category)
    if category_config is None:
        return None
    if start_date is None or end_date is None:
        return None
    if start_date > end_date:
        start_date, end_date = end_date, start_date

    display_label = label or get_unclassified_label()
    field_name = category_config["field"]
    records = (
        filter_records_by_classification_label(
            get_period_records(user, start_date, end_date).select_related("question"),
            field_name,
            display_label,
        )
        .filter(is_correct=False)
        .order_by("session__recorded_date", "question_id")
    )
    record_list = list(records)
    option_content_map = get_question_option_content_map(record_list)
    return {
        "categoryLabel": category_config["label"],
        "itemLabel": display_label,
        "periodLabel": format_period_label(start_date, end_date),
        "title": f"{display_label} 문제 목록",
        "hasRecords": bool(record_list),
        "questions": [
            build_wrong_rate_question_detail(record, option_content_map)
            for record in record_list
        ],
    }


def get_period_records(user, start_date, end_date):
    """
    선택 기간에 포함된 완료 풀이 기록 queryset을 반환한다.
    """
    return SolveRecords.objects.filter(
        session__user=user,
        session__status="completed",
        session__recorded_date__gte=start_date,
        session__recorded_date__lte=end_date,
    )


def build_period_analysis_overview(records, start_date, end_date):
    """
    선택 기간 전체 기준 정답률, 오답률, 풀이시간 요약을 만든다.
    """
    stats = records.aggregate(
        session_count=Count("session_id", distinct=True),
        total_count=Count("record_id"),
        solved_count=Count("record_id", filter=Q(selected_no__isnull=False)),
        correct_count=Count("record_id", filter=Q(is_correct=True)),
        wrong_count=Count("record_id", filter=Q(is_correct=False)),
        average_time_ms=Avg("time_spent_ms"),
    )
    total_count = stats["total_count"] or 0
    solved_count = stats["solved_count"] or 0
    correct_count = stats["correct_count"] or 0
    wrong_count = stats["wrong_count"] or 0
    unanswered_count = max(total_count - solved_count, 0)
    answered_wrong_count = max(wrong_count - unanswered_count, 0)
    return {
        "periodLabel": format_period_label(start_date, end_date),
        "sessionCount": stats["session_count"] or 0,
        "totalCount": total_count,
        "solvedCount": solved_count,
        "correctCount": correct_count,
        "wrongCount": wrong_count,
        "answeredWrongCount": answered_wrong_count,
        "unansweredCount": unanswered_count,
        "answerRate": calculate_percent_rate(correct_count, total_count),
        "wrongRate": calculate_percent_rate(wrong_count, total_count),
        "averageTimeLabel": format_seconds(ms_to_sec(stats["average_time_ms"])),
        "stack": build_session_answer_stack(
            correct_count,
            answered_wrong_count,
            unanswered_count,
            total_count,
        ),
    }


def build_period_change_summary(records):
    """
    선택 기간의 첫 기록일과 마지막 기록일 사이의 변화량을 만든다.
    """
    daily_rows = list(
        records.values("session__recorded_date")
        .annotate(
            total_count=Count("record_id"),
            correct_count=Count("record_id", filter=Q(is_correct=True)),
            wrong_count=Count("record_id", filter=Q(is_correct=False)),
            average_time_ms=Avg("time_spent_ms"),
        )
        .order_by("session__recorded_date")
    )
    if len(daily_rows) < 2:
        return {
            "hasComparison": False,
            "message": "비교할 날짜가 부족합니다.",
            "metrics": [],
        }

    start_row = daily_rows[0]
    end_row = daily_rows[-1]
    start_label = format_wrong_rate_session_date(start_row["session__recorded_date"])
    end_label = format_wrong_rate_session_date(end_row["session__recorded_date"])
    return {
        "hasComparison": True,
        "startLabel": start_label,
        "endLabel": end_label,
        "metrics": [
            build_percent_change_metric(
                "오답률",
                calculate_percent_rate(start_row["wrong_count"], start_row["total_count"]),
                calculate_percent_rate(end_row["wrong_count"], end_row["total_count"]),
                lower_is_better=True,
            ),
            build_percent_change_metric(
                "정답률",
                calculate_percent_rate(start_row["correct_count"], start_row["total_count"]),
                calculate_percent_rate(end_row["correct_count"], end_row["total_count"]),
                lower_is_better=False,
            ),
            build_time_change_metric(
                "문제당 평균 풀이시간",
                ms_to_sec(start_row["average_time_ms"]),
                ms_to_sec(end_row["average_time_ms"]),
            ),
            build_count_change_metric(
                "풀이 수",
                start_row["total_count"] or 0,
                end_row["total_count"] or 0,
            ),
        ],
    }


def build_percent_change_metric(label, start_value, end_value, lower_is_better):
    """
    퍼센트 기반 변화량 표시 데이터를 만든다.
    """
    change_value = end_value - start_value
    tone = get_change_tone(change_value, lower_is_better)
    return {
        "label": label,
        "startValue": f"{start_value}%",
        "endValue": f"{end_value}%",
        "changeLabel": format_signed_number(change_value, "%"),
        "tone": tone,
    }


def build_time_change_metric(label, start_value, end_value):
    """
    풀이시간 변화량 표시 데이터를 만든다.
    """
    start_seconds = start_value or 0
    end_seconds = end_value or 0
    change_value = int(round(end_seconds - start_seconds))
    tone = get_change_tone(change_value, lower_is_better=True)
    change_label = "변화 없음"
    if change_value < 0:
        change_label = f"{abs(change_value)}초 단축"
    elif change_value > 0:
        change_label = f"{change_value}초 증가"

    return {
        "label": label,
        "startValue": format_seconds(start_seconds),
        "endValue": format_seconds(end_seconds),
        "changeLabel": change_label,
        "tone": tone,
    }


def build_count_change_metric(label, start_value, end_value):
    """
    풀이 수 변화량 표시 데이터를 만든다.
    """
    change_value = end_value - start_value
    return {
        "label": label,
        "startValue": f"{start_value}문제",
        "endValue": f"{end_value}문제",
        "changeLabel": format_signed_number(change_value, "문제"),
        "tone": get_change_tone(change_value, lower_is_better=False),
    }


def get_change_tone(change_value, lower_is_better):
    """
    변화량의 긍정/주의/중립 톤을 반환한다.
    """
    if change_value == 0:
        return "neutral"
    if lower_is_better and change_value < 0:
        return "good"
    elif lower_is_better and change_value > 0:
        return "warn"
    elif not lower_is_better and change_value > 0:
        return "good"
    elif not lower_is_better and change_value < 0:
        return "warn"

    return "neutral"


def format_signed_number(value, suffix):
    """
    변화량 숫자를 부호와 단위가 있는 문자열로 만든다.
    """
    if value > 0:
        return f"+{value}{suffix}"
    elif value < 0:
        return f"{value}{suffix}"

    return f"0{suffix}"


def build_session_analysis_overview(session, records, session_type_label):
    """
    세션 전체 기준 정답률, 오답률, 풀이시간 요약을 만든다.
    """
    stats = records.aggregate(
        total_count=Count("record_id"),
        solved_count=Count("record_id", filter=Q(selected_no__isnull=False)),
        correct_count=Count("record_id", filter=Q(is_correct=True)),
        wrong_count=Count("record_id", filter=Q(is_correct=False)),
        average_time_ms=Avg("time_spent_ms"),
    )
    total_count = stats["total_count"] or 0
    solved_count = stats["solved_count"] or 0
    correct_count = stats["correct_count"] or 0
    wrong_count = stats["wrong_count"] or 0
    unanswered_count = max(total_count - solved_count, 0)
    answered_wrong_count = max(wrong_count - unanswered_count, 0)
    return {
        "sessionId": session.session_id,
        "sessionTypeLabel": session_type_label,
        "recordedDate": format_wrong_rate_session_date(session.recorded_date),
        "totalCount": total_count,
        "solvedCount": solved_count,
        "correctCount": correct_count,
        "wrongCount": wrong_count,
        "answeredWrongCount": answered_wrong_count,
        "unansweredCount": unanswered_count,
        "answerRate": calculate_percent_rate(correct_count, total_count),
        "wrongRate": calculate_percent_rate(wrong_count, total_count),
        "averageTimeLabel": format_seconds(ms_to_sec(stats["average_time_ms"])),
        "stack": build_session_answer_stack(
            correct_count,
            answered_wrong_count,
            unanswered_count,
            total_count,
        ),
    }


def build_session_group_analysis(records, field_name, classification_label, category):
    """
    한 세션 안에서 지정 분류별 오답률과 평균 풀이시간을 계산한다.
    """
    rows = (
        records.values(field_name)
        .annotate(
            total_count=Count("record_id"),
            correct_count=Count("record_id", filter=Q(is_correct=True)),
            wrong_count=Count("record_id", filter=Q(is_correct=False)),
            total_time_ms=Sum("time_spent_ms"),
            time_count=Count("time_spent_ms"),
        )
        .order_by(field_name)
    )
    items = [
        build_session_group_analysis_item(summary, classification_label, category)
        for summary in build_classification_group_summaries(rows, field_name)
    ]
    return sorted(
        items,
        key=lambda item: (
            -item["wrongRate"],
            -item["totalCount"],
            item["label"],
        ),
    )


def build_session_group_analysis_item(summary, classification_label, category):
    """
    세션별 분류 집계 row를 모달 표시용 dict로 변환한다.
    """
    total_count = summary["totalCount"]
    correct_count = summary["correctCount"]
    wrong_count = summary["wrongCount"]
    wrong_rate = calculate_percent_rate(wrong_count, total_count)
    return {
        "category": category,
        "classification": classification_label,
        "label": summary["label"],
        "totalCount": total_count,
        "correctCount": correct_count,
        "wrongCount": wrong_count,
        "answerRate": calculate_percent_rate(correct_count, total_count),
        "wrongRate": wrong_rate,
        "averageTimeLabel": format_seconds(get_group_average_time_from_summary(summary)),
        "statusClass": get_raw_rate_class(total_count, wrong_rate),
    }


def build_session_answer_stack(correct_count, wrong_count, unanswered_count, total_count):
    """
    세션 전체의 정답/오답/미응답 구성 비율을 만든다.
    """
    return {
        "correct": {
            "label": "정답",
            "count": correct_count,
            "percent": calculate_percent_rate(correct_count, total_count),
        },
        "wrong": {
            "label": "오답",
            "count": wrong_count,
            "percent": calculate_percent_rate(wrong_count, total_count),
        },
        "unanswered": {
            "label": "미응답",
            "count": unanswered_count,
            "percent": calculate_percent_rate(unanswered_count, total_count),
        },
    }


def build_session_top_weak_items(groups):
    """
    세션 안의 시대/유형/주제 취약 항목을 한 목록으로 합쳐 상위 항목만 반환한다.
    """
    display_limit = 5
    weak_rate_threshold = get_wrong_rate_weak_threshold()
    weak_items = []
    for group in groups:
        for item in group["items"]:
            if item["wrongRate"] >= weak_rate_threshold:
                weak_items.append(item)

    # TODO: ML 기반 출제가능성 점수가 연결되면 오답률 동률 시 출제가능성을
    # wrongCount/totalCount보다 먼저 비교해 TOP 취약 항목을 정렬한다.
    return sorted(
        weak_items,
        key=lambda item: (
            -item["wrongRate"],
            -item["wrongCount"],
            -item["totalCount"],
            item["classification"],
            item["label"],
        ),
    )[:display_limit]


def format_session_analysis_title(session, session_type_label):
    """
    세션 상세 모달 제목을 세션 번호와 유형으로 만든다.
    """
    return f"세션 #{session.session_id} · {session_type_label}"


def get_wrong_rate_detail_category_config(category):
    """
    화면의 분석 구분값을 SolveRecords 필드명으로 변환한다.
    """
    configs = {
        "era": {"field": "era", "label": "시대별 분석"},
        "type": {"field": "q_type", "label": "유형별 분석"},
        "q_type": {"field": "q_type", "label": "유형별 분석"},
        "topic": {"field": "topic", "label": "주제별 분석"},
    }
    return configs.get(category)


def filter_records_by_classification_label(queryset, field_name, label):
    """
    화면 분류 라벨을 기준으로 풀이 기록 queryset을 필터링한다.
    """
    display_label = label or get_unclassified_label()
    if should_normalize_classification(field_name):
        return filter_records_by_normalized_label(queryset, field_name, display_label)
    elif display_label == get_unclassified_label():
        return queryset.filter(
            Q(**{f"{field_name}__isnull": True})
            | Q(**{field_name: ""})
        )
    elif display_label != get_unclassified_label():
        return queryset.filter(**{field_name: display_label})

    return queryset


def filter_records_by_normalized_label(queryset, field_name, display_label):
    condition = Q(pk__in=[])
    for raw_value in queryset.values_list(field_name, flat=True).distinct():
        value_label = get_classification_display_label(field_name, raw_value)
        if value_label == display_label:
            condition |= build_raw_value_filter(field_name, raw_value)

    return queryset.filter(condition)


def build_raw_value_filter(field_name, raw_value):
    if raw_value is None:
        return Q(**{f"{field_name}__isnull": True})
    elif raw_value == "":
        return Q(**{field_name: ""})

    return Q(**{field_name: raw_value})


def get_question_option_content_map(records):
    """
    문제별 선택지 번호에 해당하는 지문을 빠르게 찾기 위한 map을 만든다.
    """
    question_ids = [record.question_id for record in records]
    options = QuestionOptions.objects.filter(question_id__in=question_ids).order_by(
        "question_id",
        "choice_no",
    )
    return {
        (option.question_id, option.choice_no): option.content
        for option in options
    }


def build_wrong_rate_question_detail(record, option_content_map):
    """
    세션 상세 항목에 속한 문제 하나를 모달 표시용 dict로 변환한다.
    """
    question = record.question
    selected_no = record.selected_no
    answer_no = question.answer_no
    return {
        "recordId": record.record_id,
        "questionId": question.question_id,
        "questionNo": question.question_no or question.question_id,
        "content": question.content,
        "passage": question.passage or "",
        "imageCaption": question.image_caption or "",
        "era": get_classification_display_label("era", record.era),
        "topic": get_classification_display_label("topic", record.topic),
        "questionType": record.q_type or get_unclassified_label(),
        "questionSubtype": question.question_subtype or "",
        "score": record.q_score,
        "selectedNo": selected_no,
        "selectedLabel": format_selected_answer_label(selected_no),
        "selectedContent": option_content_map.get((question.question_id, selected_no), ""),
        "answerNo": answer_no,
        "answerLabel": f"{answer_no}번",
        "answerContent": option_content_map.get((question.question_id, answer_no), ""),
        "isCorrect": record.is_correct,
        "resultLabel": format_question_result_label(record),
        "statusClass": get_question_result_status_class(record),
        "timeLabel": format_seconds(ms_to_sec(record.time_spent_ms)),
        "answerExplanation": question.answer_explanation or "",
        "coreConcept": question.core_concept or "",
    }


def format_selected_answer_label(selected_no):
    """
    선택 답안을 화면 표시용으로 변환한다.
    """
    if selected_no is None:
        return "미응답"

    return f"{selected_no}번"


def format_question_result_label(record):
    """
    문제 풀이 결과 라벨을 만든다.
    """
    if record.is_correct:
        return "정답"
    elif record.selected_no is None:
        return "미응답"

    return "오답"


def get_question_result_status_class(record):
    """
    문제 풀이 결과에 맞는 상태 클래스를 반환한다.
    """
    if record.is_correct:
        return "stable"
    elif record.selected_no is None:
        return "empty"

    return "weak"


def get_wrong_rate_session_rows(user, field_name, label):
    """
    선택한 분석 항목을 세션별 통계 row로 조회한다.
    """
    queryset = SolveRecords.objects.filter(
        session__user=user,
        session__status="completed",
    )
    queryset = filter_records_by_classification_label(queryset, field_name, label)

    return list(
        queryset.values(
            "session_id",
            "session__recorded_date",
            "session__session_type",
        )
        .annotate(
            total_count=Count("record_id"),
            correct_count=Count("record_id", filter=Q(is_correct=True)),
            wrong_count=Count("record_id", filter=Q(is_correct=False)),
            solved_count=Count("record_id", filter=Q(selected_no__isnull=False)),
            average_time_ms=Avg("time_spent_ms"),
        )
        .order_by("-session__recorded_date", "-session_id")
    )


def build_wrong_rate_session_detail(row):
    """
    세션별 통계 row를 팝업 표시용 dict로 변환한다.
    """
    total_count = row["total_count"] or 0
    correct_count = row["correct_count"] or 0
    wrong_count = row["wrong_count"] or 0
    recorded_date = row["session__recorded_date"]
    session_type_label = format_session_type_label(row["session__session_type"])
    return {
        "sessionId": row["session_id"],
        "title": format_wrong_rate_session_title(recorded_date, session_type_label),
        "sessionTypeLabel": session_type_label,
        "recordedDate": format_wrong_rate_session_date(recorded_date),
        "totalCount": total_count,
        "solvedCount": row["solved_count"] or 0,
        "correctCount": correct_count,
        "wrongCount": wrong_count,
        "answerRate": calculate_percent_rate(correct_count, total_count),
        "wrongRate": calculate_percent_rate(wrong_count, total_count),
        "averageTimeLabel": format_seconds(ms_to_sec(row["average_time_ms"])),
    }


def format_wrong_rate_session_title(recorded_date, session_type_label):
    """
    팝업 목록의 세션 제목을 날짜와 세션 유형으로 만든다.
    """
    if recorded_date:
        return f"{recorded_date.strftime('%m.%d')} {session_type_label}"

    return session_type_label


def format_wrong_rate_session_chart_date(recorded_date):
    """
    세션별 오답률 차트의 x축 날짜 라벨을 만든다.
    """
    if recorded_date:
        return recorded_date.strftime("%m.%d")

    return "-"


def format_wrong_rate_session_date(recorded_date):
    """
    세션 날짜를 YYYY.MM.DD 형식으로 반환한다.
    """
    if recorded_date:
        return recorded_date.strftime("%Y.%m.%d")

    return "-"


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


def build_classification_group_summaries(rows, field_name):
    summary_map = {}
    for row in rows:
        label = get_classification_display_label(field_name, row[field_name])
        if label not in summary_map:
            summary_map[label] = build_group_summary_seed(label)
        update_group_summary(summary_map[label], row)

    return sorted(summary_map.values(), key=lambda item: item["label"])


def build_group_summary_seed(label):
    return {
        "label": label,
        "totalCount": 0,
        "correctCount": 0,
        "wrongCount": 0,
        "totalTimeMs": 0,
        "timeCount": 0,
    }


def update_group_summary(summary, row):
    summary["totalCount"] += row["total_count"] or 0
    summary["correctCount"] += row["correct_count"] or 0
    summary["wrongCount"] += row["wrong_count"] or 0
    summary["totalTimeMs"] += row["total_time_ms"] or 0
    summary["timeCount"] += row["time_count"] or 0


def build_composite_group_seed(era, topic, q_type):
    seed = build_group_summary_seed(build_composite_target_label(era, topic, q_type))
    seed["era"] = era
    seed["topic"] = topic
    seed["qType"] = q_type
    return seed


def update_composite_group_summary(summary, row):
    summary["totalCount"] += row["total_count"] or 0
    summary["wrongCount"] += row["wrong_count"] or 0
    summary["totalTimeMs"] += row["total_time_ms"] or 0
    summary["timeCount"] += row["time_count"] or 0


def get_group_average_time_from_summary(summary):
    if summary["timeCount"]:
        return ms_to_sec(summary["totalTimeMs"] / summary["timeCount"])

    return None


def get_classification_display_label(field_name, value):
    return get_taxonomy_display_label(field_name, value)


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
    return get_taxonomy_unclassified_label()


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
