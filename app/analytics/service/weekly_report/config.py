from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class WeeklyReportConfig:
    version: str
    schema_version: str
    maximum_strength_count: int
    maximum_improvement_count: int
    maximum_concept_weakness_count: int
    maximum_exam_trend_count: int
    weakness_tie_tolerance: float
    maximum_time_summary_count: int
    maximum_next_target_count: int
    maximum_confusion_pattern_count: int
    minimum_confusion_repeat_count: int
    minimum_type_time_sample: int
    minimum_reference_time_sample: int
    slow_time_ratio: float
    maximum_tip_count: int
    maximum_comment_length: int
    maximum_tip_length: int
    llm_timeout_seconds: int
    analyst_maximum_tokens: int
    coach_maximum_tokens: int
    writer_maximum_tokens: int
    validator_maximum_tokens: int
    provider_maximum_retry_count: int
    maximum_revision_count: int
    stuck_after_seconds: int
    claim_candidate_count: int
    maximum_attempt_count: int
    retry_delays_seconds: tuple[int, ...]
    completed_session_status: str
    model: str
    forbidden_phrases: tuple[str, ...]
    forbidden_output_tokens: tuple[str, ...]
    fallback_confusion_comment: str
    fallback_confusion_tip: str
    fallback_confusion_general_tip: str
    fallback_neutral_comment: str
    fallback_improving_comment: str
    fallback_priority_comment: str
    fallback_concept_comment: str
    fallback_concept_tip: str
    fallback_priority_tip: str
    fallback_time_tip: str
    fallback_general_tip: str


def get_weekly_report_config() -> WeeklyReportConfig:
    configured_model = os.getenv("WEEKLY_REPORT_LLM_MODEL")
    if not configured_model:
        configured_model = os.getenv("OPENAI_CHAT_MODEL")
    if not configured_model:
        configured_model = "configured-model"
    return WeeklyReportConfig(
        version="weekly-report-v3-langgraph",
        schema_version="1",
        maximum_strength_count=3,
        maximum_improvement_count=3,
        maximum_concept_weakness_count=3,
        maximum_exam_trend_count=5,
        weakness_tie_tolerance=0.05,
        maximum_time_summary_count=2,
        maximum_next_target_count=3,
        maximum_confusion_pattern_count=3,
        minimum_confusion_repeat_count=2,
        minimum_type_time_sample=5,
        minimum_reference_time_sample=10,
        slow_time_ratio=1.3,
        maximum_tip_count=4,
        maximum_comment_length=420,
        maximum_tip_length=220,
        llm_timeout_seconds=60,
        analyst_maximum_tokens=650,
        coach_maximum_tokens=650,
        writer_maximum_tokens=1000,
        validator_maximum_tokens=450,
        provider_maximum_retry_count=0,
        maximum_revision_count=1,
        stuck_after_seconds=420,
        claim_candidate_count=20,
        maximum_attempt_count=3,
        retry_delays_seconds=(30, 120),
        completed_session_status="completed",
        model=configured_model,
        forbidden_phrases=(
            "합격 보장",
            "반드시 합격",
            "능력이 부족",
            "게으르",
            "재능이 없",
        ),
        forbidden_output_tokens=(
            "strengths",
            "priorityImprovements",
            "conceptWeaknesses",
            "examTrends",
            "timeSummary",
            "nextPlanTargets",
            "confusionPatterns",
            "evidenceId",
            "groupKeyId",
        ),
        fallback_confusion_comment=(
            "정답의 {correct_subject}–{correct_object} 관계와 선택한 "
            "{selected_subject}–{selected_object} 관계를 반복해서 혼동했어요."
        ),
        fallback_confusion_tip="{dimensions} 기준으로 두 관계를 나란히 비교해 보세요.",
        fallback_confusion_general_tip="두 관계를 같은 기준으로 나란히 비교해 보세요.",
        fallback_neutral_comment="이번 주 학습 결과를 기준으로 다음 학습을 이어가 보세요.",
        fallback_improving_comment="{label} 영역에서 개선 흐름이 확인됐어요.",
        fallback_priority_comment="{label} 영역을 다음 학습에서 먼저 보완해 보세요.",
        fallback_concept_comment="{label} 개념에서 반복해서 막히고 있어요.",
        fallback_concept_tip="{label} 개념을 정의부터 다시 정리해 보세요.",
        fallback_priority_tip="{label} 문제를 짧은 단위로 나누어 풀어 보세요.",
        fallback_time_tip="{label} 유형은 풀이 순서를 정한 뒤 시간을 확인해 보세요.",
        fallback_general_tip="학습 블록을 정해진 순서대로 풀고 제출까지 마무리해 보세요.",
    )
