from datetime import date, timedelta
from uuid import uuid4

from django.db.models import Avg, Count, Q
from django.utils import timezone

from question.models import SolveRecords, SolveSessions


def get_snapshot_config():
    """
    분석 snapshot에서 사용하는 scope, unit, 표시명을 반환한다.

    여러 함수에서 같은 문자열을 반복하지 않도록 한곳에서 관리하며,
    모델 담당자가 컬럼/선택값을 맞출 때 기준으로 사용할 수 있다.
    """
    return {
        "completed_status": "completed",
        "scope_session": "session",
        "scope_weekly": "weekly",
        "scope_monthly": "monthly",
        "scope_total": "total",
        "scope_study_plan_base": "study_plan_base",
        "unit_overall": "overall",
        "unit_era": "era",
        "unit_type": "type",
        "unit_topic": "topic",
        "unit_era_topic": "era_topic",
        "unit_question": "question",
        "classification_overall": "전체",
        "classification_era": "시대",
        "classification_type": "유형",
        "classification_topic": "주제",
        "classification_era_topic": "시대+주제",
        "classification_question": "문제",
        "unclassified_label": "미분류",
        "overall_label": "전체",
    }


def create_session_snapshot(session_id):
    """
    특정 completed 세션 하나를 기준으로 분석 snapshot을 생성한다.

    시험/문제 제출 API에서 세션 저장이 끝난 직후 호출할 함수이며,
    session scope로 전체/시대/유형/주제/문제별 item을 남긴다.
    """
    config = get_snapshot_config()
    session = SolveSessions.objects.filter(
        session_id=session_id,
        status=config["completed_status"],
    ).first()
    if session is None:
        return None

    records = SolveRecords.objects.filter(session=session)
    return create_snapshot_from_records(
        user_id=session.user_id,
        analysis_scope=config["scope_session"],
        records=records,
        session_id=session.session_id,
        latest_session_id=session.session_id,
    )


def create_study_plan_base_snapshot(user_id):
    """
    학습계획 생성 근거로 사용할 사용자 전체 기준 snapshot을 생성한다.

    특정 세션 하나가 아니라 사용자의 completed 전체 기록을 기준으로 하며,
    나중에 학습계획이 어떤 분석 상태에서 만들어졌는지 추적하는 용도다.
    """
    config = get_snapshot_config()
    sessions = get_completed_sessions(user_id)
    records = SolveRecords.objects.filter(session__in=sessions)
    latest_session_id = get_latest_session_id(sessions)
    return create_snapshot_from_records(
        user_id=user_id,
        analysis_scope=config["scope_study_plan_base"],
        records=records,
        latest_session_id=latest_session_id,
    )


def create_total_snapshot(user_id, force=False):
    """
    사용자의 completed 전체 풀이 기록 기준 누적 분석 snapshot을 생성한다.

    force가 False이고 최신 completed session_id까지 반영된 snapshot이 있으면
    새로 만들지 않고 기존 최신 snapshot을 재사용한다.
    """
    config = get_snapshot_config()
    sessions = get_completed_sessions(user_id)
    latest_session_id = get_latest_session_id(sessions)
    if not force and can_reuse_latest_snapshot(
        user_id=user_id,
        analysis_scope=config["scope_total"],
        latest_session_id=latest_session_id,
    ):
        return get_latest_snapshot(user_id, config["scope_total"])

    records = SolveRecords.objects.filter(session__in=sessions)
    return create_snapshot_from_records(
        user_id=user_id,
        analysis_scope=config["scope_total"],
        records=records,
        latest_session_id=latest_session_id,
    )


def create_weekly_snapshot(user_id, today=None, force=False):
    """
    이번 주 완료 풀이 기록 기준 weekly snapshot을 생성한다.

    주간 범위는 월요일부터 일요일까지이며,
    force가 False이면 동일 기간과 최신 세션 기준의 snapshot을 재사용한다.
    """
    config = get_snapshot_config()
    period_start, period_end = get_week_range(today)
    sessions = get_completed_sessions(user_id).filter(
        recorded_date__gte=period_start,
        recorded_date__lte=period_end,
    )
    latest_session_id = get_latest_session_id(get_completed_sessions(user_id))
    if not force and can_reuse_latest_snapshot(
        user_id=user_id,
        analysis_scope=config["scope_weekly"],
        latest_session_id=latest_session_id,
        period_start=period_start,
        period_end=period_end,
    ):
        return get_latest_snapshot(
            user_id,
            config["scope_weekly"],
            period_start,
            period_end,
        )

    records = SolveRecords.objects.filter(session__in=sessions)
    return create_snapshot_from_records(
        user_id=user_id,
        analysis_scope=config["scope_weekly"],
        records=records,
        latest_session_id=latest_session_id,
        period_start=period_start,
        period_end=period_end,
    )


