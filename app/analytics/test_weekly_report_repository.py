"""repository.py 테스트.

프로젝트 관례에 따라 DB 를 쓰지 않는다. 모델이 managed = False 라
테스트 러너가 테이블을 만들지 않기 때문이다. ORM 은 mock 으로 대체하고,
이 파일이 실제로 하는 일인 "무엇을 어떤 순서로 호출하는가" 를 검증한다.

실제 행 잠금과 커밋 시점은 자동 검증이 불가능하다. 설계 문서 9절의
수동 확인 체크리스트를 따른다.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone as datetime_timezone
from unittest import TestCase
from unittest.mock import MagicMock, patch

from analytics.service.weekly_report.config import get_weekly_report_config
from analytics.service.weekly_report.repository import (
    StudyPlanNotFound,
    claim_next_report,
    finish_report,
    load_report,
    retry_report,
    save_report,
)


MODULE = "analytics.service.weekly_report.repository"


def build_report(
    status: str = "pending",
    attempt_count: int = 0,
    available_at: str | None = None,
    started_at: str | None = None,
) -> dict[str, object]:
    return {
        "schemaVersion": "1",
        "status": status,
        "reportType": "weekly",
        "sourceSessionId": 11,
        "result": {"snapshotAt": "2026-07-20T00:00:00Z"},
        "content": {"comment": None, "tips": [], "fallbackUsed": False, "validation": None},
        "worker": {
            "attemptCount": attempt_count,
            "availableAt": available_at,
            "startedAt": started_at,
            "lastError": None,
        },
        "nextPlan": {"status": "pending", "studyPlanId": None, "blockedReason": None},
        "createdAt": "2026-07-20T00:00:00Z",
        "readyAt": None,
    }


def build_plan(studyplan_id: int, report: dict[str, object] | None) -> MagicMock:
    plan = MagicMock()
    plan.studyplan_id = studyplan_id
    plan.weekly_report_data = report
    return plan


class LoadReportTests(TestCase):
    def setUp(self) -> None:
        self.now = datetime(2026, 7, 26, 3, 0, tzinfo=datetime_timezone.utc)

    def _patch_queryset(self, active_plan, fallback_plans):
        """load_report 가 쓰는 두 갈래 조회를 흉내낸다.

        폴백은 후보를 순회하므로 목록을 받는다.
        """
        base = MagicMock()
        base.filter.return_value.first.return_value = active_plan
        ordered = base.filter.return_value.order_by.return_value
        ordered.__iter__.return_value = iter(list(fallback_plans))
        queryset = MagicMock()
        queryset.filter.return_value.exclude.return_value = base
        return queryset

    def test_active_plan_report_is_preferred(self) -> None:
        report = build_report(status="ready")
        queryset = self._patch_queryset(build_plan(3, report), [build_plan(1, build_report())])

        with patch(f"{MODULE}.StudyPlanMypage") as model:
            model.objects = queryset
            loaded = load_report(7)

        self.assertEqual(loaded, report)

    def test_falls_back_to_latest_plan_that_has_a_report(self) -> None:
        report = build_report(status="ready")
        queryset = self._patch_queryset(build_plan(9, None), [build_plan(4, report)])

        with patch(f"{MODULE}.StudyPlanMypage") as model:
            model.objects = queryset
            loaded = load_report(7)

        self.assertEqual(loaded, report)

    def test_no_plan_and_no_report_returns_none(self) -> None:
        queryset = self._patch_queryset(None, [])

        with patch(f"{MODULE}.StudyPlanMypage") as model:
            model.objects = queryset
            loaded = load_report(7)

        self.assertIsNone(loaded)

    def test_fallback_orders_by_plan_version_not_modified_at(self) -> None:
        """modified_at 은 워커 재시도마다 갱신되어 정렬 기준으로 쓸 수 없다."""
        queryset = self._patch_queryset(build_plan(9, None), [build_plan(4, build_report())])

        with patch(f"{MODULE}.StudyPlanMypage") as model:
            model.objects = queryset
            load_report(7)

        base = queryset.filter.return_value.exclude.return_value
        base.filter.return_value.order_by.assert_called_once_with(
            "-plan_version",
            "-studyplan_id",
        )


class SaveReportTests(TestCase):
    def test_saves_only_report_and_modified_at(self) -> None:
        report = build_report()

        with patch(f"{MODULE}.StudyPlanMypage") as model:
            model.objects.filter.return_value.update.return_value = 1
            save_report(5, report)

        model.objects.filter.assert_called_once_with(studyplan_id=5)
        update_kwargs = model.objects.filter.return_value.update.call_args.kwargs
        self.assertEqual(set(update_kwargs), {"weekly_report_data", "modified_at"})
        self.assertEqual(update_kwargs["weekly_report_data"], report)

    def test_non_mapping_is_rejected_before_reaching_the_database(self) -> None:
        with patch(f"{MODULE}.StudyPlanMypage") as model:
            with self.assertRaises(TypeError):
                save_report(5, ["not", "an", "object"])

        model.objects.filter.assert_not_called()

    def test_missing_plan_raises(self) -> None:
        with patch(f"{MODULE}.StudyPlanMypage") as model:
            model.objects.filter.return_value.update.return_value = 0
            with self.assertRaises(StudyPlanNotFound):
                save_report(404, build_report())


class ClaimNextReportTests(TestCase):
    def setUp(self) -> None:
        self.now = datetime(2026, 7, 26, 3, 0, tzinfo=datetime_timezone.utc)
        self.config = get_weekly_report_config()

    def _patch_claim_queryset(self, plan, extra_plans=()):
        """후보 목록을 훑는 구조를 흉내낸다."""
        candidates = [] if plan is None else [plan, *extra_plans]
        queryset = MagicMock()
        chain = queryset.filter.return_value.order_by.return_value
        chain.select_for_update.return_value.__getitem__.return_value = candidates
        return queryset, chain

    def test_no_pending_report_returns_none(self) -> None:
        queryset, _ = self._patch_claim_queryset(None)

        with patch(f"{MODULE}.StudyPlanMypage") as model, patch(f"{MODULE}.save_report") as save:
            model.objects = queryset
            claimed = claim_next_report(self.now, self.config)

        self.assertIsNone(claimed)
        save.assert_not_called()

    def test_due_pending_report_is_claimed_and_saved(self) -> None:
        plan = build_plan(12, build_report(status="pending", attempt_count=0))
        queryset, _ = self._patch_claim_queryset(plan)

        with patch(f"{MODULE}.StudyPlanMypage") as model, patch(f"{MODULE}.save_report") as save:
            model.objects = queryset
            claimed = claim_next_report(self.now, self.config)

        self.assertIsNotNone(claimed)
        self.assertEqual(claimed["studyPlanId"], 12)
        saved_report = save.call_args.args[1]
        self.assertEqual(saved_report["status"], "running")
        self.assertEqual(saved_report["worker"]["attemptCount"], 1)
        self.assertIsNotNone(saved_report["worker"]["startedAt"])

    def test_future_available_at_is_not_claimed(self) -> None:
        available_at = (self.now + timedelta(minutes=5)).isoformat().replace("+00:00", "Z")
        plan = build_plan(12, build_report(available_at=available_at))
        queryset, _ = self._patch_claim_queryset(plan)

        with patch(f"{MODULE}.StudyPlanMypage") as model, patch(f"{MODULE}.save_report") as save:
            model.objects = queryset
            claimed = claim_next_report(self.now, self.config)

        self.assertIsNone(claimed)
        save.assert_not_called()

    def test_stuck_running_report_is_reset_but_not_claimed(self) -> None:
        started_at = (self.now - timedelta(seconds=600)).isoformat().replace("+00:00", "Z")
        plan = build_plan(
            12,
            build_report(status="running", attempt_count=1, started_at=started_at),
        )
        queryset, _ = self._patch_claim_queryset(plan)

        with patch(f"{MODULE}.StudyPlanMypage") as model, patch(f"{MODULE}.save_report") as save:
            model.objects = queryset
            claimed = claim_next_report(self.now, self.config)

        self.assertIsNone(claimed)
        saved_report = save.call_args.args[1]
        self.assertEqual(saved_report["status"], "pending")

    def test_lock_skips_rows_held_by_other_workers(self) -> None:
        plan = build_plan(12, build_report())
        queryset, chain = self._patch_claim_queryset(plan)

        with patch(f"{MODULE}.StudyPlanMypage") as model, patch(f"{MODULE}.save_report"):
            model.objects = queryset
            claim_next_report(self.now, self.config)

        queryset.filter.assert_called_once_with(
            weekly_report_data__status__in=("pending", "running"),
        )
        queryset.filter.return_value.order_by.assert_called_once_with("studyplan_id")
        chain.select_for_update.assert_called_once_with(skip_locked=True)


class FinishAndRetryTests(TestCase):
    def setUp(self) -> None:
        self.now = datetime(2026, 7, 26, 3, 0, tzinfo=datetime_timezone.utc)
        self.config = get_weekly_report_config()
        self.content = {
            "comment": {"text": "이번 주 흐름이 좋아요.", "evidenceIds": []},
            "tips": [],
            "fallbackUsed": False,
            "validation": {"guard": "passed", "validator": "passed"},
        }

    def test_finish_marks_ready(self) -> None:
        running = build_report(status="running", attempt_count=1, started_at="2026-07-26T02:59:00Z")

        with patch(f"{MODULE}._lock_report", return_value=running), \
             patch(f"{MODULE}.save_report") as save:
            changed = finish_report(12, 1, self.content, self.now)

        self.assertTrue(changed)
        saved_report = save.call_args.args[1]
        self.assertEqual(saved_report["status"], "ready")
        self.assertEqual(saved_report["content"], self.content)
        self.assertIsNotNone(saved_report["readyAt"])

    def test_late_finish_cannot_overwrite_a_newer_attempt(self) -> None:
        running = build_report(status="running", attempt_count=2, started_at="2026-07-26T02:59:00Z")

        with patch(f"{MODULE}._lock_report", return_value=running), \
             patch(f"{MODULE}.save_report") as save:
            changed = finish_report(12, 1, self.content, self.now)

        self.assertFalse(changed)
        save.assert_not_called()

    def test_retry_uses_configured_backoff(self) -> None:
        running = build_report(status="running", attempt_count=1, started_at="2026-07-26T02:59:00Z")

        with patch(f"{MODULE}._lock_report", return_value=running), \
             patch(f"{MODULE}.save_report") as save:
            changed = retry_report(12, 1, "AI_FALLBACK", self.now, self.config)

        self.assertTrue(changed)
        saved_report = save.call_args.args[1]
        self.assertEqual(saved_report["status"], "pending")
        self.assertEqual(saved_report["worker"]["lastError"], "AI_FALLBACK")
        expected_available_at = self.now + timedelta(
            seconds=self.config.retry_delays_seconds[0],
        )
        self.assertEqual(
            saved_report["worker"]["availableAt"],
            expected_available_at.isoformat().replace("+00:00", "Z"),
        )

    def test_missing_report_is_not_an_error(self) -> None:
        with patch(f"{MODULE}._lock_report", return_value=None), \
             patch(f"{MODULE}.save_report") as save:
            self.assertFalse(finish_report(12, 1, self.content, self.now))
            self.assertFalse(retry_report(12, 1, "AI_FALLBACK", self.now, self.config))

        save.assert_not_called()


class ClaimQueueTests(TestCase):
    """한 건이 막혀도 뒤에 있는 건이 처리되어야 한다."""

    def setUp(self) -> None:
        self.now = datetime(2026, 7, 26, 3, 0, tzinfo=datetime_timezone.utc)
        self.config = get_weekly_report_config()

    def patch_candidates(self, plans):
        queryset = MagicMock()
        chain = queryset.filter.return_value.order_by.return_value
        chain.select_for_update.return_value.__getitem__.return_value = plans
        return queryset

    def test_backed_off_report_does_not_block_the_next_one(self) -> None:
        blocked_at = (self.now + timedelta(minutes=5)).isoformat().replace("+00:00", "Z")
        blocked = build_plan(10, build_report(available_at=blocked_at))
        ready = build_plan(11, build_report())
        queryset = self.patch_candidates([blocked, ready])

        with patch(f"{MODULE}.StudyPlanMypage") as model, patch(f"{MODULE}.save_report"):
            model.objects = queryset
            claimed = claim_next_report(self.now, self.config)

        self.assertIsNotNone(claimed)
        self.assertEqual(claimed["studyPlanId"], 11)

    def test_running_report_held_by_another_worker_is_skipped(self) -> None:
        started_at = self.now.isoformat().replace("+00:00", "Z")
        running = build_plan(
            10,
            build_report(status="running", attempt_count=1, started_at=started_at),
        )
        ready = build_plan(11, build_report())
        queryset = self.patch_candidates([running, ready])

        with patch(f"{MODULE}.StudyPlanMypage") as model, patch(f"{MODULE}.save_report"):
            model.objects = queryset
            claimed = claim_next_report(self.now, self.config)

        self.assertEqual(claimed["studyPlanId"], 11)

    def test_broken_payload_is_skipped_not_fatal(self) -> None:
        broken = build_plan(10, None)
        ready = build_plan(11, build_report())
        queryset = self.patch_candidates([broken, ready])

        with patch(f"{MODULE}.StudyPlanMypage") as model, patch(f"{MODULE}.save_report"):
            model.objects = queryset
            claimed = claim_next_report(self.now, self.config)

        self.assertEqual(claimed["studyPlanId"], 11)

    def test_nothing_claimable_returns_none(self) -> None:
        blocked_at = (self.now + timedelta(minutes=5)).isoformat().replace("+00:00", "Z")
        queryset = self.patch_candidates([build_plan(10, build_report(available_at=blocked_at))])

        with patch(f"{MODULE}.StudyPlanMypage") as model, patch(f"{MODULE}.save_report"):
            model.objects = queryset
            self.assertIsNone(claim_next_report(self.now, self.config))


class LoadReportFallbackTests(TestCase):
    def test_non_dict_payload_does_not_hide_an_older_good_report(self) -> None:
        good = build_report(status="ready")
        base = MagicMock()
        base.filter.return_value.first.return_value = None
        ordered = base.filter.return_value.order_by.return_value
        ordered.__iter__.return_value = iter([build_plan(9, ["잘못된 값"]), build_plan(4, good)])
        queryset = MagicMock()
        queryset.filter.return_value.exclude.return_value = base

        with patch(f"{MODULE}.StudyPlanMypage") as model:
            model.objects = queryset
            self.assertEqual(load_report(7), good)
