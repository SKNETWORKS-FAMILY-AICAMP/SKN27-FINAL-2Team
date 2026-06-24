from django.contrib.auth.decorators import login_required
from django.db.models import Count, Q
from django.shortcuts import render

from analytics.service.analytics import analytics_summary
from analytics.service.studyplan import get_study_plan_info
from question.models import SolveRecords



@login_required
def mypage(request):
    user_id = request.user.user_id

    analytics = analytics_summary(user_id)
    study_plan = get_study_plan_info(user_id)

    return render(
        request,
        "analytics/mypage.html",
        {
            "user": request.user,
            "analytics": analytics,
            "study_plan": study_plan,
        },
    )

@login_required
def wrong_rate_detail(request):
    era_stats = _build_wrong_rate_group(
        request.user,
        "era",
        ["선사", "삼국", "고려", "조선", "근대", "현대"],
    )
    type_stats = _build_wrong_rate_group(
        request.user,
        "q_type",
        ["연표", "사료", "개념", "인물", "지역"],
    )
    topic_stats = _build_wrong_rate_group(
        request.user,
        "topic",
        ["정치", "경제", "사회", "문화", "외교"],
    )

    return render(
        request,
        "analytics/wrong_rate_detail.html",
        {
            "era_stats": era_stats,
            "type_stats": type_stats,
            "topic_stats": topic_stats,
        },
    )

def _build_wrong_rate_group(user, field, labels):
    unclassified_label = "미분류"
    weak_rate_threshold = 20
    rows = (
        SolveRecords.objects.filter(session__user=user)
        .values(field)
        .annotate(
            total=Count("record_id"),
            wrong=Count("record_id", filter=Q(is_correct=False)),
        )
    )
    row_map = {
        (row[field] or unclassified_label): {
            "total": row["total"] or 0,
            "wrong": row["wrong"] or 0,
        }
        for row in rows
    }

    stat_labels = list(labels)
    for label in row_map:
        if label not in stat_labels:
            stat_labels.append(label)

    stats = []
    for label in stat_labels:
        item = row_map.get(label, {"total": 0, "wrong": 0})
        total = item["total"]
        wrong = item["wrong"]
        rate = 0
        if total:
            rate = round((wrong / total) * 100)

        if not total:
            status_label = "데이터 부족"
            status_class = "empty"
        elif rate >= weak_rate_threshold:
            status_label = "취약"
            status_class = "weak"
        elif rate < weak_rate_threshold:
            status_label = "안정"
            status_class = "stable"

        stats.append(
            {
                "label": label,
                "total": total,
                "wrong": wrong,
                "rate": max(0, min(100, rate)),
                "status_label": status_label,
                "status_class": status_class,
            }
        )

    return stats
