from contextlib import nullcontext
from datetime import date, timedelta
import json
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.db import IntegrityError
from django.test import SimpleTestCase

from analytics.service.analytics import get_diagnosis_comparison_pair
from analytics.service.display import build_planner_summary, build_wrong_rate_display
from analytics.service.mypage import (
    build_d_day_label,
    build_learning_summary,
    build_wrong_rate_summary,
    build_wrong_type_summary,
)
from analytics.service.studyplan import (
    StudyPlanGenerationUnavailable,
    build_daily_plan_items,
    carry_over_incomplete_past_blocks_to_today,
    complete_study_plan_block_by_id,
    create_study_plan,
    delete_study_plan_block,
    ensure_today_study_plan,
    get_study_plan_config,
    get_review_block_target_key,
    is_active_study_plan_unique_violation,
)
from analytics.service.taxonomy import build_target_display_label


class MypageServiceTests(SimpleTestCase):
    @patch("analytics.service.mypage.get_weekly_practice_summary")
    @patch("analytics.service.mypage.get_completed_sessions")
    def test_learning_summary_uses_same_base_date(
        self,
        get_completed_sessions_mock,
        get_weekly_practice_summary_mock,
    ):
        today = date(2026, 7, 13)
        completed_sessions = Mock()
        completed_sessions.values_list.return_value = [
            today,
            today - timedelta(days=1),
            today - timedelta(days=3),
        ]
        get_completed_sessions_mock.return_value = completed_sessions
        get_weekly_practice_summary_mock.return_value = {
            "answerRate": 75,
            "solvedCount": 8,
            "averageQuestionTimeSec": 66,
            "averageSessionTimeSec": 126,
        }
        user = SimpleNamespace(user_id=7)

        summary = build_learning_summary(user, today)

        get_completed_sessions_mock.assert_called_once_with(user.user_id)
        get_weekly_practice_summary_mock.assert_called_once_with(user.user_id, today)
        self.assertEqual(summary["answer_rate"], 75)
        self.assertEqual(summary["solved_count"], 8)
        self.assertEqual(summary["study_streak_days"], 2)
        self.assertEqual(summary["avg_question_time"], "01:06")
        self.assertEqual(summary["avg_session_time"], "02:06")

    @patch("analytics.service.mypage.get_completed_weekly_review_sessions")
    @patch("analytics.service.mypage.get_recent_wrong_rate_period")
    @patch("analytics.service.mypage.get_completed_records")
    def test_wrong_type_summary_exposes_recent_records(
        self,
        get_completed_records_mock,
        get_recent_wrong_rate_period_mock,
        get_completed_weekly_review_sessions_mock,
    ):
        today = date(2026, 7, 13)
        period = {
            "startDate": date(2026, 7, 7),
            "endDate": today,
            "label": "최근 7일",
        }
        get_recent_wrong_rate_period_mock.return_value = period
        completed_records = Mock()
        filtered_records = completed_records.filter.return_value
        grouped_records = filtered_records.values.return_value
        grouped_records.annotate.return_value = [
            {"q_type": "연도", "total": 4, "wrong": 2},
            {"q_type": None, "total": 1, "wrong": 1},
        ]
        get_completed_records_mock.return_value = completed_records
        get_completed_weekly_review_sessions_mock.return_value.last.return_value = None
        user = SimpleNamespace(user_id=7)

        summary = build_wrong_type_summary(user, today)

        get_recent_wrong_rate_period_mock.assert_called_once_with(today)
        completed_records.filter.assert_called_once_with(
            session__recorded_date__gte=period["startDate"],
            session__recorded_date__lte=period["endDate"],
        )
        self.assertTrue(summary["has_records"])
        self.assertEqual(summary["overall_rate"], 60)
        self.assertEqual(summary["items"][0]["label"], "미분류")
        self.assertEqual(summary["items"][0]["rate"], 100)
        self.assertEqual(summary["period_label"], period["label"])
        self.assertEqual(summary["source"], "recent_learning")

    @patch("analytics.service.mypage.get_completed_weekly_review_sessions")
    @patch("analytics.service.mypage.get_recent_wrong_rate_period")
    @patch("analytics.service.mypage.get_completed_records")
    def test_wrong_type_summary_prefers_latest_weekly_review(
        self,
        get_completed_records_mock,
        get_recent_wrong_rate_period_mock,
        get_completed_weekly_review_sessions_mock,
    ):
        latest_weekly_review = SimpleNamespace(
            session_id=20,
            recorded_date=date(2026, 7, 13),
        )
        get_completed_weekly_review_sessions_mock.return_value.last.return_value = (
            latest_weekly_review
        )
        get_recent_wrong_rate_period_mock.return_value = {
            "startDate": date(2026, 7, 7),
            "endDate": date(2026, 7, 13),
            "label": "최근 7일",
        }
        completed_records = Mock()
        weekly_records = completed_records.filter.return_value
        grouped_records = weekly_records.values.return_value
        grouped_records.annotate.return_value = [
            {"q_type": "사료", "total": 10, "wrong": 4},
        ]
        get_completed_records_mock.return_value = completed_records

        summary = build_wrong_type_summary(
            SimpleNamespace(user_id=7),
            date(2026, 7, 13),
        )

        completed_records.filter.assert_called_once_with(session=latest_weekly_review)
        self.assertEqual(summary["period_label"], "07.13 주간평가")
        self.assertEqual(summary["source"], "weekly_review")
        self.assertEqual(summary["overall_rate"], 40)

    @patch("analytics.service.mypage.get_completed_weekly_review_sessions")
    @patch("analytics.service.mypage.get_recent_wrong_rate_period")
    @patch("analytics.service.mypage.get_completed_records")
    def test_wrong_rate_summary_uses_classification_display_label(
        self,
        get_completed_records_mock,
        get_recent_wrong_rate_period_mock,
        get_completed_weekly_review_sessions_mock,
    ):
        today = date(2026, 7, 13)
        get_recent_wrong_rate_period_mock.return_value = {
            "startDate": date(2026, 7, 7),
            "endDate": today,
            "label": "recent period",
        }
        completed_records = Mock()
        grouped_records = completed_records.filter.return_value.values.return_value
        grouped_records.annotate.return_value = [
            {"era": "\uc870\uc120\uc2dc\ub300", "total": 2, "wrong": 1},
        ]
        get_completed_records_mock.return_value = completed_records
        get_completed_weekly_review_sessions_mock.return_value.last.return_value = None

        summary = build_wrong_rate_summary(
            SimpleNamespace(user_id=7),
            "era",
            today,
        )

        self.assertTrue(summary["has_records"])
        self.assertEqual(summary["items"][0]["label"], "\uc870\uc120")
        self.assertEqual(summary["items"][0]["rate"], 50)

    @patch("analytics.service.mypage.get_user_study_info")
    def test_d_day_label_uses_exam_date(self, get_user_study_info_mock):
        today = date(2026, 7, 13)
        user = SimpleNamespace(user_id=7)
        cases = [
            (today + timedelta(days=10), "D - 10"),
            (today, "D-day"),
            (today - timedelta(days=3), "D + 3"),
        ]

        for exam_date, expected_label in cases:
            with self.subTest(exam_date=exam_date):
                get_user_study_info_mock.return_value = SimpleNamespace(
                    exam_date=exam_date,
                )
                self.assertEqual(build_d_day_label(user, today), expected_label)

    @patch("analytics.service.mypage.get_user_study_info", return_value=None)
    def test_d_day_label_handles_missing_profile(self, get_user_study_info_mock):
        user = SimpleNamespace(user_id=7)

        label = build_d_day_label(user, date(2026, 7, 13))

        get_user_study_info_mock.assert_called_once_with(user.user_id)
        self.assertEqual(label, "미설정")


