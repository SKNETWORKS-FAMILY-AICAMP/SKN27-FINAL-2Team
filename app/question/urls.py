from django.urls import path

from . import views

app_name = "question"

urlpatterns = [
    path("", views.question_create, name="create"),
    path("exam/", views.question_exam, name="exam"),
    path("result/", views.question_result, name="result"),
    path("api/filters/", views.question_filters, name="api_filters"),
    path("api/start/", views.question_start, name="api_start"),
    path("api/sessions/in-progress/", views.question_in_progress_sessions, name="api_in_progress_sessions"),
    path("api/session/<int:session_id>/", views.question_save_session, name="api_session"),
    path("api/session/<int:session_id>/answer/", views.question_save_answer, name="api_save_answer"),
]
