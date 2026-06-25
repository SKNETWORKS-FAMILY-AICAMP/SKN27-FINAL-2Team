from datetime import date, timedelta
from uuid import uuid4

from django.db.models import Avg, Count, Q
from django.utils import timezone

from analytics.models import Analytics
from question.models import SolveRecords, SolveSessions


def get_analysis_store_config():
    """
    확장된 analytics 테이블에 저장할 분석 scope, unit, 표시명을 반환한다.

    별도 snapshot 테이블을 만들지 않고 analytics.analysis_scope,
    analytics.analysis_run_id, analytics.analysis_unit으로 실행 단위를 구분한다.
    """
    return {
        "completed_status": "completed",
        "scope_session": "session",
        "scope_weekly": "weekly",
        "scope_monthly": "monthly",
        "scope_total": "total",
        "scope_study_plan_base": "study_plan_base",
        "scope_study_plan_result": "study_plan_result",
        "unit_overall": "overall",
        "unit_era": "era",
        "unit_type": "type",
        "unit_topic": "topic",
        "classification_overall": "전체",
        "classification_era": "시대",
        "classification_type": "유형",
        "classification_topic": "주제",
        "unclassified_label": "미분류",
        "overall_label": "전체",
    }


def create_session_snapshot(session_id):
    """
    특정 completed 세션 하나의 분석 결과를 analytics 테이블에 저장한다.

    제출 API에서 세션이 completed 처리된 직후 호출하는 진입점이며,
    같은 session/scope의 기존 row는 새 결과로 교체한다.
    """
    config = get_analysis_store_config()
    session = SolveSessions.objects.filter(
        session_id=session_id,
        status=config["completed_status"],
    ).first()
    if session is None:
        return []

    records = SolveRecords.objects.filter(
        session=session,
        selected_no__isnull=False,
    )
    return create_analysis_from_records(
        user_id=session.user_id,
        analysis_scope=config["scope_session"],
        records=records,
        session_id=session.session_id,
        period_start=session.recorded_date,
        period_end=session.recorded_date,
        replace_existing=True,
    )


def create_study_plan_base_snapshot(user_id, study_plan_id):
    """
    학습계획 생성 당시의 전체 completed 풀이 기준 분석 결과를 저장한다.

    특정 세션이 아니라 사용자의 누적 완료 기록을 기준으로 하며,
    studyplan_id를 함께 저장해 계획 생성 근거를 추적할 수 있게 한다.
    """
    config = get_analysis_store_config()
    sessions = get_completed_sessions(user_id)
    records = get_answered_records(sessions)
    return create_analysis_from_records(
        user_id=user_id,
        analysis_scope=config["scope_study_plan_base"],
        records=records,
        study_plan_id=study_plan_id,
        replace_existing=True,
    )


def create_study_plan_result_snapshot(user_id, study_plan_id, period_start=None, period_end=None):
    """
    학습계획 수행 이후 비교용 분석 결과를 analytics 테이블에 저장한다.

    계획 기간이 있으면 해당 기간의 completed 기록만 사용하고,
    기간이 없으면 사용자의 전체 completed 기록을 기준으로 저장한다.
    """
    config = get_analysis_store_config()
    sessions = get_completed_sessions(user_id)
    if period_start is not None:
        sessions = sessions.filter(recorded_date__gte=period_start)
    if period_end is not None:
        sessions = sessions.filter(recorded_date__lte=period_end)

    records = get_answered_records(sessions)
    return create_analysis_from_records(
        user_id=user_id,
        analysis_scope=config["scope_study_plan_result"],
        records=records,
        study_plan_id=study_plan_id,
        period_start=period_start,
        period_end=period_end,
        replace_existing=True,
    )


def create_weekly_snapshot(user_id, today=None, force=False):
    """
    이번 주 completed 풀이 기록 기준 분석 결과를 저장한다.

    force가 False이고 같은 기간의 분석이 이미 있으면 최신 row 묶음을 재사용한다.
    """
    config = get_analysis_store_config()
    period_start, period_end = get_week_range(today)
    if not force and has_existing_analysis(user_id, config["scope_weekly"], period_start, period_end):
        return get_latest_analysis_run(user_id, config["scope_weekly"], period_start, period_end)

    sessions = get_completed_sessions(user_id).filter(
        recorded_date__gte=period_start,
        recorded_date__lte=period_end,
    )
    records = get_answered_records(sessions)
    return create_analysis_from_records(
        user_id=user_id,
        analysis_scope=config["scope_weekly"],
        records=records,
        period_start=period_start,
        period_end=period_end,
        replace_existing=force,
    )


