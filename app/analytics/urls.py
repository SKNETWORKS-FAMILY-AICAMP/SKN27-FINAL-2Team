from django.urls import path
from . import views

app_name = "analytics"

urlpatterns = [
    path("mypage/", views.mypage, name="mypage"),
    path("wrong-rate-detail/", views.wrong_rate_detail, name="wrong_rate_detail"),
]
