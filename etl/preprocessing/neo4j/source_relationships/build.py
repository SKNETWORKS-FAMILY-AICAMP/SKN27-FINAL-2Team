from __future__ import annotations

from hashlib import new as new_hash
from json import dumps, load, loads
from pathlib import Path

import pandas as pd


def load_source_relationship_policy(policy_path: str) -> dict:
    """원천 관계 전처리 정책을 읽고 필수 구성을 검증한다."""
    path = Path(policy_path)
    if not path.is_file():
        raise FileNotFoundError(f"원천 관계 정책 파일이 없습니다: {path}")
    with path.open("r", encoding="utf-8") as policy_file:
        policy = load(policy_file)
    required_sections = {
        "policy_version",
        "source_release",
        "identifier",
        "inputs",
        "outputs",
        "sources",
        "relationships",
        "thesaurus_categories",
        "canonical_projection",
        "neo4j_load",
    }
    missing_sections = required_sections.difference(policy)
    if missing_sections:
        missing_text = ", ".join(sorted(missing_sections))
        raise ValueError(f"원천 관계 정책 구성이 없습니다: {missing_text}")
    return policy


def create_stable_id(prefix: str, parts: list[str], policy: dict) -> str:
    """정책에 지정된 해시로 재실행 가능한 식별자를 만든다."""
    identifier_policy = policy["identifier"]
    hasher = new_hash(identifier_policy["hash_algorithm"])
    serialized = "\u241f".join(str(part) for part in parts)
    hasher.update(serialized.encode("utf-8"))
    digest_length = int(identifier_policy["digest_length"])
    return f"{prefix}{hasher.hexdigest()[:digest_length]}"


def calculate_source_release(source_path: Path, policy: dict) -> str:
    """원천 파일 내용으로 release 식별자를 계산한다."""
    release_policy = policy["source_release"]
    hasher = new_hash(release_policy["hash_algorithm"])
    chunk_size = int(release_policy["chunk_size_bytes"])
    with source_path.open("rb") as input_file:
        chunk = input_file.read(chunk_size)
        while chunk:
            hasher.update(chunk)
            chunk = input_file.read(chunk_size)
    digest_length = int(release_policy["digest_length"])
    return (
        f"{release_policy['hash_algorithm']}-"
        f"{hasher.hexdigest()[:digest_length]}"
    )


def first_non_blank(values: pd.Series) -> str:
    """같은 원천 ID의 값 중 첫 번째 비어 있지 않은 값을 고른다."""
    for value in values:
        stripped = str(value).strip()
        if stripped:
            return stripped
    return ""


def build_source_record_id(
    source_policy: dict,
    source_key: str,
    source_release: str,
) -> str:
    """원천별 ID 템플릿에 key와 release를 적용한다."""
    return source_policy["source_record_id_template"].format(
        source_key=source_key,
        source_release=source_release,
    )


