import re
from json import JSONDecodeError, dumps, loads

import pandas as pd


def parse_json_list(value: object) -> list[str]:
    """JSON 배열을 문자열 목록으로 읽고 잘못된 값은 빈 목록으로 처리한다."""
    try:
        parsed = loads(str(value or "[]"))
    except (JSONDecodeError, TypeError):
        return []
    if not isinstance(parsed, list):
        return []
    return [str(item) for item in parsed if str(item)]


def parse_json_object(value: object) -> dict:
    """SourceRecord의 JSON 객체를 안전하게 읽는다."""
    try:
        parsed = loads(str(value or "{}"))
    except (JSONDecodeError, TypeError):
        return {}
    if not isinstance(parsed, dict):
        return {}
    return parsed


def build_classification_anchor_tables(
    canonical_registry: pd.DataFrame,
    source_records: pd.DataFrame,
    resolution_cases: pd.DataFrame,
    policy: dict,
) -> dict[str, pd.DataFrame]:
    """검증된 구조화 근거로 CanonicalEntity의 Topic·Era 연결을 만든다."""
    resolution_policy = policy["entity_resolution"]
    anchor_policy = resolution_policy["classification_anchors"]
    feature_policy = resolution_policy["source_feature_policy"]
    anchor_version = str(anchor_policy["policy_version"])

    topic_rows = [
        {
            "topic_id": str(topic["topic_id"]),
            "name": str(topic["name"]),
            "status": str(anchor_policy["node_status"]),
            "version": anchor_version,
        }
        for topic in anchor_policy["topics"]
    ]
    era_rows = [
        {
            "era_id": str(era["era_id"]),
            "name": str(era["name"]),
            "status": str(anchor_policy["node_status"]),
            "version": anchor_version,
        }
        for era in anchor_policy["eras"]
    ]
    topic_ids = {row["topic_id"] for row in topic_rows}
    era_ids = {row["era_id"] for row in era_rows}
    if len(topic_ids) != len(topic_rows):
        raise ValueError("Topic 정책에 중복 topic_id가 있습니다.")
    if len(era_ids) != len(era_rows):
        raise ValueError("Era 정책에 중복 era_id가 있습니다.")

    configured_topic_ids: set[str] = set()
    for mapped_ids in anchor_policy["entity_type_topics"].values():
        configured_topic_ids.update(str(topic_id) for topic_id in mapped_ids)
    for mapped_ids in anchor_policy["case_category_topics"].values():
        configured_topic_ids.update(str(topic_id) for topic_id in mapped_ids)
    for rule in anchor_policy["source_topic_rules"]:
        configured_topic_ids.update(
            str(topic_id) for topic_id in rule["topic_ids"]
        )
    unknown_topic_ids = configured_topic_ids.difference(topic_ids)
    if unknown_topic_ids:
        raise ValueError(
            "Topic 규칙이 정의되지 않은 ID를 참조합니다: "
            + ", ".join(sorted(unknown_topic_ids))
        )
    configured_era_ids = {
        str(rule["era_id"]) for rule in anchor_policy["era_rules"]
    }
    unknown_era_ids = configured_era_ids.difference(era_ids)
    if unknown_era_ids:
        raise ValueError(
            "Era 규칙이 정의되지 않은 ID를 참조합니다: "
            + ", ".join(sorted(unknown_era_ids))
        )

    case_by_id = {
        str(row.get("resolution_case_id") or ""): row
        for row in resolution_cases.to_dict("records")
    }
    source_by_id = {
        str(row.get("source_record_id") or ""): row
        for row in source_records.to_dict("records")
    }
    excluded_era_values = {
        str(value).strip()
        for value in feature_policy["era_excluded_values"]
    }
    topic_relationship_rows: list[dict] = []
    era_relationship_rows: list[dict] = []
    review_rows: list[dict] = []

    for canonical in canonical_registry.to_dict("records"):
        canonical_id = str(canonical.get("canonical_id") or "")
        topic_evidence: dict[str, list[dict]] = {}
        era_evidence: dict[str, list[dict]] = {}
        entity_type = str(canonical.get("entity_type") or "")
        for topic_id in anchor_policy["entity_type_topics"].get(
            entity_type,
            [],
        ):
            topic_evidence.setdefault(str(topic_id), []).append(
                {
                    "evidence_type": "ENTITY_TYPE",
                    "field": "entity_type",
                    "value": entity_type,
                }
            )

        resolution_case_ids = parse_json_list(
            canonical.get("resolution_case_ids_json")
        )
        for resolution_case_id in resolution_case_ids:
            case = case_by_id.get(resolution_case_id, {})
            category = str(case.get("category") or "").strip()
            for topic_id in anchor_policy["case_category_topics"].get(
                category,
                [],
            ):
                topic_evidence.setdefault(str(topic_id), []).append(
                    {
                        "evidence_type": "CASE_CATEGORY",
                        "resolution_case_id": resolution_case_id,
                        "field": "category",
                        "value": category,
                    }
                )

        source_record_ids = parse_json_list(
            canonical.get("identity_member_source_ids_json")
        )
        for source_record_id in source_record_ids:
            source_row = source_by_id.get(source_record_id, {})
            source = str(source_row.get("source") or "")
            metadata = parse_json_object(
                source_row.get("source_metadata_json")
            )
            for rule in anchor_policy["source_topic_rules"]:
                if str(rule["source"]) != source:
                    continue
                field = str(rule["field"])
                value = str(metadata.get(field) or "").strip()
                if not value:
                    continue
                if not re.search(str(rule["pattern"]), value):
                    continue
                for topic_id in rule["topic_ids"]:
                    topic_evidence.setdefault(str(topic_id), []).append(
                        {
                            "evidence_type": "SOURCE_METADATA",
                            "source_record_id": source_record_id,
                            "source": source,
                            "field": field,
                            "value": value,
                        }
                    )

            source_policy = feature_policy["sources"].get(source, {})
            for field_value in source_policy.get("era_fields", []):
                field = str(field_value)
                value = str(metadata.get(field) or "").strip()
                if not value or value in excluded_era_values:
                    continue
                for rule in anchor_policy["era_rules"]:
                    if not re.search(str(rule["pattern"]), value):
                        continue
                    era_id = str(rule["era_id"])
                    era_evidence.setdefault(era_id, []).append(
                        {
                            "evidence_type": "SOURCE_METADATA",
                            "source_record_id": source_record_id,
                            "source": source,
                            "field": field,
                            "value": value,
                        }
                    )

        for topic_id, evidence in sorted(topic_evidence.items()):
            topic_relationship_rows.append(
                {
                    "canonical_id": canonical_id,
                    "topic_id": topic_id,
                    "verification_status": str(
                        anchor_policy["relationship_status"]
                    ),
                    "method": str(
                        anchor_policy["relationship_method"]
                    ),
                    "evidence_json": dumps(
                        evidence,
                        ensure_ascii=False,
                        sort_keys=True,
                    ),
                    "version": anchor_version,
                }
            )
        for era_id, evidence in sorted(era_evidence.items()):
            era_relationship_rows.append(
                {
                    "canonical_id": canonical_id,
                    "era_id": era_id,
                    "verification_status": str(
                        anchor_policy["relationship_status"]
                    ),
                    "method": str(
                        anchor_policy["relationship_method"]
                    ),
                    "evidence_json": dumps(
                        evidence,
                        ensure_ascii=False,
                        sort_keys=True,
                    ),
                    "version": anchor_version,
                }
            )

        missing_dimensions: list[str] = []
        if not topic_evidence:
            missing_dimensions.append("TOPIC")
        if not era_evidence:
            missing_dimensions.append("ERA")
        if missing_dimensions:
            review_rows.append(
                {
                    "canonical_id": canonical_id,
                    "display_name": str(
                        canonical.get("display_name") or ""
                    ),
                    "entity_type": entity_type,
                    "missing_dimensions_json": dumps(
                        missing_dimensions,
                        ensure_ascii=False,
                    ),
                    "resolution_case_ids_json": dumps(
                        resolution_case_ids,
                        ensure_ascii=False,
                    ),
                    "source_record_ids_json": dumps(
                        source_record_ids,
                        ensure_ascii=False,
                    ),
                    "review_status": str(anchor_policy["review_status"]),
                    "version": anchor_version,
                }
            )

    return {
        "topic_nodes": pd.DataFrame(
            topic_rows,
            columns=["topic_id", "name", "status", "version"],
        ),
        "era_nodes": pd.DataFrame(
            era_rows,
            columns=["era_id", "name", "status", "version"],
        ),
        "canonical_topic_relationships": pd.DataFrame(
            topic_relationship_rows,
            columns=[
                "canonical_id",
                "topic_id",
                "verification_status",
                "method",
                "evidence_json",
                "version",
            ],
        ),
        "canonical_era_relationships": pd.DataFrame(
            era_relationship_rows,
            columns=[
                "canonical_id",
                "era_id",
                "verification_status",
                "method",
                "evidence_json",
                "version",
            ],
        ),
        "canonical_classification_review": pd.DataFrame(
            review_rows,
            columns=[
                "canonical_id",
                "display_name",
                "entity_type",
                "missing_dimensions_json",
                "resolution_case_ids_json",
                "source_record_ids_json",
                "review_status",
                "version",
            ],
        ),
    }
