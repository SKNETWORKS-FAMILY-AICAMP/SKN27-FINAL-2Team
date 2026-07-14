from analytics.service.classification import (
    normalize_classification_value,
    should_normalize_classification,
)


def get_taxonomy_config():
    return {
        "unclassified_label": "미분류",
        "composite_separator": " · ",
    }


def get_unclassified_label():
    return get_taxonomy_config()["unclassified_label"]


def get_display_label(field_name, value):
    """
    분류 정규화값을 화면 표시명으로 변환한다.
    """
    normalized_value = normalize_classification_value(field_name, value)
    if normalized_value:
        return normalized_value
    if should_normalize_classification(field_name):
        return get_unclassified_label()

    display_value = str(value or "").strip()
    if display_value:
        return display_value

    return get_unclassified_label()


def build_group_display_label(group_key):
    """
    [[field, value], ...] 구조를 복합 표시명으로 변환한다.
    """
    labels = [
        get_display_label(field_name, value)
        for field_name, value in group_key
    ]
    return get_taxonomy_config()["composite_separator"].join(labels)


def build_target_display_label(era, topic, q_type):
    return build_group_display_label(
        [
            ["era", era],
            ["topic", topic],
            ["q_type", q_type],
        ]
    )
