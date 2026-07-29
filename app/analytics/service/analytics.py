from datetime import date, timedelta

from django.db import DatabaseError
from django.db.models import Avg, Count, Q, Sum
from django.utils import timezone
from analytics.models import StudyPlanMypage
from analytics.serializers import parse_study_plan_items
from analytics.service.classification import should_normalize_classification
from analytics.service.taxonomy import (
    get_display_label as get_taxonomy_display_label,
    get_unclassified_label as get_taxonomy_unclassified_label,
)
from analytics.service.weakness import (
    build_group_key_id,
    build_weakness_rows,
)
from question.models import QuestionOptions, SolveRecords, SolveSessions


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
            .order_by("session__recorded_date", "session_id")
        )
    except DatabaseError:
        return []

    session_display_map = get_session_display_map(user_id)
    bars = []
    for row in rows:
        recorded_date = row["session__recorded_date"]
        label = format_wrong_rate_session_chart_date(recorded_date)
        description = format_session_chart_description(
            session_display_map.get(row["session_id"], {}).get("title", "풀이 기록"),
        )
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
    if period_start.year != period_end.year:
        # 연도를 넘는 기간은 끝 날짜 연도를 생략하면 안 된다.
        return f"{period_start.strftime('%Y.%m.%d')} - {period_end.strftime('%Y.%m.%d')}"

    return f"{period_start.strftime('%Y.%m.%d')} - {period_end.strftime('%m.%d')}"


