from __future__ import annotations

from datetime import datetime, timezone
from unittest import TestCase
from unittest.mock import Mock

from analytics.service.study_plan.planner import PriorityTarget
from analytics.service.weekly_report.llm import generate_report_content, validate_ai_content
from analytics.service.weekly_report.service import (
    build_fallback_content,
    build_pending_report,
    build_report_result,
    render_report_dto,
)
from analytics.service.weekly_report.worker import (
    claim_report,
    complete_report,
    is_next_plan_recovery_candidate,
    schedule_report_retry,
)


class WeeklyReportAnalysisTests(TestCase):
    def setUp(self) -> None:
        self.snapshot_at = datetime(2026, 7, 20, 12, 0, tzinfo=timezone.utc)
        self.assessment = {
            "score": 82,
            "totalScore": 100,
            "correctCount": 41,
            "totalCount": 50,
        }
        self.progress = {
            "completedLearningBlocks": 10,
            "totalLearningBlocks": 12,
            "completionRate": 0.833,
        }

    def test_result_selects_deterministic_evidence(self) -> None:
        weakness_rows = [
            self._weakness("improved-b", "개선 B", "NEUTRAL", 0.3, "improving", -0.2, 7),
            self._weakness("improved-a", "개선 A", "STABLE", 0.1, "improving", -0.2, 9),
            self._weakness("weak-a", "취약 A", "WEAK", 0.8, "flat", 0.0, 8),
            self._weakness("weak-b", "취약 B", "WEAK", 0.7, "worsening", 0.2, 12),
        ]
        priorities = [self._priority("target-b", "대상 B", 0.7), self._priority("target-a", "대상 A", 0.9)]
        time_records = [
            *[{"qType": "사료", "timeSpentMs": 200_000} for _ in range(5)],
            *[{"qType": "개념", "timeSpentMs": 50_000} for _ in range(5)],
        ]

        report = build_report_result(
            self.assessment,
            {"type": "weekly_review", "sessionId": 98, "score": 76},
            self.progress,
            weakness_rows,
            {"weak-a": 0.4, "weak-b": 0.9},
            time_records,
            priorities,
            self.snapshot_at,
            False,
            "personalized",
            True,
        )
        result = report["result"]

        self.assertEqual(report["reportType"], "weekly")
        self.assertEqual([item["groupKeyId"] for item in result["strengths"]], ["improved-a", "improved-b"])
        self.assertEqual([item["groupKeyId"] for item in result["priorityImprovements"]], ["weak-a", "weak-b"])
        self.assertEqual(result["timeSummary"][0]["qType"], "사료")
        self.assertEqual([item["groupKeyId"] for item in result["nextPlanTargets"]], ["target-a", "target-b"])
        self.assertEqual(result["comparison"]["scoreChange"], 6.0)

    def test_no_baseline_and_no_previous_weekly_is_first_week(self) -> None:
        report = build_report_result(
            self.assessment,
            None,
            self.progress,
            [],
            {},
            [],
            [],
            self.snapshot_at,
            True,
            None,
            False,
        )

        self.assertEqual(report["reportType"], "first_week")
        self.assertEqual(report["result"]["comparison"]["status"], "INSUFFICIENT_BASELINE")
        self.assertIsNone(report["result"]["comparison"]["scoreChange"])

    def test_time_summary_requires_type_and_reference_samples(self) -> None:
        records = [
            *[{"qType": "사료", "timeSpentMs": 200_000} for _ in range(4)],
            *[{"qType": "개념", "timeSpentMs": 50_000} for _ in range(6)],
        ]
        report = build_report_result(
            self.assessment,
            None,
            self.progress,
            [],
            {},
            records,
            [],
            self.snapshot_at,
            False,
            "personalized",
            True,
        )

        self.assertEqual(report["result"]["timeSummary"], [])

    def test_pending_report_matches_compact_storage_contract(self) -> None:
        result = {"assessment": self.assessment, "recoveredSnapshot": False}
        report = build_pending_report(123, "first_week", result, self.snapshot_at)

        self.assertEqual(report["status"], "pending")
        self.assertEqual(report["worker"]["attemptCount"], 0)
        self.assertEqual(report["nextPlan"]["status"], "pending")
        self.assertEqual(report["createdAt"], "2026-07-20T12:00:00Z")

    def test_completion_rate_outside_zero_to_one_is_rejected(self) -> None:
        invalid_progress = {**self.progress, "completionRate": 83.3}

        with self.assertRaises(ValueError):
            build_report_result(
                self.assessment,
                None,
                invalid_progress,
                [],
                {},
                [],
                [],
                self.snapshot_at,
                False,
                "personalized",
                True,
            )

    def test_renderer_converts_completion_to_percent_and_escapes_ai_text(self) -> None:
        report = {
            "status": "ready",
            "reportType": "weekly",
            "result": {
                "assessment": self.assessment,
                "comparison": {"status": "AVAILABLE", "scoreChange": 6},
                "planProgress": self.progress,
            },
            "content": {
                "comment": {"text": "<script>alert(1)</script>", "evidenceIds": []},
                "tips": [],
            },
            "nextPlan": {"status": "pending"},
        }

        rendered = render_report_dto(report)

        self.assertEqual(rendered["completionSummary"], "83.3%")
        self.assertNotIn("<script>", rendered["comment"]["text"])

    def _weakness(
        self,
        group_key_id: str,
        label: str,
        status: str,
        score: float,
        trend: str,
        trend_delta: float,
        effective_total: float,
    ) -> dict[str, object]:
        return {
            "groupKeyId": group_key_id,
            "label": label,
            "status": status,
            "weaknessScore": score,
            "trend": trend,
            "trendDelta": trend_delta,
            "recentScore": 0.2,
            "previousScore": 0.4,
            "raw": {"total": 10},
            "effective": {"total": effective_total},
        }

    def _priority(
        self,
        group_key_id: str,
        label: str,
        priority_score: float,
    ) -> PriorityTarget:
        return PriorityTarget(
            group_key_id=group_key_id,
            label=label,
            era="조선",
            topic="정치",
            q_type="",
            weakness_score=0.5,
            weakness_status="WEAK",
            trend="flat",
            effective_total=5,
            exam_weight=0.5,
            repeated_error=0.5,
            average_seconds_per_question=100,
            priority_score=priority_score,
            generation_reason="personalized",
        )


