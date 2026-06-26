import json
from datetime import date

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from analytics.service.analytics import (
    analytics_summary,
    get_analysis_scope_chart_data,
    get_wrong_rate_item_session_details,
    get_wrong_rate_session_analysis_detail,
    get_wrong_rate_group_stats,
)
from analytics.service.display import (
    build_planner_summary,
    build_wrong_rate_display,
    build_wrong_rate_donut_summary,
)
from analytics.service.mypage import (
    build_d_day_label,
    build_diagnosis_comparison_summary,
    build_learning_summary,
    build_weakness_summary,
    build_wrong_type_summary,
)
from analytics.service.studyplan import (
    complete_study_plan_block,
    create_study_plan,
    delete_study_plan_block,
    get_previous_study_plan_info,
    get_study_plan_info,
    move_study_plan_blocks,
)


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
        "previous_study_plans": get_previous_study_plan_info(user_id),
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
    era_display = build_wrong_rate_display(era_stats)
    type_display = build_wrong_rate_display(type_stats)
    topic_display = build_wrong_rate_display(topic_stats)
    context = {
        "analysis_scope_chart_data": get_analysis_scope_chart_data(request.user.user_id),
        "era_stats": era_display,
        "type_stats": type_display,
        "topic_stats": topic_display,
        "era_donut": build_wrong_rate_donut_summary(era_display),
        "type_donut": build_wrong_rate_donut_summary(type_display),
        "topic_donut": build_wrong_rate_donut_summary(topic_display),
    }

    return render(
        request,
        "analytics/wrong_rate_detail.html",
        context,
    )


@login_required
def wrong_rate_item_sessions(request):
    category = request.GET.get("category", "")
    label = request.GET.get("label", "")
    detail_data = get_wrong_rate_item_session_details(request.user, category, label)
    if detail_data is None:
        return JsonResponse({"ok": False}, status=400)

    return JsonResponse({"ok": True, "detail": detail_data})


@login_required
def wrong_rate_session_detail(request):
    session_id = request.GET.get("sessionId")
    detail_data = get_wrong_rate_session_analysis_detail(request.user, session_id)
    if detail_data is None:
        return JsonResponse({"ok": False}, status=404)

    return JsonResponse({"ok": True, "detail": detail_data})


@login_required
@require_POST
def create_study_plan_view(request):
    create_study_plan(request.user.user_id)
    return redirect("analytics:mypage")


@login_required
@require_POST
def delete_study_plan_block_view(request):
    data = get_json_request_data(request)
    try:
        study_plan_id = int(data.get("studyPlanId"))
        day_index = int(data.get("dayIndex"))
        block_index = int(data.get("blockIndex"))
    except (TypeError, ValueError):
        return JsonResponse({"ok": False}, status=400)

    deleted_plan = delete_study_plan_block(
        request.user.user_id,
        study_plan_id,
        day_index,
        block_index,
    )
    if deleted_plan is None:
        return JsonResponse({"ok": False}, status=404)

    return JsonResponse({"ok": True})


@login_required
@require_POST
def complete_study_plan_block_view(request):
    data = get_json_request_data(request)
    try:
        study_plan_id = int(data.get("studyPlanId"))
        day_index = int(data.get("dayIndex"))
        block_index = int(data.get("blockIndex"))
        is_completed = bool(data.get("isCompleted", True))
    except (TypeError, ValueError):
        return JsonResponse({"ok": False}, status=400)

    completed_plan = complete_study_plan_block(
        request.user.user_id,
        study_plan_id,
        day_index,
        block_index,
        is_completed,
    )
    if completed_plan is None:
        return JsonResponse({"ok": False}, status=404)

    return JsonResponse({"ok": True})


@login_required
@require_POST
def move_study_plan_blocks_view(request):
    data = get_json_request_data(request)
    move_items = data.get("items") or []
    target_date = data.get("targetDate")
    if not move_items or not target_date:
        return JsonResponse({"ok": False}, status=400)

    try:
        target_date_key = date.fromisoformat(target_date[:10]).isoformat()
        normalized_items = [
            {
                "studyPlanId": int(item["studyPlanId"]),
                "dayIndex": int(item["dayIndex"]),
                "blockIndex": int(item["blockIndex"]),
            }
            for item in move_items
        ]
    except (KeyError, TypeError, ValueError):
        return JsonResponse({"ok": False}, status=400)

    updated_plans = move_study_plan_blocks(
        request.user.user_id,
        normalized_items,
        target_date_key,
    )
    if not updated_plans:
        return JsonResponse({"ok": False}, status=404)

    return JsonResponse({"ok": True})


def get_json_request_data(request):
    if not request.body:
        return {}

    try:
        return json.loads(request.body.decode("utf-8"))
    except json.JSONDecodeError:
        return {}
