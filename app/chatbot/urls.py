from django.urls import path

from . import views


app_name = "chatbot"

urlpatterns = [
    path("", views.chat_page, name="chat_page"),
    path("api/rag/", views.rag_chat_api, name="rag_chat_api"),
    path("api/image-proxy/", views.image_proxy, name="image_proxy"),
]
