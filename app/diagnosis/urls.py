from django.urls import path

from . import views


app_name = "diagnosis"

urlpatterns = [
    path("", views.diagnosis_intro, name="intro"),
    path("exam/", views.diagnosis_exam, name="exam"),
    path("result/", views.diagnosis_result, name="result"),
]
