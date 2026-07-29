from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Iterable, Mapping, Sequence
from zoneinfo import ZoneInfo

from analytics.service.taxonomy import (
    TaxonomyConfig,
    build_group_display_label,
    build_group_key_id as taxonomy_build_group_key_id,
    get_taxonomy_config,
    normalize_classification_value,
    normalize_field_name,
    order_group_fields,
)


@dataclass(frozen=True)
class WeaknessConfig:
    version: str
    lookback_days: int
    half_life_days: int
    minimum_effective_sample: float
    wilson_z: float
    weak_threshold: float
    stable_threshold: float
    trend_window_days: int
    trend_threshold: float
    trend_minimum_sample: int
    trend_sample_balance_ratio: float
    output_precision: int
    status_insufficient: str
    status_weak: str
    status_neutral: str
    status_stable: str
    trend_unknown: str
    trend_worsening: str
    trend_improving: str
    trend_flat: str
    unclassified_label: str

    def __getitem__(self, key: str) -> object:
        aliases = {
            "min_sample": "minimum_effective_sample",
            "z_value": "wilson_z",
            "window_days": "lookback_days",
        }
        attribute_name = aliases.get(key, key)
        if not hasattr(self, attribute_name):
            raise KeyError(key)
        return getattr(self, attribute_name)


def get_weakness_config() -> WeaknessConfig:
    return WeaknessConfig(
        version="weakness-v3",
        # 판정 기간. 90일은 오래된 기록이 배지에 남아 최근 실력과 어긋나서
        # 최근 4주(추세 비교 창 14일 x 2와 같은 폭)로 줄였다.
        lookback_days=28,
        half_life_days=14,
        minimum_effective_sample=3.0,
        wilson_z=1.28,
        weak_threshold=0.50,
        stable_threshold=0.20,
        trend_window_days=14,
        trend_threshold=0.10,
        trend_minimum_sample=6,
        trend_sample_balance_ratio=0.5,
        output_precision=4,
        status_insufficient="INSUFFICIENT",
        status_weak="WEAK",
        status_neutral="NEUTRAL",
        status_stable="STABLE",
        trend_unknown="unknown",
        trend_worsening="worsening",
        trend_improving="improving",
        trend_flat="flat",
        unclassified_label=get_taxonomy_config().unclassified_label,
    )


def build_weakness_rows(
    records: Iterable[Mapping[str, object]],
    group_fields: Sequence[str],
    today: date | None = None,
    config: WeaknessConfig | None = None,
    taxonomy_config: TaxonomyConfig | None = None,
) -> list[dict[str, object]]:
    resolved_config = config or get_weakness_config()
    resolved_taxonomy = taxonomy_config or get_taxonomy_config()
    if today is None:
        from django.utils import timezone
        from analytics.service.study_plan.config import get_study_plan_config

        today = timezone.localdate(
            timezone=ZoneInfo(get_study_plan_config().timezone),
        )
    ordered_fields = order_group_fields(group_fields, resolved_taxonomy)
    period_start = today - timedelta(days=resolved_config.lookback_days - 1)
    grouped: dict[str, dict[str, object]] = {}

    if hasattr(records, "filter") and hasattr(records, "values"):
        source_fields = ["session__recorded_date", "is_correct", "time_spent_ms"]
        for field_name in ordered_fields:
            source_field = field_name
            if field_name == "qType":
                source_field = "q_type"
            source_fields.append(source_field)
        records = records.filter(
            session__recorded_date__gte=period_start,
            session__recorded_date__lte=today,
        ).values(*source_fields)

    for record in records:
        recorded_date = _get_recorded_date(record)
        is_correct = _get_record_value(record, "isCorrect", "is_correct")
        if recorded_date is None or not isinstance(is_correct, bool):
            continue
        if recorded_date < period_start or recorded_date > today:
            continue

        group = {
            field_name: normalize_classification_value(
                field_name,
                _get_group_value(record, field_name),
                resolved_taxonomy,
            )
            for field_name in ordered_fields
        }
        group_key_id = taxonomy_build_group_key_id(group, resolved_taxonomy)
        summary = grouped.setdefault(
            group_key_id,
            {
                "group": group,
                "rawTotal": 0,
                "rawWrong": 0,
                "effectiveTotal": 0.0,
                "effectiveWrong": 0.0,
                "timeTotalMs": 0,
                "timeCount": 0,
                "recentTotal": 0,
                "recentWrong": 0,
                "previousTotal": 0,
                "previousWrong": 0,
            },
        )
        _update_summary(summary, record, recorded_date, is_correct, today, resolved_config)

    rows = [
        _build_weakness_row(group_key_id, summary, resolved_config, resolved_taxonomy)
        for group_key_id, summary in grouped.items()
    ]
    return sorted(
        rows,
        key=lambda row: (
            -float(row["weaknessScore"]),
            -int(row["raw"]["wrong"]),
            -int(row["raw"]["total"]),
            str(row["groupKeyId"]),
        ),
    )