class WeeklyReportLlmGuardTests(TestCase):
    def setUp(self) -> None:
        self.result = {
            "assessment": {"score": 82, "totalScore": 100},
            "strengths": [
                {"evidenceId": "strength-1", "label": "조선 정치", "sampleCount": 10}
            ],
            "priorityImprovements": [
                {"evidenceId": "priority-1", "label": "근현대 사회", "sampleCount": 8}
            ],
            "timeSummary": [],
            "nextPlanTargets": [],
        }
        self.valid_content = {
            "comment": {
                "text": "조선 정치 영역에서 개선 흐름이 확인됐어요.",
                "evidenceIds": ["strength-1"],
            },
            "tips": [
                {
                    "text": "근현대 사회 문제를 짧게 나누어 풀어 보세요.",
                    "evidenceIds": ["priority-1"],
                }
            ],
        }

    def test_guard_accepts_supported_content(self) -> None:
        self.assertEqual(validate_ai_content(self.valid_content, self.result), [])

    def test_guard_rejects_unknown_evidence_number_and_forbidden_phrase(self) -> None:
        candidate = {
            "comment": {
                "text": "점수가 999점이며 반드시 합격합니다.",
                "evidenceIds": ["unknown-1"],
            },
            "tips": [],
        }
        errors = validate_ai_content(candidate, self.result)

        self.assertIn("COMMENT_UNKNOWN_EVIDENCE", errors)
        self.assertIn("COMMENT_UNSUPPORTED_NUMBER", errors)
        self.assertIn("COMMENT_FORBIDDEN_PHRASE", errors)

    def test_guard_rejects_unknown_output_field(self) -> None:
        candidate = {**self.valid_content, "score": 100}

        self.assertIn("UNKNOWN_FIELD", validate_ai_content(candidate, self.result))

    def test_guard_failure_uses_fallback_without_validator_call(self) -> None:
        writer = Mock(
            return_value={
                "comment": {"text": "근거 없음", "evidenceIds": ["unknown"]},
                "tips": [],
            }
        )
        validator = Mock(return_value=True)

        content = generate_report_content(self.result, writer, validator)

        self.assertTrue(content["fallbackUsed"])
        validator.assert_not_called()

    def test_unexpected_writer_error_still_uses_fallback(self) -> None:
        writer = Mock(side_effect=Exception("provider error"))
        validator = Mock(return_value=True)

        content = generate_report_content(self.result, writer, validator)

        self.assertTrue(content["fallbackUsed"])
        validator.assert_not_called()

    def test_validator_failure_uses_fallback(self) -> None:
        writer = Mock(return_value=self.valid_content)
        validator = Mock(return_value=False)

        content = generate_report_content(self.result, writer, validator)

        self.assertTrue(content["fallbackUsed"])
        writer.assert_called_once()
        validator.assert_called_once()

    def test_writer_and_validator_each_run_once_on_success(self) -> None:
        writer = Mock(return_value=self.valid_content)
        validator = Mock(return_value=True)

        content = generate_report_content(self.result, writer, validator)

        self.assertFalse(content["fallbackUsed"])
        writer.assert_called_once()
        validator.assert_called_once()

    def test_fallback_uses_available_evidence_and_tip_limit(self) -> None:
        content = build_fallback_content(self.result)

        self.assertTrue(content["fallbackUsed"])
        self.assertEqual(content["comment"]["evidenceIds"], ["strength-1"])
        self.assertLessEqual(len(content["tips"]), 3)


