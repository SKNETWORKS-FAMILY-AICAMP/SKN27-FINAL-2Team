"""워커 커맨드 테스트.

가짜 생성기를 꽂아 LLM 없이 판정표를 전수 검증한다.
repository 는 mock 으로 대체한다.
"""

from __future__ import annotations

from datetime import datetime, timezone as datetime_timezone
from unittest import TestCase
from unittest.mock import MagicMock, patch

from analytics.management.commands.run_weekly_report_worker import (
    IDLE,
    READY,
    RETRIED,
    process_one_report,
    scan_missing_reports,
)
from analytics.service.weekly_report.config import get_weekly_report_config
from analytics.service.weekly_report.graph import generate_graph_report_content
from analytics.test_weekly_report_graph import (
    FakeWeeklyReportAgentSuite,
    WeeklyReportLangGraphTests,
)


MODULE = "analytics.management.commands.run_weekly_report_worker"
CLAIM_TIME = datetime(2026, 7, 26, 3, 0, tzinfo=datetime_timezone.utc)
FINISH_TIME = datetime(2026, 7, 26, 3, 1, tzinfo=datetime_timezone.utc)


def build_claimed(attempt_count: int) -> dict[str, object]:
    return {
        "studyPlanId": 77,
        "report": {
            "status": "running",
            "reportType": "weekly",
            "result": {"assessment": {}},
            "worker": {"attemptCount": attempt_count},
        },
    }


def ai_content(fallback_used: bool) -> dict[str, object]:
    return {
        "comment": {"text": "이번 주 흐름이 좋아요.", "evidenceIds": []},
        "tips": [],
        "fallbackUsed": fallback_used,
        "validation": {"guard": "passed", "validator": "passed"},
    }


class ProcessOneReportTests(TestCase):
    def setUp(self) -> None:
        self.config = get_weekly_report_config()
        self.clock = MagicMock(side_effect=[CLAIM_TIME, FINISH_TIME])

    def run_worker(self, claimed, generator):
        with patch(f"{MODULE}.repository") as repository_module, \
                patch(f"{MODULE}.process_next_plan") as process_next_plan_mock:
            repository_module.claim_next_report.return_value = claimed
            code = process_one_report(self.config, generator, self.clock)
        self.process_next_plan_mock = process_next_plan_mock
        return code, repository_module

    def test_nothing_to_do(self) -> None:
        code, repository_module = self.run_worker(None, MagicMock())

        self.assertEqual(code, IDLE)
        repository_module.finish_report.assert_not_called()
        repository_module.retry_report.assert_not_called()

    def test_ai_content_is_confirmed_immediately(self) -> None:
        generator = MagicMock(return_value=ai_content(fallback_used=False))
        code, repository_module = self.run_worker(build_claimed(1), generator)

        self.assertEqual(code, READY)
        repository_module.finish_report.assert_called_once()
        repository_module.retry_report.assert_not_called()

    def test_next_plan_runs_after_ready(self) -> None:
        """리포트가 ready 로 확정된 같은 작업에서 다음 계획을 처리해야 한다."""
        generator = MagicMock(return_value=ai_content(fallback_used=False))
        self.run_worker(build_claimed(1), generator)

        self.process_next_plan_mock.assert_called_once_with(77, self.config)

    def test_next_plan_does_not_run_on_retry(self) -> None:
        generator = MagicMock(return_value=ai_content(fallback_used=True))
        code, _ = self.run_worker(build_claimed(1), generator)

        self.assertEqual(code, RETRIED)
        self.process_next_plan_mock.assert_not_called()

    def test_next_plan_runs_after_fallback_confirmation(self) -> None:
        """기본 문구로 확정되는 마지막 시도도 ready 이므로 다음 계획을 처리한다."""
        generator = MagicMock(return_value=ai_content(fallback_used=True))
        code, _ = self.run_worker(build_claimed(3), generator)

        self.assertEqual(code, READY)
        self.process_next_plan_mock.assert_called_once_with(77, self.config)

    def test_fallback_on_the_first_attempt_is_retried(self) -> None:
        generator = MagicMock(return_value=ai_content(fallback_used=True))
        code, repository_module = self.run_worker(build_claimed(1), generator)

        self.assertEqual(code, RETRIED)
        repository_module.retry_report.assert_called_once()
        self.assertEqual(repository_module.retry_report.call_args.args[2], "AI_FALLBACK")
        repository_module.finish_report.assert_not_called()

    def test_fallback_on_the_second_attempt_is_retried(self) -> None:
        generator = MagicMock(return_value=ai_content(fallback_used=True))
        code, _ = self.run_worker(build_claimed(2), generator)

        self.assertEqual(code, RETRIED)

    def test_fallback_on_the_last_attempt_is_confirmed_not_failed(self) -> None:
        """마지막 시도에서 retry_report 를 부르면 status 가 failed 가 되어 빈 리포트가 나간다."""
        generator = MagicMock(return_value=ai_content(fallback_used=True))
        code, repository_module = self.run_worker(build_claimed(3), generator)

        self.assertEqual(code, READY)
        repository_module.finish_report.assert_called_once()
        repository_module.retry_report.assert_not_called()
        confirmed_content = repository_module.finish_report.call_args.args[2]
        self.assertTrue(confirmed_content["fallbackUsed"])

    def test_generator_exception_is_retried(self) -> None:
        generator = MagicMock(side_effect=RuntimeError("provider down"))

        with self.assertLogs(MODULE, level="ERROR"):
            code, repository_module = self.run_worker(build_claimed(1), generator)

        self.assertEqual(code, RETRIED)
        self.assertEqual(repository_module.retry_report.call_args.args[2], "GENERATOR_ERROR")

    def test_generator_exception_on_the_last_attempt_still_confirms(self) -> None:
        generator = MagicMock(side_effect=RuntimeError("provider down"))

        with self.assertLogs(MODULE, level="ERROR"):
            code, repository_module = self.run_worker(build_claimed(3), generator)

        self.assertEqual(code, READY)
        repository_module.finish_report.assert_called_once()
        confirmed_content = repository_module.finish_report.call_args.args[2]
        self.assertTrue(confirmed_content["fallbackUsed"])

    def test_claim_and_finish_use_different_timestamps(self) -> None:
        """claim 시각을 재사용하면 재시도 백오프가 무효화된다."""
        generator = MagicMock(return_value=ai_content(fallback_used=False))
        _, repository_module = self.run_worker(build_claimed(1), generator)

        self.assertEqual(repository_module.claim_next_report.call_args.args[0], CLAIM_TIME)
        self.assertEqual(repository_module.finish_report.call_args.args[3], FINISH_TIME)

    def test_report_type_is_passed_to_the_generator(self) -> None:
        generator = MagicMock(return_value=ai_content(fallback_used=False))
        self.run_worker(build_claimed(1), generator)

        self.assertEqual(generator.call_args.args[2], "weekly")


