from django.urls import path

from . import views


app_name = "user"

urlpatterns = [
    path("login/", views.login_page, name="login"),
    path("register/", views.register_page, name="register"),
    path("mypage/", views.mypage, name="mypage"),
]
