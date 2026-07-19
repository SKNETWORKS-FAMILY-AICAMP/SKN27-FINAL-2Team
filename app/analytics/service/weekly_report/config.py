from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class WeeklyReportConfig:
    version: str
    schema_version: str
    maximum_strength_count: int
    maximum_improvement_count: int
    maximum_time_summary_count: int
    maximum_next_target_count: int
    minimum_type_time_sample: int
    minimum_reference_time_sample: int
    slow_time_ratio: float
    maximum_tip_count: int
    maximum_comment_length: int
    maximum_tip_length: int
    llm_timeout_seconds: int
    writer_maximum_tokens: int
    validator_maximum_tokens: int
    stuck_after_seconds: int
    maximum_attempt_count: int
    retry_delays_seconds: tuple[int, ...]
    model: str
    forbidden_phrases: tuple[str, ...]
    fallback_neutral_comment: str
    fallback_improving_comment: str
    fallback_priority_comment: str
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
        version="weekly-report-v1",
        schema_version="1",
        maximum_strength_count=3,
        maximum_improvement_count=3,
        maximum_time_summary_count=2,
        maximum_next_target_count=3,
        minimum_type_time_sample=5,
        minimum_reference_time_sample=10,
        slow_time_ratio=1.3,
        maximum_tip_count=3,
        maximum_comment_length=240,
        maximum_tip_length=160,
        llm_timeout_seconds=60,
        writer_maximum_tokens=600,
        validator_maximum_tokens=200,
        stuck_after_seconds=300,
        maximum_attempt_count=3,
        retry_delays_seconds=(30, 120),
        model=configured_model,
        forbidden_phrases=(
            "합격 보장",
            "반드시 합격",
            "능력이 부족",
            "게으르",
            "재능이 없",
        ),
        fallback_neutral_comment="이번 주 학습 결과를 기준으로 다음 학습을 이어가 보세요.",
        fallback_improving_comment="{label} 영역에서 개선 흐름이 확인됐어요.",
        fallback_priority_comment="{label} 영역을 다음 학습에서 먼저 보완해 보세요.",
        fallback_priority_tip="{label} 문제를 짧은 단위로 나누어 풀어 보세요.",
        fallback_time_tip="{label} 유형은 풀이 순서를 정한 뒤 시간을 확인해 보세요.",
        fallback_general_tip="학습 블록을 정해진 순서대로 풀고 제출까지 마무리해 보세요.",
    )
