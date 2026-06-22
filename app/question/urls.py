from django.urls import path

from . import views

app_name = "question"

urlpatterns = [
    path("", views.question_create, name="create"),
    path("exam/", views.question_exam, name="exam"),
    path("result/", views.question_result, name="result"),
]
