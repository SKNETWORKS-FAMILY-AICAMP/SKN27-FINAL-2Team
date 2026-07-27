from __future__ import annotations

from collections import defaultdict
from hashlib import new as new_hash
from json import JSONDecodeError, dumps, loads

import pandas as pd

from common import normalize_history_term


def create_stable_id(
    prefix: str,
    parts: list[str],
    policy: dict,
) -> str:
    """검색 그래프용 재실행 가능한 ID를 만든다."""
    identifier_policy = policy["identifier"]
    hasher = new_hash(str(identifier_policy["hash_algorithm"]))
    hasher.update("\u241f".join(parts).encode("utf-8"))
    digest_length = int(identifier_policy["digest_length"])
    return f"{prefix}{hasher.hexdigest()[:digest_length]}"


def parse_json_list(value: object) -> list[str]:
    """CSV의 JSON 배열을 문자열 목록으로 읽는다."""
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


def build_entity_anchor_tables(
    canonical_registry: pd.DataFrame,
    canonical_facts: pd.DataFrame,
    source_nodes: pd.DataFrame,
    source_relationships: pd.DataFrame,
    source_resolutions: pd.DataFrame,
    canonical_topics: pd.DataFrame,
    canonical_eras: pd.DataFrame,
    policy: dict,
) -> dict[str, pd.DataFrame]:
    """Canonical 사실과 공식 1-hop 관계를 검색용 Anchor 그래프로 투영한다."""
    projection_policy = policy["anchor_projection"]
    identifier_policy = policy["identifier"]
    accepted_status = str(
        projection_policy["accepted_match_status"]
    )
    accepted_resolutions = source_resolutions[
        source_resolutions["match_status"].eq(accepted_status)
    ].copy()
    duplicate_resolution_counts = accepted_resolutions.groupby(
        "source_record_id"
    )["canonical_id"].nunique()
    ambiguous_source_ids = set(
        duplicate_resolution_counts[
            duplicate_resolution_counts.gt(1)
        ].index
    )
    if ambiguous_source_ids:
        raise ValueError(
            "하나의 SourceRecord가 여러 CanonicalEntity에 승인됐습니다: "
            f"{len(ambiguous_source_ids)}건"
        )
    canonical_by_source_id = {
        str(row["source_record_id"]): str(row["canonical_id"])
        for row in accepted_resolutions.to_dict("records")
    }
    registry_by_id = {
        str(row["canonical_id"]): row
        for row in canonical_registry.to_dict("records")
    }
    registry_member_conflicts: set[str] = set()
    for canonical_id, registry_row in registry_by_id.items():
        member_source_ids = parse_json_list(
            registry_row.get(
                "identity_member_source_ids_json",
                "[]",
            )
        )
        for source_record_id in member_source_ids:
            existing_canonical_id = canonical_by_source_id.get(
                source_record_id
            )
            if (
                existing_canonical_id
                and existing_canonical_id != canonical_id
            ):
                registry_member_conflicts.add(source_record_id)
                continue
            canonical_by_source_id[source_record_id] = canonical_id
    if registry_member_conflicts:
        raise ValueError(
            "Canonical registry identity member가 여러 엔티티에 "
            f"중복됐습니다: {len(registry_member_conflicts)}건"
        )
    source_by_id = {
        str(row["source_record_id"]): row
        for row in source_nodes.to_dict("records")
    }
    unknown_canonical_ids = set(
        canonical_by_source_id.values()
    ).difference(registry_by_id)
    if unknown_canonical_ids:
        raise ValueError(
            "SourceRecord 해소 결과가 registry에 없는 CanonicalEntity를 "
            f"참조합니다: {len(unknown_canonical_ids)}건"
        )

    topic_ids_by_canonical: dict[str, set[str]] = defaultdict(set)
    for row in canonical_topics.to_dict("records"):
        topic_ids_by_canonical[str(row["canonical_id"])].add(
            str(row["topic_id"])
        )
    era_ids_by_canonical: dict[str, set[str]] = defaultdict(set)
    for row in canonical_eras.to_dict("records"):
        era_ids_by_canonical[str(row["canonical_id"])].add(
            str(row["era_id"])
        )

    canonical_anchor_id_by_canonical: dict[str, str] = {}
    source_anchor_id_by_source: dict[str, str] = {}
    anchor_rows: dict[str, dict] = {}
    canonical_link_rows: dict[str, dict] = {}
    source_link_rows: dict[str, dict] = {}

    def ensure_canonical_anchor(canonical_id: str) -> str:
        existing_anchor_id = canonical_anchor_id_by_canonical.get(
            canonical_id
        )
        if existing_anchor_id:
            return existing_anchor_id
        registry_row = registry_by_id.get(canonical_id)
        if registry_row is None:
            raise ValueError(
                f"Anchor 대상 CanonicalEntity가 없습니다: {canonical_id}"
            )
        anchor_id = create_stable_id(
            str(identifier_policy["anchor_prefix"]),
            [
                "CANONICAL",
                canonical_id,
                str(policy["policy_version"]),
            ],
            policy,
        )
        display_name = str(registry_row.get("display_name") or "")
        anchor_rows[anchor_id] = {
            "anchor_id": anchor_id,
            "anchor_kind": str(
                projection_policy["canonical_anchor_kind"]
            ),
            "canonical_id": canonical_id,
            "source_record_id": "",
            "display_name": display_name,
            "normalized_name": normalize_history_term(display_name),
            "entity_type": str(
                registry_row.get("entity_type") or ""
            ),
            "resolution_status": str(
                projection_policy["resolved_status"]
            ),
            "source": "",
            "source_urls_json": "[]",
            "topic_ids_json": dumps(
                sorted(topic_ids_by_canonical.get(canonical_id, set())),
                ensure_ascii=False,
            ),
            "era_ids_json": dumps(
                sorted(era_ids_by_canonical.get(canonical_id, set())),
                ensure_ascii=False,
            ),
            "policy_version": str(policy["policy_version"]),
        }
        link_id = create_stable_id(
            str(identifier_policy["canonical_link_prefix"]),
            [canonical_id, anchor_id, str(policy["policy_version"])],
            policy,
        )
        canonical_link_rows[link_id] = {
            "canonical_anchor_link_id": link_id,
            "canonical_id": canonical_id,
            "anchor_id": anchor_id,
            "verification_status": "VERIFIED",
            "policy_version": str(policy["policy_version"]),
        }
        canonical_anchor_id_by_canonical[canonical_id] = anchor_id
        return anchor_id

    def ensure_source_anchor(source_record_id: str) -> str:
        existing_anchor_id = source_anchor_id_by_source.get(
            source_record_id
        )
        if existing_anchor_id:
            return existing_anchor_id
        source_row = source_by_id.get(source_record_id)
        if source_row is None:
            raise ValueError(
                f"Anchor 대상 SourceRecord가 없습니다: {source_record_id}"
            )
        anchor_id = create_stable_id(
            str(identifier_policy["anchor_prefix"]),
            [
                "SOURCE_RECORD",
                source_record_id,
                str(policy["policy_version"]),
            ],
            policy,
        )
        display_name = str(source_row.get("display_name") or "")
        record_type = str(source_row.get("record_type") or "")
        entity_type = str(
            projection_policy["source_record_type_to_entity_type"].get(
                record_type,
                "Concept",
            )
        )
        anchor_rows[anchor_id] = {
            "anchor_id": anchor_id,
            "anchor_kind": str(
                projection_policy["source_anchor_kind"]
            ),
            "canonical_id": "",
            "source_record_id": source_record_id,
            "display_name": display_name,
            "normalized_name": normalize_history_term(display_name),
            "entity_type": entity_type,
            "resolution_status": str(
                projection_policy["unresolved_status"]
            ),
            "source": str(source_row.get("source") or ""),
            "source_urls_json": str(
                source_row.get("source_urls_json") or "[]"
            ),
            "topic_ids_json": "[]",
            "era_ids_json": "[]",
            "policy_version": str(policy["policy_version"]),
        }
        source_anchor_id_by_source[source_record_id] = anchor_id
        return anchor_id

    fact_aggregates: dict[tuple[str, str, str], dict] = {}

    def add_anchor_fact(
        start_anchor_id: str,
        relation_type: str,
        end_anchor_id: str,
        origin_scope: str,
        source_relationship_ids: list[str],
        canonical_relationship_ids: list[str],
        evidence_urls: list[str],
        detail_urls: list[str],
        evidence_sentences: list[str],
        source_datasets: list[str],
        verification_status: str,
        resolution_scope: str,
    ) -> None:
        symmetric_types = {
            str(value)
            for value in projection_policy["symmetric_relation_types"]
        }
        effective_start_id = start_anchor_id
        effective_end_id = end_anchor_id
        if (
            relation_type in symmetric_types
            and end_anchor_id < start_anchor_id
        ):
            effective_start_id = end_anchor_id
            effective_end_id = start_anchor_id
        key = (
            effective_start_id,
            relation_type,
            effective_end_id,
        )
        if key not in fact_aggregates:
            fact_aggregates[key] = {
                "origin_scopes": set(),
                "source_relationship_ids": set(),
                "canonical_relationship_ids": set(),
                "evidence_urls": set(),
                "detail_urls": set(),
                "evidence_sentences": set(),
                "source_datasets": set(),
                "verification_statuses": set(),
                "resolution_scopes": set(),
            }
        aggregate = fact_aggregates[key]
        aggregate["origin_scopes"].add(origin_scope)
        aggregate["source_relationship_ids"].update(
            source_relationship_ids
        )
        aggregate["canonical_relationship_ids"].update(
            canonical_relationship_ids
        )
        aggregate["evidence_urls"].update(evidence_urls)
        aggregate["detail_urls"].update(detail_urls)
        aggregate["evidence_sentences"].update(evidence_sentences)
        aggregate["source_datasets"].update(source_datasets)
        aggregate["verification_statuses"].add(verification_status)
        aggregate["resolution_scopes"].add(resolution_scope)

    for row in canonical_facts.to_dict("records"):
        start_canonical_id = str(row["start_canonical_id"])
        end_canonical_id = str(row["end_canonical_id"])
        start_anchor_id = ensure_canonical_anchor(start_canonical_id)
        end_anchor_id = ensure_canonical_anchor(end_canonical_id)
        add_anchor_fact(
            start_anchor_id,
            str(row["relation_type"]),
            end_anchor_id,
            str(projection_policy["canonical_fact_scope"]),
            parse_json_list(row.get("source_relationship_ids_json", "")),
            [str(row["canonical_relationship_id"])],
            parse_json_list(row.get("evidence_urls_json", "")),
            parse_json_list(row.get("detail_urls_json", "")),
            parse_json_list(row.get("evidence_sentences_json", "")),
            parse_json_list(row.get("source_datasets_json", "")),
            str(row["verification_status"]),
            "BOTH_CANONICAL",
        )

    included_source_types = {
        str(value)
        for value in projection_policy[
            "included_source_relation_types"
        ]
    }
    for row in source_relationships.to_dict("records"):
        relation_type = str(row["relation_type"])
        if relation_type not in included_source_types:
            continue
        start_source_id = str(row["start_source_record_id"])
        end_source_id = str(row["end_source_record_id"])
        start_canonical_id = canonical_by_source_id.get(start_source_id)
        end_canonical_id = canonical_by_source_id.get(end_source_id)
        resolved_count = int(bool(start_canonical_id)) + int(
            bool(end_canonical_id)
        )
        if resolved_count != 1:
            continue
        start_anchor_id = ""
        if start_canonical_id:
            start_anchor_id = ensure_canonical_anchor(
                start_canonical_id
            )
        elif not start_canonical_id:
            start_anchor_id = ensure_source_anchor(start_source_id)
        end_anchor_id = ""
        if end_canonical_id:
            end_anchor_id = ensure_canonical_anchor(end_canonical_id)
        elif not end_canonical_id:
            end_anchor_id = ensure_source_anchor(end_source_id)
        add_anchor_fact(
            start_anchor_id,
            relation_type,
            end_anchor_id,
            str(projection_policy["one_hop_scope"]),
            [str(row["source_relationship_id"])],
            [],
            parse_json_list(row.get("evidence_urls_json", "")),
            parse_json_list(row.get("detail_urls_json", "")),
            [],
            [str(row.get("source_dataset") or "")],
            str(row["verification_status"]),
            "ONE_CANONICAL",
        )

    used_source_ids = set(source_anchor_id_by_source)
    used_source_ids.update(
        source_id
        for source_id in canonical_by_source_id
        if canonical_by_source_id[source_id]
        in canonical_anchor_id_by_canonical
    )
    for source_record_id in sorted(used_source_ids):
        canonical_id = canonical_by_source_id.get(source_record_id)
        anchor_id = source_anchor_id_by_source.get(source_record_id)
        link_status = "UNRESOLVED"
        if canonical_id:
            anchor_id = ensure_canonical_anchor(canonical_id)
            link_status = "RESOLVED_TO_CANONICAL"
        if not anchor_id:
            raise ValueError(
                f"SourceRecord Anchor 연결을 만들 수 없습니다: {source_record_id}"
            )
        link_id = create_stable_id(
            str(identifier_policy["source_link_prefix"]),
            [
                source_record_id,
                anchor_id,
                str(policy["policy_version"]),
            ],
            policy,
        )
        source_link_rows[link_id] = {
            "source_anchor_link_id": link_id,
            "source_record_id": source_record_id,
            "anchor_id": anchor_id,
            "link_status": link_status,
            "policy_version": str(policy["policy_version"]),
        }

    anchor_fact_rows: list[dict] = []
    for key, aggregate in fact_aggregates.items():
        start_anchor_id, relation_type, end_anchor_id = key
        anchor_fact_id = create_stable_id(
            str(identifier_policy["anchor_fact_prefix"]),
            [
                start_anchor_id,
                relation_type,
                end_anchor_id,
                str(policy["policy_version"]),
            ],
            policy,
        )
        verification_status = "PATTERN_ASSERTED"
        if "SOURCE_ASSERTED" in aggregate["verification_statuses"]:
            verification_status = "SOURCE_ASSERTED"
        search_status = str(
            projection_policy["source_neighbor_search_status"]
        )
        if "BOTH_CANONICAL" in aggregate["resolution_scopes"]:
            search_status = str(
                projection_policy["canonical_search_status"]
            )
        anchor_fact_rows.append(
            {
                "anchor_fact_id": anchor_fact_id,
                "start_anchor_id": start_anchor_id,
                "end_anchor_id": end_anchor_id,
                "relation_type": relation_type,
                "origin_scopes_json": dumps(
                    sorted(aggregate["origin_scopes"]),
                    ensure_ascii=False,
                ),
                "source_relationship_ids_json": dumps(
                    sorted(aggregate["source_relationship_ids"]),
                    ensure_ascii=False,
                ),
                "canonical_relationship_ids_json": dumps(
                    sorted(aggregate["canonical_relationship_ids"]),
                    ensure_ascii=False,
                ),
                "evidence_urls_json": dumps(
                    sorted(aggregate["evidence_urls"]),
                    ensure_ascii=False,
                ),
                "detail_urls_json": dumps(
                    sorted(aggregate["detail_urls"]),
                    ensure_ascii=False,
                ),
                "evidence_sentences_json": dumps(
                    sorted(aggregate["evidence_sentences"]),
                    ensure_ascii=False,
                ),
                "source_datasets_json": dumps(
                    sorted(aggregate["source_datasets"]),
                    ensure_ascii=False,
                ),
                "verification_statuses_json": dumps(
                    sorted(aggregate["verification_statuses"]),
                    ensure_ascii=False,
                ),
                "resolution_scopes_json": dumps(
                    sorted(aggregate["resolution_scopes"]),
                    ensure_ascii=False,
                ),
                "verification_status": verification_status,
                "search_status": search_status,
                "policy_version": str(policy["policy_version"]),
            }
        )

    anchors = pd.DataFrame(list(anchor_rows.values()))
    canonical_links = pd.DataFrame(
        list(canonical_link_rows.values())
    )
    source_links = pd.DataFrame(list(source_link_rows.values()))
    anchor_facts = pd.DataFrame(anchor_fact_rows)
    if anchors["anchor_id"].duplicated().any():
        raise ValueError("EntityAnchor ID가 중복됐습니다.")
    if anchor_facts["anchor_fact_id"].duplicated().any():
        raise ValueError("Anchor 사실 관계 ID가 중복됐습니다.")
    valid_anchor_ids = set(anchors["anchor_id"])
    fact_endpoint_ids = set(
        anchor_facts["start_anchor_id"]
    ).union(anchor_facts["end_anchor_id"])
    missing_anchor_ids = fact_endpoint_ids.difference(valid_anchor_ids)
    if missing_anchor_ids:
        raise ValueError(
            f"Anchor 사실 관계 endpoint가 없습니다: {len(missing_anchor_ids)}건"
        )
    return {
        "anchor_nodes": anchors.sort_values("anchor_id").reset_index(
            drop=True
        ),
        "canonical_anchor_links": canonical_links.sort_values(
            "canonical_anchor_link_id"
        ).reset_index(drop=True),
        "source_anchor_links": source_links.sort_values(
            "source_anchor_link_id"
        ).reset_index(drop=True),
        "anchor_fact_relationships": anchor_facts.sort_values(
            "anchor_fact_id"
        ).reset_index(drop=True),
    }
