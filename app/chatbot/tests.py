import json
from types import SimpleNamespace
from unittest.mock import patch

from django.test import RequestFactory, TestCase
from django.urls import reverse

from .views import rag_chat_api, rag_chat_stream_api


class ChatbotApiTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()

    def request(self, payload: dict):
        request = self.factory.post("/chatbot/api/rag/", data=json.dumps(payload), content_type="application/json")
        request.user = SimpleNamespace(is_authenticated=True, user_id=1)
        return request

    def test_top_k_rejects_invalid_value(self):
        response = rag_chat_api(self.request({"question": "세종대왕", "top_k": "abc"}))
        self.assertEqual(response.status_code, 400)

    @patch("chatbot.views.save_chat_turn")
    @patch("chatbot.views.build_history_rag_answer", return_value={"answer": "정상"})
    def test_top_k_is_capped(self, build_answer, save_chat_turn):
        response = rag_chat_api(self.request({"question": "세종대왕", "top_k": 999}))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(build_answer.call_args.kwargs["top_k"], 20)

    @patch("chatbot.views.save_chat_turn")
    @patch("chatbot.views.build_history_rag_answer", return_value={"answer": "정상"})
    def test_top_k_defaults_to_20(self, build_answer, _save_chat_turn):
        response = rag_chat_api(self.request({"question": "세종대왕"}))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(build_answer.call_args.kwargs["top_k"], 20)

    @patch("chatbot.views.build_history_rag_answer", side_effect=RuntimeError("internal detail"))
    def test_rag_error_hides_internal_detail(self, _build_answer):
        response = rag_chat_api(self.request({"question": "세종대왕"}))
        self.assertEqual(response.status_code, 500)
        self.assertNotIn("internal detail", response.content.decode())

    @patch("chatbot.views.save_chat_turn", side_effect=RuntimeError("database unavailable"))
    @patch("chatbot.views.build_history_rag_answer", return_value={"answer": "정상"})
    def test_save_failure_does_not_hide_answer(self, _build_answer, _save_chat_turn):
        response = rag_chat_api(self.request({"question": "세종대왕", "session_id": "test"}))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(json.loads(response.content)["answer"], "정상")

    @patch("chatbot.views.save_chat_turn")
    @patch(
        "chatbot.views.stream_concept_rag_answer",
        return_value=iter(({"type": "meta", "title": "세종", "summary": "요약"}, {"type": "done", "data": {"answer": "정상"}})),
    )
    def test_concept_stream_returns_sse_events(self, _stream_answer, save_chat_turn):
        response = rag_chat_stream_api(self.request({"question": "세종대왕"}))
        body = b"".join(response.streaming_content).decode()
        self.assertEqual(response["Content-Type"], "text/event-stream; charset=utf-8")
        self.assertIn('event: meta', body)
        self.assertIn('event: done', body)
        save_chat_turn.assert_called_once()

    def test_image_proxy_requires_login(self):
        response = self.client.get(reverse("chatbot:image_proxy"), {"url": "https://contents.history.go.kr/data/img/test.jpg"})
        self.assertEqual(response.status_code, 302)
