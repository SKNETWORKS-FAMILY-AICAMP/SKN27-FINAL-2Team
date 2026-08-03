import json
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor
from types import ModuleType, SimpleNamespace
from unittest.mock import MagicMock, patch

from django.core.cache import cache
from django.test import RequestFactory, SimpleTestCase, TestCase
from django.urls import reverse

from .rag.llm_answer_generator import CONCEPT_STREAM_STRUCTURED_SYSTEM_PROMPT, CORE_STREAM_STRUCTURED_SYSTEM_PROMPT, FOUNDATION_EXPLANATION_SYSTEM_PROMPT, LLMAnswerGenerator, PROMPT_SNIPPET_MAX_CHARS, normalize_structured_answer, prompt_snippet, sanitize_answer
from .rag import reranker
from .rag_service import (
    build_search_question,
    build_problem_option_queries,
    exclude_previously_answered_results,
    stream_concept_rag_answer,
    stream_question_rag_answer,
)
from .views import _chat_request_blocked, chat_page, chat_session_delete_api, chat_sessions_api, proxied_image_path, rag_chat_api, rag_chat_stream_api


class ChatPageStorageIsolationTests(SimpleTestCase):
    def setUp(self) -> None:
        self.factory = RequestFactory()

    def render_for_user(self, user_id: int) -> str:
        request = self.factory.get("/chatbot/")
        request.user = SimpleNamespace(is_authenticated=True, user_id=user_id)
        return chat_page(request).content.decode()

    def test_browser_storage_keys_are_scoped_by_user(self) -> None:
        first_user_page = self.render_for_user(101)
        second_user_page = self.render_for_user(202)

        self.assertIn(
            'const STORAGE_KEY = "himate_chat_sessions:101";',
            first_user_page,
        )
        self.assertIn(
            'const STORAGE_KEY = "himate_chat_sessions:202";',
            second_user_page,
        )
        self.assertIn(
            'const PROBLEM_CONTEXT_STORAGE_KEY = "chatbotProblemContext:101";',
            first_user_page,
        )
        self.assertIn(
            'localStorage.removeItem("himate_chat_sessions")',
            first_user_page,
        )


class ChatbotApiTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()

    def test_prompt_snippet_is_capped(self):
        snippet = prompt_snippet("가" * 300)
        self.assertEqual(len(snippet), PROMPT_SNIPPET_MAX_CHARS)
        self.assertTrue(snippet.endswith("…"))

    def request(self, payload: dict):
        request = self.factory.post("/chatbot/api/rag/", data=json.dumps(payload), content_type="application/json")
        request.user = SimpleNamespace(is_authenticated=True, user_id=1)
        return request

    @patch("chatbot.views.ChatMessages.objects.filter")
    @patch("chatbot.views.ChatSessions.objects.filter")
    def test_deleting_session_removes_only_current_users_history(self, session_filter, message_filter):
        request = self.factory.delete("/chatbot/api/sessions/session-1/")
        request.user = SimpleNamespace(is_authenticated=True, user_id=1)

        response = chat_session_delete_api(request, "session-1")

        self.assertEqual(response.status_code, 204)
        message_filter.assert_called_once_with(
            session__session_id="session-1",
            session__user=request.user,
        )
        session_filter.assert_called_once_with(session_id="session-1", user=request.user)

    def test_top_k_rejects_invalid_value(self):
        response = rag_chat_api(self.request({"question": "세종대왕", "top_k": "abc"}))
        self.assertEqual(response.status_code, 400)

    @patch("chatbot.views.ChatMessages.objects.filter")
    @patch("chatbot.views.ChatSessions.objects.filter")
    def test_session_restore_scopes_user_and_parses_assistant_json(self, session_filter, message_filter):
        now = datetime.now(timezone.utc)
        session_filter.return_value.annotate.return_value.filter.return_value.order_by.return_value.__getitem__.return_value = [
            SimpleNamespace(session_id="session-1", created_at=now, last_message_at=now)
        ]
        message_filter.return_value.order_by.return_value = [
            SimpleNamespace(session_id="session-1", sender_type="assistant", content=json.dumps({"answer": "세종대왕입니다.", "intent": "concept"}), created_at=now),
            SimpleNamespace(session_id="session-1", sender_type="user", content="세종대왕 알려줘", created_at=now),
        ]
        request = self.factory.get("/chatbot/api/sessions/")
        request.user = SimpleNamespace(is_authenticated=True, user_id=1)

        response = chat_sessions_api(request)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(json.loads(response.content)["sessions"][0]["messages"][1]["data"]["answer"], "세종대왕입니다.")
        session_filter.assert_called_once_with(user=request.user)

    def test_chat_rate_limit_blocks_request_over_limit(self):
        request = self.request({"question": "세종대왕"})
        request.user.user_id = 987654321
        cache_key = f"chat-rate:{request.user.user_id}"
        cache.delete(cache_key)
        self.addCleanup(cache.delete, cache_key)

        for _attempt in range(20):
            self.assertIsNone(_chat_request_blocked(request, "세종대왕", []))

        response = _chat_request_blocked(request, "세종대왕", [])
        self.assertIsNotNone(response)
        self.assertEqual(response.status_code, 429)

    def test_solved_problem_images_use_local_proxy(self):
        value = proxied_image_path("https://contents.history.go.kr/data/img/ki/test.jpg")
        self.assertTrue(value.startswith("/chatbot/api/image-proxy/?url="))

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
        queries = build_problem_option_queries(
            "[지문] 수가 고구려를 침공했다.\n[문제] 이후 사실은?\n[보기]\n1. 을지문덕\n2. 연개소문\n[내 답] 1번\n[정답] 2번\n[분류] 고대 / 정치",
            {1: "살수 대첩은 을지문덕의 승리예요."},
        )
        self.assertEqual(len(queries), 3)
        self.assertTrue(all("수가 고구려를 침공했다" in query and "이후 사실은" in query for query in queries))
        self.assertTrue(any("을지문덕" in query for query in queries))
        self.assertTrue(any("살수 대첩" in query for query in queries))

    def test_problem_context_does_not_use_previous_chat_topic(self):
        question = "[문제] 미우라 공사의 정책 결과는?\n[보기]\n3. 을미사변\n[사용자 질문] 3번 선지 설명해줘"
        history = [{"role": "user", "content": "계해약조를 설명해줘"}]
        self.assertEqual(build_search_question(question, history, "question"), question)

    def test_additional_question_excludes_previously_answered_source_title(self):
        history = [{"role": "assistant", "content": "계해약조와 북방 사민 정책을 시행했어요."}]
        results = [SimpleNamespace(title="계해약조"), SimpleNamespace(title="훈민정음 창제")]

        filtered = exclude_previously_answered_results(results, "다른 업적 더 알려줘", history)

        self.assertEqual([result.title for result in filtered], ["훈민정음 창제"])

    def test_core_problem_stream_uses_db_choice_explanations_verbatim(self):
        result = SimpleNamespace(chunk_id=1, score=1.0)
        generator = SimpleNamespace(config=SimpleNamespace(provider="openai", model="test", temperature=0))
        generator.generate_structured_stream = lambda *args, **kwargs: iter((
            {"type": "section", "heading": "1. 정답 근거"},
            {"type": "row", "term": "핵심", "content": "검색 근거"},
            {"type": "section", "heading": "2. 선지 판단"},
            {"type": "row", "term": "1번", "content": "모델이 만든 판단"},
            {"type": "done"},
        ))
        with patch("chatbot.rag_service.PgVectorHybridRetriever") as retriever_class, patch(
            "chatbot.rag_service.should_use_graph_context", return_value=False
        ), patch("chatbot.rag_service.search_timeline_sources", return_value=[]), patch(
            "chatbot.rag_service.result_to_payload", return_value={"title": "살수 대첩"}
        ), patch("chatbot.rag_service.has_enough_evidence", return_value=True), patch(
            "chatbot.rag_service.LLMAnswerGenerator.from_env", return_value=generator
        ):
            retriever_class.return_value.search.return_value = [result]
            events = list(stream_question_rag_answer(
                "[문제] 옳은 것은?\n[보기]\n1. 그림\n[내 답] 1번\n[분류] 고대 / 정치",
                explanation_level="core",
                choice_explanations={1: "DB에 저장된 선지 해설"},
            ))

        row_contents = [event["content"] for event in events if event["type"] == "row"]
        self.assertIn("DB에 저장된 선지 해설", row_contents)
        self.assertNotIn("모델이 만든 판단", row_contents)

    def test_parallel_reranker_loads_once(self):
        model = object()
        sentence_transformers_module = ModuleType("sentence_transformers")
        cross_encoder = MagicMock(return_value=model)
        sentence_transformers_module.CrossEncoder = cross_encoder

        with patch.dict(
            "sys.modules",
            {"sentence_transformers": sentence_transformers_module},
        ), patch.object(
            reranker,
            "_reranker",
            None,
        ), patch.object(
            reranker,
            "_reranker_loaded",
            False,
        ):
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

    @patch("chatbot.views.stream_question_rag_answer", side_effect=RuntimeError("stream failed"))
    def test_stream_error_uses_client_event_shape(self, _stream_answer):
        response = rag_chat_stream_api(self.request({"question": "문제 풀이", "intent": "question"}))
        body = b"".join(response.streaming_content).decode()
        self.assertIn('event: error', body)
        self.assertIn('"type":"error"', body)

    @patch("chatbot.views.save_chat_turn")
    @patch("chatbot.views.load_problem_choice_explanations", return_value={1: "DB 해설"})
    @patch("chatbot.views.stream_question_rag_answer", return_value=iter(({"type": "done", "data": {"structured_answer": {"sections": []}}},)))
    def test_problem_stream_can_request_foundation_explanation(self, stream_answer, load_explanations, _save_chat_turn):
        response = rag_chat_stream_api(self.request({"question": "문제 풀이", "intent": "question", "foundation_explanation": True, "problem_record_id": 9}))
        b"".join(response.streaming_content)
        load_explanations.assert_called_once()
        self.assertEqual(stream_answer.call_args.kwargs["explanation_level"], "foundation")
        self.assertEqual(stream_answer.call_args.kwargs["choice_explanations"], {1: "DB 해설"})

    def test_image_proxy_requires_login(self):
        response = self.client.get(reverse("chatbot:image_proxy"), {"url": "https://contents.history.go.kr/data/img/test.jpg"})
        self.assertEqual(response.status_code, 302)
