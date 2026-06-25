from datetime import timedelta

from django.db.models import Avg, Count, Q
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

    주간 범위는 월요일부터 일요일까지로 잡고,
    정답률, 풀이 수, 문제당 평균 풀이시간, 세션 평균 소요시간을 반환한다.
    """
    completed_status = "completed"
    practice_type = "practice"
    base_date = today or timezone.localdate()
    week_start = base_date - timedelta(days=base_date.weekday())
    next_week_start = week_start + timedelta(days=7)
    weekly_sessions = SolveSessions.objects.filter(
        user_id=user_id,
        status=completed_status,
        session_type=practice_type,
        recorded_date__gte=week_start,
        recorded_date__lt=next_week_start,
    )
    weekly_records = SolveRecords.objects.filter(
        session__in=weekly_sessions,
        selected_no__isnull=False,
    )
    record_stats = weekly_records.aggregate(
        total_count=Count("record_id"),
        correct_count=Count("record_id", filter=Q(is_correct=True)),
        average_time_ms=Avg("time_spent_ms"),
    )
    session_stats = weekly_sessions.aggregate(
        average_session_time_sec=Avg("elapsed_sec"),
    )
    total_count = record_stats["total_count"] or 0
    correct_count = record_stats["correct_count"] or 0
    average_session_time_sec = None
    if session_stats["average_session_time_sec"] is not None:
        average_session_time_sec = int(round(session_stats["average_session_time_sec"]))

    return {
        "answerRate": calculate_percent_rate(correct_count, total_count),
        "solvedCount": total_count,
        "averageQuestionTimeSec": ms_to_sec(record_stats["average_time_ms"]),
        "averageSessionTimeSec": average_session_time_sec,
        "hasRecords": total_count > 0,
        "weekStart": week_start,
        "weekEnd": next_week_start - timedelta(days=1),
    }


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
            "averageQuestionTimeSec": None,
            "hasRecords": False,
            "recordedDate": None,
        }

    records = SolveRecords.objects.filter(
        session=session,
        selected_no__isnull=False,
    )
    record_stats = records.aggregate(
        total_count=Count("record_id"),
        correct_count=Count("record_id", filter=Q(is_correct=True)),
        average_time_ms=Avg("time_spent_ms"),
    )
    total_count = record_stats["total_count"] or 0
    correct_count = record_stats["correct_count"] or 0

    return {
        "answerRate": calculate_percent_rate(correct_count, total_count),
        "solvedCount": total_count,
        "averageQuestionTimeSec": ms_to_sec(record_stats["average_time_ms"]),
        "hasRecords": total_count > 0,
        "recordedDate": session.recorded_date,
    }


def get_diagnosis_improvement_summary(user_id, today=None):
    """
    첫 진단평가와 이번 주 practice 결과의 개선 정도를 계산한다.

    두 기록이 모두 있을 때 정답률 차이와 평균 풀이시간 차이를 계산하고,
    비교가 불가능하면 변화값을 None으로 반환한다.
    """
    diagnosis_summary = get_first_diagnosis_summary(user_id)
    weekly_summary = get_weekly_practice_summary(user_id, today)
    has_comparison = diagnosis_summary["hasRecords"] and weekly_summary["hasRecords"]
    answer_rate_change = None
    average_question_time_change_sec = None
    if has_comparison:
        answer_rate_change = weekly_summary["answerRate"] - diagnosis_summary["answerRate"]
        if (
            diagnosis_summary["averageQuestionTimeSec"] is not None
            and weekly_summary["averageQuestionTimeSec"] is not None
        ):
            average_question_time_change_sec = (
                weekly_summary["averageQuestionTimeSec"]
                - diagnosis_summary["averageQuestionTimeSec"]
            )

    return {
        "diagnosis": diagnosis_summary,
        "current": weekly_summary,
        "answerRateChange": answer_rate_change,
        "averageQuestionTimeChangeSec": average_question_time_change_sec,
        "hasComparison": has_comparison,
    }


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
    사용자의 완료된 세션에 속한 실제 풀이 기록 QuerySet을 반환한다.

    solve_records는 문제 생성 시 전체 문항 row가 먼저 만들어지므로,
    selected_no가 있는 row만 사용해야 풀이 수와 오답률이 왜곡되지 않는다.
    """
    return SolveRecords.objects.filter(
        session__user_id=user_id,
        session__status="completed",
        selected_no__isnull=False,
    )


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
            selected_no__isnull=False,
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
