from __future__ import annotations

from datetime import date, timedelta
from unittest import TestCase
from unittest.mock import patch

from analytics.service.study_plan.planner import (
    PlanTarget,
    QuestionPoolInsufficient,
    StudyPlanPeriodUnavailable,
    build_plan_draft,
    build_priority_targets,
    calculate_daily_block_minutes,
    calculate_plan_period,
    calculate_score_counts,
    is_question_pool_available,
)
from analytics.service.study_plan.dto import build_study_plan_dto
from analytics.service.study_plan.service import (
    StudyPlanBlockNotDue,
    StudyPlanBlockRouteMismatch,
    StudyPlanBlockTerminal,
    StudyPlanDataIntegrityError,
    synchronize_plan_items,
    validate_block_start,
    create_personalized_study_plan,
)
from analytics.service.taxonomy import build_group_key_id, parse_group_key_id
from analytics.service.weakness import (
    build_weakness_rows,
    calculate_decay_weight,
    calculate_wilson_lower_bound,
    determine_trend,
)


class WeaknessCoreTests(TestCase):
    def test_decay_weights_follow_fourteen_day_half_life(self) -> None:
        self.assertEqual(calculate_decay_weight(0, 14), 1.0)
        self.assertEqual(calculate_decay_weight(14, 14), 0.5)
        self.assertEqual(calculate_decay_weight(28, 14), 0.25)

    def test_small_perfect_wrong_sample_is_insufficient(self) -> None:
        today = date(2026, 7, 20)
        rows = build_weakness_rows(
            [{"recordedDate": today, "isCorrect": False, "era": "조선", "topic": "정치"}],
            ("era", "topic"),
            today,
        )

        self.assertEqual(rows[0]["status"], "INSUFFICIENT")
        self.assertAlmostEqual(rows[0]["weaknessScore"], 0.3790, places=4)

    def test_three_wrong_answers_are_weak(self) -> None:
        today = date(2026, 7, 20)
        records = [
            {"recordedDate": today, "isCorrect": False, "era": "조선", "topic": "정치"}
            for _ in range(3)
        ]
        row = build_weakness_rows(records, ("era", "topic"), today)[0]

        self.assertEqual(row["status"], "WEAK")
        self.assertAlmostEqual(row["weaknessScore"], 0.6468, places=4)

    def test_old_records_decay_below_minimum_sample(self) -> None:
        # lookback 28일 창의 끝자락 기록은 감쇠 가중치가 0.5^(27/14) ≈ 0.26 이라
        # 원본 10건이어도 유효 표본이 최소 기준(3.0) 아래로 내려간다.
        today = date(2026, 7, 20)
        recorded_date = today - timedelta(days=27)
        records = [
            {
                "recordedDate": recorded_date,
                "isCorrect": index >= 7,
                "era": "조선",
                "topic": "정치",
            }
            for index in range(10)
        ]
        row = build_weakness_rows(records, ("era", "topic"), today)[0]

        self.assertEqual(row["status"], "INSUFFICIENT")
        self.assertLess(row["effective"]["total"], 3.0)

    def test_trend_uses_equal_weight_windows(self) -> None:
        trend = determine_trend(8, 10, 4, 10)

        self.assertEqual(trend["trend"], "worsening")
        self.assertGreater(trend["trendDelta"], 0.10)

    def test_analysis_is_independent_of_input_order(self) -> None:
        today = date(2026, 7, 20)
        records = [
            {"recordedDate": today, "isCorrect": False, "era": "고려", "topic": "경제"},
            {"recordedDate": today, "isCorrect": True, "era": "조선", "topic": "정치"},
            {"recordedDate": today, "isCorrect": False, "era": "조선", "topic": "정치"},
        ]

        self.assertEqual(
            build_weakness_rows(records, ("era", "topic"), today),
            build_weakness_rows(list(reversed(records)), ("topic", "era"), today),
        )

    def test_group_key_percent_encoding_round_trip(self) -> None:
        group = {"era": "조선", "topic": "a=b|c %", "qType": "사료"}
        group_key_id = build_group_key_id(group)

        self.assertEqual(parse_group_key_id(group_key_id), group)
        self.assertIn("%3D", group_key_id)
        self.assertIn("%7C", group_key_id)

    def test_wilson_rejects_impossible_sample(self) -> None:
        with self.assertRaises(ValueError):
            calculate_wilson_lower_bound(2, 1, 1.28)


