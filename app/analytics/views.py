import json
import logging
from datetime import date
from zoneinfo import ZoneInfo

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from analytics.service.analytics import (
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
    get_wrong_rate_weakness_rows,
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
    build_wrong_rate_summary,
    build_wrong_type_summary,
)
from analytics.service.studyplan import (
    StudyPlanGenerationUnavailable,
    create_study_plan,
    get_study_plan_info,
)
from analytics.service.study_plan.config import get_study_plan_config
from analytics.service.study_plan.service import (
    get_archived_study_plan_dtos,
    has_completed_diagnostic_session,
    synchronize_active_study_plan,
)
from analytics.service.weekly_report.next_plan import (
    NEXT_PLAN_SUCCEEDED,
    recheck_user_next_plan,
)
from analytics.service.weekly_report.repository import load_report
from analytics.service.weekly_report.service import render_report_dto


@login_required
def mypage(request):
    user_id = request.user.user_id
    today = timezone.localdate(
        timezone=ZoneInfo(get_study_plan_config().timezone),
    )
    study_plan = get_study_plan_info(user_id)
    if request.GET.get("format") == "json":
        active_plan = study_plan[0] if study_plan else None
        return JsonResponse(
            {"studyPlan": active_plan, "weeklyReport": get_weekly_report_dto(user_id)},
        )
    plan_generation_available = True
    has_completed_diagnosis = has_completed_diagnostic_session(user_id)
    planner_summary = build_planner_summary(
        study_plan,
        today,
        plan_generation_available,
        history_study_plans=get_archived_study_plan_dtos(user_id, today),
        has_completed_diagnosis=has_completed_diagnosis,
    )
    wrong_type_summary = build_wrong_type_summary(request.user, today)
    wrong_rate_summaries = [
        {"title": "유형별 오답률", **wrong_type_summary},
        {"title": "주제별 오답률", **build_wrong_rate_summary(request.user, "topic", today)},
        {"title": "시대별 오답률", **build_wrong_rate_summary(request.user, "era", today)},
    ]
    weakness_summary = build_weakness_summary(request.user, today)
    weekly_report = get_weekly_report_dto(user_id)
    context = {
        "weekly_report": weekly_report,
        "learning_summary": build_learning_summary(request.user, today),
        "diagnosis_comparison": build_diagnosis_comparison_summary(request.user),
        "wrong_type_summary": wrong_type_summary,
        "wrong_rate_summaries": wrong_rate_summaries,
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


def get_weekly_report_dto(user_id):
    """저장된 주간 리포트를 화면용 DTO 로 바꾼다. 없으면 None."""
    report = load_report(user_id)
    if report is None:
        return None

    return render_report_dto(report)


@login_required
def weekly_report(request):
    """마이페이지가 리포트 생성 완료를 확인할 때 쓰는 조회 엔드포인트."""
    report = get_weekly_report_dto(request.user.user_id)
    if report is None:
        return JsonResponse({"ok": True, "weeklyReport": None})

    return JsonResponse({"ok": True, "weeklyReport": report})


@login_required
def wrong_rate_detail(request):
    donut_period_days = 7
    today = timezone.localdate(
        timezone=ZoneInfo(get_study_plan_config().timezone),
    )
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
    era_weakness_rows = get_wrong_rate_weakness_rows(request.user, "era", today)
    type_weakness_rows = get_wrong_rate_weakness_rows(request.user, "q_type", today)
    topic_weakness_rows = get_wrong_rate_weakness_rows(request.user, "topic", today)
    era_display = build_wrong_rate_display(era_stats, era_weakness_rows)
    type_display = build_wrong_rate_display(type_stats, type_weakness_rows)
    topic_display = build_wrong_rate_display(topic_stats, topic_weakness_rows)
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
    data = get_json_request_data(request)
    source_study_plan_id = data.get("sourceStudyPlanId")
    if source_study_plan_id is None:
        # 폼 전송은 본문이 JSON 이 아니라 get_json_request_data 가 빈 값을 준다.
        source_study_plan_id = request.POST.get("sourceStudyPlanId")
    if source_study_plan_id is not None:
        try:
            source_study_plan_id = int(source_study_plan_id)
        except (TypeError, ValueError):
            return JsonResponse({"ok": False}, status=400)
    try:
        result = create_study_plan(
            request.user.user_id,
            source_study_plan_id=source_study_plan_id,
        )
    except StudyPlanGenerationUnavailable as error:
        if request.content_type == "application/json":
            return JsonResponse({"ok": False, "error": str(error)}, status=422)
        return redirect("analytics:mypage")

    if request.content_type == "application/json":
        return JsonResponse(
            {"ok": True, "weeklyReport": get_weekly_report_dto(request.user.user_id), **result},
        )
    return redirect("analytics:mypage")


@login_required
@require_POST
def synchronize_study_plan_view(request):
    data = get_json_request_data(request)
    try:
        study_plan_id = int(data.get("studyPlanId"))
    except (TypeError, ValueError):
        return JsonResponse({"ok": False}, status=400)

    # 보류·대기 중인 다음 계획을 먼저 재확인한다. 풀이가 끝난 뒤 마이페이지에
    # 들어오는 이 경로가 blocked 를 풀 수 있는 유일한 지점이다.
    next_plan_code = recheck_next_plan_safely(request.user.user_id)
    result = synchronize_active_study_plan(request.user.user_id, study_plan_id)
    if result is None:
        return JsonResponse({"ok": False}, status=404)
    if next_plan_code == NEXT_PLAN_SUCCEEDED:
        # 새 계획이 생겼으면 넘겨받은 옛 계획 번호 기준 동기화는 changed=False 다.
        # 화면이 새 계획을 다시 그리도록 changed 를 올려서 돌려준다.
        result = {**result, "changed": True}
    return JsonResponse(
        {"ok": True, "weeklyReport": get_weekly_report_dto(request.user.user_id), **result},
    )


def recheck_next_plan_safely(user_id: int) -> str | None:
    """다음 계획 재확인 실패가 동기화 응답을 깨면 안 되므로 예외를 삼킨다."""
    try:
        return recheck_user_next_plan(user_id)
    except Exception:
        logging.getLogger(__name__).exception(
            "다음 계획 재확인 실패 user=%s", user_id,
        )
        return None


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
