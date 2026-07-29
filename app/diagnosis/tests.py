from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from analytics.service.study_plan import StudyPlanBlockNotDue, StudyPlanBlockTerminal
from .serializers import DiagnosisStartResponseSerializer
from .views import (
    _complete_weekly_review_block_for_session,
    _get_expected_grade,
    diagnosis_start,
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
