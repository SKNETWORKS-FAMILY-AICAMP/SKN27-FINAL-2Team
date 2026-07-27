import re


def resolve_source_entity_type(
    metadata: dict,
    source_policy: dict,
) -> str:
    """출처 메타데이터에서 근거가 명확한 EntityType만 반환한다."""
    default_entity_type = str(
        source_policy.get("default_entity_type") or ""
    ).strip()
    if default_entity_type:
        return default_entity_type

    type_field = str(source_policy.get("type_field") or "").strip()
    raw_type = str(metadata.get(type_field) or "").strip()
    exact_type = str(
        source_policy.get("type_mapping", {}).get(raw_type) or ""
    ).strip()
    if exact_type:
        return exact_type
    for type_rule in source_policy.get("type_contains_mapping", []):
        source_value = str(type_rule.get("value") or "").strip()
        entity_type = str(type_rule.get("entity_type") or "").strip()
        if source_value and entity_type and source_value in raw_type:
            return entity_type

    description_field = str(
        source_policy.get("description_field") or ""
    ).strip()
    description = str(metadata.get(description_field) or "").strip()
    for description_rule in source_policy.get(
        "description_type_rules",
        [],
    ):
        pattern = str(description_rule.get("pattern") or "").strip()
        entity_type = str(
            description_rule.get("entity_type") or ""
        ).strip()
        if pattern and entity_type and re.search(pattern, description):
            return entity_type
    return ""