def build_source_nodes(
    people: pd.DataFrame,
    events: pd.DataFrame,
    thesaurus: pd.DataFrame,
    releases: dict[str, str],
    policy: dict,
) -> pd.DataFrame:
    """ITKC 인물·사건과 시소러스 용어를 SourceRecord 노드로 만든다."""
    source_policies = policy["sources"]
    rows: list[dict] = []

    person_policy = source_policies["itkc_person"]
    for person_id, group in people.groupby("person_id", sort=False):
        source_key = str(person_id).strip()
        if not source_key:
            continue
        metadata = {
            "name": first_non_blank(group["name"]),
            "birth_year": first_non_blank(group["birth_year"]),
            "death_year": first_non_blank(group["death_year"]),
            "bonkwan": first_non_blank(group["bonkwan"]),
            "ja": first_non_blank(group["ja"]),
            "ho": first_non_blank(group["ho"]),
            "father": first_non_blank(group["father"]),
        }
        source_urls = sorted(
            {
                str(value).strip()
                for value in group["detail_url"]
                if str(value).strip()
            }
        )
        rows.append(
            {
                "source_record_id": build_source_record_id(
                    person_policy,
                    source_key,
                    releases["itkc_people"],
                ),
                "source": person_policy["source"],
                "source_key": source_key,
                "source_release": releases["itkc_people"],
                "record_type": person_policy["record_type"],
                "display_name": metadata["name"],
                "source_urls_json": dumps(
                    source_urls,
                    ensure_ascii=False,
                ),
                "source_metadata_json": dumps(
                    metadata,
                    ensure_ascii=False,
                    sort_keys=True,
                ),
            }
        )

    event_policy = source_policies["itkc_event"]
    for event_id, group in events.groupby("event_id", sort=False):
        source_key = str(event_id).strip()
        if not source_key:
            continue
        metadata = {
            "event_name": first_non_blank(group["event_name"]),
            "subject_categories": sorted(
                {
                    str(value).strip()
                    for value in group["subject_category"]
                    if str(value).strip()
                }
            ),
            "periods": sorted(
                {
                    str(value).strip()
                    for value in group["period"]
                    if str(value).strip()
                }
            ),
            "event_dates": sorted(
                {
                    str(value).strip()
                    for value in group["event_date"]
                    if str(value).strip()
                }
            ),
            "related_events": sorted(
                {
                    str(value).strip()
                    for value in group["related_event"]
                    if str(value).strip()
                }
            ),
            "scopes": sorted(
                {
                    str(value).strip()
                    for value in group["scope"]
                    if str(value).strip()
                }
            ),
        }
        source_urls = sorted(
            {
                str(value).strip()
                for value in group["detail_url"]
                if str(value).strip()
            }
        )
        rows.append(
            {
                "source_record_id": build_source_record_id(
                    event_policy,
                    source_key,
                    releases["itkc_events"],
                ),
                "source": event_policy["source"],
                "source_key": source_key,
                "source_release": releases["itkc_events"],
                "record_type": event_policy["record_type"],
                "display_name": metadata["event_name"],
                "source_urls_json": dumps(
                    source_urls,
                    ensure_ascii=False,
                ),
                "source_metadata_json": dumps(
                    metadata,
                    ensure_ascii=False,
                    sort_keys=True,
                ),
            }
        )

    term_policy = source_policies["thesaurus_term"]
    for record in thesaurus.to_dict("records"):
        source_key = str(record["term_id"]).strip()
        if not source_key:
            continue
        metadata = {
            "term_name": str(record["term_name"]).strip(),
            "term_kind": str(record["term_kind"]).strip(),
            "hanja": str(record["term_ch"]).strip(),
            "remark": str(record["term_remark"]).strip(),
            "attribute": str(record["term_attr"]).strip(),
            "year": str(record["term_year"]).strip(),
            "era": str(record["term_times"]).strip(),
            "category_path": str(record["term_lk"]).strip(),
            "description": str(record["term_desc"]).strip(),
            "reference": str(record["term_reference"]).strip(),
        }
        rows.append(
            {
                "source_record_id": build_source_record_id(
                    term_policy,
                    source_key,
                    releases["thesaurus"],
                ),
                "source": term_policy["source"],
                "source_key": source_key,
                "source_release": releases["thesaurus"],
                "record_type": term_policy["record_type"],
                "display_name": metadata["term_name"],
                "source_urls_json": "[]",
                "source_metadata_json": dumps(
                    metadata,
                    ensure_ascii=False,
                    sort_keys=True,
                ),
            }
        )
    return pd.DataFrame(rows)