def create_monthly_snapshot(user_id, today=None, force=False):
    """
    이번 달 completed 풀이 기록 기준 분석 결과를 저장한다.

    월간 분석은 해당 월 1일부터 말일까지를 기간으로 사용한다.
    """
    config = get_analysis_store_config()
    period_start, period_end = get_month_range(today)
    if not force and has_existing_analysis(user_id, config["scope_monthly"], period_start, period_end):
        return get_latest_analysis_run(user_id, config["scope_monthly"], period_start, period_end)

    sessions = get_completed_sessions(user_id).filter(
        recorded_date__gte=period_start,
        recorded_date__lte=period_end,
    )
    records = get_answered_records(sessions)
    return create_analysis_from_records(
        user_id=user_id,
        analysis_scope=config["scope_monthly"],
        records=records,
        period_start=period_start,
        period_end=period_end,
        replace_existing=force,
    )


def create_total_snapshot(user_id, force=False):
    """
    사용자의 전체 completed 풀이 기록 기준 분석 결과를 저장한다.

    force가 False이고 total 분석이 이미 있으면 최신 row 묶음을 재사용한다.
    """
    config = get_analysis_store_config()
    if not force and has_existing_analysis(user_id, config["scope_total"]):
        return get_latest_analysis_run(user_id, config["scope_total"])

    sessions = get_completed_sessions(user_id)
    records = get_answered_records(sessions)
    return create_analysis_from_records(
        user_id=user_id,
        analysis_scope=config["scope_total"],
        records=records,
        replace_existing=force,
    )


def create_login_analysis_snapshots(user_id, today=None):
    """
    로그인 직후 백그라운드에서 호출할 주간/월간/전체 분석 저장 진입점이다.

    사용자를 기다리게 하지 않는 위치에서 호출하며,
    이미 같은 기간 분석이 있으면 재사용한다.
    """
    return refresh_user_analysis_snapshots(user_id, today, force=False)


def refresh_user_analysis_snapshots(user_id, today=None, force=True):
    """
    분석 상세 페이지 새로고침에서 사용할 강제 재분석 진입점이다.

    weekly, monthly, total 분석을 한 번에 생성하고 최신 결과 묶음을 반환한다.
    """
    return {
        "weekly": create_weekly_snapshot(user_id, today, force),
        "monthly": create_monthly_snapshot(user_id, today, force),
        "total": create_total_snapshot(user_id, force),
    }


def create_analysis_from_records(
    user_id,
    analysis_scope,
    records,
    session_id=None,
    study_plan_id=None,
    period_start=None,
    period_end=None,
    replace_existing=False,
):
    """
    전달받은 풀이 기록 QuerySet을 analytics row 묶음으로 저장한다.

    하나의 실행은 analysis_run_id로 묶고, overall/era/type/topic 단위의
    전체 수, 정답 수, 오답 수, 정답률, 오답률, 평균 풀이시간을 저장한다.
    """
    if replace_existing:
        delete_existing_analysis(
            user_id=user_id,
            analysis_scope=analysis_scope,
            session_id=session_id,
            study_plan_id=study_plan_id,
            period_start=period_start,
            period_end=period_end,
        )

    config = get_analysis_store_config()
    analysis_run_id = str(uuid4())
    created_at = timezone.now()
    analytics_rows = [
        build_overall_analysis_row(
            user_id=user_id,
            analysis_scope=analysis_scope,
            analysis_run_id=analysis_run_id,
            records=records,
            session_id=session_id,
            study_plan_id=study_plan_id,
            period_start=period_start,
            period_end=period_end,
            created_at=created_at,
            config=config,
        )
    ]
    analytics_rows.extend(
        build_group_analysis_rows(
            user_id=user_id,
            analysis_scope=analysis_scope,
            analysis_run_id=analysis_run_id,
            records=records,
            field_name="era",
            analysis_unit=config["unit_era"],
            classification=config["classification_era"],
            session_id=session_id,
            study_plan_id=study_plan_id,
            period_start=period_start,
            period_end=period_end,
            created_at=created_at,
            config=config,
        )
    )
    analytics_rows.extend(
        build_group_analysis_rows(
            user_id=user_id,
            analysis_scope=analysis_scope,
            analysis_run_id=analysis_run_id,
            records=records,
            field_name="q_type",
            analysis_unit=config["unit_type"],
            classification=config["classification_type"],
            session_id=session_id,
            study_plan_id=study_plan_id,
            period_start=period_start,
            period_end=period_end,
            created_at=created_at,
            config=config,
        )
    )
    analytics_rows.extend(
        build_group_analysis_rows(
            user_id=user_id,
            analysis_scope=analysis_scope,
            analysis_run_id=analysis_run_id,
            records=records,
            field_name="topic",
            analysis_unit=config["unit_topic"],
            classification=config["classification_topic"],
            session_id=session_id,
            study_plan_id=study_plan_id,
            period_start=period_start,
            period_end=period_end,
            created_at=created_at,
            config=config,
        )
    )

    Analytics.objects.bulk_create(analytics_rows)
    return analytics_rows


