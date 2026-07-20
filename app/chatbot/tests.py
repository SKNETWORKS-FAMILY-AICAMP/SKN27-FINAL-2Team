import json
from types import SimpleNamespace
from unittest.mock import patch

from django.test import RequestFactory, TestCase
from django.urls import reverse

from .rag.llm_answer_generator import LLMAnswerGenerator, normalize_structured_answer, sanitize_answer
from .rag_service import build_problem_option_queries
from .views import explanation_level_for_score, rag_chat_api, rag_chat_stream_api


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

    def test_explanation_level_uses_diagnosis_total_score(self):
        self.assertEqual(explanation_level_for_score(59), "foundation")
        self.assertEqual(explanation_level_for_score(60), "core")

    def test_sanitize_preserves_positive_sufficient_evidence(self):
        self.assertEqual(sanitize_answer("유물은 충분한 근거를 확인할 수 있다."), "유물은 충분한 근거를 확인할 수 있다.")
        self.assertEqual(sanitize_answer("판단할 충분한 근거가 없다."), "")

    def test_structured_answer_sanitizes_items_and_stream_keeps_final_event(self):
        answer = normalize_structured_answer({"sections": [{"heading": "근거 부족", "items": [{"content": "설명할 만큼의 근거가 없다."}]}], "highlights": ["정상"]})
        self.assertEqual(answer["sections"][0]["heading"], "")
        self.assertEqual(answer["sections"][0]["items"][0]["content"], "")
        self.assertEqual(list(LLMAnswerGenerator._parse_stream_events(iter(['{"type":"done"}']))), [{"type": "done"}])

    def test_problem_option_queries_keep_context_for_each_choice(self):
        queries = build_problem_option_queries("[지문] 수가 고구려를 침공했다.\n[문제] 이후 사실은?\n[보기]\n1. 을지문덕\n2. 연개소문\n[정답] 2번\n[분류] 고대 / 정치")
        self.assertEqual(len(queries), 2)
        self.assertTrue(all("수가 고구려를 침공했다" in query and "이후 사실은" in query for query in queries))
        self.assertTrue(any("연개소문" in query for query in queries))

    @patch("chatbot.views.save_chat_turn")
    @patch("chatbot.views.personalized_explanation_level", return_value="foundation")
    @patch("chatbot.views.build_history_rag_answer", return_value={"answer": "정상"})
    def test_problem_question_receives_personalized_instruction(self, build_answer, _instruction, _save_chat_turn):
        response = rag_chat_api(self.request({"question": "문제 풀이", "intent": "question", "problem_session_id": 12}))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(build_answer.call_args.kwargs["explanation_level"], "foundation")
        _instruction.assert_called_once_with(self.request({}).user, 12)

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