def build_source_relationships(
    person_relations: pd.DataFrame,
    event_relations: pd.DataFrame,
    thesaurus: pd.DataFrame,
    releases: dict[str, str],
    policy: dict,
) -> pd.DataFrame:
    """원천 관계를 방향과 출처를 보존한 중복 없는 edge로 만든다."""
    relationship_policy = policy["relationships"]
    source_policies = policy["sources"]
    identifier_policy = policy["identifier"]
    person_type_map = relationship_policy["itkc_person_type_map"]
    unknown_person_types = set(
        person_relations["relation_type"]
    ).difference(person_type_map)
    if unknown_person_types:
        unknown_text = ", ".join(sorted(unknown_person_types))
        raise ValueError(f"매핑되지 않은 ITKC 인물 관계입니다: {unknown_text}")

    rows: list[dict] = []
    person_policy = source_policies["itkc_person"]
    person_group_columns = [
        "person_id",
        "relation_type",
        "related_person_id",
    ]
    for keys, group in person_relations.groupby(
        person_group_columns,
        sort=False,
    ):
        person_id, raw_relation_type, related_person_id = keys
        start_id = build_source_record_id(
            person_policy,
            str(person_id).strip(),
            releases["itkc_people"],
        )
        end_id = build_source_record_id(
            person_policy,
            str(related_person_id).strip(),
            releases["itkc_people"],
        )
        relation_type = person_type_map[str(raw_relation_type)]
        relationship_identity_parts = [
            start_id,
            relation_type,
            end_id,
            relationship_policy["itkc_person_dataset"],
        ]
        if relation_type in relationship_policy[
            "raw_type_distinct_relation_types"
        ]:
            relationship_identity_parts.append(str(raw_relation_type))
        relationship_id = create_stable_id(
            identifier_policy["relationship_prefix"],
            relationship_identity_parts,
            policy,
        )
        rows.append(
            {
                "source_relationship_id": relationship_id,
                "start_source_record_id": start_id,
                "end_source_record_id": end_id,
                "relation_type": relation_type,
                "raw_relation_type": str(raw_relation_type),
                "source_dataset": relationship_policy[
                    "itkc_person_dataset"
                ],
                "source_release": releases["itkc_person_relations"],
                "verification_status": relationship_policy[
                    "verification_status"
                ],
                "evidence_urls_json": dumps(
                    sorted(
                        {
                            str(value).strip()
                            for value in group["evidence_url"]
                            if str(value).strip()
                        }
                    ),
                    ensure_ascii=False,
                ),
                "detail_urls_json": dumps(
                    sorted(
                        {
                            str(value).strip()
                            for value in group["detail_url"]
                            if str(value).strip()
                        }
                    ),
                    ensure_ascii=False,
                ),
                "scopes_json": "[]",
                "source_row_count": len(group),
            }
        )

    event_policy = source_policies["itkc_event"]
    event_group_columns = [
        "event_id",
        "relation_type",
        "person_id",
    ]
    for keys, group in event_relations.groupby(
        event_group_columns,
        sort=False,
    ):
        event_id, raw_relation_type, person_id = keys
        start_id = build_source_record_id(
            event_policy,
            str(event_id).strip(),
            releases["itkc_events"],
        )
        end_id = build_source_record_id(
            person_policy,
            str(person_id).strip(),
            releases["itkc_people"],
        )
        relation_type = relationship_policy["itkc_event_relation_type"]
        relationship_id = create_stable_id(
            identifier_policy["relationship_prefix"],
            [
                start_id,
                relation_type,
                end_id,
                relationship_policy["itkc_event_dataset"],
            ],
            policy,
        )
        rows.append(
            {
                "source_relationship_id": relationship_id,
                "start_source_record_id": start_id,
                "end_source_record_id": end_id,
                "relation_type": relation_type,
                "raw_relation_type": str(raw_relation_type),
                "source_dataset": relationship_policy[
                    "itkc_event_dataset"
                ],
                "source_release": releases["itkc_event_relations"],
                "verification_status": relationship_policy[
                    "verification_status"
                ],
                "evidence_urls_json": dumps(
                    sorted(
                        {
                            str(value).strip()
                            for value in group["evidence_url"]
                            if str(value).strip()
                        }
                    ),
                    ensure_ascii=False,
                ),
                "detail_urls_json": dumps(
                    sorted(
                        {
                            str(value).strip()
                            for value in group["detail_url"]
                            if str(value).strip()
                        }
                    ),
                    ensure_ascii=False,
                ),
                "scopes_json": dumps(
                    sorted(
                        {
                            str(value).strip()
                            for value in group["scope"]
                            if str(value).strip()
                        }
                    ),
                    ensure_ascii=False,
                ),
                "source_row_count": len(group),
            }
        )

    term_policy = source_policies["thesaurus_term"]
    relation_type = relationship_policy[
        "thesaurus_top_category_relation_type"
    ]
    for record in thesaurus.to_dict("records"):
        term_id = str(record["term_id"]).strip()
        topterm_id = str(record["topterm_id"]).strip()
        if not term_id or not topterm_id or term_id == topterm_id:
            continue
        start_id = build_source_record_id(
            term_policy,
            term_id,
            releases["thesaurus"],
        )
        end_id = build_source_record_id(
            term_policy,
            topterm_id,
            releases["thesaurus"],
        )
        relationship_id = create_stable_id(
            identifier_policy["relationship_prefix"],
            [
                start_id,
                relation_type,
                end_id,
                relationship_policy["thesaurus_top_category_dataset"],
            ],
            policy,
        )
        rows.append(
            {
                "source_relationship_id": relationship_id,
                "start_source_record_id": start_id,
                "end_source_record_id": end_id,
                "relation_type": relation_type,
                "raw_relation_type": "topterm_id",
                "source_dataset": relationship_policy[
                    "thesaurus_top_category_dataset"
                ],
                "source_release": releases["thesaurus"],
                "verification_status": relationship_policy[
                    "verification_status"
                ],
                "evidence_urls_json": "[]",
                "detail_urls_json": "[]",
                "scopes_json": "[]",
                "source_row_count": 1,
            }
        )
    return pd.DataFrame(rows)


