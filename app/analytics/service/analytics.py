from datetime import timedelta

from django.db.models import Avg, Count, Q
from django.utils import timezone
from analytics.models import Analytics
from question.models import SolveRecords, SolveSessions



def get_user_analytics(user_id):
    session_ids = SolveSessions.objects.filter(user_id=user_id).values_list(
        "session_id",
        flat=True,
    )
    return Analytics.objects.filter(session_id__in=session_ids)


def get_analytics_info(user_id):
    return analytics_summary(user_id)


def analytics_summary(user_id):
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
    completed_records = get_completed_records(user_id)
    return build_weak_targets(completed_records)


def get_weekly_practice_summary(user_id, today=None):
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
    weekly_records = SolveRecords.objects.filter(session__in=weekly_sessions)
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

    records = SolveRecords.objects.filter(session=session)
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
    return SolveSessions.objects.filter(
        user_id=user_id,
        status="completed",
    )


def get_completed_records(user_id):
    return SolveRecords.objects.filter(
        session__user_id=user_id,
        session__status="completed",
    )


def build_analytics_summary(sessions, records):
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
    era = row["era"] or get_unclassified_label()
    topic = row["topic"] or get_unclassified_label()
    wrong_count = row["wrong_count"] or 0
    return f"{era} / {topic}에서 오답 {wrong_count}건이 발생했습니다."


def get_classification_fields():
    return [
        ("시대", "era"),
        ("유형", "q_type"),
        ("주제", "topic"),
    ]


def get_unclassified_label():
    return "미분류"


def calculate_rate(count, total):
    if total:
        return round(count / total, 4)

    return 0.0


def calculate_percent_rate(count, total):
    if not total:
        return 0

    rate = round((count / total) * 100)
    return max(0, min(100, rate))


def round_float(value):
    if value is None:
        return 0.0

    return round(float(value), 4)


def ms_to_sec(value):
    if value is None:
        return None

    return int(round(value / 1000))


def cant_create_analytics(user_id):
    if get_completed_records(user_id).exists():
        return False

    return True


def create_analytics(user_id):
    sessions = get_completed_sessions(user_id)
    Analytics.objects.filter(session__in=sessions).delete()

    analytics_rows = []
    now = timezone.now()
    for session in sessions:
        records = SolveRecords.objects.filter(session=session)
        for classification, field_name in get_classification_fields():
            rows = records.values(field_name).annotate(
                total_count=Count("record_id"),
                correct_count=Count("record_id", filter=Q(is_correct=True)),
                average_time_ms=Avg("time_spent_ms"),
            )
            for row in rows:
                total_count = row["total_count"] or 0
                if total_count:
                    correct_count = row["correct_count"] or 0
                    analytics_rows.append(
                        Analytics(
                            session=session,
                            key_concept=row[field_name] or get_unclassified_label(),
                            classification=classification,
                            avg_time_sec=ms_to_sec(row["average_time_ms"]),
                            topic_rate=calculate_rate(correct_count, total_count),
                            date=now,
                        )
                    )

    if analytics_rows:
        Analytics.objects.bulk_create(analytics_rows)

    return True
