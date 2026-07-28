"""다음 계획 자동 생성 테스트.

DB 는 mock 으로 대체하고 상태 전이와 분기 판정을 검증한다.
설계 기준: 학습계획_AI주간리포트_통합설계.md 3.4, 구현상세부록 2.6.
"""

from __future__ import annotations

from datetime import datetime, timezone as datetime_timezone
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import MagicMock, patch

from analytics.service.study_plan.service import InitialStudyPlanConfigUnavailable
from analytics.service.weekly_report.config import get_weekly_report_config
from analytics.service.weekly_report.next_plan import (
    GENERATION_UNAVAILABLE_REASON,
    IN_PROGRESS_SESSION_REASON,
    NEXT_PLAN_BLOCKED,
    NEXT_PLAN_FAILED,
    NEXT_PLAN_IDLE,
    NEXT_PLAN_SUCCEEDED,
    process_next_plan,
)
from analytics.service.weekly_report.worker import (
    defer_next_plan,
    mark_next_plan_blocked,
    mark_next_plan_failed,
    mark_next_plan_succeeded,
)


NEXT_MODULE = "analytics.service.weekly_report.next_plan"
CREATE_TARGET = "analytics.service.study_plan.service.create_personalized_study_plan"
NOW = datetime(2026, 7, 26, 3, 0, tzinfo=datetime_timezone.utc)


def build_ready_report(
    next_status: str = "pending",
    next_plan_id: int | None = None,
) -> dict[str, object]:
    return {
        "status": "ready",
        "worker": {
            "attemptCount": 1,
            "availableAt": None,
            "startedAt": None,
            "lastError": None,
        },
        "nextPlan": {
            "status": next_status,
            "studyPlanId": next_plan_id,
            "blockedReason": None,
        },
    }


def build_locked_plan(
    report: dict[str, object],
    plan_status: str = "active",
) -> SimpleNamespace:
    return SimpleNamespace(
        studyplan_id=77,
        user_id=5,
        status=plan_status,
        weekly_report_data=report,
    )


class NextPlanTransitionTests(TestCase):
    """worker.py 의 순수 상태 전이 함수를 검증한다."""

    def test_succeeded_records_plan_id(self) -> None:
        result = mark_next_plan_succeeded(build_ready_report(), 43)

        self.assertTrue(result["changed"])
        self.assertEqual(result["report"]["nextPlan"]["status"], "succeeded")
        self.assertEqual(result["report"]["nextPlan"]["studyPlanId"], 43)
        self.assertIsNone(result["report"]["nextPlan"]["blockedReason"])

    def test_blocked_records_reason(self) -> None:
        result = mark_next_plan_blocked(build_ready_report(), IN_PROGRESS_SESSION_REASON)

        self.assertTrue(result["changed"])
        self.assertEqual(result["report"]["nextPlan"]["status"], "blocked")
        self.assertEqual(
            result["report"]["nextPlan"]["blockedReason"],
            IN_PROGRESS_SESSION_REASON,
        )

    def test_blocked_report_can_be_resolved_later(self) -> None:
        """보류는 종결이 아니다. 세션이 끝나면 succeeded 로 풀 수 있어야 한다."""
        blocked = mark_next_plan_blocked(build_ready_report(), IN_PROGRESS_SESSION_REASON)
        result = mark_next_plan_succeeded(blocked["report"], 43)

        self.assertTrue(result["changed"])
        self.assertEqual(result["report"]["nextPlan"]["status"], "succeeded")

    def test_terminal_status_is_not_overwritten(self) -> None:
        """succeeded 를 덮어쓰면 이미 만든 계획 번호를 잃는다."""
        result = mark_next_plan_failed(
            build_ready_report(next_status="succeeded", next_plan_id=43),
            GENERATION_UNAVAILABLE_REASON,
        )

        self.assertFalse(result["changed"])
        self.assertEqual(result["report"]["nextPlan"]["studyPlanId"], 43)

    def test_non_ready_report_is_not_touched(self) -> None:
        report = build_ready_report()
        report["status"] = "pending"
        result = mark_next_plan_succeeded(report, 43)

        self.assertFalse(result["changed"])

    def test_defer_pushes_available_at(self) -> None:
        config = get_weekly_report_config()
        result = defer_next_plan(build_ready_report(), NOW, config)

        self.assertTrue(result["changed"])
        self.assertEqual(result["report"]["nextPlan"]["status"], "pending")
        deferred_at = datetime.fromisoformat(
            str(result["report"]["worker"]["availableAt"]).replace("Z", "+00:00"),
        )
        self.assertEqual(
            (deferred_at - NOW).total_seconds(),
            config.next_plan_retry_delay_seconds,
        )


