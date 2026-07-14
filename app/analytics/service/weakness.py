import math
from datetime import timedelta

from django.utils import timezone

from analytics.service.classification import normalize_classification_value
from analytics.service.taxonomy import build_group_display_label, get_unclassified_label


def get_weakness_config():
    return {
        "min_sample": 3.0,
        "weak_threshold": 0.50,
        "stable_threshold": 0.20,
        "z_value": 1.28,
        "half_life_days": 14,
        "window_days": 90,
        "trend_window_days": 14,
        "trend_threshold": 0.10,
        "trend_bonus_worsening": 0.08,
        "trend_bonus_improving": -0.04,
        "unclassified_label": get_unclassified_label(),
        "status_insufficient": "INSUFFICIENT",
        "status_weak": "WEAK",
        "status_neutral": "NEUTRAL",
        "status_stable": "STABLE",
        "trend_unknown": "UNKNOWN",
        "trend_worsening": "WORSENING",
        "trend_improving": "IMPROVING",
        "trend_flat": "FLAT",
    }


def build_weakness_rows(records, group_fields, today=None):
    config = get_weakness_config()
    base_date = today or timezone.localdate()
    rows = get_weakness_record_rows(records, group_fields, base_date, config)
    grouped = {}
    for row in rows:
        normalized_values = normalize_group_values(group_fields, row, config)
        group_key_id = build_group_key_id(group_fields, normalized_values)
        summary = grouped.setdefault(
            group_key_id,
            build_weakness_summary_seed(group_fields, normalized_values),
        )
        update_weakness_summary(summary, row, base_date, config)

    return sorted(
        [
            build_weakness_row(summary, config)
            for summary in grouped.values()
        ],
        key=lambda item: (
            -item["weaknessScore"],
            -item["raw"]["wrong"],
            -item["raw"]["total"],
            item["groupKeyId"],
        ),
    )


def get_weakness_record_rows(records, group_fields, today, config):
    start_date = today - timedelta(days=config["window_days"] - 1)
    selected_fields = ["session__recorded_date", "is_correct", "time_spent_ms"]
    selected_fields.extend(group_fields)
    queryset = records.filter(session__recorded_date__gte=start_date)
    return list(queryset.values(*selected_fields))


def normalize_group_values(group_fields, row, config):
    return [
        normalize_group_value(field_name, row.get(field_name), config)
        for field_name in group_fields
    ]


def normalize_group_value(field_name, value, config):
    normalized_value = normalize_classification_value(field_name, value)
    if normalized_value:
        return normalized_value
    return config["unclassified_label"]


def build_weakness_summary_seed(group_fields, normalized_values):
    group_key = [
        [field_name, normalized_values[index]]
        for index, field_name in enumerate(group_fields)
    ]
    return {
        "groupKeyId": build_group_key_id(group_fields, normalized_values),
        "groupKey": group_key,
        "group": build_group_dict(group_fields, normalized_values),
        "total": 0,
        "wrong": 0,
        "totalTimeMs": 0,
        "timeCount": 0,
        "effectiveTotal": 0.0,
        "effectiveWrong": 0.0,
        "trend": build_trend_summary_seed(),
    }


def build_group_dict(group_fields, normalized_values):
    group = {}
    for index, field_name in enumerate(group_fields):
        group[get_group_payload_key(field_name)] = normalized_values[index]
    return group


def get_group_payload_key(field_name):
    if field_name == "q_type":
        return "qType"
    return field_name


def build_trend_summary_seed():
    return {
        "currentTotal": 0,
        "currentWrong": 0,
        "previousTotal": 0,
        "previousWrong": 0,
    }


def update_weakness_summary(summary, row, today, config):
    recorded_date = row.get("session__recorded_date") or today
    days_ago = max((today - recorded_date).days, 0)
    weight = calculate_decay_weight(days_ago, config["half_life_days"])
    is_wrong = not bool(row.get("is_correct"))

    summary["total"] += 1
    if is_wrong:
        summary["wrong"] += 1
        summary["effectiveWrong"] += weight
    summary["effectiveTotal"] += weight

    time_spent_ms = row.get("time_spent_ms")
    if time_spent_ms is not None:
        summary["totalTimeMs"] += time_spent_ms
        summary["timeCount"] += 1

    update_trend_summary(summary["trend"], recorded_date, is_wrong, today, config)


def update_trend_summary(trend_summary, recorded_date, is_wrong, today, config):
    trend_window_days = config["trend_window_days"]
    current_start = today - timedelta(days=trend_window_days - 1)
    previous_start = current_start - timedelta(days=trend_window_days)
    previous_end = current_start - timedelta(days=1)

    if current_start <= recorded_date <= today:
        trend_summary["currentTotal"] += 1
        if is_wrong:
            trend_summary["currentWrong"] += 1
    elif previous_start <= recorded_date <= previous_end:
        trend_summary["previousTotal"] += 1
        if is_wrong:
            trend_summary["previousWrong"] += 1


