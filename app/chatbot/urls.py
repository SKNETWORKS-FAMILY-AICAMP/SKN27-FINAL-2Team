from django.urls import path

from . import views


app_name = "chatbot"

urlpatterns = [
    path("", views.chat_page, name="chat_page"),
    path("api/rag/", views.rag_chat_api, name="rag_chat_api"),
    path("api/rag/stream/", views.rag_chat_stream_api, name="rag_chat_stream_api"),
    path("api/sessions/", views.chat_sessions_api, name="chat_sessions_api"),
    path("api/sessions/<str:session_id>/", views.chat_session_delete_api, name="chat_session_delete_api"),
    path("api/solved-problems/", views.solved_problem_options_api, name="solved_problem_options_api"),
    path("api/image-proxy/", views.image_proxy, name="image_proxy"),
]
