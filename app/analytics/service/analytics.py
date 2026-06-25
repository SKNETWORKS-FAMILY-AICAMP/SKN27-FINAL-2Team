from django.db.models import Avg, Count, Q
from django.utils import timezone
from analytics.models import Analytics
from question.models import SolveRecords, SolveSessions


UNCLASSIFIED_LABEL = "미분류"
CLASSIFICATION_FIELDS = [
    ("시대", "era"),
    ("유형", "q_type"),
    ("주제", "topic"),
]


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
                response_key: row[field_name] or UNCLASSIFIED_LABEL,
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
                "era": row["era"] or UNCLASSIFIED_LABEL,
                "topic": row["topic"] or UNCLASSIFIED_LABEL,
                "reason": make_recommendation_reason(row),
                "priority": priority,
                "recommendedQuestionCount": row["wrong_count"] or 0,
            }
        )
    return targets


def make_recommendation_reason(row):
    return (
        f"{row['era'] or UNCLASSIFIED_LABEL} / "
        f"{row['topic'] or UNCLASSIFIED_LABEL}에서 "
        f"오답 {row['wrong_count'] or 0}건이 발생했습니다."
    )


def get_classification_fields():
    return CLASSIFICATION_FIELDS


def calculate_rate(count, total):
    if total:
        return round(count / total, 4)
    return 0.0


def round_float(value):
    if value is None:
        return 0.0
    return round(float(value), 4)


def round_int(value):
    if value is None:
        return None
    return int(round(value))


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
                if not total_count:
                    continue

                correct_count = row["correct_count"] or 0
                analytics_rows.append(
                    Analytics(
                        session=session,
                        key_concept=row[field_name] or UNCLASSIFIED_LABEL,
                        classification=classification,
                        avg_time_sec=ms_to_sec(row["average_time_ms"]),
                        topic_rate=calculate_rate(correct_count, total_count),
                        date=now,
                    )
                )

    if analytics_rows:
        Analytics.objects.bulk_create(analytics_rows)
    return True