class ProcessNextPlanTests(TestCase):
    """process_next_plan 의 분기 판정을 검증한다."""

    def setUp(self) -> None:
        self.config = get_weekly_report_config()

    def run_process(
        self,
        locked_plan,
        has_in_progress_session: bool = False,
        create_result=None,
        create_error=None,
    ):
        create_mock = MagicMock(return_value=create_result)
        if create_error is not None:
            create_mock.side_effect = create_error
        with patch(f"{NEXT_MODULE}.transaction"), \
                patch(f"{NEXT_MODULE}._lock_source_plan", return_value=locked_plan), \
                patch(f"{NEXT_MODULE}.StudyPlanMypage") as plan_model_mock, \
                patch(
                    f"{NEXT_MODULE}._has_in_progress_session",
                    return_value=has_in_progress_session,
                ), \
                patch(f"{NEXT_MODULE}.repository") as repository_module, \
                patch(CREATE_TARGET, create_mock):
            code = process_next_plan(77, self.config)
        self.plan_model_mock = plan_model_mock
        self.create_mock = create_mock
        return code, repository_module

    def saved_report(self, repository_module) -> dict[str, object]:
        return repository_module.save_report.call_args.args[1]

    def test_in_progress_session_blocks_generation(self) -> None:
        code, repository_module = self.run_process(
            build_locked_plan(build_ready_report()),
            has_in_progress_session=True,
        )

        self.assertEqual(code, NEXT_PLAN_BLOCKED)
        self.create_mock.assert_not_called()
        saved_next_plan = self.saved_report(repository_module)["nextPlan"]
        self.assertEqual(saved_next_plan["status"], "blocked")
        self.assertEqual(saved_next_plan["blockedReason"], IN_PROGRESS_SESSION_REASON)

    def test_generation_success_records_new_plan(self) -> None:
        code, repository_module = self.run_process(
            build_locked_plan(build_ready_report()),
            create_result={"changed": True, "studyPlan": {"studyPlanId": 43}},
        )

        self.assertEqual(code, NEXT_PLAN_SUCCEEDED)
        self.create_mock.assert_called_once_with(5, source_study_plan_id=77)
        saved_next_plan = self.saved_report(repository_module)["nextPlan"]
        self.assertEqual(saved_next_plan["status"], "succeeded")
        self.assertEqual(saved_next_plan["studyPlanId"], 43)

    def test_blocked_report_is_resolved_on_recheck(self) -> None:
        """세션이 끝난 뒤 재확인이 보류를 풀고 계획을 생성해야 한다."""
        code, repository_module = self.run_process(
            build_locked_plan(build_ready_report(next_status="blocked")),
            create_result={"changed": True, "studyPlan": {"studyPlanId": 43}},
        )

        self.assertEqual(code, NEXT_PLAN_SUCCEEDED)
        saved_next_plan = self.saved_report(repository_module)["nextPlan"]
        self.assertEqual(saved_next_plan["status"], "succeeded")

    def test_permanent_generation_error_is_closed_as_failed(self) -> None:
        code, repository_module = self.run_process(
            build_locked_plan(build_ready_report()),
            create_error=InitialStudyPlanConfigUnavailable("후보 부족"),
        )

        self.assertEqual(code, NEXT_PLAN_FAILED)
        saved_next_plan = self.saved_report(repository_module)["nextPlan"]
        self.assertEqual(saved_next_plan["status"], "failed")
        self.assertEqual(
            saved_next_plan["blockedReason"],
            GENERATION_UNAVAILABLE_REASON,
        )

    def test_recorded_plan_id_short_circuits_creation(self) -> None:
        """이미 번호가 기록돼 있으면 다시 만들지 않고 succeeded 로만 확정한다."""
        code, repository_module = self.run_process(
            build_locked_plan(build_ready_report(next_plan_id=43)),
        )

        self.assertEqual(code, NEXT_PLAN_SUCCEEDED)
        self.create_mock.assert_not_called()
        saved_next_plan = self.saved_report(repository_module)["nextPlan"]
        self.assertEqual(saved_next_plan["studyPlanId"], 43)

    def test_user_made_active_plan_is_adopted(self) -> None:
        """원본이 보관된 뒤 사용자가 먼저 만든 활성 계획을 다음 계획으로 인정한다."""
        locked_plan = build_locked_plan(build_ready_report(), plan_status="archived")

        create_mock = MagicMock()
        with patch(f"{NEXT_MODULE}.transaction"), \
                patch(f"{NEXT_MODULE}._lock_source_plan", return_value=locked_plan), \
                patch(f"{NEXT_MODULE}.StudyPlanMypage") as plan_model_mock, \
                patch(f"{NEXT_MODULE}._has_in_progress_session", return_value=False), \
                patch(f"{NEXT_MODULE}.repository") as repository_module, \
                patch(CREATE_TARGET, create_mock):
            other_active_chain = plan_model_mock.objects.filter.return_value
            other_active_chain.exclude.return_value.values_list.return_value \
                .first.return_value = 99
            code = process_next_plan(77, self.config)

        self.assertEqual(code, NEXT_PLAN_SUCCEEDED)
        create_mock.assert_not_called()
        saved_next_plan = repository_module.save_report.call_args.args[1]["nextPlan"]
        self.assertEqual(saved_next_plan["studyPlanId"], 99)

    def test_terminal_next_plan_is_ignored(self) -> None:
        code, repository_module = self.run_process(
            build_locked_plan(build_ready_report(next_status="succeeded", next_plan_id=43)),
        )

        self.assertEqual(code, NEXT_PLAN_IDLE)
        repository_module.save_report.assert_not_called()

    def test_missing_plan_is_ignored(self) -> None:
        code, repository_module = self.run_process(None)

        self.assertEqual(code, NEXT_PLAN_IDLE)
        repository_module.save_report.assert_not_called()


class RecoverPendingNextPlansTests(TestCase):
    """워커 복구 스캔이 pending 다음 계획만 다시 처리하는지 검증한다."""

    def test_pending_candidates_are_processed(self) -> None:
        from analytics.management.commands.run_weekly_report_worker import (
            recover_pending_next_plans,
        )

        module = "analytics.management.commands.run_weekly_report_worker"
        candidates = [
            {"studyPlanId": 77, "report": build_ready_report()},
            {"studyPlanId": 88, "report": build_ready_report(next_status="succeeded")},
        ]
        config = get_weekly_report_config()

        with patch(f"{module}.repository") as repository_module, \
                patch(
                    f"{module}.process_next_plan",
                    return_value=NEXT_PLAN_SUCCEEDED,
                ) as process_mock:
            repository_module.find_next_plan_candidates.return_value = candidates
            recovered_count = recover_pending_next_plans(config, MagicMock(return_value=NOW))

        self.assertEqual(recovered_count, 1)
        process_mock.assert_called_once_with(77, config)
