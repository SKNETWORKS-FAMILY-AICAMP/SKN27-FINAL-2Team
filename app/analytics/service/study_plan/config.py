from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping


@dataclass(frozen=True)
class PriorityWeights:
    weakness: float
    exam: float
    repeated_error: float


@dataclass(frozen=True)
class StudyPlanConfig:
    version: str
    timezone: str
    weekly_plan_days: int
    weekly_learning_days: int
    maximum_daily_minutes: int
    maximum_block_minutes: int
    maximum_daily_blocks: int
    minutes_per_hour: int
    default_daily_minutes: int
    default_average_seconds_per_question: int
    minimum_question_count: int
    maximum_question_count: int
    weekly_review_question_count: int
    weekly_review_minutes: int
    short_term_days: int
    medium_term_days: int
    stable_weakness_threshold: float
    score_ratio: Mapping[int, int]
    strategy_weights: Mapping[str, PriorityWeights]
    trend_order: Mapping[str, int]
    practice_block_type: str
    weekly_review_block_type: str
    practice_activity: str
    weekly_review_activity: str
    personalized_reason: str
    prediction_fallback_reason: str
    scope_relaxed_reason: str


def get_study_plan_config() -> StudyPlanConfig:
    return StudyPlanConfig(
        version="study-plan-v2",
        timezone="Asia/Seoul",
        weekly_plan_days=7,
        weekly_learning_days=6,
        maximum_daily_minutes=300,
        maximum_block_minutes=30,
        maximum_daily_blocks=10,
        minutes_per_hour=60,
        default_daily_minutes=60,
        default_average_seconds_per_question=180,
        minimum_question_count=1,
        maximum_question_count=20,
        weekly_review_question_count=50,
        weekly_review_minutes=80,
        short_term_days=7,
        medium_term_days=21,
        stable_weakness_threshold=0.20,
        score_ratio={3: 1, 2: 3, 1: 1},
        strategy_weights={
            "short": PriorityWeights(weakness=0.40, exam=0.45, repeated_error=0.15),
            "medium": PriorityWeights(weakness=0.45, exam=0.40, repeated_error=0.15),
            "long": PriorityWeights(weakness=0.55, exam=0.30, repeated_error=0.15),
        },
        trend_order={"worsening": 0, "flat": 1, "unknown": 2, "improving": 3},
        practice_block_type="practice",
        weekly_review_block_type="weekly_review",
        practice_activity="문제 풀이",
        weekly_review_activity="주간 평가",
        personalized_reason="취약 점수·시험 중요도·반복 오답 우선",
        prediction_fallback_reason="학습 기록 부족으로 문제은행 비중 우선",
        scope_relaxed_reason="문항 부족으로 시대 범위로 완화",
    )
