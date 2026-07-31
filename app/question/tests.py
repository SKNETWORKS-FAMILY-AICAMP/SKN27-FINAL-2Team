from types import SimpleNamespace

from django.template.loader import render_to_string
from django.test import RequestFactory, SimpleTestCase

from .serializers import SavedSessionResponse, StartQuestionsResponse


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