class DiagnosisComparisonTests(SimpleTestCase):
    def test_first_weekly_review_compares_with_initial_diagnosis(self):
        diagnosis_session = SimpleNamespace(
            session_id=10,
            recorded_date=date(2026, 7, 1),
        )
        weekly_session = SimpleNamespace(
            session_id=20,
            recorded_date=date(2026, 7, 8),
        )
        diagnosis_queryset = Mock()
        diagnosis_queryset.exclude.return_value = [diagnosis_session]

        with (
            patch(
                "analytics.service.analytics.get_completed_weekly_review_sessions",
                return_value=[weekly_session],
            ),
            patch(
                "analytics.service.analytics.get_completed_diagnostic_sessions",
                return_value=diagnosis_queryset,
            ),
            patch(
                "analytics.service.analytics.build_evaluation_session_summary",
                side_effect=lambda session, label, number: {
                    "sessionId": session.session_id,
                    "sessionLabel": label,
                    "sessionNumber": number,
                },
            ),
        ):
            pair = get_diagnosis_comparison_pair(7)

        self.assertEqual(pair["diagnosis"]["sessionLabel"], "직전 진단평가")
        self.assertEqual(pair["current"]["sessionLabel"], "1주차 주간평가")

    def test_later_weekly_review_compares_with_previous_weekly_review(self):
        weekly_sessions = [
            SimpleNamespace(session_id=20, recorded_date=date(2026, 7, 8)),
            SimpleNamespace(session_id=30, recorded_date=date(2026, 7, 15)),
        ]
        diagnosis_queryset = Mock()
        diagnosis_queryset.exclude.return_value = []

        with (
            patch(
                "analytics.service.analytics.get_completed_weekly_review_sessions",
                return_value=weekly_sessions,
            ),
            patch(
                "analytics.service.analytics.get_completed_diagnostic_sessions",
                return_value=diagnosis_queryset,
            ),
            patch(
                "analytics.service.analytics.build_evaluation_session_summary",
                side_effect=lambda session, label, number: {
                    "sessionId": session.session_id,
                    "sessionLabel": label,
                    "sessionNumber": number,
                },
            ),
        ):
            pair = get_diagnosis_comparison_pair(7)

        self.assertEqual(pair["diagnosis"]["sessionLabel"], "1주차 주간평가")
        self.assertEqual(pair["current"]["sessionLabel"], "2주차 주간평가")