def build_group_key_id(
    group_fields: Mapping[str, object] | Sequence[str],
    normalized_values: Sequence[object] | None = None,
) -> str:
    if isinstance(group_fields, Mapping):
        return taxonomy_build_group_key_id(group_fields)
    if normalized_values is None:
        raise ValueError("영역 값이 필요합니다.")
    group = {
        normalize_field_name(field_name): normalized_values[index]
        for index, field_name in enumerate(group_fields)
    }
    return taxonomy_build_group_key_id(group)


def calculate_decay_weight(
    days_ago: int,
    half_life_days: int,
) -> float:
    if half_life_days <= 0:
        raise ValueError("반감기는 0보다 커야 합니다.")
    return 0.5 ** (max(days_ago, 0) / half_life_days)


def calculate_wilson_lower_bound(
    wrong: float,
    total: float,
    z_value: float,
) -> float:
    if total <= 0:
        return 0.0
    if wrong < 0 or wrong > total:
        raise ValueError("오답 표본은 0 이상 전체 표본 이하여야 합니다.")

    proportion = wrong / total
    z_squared = z_value * z_value
    denominator = 1 + z_squared / total
    center = proportion + z_squared / (2 * total)
    margin = z_value * math.sqrt(
        (proportion * (1 - proportion) / total)
        + (z_squared / (4 * total * total))
    )
    return max(0.0, (center - margin) / denominator)


def determine_weakness_status(
    effective_total: float,
    weakness_score: float,
    config: WeaknessConfig | None = None,
) -> str:
    resolved_config = config or get_weakness_config()
    if effective_total < resolved_config.minimum_effective_sample:
        return resolved_config.status_insufficient
    elif weakness_score >= resolved_config.weak_threshold:
        return resolved_config.status_weak
    elif weakness_score <= resolved_config.stable_threshold:
        return resolved_config.status_stable
    return resolved_config.status_neutral


def determine_trend(
    recent_wrong: int,
    recent_total: int,
    previous_wrong: int,
    previous_total: int,
    config: WeaknessConfig | None = None,
) -> dict[str, object]:
    """두 구간의 취약 지표를 비교해 추세를 판정한다.

    비교 대상은 윌슨 하한이고, 하한은 표본 수가 늘어나는 것만으로도 올라간다.
    두 구간의 표본 수가 크게 다르면 실력 변화가 아니라 표본 변화를 재게 되어
    판정이 실제 성적과 반대로 나올 수 있다. 그래서 표본이 충분하고 두 구간의
    크기가 비슷할 때만 판정하고, 아니면 판단을 보류한다.
    """
    resolved_config = config or get_weakness_config()
    if not _is_trend_comparable(recent_total, previous_total, resolved_config):
        return {
            "trend": resolved_config.trend_unknown,
            "trendDelta": None,
            "recentScore": None,
            "previousScore": None,
        }

    recent_score = calculate_wilson_lower_bound(
        recent_wrong,
        recent_total,
        resolved_config.wilson_z,
    )
    previous_score = calculate_wilson_lower_bound(
        previous_wrong,
        previous_total,
        resolved_config.wilson_z,
    )
    delta = recent_score - previous_score
    trend = resolved_config.trend_flat
    if delta >= resolved_config.trend_threshold:
        trend = resolved_config.trend_worsening
    elif delta <= -resolved_config.trend_threshold:
        trend = resolved_config.trend_improving

    precision = resolved_config.output_precision
    return {
        "trend": trend,
        "trendDelta": round(delta, precision),
        "recentScore": round(recent_score, precision),
        "previousScore": round(previous_score, precision),
    }


def _is_trend_comparable(
    recent_total: int,
    previous_total: int,
    config: WeaknessConfig,
) -> bool:
    """두 구간을 비교해도 되는지 판단한다."""
    if recent_total < config.trend_minimum_sample:
        return False
    elif previous_total < config.trend_minimum_sample:
        return False
    larger = max(recent_total, previous_total)
    if larger <= 0:
        return False
    return min(recent_total, previous_total) / larger >= config.trend_sample_balance_ratio