def build_thesaurus_categories(
    thesaurus: pd.DataFrame,
    releases: dict[str, str],
    policy: dict,
) -> dict[str, pd.DataFrame]:
    """term_lk 분류 경로를 Category 노드와 계층 edge로 변환한다."""
    category_policy = policy["thesaurus_categories"]
    relationship_policy = policy["relationships"]
    identifier_policy = policy["identifier"]
    term_policy = policy["sources"]["thesaurus_term"]
    separator = category_policy["separator"]
    null_values = set(category_policy["null_values"])
    category_rows: dict[str, dict] = {}
    hierarchy_rows: dict[str, dict] = {}
    membership_rows: dict[str, dict] = {}

    for record in thesaurus.to_dict("records"):
        raw_path = str(record["term_lk"]).strip()
        if raw_path in null_values:
            continue
        path_parts = [
            part.strip()
            for part in raw_path.split(separator)
            if part.strip()
        ]
        if not path_parts:
            continue
        parent_category_id = ""
        cumulative_parts: list[str] = []
        for depth, category_name in enumerate(path_parts, start=1):
            cumulative_parts.append(category_name)
            category_path = separator.join(cumulative_parts)
            category_id = create_stable_id(
                identifier_policy["category_prefix"],
                [category_path],
                policy,
            )
            category_rows[category_id] = {
                "category_id": category_id,
                "category_name": category_name,
                "category_path": category_path,
                "depth": depth,
                "source": term_policy["source"],
                "source_release": releases["thesaurus"],
            }
            if parent_category_id:
                hierarchy_id = create_stable_id(
                    identifier_policy["category_relationship_prefix"],
                    [
                        category_id,
                        relationship_policy[
                            "thesaurus_subcategory_relation_type"
                        ],
                        parent_category_id,
                    ],
                    policy,
                )
                hierarchy_rows[hierarchy_id] = {
                    "category_relationship_id": hierarchy_id,
                    "child_category_id": category_id,
                    "parent_category_id": parent_category_id,
                    "relation_type": relationship_policy[
                        "thesaurus_subcategory_relation_type"
                    ],
                    "source_dataset": relationship_policy[
                        "thesaurus_category_dataset"
                    ],
                    "source_release": releases["thesaurus"],
                    "verification_status": relationship_policy[
                        "verification_status"
                    ],
                }
            parent_category_id = category_id

        term_source_record_id = build_source_record_id(
            term_policy,
            str(record["term_id"]).strip(),
            releases["thesaurus"],
        )
        membership_id = create_stable_id(
            identifier_policy["category_relationship_prefix"],
            [
                term_source_record_id,
                relationship_policy[
                    "thesaurus_term_category_relation_type"
                ],
                parent_category_id,
            ],
            policy,
        )
        membership_rows[membership_id] = {
            "source_category_relationship_id": membership_id,
            "source_record_id": term_source_record_id,
            "category_id": parent_category_id,
            "relation_type": relationship_policy[
                "thesaurus_term_category_relation_type"
            ],
            "source_dataset": relationship_policy[
                "thesaurus_category_dataset"
            ],
            "source_release": releases["thesaurus"],
            "verification_status": relationship_policy[
                "verification_status"
            ],
        }
    category_columns = [
        "category_id",
        "category_name",
        "category_path",
        "depth",
        "source",
        "source_release",
    ]
    membership_columns = [
        "source_category_relationship_id",
        "source_record_id",
        "category_id",
        "relation_type",
        "source_dataset",
        "source_release",
        "verification_status",
    ]
    hierarchy_columns = [
        "category_relationship_id",
        "child_category_id",
        "parent_category_id",
        "relation_type",
        "source_dataset",
        "source_release",
        "verification_status",
    ]
    return {
        "thesaurus_category_nodes": pd.DataFrame(
            list(category_rows.values()),
            columns=category_columns,
        ),
        "source_category_relationships": pd.DataFrame(
            list(membership_rows.values()),
            columns=membership_columns,
        ),
        "thesaurus_category_relationships": pd.DataFrame(
            list(hierarchy_rows.values()),
            columns=hierarchy_columns,
        ),
    }