class PlannerDisplayTests(SimpleTestCase):
    def test_expired_weekly_plan_exposes_next_plan_button(self):
        today = date(2026, 7, 20)
        study_plans = [
            {
                "studyPlanId": 1,
                "startDate": "2026-07-13",
                "endDate": "2026-07-19",
                "progress": {
                    "targetCount": 50,
                    "achievedCount": 0,
                    "remainingCount": 50,
                    "completionRate": 0,
                    "completionPercent": 0,
                    "periodLabel": "07.13 - 07.19",
                },
                "plans": [
                    {
                        "date": "2026-07-19",
                        "blocks": [
                            {
                                "blockId": "weekly-review",
                                "blockType": "weekly_review",
                                "label": "주간 평가",
                                "questionCount": 50,
                                "isAchieved": False,
                            }
                        ],
                    }
                ],
            }
        ]

        summary = build_planner_summary(study_plans, today)

        weekly_item = summary["data"]["plansByDate"]["2026-07-19"][0]
        self.assertTrue(summary["is_expired_plan"])
        self.assertTrue(summary["can_create_plan"])
        self.assertFalse(summary["show_add_extra_study"])
        self.assertEqual(summary["create_plan_label"], "다음 7일 계획 만들기")
        self.assertIn("미응시", weekly_item["meta"])

    def test_future_plan_dates_remain_visible(self):
        today = date(2026, 7, 13)
        study_plans = [
            {
                "studyPlanId": 1,
                "startDate": "2026-07-13",
                "endDate": "2026-07-19",
                "plans": [
                    {
                        "date": "2026-07-13",
                        "blocks": [
                            {
                                "blockId": "today-block",
                                "blockType": "newWeakness",
                                "label": "오늘 학습",
                                "questionCount": 5,
                                "isAchieved": False,
                            }
                        ],
                    },
                    {
                        "date": "2026-07-19",
                        "blocks": [
                            {
                                "blockId": "weekly-review",
                                "blockType": "weekly_review",
                                "label": "주간 평가",
                                "questionCount": 50,
                                "isAchieved": False,
                            }
                        ],
                    },
                ],
            }
        ]

        summary = build_planner_summary(study_plans, today)

        self.assertEqual(
            sorted(summary["data"]["plansByDate"]),
            ["2026-07-13", "2026-07-19"],
        )
        self.assertEqual(
            summary["data"]["plannedKeys"],
            ["2026-07-13", "2026-07-19"],
        )

    def test_completed_weekly_review_does_not_expose_manual_plan_button(self):
        today = date(2026, 7, 20)
        study_plans = [
            {
                "studyPlanId": 1,
                "startDate": "2026-07-13",
                "endDate": "2026-07-19",
                "dailyAvailableMinutes": 60,
                "plans": [
                    {
                        "date": "2026-07-13",
                        "blocks": [
                            {
                                "blockId": "learning",
                                "blockType": "newWeakness",
                                "questionCount": 5,
                                "estimatedMinutes": 60,
                                "isAchieved": True,
                            }
                        ],
                    },
                    {
                        "date": "2026-07-19",
                        "blocks": [
                            {
                                "blockId": "weekly-review",
                                "blockType": "weekly_review",
                                "questionCount": 50,
                                "estimatedMinutes": 80,
                                "isAchieved": True,
                            }
                        ],
                    },
                ],
            }
        ]

        summary = build_planner_summary(study_plans, today)

        self.assertTrue(summary["is_expired_plan"])
        self.assertFalse(summary["can_create_plan"])
        self.assertFalse(summary["show_add_extra_study"])

    def test_generation_unavailable_hides_repeated_empty_plan_button(self):
        summary = build_planner_summary(
            [],
            date(2026, 7, 13),
            plan_generation_available=False,
        )

        self.assertFalse(summary["can_create_plan"])
        self.assertFalse(summary["show_add_extra_study"])

    def test_overloaded_plan_exposes_regeneration_button(self):
        today = date(2026, 7, 13)
        study_plans = [
            {
                "studyPlanId": 1,
                "startDate": "2026-07-13",
                "endDate": "2026-07-19",
                "dailyAvailableMinutes": 60,
                "plans": [
                    {
                        "date": today.isoformat(),
                        "blocks": [
                            {
                                "blockId": "first",
                                "blockType": "newWeakness",
                                "questionCount": 5,
                                "estimatedMinutes": 75,
                                "isAchieved": False,
                            },
                            {
                                "blockId": "second",
                                "blockType": "newWeakness",
                                "questionCount": 5,
                                "estimatedMinutes": 60,
                                "isAchieved": False,
                            },
                        ],
                    }
                ],
            }
        ]

        summary = build_planner_summary(study_plans, today)

        self.assertTrue(summary["is_overloaded_plan"])
        self.assertTrue(summary["can_create_plan"])
        self.assertEqual(summary["create_plan_label"], "학습계획 재생성")

    def test_wrong_rate_display_sorts_by_raw_rate(self):
        stats = [
            {
                "label": "오답률 90%",
                "groupKeyId": "era=a",
                "groupKey": [],
                "total": 10,
                "wrong": 9,
                "rate": 90,
                "averageTimeSec": 10,
            },
            {
                "label": "오답률 50%",
                "groupKeyId": "era=b",
                "groupKey": [],
                "total": 10,
                "wrong": 5,
                "rate": 50,
                "averageTimeSec": 10,
            },
        ]
        weakness_rows = [
            {
                "groupKeyId": "era=a",
                "status": "NEUTRAL",
                "weaknessScore": 0.1,
                "trend": {},
            },
            {
                "groupKeyId": "era=b",
                "status": "WEAK",
                "weaknessScore": 0.9,
                "trend": {},
            },
        ]

        items = build_wrong_rate_display(stats, weakness_rows)

        self.assertEqual(
            [item["label"] for item in items],
            ["오답률 90%", "오답률 50%"],
        )