def create_monthly_snapshot(user_id, today=None, force=False):
    """
    이번 달 완료 풀이 기록 기준 monthly snapshot을 생성한다.

    월간 범위는 해당 월 1일부터 말일까지이며,
    동일 기간과 최신 세션 기준 snapshot이 있으면 재사용할 수 있다.
    """
    config = get_snapshot_config()
    period_start, period_end = get_month_range(today)
    sessions = get_completed_sessions(user_id).filter(
        recorded_date__gte=period_start,
        recorded_date__lte=period_end,
    )
    latest_session_id = get_latest_session_id(get_completed_sessions(user_id))
    if not force and can_reuse_latest_snapshot(
        user_id=user_id,
        analysis_scope=config["scope_monthly"],
        latest_session_id=latest_session_id,
        period_start=period_start,
        period_end=period_end,
    ):
        return get_latest_snapshot(
            user_id,
            config["scope_monthly"],
            period_start,
            period_end,
        )

    records = SolveRecords.objects.filter(session__in=sessions)
    return create_snapshot_from_records(
        user_id=user_id,
        analysis_scope=config["scope_monthly"],
        records=records,
        latest_session_id=latest_session_id,
        period_start=period_start,
        period_end=period_end,
    )


def create_login_analysis_snapshots(user_id, today=None):
    """
    로그인 직후 백그라운드 작업에서 호출할 분석 갱신 진입점이다.

    사용자의 로그인 응답을 지연시키지 않는 곳에서 호출하고,
    주간/월간/전체 snapshot을 필요한 경우에만 생성한다.
    """
    return refresh_user_analysis_snapshots(user_id, today, force=False)


def refresh_user_analysis_snapshots(user_id, today=None, force=True):
    """
    분석 상세 페이지의 새로고침 요청에서 사용할 강제 재분석 함수다.

    weekly, monthly, total snapshot을 한 번에 생성하며,
    기본값은 force=True라 기존 snapshot이 있어도 새 분석 이력을 남긴다.
    """
    return {
        "weekly": create_weekly_snapshot(user_id, today, force),
        "monthly": create_monthly_snapshot(user_id, today, force),
        "total": create_total_snapshot(user_id, force),
    }


def create_snapshot_from_records(
    user_id,
    analysis_scope,
    records,
    session_id=None,
    latest_session_id=None,
    period_start=None,
    period_end=None,
):
    """
    분석 기준 QuerySet으로 snapshot 1건과 상세 item 여러 건을 저장한다.

    analysis_run_id로 같은 실행에서 생성된 item들을 묶고,
    period/session/latest_session 정보는 scope에 맞게 선택적으로 저장한다.
    """
    analytics_snapshot, analytics_snapshot_item = get_snapshot_models()
    snapshot = analytics_snapshot.objects.create(
        user_id=user_id,
        analysis_run_id=str(uuid4()),
        analysis_scope=analysis_scope,
        session_id=session_id,
        latest_session_id=latest_session_id,
        period_start=period_start,
        period_end=period_end,
        created_at=timezone.now(),
    )
    items = build_snapshot_items(snapshot, records, analytics_snapshot_item)
    if items:
        analytics_snapshot_item.objects.bulk_create(items)

    return snapshot


def build_snapshot_items(snapshot, records, analytics_snapshot_item):
    """
    하나의 snapshot에 들어갈 모든 분석 item을 생성한다.

    전체 요약, 시대별, 유형별, 주제별, 시대+주제별, 문제별 분석을
    동일한 item 구조로 만들어 bulk_create 대상 목록을 반환한다.
    """
    config = get_snapshot_config()
    items = [build_overall_item(snapshot, records, config, analytics_snapshot_item)]
    items.extend(
        build_group_items(
            snapshot=snapshot,
            records=records,
            field_name="era",
            analysis_unit=config["unit_era"],
            classification=config["classification_era"],
            config=config,
            analytics_snapshot_item=analytics_snapshot_item,
        )
    )
    items.extend(
        build_group_items(
            snapshot=snapshot,
            records=records,
            field_name="q_type",
            analysis_unit=config["unit_type"],
            classification=config["classification_type"],
            config=config,
            analytics_snapshot_item=analytics_snapshot_item,
        )
    )
    items.extend(
        build_group_items(
            snapshot=snapshot,
            records=records,
            field_name="topic",
            analysis_unit=config["unit_topic"],
            classification=config["classification_topic"],
            config=config,
            analytics_snapshot_item=analytics_snapshot_item,
        )
    )
    items.extend(build_era_topic_items(snapshot, records, config, analytics_snapshot_item))
    items.extend(build_question_items(snapshot, records, config, analytics_snapshot_item))
    return items