class ScanMissingReportsTests(TestCase):
    def test_recoverable_plans_are_enqueued(self) -> None:
        candidates = [
            {"userId": 1, "studyPlanId": 77, "sourceSessionId": 30},
            {"userId": 2, "studyPlanId": 88, "sourceSessionId": 31},
        ]

        with patch(f"{MODULE}.find_recoverable_sessions", return_value=candidates), \
                patch(f"{MODULE}.enqueue_weekly_report", side_effect=[True, False]) as enqueue:
            created_count = scan_missing_reports()

        self.assertEqual(created_count, 1)
        self.assertEqual(enqueue.call_count, 2)
        self.assertEqual(enqueue.call_args_list[0].args, (1, 30, 77))

    def test_nothing_to_recover(self) -> None:
        with patch(f"{MODULE}.find_recoverable_sessions", return_value=[]), \
                patch(f"{MODULE}.enqueue_weekly_report") as enqueue:
            self.assertEqual(scan_missing_reports(), 0)

        enqueue.assert_not_called()


class GraphIntegrationTests(WeeklyReportLangGraphTests):
    """실제 그래프를 가짜 에이전트로 돌려 워커 판정과 이어지는지 확인한다.

    근거 뭉치(self.result)는 기존 그래프 테스트의 setUp 을 그대로 쓴다.
    """

    def generate(self, agent_suite):
        return generate_graph_report_content(
            self.result,
            agent_suite,
            get_weekly_report_config(),
            "weekly",
        )

    def test_fake_agents_produce_confirmable_content(self) -> None:
        content = self.generate(FakeWeeklyReportAgentSuite())

        self.assertFalse(content["fallbackUsed"])

    def test_failing_agents_fall_back_without_raising(self) -> None:
        """그래프가 예외를 삼키므로 워커는 fallbackUsed 로만 실패를 안다."""
        content = self.generate(FakeWeeklyReportAgentSuite(failure_stage="analyst"))

        self.assertTrue(content["fallbackUsed"])
