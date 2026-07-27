from django.urls import path
from . import views

app_name = "analytics"

urlpatterns = [
    path("mypage/", views.mypage, name="mypage"),
    path("weekly-report/", views.weekly_report, name="weekly_report"),
    path("wrong-rate-detail/", views.wrong_rate_detail, name="wrong_rate_detail"),
    path(
        "wrong-rate-detail/item-sessions/",
        views.wrong_rate_item_sessions,
        name="wrong_rate_item_sessions",
    ),
    path(
        "wrong-rate-detail/session/",
        views.wrong_rate_session_detail,
        name="wrong_rate_session_detail",
    ),
    path(
        "wrong-rate-detail/session/questions/",
        views.wrong_rate_session_item_questions,
        name="wrong_rate_session_item_questions",
    ),
    path(
        "wrong-rate-detail/period/",
        views.wrong_rate_period_detail,
        name="wrong_rate_period_detail",
    ),
    path(
        "wrong-rate-detail/period/questions/",
        views.wrong_rate_period_item_questions,
        name="wrong_rate_period_item_questions",
    ),
    path("study-plan/create/", views.create_study_plan_view, name="study_plan_create"),
    path(
        "study-plan/sync/",
        views.synchronize_study_plan_view,
        name="study_plan_sync",
    ),
]
