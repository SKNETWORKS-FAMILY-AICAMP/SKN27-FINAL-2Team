from __future__ import annotations

import logging
from copy import deepcopy
from typing import Literal, Mapping, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from analytics.service.weekly_report.agents import (
    LangGraphWeeklyReportAgentSuite,
    ReportCritique,
    ReportDraft,
    StudyCoaching,
    WeaknessAnalysis,
    WeeklyReportAgentSuite,
)
from analytics.service.weekly_report.config import (
    WeeklyReportConfig,
    get_weekly_report_config,
)
from analytics.service.weekly_report.llm import (
    validate_ai_content,
    validate_grounded_statements,
)
from analytics.service.weekly_report.service import build_fallback_content


logger = logging.getLogger(__name__)


class WeeklyReportGraphState(TypedDict, total=False):
    report_type: str | None
    result: dict[str, object]
    weakness_analysis: dict[str, object]
    coaching_recommendations: dict[str, object]
    draft: dict[str, object]
    guard_errors: list[str]
    critic_result: dict[str, object]
    revision_feedback: list[str]
    revision_count: int
    failed_stage: str | None
    content: dict[str, object]


class WeeklyReportGraphWorkflow:
    def __init__(
        self,
        agent_suite: WeeklyReportAgentSuite,
        config: WeeklyReportConfig,
    ) -> None:
        self._agent_suite = agent_suite
        self._config = config

    def compile(self) -> CompiledStateGraph:
        graph = StateGraph(WeeklyReportGraphState)
        graph.add_node("weakness_analyst", self._analyze)
        graph.add_node("study_coach", self._coach)
        graph.add_node("report_writer", self._write)
        graph.add_node("code_guard", self._guard)
        graph.add_node("report_critic", self._critique)
        graph.add_node("finalize", self._finalize)
        graph.add_node("fallback", self._fallback)

        graph.add_edge(START, "weakness_analyst")
        graph.add_conditional_edges(
            "weakness_analyst",
            self._route_after_agent,
            {"continue": "study_coach", "fallback": "fallback"},
        )
        graph.add_conditional_edges(
            "study_coach",
            self._route_after_agent,
            {"continue": "report_writer", "fallback": "fallback"},
        )
        graph.add_conditional_edges(
            "report_writer",
            self._route_after_writer,
            {"guard": "code_guard", "fallback": "fallback"},
        )
        graph.add_conditional_edges(
            "code_guard",
            self._route_after_guard,
            {
                "critic": "report_critic",
                "rewrite": "report_writer",
                "fallback": "fallback",
            },
        )
        graph.add_conditional_edges(
            "report_critic",
            self._route_after_critic,
            {
                "finalize": "finalize",
                "rewrite": "report_writer",
                "fallback": "fallback",
            },
        )
        graph.add_edge("finalize", END)
        graph.add_edge("fallback", END)
        return graph.compile()

    def _analyze(self, state: WeeklyReportGraphState) -> WeeklyReportGraphState:
        try:
            analysis = self._agent_suite.analyze(
                state.get("report_type"),
                deepcopy(state["result"]),
            )
            validated = WeaknessAnalysis.model_validate(analysis)
        except Exception:
            logger.exception("주간 리포트 weakness_analyst 단계 실패")
            return {
                "failed_stage": "weakness_analyst",
                "guard_errors": ["ANALYST_CALL_OR_SCHEMA_ERROR"],
            }
        analysis_data = validated.model_dump(by_alias=True)
        grounding_errors = validate_grounded_statements(
            [analysis_data["summary"], *analysis_data["findings"]],
            state["result"],
            self._config,
            (
                "strengths",
                "priorityImprovements",
                "conceptWeaknesses",
                "examTrends",
                "timeSummary",
                "confusionPatterns",
            ),
        )
        if grounding_errors:
            return {
                "failed_stage": "weakness_analyst_grounding",
                "guard_errors": grounding_errors,
            }
        return {
            "weakness_analysis": analysis_data,
            "failed_stage": None,
        }

    def _coach(self, state: WeeklyReportGraphState) -> WeeklyReportGraphState:
        try:
            coaching = self._agent_suite.coach(
                state.get("report_type"),
                deepcopy(state["result"]),
                deepcopy(state["weakness_analysis"]),
            )
            validated = StudyCoaching.model_validate(coaching)
        except Exception:
            logger.exception("주간 리포트 study_coach 단계 실패")
            return {
                "failed_stage": "study_coach",
                "guard_errors": ["COACH_CALL_OR_SCHEMA_ERROR"],
            }
        coaching_data = validated.model_dump(by_alias=True)
        grounding_errors = validate_grounded_statements(
            coaching_data["recommendations"],
            state["result"],
            self._config,
            (
                "strengths",
                "priorityImprovements",
                "conceptWeaknesses",
                "examTrends",
                "timeSummary",
                "confusionPatterns",
            ),
        )
        if grounding_errors:
            return {
                "failed_stage": "study_coach_grounding",
                "guard_errors": grounding_errors,
            }
        return {
            "coaching_recommendations": coaching_data,
            "failed_stage": None,
        }

    def _write(self, state: WeeklyReportGraphState) -> WeeklyReportGraphState:
        revision_count = int(state.get("revision_count") or 0)
        if state.get("draft"):
            revision_count += 1
        try:
            draft = self._agent_suite.write(
                state.get("report_type"),
                deepcopy(state["result"]),
                deepcopy(state["weakness_analysis"]),
                deepcopy(state["coaching_recommendations"]),
                list(state.get("revision_feedback") or []),
            )
            validated = ReportDraft.model_validate(draft)
        except Exception:
            logger.exception("주간 리포트 report_writer 단계 실패")
            return {
                "failed_stage": "report_writer",
                "guard_errors": ["WRITER_CALL_OR_SCHEMA_ERROR"],
                "revision_count": revision_count,
            }
        return {
            "draft": validated.model_dump(by_alias=True),
            "revision_count": revision_count,
            "failed_stage": None,
        }

    def _guard(self, state: WeeklyReportGraphState) -> WeeklyReportGraphState:
        errors = validate_ai_content(
            state["draft"],
            state["result"],
            self._config,
        )
        if errors:
            return {
                "guard_errors": errors,
                "revision_feedback": self._build_guard_feedback(errors),
            }
        return {"guard_errors": [], "revision_feedback": []}

    def _critique(self, state: WeeklyReportGraphState) -> WeeklyReportGraphState:
        try:
            critique = self._agent_suite.critique(
                state.get("report_type"),
                deepcopy(state["result"]),
                deepcopy(state["weakness_analysis"]),
                deepcopy(state["coaching_recommendations"]),
                deepcopy(state["draft"]),
            )
            validated = ReportCritique.model_validate(critique)
        except Exception:
            logger.exception("주간 리포트 report_critic 단계 실패")
            return {
                "failed_stage": "report_critic",
                "guard_errors": ["CRITIC_CALL_OR_SCHEMA_ERROR"],
            }
        feedback = list(validated.feedback)
        if not feedback and not validated.passed:
            feedback = list(validated.error_codes)
        if not feedback and not validated.passed:
            feedback = ["CRITIC_REJECTED"]
        return {
            "critic_result": validated.model_dump(by_alias=True),
            "revision_feedback": feedback,
            "failed_stage": None,
        }

    def _finalize(self, state: WeeklyReportGraphState) -> WeeklyReportGraphState:
        draft = state["draft"]
        return {
            "content": {
                "comment": dict(draft["comment"]),
                "tips": [dict(item) for item in draft["tips"]],
                "fallbackUsed": False,
                "validation": {"guard": "passed", "validator": "passed"},
            }
        }

    def _fallback(self, state: WeeklyReportGraphState) -> WeeklyReportGraphState:
        return {"content": build_fallback_content(state["result"], self._config)}

    @staticmethod
    def _build_guard_feedback(errors: list[str]) -> list[str]:
        feedback = list(errors)
        if any(error.endswith("_UNSUPPORTED_NUMBER") for error in errors):
            feedback.append(
                "입력 근거와 직접 연결되지 않은 숫자를 제거하세요. 숫자가 꼭 필요하면 "
                "그 값을 직접 포함한 evidenceId만 인용하세요."
            )
        if any(error.endswith("_UNKNOWN_EVIDENCE") for error in errors):
            feedback.append(
                "각 문장의 evidenceIds를 그 문장을 직접 뒷받침하는 입력 근거 번호로 바꾸세요."
            )
        if any(error.endswith("_FORBIDDEN_PHRASE") for error in errors):
            feedback.append(
                "비난·보장 표현과 내부 JSON 필드명을 사용자 문장에서 제거하세요."
            )
        return feedback

    @staticmethod
    def _route_after_agent(
        state: WeeklyReportGraphState,
    ) -> Literal["continue", "fallback"]:
        if state.get("failed_stage"):
            return "fallback"
        return "continue"

    @staticmethod
    def _route_after_writer(
        state: WeeklyReportGraphState,
    ) -> Literal["guard", "fallback"]:
        if state.get("failed_stage"):
            return "fallback"
        return "guard"

    def _route_after_guard(
        self,
        state: WeeklyReportGraphState,
    ) -> Literal["critic", "rewrite", "fallback"]:
        if not state.get("guard_errors"):
            return "critic"
        if int(state.get("revision_count") or 0) < self._config.maximum_revision_count:
            return "rewrite"
        return "fallback"

    def _route_after_critic(
        self,
        state: WeeklyReportGraphState,
    ) -> Literal["finalize", "rewrite", "fallback"]:
        if state.get("failed_stage"):
            return "fallback"
        critic_result = state.get("critic_result") or {}
        if critic_result.get("passed") is True:
            return "finalize"
        if int(state.get("revision_count") or 0) < self._config.maximum_revision_count:
            return "rewrite"
        return "fallback"