class StudyPlanCoreTests(TestCase):
    def setUp(self) -> None:
        self.start_date = date(2026, 7, 20)
        self.targets = [
            PlanTarget(
                group_key_id=build_group_key_id({"era": "조선", "topic": "정치"}),
                label="조선 · 정치",
                era="조선",
                topic="정치",
                weakness_score=0.8,
                weakness_status="WEAK",
                trend="worsening",
                effective_total=10,
                exam_question_count=40,
                repeated_error=0.6,
            ),
            PlanTarget(
                group_key_id=build_group_key_id({"era": "고려", "topic": "경제"}),
                label="고려 · 경제",
                era="고려",
                topic="경제",
                weakness_score=0.5,
                weakness_status="NEUTRAL",
                trend="flat",
                effective_total=8,
                exam_question_count=20,
                repeated_error=0.3,
            ),
        ]

    def test_daily_minutes_are_split_into_thirty_minute_blocks(self) -> None:
        self.assertEqual(calculate_daily_block_minutes(45), (30, 15))
        self.assertEqual(calculate_daily_block_minutes(60), (30, 30))
        self.assertEqual(calculate_daily_block_minutes(360), (30,) * 10)

    def test_score_counts_match_question_study_plan_ratio(self) -> None:
        self.assertEqual(calculate_score_counts(10), {3: 2, 2: 6, 1: 2})
        self.assertEqual(calculate_score_counts(1), {3: 0, 2: 1, 1: 0})

    def test_pool_requires_each_score_bucket(self) -> None:
        priority = build_priority_targets(self.targets, days_until_exam=None)[0]
        available = {
            priority.group_key_id: {3: 2, 2: 5, 1: 3},
        }

        self.assertFalse(is_question_pool_available(priority, 10, available))
        available[priority.group_key_id][2] = 6
        self.assertTrue(is_question_pool_available(priority, 10, available))

    def test_exam_boundaries_control_weekly_review(self) -> None:
        with self.assertRaises(StudyPlanPeriodUnavailable):
            calculate_plan_period(self.start_date, self.start_date)

        one_day = calculate_plan_period(self.start_date, self.start_date + timedelta(days=1))
        seven_day_exam = calculate_plan_period(
            self.start_date,
            self.start_date + timedelta(days=7),
        )
        regular = calculate_plan_period(
            self.start_date,
            self.start_date + timedelta(days=8),
        )

        self.assertEqual(one_day.learning_dates, (self.start_date,))
        self.assertIsNone(one_day.weekly_review_date)
        self.assertIsNone(seven_day_exam.weekly_review_date)
        self.assertEqual(regular.weekly_review_date, self.start_date + timedelta(days=6))

    def test_priority_uses_short_term_weights(self) -> None:
        priorities = build_priority_targets(self.targets, days_until_exam=5)

        expected = 0.8 * 0.40 + 1.0 * 0.45 + 0.6 * 0.15
        self.assertAlmostEqual(priorities[0].priority_score, expected)
        self.assertEqual(priorities[0].generation_reason, "personalized")

    def test_unreliable_targets_use_exam_weight_fallback(self) -> None:
        unreliable = [
            PlanTarget(
                group_key_id="era=A|topic=B",
                label="A · B",
                era="A",
                topic="B",
                weakness_score=0.9,
                weakness_status="INSUFFICIENT",
                exam_question_count=20,
                repeated_error=1.0,
            )
        ]
        priority = build_priority_targets(unreliable, days_until_exam=None)[0]

        self.assertEqual(priority.weakness_score, 0.0)
        self.assertEqual(priority.repeated_error, 0.0)
        self.assertEqual(priority.generation_reason, "fallback_prediction_only")
        self.assertEqual(priority.priority_score, 0.40)

    def test_full_plan_has_six_learning_days_and_weekly_review(self) -> None:
        priorities = build_priority_targets(self.targets, days_until_exam=None)
        draft = build_plan_draft(
            priorities,
            self.start_date,
            exam_date=None,
            daily_available_minutes=60,
        )

        self.assertEqual(len(draft["plans"]), 7)
        self.assertTrue(all(len(day["blocks"]) == 2 for day in draft["plans"][:6]))
        self.assertEqual(draft["plans"][-1]["blocks"][0]["blockType"], "weekly_review")
        self.assertNotIn("blockId", draft["plans"][0]["blocks"][0])

    def test_candidate_fallback_relaxes_to_era(self) -> None:
        priorities = build_priority_targets(self.targets, days_until_exam=None)

        def only_era_is_available(target, question_count: int) -> bool:
            return bool(target.era and not target.topic and question_count > 0)

        draft = build_plan_draft(
            priorities,
            self.start_date,
            exam_date=self.start_date + timedelta(days=1),
            daily_available_minutes=30,
            pool_validator=only_era_is_available,
        )
        block = draft["plans"][0]["blocks"][0]

        self.assertEqual(block["topic"], "")
        self.assertIn("시대 범위로 완화", block["reason"])

    def test_all_unavailable_candidates_fail_generation(self) -> None:
        priorities = build_priority_targets(self.targets, days_until_exam=None)

        with self.assertRaises(QuestionPoolInsufficient):
            build_plan_draft(
                priorities,
                self.start_date,
                exam_date=None,
                daily_available_minutes=60,
                pool_validator=lambda target, question_count: False,
            )


