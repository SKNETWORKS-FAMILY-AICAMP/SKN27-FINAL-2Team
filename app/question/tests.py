from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.template.loader import render_to_string
from django.test import RequestFactory, SimpleTestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from .serializers import SavedSessionResponse, StartQuestionsResponse
from .views import question_start


class QuestionPageStorageIsolationTest(SimpleTestCase):
    def setUp(self) -> None:
        self.factory = RequestFactory()

    def render_exam_for_user(self, user_id: int) -> str:
        request = self.factory.get("/question/exam/")
        request.user = SimpleNamespace(is_authenticated=True, user_id=user_id)
        return render_to_string(
            "question/question_exam.html",
            {"exam_mode": "practice"},
            request=request,
        )

    def test_exam_storage_keys_are_scoped_by_user(self) -> None:
        first_user_page = self.render_exam_for_user(101)
        second_user_page = self.render_exam_for_user(202)

        self.assertIn('const STORAGE_SCOPE = "101";', first_user_page)
        self.assertIn('const STORAGE_SCOPE = "202";', second_user_page)
        self.assertIn(
            "const progressStorageKey = `questionInProgress:${STORAGE_SCOPE}`;",
            first_user_page,
        )
        self.assertIn(
            "const resultStorageKey = `questionResult:${STORAGE_SCOPE}`;",
            first_user_page,
        )

    def test_exam_offers_available_count_without_relaxing_conditions(self) -> None:
        page = self.render_exam_for_user(101)

        self.assertIn('payload.error_code !== "insufficient_questions"', page)
        self.assertIn('requestBody = { ...requestBody, count: availableCount };', page)
        self.assertIn('window.location.href = "/question/?restore=1";', page)
        self.assertIn('"조건 순차 적용 결과"', page)
        self.assertIn('"난이도별 부족"', page)


class QuestionSelectionFilterTest(SimpleTestCase):
    def setUp(self) -> None:
        self.factory = APIRequestFactory()

    @patch("question.views._sample_questions_by_score_counts")
    @patch("question.views._base_question_queryset")
    def test_shortage_does_not_fill_from_unselected_conditions(
        self,
        base_question_queryset: MagicMock,
        sample_questions: MagicMock,
    ) -> None:
        question_queryset = MagicMock()
        question_queryset.filter.return_value = question_queryset
        question_queryset.count.return_value = 0
        question_queryset.order_by.return_value.values_list.return_value = []
        base_question_queryset.return_value = question_queryset
        sample_questions.return_value = (
            [],
            {
                "error": "조건에 맞는 문제가 부족합니다.",
                "available_count": 0,
                "requested_count": 10,
            },
            [],
        )
        request = self.factory.post(
            "/question/api/start/",
            {
                "generation_mode": "detail",
                "eras": ["조선"],
                "topics": ["정치"],
                "difficulties": ["중"],
                "question_types": ["결론의 도출 및 평가"],
                "question_subtypes": ["사건·자료 순서 배열"],
                "count": 10,
            },
            format="json",
        )
        force_authenticate(
            request,
            user=SimpleNamespace(is_authenticated=True, user_id=101),
        )

        response = question_start(request)

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["error_code"], "insufficient_questions")
        self.assertEqual(
            [detail["label"] for detail in response.data["condition_availability"]],
            ["시대", "주제", "대유형", "소유형"],
        )
        self.assertEqual(
            [detail["score"] for detail in response.data["score_availability"]],
            [3, 2, 1],
        )
        self.assertEqual(base_question_queryset.call_count, 1)
        question_queryset.filter.assert_any_call(
            question_type__in=["결론의 도출 및 평가"],
        )
        question_queryset.filter.assert_any_call(
            question_subtype__in=["사건·자료 순서 배열"],
        )


class ActiveQuestionResponseSecurityTest(SimpleTestCase):
    def test_start_response_omits_answer_fields(self) -> None:
        serializer = StartQuestionsResponse(
            {
                "session_id": None,
                "total_count": 1,
                "is_saved": False,
                "questions": [self._active_question()],
            }
        )

        question = serializer.data["questions"][0]
        self.assertNotIn("answer_no", question)
        self.assertNotIn("answer_explanation", question)
        self.assertNotIn("core_concept", question)
        self.assertNotIn("choice_explanation", question["choices"][0])

    def test_saved_session_omits_answer_fields(self) -> None:
        question = self._active_question()
        question.update(
            {
                "selected_choice_id": None,
                "selected_choice_no": None,
                "time_spent_ms": None,
                "is_answered": False,
            }
        )
        serializer = SavedSessionResponse(
            {
                "session_id": 1,
                "session_type": "practice",
                "total_count": 1,
                "elapsed_sec": 0,
                "remaining_sec": 60,
                "status": "in_progress",
                "answered_count": 0,
                "questions": [question],
            }
        )

        serialized_question = serializer.data["questions"][0]
        self.assertNotIn("answer_no", serialized_question)
        self.assertNotIn("answer_explanation", serialized_question)
        self.assertNotIn("core_concept", serialized_question)
        self.assertNotIn(
            "choice_explanation",
            serialized_question["choices"][0],
        )

    def _active_question(self) -> dict:
        return {
            "question_id": 1,
            "content": "문제",
            "passage": "",
            "image_caption": "",
            "question_image_path": "",
            "q_score": 2,
            "era": "조선",
            "topic": "정치",
            "question_type": "개념",
            "question_subtype": "기본",
            "answer_no": 1,
            "answer_explanation": "정답 해설",
            "core_concept": "핵심 개념",
            "choices": [
                {
                    "choice_id": 10,
                    "choice_no": 1,
                    "content": "선택지",
                    "choice_image_path": "",
                    "choice_explanation": "선택지 해설",
                }
            ],
        }
