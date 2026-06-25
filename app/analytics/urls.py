from django.urls import path
from . import views

app_name = "analytics"

urlpatterns = [
    path("mypage/", views.mypage, name="mypage"),
    path("wrong-rate-detail/", views.wrong_rate_detail, name="wrong_rate_detail"),
    path("study-plan/create/", views.create_study_plan_view, name="study_plan_create"),
    path(
        "study-plan/block/delete/",
        views.delete_study_plan_block_view,
        name="study_plan_block_delete",
    ),
    path(
        "study-plan/block/move/",
        views.move_study_plan_blocks_view,
        name="study_plan_block_move",
    ),
]
