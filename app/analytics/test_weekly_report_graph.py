from __future__ import annotations

from copy import deepcopy
from typing import Mapping
from unittest import TestCase

from analytics.service.weekly_report.agents import (
    GroundedStatement,
    LangGraphWeeklyReportAgentSuite,
    ReportCritique,
    ReportDraft,
    StudyCoaching,
    WeaknessAnalysis,
)
from analytics.service.weekly_report.graph import generate_graph_report_content


class FakeWeeklyReportAgentSuite:
    def __init__(
        self,
        drafts: list[ReportDraft] | None = None,
        critiques: list[ReportCritique] | None = None,
        analysis: WeaknessAnalysis | None = None,
        coaching: StudyCoaching | None = None,
        failure_stage: str | None = None,
        mutate_inputs: bool = False,
    ) -> None:
        self.calls: list[str] = []
        self.report_types: list[str | None] = []
        self.writer_feedback: list[list[str]] = []
        self._drafts = list(drafts or [self.valid_draft()])
        self._critiques = list(critiques or [self.passing_critique()])
        self._analysis = analysis or self.valid_analysis()
        self._coaching = coaching or self.valid_coaching()
        self._failure_stage = failure_stage
        self._mutate_inputs = mutate_inputs

    def analyze(
        self,
        report_type: str | None,
        result: Mapping[str, object],
    ) -> WeaknessAnalysis:
        self.calls.append("analyst")
        self.report_types.append(report_type)
        self._raise_if_requested("analyst")
        if self._mutate_inputs:
            targets = result.get("nextPlanTargets")
            if isinstance(targets, list):
                targets.clear()
        return self._analysis

    def coach(
        self,
        report_type: str | None,
        result: Mapping[str, object],
        analysis: Mapping[str, object],
    ) -> StudyCoaching:
        self.calls.append("coach")
        self.report_types.append(report_type)
        self._raise_if_requested("coach")
        return self._coaching

    def write(
        self,
        report_type: str | None,
        result: Mapping[str, object],
        analysis: Mapping[str, object],
        coaching: Mapping[str, object],
        feedback: list[str],
    ) -> ReportDraft:
        self.calls.append("writer")
        self.report_types.append(report_type)
        self._raise_if_requested("writer")
        self.writer_feedback.append(list(feedback))
        return self._drafts.pop(0)

    def critique(
        self,
        report_type: str | None,
        result: Mapping[str, object],
        analysis: Mapping[str, object],
        coaching: Mapping[str, object],
        draft: Mapping[str, object],
    ) -> ReportCritique:
        self.calls.append("critic")
        self.report_types.append(report_type)
        self._raise_if_requested("critic")
        return self._critiques.pop(0)

    def _raise_if_requested(self, stage: str) -> None:
        if self._failure_stage == stage:
            raise RuntimeError("provider error")

    @staticmethod
    def valid_analysis() -> WeaknessAnalysis:
        return WeaknessAnalysis(
            summary=GroundedStatement(
                text="개선 영역과 우선 보완 영역이 함께 확인됩니다.",
                evidenceIds=["strength-1", "priority-1"],
            ),
            findings=[
                GroundedStatement(
                    text="근현대 사회 영역을 우선 보완할 필요가 있습니다.",
                    evidenceIds=["priority-1"],
                )
            ],
        )

    @staticmethod
    def valid_coaching() -> StudyCoaching:
        return StudyCoaching(
            recommendations=[
                GroundedStatement(
                    text="근현대 사회 문제를 짧은 단위로 복습하세요.",
                    evidenceIds=["priority-1"],
                )
            ]
        )

    @staticmethod
    def valid_draft() -> ReportDraft:
        return ReportDraft(
            comment=GroundedStatement(
                text="조선 정치 영역에서 개선 흐름이 확인됐어요.",
                evidenceIds=["strength-1"],
            ),
            tips=[
                GroundedStatement(
                    text="근현대 사회 문제를 짧은 단위로 나누어 복습해 보세요.",
                    evidenceIds=["priority-1"],
                )
            ],
        )

    @staticmethod
    def invalid_evidence_draft() -> ReportDraft:
        return ReportDraft(
            comment=GroundedStatement(
                text="근거가 없는 해석입니다.",
                evidenceIds=["unknown-evidence"],
            ),
            tips=[],
        )

    @staticmethod
    def passing_critique() -> ReportCritique:
        return ReportCritique(passed=True, errorCodes=[], feedback=[])

    @staticmethod
    def failing_critique() -> ReportCritique:
        return ReportCritique(
            passed=False,
            errorCodes=["TONE_REWRITE_REQUIRED"],
            feedback=["표현을 더 중립적으로 수정하세요."],
        )


