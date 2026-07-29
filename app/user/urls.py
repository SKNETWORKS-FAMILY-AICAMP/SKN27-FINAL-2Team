from django.urls import path

from . import oauth, views


app_name = "user"

urlpatterns = [
    path("login/", views.login_page, name="login"),
    path("logout/", views.logout_page, name="logout"),
    path("oauth/<str:provider>/login/", oauth.oauth_login, name="oauth_login"),
    path("oauth/<str:provider>/callback/", oauth.oauth_callback, name="oauth_callback"),
    path("profile/edit/", views.profile_edit, name="profile_edit"),
    path("solved-problems/", views.solved_problems, name="solved_problems"),
    path("wrong-note/", views.wrong_note, name="wrong_note"),
]
