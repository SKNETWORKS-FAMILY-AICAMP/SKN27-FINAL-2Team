import json
import logging
from datetime import date

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from analytics.service.analytics import (
    analytics_summary,
    build_wrong_rate_detail_validation,
    get_analysis_scope_chart_data,
    get_plan_completion_chart_data,
    get_recent_wrong_rate_period,
    get_wrong_rate_item_session_details,
    get_wrong_rate_period_analysis_detail,
    get_wrong_rate_period_item_questions,
    get_wrong_rate_session_analysis_detail,
    get_wrong_rate_session_item_questions,
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
    build_mypage_summary_validation,
    build_weakness_summary,
    build_wrong_type_summary,
)
from analytics.service.studyplan import (
    StudyPlanBlockDeleteLimitExceeded,
    StudyPlanDateOutOfRange,
    StudyPlanExtraBlockCompletionRequired,
    StudyPlanExtraBlockUnavailable,
    add_extra_study_plan_block,
    complete_study_plan_block,
    create_study_plan,
    delete_study_plan_block,
    ensure_today_study_plan,
    get_study_plan_info,
)


@login_required
def mypage(request):
    user_id = request.user.user_id
    today = timezone.localdate()
    ensure_today_study_plan(user_id, today)
    study_plan = get_study_plan_info(user_id)
    planner_summary = build_planner_summary(study_plan, today)
    wrong_type_summary = build_wrong_type_summary(request.user, today)
    weakness_summary = build_weakness_summary(request.user, today)
    validation = build_mypage_summary_validation(
        request.user,
        today,
        weakness_summary,
        wrong_type_summary,
    )
    log_analytics_validation("mypage", validation)
    context = {
        "user": request.user,
        "analytics": analytics_summary(user_id),
        "study_plan": study_plan,
        "learning_summary": build_learning_summary(request.user),
        "diagnosis_comparison": build_diagnosis_comparison_summary(request.user),
        "wrong_type_summary": wrong_type_summary,
        "weakness_summary": weakness_summary,
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
    donut_period_days = 7
    today = timezone.localdate()
    donut_period = get_recent_wrong_rate_period(today, donut_period_days)
    era_stats = get_wrong_rate_group_stats(
        request.user,
        "era",
        donut_period["startDate"],
        donut_period["endDate"],
    )
    type_stats = get_wrong_rate_group_stats(
        request.user,
        "q_type",
        donut_period["startDate"],
        donut_period["endDate"],
    )
    topic_stats = get_wrong_rate_group_stats(
        request.user,
        "topic",
        donut_period["startDate"],
        donut_period["endDate"],
    )
    era_display = build_wrong_rate_display(era_stats)
    type_display = build_wrong_rate_display(type_stats)
    topic_display = build_wrong_rate_display(topic_stats)
    era_donut = build_wrong_rate_donut_summary(era_display)
    type_donut = build_wrong_rate_donut_summary(type_display)
    topic_donut = build_wrong_rate_donut_summary(topic_display)
    analysis_scope_chart_data = get_analysis_scope_chart_data(request.user.user_id)
    validation = build_wrong_rate_detail_validation(
        request.user.user_id,
        analysis_scope_chart_data,
    )
    log_analytics_validation("wrong_rate_detail", validation)
    context = {
        "analysis_scope_chart_data": analysis_scope_chart_data,
        "plan_completion_chart_data": get_plan_completion_chart_data(request.user.user_id),
        "donut_period": donut_period,
        "donut_total_wrong": era_donut["totalWrong"],
        "era_stats": era_display,
        "type_stats": type_display,
        "topic_stats": topic_display,
        "era_donut": era_donut,
        "type_donut": type_donut,
        "topic_donut": topic_donut,
    }

    return render(
        request,
        "analytics/wrong_rate_detail.html",
        context,
    )


def log_analytics_validation(page_name, validation):
    """
    임시 진단용 로그다.

    사용자 입력 검증이 아니라, 분석 화면들이 같은 사용자 기준 데이터를
    보고 있는지 확인하기 위한 로그다. 원인 확인 후 테스트로 대체하고
    제거할 수 있다.
    """
    if not validation.get("isValid", True):
        logging.getLogger(__name__).warning(
            "analytics validation failed page=%s payload=%s",
            page_name,
            validation,
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
def wrong_rate_session_item_questions(request):
    session_id = request.GET.get("sessionId")
    category = request.GET.get("category", "")
    label = request.GET.get("label", "")
    detail_data = get_wrong_rate_session_item_questions(
        request.user,
        session_id,
        category,
        label,
    )
    if detail_data is None:
        return JsonResponse({"ok": False}, status=404)

    return JsonResponse({"ok": True, "detail": detail_data})


@login_required
def wrong_rate_period_detail(request):
    start_date = get_query_date(request, "startDate")
    end_date = get_query_date(request, "endDate")
    detail_data = get_wrong_rate_period_analysis_detail(
        request.user,
        start_date,
        end_date,
    )
    if detail_data is None:
        return JsonResponse({"ok": False}, status=400)

    return JsonResponse({"ok": True, "detail": detail_data})


@login_required
def wrong_rate_period_item_questions(request):
    start_date = get_query_date(request, "startDate")
    end_date = get_query_date(request, "endDate")
    category = request.GET.get("category", "")
    label = request.GET.get("label", "")
    detail_data = get_wrong_rate_period_item_questions(
        request.user,
        start_date,
        end_date,
        category,
        label,
    )
    if detail_data is None:
        return JsonResponse({"ok": False}, status=400)

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

    try:
        deleted_plan = delete_study_plan_block(
            request.user.user_id,
            study_plan_id,
            day_index,
            block_index,
        )
    except StudyPlanBlockDeleteLimitExceeded:
        return JsonResponse(
            {
                "ok": False,
                "error": "하루에 삭제할 수 있는 학습계획은 최대 2개입니다.",
            },
            status=429,
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
def add_extra_study_plan_block_view(request):
    data = get_json_request_data(request)
    target_date = data.get("targetDate") or timezone.localdate().isoformat()
    try:
        target_date_key = date.fromisoformat(str(target_date)[:10]).isoformat()
    except (TypeError, ValueError):
        return JsonResponse({"ok": False}, status=400)

    try:
        updated_plan = add_extra_study_plan_block(
            request.user.user_id,
            target_date_key,
        )
    except StudyPlanDateOutOfRange:
        return JsonResponse(
            {
                "ok": False,
                "error": "추가학습은 오늘 계획에만 만들 수 있습니다.",
            },
            status=400,
        )
    except StudyPlanExtraBlockUnavailable:
        return JsonResponse(
            {
                "ok": False,
                "error": "추가학습을 만들 취약점 데이터가 부족합니다.",
            },
            status=400,
        )
    except StudyPlanExtraBlockCompletionRequired:
        return JsonResponse(
            {
                "ok": False,
                "error": "오늘의 학습 문제를 모두 푼 뒤 추가학습을 만들 수 있습니다.",
            },
            status=400,
        )

    if updated_plan is None:
        return JsonResponse({"ok": False}, status=404)

    return JsonResponse({"ok": True})


def get_json_request_data(request):
    if not request.body:
        return {}

    try:
        return json.loads(request.body.decode("utf-8"))
    except json.JSONDecodeError:
        return {}


def get_query_date(request, key):
    """
    GET 파라미터의 YYYY-MM-DD 값을 date로 변환한다.
    """
    raw_value = request.GET.get(key)
    if not raw_value:
        return None

    try:
        return date.fromisoformat(raw_value[:10])
    except ValueError:
        return None
