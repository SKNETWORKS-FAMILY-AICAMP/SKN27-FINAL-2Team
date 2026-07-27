"""학습계획 우선순위 대상 구성의 특성화 테스트.

build_user_plan_draft 안에 인라인으로 들어 있는 "DB 조회 → PlanTarget 구성"
구간을 build_plan_targets 로 분리하기 전에 현재 동작을 고정한다.
주간 리포트 collector 가 같은 구간을 재사용해야 하므로, 분리 후에도
build_priority_targets 에 넘어가는 값과 최종 draft 가 동일해야 한다.

프로젝트 관례에 따라 DB 를 쓰지 않고 ORM 을 mock 으로 대체한다.
"""

from __future__ import annotations

from datetime import date
from unittest import TestCase
from unittest.mock import MagicMock, patch

from analytics.service.study_plan import service as plan_service
from analytics.service.study_plan.service import InitialStudyPlanConfigUnavailable
from analytics.service.taxonomy import build_group_key_id
from analytics.service.weakness import build_weakness_rows


TODAY = date(2026, 7, 26)
EXAM_DATE = date(2026, 9, 30)

JOSEON_POLITICS = build_group_key_id({"era": "조선", "topic": "정치"})
GORYEO_ECONOMY = build_group_key_id({"era": "고려", "topic": "경제"})

# 조선·정치는 최근 3세션에 모두 등장하고 모두 틀렸다 → repeated_error 1.0
# 고려·경제는 2세션에만 등장한다 → 최소 3세션 조건에 걸려 repeated_error 0.0
RECORD_ROWS = [
    {"session_id": 101, "session__recorded_date": date(2026, 7, 24), "is_correct": False,
     "time_spent_ms": 90000, "era": "조선", "topic": "정치"},
    {"session_id": 101, "session__recorded_date": date(2026, 7, 24), "is_correct": False,
     "time_spent_ms": 80000, "era": "조선", "topic": "정치"},
    {"session_id": 102, "session__recorded_date": date(2026, 7, 25), "is_correct": False,
     "time_spent_ms": 70000, "era": "조선", "topic": "정치"},
    {"session_id": 102, "session__recorded_date": date(2026, 7, 25), "is_correct": True,
     "time_spent_ms": 60000, "era": "고려", "topic": "경제"},
    {"session_id": 103, "session__recorded_date": date(2026, 7, 26), "is_correct": True,
     "time_spent_ms": 50000, "era": "고려", "topic": "경제"},
    {"session_id": 103, "session__recorded_date": date(2026, 7, 26), "is_correct": False,
     "time_spent_ms": 55000, "era": "조선", "topic": "정치"},
]

QUESTION_ROWS = [
    {"era": "조선", "topic": "정치", "q_score": 1, "question_count": 20},
    {"era": "조선", "topic": "정치", "q_score": 2, "question_count": 30},
    {"era": "조선", "topic": "정치", "q_score": 3, "question_count": 10},
    {"era": "고려", "topic": "경제", "q_score": 1, "question_count": 15},
    {"era": "고려", "topic": "경제", "q_score": 2, "question_count": 25},
    {"era": "고려", "topic": "경제", "q_score": 3, "question_count": 8},
]