def build_canonical_projection(
    source_relationships: pd.DataFrame,
    canonical_resolutions: pd.DataFrame | None,
    canonical_registry: pd.DataFrame | None,
    policy: dict,
) -> dict[str, pd.DataFrame]:
    """양쪽 SourceRecord가 승인된 경우에만 canonical 관계로 투영한다."""
    projection_policy = policy["canonical_projection"]
    identifier_policy = policy["identifier"]
    included_types = set(projection_policy["included_relation_types"])
    candidates = source_relationships[
        source_relationships["relation_type"].isin(included_types)
    ].copy()
    resolution_map: dict[str, str] = {}
    canonical_type_by_id: dict[str, str] = {}
    if canonical_resolutions is not None:
        accepted_status = projection_policy["accepted_match_status"]
        accepted = canonical_resolutions[
            canonical_resolutions["match_status"] == accepted_status
        ]
        conflicting = (
            accepted.groupby("source_record_id")["canonical_id"]
            .nunique()
            .gt(1)
        )
        if conflicting.any():
            conflict_count = int(conflicting.sum())
            raise ValueError(
                "하나의 SourceRecord가 여러 canonical ID에 승인됐습니다: "
                f"{conflict_count}건"
            )
        resolution_map = dict(
            zip(accepted["source_record_id"], accepted["canonical_id"])
        )
    if canonical_registry is not None:
        required_columns = {
            "canonical_id",
            "entity_type",
            "lifecycle_status",
            "identity_member_source_ids_json",
        }
        missing_columns = required_columns.difference(
            canonical_registry.columns
        )
        if missing_columns:
            raise ValueError(
                "canonical registry 필수 컬럼이 없습니다: "
                + ", ".join(sorted(missing_columns))
            )
        active_registry = canonical_registry[
            canonical_registry["lifecycle_status"]
            == projection_policy["accepted_registry_status"]
        ]
        for registry_row in active_registry.to_dict("records"):
            canonical_id = str(registry_row["canonical_id"])
            canonical_type_by_id[canonical_id] = str(
                registry_row["entity_type"]
            )
            source_record_ids = loads(
                str(
                    registry_row[
                        "identity_member_source_ids_json"
                    ]
                    or "[]"
                )
            )
            for source_record_id_value in source_record_ids:
                source_record_id = str(source_record_id_value)
                existing_target = resolution_map.get(source_record_id)
                if (
                    existing_target
                    and existing_target != canonical_id
                ):
                    raise ValueError(
                        "SourceRecord의 accepted canonical ID와 "
                        "registry canonical ID가 다릅니다: "
                        f"{source_record_id}"
                    )
                resolution_map[source_record_id] = canonical_id

    projected_rows: list[dict] = []
    exclusion_rows: list[dict] = []
    for record in candidates.to_dict("records"):
        start_canonical_id = resolution_map.get(
            record["start_source_record_id"],
            "",
        )
        end_canonical_id = resolution_map.get(
            record["end_source_record_id"],
            "",
        )
        start_entity_type = canonical_type_by_id.get(
            start_canonical_id,
            "",
        )
        end_entity_type = canonical_type_by_id.get(
            end_canonical_id,
            "",
        )
        if (
            start_canonical_id
            and end_canonical_id
            and record["relation_type"]
            in projection_policy["symmetric_relation_types"]
            and start_canonical_id > end_canonical_id
        ):
            start_canonical_id, end_canonical_id = (
                end_canonical_id,
                start_canonical_id,
            )
            start_entity_type, end_entity_type = (
                end_entity_type,
                start_entity_type,
            )
        exclusion_reason = ""
        if not start_canonical_id and not end_canonical_id:
            exclusion_reason = "BOTH_ENDPOINTS_UNRESOLVED"
        elif not start_canonical_id:
            exclusion_reason = "START_ENDPOINT_UNRESOLVED"
        elif not end_canonical_id:
            exclusion_reason = "END_ENDPOINT_UNRESOLVED"
        elif (
            projection_policy["exclude_self_relationships"]
            and start_canonical_id == end_canonical_id
        ):
            exclusion_reason = "CANONICAL_SELF_RELATIONSHIP"
        relation_contract = projection_policy[
            "relation_type_contracts"
        ].get(record["relation_type"], {})
        if (
            not exclusion_reason
            and start_entity_type
            and start_entity_type
            not in relation_contract.get("start_entity_types", [])
        ):
            exclusion_reason = "START_ENTITY_TYPE_CONFLICT"
        elif (
            not exclusion_reason
            and end_entity_type
            and end_entity_type
            not in relation_contract.get("end_entity_types", [])
        ):
            exclusion_reason = "END_ENTITY_TYPE_CONFLICT"
        if exclusion_reason:
            should_write_exclusion = (
                exclusion_reason != "BOTH_ENDPOINTS_UNRESOLVED"
                or projection_policy[
                    "write_both_unresolved_exclusions"
                ]
            )
            if should_write_exclusion:
                exclusion_rows.append(
                    {
                        "source_relationship_id": record[
                            "source_relationship_id"
                        ],
                        "start_source_record_id": record[
                            "start_source_record_id"
                        ],
                        "end_source_record_id": record[
                            "end_source_record_id"
                        ],
                        "relation_type": record["relation_type"],
                        "start_canonical_id": start_canonical_id,
                        "end_canonical_id": end_canonical_id,
                        "start_entity_type": start_entity_type,
                        "end_entity_type": end_entity_type,
                        "exclusion_reason": exclusion_reason,
                    }
                )
            continue
        projected_rows.append(
            {
                **record,
                "start_canonical_id": start_canonical_id,
                "end_canonical_id": end_canonical_id,
                "start_entity_type": start_entity_type,
                "end_entity_type": end_entity_type,
            }
        )

    canonical_rows: list[dict] = []
    if projected_rows:
        projected = pd.DataFrame(projected_rows)
        group_columns = [
            "start_canonical_id",
            "relation_type",
            "end_canonical_id",
        ]
        for keys, group in projected.groupby(group_columns, sort=False):
            start_canonical_id, relation_type, end_canonical_id = keys
            canonical_id = create_stable_id(
                identifier_policy["canonical_relationship_prefix"],
                [
                    str(start_canonical_id),
                    str(relation_type),
                    str(end_canonical_id),
                ],
                policy,
            )
            canonical_rows.append(
                {
                    "canonical_relationship_id": canonical_id,
                    "start_canonical_id": start_canonical_id,
                    "end_canonical_id": end_canonical_id,
                    "relation_type": relation_type,
                    "source_relationship_ids_json": dumps(
                        sorted(group["source_relationship_id"].unique()),
                        ensure_ascii=False,
                    ),
                    "raw_relation_types_json": dumps(
                        sorted(group["raw_relation_type"].unique()),
                        ensure_ascii=False,
                    ),
                    "source_datasets_json": dumps(
                        sorted(group["source_dataset"].unique()),
                        ensure_ascii=False,
                    ),
                    "source_releases_json": dumps(
                        sorted(group["source_release"].unique()),
                        ensure_ascii=False,
                    ),
                    "evidence_urls_json": dumps(
                        sorted(
                            {
                                str(url)
                                for value in group[
                                    "evidence_urls_json"
                                ]
                                for url in loads(str(value or "[]"))
                                if str(url)
                            }
                        ),
                        ensure_ascii=False,
                    ),
                    "detail_urls_json": dumps(
                        sorted(
                            {
                                str(url)
                                for value in group[
                                    "detail_urls_json"
                                ]
                                for url in loads(str(value or "[]"))
                                if str(url)
                            }
                        ),
                        ensure_ascii=False,
                    ),
                    "scopes_json": dumps(
                        sorted(
                            {
                                str(scope)
                                for value in group["scopes_json"]
                                for scope in loads(str(value or "[]"))
                                if str(scope)
                            }
                        ),
                        ensure_ascii=False,
                    ),
                    "evidence_count": len(group),
                    "source_row_count": int(
                        group["source_row_count"].astype(int).sum()
                    ),
                    "verification_status": policy["relationships"][
                        "verification_status"
                    ],
                }
            )
    canonical_columns = [
        "canonical_relationship_id",
        "start_canonical_id",
        "end_canonical_id",
        "relation_type",
        "source_relationship_ids_json",
        "raw_relation_types_json",
        "source_datasets_json",
        "source_releases_json",
        "evidence_urls_json",
        "detail_urls_json",
        "scopes_json",
        "evidence_count",
        "source_row_count",
        "verification_status",
    ]
    exclusion_columns = [
        "source_relationship_id",
        "start_source_record_id",
        "end_source_record_id",
        "relation_type",
        "start_canonical_id",
        "end_canonical_id",
        "start_entity_type",
        "end_entity_type",
        "exclusion_reason",
    ]
    return {
        "canonical_entity_relationships": pd.DataFrame(
            canonical_rows,
            columns=canonical_columns,
        ),
        "canonical_projection_exclusions": pd.DataFrame(
            exclusion_rows,
            columns=exclusion_columns,
        ),
    }