class WeeklyReportWorkerStateTests(TestCase):
    def setUp(self) -> None:
        self.now = datetime(2026, 7, 20, 12, 0, tzinfo=timezone.utc)
        self.report = build_pending_report(
            source_session_id=123,
            report_type="weekly",
            result={"assessment": {"score": 80}},
            created_at=self.now,
        )

    def test_due_pending_report_is_claimed_once(self) -> None:
        claimed = claim_report(self.report, self.now)
        duplicate = claim_report(claimed["report"], self.now)

        self.assertTrue(claimed["claimed"])
        self.assertEqual(claimed["report"]["status"], "running")
        self.assertEqual(claimed["report"]["worker"]["attemptCount"], 1)
        self.assertFalse(duplicate["claimed"])

    def test_retry_uses_configured_backoff(self) -> None:
        claimed = claim_report(self.report, self.now)["report"]
        retry = schedule_report_retry(claimed, 1, "LLM_TIMEOUT", self.now)

        self.assertEqual(retry["report"]["status"], "pending")
        self.assertEqual(retry["report"]["worker"]["availableAt"], "2026-07-20T12:00:30Z")
        self.assertFalse(claim_report(retry["report"], self.now)["claimed"])

    def test_stuck_running_report_is_recovered(self) -> None:
        claimed = claim_report(self.report, self.now)["report"]
        recovered = claim_report(claimed, self.now.replace(minute=6))

        self.assertTrue(recovered["changed"])
        self.assertEqual(recovered["report"]["status"], "pending")
        self.assertEqual(recovered["report"]["worker"]["lastError"], "WORKER_STUCK")

    def test_max_attempt_report_fails(self) -> None:
        running = claim_report(self.report, self.now)["report"]
        running["worker"]["attemptCount"] = 3
        failed = schedule_report_retry(running, 3, "LLM_TIMEOUT", self.now)

        self.assertEqual(failed["report"]["status"], "failed")

    def test_late_completion_cannot_overwrite_new_attempt(self) -> None:
        running = claim_report(self.report, self.now)["report"]
        running["worker"]["attemptCount"] = 2
        completed = complete_report(
            running,
            expected_attempt_count=1,
            content={"fallbackUsed": False},
            now=self.now,
        )

        self.assertFalse(completed["changed"])
        self.assertEqual(completed["report"]["status"], "running")

    def test_completion_marks_ready(self) -> None:
        running = claim_report(self.report, self.now)["report"]
        completed = complete_report(
            running,
            expected_attempt_count=1,
            content={"fallbackUsed": True},
            now=self.now,
        )

        self.assertTrue(completed["changed"])
        self.assertEqual(completed["report"]["status"], "ready")

    def test_next_plan_recovery_is_separate_ready_branch(self) -> None:
        ready = dict(self.report)
        ready["status"] = "ready"

        self.assertTrue(is_next_plan_recovery_candidate(ready, self.now))
