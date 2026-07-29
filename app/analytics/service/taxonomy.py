from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence
from urllib.parse import quote, unquote


@dataclass(frozen=True)
class TaxonomyConfig:
    version: str
    unclassified_value: str
    unclassified_label: str
    composite_separator: str
    field_order: tuple[str, ...]
    field_labels: Mapping[str, str]
    era_aliases: Mapping[str, tuple[str, ...]]
    topic_aliases: Mapping[str, tuple[str, ...]]


def get_taxonomy_config() -> TaxonomyConfig:
    return TaxonomyConfig(
        version="taxonomy-v1",
        unclassified_value="__unclassified__",
        unclassified_label="미분류",
        composite_separator=" · ",
        field_order=("era", "topic", "qType", "coreConcept"),
        field_labels={
            "era": "시대",
            "topic": "주제",
            "qType": "유형",
            "coreConcept": "핵심 개념",
        },
        era_aliases={
            "고조선": ("고조선",),
            "남북국 시대": ("남북국시대", "남북국", "통일신라", "발해"),
            "초기 국가": ("초기국가", "부여", "삼한", "옥저", "동예"),
            "선사 시대": ("선사시대", "선사", "구석기", "신석기", "청동기"),
            "삼국시대": ("삼국시대", "삼국", "고구려", "백제", "신라", "가야"),
            "일제강점기": ("일제강점기", "일제강점", "일제", "식민지"),
            "개항기": ("개항기", "개화기", "대한제국", "근대"),
            "현대": ("현대", "대한민국", "광복이후", "해방이후"),
            "고려": ("고려", "고려시대"),
            "조선": ("조선", "조선시대", "조선전기", "조선중기", "조선후기"),
        },
        topic_aliases={
            "사건": ("사건",),
            "인물": ("인물",),
            "정치": ("정치",),
            "제도": ("제도",),
            "문화": ("문화",),
            "사회": ("사회",),
            "군사": ("군사", "전쟁", "전투", "국방"),
            "경제": ("경제",),
            "사상 종교": ("사상종교", "사상", "종교", "불교", "유교", "성리학"),
            "외교": ("외교", "대외", "국제"),
        },
    )


def normalize_field_name(field_name: str) -> str:
    if field_name in ("q_type", "question_type", "qType"):
        return "qType"
    elif field_name in ("core_concept", "coreConcept", "question__core_concept"):
        return "coreConcept"
    return field_name


def normalize_classification_value(
    field_name: str,
    value: object,
    config: TaxonomyConfig | None = None,
) -> str:
    resolved_config = config or get_taxonomy_config()
    normalized_field = normalize_field_name(field_name)
    display_value = str(value or "").strip()
    if not display_value:
        return resolved_config.unclassified_value
    if display_value == resolved_config.unclassified_label:
        # 화면 라벨("미분류")로 되돌아온 값도 내부 미분류 값으로 정규화한다.
        # 통계 쪽 groupKeyId(표시 라벨 기반)와 취약 판정 쪽 groupKeyId(원본 값
        # 기반)가 같은 키로 만나야 미분류 항목에도 취약 배지가 붙는다.
        return resolved_config.unclassified_value

    aliases: Mapping[str, tuple[str, ...]] = {}
    if normalized_field == "era":
        aliases = resolved_config.era_aliases
    elif normalized_field == "topic":
        aliases = resolved_config.topic_aliases
    elif normalized_field in ("qType", "coreConcept"):
        # 유형과 핵심 개념은 별칭 표를 두지 않고 원문을 그대로 쓴다.
        return display_value

    comparison_key = _build_comparison_key(display_value)
    for canonical_value, alias_values in aliases.items():
        for alias in (canonical_value, *alias_values):
            if comparison_key == _build_comparison_key(alias):
                return canonical_value

    return display_value


def build_group_key_id(
    group_values: Mapping[str, object],
    config: TaxonomyConfig | None = None,
) -> str:
    resolved_config = config or get_taxonomy_config()
    normalized_values = {
        normalize_field_name(field_name): normalize_classification_value(
            field_name,
            value,
            resolved_config,
        )
        for field_name, value in group_values.items()
    }
    ordered_fields = [
        field_name
        for field_name in resolved_config.field_order
        if field_name in normalized_values
    ]
    additional_fields = sorted(set(normalized_values) - set(ordered_fields))
    ordered_fields.extend(additional_fields)
    return "|".join(
        f"{quote(field_name, safe='')}={quote(normalized_values[field_name], safe='')}"
        for field_name in ordered_fields
    )


def parse_group_key_id(group_key_id: str) -> dict[str, str]:
    parsed: dict[str, str] = {}
    if not group_key_id:
        return parsed

    for item in group_key_id.split("|"):
        field_name, separator, value = item.partition("=")
        if not separator:
            raise ValueError("영역 식별자 형식이 올바르지 않습니다.")
        decoded_field = normalize_field_name(unquote(field_name))
        if decoded_field in parsed:
            raise ValueError("영역 식별자에 중복 필드가 있습니다.")
        parsed[decoded_field] = unquote(value)
    return parsed


def build_group_display_label(
    group_values: Mapping[str, object] | Sequence[Sequence[object]],
    config: TaxonomyConfig | None = None,
) -> str:
    resolved_config = config or get_taxonomy_config()
    mapped_values: Mapping[str, object] = group_values
    if not isinstance(group_values, Mapping):
        mapped_values = {
            str(item[0]): item[1]
            for item in group_values
            if len(item) >= 2
        }
    normalized = {
        normalize_field_name(field_name): normalize_classification_value(
            field_name,
            value,
            resolved_config,
        )
        for field_name, value in mapped_values.items()
    }
    labels: list[str] = []
    for field_name in resolved_config.field_order:
        if field_name not in normalized:
            continue
        value = normalized[field_name]
        if value == resolved_config.unclassified_value:
            value = resolved_config.unclassified_label
        labels.append(value)
    return resolved_config.composite_separator.join(labels)


def build_target_display_label(
    era: object,
    topic: object,
    q_type: object = None,
    config: TaxonomyConfig | None = None,
) -> str:
    group_values: dict[str, object] = {"era": era, "topic": topic}
    if q_type is not None:
        group_values["qType"] = q_type
    return build_group_display_label(group_values, config)


def get_unclassified_label(config: TaxonomyConfig | None = None) -> str:
    resolved_config = config or get_taxonomy_config()
    return resolved_config.unclassified_label


def get_display_label(
    field_name: str,
    value: object,
    config: TaxonomyConfig | None = None,
) -> str:
    resolved_config = config or get_taxonomy_config()
    normalized_value = normalize_classification_value(field_name, value, resolved_config)
    if normalized_value == resolved_config.unclassified_value:
        return resolved_config.unclassified_label
    return normalized_value


def order_group_fields(
    group_fields: Sequence[str],
    config: TaxonomyConfig | None = None,
) -> tuple[str, ...]:
    resolved_config = config or get_taxonomy_config()
    normalized_fields = {normalize_field_name(field_name) for field_name in group_fields}
    ordered = [field for field in resolved_config.field_order if field in normalized_fields]
    ordered.extend(sorted(normalized_fields - set(ordered)))
    return tuple(ordered)


def _build_comparison_key(value: object) -> str:
    return "".join(
        character.lower()
        for character in str(value or "").strip()
        if character not in " \t\r\n·ㆍ/()_-"
    )
