from django.urls import path

from . import views

app_name = "diagnosis"

urlpatterns = [
    # 페이지 (템플릿)
    path("", views.diagnosis_intro, name="intro"),
    path("exam/", views.diagnosis_exam, name="exam"),
    path("result/", views.diagnosis_result, name="result"),

    # REST API
    path("api/info/", views.diagnosis_info, name="api_info"),
    path("api/start/", views.diagnosis_start, name="api_start"),
    path("api/sessions/in-progress/", views.diagnosis_in_progress_sessions, name="api_in_progress_sessions"),
    path("api/session/<int:session_id>/", views.diagnosis_session, name="api_session"),
    path("api/session/<int:session_id>/answer/", views.diagnosis_save_answer, name="api_save_answer"),
    path("api/submit/", views.diagnosis_submit, name="api_submit"),
    path("api/result/<int:session_id>/", views.diagnosis_result_api, name="api_result"),
    path(
        "api/result/<int:session_id>/explanation/<int:question_id>/",
        views.diagnosis_explanation,
        name="api_explanation",
    ),
]
