from django.contrib.auth.decorators import login_required
from django.shortcuts import render
from django.utils import timezone

from analytics.service.analytics import analytics_summary, get_wrong_rate_group_stats
from analytics.service.display import build_planner_summary, build_wrong_rate_display
from analytics.service.mypage import (
    build_d_day_label,
    build_diagnosis_comparison_summary,
    build_learning_summary,
    build_weakness_summary,
    build_wrong_type_summary,
)
from analytics.service.studyplan import get_study_plan_info


@login_required
def mypage(request):
    user_id = request.user.user_id
    today = timezone.localdate()
    study_plan = get_study_plan_info(user_id)
    planner_summary = build_planner_summary(study_plan, today)
    context = {
        "user": request.user,
        "analytics": analytics_summary(user_id),
        "study_plan": study_plan,
        "learning_summary": build_learning_summary(request.user),
        "diagnosis_comparison": build_diagnosis_comparison_summary(request.user),
        "wrong_type_summary": build_wrong_type_summary(request.user),
        "weakness_summary": build_weakness_summary(request.user),
        "d_day_label": build_d_day_label(request.user, today),
        "planner_summary": planner_summary,
        "planner_data": planner_summary["data"],
    }

    return render(
        request,
        "analytics/mypage.html",
        context,
    )


@login_required
def wrong_rate_detail(request):
    era_stats = get_wrong_rate_group_stats(request.user, "era")
    type_stats = get_wrong_rate_group_stats(request.user, "q_type")
    topic_stats = get_wrong_rate_group_stats(request.user, "topic")
    context = {
        "era_stats": build_wrong_rate_display(era_stats),
        "type_stats": build_wrong_rate_display(type_stats),
        "topic_stats": build_wrong_rate_display(topic_stats),
    }

    return render(
        request,
        "analytics/wrong_rate_detail.html",
        context,
    )