def build_overall_analysis_row(
    user_id,
    analysis_scope,
    analysis_run_id,
    records,
    session_id,
    study_plan_id,
    period_start,
    period_end,
    created_at,
    config,
):
    """
    전체 풀이 기록을 overall 분석 row 1개로 만든다.

    전체 문제 수, 정답 수, 오답 수, 정답률, 오답률, 평균 풀이시간을 담는다.
    """
    stats = records.aggregate(
        total_count=Count("record_id"),
        correct_count=Count("record_id", filter=Q(is_correct=True)),
        average_time_ms=Avg("time_spent_ms"),
    )
    return build_analysis_row(
        user_id=user_id,
        analysis_scope=analysis_scope,
        analysis_run_id=analysis_run_id,
        analysis_unit=config["unit_overall"],
        classification=config["classification_overall"],
        key_concept=config["overall_label"],
        total_count=stats["total_count"] or 0,
        correct_count=stats["correct_count"] or 0,
        average_time_ms=stats["average_time_ms"],
        session_id=session_id,
        study_plan_id=study_plan_id,
        period_start=period_start,
        period_end=period_end,
        created_at=created_at,
    )


def build_group_analysis_rows(
    user_id,
    analysis_scope,
    analysis_run_id,
    records,
    field_name,
    analysis_unit,
    classification,
    session_id,
    study_plan_id,
    period_start,
    period_end,
    created_at,
    config,
):
    """
    era/q_type/topic 같은 단일 컬럼 기준 분석 row 목록을 만든다.

    각 그룹의 전체 수, 정답 수, 오답 수, 정답률, 오답률, 평균 시간을 계산한다.
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

    analytics_rows = []
    for row in rows:
        analytics_rows.append(
            build_analysis_row(
                user_id=user_id,
                analysis_scope=analysis_scope,
                analysis_run_id=analysis_run_id,
                analysis_unit=analysis_unit,
                classification=classification,
                key_concept=row[field_name] or config["unclassified_label"],
                total_count=row["total_count"] or 0,
                correct_count=row["correct_count"] or 0,
                average_time_ms=row["average_time_ms"],
                session_id=session_id,
                study_plan_id=study_plan_id,
                period_start=period_start,
                period_end=period_end,
                created_at=created_at,
            )
        )

    return analytics_rows


def build_analysis_row(
    user_id,
    analysis_scope,
    analysis_run_id,
    analysis_unit,
    classification,
    key_concept,
    total_count,
    correct_count,
    average_time_ms,
    session_id,
    study_plan_id,
    period_start,
    period_end,
    created_at,
):
    """
    공통 집계값을 Analytics 모델 인스턴스로 변환한다.

    기존 topic_rate에는 하위 호환을 위해 answer_rate와 같은 값을 저장한다.
    """
    wrong_count = total_count - correct_count
    answer_rate = calculate_rate(correct_count, total_count)
    return Analytics(
        user_id=user_id,
        session_id=session_id,
        studyplan_id=study_plan_id,
        analysis_scope=analysis_scope,
        analysis_run_id=analysis_run_id,
        analysis_unit=analysis_unit,
        key_concept=key_concept,
        classification=classification,
        avg_time_sec=milliseconds_to_seconds(average_time_ms),
        topic_rate=answer_rate,
        total_count=total_count,
        correct_count=correct_count,
        wrong_count=wrong_count,
        answer_rate=answer_rate,
        wrong_rate=calculate_rate(wrong_count, total_count),
        period_start=period_start,
        period_end=period_end,
        created_at=created_at,
    )


def delete_existing_analysis(
    user_id,
    analysis_scope,
    session_id=None,
    study_plan_id=None,
    period_start=None,
    period_end=None,
):
    """
    같은 분석 범위와 기준에 해당하는 기존 analytics row를 삭제한다.

    session 분석, 학습계획 전후 분석, 기간 분석을 각각의 기준으로 좁혀 지운다.
    """
    queryset = Analytics.objects.filter(
        user_id=user_id,
        analysis_scope=analysis_scope,
    )
    if session_id is not None:
        queryset = queryset.filter(session_id=session_id)
    if study_plan_id is not None:
        queryset = queryset.filter(studyplan_id=study_plan_id)
    if period_start is not None:
        queryset = queryset.filter(period_start=period_start)
    if period_end is not None:
        queryset = queryset.filter(period_end=period_end)

    queryset.delete()


def has_existing_analysis(user_id, analysis_scope, period_start=None, period_end=None):
    """
    같은 사용자와 scope, 선택적 기간에 해당하는 분석 row가 있는지 확인한다.

    로그인 직후 백그라운드 분석에서 불필요한 중복 생성을 줄이는 데 사용한다.
    """
    return get_analysis_queryset(user_id, analysis_scope, period_start, period_end).exists()


def get_latest_analysis_run(user_id, analysis_scope, period_start=None, period_end=None):
    """
    같은 scope/기간의 최신 analysis_run_id에 속한 analytics row 묶음을 반환한다.

    화면이나 후속 로직에서 최신 분석 결과 묶음을 사용할 수 있게 한다.
    """
    queryset = get_analysis_queryset(user_id, analysis_scope, period_start, period_end)
    latest_run_id = (
        queryset.order_by("-created_at", "-analytics_id")
        .values_list("analysis_run_id", flat=True)
        .first()
    )
    if latest_run_id is None:
        return []

    return list(queryset.filter(analysis_run_id=latest_run_id))


def get_analysis_queryset(user_id, analysis_scope, period_start=None, period_end=None):
    """
    사용자와 scope, 선택적 기간 조건으로 analytics QuerySet을 만든다.

    중복 분석 확인과 최신 분석 묶음 조회에서 공통으로 사용한다.
    """
    queryset = Analytics.objects.filter(
        user_id=user_id,
        analysis_scope=analysis_scope,
    )
    if period_start is not None:
        queryset = queryset.filter(period_start=period_start)
    if period_end is not None:
        queryset = queryset.filter(period_end=period_end)

    return queryset


def get_completed_sessions(user_id):
    """
    분석 대상으로 사용할 사용자의 completed 세션 QuerySet을 반환한다.

    session, weekly, monthly, total, 학습계획 전후 분석에서 공통으로 사용한다.
    """
    config = get_analysis_store_config()
    return SolveSessions.objects.filter(
        user_id=user_id,
        status=config["completed_status"],
    )


def get_answered_records(sessions):
    """
    전달된 세션 목록에 속한 실제 풀이 기록 QuerySet을 반환한다.

    selected_no가 없는 row는 출제만 되고 답을 고르지 않은 문제이므로
    분석 저장의 전체 수, 정답 수, 오답 수에서 제외한다.
    """
    return SolveRecords.objects.filter(
        session__in=sessions,
        selected_no__isnull=False,
    )


def get_week_range(today=None):
    """
    월요일 시작, 일요일 종료 기준의 주간 분석 기간을 계산한다.

    today가 없으면 Django timezone 기준 오늘 날짜를 사용한다.
    """
    base_date = today or timezone.localdate()
    week_start = base_date - timedelta(days=base_date.weekday())
    week_end = week_start + timedelta(days=6)
    return week_start, week_end


def get_month_range(today=None):
    """
    기준 날짜가 속한 달의 1일부터 말일까지를 계산한다.

    12월인 경우 다음 해 1월 1일, 그 외에는 다음 달 1일을 기준으로 말일을 구한다.
    """
    base_date = today or timezone.localdate()
    month_start = date(base_date.year, base_date.month, 1)
    if base_date.month == 12:
        next_month_start = date(base_date.year + 1, 1, 1)
    elif base_date.month < 12:
        next_month_start = date(base_date.year, base_date.month + 1, 1)

    return month_start, next_month_start - timedelta(days=1)


def calculate_rate(count, total):
    """
    count / total 비율을 0~1 사이 소수 4자리 값으로 계산한다.

    total이 0이면 안전하게 0.0을 반환한다.
    """
    if total:
        return round(count / total, 4)

    return 0.0


def milliseconds_to_seconds(value):
    """
    밀리초 단위 풀이 시간을 초 단위 정수로 변환한다.

    solve_records.time_spent_ms 집계 결과를 analytics.avg_time_sec에 저장한다.
    """
    if value is None:
        return None

    return int(round(value / 1000))
