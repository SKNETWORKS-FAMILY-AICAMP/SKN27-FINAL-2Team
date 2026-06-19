from django.urls import path

from . import views


app_name = "user"

urlpatterns = [
    path("login/", views.login_page, name="login"),
    path("logout/", views.logout_page, name="logout"),
    path("register/", views.register_page, name="register"),
    path("send-verification-code/", views.send_verification_code, name="send_verification_code"),
    path("verify-verification-code/", views.verify_verification_code, name="verify_verification_code"),
    path("check-nickname/", views.check_nickname, name="check_nickname"),
    path("mypage/", views.mypage, name="mypage"),
    path("profile/edit/", views.profile_edit, name="profile_edit"),
    path("wrong-note/", views.wrong_note, name="wrong_note"),
]