def build_weakness_row(summary, config):
    effective_total = summary["effectiveTotal"]
    effective_wrong = summary["effectiveWrong"]
    weakness_score = calculate_wilson_lower_bound(
        effective_wrong,
        effective_total,
        config["z_value"],
    )
    status = determine_weakness_status(effective_total, weakness_score, config)
    raw_total = summary["total"]
    raw_wrong = summary["wrong"]
    row = {
        "groupKeyId": summary["groupKeyId"],
        "groupKey": summary["groupKey"],
        "group": summary["group"],
        "label": build_group_display_label(summary["groupKey"]),
        "raw": {
            "total": raw_total,
            "wrong": raw_wrong,
            "wrongRate": calculate_raw_rate(raw_wrong, raw_total),
            "avgTimeSec": calculate_average_time_sec(summary),
        },
        "effective": {
            "total": round(effective_total, 4),
            "wrong": round(effective_wrong, 4),
        },
        "weaknessScore": round(weakness_score, 4),
        "status": status,
        "trend": build_trend(summary["trend"], config),
        "insufficientReason": None,
    }
    if status == config["status_insufficient"]:
        row["insufficientReason"] = "effective_total_below_min_sample"

    return row


def determine_weakness_status(effective_total, weakness_score, config=None):
    resolved_config = config or get_weakness_config()
    if effective_total < resolved_config["min_sample"]:
        return resolved_config["status_insufficient"]
    elif weakness_score >= resolved_config["weak_threshold"]:
        return resolved_config["status_weak"]
    elif weakness_score <= resolved_config["stable_threshold"]:
        return resolved_config["status_stable"]

    return resolved_config["status_neutral"]


def calculate_wilson_lower_bound(wrong, total, z_value):
    if not total:
        return 0.0

    proportion = wrong / total
    z_squared = z_value * z_value
    denominator = 1 + z_squared / total
    center = proportion + z_squared / (2 * total)
    margin = z_value * math.sqrt(
        (proportion * (1 - proportion) / total)
        + (z_squared / (4 * total * total))
    )
    return max(0.0, (center - margin) / denominator)


def calculate_decay_weight(days_ago, half_life_days):
    if half_life_days <= 0:
        return 1.0
    return 0.5 ** (max(days_ago, 0) / half_life_days)


def build_trend(trend_summary, config=None):
    resolved_config = config or get_weakness_config()
    current_total = trend_summary["currentTotal"]
    previous_total = trend_summary["previousTotal"]
    current_rate = calculate_wilson_lower_bound(
        trend_summary["currentWrong"],
        current_total,
        resolved_config["z_value"],
    )
    previous_rate = calculate_wilson_lower_bound(
        trend_summary["previousWrong"],
        previous_total,
        resolved_config["z_value"],
    )

    trend_value = resolved_config["trend_unknown"]
    delta = None
    if (
        current_total >= resolved_config["min_sample"]
        and previous_total >= resolved_config["min_sample"]
    ):
        delta = current_rate - previous_rate
        if delta >= resolved_config["trend_threshold"]:
            trend_value = resolved_config["trend_worsening"]
        elif delta <= -resolved_config["trend_threshold"]:
            trend_value = resolved_config["trend_improving"]
        elif -resolved_config["trend_threshold"] < delta < resolved_config["trend_threshold"]:
            trend_value = resolved_config["trend_flat"]

    return {
        "value": trend_value,
        "delta": round(delta, 4) if delta is not None else None,
        "current": {
            "total": current_total,
            "adjustedRate": round(current_rate, 4),
        },
        "previous": {
            "total": previous_total,
            "adjustedRate": round(previous_rate, 4),
        },
    }


def build_group_key_id(group_fields, normalized_values):
    return "|".join(
        f"{escape_group_key_part(field_name)}={escape_group_key_part(normalized_values[index])}"
        for index, field_name in enumerate(group_fields)
    )


def escape_group_key_part(value):
    return str(value).replace("\\", "\\\\").replace("|", "\\|").replace("=", "\\=")


def calculate_raw_rate(wrong, total):
    if not total:
        return 0.0
    return round(wrong / total, 4)


def calculate_average_time_sec(summary):
    if not summary["timeCount"]:
        return None
    return round((summary["totalTimeMs"] / summary["timeCount"]) / 1000)


def get_status_class(status):
    return status.lower()


def get_trend_bonus(trend_value, config=None):
    resolved_config = config or get_weakness_config()
    if trend_value == resolved_config["trend_worsening"]:
        return resolved_config["trend_bonus_worsening"]
    elif trend_value == resolved_config["trend_improving"]:
        return resolved_config["trend_bonus_improving"]

    return 0.0