def get_status_class(status: str) -> str:
    return status.lower()


def _get_recorded_date(record: Mapping[str, object]) -> date | None:
    raw_value = _get_record_value(record, "recordedDate", "recorded_date", "session__recorded_date")
    if isinstance(raw_value, datetime):
        return raw_value.date()
    elif isinstance(raw_value, date):
        return raw_value
    elif isinstance(raw_value, str):
        try:
            return date.fromisoformat(raw_value[:10])
        except ValueError:
            return None
    return None


def _get_record_value(record: Mapping[str, object], *keys: str) -> object:
    for key in keys:
        if key in record:
            return record[key]
    return None


def _get_group_value(record: Mapping[str, object], field_name: str) -> object:
    normalized_field = normalize_field_name(field_name)
    candidate_keys = [normalized_field]
    if normalized_field == "qType":
        candidate_keys.extend(("q_type", "question_type"))
    elif normalized_field == "coreConcept":
        candidate_keys.extend(("core_concept", "question__core_concept"))
    return _get_record_value(record, *candidate_keys)


def _update_summary(
    summary: dict[str, object],
    record: Mapping[str, object],
    recorded_date: date,
    is_correct: bool,
    today: date,
    config: WeaknessConfig,
) -> None:
    is_wrong = not is_correct
    days_ago = (today - recorded_date).days
    weight = calculate_decay_weight(days_ago, config.half_life_days)
    summary["rawTotal"] = int(summary["rawTotal"]) + 1
    summary["effectiveTotal"] = float(summary["effectiveTotal"]) + weight
    if is_wrong:
        summary["rawWrong"] = int(summary["rawWrong"]) + 1
        summary["effectiveWrong"] = float(summary["effectiveWrong"]) + weight

    time_spent_ms = _get_record_value(record, "timeSpentMs", "time_spent_ms")
    if isinstance(time_spent_ms, (int, float)) and time_spent_ms >= 0:
        summary["timeTotalMs"] = float(summary["timeTotalMs"]) + time_spent_ms
        summary["timeCount"] = int(summary["timeCount"]) + 1

    recent_start = today - timedelta(days=config.trend_window_days - 1)
    previous_start = recent_start - timedelta(days=config.trend_window_days)
    if recent_start <= recorded_date <= today:
        summary["recentTotal"] = int(summary["recentTotal"]) + 1
        if is_wrong:
            summary["recentWrong"] = int(summary["recentWrong"]) + 1
    elif previous_start <= recorded_date < recent_start:
        summary["previousTotal"] = int(summary["previousTotal"]) + 1
        if is_wrong:
            summary["previousWrong"] = int(summary["previousWrong"]) + 1


def _build_weakness_row(
    group_key_id: str,
    summary: Mapping[str, object],
    config: WeaknessConfig,
    taxonomy_config: TaxonomyConfig,
) -> dict[str, object]:
    effective_total = float(summary["effectiveTotal"])
    effective_wrong = float(summary["effectiveWrong"])
    raw_total = int(summary["rawTotal"])
    raw_wrong = int(summary["rawWrong"])
    weakness_score = calculate_wilson_lower_bound(
        effective_wrong,
        effective_total,
        config.wilson_z,
    )
    status = determine_weakness_status(effective_total, weakness_score, config)
    trend = determine_trend(
        int(summary["recentWrong"]),
        int(summary["recentTotal"]),
        int(summary["previousWrong"]),
        int(summary["previousTotal"]),
        config,
    )
    time_count = int(summary["timeCount"])
    average_time_sec = None
    if time_count:
        average_time_sec = round(float(summary["timeTotalMs"]) / time_count / 1000)

    precision = config.output_precision
    group = dict(summary["group"])
    raw_wrong_rate = 0.0
    if raw_total:
        raw_wrong_rate = round(raw_wrong / raw_total, precision)
    insufficient_reason = None
    if status == config.status_insufficient:
        insufficient_reason = "effective_total_below_minimum"
    return {
        "groupKeyId": group_key_id,
        "groupKey": group,
        "group": group,
        "label": build_group_display_label(group, taxonomy_config),
        "raw": {
            "total": raw_total,
            "wrong": raw_wrong,
            "wrongRate": raw_wrong_rate,
            "averageTimeSec": average_time_sec,
            "avgTimeSec": average_time_sec,
        },
        "effective": {
            "total": round(effective_total, precision),
            "wrong": round(effective_wrong, precision),
        },
        "weaknessScore": round(weakness_score, precision),
        "status": status,
        **trend,
        "insufficientReason": insufficient_reason,
    }
