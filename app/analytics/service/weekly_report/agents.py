from __future__ import annotations

import json
from typing import Any, Mapping, Protocol, Self, TypeVar

from langchain.agents import create_agent
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, ConfigDict, Field, model_validator

from analytics.service.weekly_report.config import WeeklyReportConfig


class GroundedStatement(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    text: str
    evidence_ids: list[str] = Field(alias="evidenceIds")


class WeaknessAnalysis(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summary: GroundedStatement
    findings: list[GroundedStatement]


class StudyCoaching(BaseModel):
    model_config = ConfigDict(extra="forbid")

    recommendations: list[GroundedStatement]


class ReportDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    comment: GroundedStatement
    tips: list[GroundedStatement]


class ReportCritique(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    passed: bool
    error_codes: list[str] = Field(alias="errorCodes")
    feedback: list[str]

    @model_validator(mode="after")
    def validate_verdict_consistency(self) -> Self:
        if self.passed and (self.error_codes or self.feedback):
            raise ValueError("통과 판정에는 오류 코드나 수정 피드백을 포함할 수 없습니다.")
        if not self.passed and not (self.error_codes or self.feedback):
            raise ValueError("실패 판정에는 오류 코드 또는 수정 피드백이 필요합니다.")
        return self


class WeeklyReportAgentSuite(Protocol):
    def analyze(
        self,
        report_type: str | None,
        result: Mapping[str, object],
    ) -> WeaknessAnalysis:
        """Interpret only the evidence already selected by deterministic code."""

    def coach(
        self,
        report_type: str | None,
        result: Mapping[str, object],
        analysis: Mapping[str, object],
    ) -> StudyCoaching:
        """Recommend learning actions without creating or changing a study plan."""

    def write(
        self,
        report_type: str | None,
        result: Mapping[str, object],
        analysis: Mapping[str, object],
        coaching: Mapping[str, object],
        feedback: list[str],
    ) -> ReportDraft:
        """Write the user-facing comment and tips."""

    def critique(
        self,
        report_type: str | None,
        result: Mapping[str, object],
        analysis: Mapping[str, object],
        coaching: Mapping[str, object],
        draft: Mapping[str, object],
    ) -> ReportCritique:
        """Judge grounding, tone, and actionability against the original result."""


StructuredOutput = TypeVar("StructuredOutput", bound=BaseModel)


class LangGraphWeeklyReportAgentSuite:
    def __init__(self, config: WeeklyReportConfig) -> None:
        self._analyst = self._build_agent(
            config,
            name="weekly_report_evidence_analyst",
            system_prompt=self._analyst_prompt(),
            response_format=WeaknessAnalysis,
            maximum_tokens=config.analyst_maximum_tokens,
        )
        self._coach = self._build_agent(
            config,
            name="weekly_report_study_coach",
            system_prompt=self._coach_prompt(config.maximum_tip_count),
            response_format=StudyCoaching,
            maximum_tokens=config.coach_maximum_tokens,
        )
        self._writer = self._build_agent(
            config,
            name="weekly_report_writer",
            system_prompt=self._writer_prompt(config.maximum_tip_count),
            response_format=ReportDraft,
            maximum_tokens=config.writer_maximum_tokens,
        )
        self._critic = self._build_agent(
            config,
            name="weekly_report_critic",
            system_prompt=self._critic_prompt(),
            response_format=ReportCritique,
            maximum_tokens=config.validator_maximum_tokens,
        )

    def analyze(
        self,
        report_type: str | None,
        result: Mapping[str, object],
    ) -> WeaknessAnalysis:
        return self._invoke_structured(
            self._analyst,
            WeaknessAnalysis,
            {
                "reportType": report_type,
                "evidence": self._select_narrative_evidence(result),
            },
        )

    def coach(
        self,
        report_type: str | None,
        result: Mapping[str, object],
        analysis: Mapping[str, object],
    ) -> StudyCoaching:
        return self._invoke_structured(
            self._coach,
            StudyCoaching,
            {
                "reportType": report_type,
                "evidence": self._select_narrative_evidence(result),
                "analysis": analysis,
            },
        )

    def write(
        self,
        report_type: str | None,
        result: Mapping[str, object],
        analysis: Mapping[str, object],
        coaching: Mapping[str, object],
        feedback: list[str],
    ) -> ReportDraft:
        return self._invoke_structured(
            self._writer,
            ReportDraft,
            {
                "reportType": report_type,
                "result": result,
                "analysis": analysis,
                "coaching": coaching,
                "revisionFeedback": feedback,
            },
        )

    def critique(
        self,
        report_type: str | None,
        result: Mapping[str, object],
        analysis: Mapping[str, object],
        coaching: Mapping[str, object],
        draft: Mapping[str, object],
    ) -> ReportCritique:
        return self._invoke_structured(
            self._critic,
            ReportCritique,
            {
                "reportType": report_type,
                "result": result,
                "analysis": analysis,
                "coaching": coaching,
                "draft": draft,
            },
        )

    @staticmethod
    def _build_agent(
        config: WeeklyReportConfig,
        name: str,
        system_prompt: str,
        response_format: type[BaseModel],
        maximum_tokens: int,
    ) -> Any:
        model = ChatOpenAI(
            model=config.model,
            timeout=config.llm_timeout_seconds,
            max_retries=config.provider_maximum_retry_count,
            max_completion_tokens=maximum_tokens,
        )
        return create_agent(
            model=model,
            tools=[],
            system_prompt=system_prompt,
            response_format=response_format,
            name=name,
        )

    @staticmethod
    def _invoke_structured(
        agent: Any,
        schema: type[StructuredOutput],
        payload: Mapping[str, object],
    ) -> StructuredOutput:
        response = agent.invoke(
            {
                "messages": [
                    {
                        "role": "user",
                        "content": json.dumps(payload, ensure_ascii=False),
                    }
                ]
            }
        )
        if not isinstance(response, Mapping):
            raise ValueError("에이전트 응답이 객체 형식이 아닙니다.")
        structured_response = response.get("structured_response")
        if isinstance(structured_response, schema):
            return structured_response
        return schema.model_validate(structured_response)

    @staticmethod
    def _select_narrative_evidence(
        result: Mapping[str, object],
    ) -> dict[str, object]:
        return {
            "strengths": result.get("strengths") or [],
            "priorityImprovements": result.get("priorityImprovements") or [],
            "timeSummary": result.get("timeSummary") or [],
            "confusionPatterns": result.get("confusionPatterns") or [],
        }

    @staticmethod
    def _analyst_prompt() -> str:
        return (
            "당신은 한국사 주간평가 근거 분석 에이전트입니다. 결정 코드가 evidence에 이미 선정한 "
            "strengths, priorityImprovements, timeSummary, confusionPatterns만 해석하세요. "
            "confusionPatterns는 그래프 관계가 하나로 확정되고 반복 기준을 통과한 오답 근거입니다. "
            "correctFact와 selectedFact를 이용해 어떤 역사 관계를 혼동했는지 설명할 수 있지만, "
            "그래프에 없는 인물·정책·사건·차이를 추가하지 마세요. 새로운 점수, 취약 판정, "
            "원인, 학습계획을 만들거나 변경하지 마세요. 모든 문장은 입력에 존재하는 evidenceId를 "
            "evidenceIds로 인용하고, 근거가 없으면 단정하지 마세요. text에는 숫자·배열 개수·순위와 "
            "입력 JSON 필드명을 쓰지 말고 사용자용 한국어로 정성 해석하세요. 개선 근거는 개선 "
            "흐름만 뜻하며 안정적 성취·높은 정답률·적은 오답을 뜻하지 않습니다. 한국어 구조화 "
            "출력만 반환하세요."
        )

    @staticmethod
    def _coach_prompt(maximum_tip_count: int) -> str:
        return (
            "당신은 한국사 학습 코치 에이전트입니다. evidence와 analysis의 검증된 근거를 학생이 "
            "실행할 수 있는 복습 행동으로 바꾸세요. 달력, 블록, 학습 기간 또는 다음 계획을 "
            "생성하거나 변경하지 마세요. 과거의 오답 이유, 암기 상태, 실수 습관을 추측하지 "
            "마세요. confusionPatterns를 사용할 때는 comparisonDimensions에 있는 비교 기준만 "
            "추천하고, 비어 있으면 새로운 비교 기준을 만들지 마세요. 새로운 사실과 숫자를 "
            "만들지 말고 입력 evidenceId만 "
            f"인용하세요. text에는 숫자·배열 개수·순위와 입력 JSON 필드명을 쓰지 마세요. 추천은 "
            f"최대 {maximum_tip_count}개이며 한국어 구조화 출력만 반환하세요."
        )

    @staticmethod
    def _writer_prompt(maximum_tip_count: int) -> str:
        return (
            "당신은 한국사 주간 리포트 작성 에이전트입니다. 제공된 result, analysis, coaching만 "
            "사용해 학생용 comment 1개와 tips를 작성하세요. 새로운 숫자, 사실, 취약 판정, "
            "합격 보장이나 비난 표현을 만들지 마세요. 개선 근거만으로 오답이 적었다고 추론하거나 "
            "안정적 성취·높은 정답률이라고 바꾸지 마세요. 보완 근거만으로 자주 틀린 이유·암기 "
            "부족·실수 습관을 추측하지 마세요. 계획 진행률도 별도 등급 기준이 없으므로 높다·낮다고 "
            "평가하지 마세요. 반복 혼동 문장은 confusionPatterns의 correctFact·selectedFact로 직접 "
            "확인되는 관계만 이름 붙이고, comparisonDimensions에 없는 비교 기준을 추가하지 마세요. "
            "각 사실 문장은 "
            "그 내용을 직접 뒷받침하는 입력 evidenceId만 인용해야 하며 "
            "strengths, priorityImprovements, timeSummary, confusionPatterns, nextPlanTargets 같은 내부 "
            "필드명은 문장에 "
            "노출하지 말고 강점, 우선 보완 영역, 풀이시간, 다음 학습 목표처럼 표현하세요. "
            f"tips는 최대 {maximum_tip_count}개입니다. revisionFeedback이 있으면 해당 문제만 "
            "수정하고 한국어 구조화 출력만 반환하세요."
        )

    @staticmethod
    def _critic_prompt() -> str:
        return (
            "당신은 독립적인 한국사 주간 리포트 비평 에이전트입니다. draft를 원본 result와 직접 "
            "대조해 근거 이탈, 과장, 입력에 없는 수치나 원인, 비난성 어조, 실행 불가능한 조언을 "
            "검사하세요. 각 사실 문장의 evidenceIds가 그 사실을 직접 뒷받침하는지 확인하고, 개선 "
            "근거를 안정적 성취·높은 정답률·낮은 오답 수로 바꾸거나 보완 근거를 반복 실수·암기 "
            "부족 원인으로 바꾼 초안은 거절하세요. 별도 등급 기준 없이 계획 진행률을 높다·낮다고 "
            "평가한 초안, confusionPatterns에 없는 역사 관계·비교 기준을 추가한 초안과 내부 JSON "
            "필드명이 사용자 문장에 노출된 초안도 거절하세요. 정책이나 계획을 "
            "새로 결정하지 마세요. passed, errorCodes, feedback을 갖는 한국어 구조화 출력만 "
            "반환하세요. passed가 true이면 errorCodes와 "
            "feedback은 반드시 빈 배열이어야 합니다. passed가 false이면 오류 코드나 구체적인 수정 "
            "피드백을 하나 이상 포함하세요."
        )