class StudyPlanSynchronizationTests(SimpleTestCase):
    def test_empty_active_plan_is_regenerated(self):
        today = date(2026, 7, 13)
        active_plan = SimpleNamespace(
            study_plan_items="[]",
            start_date=today,
            end_date=today + timedelta(days=6),
            modified_at=None,
            created_at=None,
        )
        active_plans = Mock()
        active_plans.first.return_value = active_plan
        created_plan = {"studyPlanId": 2}

        with (
            patch(
                "analytics.service.studyplan.transaction.atomic",
                return_value=nullcontext(),
            ),
            patch("analytics.service.studyplan.lock_study_plan_user") as lock_user_mock,
            patch(
                "analytics.service.studyplan.get_active_study_plans",
                return_value=active_plans,
            ),
            patch(
                "analytics.service.studyplan.create_study_plan",
                return_value=created_plan,
            ) as create_plan_mock,
        ):
            result = ensure_today_study_plan(7, today)

        lock_user_mock.assert_called_once_with(7)
        create_plan_mock.assert_called_once_with(7)
        self.assertEqual(result, created_plan)

    def test_carry_over_keeps_completed_and_weekly_review_blocks(self):
        today = date(2026, 7, 13)
        plan_items = [
            {
                "date": "2026-07-12",
                "blocks": [
                    {
                        "blockId": "incomplete",
                        "blockType": "newWeakness",
                        "isCompleted": False,
                    },
                    {
                        "blockId": "completed",
                        "blockType": "newWeakness",
                        "isCompleted": True,
                    },
                    {
                        "blockId": "weekly-review",
                        "blockType": "weekly_review",
                        "isCompleted": False,
                    },
                ],
            }
        ]

        result = carry_over_incomplete_past_blocks_to_today(plan_items, today)

        blocks_by_date = {
            day_plan["date"]: [
                block["blockId"]
                for block in day_plan["blocks"]
            ]
            for day_plan in result["items"]
        }
        self.assertEqual(
            blocks_by_date["2026-07-12"],
            ["completed", "weekly-review"],
        )
        self.assertEqual(blocks_by_date["2026-07-13"], ["incomplete"])

    def test_review_target_uses_canonical_group_key(self):
        first_block = {
            "groupKeyId": "era=조선|topic=정치|q_type=사료",
            "classification": "복합",
            "label": "기존 표시명",
        }
        renamed_block = {
            "groupKeyId": "era=조선|topic=정치|q_type=사료",
            "classification": "복합",
            "label": "변경된 표시명",
        }

        self.assertEqual(
            get_review_block_target_key(first_block),
            get_review_block_target_key(renamed_block),
        )

    def test_active_plan_unique_violation_checks_constraint_name(self):
        error = IntegrityError()
        cause = Exception()
        cause.diag = SimpleNamespace(
            constraint_name="study_plan_mypage_user_active_uidx",
        )
        error.__cause__ = cause

        self.assertTrue(is_active_study_plan_unique_violation(error))
        self.assertFalse(is_active_study_plan_unique_violation(IntegrityError()))

    def test_empty_plan_is_rejected_before_existing_plan_is_archived(self):
        with (
            patch("analytics.service.studyplan.transaction.atomic") as atomic_mock,
            self.assertRaises(StudyPlanGenerationUnavailable),
        ):
            create_study_plan(7, study_plan_items=[])

        atomic_mock.assert_not_called()

    def test_delete_block_locks_only_the_active_plan(self):
        today = date.today().isoformat()
        active_plan = SimpleNamespace(
            studyplan_id=1,
            study_plans="summary",
            study_plan_items=json.dumps(
                [
                    {
                        "date": today,
                        "blocks": [
                            {
                                "blockId": "learning",
                                "blockType": "newWeakness",
                                "isCompleted": False,
                            }
                        ],
                    }
                ]
            ),
        )
        active_plans = Mock()
        active_plans.filter.return_value.first.return_value = active_plan

        with (
            patch(
                "analytics.service.studyplan.transaction.atomic",
                return_value=nullcontext(),
            ),
            patch(
                "analytics.service.studyplan.get_active_study_plans",
                return_value=active_plans,
            ) as active_plans_mock,
            patch("analytics.service.studyplan.refill_deleted_plan_block"),
            patch(
                "analytics.service.studyplan.update_study_plan",
                return_value={"studyPlanId": 1},
            ) as update_mock,
        ):
            result = delete_study_plan_block(7, 1, 0, 0)

        active_plans_mock.assert_called_once_with(7, lock=True)
        active_plans.filter.assert_called_once_with(studyplan_id=1)
        update_mock.assert_called_once()
        self.assertEqual(result, {"studyPlanId": 1})

    def test_complete_block_by_id_locks_only_the_active_plan(self):
        active_plan = SimpleNamespace(
            studyplan_id=1,
            study_plans="summary",
            study_plan_items=json.dumps(
                [
                    {
                        "date": "2026-07-13",
                        "blocks": [
                            {
                                "blockId": "learning",
                                "blockType": "newWeakness",
                                "isCompleted": False,
                            }
                        ],
                    }
                ]
            ),
        )
        active_plans = Mock()
        active_plans.filter.return_value.first.return_value = active_plan

        with (
            patch(
                "analytics.service.studyplan.transaction.atomic",
                return_value=nullcontext(),
            ),
            patch(
                "analytics.service.studyplan.get_active_study_plans",
                return_value=active_plans,
            ) as active_plans_mock,
            patch(
                "analytics.service.studyplan.update_study_plan",
                return_value={"studyPlanId": 1},
            ) as update_mock,
        ):
            result = complete_study_plan_block_by_id(7, 1, "learning")

        active_plans_mock.assert_called_once_with(7, lock=True)
        active_plans.filter.assert_called_once_with(studyplan_id=1)
        updated_items = update_mock.call_args.args[3]
        self.assertTrue(updated_items[0]["blocks"][0]["isCompleted"])
        self.assertEqual(result, {"studyPlanId": 1})