def build_weekly_report_graph(
    agent_suite: WeeklyReportAgentSuite,
    config: WeeklyReportConfig | None = None,
) -> CompiledStateGraph:
    resolved_config = config or get_weekly_report_config()
    return WeeklyReportGraphWorkflow(agent_suite, resolved_config).compile()


def generate_graph_report_content(
    result: Mapping[str, object],
    agent_suite: WeeklyReportAgentSuite | None = None,
    config: WeeklyReportConfig | None = None,
    report_type: str | None = None,
) -> dict[str, object]:
    resolved_config = config or get_weekly_report_config()
    try:
        resolved_agent_suite = agent_suite
        if resolved_agent_suite is None:
            resolved_agent_suite = LangGraphWeeklyReportAgentSuite(resolved_config)
        graph = build_weekly_report_graph(resolved_agent_suite, resolved_config)
        final_state = graph.invoke(
            {
                "report_type": report_type,
                "result": deepcopy(dict(result)),
                "revision_feedback": [],
                "revision_count": 0,
                "failed_stage": None,
            }
        )
        content = final_state.get("content")
        if not isinstance(content, Mapping):
            raise ValueError("주간 리포트 그래프가 최종 content를 반환하지 않았습니다.")
        return deepcopy(dict(content))
    except Exception:
        logger.exception("주간 리포트 그래프 실행 실패")
        return build_fallback_content(result, resolved_config)
