"""주간 리포트 전체 흐름 통합 테스트.

주간복습 완료 → pending 저장 → 워커가 집음 → 가짜 AI → ready → 화면 DTO
까지 실제 코드로 이어지는지 확인한다. LLM 은 부르지 않는다.

DB 대신 weekly_report_data 한 칸을 흉내내는 메모리 저장소를 쓴다.
모델이 managed = False 라 테스트 러너가 테이블을 만들지 못하기 때문이다.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone as datetime_timezone
from unittest import TestCase
from unittest.mock import MagicMock, patch

from analytics.management.commands.run_weekly_report_worker import (
    IDLE,
    READY,
    RETRIED,
    process_one_report,
)
from analytics.service.study_plan.planner import PriorityTarget
from analytics.service.taxonomy import build_group_key_id
from analytics.service.weekly_report import collector, repository
from analytics.service.weekly_report.config import get_weekly_report_config
from analytics.service.weekly_report.service import render_report_dto


COLLECTOR = "analytics.service.weekly_report.collector"
WORKER = "analytics.management.commands.run_weekly_report_worker"

TODAY = date(2026, 7, 26)
STUDY_PLAN_ID = 77
SESSION_ID = 30
USER_ID = 1
JOSEON_POLITICS = build_group_key_id({"era": "조선", "topic": "정치"})
# 리포트 생성 시각을 고정한다. 고정하지 않으면 availableAt 이 실제 현재 시각이 되어
# 아래 가짜 시계보다 미래가 되고, 워커가 영원히 idle 을 돌려준다.
CREATED_AT = datetime(2026, 7, 26, 2, 0, tzinfo=datetime_timezone.utc)

# 핵심 개념 취약 판정 결과 (build_weakness_rows 출력 형태)
CONCEPT_ROWS = [
    {
        "groupKeyId": build_group_key_id({"coreConcept": "붕당 정치"}),
        "label": "붕당 정치",
        "status": "WEAK",
        "trend": "worsening",
        "trendDelta": 0.15,
        "weaknessScore": 0.72,
        "raw": {"total": 9, "wrong": 7, "wrongRate": 0.7778, "averageTimeSec": 81},
        "effective": {"total": 6.2, "wrong": 4.8},
    },
]

# ml_trend_top5 의 최근 5회차 실제 TOP5
EXAM_TRENDS = [
    {
        "rank": 1,
        "groupKeyId": build_group_key_id({"era": "개항기", "topic": "사건"}),
        "label": "개항기 + 사건",
        "questionCount": 22,
        "ratioPercent": 8.8,
        "recentRounds": "66~70",
    },
    {
        "rank": 2,
        "groupKeyId": JOSEON_POLITICS,
        "label": "조선 + 정치",
        "questionCount": 18,
        "ratioPercent": 7.2,
        "recentRounds": "66~70",
    },
]


class ReportStore:
    """weekly_report_data 한 칸을 흉내내는 메모리 저장소."""

    def __init__(self) -> None:
        self.report: dict[str, object] | None = None
        self.save_count = 0

    def save_report(self, study_plan_id: int, report) -> None:
        self.report = dict(report)
        self.save_count += 1

    def load_report(self, user_id: int):
        return self.report

    def claim_next_report(self, now: datetime, config):
        from analytics.service.weekly_report.worker import claim_report

        if self.report is None:
            return None
        claim_result = claim_report(self.report, now, config)
        if claim_result["changed"]:
            self.save_report(STUDY_PLAN_ID, claim_result["report"])
        if not claim_result["claimed"]:
            return None
        return {"studyPlanId": STUDY_PLAN_ID, "report": claim_result["report"]}

    def finish_report(self, study_plan_id, expected_attempt_count, content, now) -> bool:
        from analytics.service.weekly_report.worker import complete_report

        finish_result = complete_report(self.report, expected_attempt_count, content, now)
        if not finish_result["changed"]:
            return False
        self.save_report(study_plan_id, finish_result["report"])
        return True

    def retry_report(self, study_plan_id, expected_attempt_count, error_code, now, config) -> bool:
        from analytics.service.weekly_report.worker import schedule_report_retry

        retry_result = schedule_report_retry(
            self.report,
            expected_attempt_count,
            error_code,
            now,
            config,
        )
        if not retry_result["changed"]:
            return False
        self.save_report(study_plan_id, retry_result["report"])
        return True


def build_priority_target(repeated_error: float = 0.6) -> PriorityTarget:
    return PriorityTarget(
        group_key_id=JOSEON_POLITICS,
        label="조선 · 정치",
        era="조선",
        topic="정치",
        q_type="",
        weakness_score=0.7,
        weakness_status="WEAK",
        trend="worsening",
        effective_total=4.0,
        exam_weight=1.0,
        repeated_error=repeated_error,
        average_seconds_per_question=74,
        priority_score=0.83,
        generation_reason="personalized",
    )


def build_weakness_row() -> dict[str, object]:
    return {
        "groupKeyId": JOSEON_POLITICS,
        "label": "조선 · 정치",
        "status": "WEAK",
        "trend": "worsening",
        "trendDelta": 0.2,
        "weaknessScore": 0.7,
        "recentScore": 0.7,
        "previousScore": 0.5,
        "raw": {"total": 12, "wrong": 9, "averageTimeSec": 74},
        "effective": {"total": 8.0, "wrong": 6.0},
    }


def ai_content(fallback_used: bool) -> dict[str, object]:
    return {
        "comment": {"text": "조선 정치 영역을 먼저 보완해 보세요.", "evidenceIds": ["priority-1"]},
        "tips": [{"text": "짧은 단위로 나누어 풀어 보세요.", "evidenceIds": ["priority-1"]}],
        "fallbackUsed": fallback_used,
        "validation": {"guard": "passed", "validator": "passed"},
    }


class WeeklyReportFlowTests(TestCase):
    def setUp(self) -> None:
        self.config = get_weekly_report_config()
        self.store = ReportStore()
        self.clock_times = [
            datetime(2026, 7, 26, 3, 0, tzinfo=datetime_timezone.utc),
            datetime(2026, 7, 26, 3, 1, tzinfo=datetime_timezone.utc),
            datetime(2026, 7, 26, 3, 2, tzinfo=datetime_timezone.utc),
            datetime(2026, 7, 26, 3, 3, tzinfo=datetime_timezone.utc),
            datetime(2026, 7, 26, 3, 4, tzinfo=datetime_timezone.utc),
            datetime(2026, 7, 26, 3, 5, tzinfo=datetime_timezone.utc),
        ]

    def trigger(self, source_session_id: int = SESSION_ID) -> bool:
        """주간복습 완료 시점을 흉내낸다."""
        with patch(f"{COLLECTOR}._load_plan_report", side_effect=lambda _: self.store.report), \
                patch(f"{COLLECTOR}.timezone") as collector_timezone, \
                patch(f"{COLLECTOR}.repository", self.store), \
                patch(f"{COLLECTOR}.build_assessment", return_value={
                    "sessionId": source_session_id,
                    "score": 74,
                    "totalScore": 100,
                    "questionCount": 50,
                }), \
                patch(f"{COLLECTOR}.build_baseline", return_value={
                    "sessionId": 20,
                    "score": 62,
                    "totalScore": 100,
                    "type": "weekly_review",
                }), \
                patch(f"{COLLECTOR}.build_plan_progress", return_value={
                    "targetCount": 24,
                    "achievedCount": 18,
                    "completionRate": 0.75,
                }), \
                patch(f"{COLLECTOR}.build_weakness_rows", return_value=[build_weakness_row()]), \
                patch(f"{COLLECTOR}._completed_records", return_value=[]), \
                patch(f"{COLLECTOR}.collect_time_records", return_value=[]), \
                patch(
                    f"{COLLECTOR}._build_priority_targets_or_empty",
                    return_value=[build_priority_target()],
                ), \
                patch(f"{COLLECTOR}.collect_concept_weakness_rows", return_value=CONCEPT_ROWS), \
                patch(f"{COLLECTOR}.collect_exam_trends", return_value=EXAM_TRENDS), \
                patch(f"{COLLECTOR}.has_previous_weekly_review", return_value=True):
            collector_timezone.now.return_value = CREATED_AT
            collector_timezone.localdate.return_value = TODAY
            return collector.enqueue_weekly_report(
                USER_ID,
                source_session_id,
                STUDY_PLAN_ID,
                today=TODAY,
            )

    def run_worker(self, generator) -> str:
        clock = MagicMock(side_effect=self.clock_times)
        with patch(f"{WORKER}.repository", self.store):
            return process_one_report(self.config, generator, clock)

    def view_dto(self):
        with patch.object(repository, "load_report", self.store.load_report):
            report = self.store.load_report(USER_ID)
            if report is None:
                return None
            return render_report_dto(report)

    def test_full_flow_reaches_the_screen(self) -> None:
        self.assertTrue(self.trigger())
        self.assertEqual(self.store.report["status"], "pending")

        code = self.run_worker(MagicMock(return_value=ai_content(fallback_used=False)))

        self.assertEqual(code, READY)
        self.assertEqual(self.store.report["status"], "ready")

        dto = self.view_dto()
        self.assertEqual(dto["status"], "ready")
        self.assertEqual(dto["reportType"], "weekly")
        self.assertEqual(dto["scoreSummary"], "74/100")
        self.assertEqual(dto["comparisonSummary"], "12점 상승")
        self.assertEqual(dto["completionSummary"], "75.0%")
        self.assertIn("조선 정치", dto["comment"]["text"])

    def test_repeated_error_survives_the_whole_flow(self) -> None:
        """collector 가 만든 근거가 화면까지 값을 잃지 않는지 확인한다."""
        self.trigger()
        improvements = self.store.report["result"]["priorityImprovements"]

        self.assertEqual(improvements[0]["groupKeyId"], JOSEON_POLITICS)
        self.assertEqual(improvements[0]["repeatedError"], 0.6)

    def test_fallback_retries_then_confirms_on_the_last_attempt(self) -> None:
        self.trigger()
        fallback_generator = MagicMock(return_value=ai_content(fallback_used=True))

        self.assertEqual(self.run_worker(fallback_generator), RETRIED)
        self.assertEqual(self.store.report["status"], "pending")
        self.assertEqual(self.store.report["worker"]["attemptCount"], 1)

        self._advance_past_backoff()
        self.assertEqual(self.run_worker(fallback_generator), RETRIED)
        self.assertEqual(self.store.report["worker"]["attemptCount"], 2)

        self._advance_past_backoff()
        self.assertEqual(self.run_worker(fallback_generator), READY)
        self.assertEqual(self.store.report["status"], "ready")
        self.assertTrue(self.store.report["content"]["fallbackUsed"])

        dto = self.view_dto()
        self.assertEqual(dto["status"], "ready")
        self.assertIsNotNone(dto["comment"])

    def test_backoff_actually_delays_the_next_claim(self) -> None:
        """claim 시각을 재사용하면 이 테스트가 깨진다."""
        self.trigger()
        self.run_worker(MagicMock(return_value=ai_content(fallback_used=True)))

        self.clock_times = [datetime(2026, 7, 26, 3, 1, 10, tzinfo=datetime_timezone.utc)] * 4
        self.assertEqual(self.run_worker(MagicMock()), IDLE)

    def test_resubmitting_the_same_session_does_not_reset_a_ready_report(self) -> None:
        self.trigger()
        self.run_worker(MagicMock(return_value=ai_content(fallback_used=False)))
        save_count_before = self.store.save_count

        self.assertFalse(self.trigger())
        self.assertEqual(self.store.report["status"], "ready")
        self.assertEqual(self.store.save_count, save_count_before)

    def test_next_week_replaces_the_previous_report(self) -> None:
        self.trigger()
        self.run_worker(MagicMock(return_value=ai_content(fallback_used=False)))

        self.assertTrue(self.trigger(source_session_id=SESSION_ID + 7))
        self.assertEqual(self.store.report["status"], "pending")
        self.assertEqual(self.store.report["sourceSessionId"], SESSION_ID + 7)

    def test_no_report_shows_nothing_on_the_screen(self) -> None:
        self.assertIsNone(self.view_dto())

    def _advance_past_backoff(self) -> None:
        last_time = self.clock_times[-1]
        self.clock_times = [last_time + timedelta(minutes=index + 1) for index in range(6)]
