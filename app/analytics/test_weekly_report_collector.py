"""collector.py 테스트.

프로젝트 관례에 따라 DB 를 쓰지 않는다. ORM 은 mock 으로 대체하고,
근거 선별 규칙은 합성 입력으로 직접 검증한다.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date
from unittest import TestCase
from unittest.mock import MagicMock, patch

from analytics.service.study_plan.planner import PriorityTarget
from analytics.service.taxonomy import build_group_key_id
from analytics.service.weakness import build_weakness_rows
from analytics.service.weekly_report import collector
from analytics.service.weekly_report.config import get_weekly_report_config
from analytics.service.weekly_report.service import build_report_result


MODULE = "analytics.service.weekly_report.collector"
TODAY = date(2026, 7, 26)

JOSEON_POLITICS = build_group_key_id({"era": "조선", "topic": "정치"})


def build_session(session_id: int, recorded_date: date, user_id: int = 1) -> MagicMock:
    session = MagicMock()
    session.session_id = session_id
    session.recorded_date = recorded_date
    session.user_id = user_id
    return session


def build_priority_target(
    group_key_id: str,
    repeated_error: float,
    generation_reason: str = "personalized",
) -> PriorityTarget:
    return PriorityTarget(
        group_key_id=group_key_id,
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
        generation_reason=generation_reason,
    )


class ScoreSummaryTests(TestCase):
    """점수는 문항 개수가 아니라 가중 점수다."""

    def run_summary(self, aggregate: dict[str, object], session=None):
        with patch(f"{MODULE}.SolveSessions") as sessions_model, \
                patch(f"{MODULE}.SolveRecords") as records_model:
            sessions_model.objects.filter.return_value.first.return_value = (
                session if session is not None else build_session(9, date(2026, 7, 25))
            )
            records_model.objects.filter.return_value.aggregate.return_value = aggregate
            return collector._score_summary(9)

    def test_score_is_weighted_not_a_question_count(self) -> None:
        summary = self.run_summary(
            {"record_count": 50, "max_score": 100, "earned_score": 74},
        )

        self.assertEqual(summary["score"], 74)
        self.assertEqual(summary["totalScore"], 100)
        self.assertEqual(summary["questionCount"], 50)

    def test_no_records_returns_none(self) -> None:
        summary = self.run_summary(
            {"record_count": 0, "max_score": None, "earned_score": None},
        )

        self.assertIsNone(summary)

    def test_all_wrong_gives_zero_score_but_keeps_max(self) -> None:
        summary = self.run_summary(
            {"record_count": 50, "max_score": 100, "earned_score": None},
        )

        self.assertEqual(summary["score"], 0)
        self.assertEqual(summary["totalScore"], 100)

    def test_missing_session_raises_from_build_assessment(self) -> None:
        with patch(f"{MODULE}._score_summary", return_value=None):
            with self.assertRaises(collector.WeeklyReportSourceUnavailable):
                collector.build_assessment(9)


class BaselineTests(TestCase):
    def setUp(self) -> None:
        self.source = build_session(30, date(2026, 7, 26))

    def patch_sessions(self, weekly_sessions, diagnostic_sessions):
        diagnostic_queryset = MagicMock()
        diagnostic_queryset.exclude.return_value = diagnostic_sessions
        return patch.multiple(
            "analytics.service.analytics",
            get_completed_weekly_review_sessions=MagicMock(return_value=weekly_sessions),
            get_completed_diagnostic_sessions=MagicMock(return_value=diagnostic_queryset),
        )

    def test_previous_weekly_review_is_preferred(self) -> None:
        previous = build_session(20, date(2026, 7, 19))

        with patch(f"{MODULE}._get_session", return_value=self.source), \
                self.patch_sessions([previous, self.source], []), \
                patch(f"{MODULE}._score_summary", return_value={"sessionId": 20, "score": 60}):
            baseline = collector.build_baseline(1, 30)

        self.assertEqual(baseline["sessionId"], 20)
        self.assertEqual(baseline["type"], "weekly_review")

    def test_falls_back_to_diagnostic_when_no_previous_weekly(self) -> None:
        diagnostic = build_session(5, date(2026, 7, 1))

        with patch(f"{MODULE}._get_session", return_value=self.source), \
                self.patch_sessions([self.source], [diagnostic]), \
                patch(f"{MODULE}._score_summary", return_value={"sessionId": 5, "score": 40}):
            baseline = collector.build_baseline(1, 30)

        self.assertEqual(baseline["sessionId"], 5)
        self.assertEqual(baseline["type"], "diagnostic")

    def test_no_earlier_session_returns_none(self) -> None:
        with patch(f"{MODULE}._get_session", return_value=self.source), \
                self.patch_sessions([self.source], []):
            self.assertIsNone(collector.build_baseline(1, 30))

    def test_later_sessions_are_not_used_as_baseline(self) -> None:
        later = build_session(40, date(2026, 7, 30))

        with patch(f"{MODULE}._get_session", return_value=self.source), \
                self.patch_sessions([self.source, later], []):
            self.assertIsNone(collector.build_baseline(1, 30))


class HasPreviousWeeklyReviewTests(TestCase):
    def test_first_week_has_no_previous_review(self) -> None:
        source = build_session(30, date(2026, 7, 26))

        with patch(f"{MODULE}._get_session", return_value=source), \
                patch(
                    "analytics.service.analytics.get_completed_weekly_review_sessions",
                    return_value=[source],
                ):
            self.assertFalse(collector.has_previous_weekly_review(1, 30))

    def test_second_week_has_a_previous_review(self) -> None:
        source = build_session(30, date(2026, 7, 26))
        previous = build_session(20, date(2026, 7, 19))

        with patch(f"{MODULE}._get_session", return_value=source), \
                patch(
                    "analytics.service.analytics.get_completed_weekly_review_sessions",
                    return_value=[previous, source],
                ):
            self.assertTrue(collector.has_previous_weekly_review(1, 30))


class PlanProgressTests(TestCase):
    def test_uses_the_completed_block_plan_not_the_active_plan(self) -> None:
        study_plan = MagicMock()
        study_plan.studyplan_id = 77
        progress = {"summary": {"targetCount": 24, "achievedCount": 18, "completionRate": 0.75}}

        with patch(f"{MODULE}.StudyPlanMypage") as model, \
                patch(
                    "analytics.service.studyplan.calculate_record_based_plan_progress",
                    return_value=progress,
                ) as calculate:
            model.objects.filter.return_value.first.return_value = study_plan
            summary = collector.build_plan_progress(1, 77)

        model.objects.filter.assert_called_once_with(studyplan_id=77)
        self.assertIs(calculate.call_args.args[1], study_plan)
        self.assertEqual(summary["completionRate"], 0.75)

    def test_missing_plan_falls_back_to_zero_progress(self) -> None:
        with patch(f"{MODULE}.StudyPlanMypage") as model:
            model.objects.filter.return_value.first.return_value = None
            summary = collector.build_plan_progress(1, 77)

        self.assertEqual(summary["completionRate"], 0.0)

    def test_completion_rate_is_clamped_for_build_report_result(self) -> None:
        study_plan = MagicMock()
        progress = {"summary": {"completionRate": 1.4}}

        with patch(f"{MODULE}.StudyPlanMypage") as model, \
                patch(
                    "analytics.service.studyplan.calculate_record_based_plan_progress",
                    return_value=progress,
                ):
            model.objects.filter.return_value.first.return_value = study_plan
            summary = collector.build_plan_progress(1, 77)

        self.assertEqual(summary["completionRate"], 1.0)


class GroupKeyMatchingTests(TestCase):
    """groupKeyId 불일치는 예외 없이 값만 0.0 이 되므로 테스트로만 잡을 수 있다."""

    def build_records(self) -> list[dict[str, object]]:
        return [
            {
                "session_id": 100 + index,
                "session__recorded_date": date(2026, 7, 20 + index % 5),
                "is_correct": index % 6 == 0,
                "time_spent_ms": 60000,
                "era": "조선",
                "topic": "정치",
            }
            for index in range(12)
        ]

    def test_weakness_rows_and_priority_targets_share_the_group_key(self) -> None:
        weakness_rows = build_weakness_rows(self.build_records(), ("era", "topic"), TODAY)
        weak_row = next(row for row in weakness_rows if row["status"] == "WEAK")

        priority_targets = [build_priority_target(JOSEON_POLITICS, repeated_error=0.6)]
        repeated_error_by_group = {
            target.group_key_id: target.repeated_error for target in priority_targets
        }

        self.assertEqual(str(weak_row["groupKeyId"]), JOSEON_POLITICS)
        self.assertIn(str(weak_row["groupKeyId"]), repeated_error_by_group)

    def test_repeated_error_reaches_the_report(self) -> None:
        weakness_rows = build_weakness_rows(self.build_records(), ("era", "topic"), TODAY)
        priority_targets = [build_priority_target(JOSEON_POLITICS, repeated_error=0.6)]

        collected = build_report_result(
            assessment={"sessionId": 30, "score": 74, "totalScore": 100},
            baseline=None,
            plan_progress={"completionRate": 0.5},
            weakness_rows=weakness_rows,
            repeated_error_by_group={
                target.group_key_id: target.repeated_error for target in priority_targets
            },
            time_records=[],
            priority_targets=priority_targets,
            snapshot_at=_utc(2026, 7, 26),
            recovered_snapshot=False,
            generation_reason="personalized",
            has_previous_weekly_review=True,
        )

        improvements = collected["result"]["priorityImprovements"]
        self.assertTrue(improvements)
        self.assertEqual(improvements[0]["repeatedError"], 0.6)

    def test_three_field_group_key_would_silently_lose_repeated_error(self) -> None:
        """era·topic·q_type 로 만들면 키가 안 맞아 조용히 0.0 이 된다는 것을 고정한다."""
        weakness_rows = build_weakness_rows(
            [dict(row, q_type="순서") for row in self.build_records()],
            ("era", "topic", "q_type"),
            TODAY,
        )
        weak_row = next(row for row in weakness_rows if row["status"] == "WEAK")

        self.assertNotEqual(str(weak_row["groupKeyId"]), JOSEON_POLITICS)


class CollectWeeklyReportResultTests(TestCase):
    def collect(self, **overrides):
        defaults = {
            "build_assessment": {"sessionId": 30, "score": 74, "totalScore": 100},
            "build_baseline": None,
            "build_plan_progress": {"completionRate": 0.5},
            "build_weakness_rows": [],
            "collect_time_records": [],
            "priority_targets": [build_priority_target(JOSEON_POLITICS, 0.6)],
            "has_previous_weekly_review": True,
            "collect_concept_weakness_rows": [],
            "collect_exam_trends": [],
        }
        defaults.update(overrides)
        with patch(f"{MODULE}.build_assessment", return_value=defaults["build_assessment"]), \
                patch(f"{MODULE}.build_baseline", return_value=defaults["build_baseline"]), \
                patch(f"{MODULE}.build_plan_progress", return_value=defaults["build_plan_progress"]), \
                patch(f"{MODULE}.build_weakness_rows", return_value=defaults["build_weakness_rows"]), \
                patch(f"{MODULE}.collect_time_records", return_value=defaults["collect_time_records"]), \
                patch(f"{MODULE}._completed_records", return_value=[]), \
                patch(
                    f"{MODULE}._build_priority_targets_or_empty",
                    return_value=defaults["priority_targets"],
                ), \
                patch(
                    f"{MODULE}.collect_concept_weakness_rows",
                    return_value=defaults["collect_concept_weakness_rows"],
                ), \
                patch(
                    f"{MODULE}.collect_exam_trends",
                    return_value=defaults["collect_exam_trends"],
                ), \
                patch(
                    f"{MODULE}.has_previous_weekly_review",
                    return_value=defaults["has_previous_weekly_review"],
                ):
            return collector.collect_weekly_report_result(1, 30, 77, TODAY)

    def test_first_week_is_detected_from_the_absence_of_a_previous_review(self) -> None:
        collected = self.collect(has_previous_weekly_review=False)

        self.assertEqual(collected["reportType"], "first_week")

    def test_second_week_is_a_normal_weekly_report(self) -> None:
        collected = self.collect()

        self.assertEqual(collected["reportType"], "weekly")

    def test_missing_priority_targets_only_empties_next_plan_targets(self) -> None:
        collected = self.collect(priority_targets=[])

        self.assertEqual(collected["result"]["nextPlanTargets"], [])
        self.assertEqual(collected["result"]["assessment"]["score"], 74)

    def test_confusion_patterns_are_empty_without_a_resolver(self) -> None:
        collected = self.collect()

        self.assertEqual(collected["result"]["confusionPatterns"], [])

    def test_result_is_json_serializable_for_jsonb_storage(self) -> None:
        import json

        collected = self.collect()
        stored = json.loads(json.dumps(collected["result"], ensure_ascii=False))

        self.assertEqual(stored["assessment"]["totalScore"], 100)


class EnqueueWeeklyReportTests(TestCase):
    def setUp(self) -> None:
        self.config = get_weekly_report_config()
        self.collected = {
            "reportType": "weekly",
            "result": {"snapshotAt": "2026-07-26T00:00:00Z", "assessment": {}},
        }

    def enqueue(self, existing_report, collected=None, raises=None):
        collect_mock = MagicMock(side_effect=raises) if raises else MagicMock(
            return_value=collected or self.collected,
        )
        with patch(f"{MODULE}._load_plan_report", return_value=existing_report), \
                patch(f"{MODULE}.collect_weekly_report_result", collect_mock), \
                patch(f"{MODULE}.repository") as repository_module:
            created = collector.enqueue_weekly_report(1, 30, 77, today=TODAY)
        return created, repository_module

    def test_creates_a_pending_report(self) -> None:
        created, repository_module = self.enqueue(existing_report=None)

        self.assertTrue(created)
        saved_report = repository_module.save_report.call_args.args[1]
        self.assertEqual(saved_report["status"], "pending")
        self.assertEqual(saved_report["sourceSessionId"], 30)
        self.assertEqual(saved_report["reportType"], "weekly")

    def test_same_session_is_not_enqueued_twice(self) -> None:
        created, repository_module = self.enqueue(
            existing_report={"status": "ready", "sourceSessionId": 30},
        )

        self.assertFalse(created)
        repository_module.save_report.assert_not_called()

    def test_a_new_session_replaces_the_previous_report(self) -> None:
        created, repository_module = self.enqueue(
            existing_report={"status": "ready", "sourceSessionId": 20},
        )

        self.assertTrue(created)
        repository_module.save_report.assert_called_once()

    def test_collection_failure_is_swallowed_but_logged(self) -> None:
        """리포트 생성 실패가 진단평가 제출 응답을 깨면 안 된다."""
        with self.assertLogs(MODULE, level="ERROR") as logs:
            created, repository_module = self.enqueue(
                existing_report=None,
                raises=RuntimeError("DB down"),
            )

        self.assertFalse(created)
        repository_module.save_report.assert_not_called()
        self.assertIn("주간 리포트 생성 실패", logs.output[0])


def _utc(year: int, month: int, day: int):
    from datetime import datetime, timezone as datetime_timezone

    return datetime(year, month, day, tzinfo=datetime_timezone.utc)


class ExamTrendTests(TestCase):
    """ml_trend_top5 의 최근 출제 경향을 취약 분석과 대조한다."""

    def build_trend_row(self, rank: int, era: str, topic: str, percent: float):
        return {
            "rank_no": rank,
            "era": era,
            "topic_train": topic,
            "combo_label": f"{era} + {topic}",
            "count_value": 20 - rank,
            "ratio_percent": percent,
            "target_round": 78,
            "recent5_rounds": "73~77",
        }

    def test_ml_era_label_is_matched_through_taxonomy_aliases(self) -> None:
        """ML 은 '일제 강점기', 서비스는 '일제강점기' 로 쓴다. 별칭이 흡수해야 한다."""
        from analytics.service.exam_trend import get_recent_exam_trends

        rows = [self.build_trend_row(1, "일제 강점기", "사건", 8.8)]
        with patch("analytics.service.exam_trend.MlTrendTop5") as model:
            model.objects.filter.return_value.order_by.return_value.values.return_value = rows
            trends = get_recent_exam_trends(target_round=78)

        self.assertEqual(
            trends[0]["groupKeyId"],
            build_group_key_id({"era": "일제강점기", "topic": "사건"}),
        )
        self.assertEqual(trends[0]["ratioPercent"], 8.8)
        self.assertEqual(trends[0]["recentRounds"], "73~77")

    def test_no_trend_data_returns_empty(self) -> None:
        from analytics.service.exam_trend import get_recent_exam_trends

        with patch("analytics.service.exam_trend.MlTrendTop5") as model:
            model.objects.filter.return_value.order_by.return_value.values_list.return_value.first.return_value = None
            self.assertEqual(get_recent_exam_trends(), [])

    def test_frequently_examined_weakness_wins_a_near_tie(self) -> None:
        """비슷하게 약하면 시험에 자주 나오는 쪽을 먼저 보완하는 게 낫다."""
        goryeo = build_group_key_id({"era": "고려", "topic": "경제"})
        # 0.72 와 0.71 은 같은 구간이라 취약 정도로는 순서를 가리지 않는다
        weakness_rows = [
            _weak_row(goryeo, "고려 · 경제", weakness_score=0.72),
            _weak_row(JOSEON_POLITICS, "조선 · 정치", weakness_score=0.71),
        ]
        exam_trends = [
            {"rank": 1, "groupKeyId": JOSEON_POLITICS, "label": "조선 + 정치", "ratioPercent": 7.2},
        ]

        collected = build_report_result(
            assessment={"sessionId": 30, "score": 74, "totalScore": 100},
            baseline=None,
            plan_progress={"completionRate": 0.5},
            weakness_rows=weakness_rows,
            repeated_error_by_group={},
            time_records=[],
            priority_targets=[],
            snapshot_at=_utc(2026, 7, 26),
            recovered_snapshot=False,
            generation_reason="personalized",
            has_previous_weekly_review=True,
            exam_trends=exam_trends,
        )

        improvements = collected["result"]["priorityImprovements"]
        # 점수는 고려·경제가 근소하게 높지만 같은 구간이라 출제 1위가 앞선다
        self.assertEqual(improvements[0]["groupKeyId"], JOSEON_POLITICS)
        self.assertEqual(improvements[0]["examTrendRank"], 1)
        # 출제 비중이지 학습자 성적이 아니므로 이름에 그 뜻이 드러나야 한다
        self.assertEqual(improvements[0]["examQuestionSharePercent"], 7.2)
        self.assertNotIn("examTrendPercent", improvements[0])
        self.assertNotIn("examTrendRank", improvements[1])

    def test_clearly_weaker_area_beats_the_exam_trend(self) -> None:
        """출제 경향은 동점을 가르는 기준이지 취약 정도를 뒤집는 기준이 아니다."""
        goryeo = build_group_key_id({"era": "고려", "topic": "경제"})
        weakness_rows = [
            _weak_row(goryeo, "고려 · 경제", weakness_score=0.92),
            _weak_row(JOSEON_POLITICS, "조선 · 정치", weakness_score=0.55),
        ]
        exam_trends = [
            {"rank": 1, "groupKeyId": JOSEON_POLITICS, "label": "조선 + 정치", "ratioPercent": 7.2},
        ]

        collected = build_report_result(
            assessment={"sessionId": 30, "score": 74, "totalScore": 100},
            baseline=None,
            plan_progress={"completionRate": 0.5},
            weakness_rows=weakness_rows,
            repeated_error_by_group={},
            time_records=[],
            priority_targets=[],
            snapshot_at=_utc(2026, 7, 26),
            recovered_snapshot=False,
            generation_reason="personalized",
            has_previous_weekly_review=True,
            exam_trends=exam_trends,
        )

        self.assertEqual(collected["result"]["priorityImprovements"][0]["groupKeyId"], goryeo)

    def test_without_trend_data_the_order_falls_back_to_weakness_score(self) -> None:
        goryeo = build_group_key_id({"era": "고려", "topic": "경제"})
        weakness_rows = [
            _weak_row(goryeo, "고려 · 경제", weakness_score=0.72),
            _weak_row(JOSEON_POLITICS, "조선 · 정치", weakness_score=0.68),
        ]

        collected = build_report_result(
            assessment={"sessionId": 30, "score": 74, "totalScore": 100},
            baseline=None,
            plan_progress={"completionRate": 0.5},
            weakness_rows=weakness_rows,
            repeated_error_by_group={},
            time_records=[],
            priority_targets=[],
            snapshot_at=_utc(2026, 7, 26),
            recovered_snapshot=False,
            generation_reason="personalized",
            has_previous_weekly_review=True,
        )

        self.assertEqual(collected["result"]["priorityImprovements"][0]["groupKeyId"], goryeo)


class ConceptWeaknessTests(TestCase):
    """핵심 개념 단위 취약점."""

    def build_concept_rows(self) -> list[dict[str, object]]:
        return [
            _weak_row(
                build_group_key_id({"coreConcept": "붕당 정치"}),
                "붕당 정치",
                weakness_score=0.74,
                sample_count=9,
                wrong_count=7,
            ),
            _weak_row(
                build_group_key_id({"coreConcept": "대동법"}),
                "대동법",
                weakness_score=0.61,
                sample_count=6,
                wrong_count=4,
            ),
            dict(
                _weak_row(
                    build_group_key_id({"coreConcept": "훈민정음"}),
                    "훈민정음",
                    weakness_score=0.2,
                ),
                status="INSUFFICIENT",
            ),
        ]

    def collect(self, concept_rows):
        return build_report_result(
            assessment={"sessionId": 30, "score": 74, "totalScore": 100},
            baseline=None,
            plan_progress={"completionRate": 0.5},
            weakness_rows=[],
            repeated_error_by_group={},
            time_records=[],
            priority_targets=[],
            snapshot_at=_utc(2026, 7, 26),
            recovered_snapshot=False,
            generation_reason="personalized",
            has_previous_weekly_review=True,
            concept_rows=concept_rows,
        )

    def test_only_weak_concepts_are_reported_in_score_order(self) -> None:
        collected = self.collect(self.build_concept_rows())
        concepts = collected["result"]["conceptWeaknesses"]

        self.assertEqual([item["label"] for item in concepts], ["붕당 정치", "대동법"])
        self.assertEqual(concepts[0]["evidenceId"], "concept-1")
        self.assertEqual(concepts[0]["wrongCount"], 7)
        self.assertEqual(concepts[0]["sampleCount"], 9)

    def test_concept_count_is_capped_by_config(self) -> None:
        many_rows = [
            _weak_row(
                build_group_key_id({"coreConcept": f"개념{index}"}),
                f"개념{index}",
                weakness_score=0.9 - index * 0.01,
            )
            for index in range(8)
        ]
        concepts = self.collect(many_rows)["result"]["conceptWeaknesses"]

        self.assertEqual(len(concepts), get_weekly_report_config().maximum_concept_weakness_count)

    def test_no_concept_rows_gives_an_empty_section(self) -> None:
        self.assertEqual(self.collect([])["result"]["conceptWeaknesses"], [])

    def test_concept_evidence_is_citable_by_the_guard(self) -> None:
        """AI 가 concept-1 을 인용할 수 있어야 한다."""
        from analytics.service.weekly_report.llm import validate_ai_content

        collected = self.collect(self.build_concept_rows())
        candidate = {
            "comment": {"text": "붕당 정치 개념에서 반복해서 막히고 있어요.", "evidenceIds": ["concept-1"]},
            "tips": [{"text": "정의부터 다시 정리해 보세요.", "evidenceIds": ["concept-1"]}],
        }

        self.assertEqual(validate_ai_content(candidate, collected["result"]), [])


class TrendFieldTests(TestCase):
    """추세 정보가 리포트까지 전달되는지 확인한다."""

    def test_priority_improvement_carries_trend_and_wrong_rate(self) -> None:
        row = _weak_row(JOSEON_POLITICS, "조선 · 정치", weakness_score=0.7)
        row.update(
            {
                "trend": "worsening",
                "trendDelta": 0.18,
                "recentScore": 0.8,
                "previousScore": 0.62,
            }
        )

        collected = build_report_result(
            assessment={"sessionId": 30, "score": 74, "totalScore": 100},
            baseline=None,
            plan_progress={"completionRate": 0.5},
            weakness_rows=[row],
            repeated_error_by_group={},
            time_records=[],
            priority_targets=[],
            snapshot_at=_utc(2026, 7, 26),
            recovered_snapshot=False,
            generation_reason="personalized",
            has_previous_weekly_review=True,
        )

        improvement = collected["result"]["priorityImprovements"][0]
        self.assertEqual(improvement["trend"], "worsening")
        self.assertEqual(improvement["trendDelta"], 0.18)
        # 윌슨 하한이지 오답률이 아니다. 이름과 의미가 어긋나면 AI 가 오답률로 진술한다.
        self.assertEqual(improvement["recentWeaknessScore"], 0.8)
        self.assertEqual(improvement["previousWeaknessScore"], 0.62)
        self.assertNotIn("recentWrongRate", improvement)
        self.assertNotIn("recentWrongPercent", improvement)
        self.assertEqual(improvement["wrongRate"], 0.75)
        self.assertEqual(improvement["wrongCount"], 9)


def _weak_row(
    group_key_id: str,
    label: str,
    weakness_score: float,
    sample_count: int = 12,
    wrong_count: int = 9,
) -> dict[str, object]:
    return {
        "groupKeyId": group_key_id,
        "label": label,
        "status": "WEAK",
        "trend": "unknown",
        "trendDelta": None,
        "weaknessScore": weakness_score,
        "raw": {
            "total": sample_count,
            "wrong": wrong_count,
            "wrongRate": round(wrong_count / sample_count, 4),
            "averageTimeSec": 74,
        },
        "effective": {"total": float(sample_count) * 0.7, "wrong": float(wrong_count) * 0.7},
    }


class ReportLengthTests(TestCase):
    """리포트 분량 정책. 가드가 실제로 이 길이를 허용하는지 확인한다."""

    def build_result(self) -> dict[str, object]:
        return build_report_result(
            assessment={"sessionId": 30, "score": 74, "totalScore": 100},
            baseline={"sessionId": 20, "score": 62, "type": "weekly_review"},
            plan_progress={"completionRate": 0.75},
            weakness_rows=[_weak_row(JOSEON_POLITICS, "조선 · 정치", weakness_score=0.7)],
            repeated_error_by_group={JOSEON_POLITICS: 0.6},
            time_records=[],
            priority_targets=[],
            snapshot_at=_utc(2026, 7, 26),
            recovered_snapshot=False,
            generation_reason="personalized",
            has_previous_weekly_review=True,
        )["result"]

    def test_multi_sentence_comment_passes_the_guard(self) -> None:
        from analytics.service.weekly_report.llm import validate_ai_content

        comment = (
            "이번 주간평가에서는 조선 · 정치 영역에서 가장 많이 막혔어요. "
            "같은 영역을 지난주에도 놓쳤기 때문에 다음 학습에서 먼저 다뤄 두는 편이 좋겠어요. "
            "문제를 짧은 묶음으로 나누어 풀면 흐름을 놓치는 지점이 어디인지 찾기 쉬워집니다. "
            "정리한 내용은 다음 주 평가에서 그대로 확인할 수 있으니 부담 없이 이어가 보세요. "
            "지금 속도라면 남은 영역도 차근차근 정리해 나갈 수 있어요. "
            "붕당 정치처럼 자주 막히는 개념은 정의부터 다시 훑어 두면 도움이 됩니다."
        )
        candidate = {
            "comment": {"text": comment, "evidenceIds": ["priority-1"]},
            "tips": [{"text": "짧은 묶음으로 나누어 풀어 보세요.", "evidenceIds": ["priority-1"]}],
        }

        # 기존 한도(240자)로는 거절됐을 길이가 통과해야 한다
        self.assertGreater(len(comment), 240)
        self.assertLessEqual(len(comment), get_weekly_report_config().maximum_comment_length)
        self.assertEqual(validate_ai_content(candidate, self.build_result()), [])

    def test_comment_beyond_the_limit_is_still_rejected(self) -> None:
        from analytics.service.weekly_report.llm import validate_ai_content

        config = get_weekly_report_config()
        candidate = {
            "comment": {"text": "가" * (config.maximum_comment_length + 1), "evidenceIds": []},
            "tips": [],
        }

        self.assertIn("COMMENT_TOO_LONG", validate_ai_content(candidate, self.build_result()))

    def test_tip_count_limit_follows_config(self) -> None:
        from analytics.service.weekly_report.llm import validate_ai_content

        config = get_weekly_report_config()
        tips = [
            {"text": f"{'가나다라'[index]} 영역을 정리해 보세요.", "evidenceIds": []}
            for index in range(config.maximum_tip_count)
        ]
        candidate = {"comment": {"text": "이번 주 학습 요약이에요.", "evidenceIds": []}, "tips": tips}

        self.assertEqual(validate_ai_content(candidate, self.build_result()), [])

        candidate["tips"] = tips + [{"text": "하나 더.", "evidenceIds": []}]
        self.assertIn("TIP_COUNT_EXCEEDED", validate_ai_content(candidate, self.build_result()))


class WilsonScoreNamingTests(TestCase):
    """윌슨 하한을 오답률로 부르지 않는지 확인한다.

    weaknessScore 는 표본이 적을수록 보수적으로 낮아지는 하한이라
    관측된 오답률과 다르다. 이름이 어긋나면 가드로는 잡히지 않고
    AI 가 "오답률 71%" 같은 잘못된 문장을 쓰게 된다.
    """

    def build_result(self, sample_count: int, wrong_count: int) -> dict[str, object]:
        row = _weak_row(
            JOSEON_POLITICS,
            "조선 · 정치",
            weakness_score=0.0,
            sample_count=sample_count,
            wrong_count=wrong_count,
        )
        from analytics.service.weakness import (
            calculate_wilson_lower_bound,
            get_weakness_config,
        )

        weakness_config = get_weakness_config()
        row["weaknessScore"] = round(
            calculate_wilson_lower_bound(
                float(wrong_count) * 0.7,
                float(sample_count) * 0.7,
                weakness_config.wilson_z,
            ),
            4,
        )
        return build_report_result(
            assessment={"sessionId": 30, "score": 74, "totalScore": 100},
            baseline=None,
            plan_progress={"completionRate": 0.5},
            weakness_rows=[row],
            repeated_error_by_group={},
            time_records=[],
            priority_targets=[],
            snapshot_at=_utc(2026, 7, 26),
            recovered_snapshot=False,
            generation_reason="personalized",
            has_previous_weekly_review=True,
        )["result"]

    def test_weakness_score_is_lower_than_the_observed_wrong_rate(self) -> None:
        improvement = self.build_result(12, 9)["priorityImprovements"][0]

        self.assertEqual(improvement["wrongRate"], 0.75)
        self.assertLess(improvement["weaknessScore"], improvement["wrongRate"])

    def test_small_sample_widens_the_gap(self) -> None:
        """표본이 적을수록 하한은 더 보수적으로 내려간다."""
        small = self.build_result(4, 3)["priorityImprovements"][0]
        large = self.build_result(40, 30)["priorityImprovements"][0]

        self.assertEqual(small["wrongRate"], large["wrongRate"])
        self.assertLess(small["weaknessScore"], large["weaknessScore"])

    def test_no_percent_form_is_offered_for_wilson_scores(self) -> None:
        """백분율 표기는 진짜 비율에만 붙인다."""
        improvement = self.build_result(12, 9)["priorityImprovements"][0]

        self.assertEqual(improvement["wrongPercent"], 75)
        for field_name in improvement:
            if "WeaknessScore" in field_name or field_name == "weaknessScore":
                self.assertNotIn("Percent", field_name)

    def test_evidence_field_names_do_not_call_a_bound_a_rate(self) -> None:
        result = self.build_result(12, 9)
        for section in ("strengths", "priorityImprovements", "conceptWeaknesses"):
            for item in result.get(section) or []:
                for field_name in item:
                    if "Rate" in field_name or "Percent" in field_name:
                        self.assertIn(
                            "wrong",
                            field_name.lower(),
                            f"{section}.{field_name} 이 비율이 아닌 값에 비율 이름을 쓰고 있다",
                        )


class GuardRobustnessTests(TestCase):
    """가드가 정상 문장을 거절하거나 이상한 문장을 통과시키지 않는지 확인한다."""

    def build_result(self, concept_label: str = "붕당 정치") -> dict[str, object]:
        concept_row = _weak_row(
            build_group_key_id({"coreConcept": concept_label}),
            concept_label,
            weakness_score=0.74,
            sample_count=9,
            wrong_count=7,
        )
        return build_report_result(
            assessment={"sessionId": 98, "score": 76, "totalScore": 100},
            baseline={"sessionId": 55, "score": 62, "totalScore": 100, "type": "weekly_review"},
            plan_progress={"completionRate": 0.75},
            weakness_rows=[_weak_row(JOSEON_POLITICS, "조선 · 정치", weakness_score=0.7)],
            repeated_error_by_group={},
            time_records=[],
            priority_targets=[],
            snapshot_at=_utc(2026, 7, 26),
            recovered_snapshot=False,
            generation_reason="personalized",
            has_previous_weekly_review=True,
            concept_rows=[concept_row],
        )["result"]

    def guard(self, text: str, evidence_ids: list[str], result=None) -> list[str]:
        from analytics.service.weekly_report.llm import validate_ai_content

        candidate = {
            "comment": {"text": text, "evidenceIds": evidence_ids},
            "tips": [],
        }
        return validate_ai_content(candidate, result or self.build_result())

    def test_concept_name_containing_digits_is_usable(self) -> None:
        """3·1 운동 같은 개념명을 쓰지 못하면 한국사 리포트에서 곤란하다."""
        result = self.build_result(concept_label="3·1 운동")

        self.assertEqual(
            self.guard("3·1 운동 개념을 정의부터 다시 정리해 보세요.", ["concept-1"], result),
            [],
        )

    def test_session_id_cannot_be_quoted_as_a_score(self) -> None:
        """세션 번호 98 을 점수처럼 쓰면 안 된다."""
        errors = self.guard("지난 평가 98점에서 올랐어요.", ["comparison-baseline"])

        self.assertIn("COMMENT_UNSUPPORTED_NUMBER", errors)

    def test_real_previous_score_is_quotable(self) -> None:
        self.assertEqual(self.guard("지난 평가 62점에서 올랐어요.", ["comparison-baseline"]), [])

    def test_thousands_separator_is_not_split_into_two_numbers(self) -> None:
        result = self.build_result()
        result["timeSummary"] = [
            {"evidenceId": "time-1", "label": "사료", "userMedianSeconds": 1200},
        ]

        self.assertEqual(self.guard("사료 유형은 1,200초가 걸렸어요.", ["time-1"], result), [])

    def test_sign_does_not_block_a_truthful_sentence(self) -> None:
        """근거가 -0.2 여도 "0.2만큼" 이라고 쓸 수 있어야 한다."""
        result = self.build_result()
        result["strengths"] = [
            {"evidenceId": "strength-1", "label": "고려 · 경제", "trendDelta": -0.2},
        ]

        self.assertEqual(self.guard("0.2만큼 개선됐어요.", ["strength-1"], result), [])

    def test_invented_number_is_still_rejected(self) -> None:
        errors = self.guard("정답률이 35% 수준이에요.", ["priority-1"])

        self.assertIn("COMMENT_UNSUPPORTED_NUMBER", errors)


class StrengthOverlapTests(TestCase):
    """같은 영역이 강점과 우선 보완 영역에 동시에 실리면 안 된다."""

    def build_result(self, status: str) -> dict[str, object]:
        row = _weak_row(JOSEON_POLITICS, "조선 · 정치", weakness_score=0.66)
        row.update(
            {
                "status": status,
                "trend": "improving",
                "trendDelta": -0.44,
                "recentScore": 0.4,
                "previousScore": 0.84,
            }
        )
        return build_report_result(
            assessment={"sessionId": 30, "score": 74, "totalScore": 100},
            baseline=None,
            plan_progress={"completionRate": 0.5},
            weakness_rows=[row],
            repeated_error_by_group={},
            time_records=[],
            priority_targets=[],
            snapshot_at=_utc(2026, 7, 26),
            recovered_snapshot=False,
            generation_reason="personalized",
            has_previous_weekly_review=True,
        )["result"]

    def test_weak_area_is_not_reported_as_a_strength(self) -> None:
        result = self.build_result("WEAK")

        self.assertEqual(result["strengths"], [])
        self.assertEqual(len(result["priorityImprovements"]), 1)

    def test_recovered_area_is_still_reported_as_a_strength(self) -> None:
        result = self.build_result("NEUTRAL")

        self.assertEqual(len(result["strengths"]), 1)
        self.assertEqual(result["priorityImprovements"], [])


class FindRecoverableSessionsTests(TestCase):
    """복구 스캔 후보 선별.

    주간복습을 마치지 않은 계획이 후보에 남으면 스캔 주기를 짧게 잡을 수 없다.
    """

    def find(self, plan_rows, record_rows, plan_item_rows):
        with patch(f"{MODULE}.repository") as repository_module, \
                patch(f"{MODULE}.SolveRecords") as solve_records, \
                patch(f"{MODULE}.StudyPlanMypage") as study_plan_model:
            repository_module.find_plans_without_report.return_value = plan_rows
            queryset = solve_records.objects.filter.return_value
            queryset = queryset.exclude.return_value.exclude.return_value
            queryset.values.return_value.distinct.return_value = record_rows
            study_plan_model.objects.filter.return_value.values.return_value = plan_item_rows
            return collector.find_recoverable_sessions()

    def test_plan_without_a_finished_weekly_review_is_skipped(self) -> None:
        found = self.find(
            plan_rows=[{"studyplan_id": 77, "user_id": 1}],
            record_rows=[],
            plan_item_rows=[],
        )

        self.assertEqual(found, [])

    def test_record_from_a_practice_block_is_ignored(self) -> None:
        found = self.find(
            plan_rows=[{"studyplan_id": 77, "user_id": 1}],
            record_rows=[
                {"studyplan_id": 77, "study_plan_block_id": "practice-1", "session_id": 30},
            ],
            plan_item_rows=[
                {
                    "studyplan_id": 77,
                    "study_plan_items": [
                        {"blocks": [{"blockId": "review-1", "blockType": "weekly_review"}]},
                    ],
                },
            ],
        )

        self.assertEqual(found, [])

    def test_latest_weekly_review_session_becomes_the_source(self) -> None:
        found = self.find(
            plan_rows=[{"studyplan_id": 77, "user_id": 1}],
            record_rows=[
                {"studyplan_id": 77, "study_plan_block_id": "review-1", "session_id": 30},
                {"studyplan_id": 77, "study_plan_block_id": "review-1", "session_id": 41},
            ],
            plan_item_rows=[
                {
                    "studyplan_id": 77,
                    "study_plan_items": [
                        {"blocks": [{"blockId": "review-1", "blockType": "weekly_review"}]},
                    ],
                },
            ],
        )

        self.assertEqual(
            found,
            [{"userId": 1, "studyPlanId": 77, "sourceSessionId": 41}],
        )


class DispatchWeeklyReportTests(TestCase):
    """주간복습 완료 직후 생성 경로.

    제출 응답을 붙잡으면 안 되므로 생성은 스레드로 빠져야 한다.
    """

    def dispatch(self, created: bool, inline_enabled: bool = True):
        from analytics.service.weekly_report import dispatcher

        config = get_weekly_report_config()
        config = replace(config, inline_generation_enabled=inline_enabled)
        with patch(f"{MODULE}.enqueue_weekly_report", return_value=created) as enqueue, \
                patch(
                    "analytics.service.weekly_report.dispatcher.threading.Thread",
                ) as thread_class:
            started = dispatcher.dispatch_weekly_report(1, 30, 77, config=config)
        return started, enqueue, thread_class

    def test_new_report_starts_a_background_thread(self) -> None:
        started, enqueue, thread_class = self.dispatch(created=True)

        self.assertTrue(started)
        self.assertEqual(enqueue.call_args.args, (1, 30, 77))
        thread_class.return_value.start.assert_called_once()

    def test_duplicate_submission_does_not_generate_again(self) -> None:
        started, _, thread_class = self.dispatch(created=False)

        self.assertFalse(started)
        thread_class.assert_not_called()

    def test_disabled_setting_leaves_the_report_to_the_worker(self) -> None:
        started, _, thread_class = self.dispatch(created=True, inline_enabled=False)

        self.assertTrue(started)
        thread_class.assert_not_called()


class InlineGenerationRetryTests(TestCase):
    """즉시 생성이 실패했을 때의 재시도.

    한 번만 돌면 리포트가 pending 으로 남아 화면이 '작성 중' 에 갇힌다.
    """

    def run_background(self, codes):
        from analytics.service.weekly_report import dispatcher

        config = replace(get_weekly_report_config(), retry_delays_seconds=(0, 0))
        with patch(
            "analytics.management.commands.run_weekly_report_worker.process_one_report",
            side_effect=codes,
        ) as process, patch(
            "analytics.service.weekly_report.dispatcher.time.sleep",
        ) as sleep:
            dispatcher._generate_in_background(config, 77)
        return process, sleep

    def test_retries_until_the_report_is_confirmed(self) -> None:
        process, sleep = self.run_background(["retried", "retried", "ready"])

        self.assertEqual(process.call_count, 3)
        self.assertEqual(sleep.call_count, 2)

    def test_stops_as_soon_as_it_is_ready(self) -> None:
        process, sleep = self.run_background(["ready", "retried", "retried"])

        self.assertEqual(process.call_count, 1)
        sleep.assert_not_called()

    def test_stops_when_another_worker_took_it(self) -> None:
        process, _ = self.run_background(["idle", "ready", "ready"])

        self.assertEqual(process.call_count, 1)