class WeeklyReportLangGraphTests(TestCase):
    def setUp(self) -> None:
        self.result = {
            "assessment": {
                "evidenceId": "assessment-current",
                "score": 82,
                "totalScore": 100,
            },
            "strengths": [
                {
                    "evidenceId": "strength-1",
                    "groupKeyId": "joseon-politics",
                    "label": "조선 정치",
                }
            ],
            "priorityImprovements": [
                {
                    "evidenceId": "priority-1",
                    "groupKeyId": "modern-society",
                    "label": "근현대 사회",
                }
            ],
            "timeSummary": [],
            "confusionPatterns": [],
            "nextPlanTargets": [
                {
                    "evidenceId": "target-1",
                    "groupKeyId": "modern-society",
                    "label": "근현대 사회",
                }
            ],
        }

    def test_four_agents_run_in_order_and_keep_content_contract(self) -> None:
        agents = FakeWeeklyReportAgentSuite()

        content = generate_graph_report_content(
            self.result,
            agents,
            report_type="weekly",
        )

        self.assertEqual(agents.calls, ["analyst", "coach", "writer", "critic"])
        self.assertEqual(agents.report_types, ["weekly", "weekly", "weekly", "weekly"])
        self.assertFalse(content["fallbackUsed"])
        self.assertEqual(
            content["validation"],
            {"guard": "passed", "validator": "passed"},
        )
        self.assertEqual(set(content), {"comment", "tips", "fallbackUsed", "validation"})

    def test_guard_failure_rewrites_before_critic(self) -> None:
        agents = FakeWeeklyReportAgentSuite(
            drafts=[
                FakeWeeklyReportAgentSuite.invalid_evidence_draft(),
                FakeWeeklyReportAgentSuite.valid_draft(),
            ]
        )

        content = generate_graph_report_content(self.result, agents)

        self.assertFalse(content["fallbackUsed"])
        self.assertEqual(
            agents.calls,
            ["analyst", "coach", "writer", "writer", "critic"],
        )
        self.assertEqual(agents.writer_feedback[0], [])
        self.assertIn("COMMENT_UNKNOWN_EVIDENCE", agents.writer_feedback[1])
        self.assertIn(
            "각 문장의 evidenceIds를 그 문장을 직접 뒷받침하는 입력 근거 번호로 바꾸세요.",
            agents.writer_feedback[1],
        )

    def test_critic_failure_rewrites_and_rechecks(self) -> None:
        agents = FakeWeeklyReportAgentSuite(
            drafts=[
                FakeWeeklyReportAgentSuite.valid_draft(),
                FakeWeeklyReportAgentSuite.valid_draft(),
            ],
            critiques=[
                FakeWeeklyReportAgentSuite.failing_critique(),
                FakeWeeklyReportAgentSuite.passing_critique(),
            ],
        )

        content = generate_graph_report_content(self.result, agents)

        self.assertFalse(content["fallbackUsed"])
        self.assertEqual(
            agents.calls,
            ["analyst", "coach", "writer", "critic", "writer", "critic"],
        )
        self.assertEqual(
            agents.writer_feedback[1],
            ["표현을 더 중립적으로 수정하세요."],
        )

    def test_guard_revision_limit_uses_deterministic_fallback(self) -> None:
        agents = FakeWeeklyReportAgentSuite(
            drafts=[
                FakeWeeklyReportAgentSuite.invalid_evidence_draft(),
                FakeWeeklyReportAgentSuite.invalid_evidence_draft(),
            ]
        )

        content = generate_graph_report_content(self.result, agents)

        self.assertTrue(content["fallbackUsed"])
        self.assertEqual(agents.calls, ["analyst", "coach", "writer", "writer"])

    def test_critic_revision_limit_uses_deterministic_fallback(self) -> None:
        agents = FakeWeeklyReportAgentSuite(
            drafts=[
                FakeWeeklyReportAgentSuite.valid_draft(),
                FakeWeeklyReportAgentSuite.valid_draft(),
            ],
            critiques=[
                FakeWeeklyReportAgentSuite.failing_critique(),
                FakeWeeklyReportAgentSuite.failing_critique(),
            ],
        )

        content = generate_graph_report_content(self.result, agents)

        self.assertTrue(content["fallbackUsed"])
        self.assertEqual(
            agents.calls,
            ["analyst", "coach", "writer", "critic", "writer", "critic"],
        )

    def test_agent_provider_errors_use_fallback_at_each_stage(self) -> None:
        for stage in ("analyst", "coach", "writer", "critic"):
            with self.subTest(stage=stage):
                agents = FakeWeeklyReportAgentSuite(failure_stage=stage)

                content = generate_graph_report_content(self.result, agents)

                self.assertTrue(content["fallbackUsed"])

    def test_intermediate_outputs_reject_disallowed_plan_target_evidence(self) -> None:
        invalid_analysis = WeaknessAnalysis(
            summary=GroundedStatement(
                text="다음 계획 대상을 분석 근거로 사용했습니다.",
                evidenceIds=["target-1"],
            ),
            findings=[],
        )
        agents = FakeWeeklyReportAgentSuite(analysis=invalid_analysis)

        content = generate_graph_report_content(self.result, agents)

        self.assertTrue(content["fallbackUsed"])
        self.assertEqual(agents.calls, ["analyst"])

    def test_analyst_and_coach_input_excludes_plan_and_score_fields(self) -> None:
        evidence = LangGraphWeeklyReportAgentSuite._select_narrative_evidence(
            self.result
        )

        self.assertEqual(
            set(evidence),
            {
                "strengths",
                "priorityImprovements",
                "conceptWeaknesses",
                "examTrends",
                "timeSummary",
                "confusionPatterns",
            },
        )
        # 점수와 학습계획은 해석 단계에 넘기지 않는다.
        self.assertNotIn("assessment", evidence)
        self.assertNotIn("comparison", evidence)
        self.assertNotIn("planProgress", evidence)
        self.assertNotIn("nextPlanTargets", evidence)

    def test_critic_verdict_rejects_passed_with_error_feedback(self) -> None:
        with self.assertRaises(ValueError):
            ReportCritique(
                passed=True,
                errorCodes=["CONTRADICTORY_RESULT"],
                feedback=["통과 판정과 모순됩니다."],
            )

    def test_graph_does_not_change_rule_based_plan_targets(self) -> None:
        original_result = deepcopy(self.result)
        agents = FakeWeeklyReportAgentSuite(mutate_inputs=True)

        content = generate_graph_report_content(self.result, agents)

        self.assertFalse(content["fallbackUsed"])
        self.assertEqual(self.result, original_result)
        self.assertNotIn("nextPlan", content)
