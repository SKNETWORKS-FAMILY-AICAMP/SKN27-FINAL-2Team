from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase, TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from analytics.service.study_plan import StudyPlanBlockNotDue, StudyPlanBlockTerminal
from .serializers import DiagnosisStartResponseSerializer
from .views import (
    _complete_weekly_review_block_for_session,
    _get_expected_grade,
    diagnosis_start,
    diagnosis_submit,
)


class DiagnosisSubmitSecurityTest(TestCase):
    def request(self, answers: list[dict]) -> object:
        request = APIRequestFactory().post(
            "/api/diagnosis/submit/",
            {
                "session_id": 55,
                "elapsed_sec": 120,
                "answers": answers,
            },
            format="json",
        )
        force_authenticate(
            request,
            user=SimpleNamespace(user_id=1, is_authenticated=True),
        )
        return request

    @patch("diagnosis.views._complete_weekly_review_block_for_session")
    @patch("diagnosis.views.create_session_snapshot")
    @patch("diagnosis.views.SolveRecords.objects.bulk_update")
    @patch("diagnosis.views.QuestionOptions.objects.filter")
    @patch("diagnosis.views.Questions.objects.filter")
    @patch("diagnosis.views.SolveRecords.objects.select_for_update")
    @patch("diagnosis.views.SolveSessions.objects.select_for_update")
    def test_rejects_choice_from_another_question(
        self,
        select_session: MagicMock,
        select_records: MagicMock,
        filter_questions: MagicMock,
        filter_options: MagicMock,
        _bulk_update: MagicMock,
        _create_snapshot: MagicMock,
        _complete_review: MagicMock,
    ) -> None:
        question = self.question()
        session = self.session()
        record = self.record(question)
        select_session.return_value.get.return_value = session
        select_records.return_value.select_related.return_value.filter.return_value = [record]
        filter_questions.return_value = [question]
        filter_options.return_value = []

        response = diagnosis_submit(
            self.request(
                [
                    {
                        "question_id": 1,
                        "choice_id": 999,
                        "selected_no": 1,
                        "time_spent_ms": 1000,
                    }
                ]
            )
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["error"], "선택지가 세션 문항에 속하지 않습니다.")

    @patch("diagnosis.views._complete_weekly_review_block_for_session")
    @patch("diagnosis.views.create_session_snapshot")
    @patch("diagnosis.views.SolveRecords.objects.bulk_update")
    @patch("diagnosis.views.QuestionOptions.objects.filter")
    @patch("diagnosis.views.Questions.objects.filter")
    @patch("diagnosis.views.SolveRecords.objects.select_for_update")
    @patch("diagnosis.views.SolveSessions.objects.select_for_update")
    def test_uses_server_choice_number_and_completes_once(
        self,
        select_session: MagicMock,
        select_records: MagicMock,
        filter_questions: MagicMock,
        filter_options: MagicMock,
        bulk_update: MagicMock,
        create_snapshot: MagicMock,
        _complete_review: MagicMock,
    ) -> None:
        question = self.question()
        session = self.session()
        record = self.record(question)
        option = SimpleNamespace(
            choice_id=10,
            question_id=1,
            choice_no=3,
            is_answer=True,
        )
        select_session.return_value.get.return_value = session
        select_records.return_value.select_related.return_value.filter.return_value = [record]
        filter_questions.return_value = [question]
        filter_options.return_value = [option]

        response = diagnosis_submit(
            self.request(
                [
                    {
                        "question_id": 1,
                        "choice_id": 10,
                        "selected_no": 1,
                        "time_spent_ms": 1000,
                    }
                ]
            )
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(record.selected_no, 3)
        self.assertTrue(record.is_correct)
        self.assertEqual(session.status, "completed")
        bulk_update.assert_called_once()
        create_snapshot.assert_called_once_with(55)

    def question(self) -> SimpleNamespace:
        return SimpleNamespace(
            question_id=1,
            q_score=2,
            question_type="concept",
            topic="politics",
            era="joseon",
        )

    def record(self, question: SimpleNamespace) -> SimpleNamespace:
        return SimpleNamespace(
            question_id=question.question_id,
            question=question,
            q_score=question.q_score,
            selected_no=None,
            is_correct=False,
            time_spent_ms=None,
            q_type=question.question_type,
            topic=question.topic,
            era=question.era,
        )

    def session(self) -> SimpleNamespace:
        return SimpleNamespace(
            session_id=55,
            status="in_progress",
            elapsed_sec=None,
            total_score=None,
            answer_rate=None,
            save=MagicMock(),
        )


class DiagnosisResponseSecurityTest(SimpleTestCase):
    def test_start_response_omits_choice_explanation(self) -> None:
        serializer = DiagnosisStartResponseSerializer(
            {
                "session_id": 1,
                "total_count": 1,
                "time_limit_sec": 60,
                "questions": [
                    {
                        "question_id": 1,
                        "content": "문제",
                        "passage": "",
                        "image_caption": "",
                        "visual_note": "",
                        "question_image_path": "",
                        "q_score": 2,
                        "era": "조선",
                        "topic": "정치",
                        "question_type": "개념",
                        "question_subtype": "기본",
                        "choices": [
                            {
                                "choice_id": 10,
                                "choice_no": 1,
                                "content": "선택지",
                                "choice_image_path": "",
                                "choice_explanation": "노출되면 안 되는 해설",
                            }
                        ],
                    }
                ],
            }
        )

        choice = serializer.data["questions"][0]["choices"][0]
        self.assertNotIn("choice_explanation", choice)


class DiagnosisPageAuthenticationTest(SimpleTestCase):
    def test_diagnosis_pages_redirect_anonymous_user_to_login(self) -> None:
        protected_paths = (
            "/diagnosis/",
            "/diagnosis/exam/",
            "/diagnosis/result/",
        )

        for protected_path in protected_paths:
            with self.subTest(path=protected_path):
                response = self.client.get(protected_path)

                self.assertEqual(response.status_code, 302)
                self.assertTrue(
                    response.url.startswith("/user/login/?next="),
                )


class ExpectedGradeTest(SimpleTestCase):
    def test_total_score_thresholds(self):
        self.assertEqual(_get_expected_grade(80), "1급")
        self.assertEqual(_get_expected_grade(70), "2급")
        self.assertEqual(_get_expected_grade(60), "3급")
        self.assertEqual(_get_expected_grade(59), "탈락")


class WeeklyReportEnqueueTest(SimpleTestCase):
    @patch("diagnosis.views.enqueue_weekly_report")
    @patch(
        "diagnosis.views.complete_study_plan_block_by_id",
        return_value={"completionRate": 1.0},
    )
    @patch(
        "diagnosis.views._find_weekly_review_block",
        return_value=({"blockType": "weekly_review"}, None),
    )
    @patch(
        "diagnosis.views._session_study_plan_ref",
        return_value={"studyplan_id": 77, "study_plan_block_id": "weekly-1"},
    )
    def test_completed_weekly_review_enqueues_report(
        self,
        _study_plan_ref,
        _find_block,
        _complete_block,
        enqueue_report,
    ):
        session = SimpleNamespace(session_id=30, session_type="diagnostic", status="completed")

        _complete_weekly_review_block_for_session(session, user_id=1)

        enqueue_report.assert_called_once_with(1, 30, 77)


class WeeklyReviewStartConflictTest(SimpleTestCase):
    def test_terminal_and_not_due_errors_follow_api_contract(self):
        cases = (
            (StudyPlanBlockTerminal, "BLOCK_TERMINAL", "종료된 블록은 새로 시작할 수 없습니다."),
            (StudyPlanBlockNotDue, "BLOCK_NOT_DUE", "오늘 예정된 블록만 시작할 수 있습니다."),
        )
        for error_type, code, message in cases:
            with self.subTest(code=code), patch(
                "diagnosis.views._find_weekly_review_block",
                return_value=({"blockType": "weekly_review"}, None),
            ), patch(
                "diagnosis.views.validate_study_plan_block_start",
                side_effect=error_type(),
            ):
                request = APIRequestFactory().post(
                    "/api/diagnosis/start/",
                    {"studyplan_id": 77, "study_plan_block_id": "weekly-1"},
                    format="json",
                )
                force_authenticate(request, user=SimpleNamespace(user_id=1, is_authenticated=True))

                response = diagnosis_start(request)

                self.assertEqual(response.status_code, 409)
                self.assertEqual(response.data, {"code": code, "error": message})