class StudyPlanStateTests(TestCase):
    def setUp(self) -> None:
        self.today = date(2026, 7, 20)

    def test_dto_reads_legacy_completion_without_mutating_input(self) -> None:
        source_items = [
            {
                "date": self.today.isoformat(),
                "blocks": [
                    {
                        "blockId": "legacy-block",
                        "blockType": "newWeakness",
                        "isCompleted": True,
                    }
                ],
            }
        ]
        plan = {
            "studyplan_id": 7,
            "study_plans": "레거시 계획",
            "study_plan_items": source_items,
            "status": "active",
            "plan_version": 1,
            "start_date": self.today,
            "end_date": self.today,
        }

        dto = build_study_plan_dto(plan, self.today)

        self.assertEqual(dto["completionRate"], 1.0)
        self.assertEqual(dto["plans"][0]["blocks"][0]["blockType"], "practice")
        self.assertEqual(source_items[0]["blocks"][0]["blockType"], "newWeakness")

    def test_stale_create_request_returns_current_plan_without_recalculation(self) -> None:
        current_plan = {"studyPlanId": 8, "status": "active"}
        with (
            patch(
                "analytics.service.study_plan.service.get_active_study_plan_dto",
                return_value=current_plan,
            ),
            patch(
                "analytics.service.study_plan.service.build_user_plan_draft",
            ) as build_draft_mock,
        ):
            result = create_personalized_study_plan(
                user_id=7,
                source_study_plan_id=6,
                today=self.today,
            )

        self.assertEqual(result, {"changed": False, "studyPlan": current_plan})
        build_draft_mock.assert_not_called()

    def test_sync_rolls_over_by_priority_and_is_idempotent(self) -> None:
        plans = [
            {
                "date": "2026-07-19",
                "blocks": [
                    self._block("low", 0.2),
                    self._block("high", 0.8),
                ],
            },
            {"date": self.today.isoformat(), "blocks": [self._block("today", 1.0)]},
        ]

        first = synchronize_plan_items(plans, self.today, daily_block_limit=2)
        second = synchronize_plan_items(first["plans"], self.today, daily_block_limit=2)

        self.assertEqual(first["rolledOverBlockIds"], ["high"])
        self.assertTrue(first["changed"])
        self.assertFalse(second["changed"])
        self.assertEqual(second["rolledOverBlockIds"], [])

    def test_sync_allows_two_rollovers_then_cancels(self) -> None:
        plans = [
            {
                "date": "2026-07-19",
                "blocks": [self._block("limit", 1.0, rollover_count=2)],
            },
            {"date": self.today.isoformat(), "blocks": []},
        ]

        result = synchronize_plan_items(plans, self.today, daily_block_limit=1)
        block = result["plans"][0]["blocks"][0]

        self.assertEqual(block["status"], "cancelled")
        self.assertEqual(result["cancelledBlockIds"], ["limit"])

    def test_weekly_review_day_does_not_receive_rollover(self) -> None:
        plans = [
            {"date": "2026-07-19", "blocks": [self._block("past", 1.0)]},
            {
                "date": self.today.isoformat(),
                "blocks": [
                    {
                        "blockId": "weekly",
                        "blockType": "weekly_review",
                        "status": "scheduled",
                    }
                ],
            },
        ]

        result = synchronize_plan_items(plans, self.today, daily_block_limit=2)

        self.assertEqual(result["rolledOverBlockIds"], [])
        self.assertEqual(len(result["plans"][0]["blocks"]), 1)

    def test_sync_does_not_extend_plan_end_date(self) -> None:
        plans = [
            {"date": "2026-07-18", "blocks": [self._block("past", 1.0)]},
            {"date": "2026-07-19", "blocks": []},
        ]

        result = synchronize_plan_items(plans, self.today, daily_block_limit=2)

        self.assertEqual(result["rolledOverBlockIds"], [])
        self.assertEqual([day["date"] for day in result["plans"]], ["2026-07-18", "2026-07-19"])

    def test_legacy_extra_is_cancelled_and_excluded_from_completion(self) -> None:
        plans = [
            {
                "date": self.today.isoformat(),
                "blocks": [
                    self._block("normal", 1.0),
                    {**self._block("extra", 1.0), "focusKind": "extra"},
                ],
            }
        ]
        result = synchronize_plan_items(plans, self.today, daily_block_limit=2)
        plan = {
            "studyplan_id": 1,
            "study_plan_items": result["plans"],
            "status": "active",
        }
        dto = build_study_plan_dto(plan, self.today, completed_block_ids={"normal"})

        self.assertEqual(dto["completionRate"], 1.0)
        self.assertEqual(dto["plans"][0]["blocks"][1]["status"], "cancelled")

    def test_mutation_rejects_duplicate_legacy_block_ids(self) -> None:
        plans = [
            {
                "date": self.today.isoformat(),
                "blocks": [self._block("same", 1.0), self._block("same", 0.5)],
            }
        ]

        with self.assertRaises(StudyPlanDataIntegrityError):
            synchronize_plan_items(plans, self.today, daily_block_limit=2)

    def test_start_validation_checks_date_and_route(self) -> None:
        plans = [
            {
                "date": self.today.isoformat(),
                "blocks": [self._block("practice", 1.0)],
            }
        ]
        validate_block_start("active", plans, "practice", self.today, "question")

        with self.assertRaises(StudyPlanBlockRouteMismatch):
            validate_block_start("active", plans, "practice", self.today, "diagnosis")
        with self.assertRaises(StudyPlanBlockNotDue):
            validate_block_start(
                "active",
                plans,
                "practice",
                self.today + timedelta(days=1),
                "question",
            )

    def test_start_validation_rejects_legacy_completed_block(self) -> None:
        plans = [
            {
                "date": self.today.isoformat(),
                "blocks": [{**self._block("done", 1.0), "isCompleted": True}],
            }
        ]

        with self.assertRaises(StudyPlanBlockTerminal):
            validate_block_start("active", plans, "done", self.today, "question")

    def test_start_validation_uses_session_progress_with_completed_precedence(self) -> None:
        plans = [
            {
                "date": self.today.isoformat(),
                "blocks": [self._block("review", 1.0)],
            }
        ]

        validate_block_start(
            "active",
            plans,
            "review",
            self.today,
            "question",
            in_progress_block_ids={"review"},
        )
        with self.assertRaises(StudyPlanBlockTerminal):
            validate_block_start(
                "active",
                plans,
                "review",
                self.today,
                "question",
                completed_block_ids={"review"},
                in_progress_block_ids={"review"},
            )

    def _block(
        self,
        block_id: str,
        priority_score: float,
        rollover_count: int = 0,
    ) -> dict[str, object]:
        return {
            "blockId": block_id,
            "blockType": "practice",
            "priorityScore": priority_score,
            "status": "scheduled",
            "rolloverCount": rollover_count,
        }