class StudyPlanTargetCharacterizationTests(TestCase):
    def build_draft(
        self,
        record_rows=None,
        question_rows=None,
        recent_session_ids=(103, 102, 101),
        exam_date=EXAM_DATE,
        daily_available_hours=2,
    ) -> dict[str, object]:
        """build_user_plan_draft 를 mock ORM 위에서 실행하고 draft 를 돌려준다.

        build_priority_targets 에 넘어간 인자는 self.captured 에 남긴다.
        """
        profile = MagicMock()
        profile.exam_date = exam_date
        profile.daily_available_hours = daily_available_hours

        records_model = MagicMock()
        records_model.objects.filter.return_value.values.return_value = (
            RECORD_ROWS if record_rows is None else record_rows
        )
        sessions_model = MagicMock()
        sessions_model.objects.filter.return_value.order_by.return_value.values_list.return_value = list(
            recent_session_ids,
        )
        questions_model = MagicMock()
        questions_model.objects.values.return_value.annotate.return_value.order_by.return_value = (
            QUESTION_ROWS if question_rows is None else question_rows
        )

        self.captured: dict[str, object] = {}
        real_build_priority_targets = plan_service.build_priority_targets

        def spy(targets, days_until_exam, config=None):
            self.captured["targets"] = list(targets)
            self.captured["daysUntilExam"] = days_until_exam
            return real_build_priority_targets(targets, days_until_exam, config)

        with patch("user.models.UserAccounts") as user_model, \
                patch("question.models.SolveRecords", records_model), \
                patch("question.models.SolveSessions", sessions_model), \
                patch("question.models.Questions", questions_model), \
                patch.object(plan_service, "build_priority_targets", spy):
            user_model.objects.get.return_value = profile
            return plan_service.build_user_plan_draft(1, TODAY)

    def get_target(self, group_key_id: str):
        for target in self.captured["targets"]:
            if target.group_key_id == group_key_id:
                return target
        raise AssertionError(f"대상이 없습니다. {group_key_id}")

    def test_weak_group_target_fields_are_stable(self) -> None:
        self.build_draft()
        target = self.get_target(JOSEON_POLITICS)

        self.assertEqual(target.label, "조선 · 정치")
        self.assertEqual(target.era, "조선")
        self.assertEqual(target.topic, "정치")
        self.assertEqual(target.weakness_status, "WEAK")
        self.assertAlmostEqual(target.weakness_score, 0.6967, places=4)
        self.assertAlmostEqual(target.effective_total, 3.7631, places=4)
        self.assertEqual(target.exam_question_count, 60)
        self.assertEqual(target.average_seconds_per_question, 74)

    def test_repeated_error_needs_three_eligible_sessions(self) -> None:
        """최근 5세션 중 그룹이 등장한 세션이 3개 미만이면 판단을 보류한다."""
        self.build_draft()

        self.assertEqual(self.get_target(JOSEON_POLITICS).repeated_error, 1.0)
        self.assertEqual(self.get_target(GORYEO_ECONOMY).repeated_error, 0.0)

    def test_insufficient_sample_group_is_not_weak(self) -> None:
        self.build_draft()
        target = self.get_target(GORYEO_ECONOMY)

        self.assertEqual(target.weakness_status, "INSUFFICIENT")
        self.assertEqual(target.weakness_score, 0.0)
        self.assertEqual(target.exam_question_count, 48)

    def test_days_until_exam_comes_from_profile(self) -> None:
        self.build_draft()

        self.assertEqual(self.captured["daysUntilExam"], 66)

    def test_no_exam_date_leaves_days_until_exam_unset(self) -> None:
        self.build_draft(exam_date=None)

        self.assertIsNone(self.captured["daysUntilExam"])

    def test_only_groups_present_in_the_question_pool_become_targets(self) -> None:
        """문제은행에 없는 분류는 풀이 기록이 있어도 대상이 되지 않는다."""
        self.build_draft()
        group_key_ids = {target.group_key_id for target in self.captured["targets"]}

        self.assertEqual(group_key_ids, {JOSEON_POLITICS, GORYEO_ECONOMY})

    def test_draft_shape_is_stable(self) -> None:
        draft = self.build_draft()

        self.assertEqual(draft["summary"]["schemaVersion"], "2")
        self.assertEqual(draft["summary"]["generationReason"], "personalized")
        self.assertEqual(draft["summary"]["configVersion"], "study-plan-v2")
        self.assertEqual(draft["startDate"], "2026-07-26")
        self.assertEqual(draft["endDate"], "2026-08-01")
        self.assertEqual(draft["dailyAvailableMinutes"], 120)
        self.assertEqual([len(day["blocks"]) for day in draft["plans"]], [4, 4, 4, 4, 4, 4, 1])

    def test_weakest_group_takes_the_first_block(self) -> None:
        draft = self.build_draft()
        first_block = draft["plans"][0]["blocks"][0]

        self.assertEqual(first_block["groupKeyId"], JOSEON_POLITICS)
        self.assertEqual(first_block["blockType"], "practice")
        self.assertEqual(first_block["estimatedMinutes"], 30)
        # 블록 문항 수는 config.maximum_question_count 로 제한된다.
        self.assertEqual(first_block["questionCount"], 20)
        self.assertAlmostEqual(first_block["priorityScore"], 0.8332, places=4)

    def test_last_day_is_the_weekly_review(self) -> None:
        draft = self.build_draft()
        last_day = draft["plans"][-1]

        self.assertEqual(len(last_day["blocks"]), 1)
        self.assertEqual(last_day["blocks"][0]["blockType"], "weekly_review")

    def test_no_records_is_rejected_before_touching_the_question_pool(self) -> None:
        with self.assertRaises(InitialStudyPlanConfigUnavailable):
            self.build_draft(record_rows=[])


