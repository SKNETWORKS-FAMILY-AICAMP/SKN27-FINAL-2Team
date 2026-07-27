from __future__ import annotations

from collections import defaultdict
from json import JSONDecodeError, dumps, loads

import pandas as pd

from source_relationships.build import create_stable_id


def parse_json_list(value: object) -> list[str]:
    """CSV의 JSON 배열 값을 문자열 목록으로 읽는다."""
    text = str(value or "").strip()
    if not text:
        return []
    try:
        parsed = loads(text)
    except (JSONDecodeError, TypeError):
        return [text]
    if isinstance(parsed, list):
        return [
            str(item)
            for item in parsed
            if str(item).strip()
        ]
    return [str(parsed)]


def build_canonical_fact_relationships(
    structured_relationships: pd.DataFrame,
    description_relationships: pd.DataFrame,
    canonical_registry: pd.DataFrame,
    policy: dict,
) -> pd.DataFrame:
    """구조화 관계와 공식 설명문 관계를 하나의 사실 관계표로 합친다."""
    projection_policy = policy["canonical_fact_projection"]
    identifier_policy = policy["identifier"]
    json_columns = [
        "description_mention_ids_json",
        "source_relationship_ids_json",
        "raw_relation_types_json",
        "relation_qualifiers_json",
        "source_datasets_json",
        "source_releases_json",
        "evidence_urls_json",
        "detail_urls_json",
        "evidence_sentences_json",
        "scopes_json",
    ]
    required_columns = {
        "start_canonical_id",
        "end_canonical_id",
        "relation_type",
        "verification_status",
    }
    inputs = [
        (structured_relationships, "STRUCTURED_SOURCE"),
        (description_relationships, "OFFICIAL_DESCRIPTION_PATTERN"),
    ]
    aggregates: dict[tuple[str, str, str], dict] = {}
    for relationships, extraction_method in inputs:
        missing_columns = required_columns.difference(
            relationships.columns
        )
        if missing_columns:
            missing_text = ", ".join(sorted(missing_columns))
            raise ValueError(
                f"Canonical 사실 관계 필수 컬럼이 없습니다: {missing_text}"
            )
        for row in relationships.to_dict("records"):
            start_canonical_id = str(
                row["start_canonical_id"]
            ).strip()
            end_canonical_id = str(
                row["end_canonical_id"]
            ).strip()
            relation_type = str(row["relation_type"]).strip()
            if (
                not start_canonical_id
                or not end_canonical_id
                or not relation_type
            ):
                raise ValueError(
                    "Canonical 사실 관계 endpoint 또는 유형이 비었습니다."
                )
            if start_canonical_id == end_canonical_id:
                raise ValueError(
                    "Canonical 사실 관계에 자기 관계가 있습니다."
                )
            key = (
                start_canonical_id,
                relation_type,
                end_canonical_id,
            )
            if key not in aggregates:
                aggregates[key] = {
                    "json_values": defaultdict(set),
                    "verification_statuses": set(),
                    "extraction_methods": set(),
                    "evidence_count": 0,
                    "source_row_count": 0,
                }
            aggregate = aggregates[key]
            for column in json_columns:
                aggregate["json_values"][column].update(
                    parse_json_list(row.get(column, ""))
                )
            aggregate["verification_statuses"].add(
                str(row["verification_status"]).strip()
            )
            aggregate["extraction_methods"].add(extraction_method)
            aggregate["evidence_count"] += int(
                str(row.get("evidence_count") or "0")
            )
            aggregate["source_row_count"] += int(
                str(row.get("source_row_count") or "0")
            )

    canonical_ids = set(
        canonical_registry["canonical_id"].astype(str)
    )
    output_rows: list[dict] = []
    for key, aggregate in aggregates.items():
        start_canonical_id, relation_type, end_canonical_id = key
        missing_endpoint_ids = {
            start_canonical_id,
            end_canonical_id,
        }.difference(canonical_ids)
        if missing_endpoint_ids:
            raise ValueError(
                "Canonical 사실 관계가 registry에 없는 endpoint를 "
                f"참조합니다: {sorted(missing_endpoint_ids)}"
            )
        verification_status = "PATTERN_ASSERTED"
        if (
            "SOURCE_ASSERTED"
            in aggregate["verification_statuses"]
        ):
            verification_status = "SOURCE_ASSERTED"
        relationship_id = create_stable_id(
            identifier_policy["canonical_fact_relationship_prefix"],
            [
                start_canonical_id,
                relation_type,
                end_canonical_id,
                str(projection_policy["policy_version"]),
            ],
            policy,
        )
        output_row = {
            "canonical_relationship_id": relationship_id,
            "start_canonical_id": start_canonical_id,
            "end_canonical_id": end_canonical_id,
            "relation_type": relation_type,
            "extraction_methods_json": dumps(
                sorted(aggregate["extraction_methods"]),
                ensure_ascii=False,
            ),
            "verification_statuses_json": dumps(
                sorted(aggregate["verification_statuses"]),
                ensure_ascii=False,
            ),
            "evidence_count": str(aggregate["evidence_count"]),
            "source_row_count": str(
                aggregate["source_row_count"]
            ),
            "verification_status": verification_status,
            "policy_version": str(
                projection_policy["policy_version"]
            ),
        }
        for column in json_columns:
            output_row[column] = dumps(
                sorted(aggregate["json_values"][column]),
                ensure_ascii=False,
            )
        output_rows.append(output_row)

    columns = [
        "canonical_relationship_id",
        "start_canonical_id",
        "end_canonical_id",
        "relation_type",
        "description_mention_ids_json",
        "source_relationship_ids_json",
        "raw_relation_types_json",
        "relation_qualifiers_json",
        "source_datasets_json",
        "source_releases_json",
        "evidence_urls_json",
        "detail_urls_json",
        "evidence_sentences_json",
        "scopes_json",
        "extraction_methods_json",
        "verification_statuses_json",
        "evidence_count",
        "source_row_count",
        "verification_status",
        "policy_version",
    ]
    facts = pd.DataFrame(output_rows, columns=columns)
    if facts["canonical_relationship_id"].duplicated().any():
        raise ValueError("통합 Canonical 사실 관계 ID가 중복됐습니다.")
    return facts.sort_values(
        [
            "relation_type",
            "start_canonical_id",
            "end_canonical_id",
        ],
        kind="stable",
    ).reset_index(drop=True)