class StudyPlanGenerationTests(SimpleTestCase):
    def setUp(self):
        self.config = get_study_plan_config()
        self.today = date(2026, 7, 13)
        self.weak_target = {
            "groupKeyId": "era=조선|topic=정치|q_type=사료",
            "classification": "복합",
            "label": "조선 · 정치 · 사료",
            "era": "조선",
            "topic": "정치",
            "qType": "사료",
            "wrongRate": 0.7,
            "weaknessScore": 0.8,
            "predictionScore": 0.4,
            "priorityScore": 0.8,
            "averageTimeSec": 60,
            "reason": "취약 영역",
            "planSource": "normal",
        }
        self.prediction_target = {
            "groupKeyId": "era=고려|topic=경제|q_type=개념",
            "classification": "복합",
            "label": "고려 · 경제 · 개념",
            "era": "고려",
            "topic": "경제",
            "qType": "개념",
            "wrongRate": 0,
            "weaknessScore": 0,
            "predictionScore": 0.9,
            "priorityScore": 0.9,
            "averageTimeSec": 60,
            "reason": "출제 예상",
            "planSource": "fallback_prediction_only",
        }

    def test_short_term_plan_uses_only_weak_targets_without_weekly_review(self):
        plans = build_daily_plan_items(
            [self.prediction_target, self.weak_target],
            daily_available_minutes=60,
            remaining_days=3,
            today=self.today,
            config=self.config,
        )

        self.assertEqual(len(plans), 3)
        self.assertEqual(plans[-1]["date"], "2026-07-15")
        self.assertTrue(
            all(
                block["groupKeyId"] == self.weak_target["groupKeyId"]
                for day_plan in plans
                for block in day_plan["blocks"]
            )
        )
        self.assertTrue(
            all(
                block["blockType"] == "newWeakness"
                for day_plan in plans
                for block in day_plan["blocks"]
            )
        )
        self.assertFalse(
            any(
                block["blockType"] == self.config["weekly_review_block_type"]
                for day_plan in plans
                for block in day_plan["blocks"]
            )
        )

    def test_seven_day_plan_keeps_weekly_review_on_last_day(self):
        plans = build_daily_plan_items(
            [self.weak_target],
            daily_available_minutes=60,
            remaining_days=7,
            today=self.today,
            config=self.config,
        )

        self.assertEqual(len(plans), 7)
        self.assertEqual(
            plans[-1]["blocks"][0]["blockType"],
            self.config["weekly_review_block_type"],
        )


class TaxonomyTests(SimpleTestCase):
    def test_target_label_normalizes_values_and_handles_missing_value(self):
        label = build_target_display_label(
            "조선시대",
            "정치",
            "",
        )

        self.assertEqual(label, "조선 · 정치 · 미분류")