class BuildPlanTargetsTests(TestCase):
    """분리된 build_plan_targets 를 직접 호출한다. 주간 리포트 collector 의 진입점이다."""

    def test_returns_priority_targets_and_question_counts(self) -> None:
        profile = MagicMock()
        profile.exam_date = EXAM_DATE
        records_model = MagicMock()
        records_model.objects.filter.return_value.values.return_value = RECORD_ROWS
        sessions_model = MagicMock()
        sessions_model.objects.filter.return_value.order_by.return_value.values_list.return_value = [
            103,
            102,
            101,
        ]
        questions_model = MagicMock()
        questions_model.objects.values.return_value.annotate.return_value.order_by.return_value = (
            QUESTION_ROWS
        )

        with patch("user.models.UserAccounts") as user_model, \
                patch("question.models.SolveRecords", records_model), \
                patch("question.models.SolveSessions", sessions_model), \
                patch("question.models.Questions", questions_model):
            user_model.objects.get.return_value = profile
            priority_targets, counts_by_group = plan_service.build_plan_targets(1, TODAY)

        # build_priority_targets 는 WEAK / NEUTRAL 만 남긴다. INSUFFICIENT 인
        # 고려·경제는 우선순위 대상에서 빠진다. 문항 수는 문제은행 기준이라 그대로 남는다.
        self.assertEqual(
            {target.group_key_id for target in priority_targets},
            {JOSEON_POLITICS},
        )
        self.assertEqual(counts_by_group[JOSEON_POLITICS], {1: 20, 2: 30, 3: 10})
        self.assertEqual(counts_by_group[GORYEO_ECONOMY], {1: 15, 2: 25, 3: 8})

    def test_repeated_error_is_reusable_as_a_lookup_by_group_key(self) -> None:
        """주간 리포트가 이 값을 groupKeyId 로 조회한다. 키 규칙이 맞아야 한다."""
        profile = MagicMock()
        profile.exam_date = EXAM_DATE
        records_model = MagicMock()
        records_model.objects.filter.return_value.values.return_value = RECORD_ROWS
        sessions_model = MagicMock()
        sessions_model.objects.filter.return_value.order_by.return_value.values_list.return_value = [
            103,
            102,
            101,
        ]
        questions_model = MagicMock()
        questions_model.objects.values.return_value.annotate.return_value.order_by.return_value = (
            QUESTION_ROWS
        )

        with patch("user.models.UserAccounts") as user_model, \
                patch("question.models.SolveRecords", records_model), \
                patch("question.models.SolveSessions", sessions_model), \
                patch("question.models.Questions", questions_model):
            user_model.objects.get.return_value = profile
            priority_targets, _ = plan_service.build_plan_targets(1, TODAY)

        repeated_error_by_group = {
            target.group_key_id: target.repeated_error for target in priority_targets
        }
        weakness_rows = build_weakness_rows(RECORD_ROWS, ("era", "topic"), TODAY)
        weak_row = next(row for row in weakness_rows if row["status"] == "WEAK")

        self.assertIn(str(weak_row["groupKeyId"]), repeated_error_by_group)
        self.assertEqual(repeated_error_by_group[str(weak_row["groupKeyId"])], 1.0)