def build_overall_item(snapshot, records, config, analytics_snapshot_item):
    """
    전체 풀이 기록을 하나의 overall 분석 item으로 만든다.

    전체 풀이 수, 정답 수, 오답 수, 정답률, 오답률,
    평균 풀이시간을 한 row에 담기 위한 item 인스턴스를 반환한다.
    """
    stats = records.aggregate(
        total_count=Count("record_id"),
        correct_count=Count("record_id", filter=Q(is_correct=True)),
        average_time_ms=Avg("time_spent_ms"),
    )
    return build_snapshot_item(
        analytics_snapshot_item=analytics_snapshot_item,
        snapshot=snapshot,
        analysis_unit=config["unit_overall"],
        classification=config["classification_overall"],
        key_concept=config["overall_label"],
        total_count=stats["total_count"] or 0,
        correct_count=stats["correct_count"] or 0,
        average_time_ms=stats["average_time_ms"],
    )


def build_group_items(
    snapshot,
    records,
    field_name,
    analysis_unit,
    classification,
    config,
    analytics_snapshot_item,
):
    """
    단일 컬럼 기준의 분류별 분석 item 목록을 만든다.

    era, q_type, topic처럼 하나의 필드로 group by 가능한 분석에 사용하며,
    각 그룹의 풀이 수, 정답 수, 오답률, 평균 풀이시간을 계산한다.
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

    items = []
    for row in rows:
        items.append(
            build_snapshot_item(
                analytics_snapshot_item=analytics_snapshot_item,
                snapshot=snapshot,
                analysis_unit=analysis_unit,
                classification=classification,
                key_concept=row[field_name] or config["unclassified_label"],
                total_count=row["total_count"] or 0,
                correct_count=row["correct_count"] or 0,
                average_time_ms=row["average_time_ms"],
            )
        )

    return items


def build_era_topic_items(snapshot, records, config, analytics_snapshot_item):
    """
    시대와 주제 조합 기준의 분석 item 목록을 만든다.

    추천 학습 대상이나 세부 취약점 설명에 활용할 수 있도록
    era/topic 두 컬럼을 함께 묶어 집계한다.
    """
    rows = (
        records.values("era", "topic")
        .annotate(
            total_count=Count("record_id"),
            correct_count=Count("record_id", filter=Q(is_correct=True)),
            average_time_ms=Avg("time_spent_ms"),
        )
        .order_by("era", "topic")
    )

    items = []
    for row in rows:
        era = row["era"] or config["unclassified_label"]
        topic = row["topic"] or config["unclassified_label"]
        items.append(
            build_snapshot_item(
                analytics_snapshot_item=analytics_snapshot_item,
                snapshot=snapshot,
                analysis_unit=config["unit_era_topic"],
                classification=config["classification_era_topic"],
                key_concept=f"{era} / {topic}",
                total_count=row["total_count"] or 0,
                correct_count=row["correct_count"] or 0,
                average_time_ms=row["average_time_ms"],
            )
        )

    return items


def build_question_items(snapshot, records, config, analytics_snapshot_item):
    """
    문제 ID 기준의 분석 item 목록을 만든다.

    같은 문제가 여러 번 풀린 경우 누적 정답률과 평균 풀이시간을 남겨
    문제 단위 취약도나 반복 오답 분석에 사용할 수 있게 한다.
    """
    rows = (
        records.values("question_id")
        .annotate(
            total_count=Count("record_id"),
            correct_count=Count("record_id", filter=Q(is_correct=True)),
            average_time_ms=Avg("time_spent_ms"),
        )
        .order_by("question_id")
    )

    items = []
    for row in rows:
        question_id = row["question_id"]
        items.append(
            build_snapshot_item(
                analytics_snapshot_item=analytics_snapshot_item,
                snapshot=snapshot,
                analysis_unit=config["unit_question"],
                classification=config["classification_question"],
                key_concept=str(question_id),
                total_count=row["total_count"] or 0,
                correct_count=row["correct_count"] or 0,
                average_time_ms=row["average_time_ms"],
                question_id=question_id,
            )
        )

    return items


def build_snapshot_item(
    analytics_snapshot_item,
    snapshot,
    analysis_unit,
    classification,
    key_concept,
    total_count,
    correct_count,
    average_time_ms,
    question_id=None,
):
    """
    공통 집계값을 AnalyticsSnapshotItem 모델 인스턴스로 변환한다.

    total/correct/average_time 값을 받아 wrong_count, answer_rate,
    wrong_rate, avg_time_sec를 계산해 저장 가능한 객체로 만든다.
    """
    wrong_count = total_count - correct_count
    return analytics_snapshot_item(
        snapshot=snapshot,
        analysis_unit=analysis_unit,
        classification=classification,
        key_concept=key_concept,
        question_id=question_id,
        total_count=total_count,
        correct_count=correct_count,
        wrong_count=wrong_count,
        answer_rate=calculate_rate(correct_count, total_count),
        wrong_rate=calculate_rate(wrong_count, total_count),
        avg_time_sec=milliseconds_to_seconds(average_time_ms),
    )


def get_snapshot_models():
    """
    snapshot 저장에 필요한 모델 클래스를 가져온다.

    models.py 담당자가 AnalyticsSnapshot과 AnalyticsSnapshotItem을 추가한 뒤
    실제 호출 시점에 import되도록 지연 import를 사용한다.
    """
    from analytics.models import AnalyticsSnapshot, AnalyticsSnapshotItem

    return AnalyticsSnapshot, AnalyticsSnapshotItem


def get_completed_sessions(user_id):
    """
    분석 대상으로 사용할 사용자의 completed 세션 QuerySet을 반환한다.

    session, weekly, monthly, total snapshot 생성 함수에서 공통으로 사용한다.
    """
    config = get_snapshot_config()
    return SolveSessions.objects.filter(
        user_id=user_id,
        status=config["completed_status"],
    )


def get_latest_session_id(sessions):
    """
    주어진 세션 QuerySet에서 가장 최신 session_id를 반환한다.

    snapshot이 어디까지 반영했는지 기록하고,
    중복 분석 생성을 줄이는 기준값으로 사용한다.
    """
    return sessions.order_by("-session_id").values_list("session_id", flat=True).first()


def get_latest_snapshot(user_id, analysis_scope, period_start=None, period_end=None):
    """
    사용자와 scope, 선택적 기간 조건에 맞는 최신 snapshot을 조회한다.

    화면 표시용 최신 분석을 가져오거나,
    새 snapshot 생성 전에 재사용 가능 여부를 판단할 때 사용한다.
    """
    analytics_snapshot, _ = get_snapshot_models()
    snapshots = analytics_snapshot.objects.filter(
        user_id=user_id,
        analysis_scope=analysis_scope,
    )
    if period_start is not None:
        snapshots = snapshots.filter(period_start=period_start)
    if period_end is not None:
        snapshots = snapshots.filter(period_end=period_end)

    return snapshots.order_by("-created_at", "-snapshot_id").first()


def can_reuse_latest_snapshot(
    user_id,
    analysis_scope,
    latest_session_id,
    period_start=None,
    period_end=None,
):
    """
    기존 최신 snapshot을 재사용할 수 있는지 판단한다.

    같은 사용자, 같은 scope, 같은 기간 조건에서
    latest_session_id가 현재 기준과 같으면 이미 최신으로 본다.
    """
    snapshot = get_latest_snapshot(user_id, analysis_scope, period_start, period_end)
    if snapshot is None:
        return False
    if snapshot.latest_session_id != latest_session_id:
        return False

    return True


def get_week_range(today=None):
    """
    월요일 시작, 일요일 종료 기준의 주간 분석 기간을 계산한다.

    today를 넘기면 테스트나 특정 기준일 분석에 사용할 수 있고,
    넘기지 않으면 Django timezone 기준 오늘 날짜를 사용한다.
    """
    base_date = today or timezone.localdate()
    week_start = base_date - timedelta(days=base_date.weekday())
    week_end = week_start + timedelta(days=6)
    return week_start, week_end


def get_month_range(today=None):
    """
    기준 날짜가 속한 달의 1일부터 말일까지를 계산한다.

    12월인 경우 다음 해 1월 1일을 기준으로 말일을 구하고,
    그 외에는 다음 달 1일에서 하루를 빼서 월말을 구한다.
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

    정답률과 오답률 저장에 공통으로 사용하며,
    total이 0이면 안전하게 0.0을 반환한다.
    """
    if total:
        return round(count / total, 4)

    return 0.0


def milliseconds_to_seconds(value):
    """
    밀리초 단위 풀이 시간을 초 단위 정수로 변환한다.

    solve_records.time_spent_ms 집계 결과를 snapshot의 avg_time_sec에
    저장하기 위한 변환 함수다.
    """
    if value is None:
        return None

    return int(round(value / 1000))