def validate_source_relationship_tables(
    tables: dict[str, pd.DataFrame],
) -> list[str]:
    """적재 전 노드 ID와 관계 endpoint 참조 무결성을 검사한다."""
    errors: list[str] = []
    source_nodes = tables["source_record_nodes"]
    category_nodes = tables["thesaurus_category_nodes"]
    source_ids = set(source_nodes["source_record_id"])
    category_ids = set(category_nodes["category_id"])
    if source_nodes["source_record_id"].duplicated().any():
        errors.append("SourceRecord ID가 중복되었습니다.")
    if category_nodes["category_id"].duplicated().any():
        errors.append("ThesaurusCategory ID가 중복되었습니다.")

    source_relationships = tables["source_record_relationships"]
    missing_source_starts = set(
        source_relationships["start_source_record_id"]
    ).difference(source_ids)
    missing_source_ends = set(
        source_relationships["end_source_record_id"]
    ).difference(source_ids)
    if missing_source_starts:
        errors.append(
            f"원천 관계 시작 노드가 {len(missing_source_starts)}개 없습니다."
        )
    if missing_source_ends:
        errors.append(
            f"원천 관계 끝 노드가 {len(missing_source_ends)}개 없습니다."
        )

    memberships = tables["source_category_relationships"]
    missing_membership_sources = set(
        memberships["source_record_id"]
    ).difference(source_ids)
    missing_membership_categories = set(
        memberships["category_id"]
    ).difference(category_ids)
    if missing_membership_sources:
        errors.append(
            "분류 소속 관계의 SourceRecord가 "
            f"{len(missing_membership_sources)}개 없습니다."
        )
    if missing_membership_categories:
        errors.append(
            "분류 소속 관계의 Category가 "
            f"{len(missing_membership_categories)}개 없습니다."
        )

    hierarchy = tables["thesaurus_category_relationships"]
    missing_child_categories = set(
        hierarchy["child_category_id"]
    ).difference(category_ids)
    missing_parent_categories = set(
        hierarchy["parent_category_id"]
    ).difference(category_ids)
    if missing_child_categories:
        errors.append(
            f"하위 Category가 {len(missing_child_categories)}개 없습니다."
        )
    if missing_parent_categories:
        errors.append(
            f"상위 Category가 {len(missing_parent_categories)}개 없습니다."
        )
    return errors


def build_source_relationship_tables(
    people: pd.DataFrame,
    person_relations: pd.DataFrame,
    events: pd.DataFrame,
    event_relations: pd.DataFrame,
    thesaurus: pd.DataFrame,
    releases: dict[str, str],
    policy: dict,
    canonical_resolutions: pd.DataFrame | None = None,
    canonical_registry: pd.DataFrame | None = None,
) -> dict[str, pd.DataFrame]:
    """모든 원천 관계·분류·canonical 투영 테이블을 한 번에 만든다."""
    tables = {
        "source_record_nodes": build_source_nodes(
            people,
            events,
            thesaurus,
            releases,
            policy,
        ),
        "source_record_relationships": build_source_relationships(
            person_relations,
            event_relations,
            thesaurus,
            releases,
            policy,
        ),
    }
    tables.update(
        build_thesaurus_categories(
            thesaurus,
            releases,
            policy,
        )
    )
    tables.update(
        build_canonical_projection(
            tables["source_record_relationships"],
            canonical_resolutions,
            canonical_registry,
            policy,
        )
    )
    validation_errors = validate_source_relationship_tables(tables)
    if validation_errors:
        raise ValueError(" ".join(validation_errors))
    return tables
