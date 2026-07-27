"""최근 출제 경향 조회.

ml_trend_top5 테이블은 ML 파이프라인이 만들어 둔 회차별 TOP5 통계다.
학습 계획·주간 리포트가 참고하는 것은 source = "recent5_actual" 이다.
(팀 정리 문서 기준: 직전 5회차 실제 라벨 TOP5 = 학습 계획 참고용)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from analytics.models import MlTrendTop5
from analytics.service.taxonomy import build_group_key_id


@dataclass(frozen=True)
class ExamTrendConfig:
    version: str
    source: str
    combo_trend_type: str
    era_trend_type: str
    topic_trend_type: str
    maximum_rank: int


def get_exam_trend_config() -> ExamTrendConfig:
    return ExamTrendConfig(
        version="exam-trend-v1",
        source="recent5_actual",
        combo_trend_type="era_topic_train",
        era_trend_type="era",
        topic_trend_type="topic_train",
        maximum_rank=5,
    )


def get_latest_target_round(
    trend_type: str | None = None,
    config: ExamTrendConfig | None = None,
) -> int | None:
    """가장 최근 회차 번호. 데이터가 없으면 None.

    trend_type 별로 적재 시점이 다를 수 있어 함께 걸러야 한다. 그러지 않으면
    최신 회차에 해당 유형 행이 없을 때 조용히 빈 결과가 나온다.
    """
    resolved_config = config or get_exam_trend_config()
    queryset = MlTrendTop5.objects.filter(source=resolved_config.source)
    if trend_type:
        queryset = queryset.filter(trend_type=trend_type)
    latest = (
        queryset.order_by("-target_round")
        .values_list("target_round", flat=True)
        .first()
    )
    if latest is None:
        return None
    return int(latest)


def get_recent_exam_trends(
    trend_type: str | None = None,
    target_round: int | None = None,
    config: ExamTrendConfig | None = None,
) -> list[dict[str, object]]:
    """최근 출제 경향 TOP5.

    groupKeyId 를 함께 만들어 취약 분석 결과와 바로 대조할 수 있게 한다.
    ML 쪽 표기("일제 강점기")와 서비스 표기("일제강점기")가 달라도
    taxonomy 별칭이 흡수한다.
    """
    resolved_config = config or get_exam_trend_config()
    resolved_trend_type = trend_type or resolved_config.combo_trend_type
    resolved_round = target_round
    if resolved_round is None:
        resolved_round = get_latest_target_round(resolved_trend_type, resolved_config)
    if resolved_round is None:
        return []

    rows = list(
        MlTrendTop5.objects.filter(
            source=resolved_config.source,
            trend_type=resolved_trend_type,
            target_round=resolved_round,
            rank_no__lte=resolved_config.maximum_rank,
        )
        .order_by("rank_no")
        .values(
            "rank_no",
            "era",
            "topic_train",
            "combo_label",
            "count_value",
            "ratio_percent",
            "target_round",
            "recent5_rounds",
        )
    )
    return [_build_trend_item(row, resolved_trend_type) for row in rows]


def build_exam_trend_lookup(
    trends: list[Mapping[str, object]],
) -> dict[str, dict[str, object]]:
    """groupKeyId → 출제 경향. 취약 영역에 순위를 붙일 때 쓴다."""
    lookup: dict[str, dict[str, object]] = {}
    for trend in trends:
        group_key_id = str(trend.get("groupKeyId") or "")
        if group_key_id and group_key_id not in lookup:
            lookup[group_key_id] = dict(trend)
    return lookup


def _build_trend_item(
    row: Mapping[str, object],
    trend_type: str,
) -> dict[str, object]:
    era = str(row.get("era") or "").strip()
    topic = str(row.get("topic_train") or "").strip()
    group_values: dict[str, object] = {}
    if era:
        group_values["era"] = era
    if topic:
        group_values["topic"] = topic

    label = str(row.get("combo_label") or "").strip()
    if not label:
        label = " · ".join(value for value in (era, topic) if value)
    return {
        "rank": int(row["rank_no"]),
        "groupKeyId": build_group_key_id(group_values) if group_values else "",
        "label": label,
        "questionCount": int(row.get("count_value") or 0),
        "ratioPercent": row.get("ratio_percent"),
        "targetRound": int(row.get("target_round") or 0),
        "recentRounds": str(row.get("recent5_rounds") or ""),
        "trendType": trend_type,
    }
