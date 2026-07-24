import json
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import patch

from django.test import RequestFactory, TestCase
from django.urls import reverse

from .rag.llm_answer_generator import CONCEPT_STREAM_STRUCTURED_SYSTEM_PROMPT, CORE_STREAM_STRUCTURED_SYSTEM_PROMPT, FOUNDATION_EXPLANATION_SYSTEM_PROMPT, LLMAnswerGenerator, normalize_structured_answer, sanitize_answer
from .rag import reranker
from .rag_service import build_problem_option_queries, stream_concept_rag_answer
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

    def test_problem_explanation_prompts_define_core_and_foundation_formats(self):
        self.assertIn("먼저 알아둘 용어", FOUNDATION_EXPLANATION_SYSTEM_PROMPT)
        self.assertIn("선지별 해설", FOUNDATION_EXPLANATION_SYSTEM_PROMPT)
        self.assertIn("1. 정답 근거", CORE_STREAM_STRUCTURED_SYSTEM_PROMPT)
        self.assertIn("~에요/~예요", CORE_STREAM_STRUCTURED_SYSTEM_PROMPT)
        self.assertIn("1. 먼저 알아둘 용어", CONCEPT_STREAM_STRUCTURED_SYSTEM_PROMPT)
        self.assertIn("1. 무슨 관계인지", CONCEPT_STREAM_STRUCTURED_SYSTEM_PROMPT)

    def test_sanitize_preserves_positive_sufficient_evidence(self):
        self.assertEqual(sanitize_answer("유물은 충분한 근거를 확인할 수 있다."), "유물은 충분한 근거를 확인할 수 있다.")
        self.assertEqual(sanitize_answer("판단할 충분한 근거가 없다."), "")

    def test_structured_answer_sanitizes_items_and_stream_keeps_final_event(self):
        answer = normalize_structured_answer({"sections": [{"heading": "근거 부족", "items": [{"content": "설명할 만큼의 근거가 없다."}]}], "highlights": ["정상"]})
        self.assertEqual(answer["sections"][0]["heading"], "")
        self.assertEqual(answer["sections"][0]["items"][0]["content"], "")
        self.assertEqual(list(LLMAnswerGenerator._parse_stream_events(iter(['{"type":"done"}']))), [{"type": "done"}])

    def test_problem_option_queries_keep_context_for_each_choice(self):
        queries = build_problem_option_queries("[지문] 수가 고구려를 침공했다.\n[문제] 이후 사실은?\n[보기]\n1. 을지문덕\n2. 연개소문\n[내 답] 1번\n[정답] 2번\n[분류] 고대 / 정치")
        self.assertEqual(len(queries), 2)
        self.assertTrue(all("수가 고구려를 침공했다" in query and "이후 사실은" in query for query in queries))
        self.assertTrue(any("을지문덕" in query for query in queries))

    def test_parallel_reranker_loads_once(self):
        model = object()
        with patch.object(reranker, "_reranker", None), patch.object(reranker, "_reranker_loaded", False), patch("sentence_transformers.CrossEncoder", return_value=model) as cross_encoder:
            with ThreadPoolExecutor(max_workers=2) as executor:
                loaded = list(executor.map(lambda _: reranker.get_reranker(), range(2)))
        self.assertEqual(loaded, [model, model])
        cross_encoder.assert_called_once()

    def test_concept_stream_searches_without_question_intent(self):
        called = {}
        generator = SimpleNamespace(config=SimpleNamespace(provider="openai", model="test", temperature=0))
        generator.generate_structured_stream = lambda *args, **kwargs: (called.update(kwargs) or iter(({"type": "done"},)))
        with patch("chatbot.rag_service.PgVectorHybridRetriever") as retriever_class, patch("chatbot.rag_service.should_use_graph_context", return_value=False), patch("chatbot.rag_service.search_timeline_sources", return_value=[]), patch("chatbot.rag_service.result_to_payload", return_value={"title": "세종"}), patch("chatbot.rag_service.has_enough_evidence", return_value=True), patch("chatbot.rag_service.LLMAnswerGenerator.from_env", return_value=generator):
            retriever_class.return_value.search.return_value = [object()]
            events = list(stream_concept_rag_answer("세종대왕"))
        self.assertEqual(events[-1]["type"], "done")
        self.assertEqual(called["explanation_level"], "concept")

    @patch("chatbot.views.save_chat_turn")
    @patch("chatbot.views.build_history_rag_answer", return_value={"answer": "정상"})
    def test_problem_question_uses_core_instruction_by_default(self, build_answer, _save_chat_turn):
        response = rag_chat_api(self.request({"question": "문제 풀이", "intent": "question", "problem_session_id": 12}))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(build_answer.call_args.kwargs["explanation_level"], "core")

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

    @patch("chatbot.views.save_chat_turn")
    @patch("chatbot.views.stream_question_rag_answer", return_value=iter(({"type": "section", "heading": "1. 문제 풀이"}, {"type": "done", "data": {"structured_answer": {"sections": []}}})))
    def test_problem_stream_returns_structured_events(self, _stream_answer, save_chat_turn):
        response = rag_chat_stream_api(self.request({"question": "문제 풀이", "intent": "question"}))
        body = b"".join(response.streaming_content).decode()
        self.assertIn('event: section', body)
        save_chat_turn.assert_called_once()

    @patch("chatbot.views.save_chat_turn")
    @patch("chatbot.views.stream_question_rag_answer", return_value=iter(({"type": "done", "data": {"structured_answer": {"sections": []}}},)))
    def test_problem_stream_can_request_foundation_explanation(self, stream_answer, _save_chat_turn):
        response = rag_chat_stream_api(self.request({"question": "문제 풀이", "intent": "question", "foundation_explanation": True}))
        b"".join(response.streaming_content)
        self.assertEqual(stream_answer.call_args.kwargs["explanation_level"], "foundation")

    def test_image_proxy_requires_login(self):
        response = self.client.get(reverse("chatbot:image_proxy"), {"url": "https://contents.history.go.kr/data/img/test.jpg"})
        self.assertEqual(response.status_code, 302)