def format_seconds(seconds):
    """
    초 단위 시간을 MM:SS 문자열로 변환한다.
    """
    if seconds is None:
        return "00:00"

    total_seconds = max(0, int(round(seconds)))
    minutes, remaining_seconds = divmod(total_seconds, 60)
    return f"{minutes:02d}:{remaining_seconds:02d}"


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
        # 정수 %로 반올림된 값끼리 빼면 ±1%p 오차가 생기므로,
        # 원본 비율로 차이를 구한 뒤 한 번만 반올림한다.
        diagnosis_exact_rate = (
            diagnosis_summary["correctCount"]
            / diagnosis_summary["totalQuestionCount"]
            * 100
        )
        current_exact_rate = (
            post_diagnosis_summary["correctCount"]
            / post_diagnosis_summary["totalQuestionCount"]
            * 100
        )
        answer_rate_change = round(current_exact_rate - diagnosis_exact_rate)
        if (
            diagnosis_summary["averageQuestionTimeMs"] is not None
            and post_diagnosis_summary["averageQuestionTimeMs"] is not None
        ):
            average_question_time_change_sec = int(
                round(
                    (
                        post_diagnosis_summary["averageQuestionTimeMs"]
                        - diagnosis_summary["averageQuestionTimeMs"]
                    )
                    / 1000
                )
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
        "correctCount": correct_count,
        "solvedCount": solved_count,
        "totalQuestionCount": total_count,
        "averageQuestionTimeSec": ms_to_sec(record_stats["average_time_ms"]),
        # 반올림 전 원본 값. 변화량 계산이 반올림된 값끼리 빼며 생기는
        # ±1 오차를 피하려면 이 값으로 한 번만 반올림해야 한다.
        "averageQuestionTimeMs": record_stats["average_time_ms"],
        "averageSessionTimeSec": None,
        "hasRecords": total_count > 0,
    }


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
    session_display_map = get_session_display_map(user)
    return {
        "categoryLabel": category_config["label"],
        "itemLabel": display_label,
        "title": f"{category_config['label']} · {display_label}",
        "hasRecords": bool(rows),
        "sessions": [
            build_wrong_rate_session_detail(row, session_display_map)
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
    session_display_title = get_session_display_map(user).get(
        session.session_id,
        {},
    ).get("title", session_type_label)
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
        "title": session_display_title,
        "overview": {
            **build_session_analysis_overview(session, records, session_type_label),
            "sessionDisplayTitle": session_display_title,
        },
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
    session_display_title = get_session_display_map(user).get(
        session.session_id,
        {},
    ).get("title", session_type_label)
    return {
        "categoryLabel": category_config["label"],
        "itemLabel": display_label,
        "sessionLabel": session_display_title,
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
                start_row["wrong_count"],
                start_row["total_count"],
                end_row["wrong_count"],
                end_row["total_count"],
                lower_is_better=True,
            ),
            build_percent_change_metric(
                "정답률",
                start_row["correct_count"],
                start_row["total_count"],
                end_row["correct_count"],
                end_row["total_count"],
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


def build_percent_change_metric(
    label,
    start_count,
    start_total,
    end_count,
    end_total,
    lower_is_better,
):
    """
    퍼센트 기반 변화량 표시 데이터를 만든다.

    시작/끝 표시값은 반올림된 정수 %지만, 변화량은 반올림된 값끼리 빼면
    ±1%p 오차가 생기므로 원본 비율 차이를 구한 뒤 한 번만 반올림한다.
    """
    start_exact_rate = 0.0
    if start_total:
        start_exact_rate = (start_count or 0) / start_total * 100
    end_exact_rate = 0.0
    if end_total:
        end_exact_rate = (end_count or 0) / end_total * 100

    change_value = round(end_exact_rate - start_exact_rate)
    tone = get_change_tone(change_value, lower_is_better)
    return {
        "label": label,
        "startValue": f"{calculate_percent_rate(start_count, start_total)}%",
        "endValue": f"{calculate_percent_rate(end_count, end_total)}%",
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


def get_session_display_map(user):
    """Reuse the session names shown on the solved-problems page."""
    from user.views import build_session_display_map

    user_id = getattr(user, "user_id", user)
    sessions = list(
        SolveSessions.objects.filter(user_id=user_id)
        .order_by("recorded_date", "session_id")
    )
    return build_session_display_map(user_id, sessions)


def build_wrong_rate_session_detail(row, session_display_map):
    """
    세션별 통계 row를 팝업 표시용 dict로 변환한다.
    """
    total_count = row["total_count"] or 0
    correct_count = row["correct_count"] or 0
    wrong_count = row["wrong_count"] or 0
    recorded_date = row["session__recorded_date"]
    session_type_label = format_session_type_label(row["session__session_type"])
    session_display_title = session_display_map.get(
        row["session_id"],
        {},
    ).get("title", session_type_label)
    return {
        "sessionId": row["session_id"],
        "title": session_display_title,
        "sessionTypeLabel": session_type_label,
        "sessionDisplayTitle": session_display_title,
        "recordedDate": format_wrong_rate_session_date(recorded_date),
        "totalCount": total_count,
        "solvedCount": row["solved_count"] or 0,
        "correctCount": correct_count,
        "wrongCount": wrong_count,
        "answerRate": calculate_percent_rate(correct_count, total_count),
        "wrongRate": calculate_percent_rate(wrong_count, total_count),
        "averageTimeLabel": format_seconds(ms_to_sec(row["average_time_ms"])),
    }


def format_wrong_rate_session_chart_date(recorded_date):
    """
    세션별 오답률 차트의 x축 날짜 라벨을 만든다.
    """
    if recorded_date:
        return recorded_date.strftime("%m.%d")

    return "-"


def format_session_chart_description(title):
    """Place a session round on its own chart-label line."""
    label, separator, round_label = str(title or "").rpartition(" ")
    if separator and round_label.endswith("회차"):
        return f"{label}\n{round_label}"
    return str(title or "풀이 기록")


def format_wrong_rate_session_date(recorded_date):
    """
    세션 날짜를 YYYY.MM.DD 형식으로 반환한다.
    """
    if recorded_date:
        return recorded_date.strftime("%Y.%m.%d")

    return "-"


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


def get_group_average_time_from_summary(summary):
    if summary["timeCount"]:
        return ms_to_sec(summary["totalTimeMs"] / summary["timeCount"])

    return None


def get_classification_display_label(field_name, value):
    return get_taxonomy_display_label(field_name, value)


def get_unclassified_label():
    """
    분류값이 비어 있을 때 사용할 기본 라벨을 반환한다.

    era, q_type, topic 값이 없을 때 화면에 빈 문자열이 노출되지 않게 한다.
    """
    return get_taxonomy_unclassified_label()


def calculate_percent_rate(count, total):
    """
    count / total 비율을 화면 표시용 0~100 정수 퍼센트로 계산한다.

    반올림 후에도 안전하게 0 이상 100 이하 값으로 제한한다.
    """
    if not total:
        return 0

    rate = round((count / total) * 100)
    return max(0, min(100, rate))


def ms_to_sec(value):
    """
    밀리초 단위 풀이 시간을 초 단위 정수로 변환한다.

    값이 없으면 None을 유지해 화면에서 기록 없음 상태를 구분할 수 있게 한다.
    """
    if value is None:
        return None

    return int(round(value / 1000))


