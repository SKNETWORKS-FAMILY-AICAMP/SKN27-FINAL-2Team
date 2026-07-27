from __future__ import annotations

from collections import Counter, defaultdict
from json import JSONDecodeError, dumps, load, loads
from pathlib import Path
import re

import pandas as pd

from common import normalize_history_term
from fact_retrieval.build import create_stable_id, parse_json_list
from fact_retrieval.retrieve import (
    build_anchor_adjacency,
    build_relation_role_profiles,
    find_bounded_paths,
)


def load_source_layer_expansion_policy(
    eda_policy_path: str,
    retrieval_policy_path: str,
    resolution_policy_path: str,
) -> dict:
    """격리 EDA에 필요한 정책 세 파일을 읽는다."""
    with open(eda_policy_path, "r", encoding="utf-8") as input_file:
        eda_policy = load(input_file)
    with open(
        retrieval_policy_path,
        "r",
        encoding="utf-8",
    ) as input_file:
        retrieval_policy = load(input_file)
    with open(
        resolution_policy_path,
        "r",
        encoding="utf-8",
    ) as input_file:
        resolution_policy = load(input_file)
    return {
        "source_layer_expansion_eda": eda_policy,
        "fact_retrieval": retrieval_policy,
        "classification_anchors": resolution_policy[
            "entity_resolution"
        ]["classification_anchors"],
    }


def normalize_base_name(value: object, policy: dict) -> str:
    """한자 병기를 제거한 동명이인 비교용 이름을 만든다."""
    name_policy = policy["source_layer_expansion_eda"][
        "name_normalization"
    ]
    text = re.sub(
        str(name_policy["parenthetical_hanja_pattern"]),
        "",
        str(value or ""),
    )
    normalized = normalize_history_term(text)
    return re.sub(
        str(name_policy["non_name_character_pattern"]),
        "",
        normalized,
    )


def parse_json_object(value: object) -> dict:
    """CSV의 JSON 객체를 안전하게 읽는다."""
    text = str(value or "").strip()
    if not text:
        return {}
    try:
        parsed = loads(text)
    except (JSONDecodeError, TypeError):
        return {}
    if isinstance(parsed, dict):
        return parsed
    return {}


def value_is_present(value: object) -> bool:
    """메타데이터 값이 실제 식별 근거를 담고 있는지 확인한다."""
    if isinstance(value, list):
        return any(str(item).strip() for item in value)
    if isinstance(value, dict):
        return bool(value)
    return bool(str(value or "").strip())


def collect_source_signals(
    source_row: dict,
    policy: dict,
) -> tuple[list[str], list[str], list[str]]:
    """소스 레코드의 식별·시간 근거와 시대 분류를 수집한다."""
    eda_policy = policy["source_layer_expansion_eda"]
    metadata = parse_json_object(
        source_row.get("source_metadata_json", "")
    )
    identity_signals = [
        str(field)
        for field in eda_policy["identity_signal_fields"]
        if value_is_present(metadata.get(str(field)))
    ]
    temporal_values: list[str] = []
    for field in eda_policy["temporal_metadata_fields"]:
        value = metadata.get(str(field))
        if not value_is_present(value):
            continue
        if isinstance(value, list):
            temporal_values.extend(
                str(item).strip()
                for item in value
                if str(item).strip()
            )
            continue
        temporal_values.append(str(value).strip())
    temporal_text = " | ".join(temporal_values)
    era_ids = sorted(
        {
            str(rule["era_id"])
            for rule in policy["classification_anchors"]["era_rules"]
            if re.search(str(rule["pattern"]), temporal_text)
        }
    )
    return identity_signals, temporal_values, era_ids


def build_name_indexes(
    anchor_nodes: pd.DataFrame,
    canonical_registry: pd.DataFrame,
    policy: dict,
) -> tuple[
    dict[str, list[dict]],
    dict[str, list[dict]],
    dict[str, int],
]:
    """소스·canonical 이름 충돌을 판별할 인덱스를 만든다."""
    eda_policy = policy["source_layer_expansion_eda"]
    source_by_base_name: dict[str, list[dict]] = defaultdict(list)
    full_source_name_counts: Counter = Counter()
    for row in anchor_nodes.to_dict("records"):
        if (
            str(row["anchor_kind"])
            != str(eda_policy["candidate_anchor_kind"])
        ):
            continue
        if (
            str(row["resolution_status"])
            != str(eda_policy["candidate_resolution_status"])
        ):
            continue
        base_name = normalize_base_name(row["display_name"], policy)
        if not base_name:
            continue
        source_by_base_name[base_name].append(row)
        full_source_name_counts[
            normalize_history_term(str(row["display_name"]))
        ] += 1
    canonical_by_base_name: dict[str, list[dict]] = defaultdict(list)
    for row in canonical_registry.to_dict("records"):
        if (
            str(row["lifecycle_status"])
            != str(eda_policy["accepted_registry_status"])
        ):
            continue
        base_name = normalize_base_name(row["display_name"], policy)
        if base_name:
            canonical_by_base_name[base_name].append(row)
    return (
        source_by_base_name,
        canonical_by_base_name,
        dict(full_source_name_counts),
    )


def classify_candidate_status(
    known_true: bool,
    same_name_as_endpoint: bool,
    source_base_name_count: int,
    source_full_name_count: int,
    canonical_name_count: int,
    identity_signal_count: int,
    era_alignment_status: str,
) -> str:
    """후보를 적재 승인이 아닌 검색 안전성 단계로만 분류한다."""
    if known_true:
        return "BLOCKED_KNOWN_TRUE"
    if same_name_as_endpoint:
        return "BLOCKED_SAME_NAME_AS_ENDPOINT"
    if canonical_name_count > 0:
        return "BLOCKED_CANONICAL_NAME_COLLISION"
    if source_full_name_count > 1:
        return "BLOCKED_UNDISTINGUISHED_HOMONYM"
    if era_alignment_status == "CONFLICT":
        return "REVIEW_ERA_CONFLICT"
    if source_base_name_count > 1:
        return "REVIEW_HOMONYM"
    if identity_signal_count == 0:
        return "REVIEW_WEAK_IDENTITY"
    return "RETRIEVAL_CANDIDATE"


def build_collision_table(
    candidate_rows: list[dict],
    source_by_base_name: dict[str, list[dict]],
    canonical_by_base_name: dict[str, list[dict]],
) -> pd.DataFrame:
    """실제 후보로 등장한 이름의 충돌 그룹을 요약한다."""
    candidate_base_names = {
        str(row["candidate_base_name"]) for row in candidate_rows
    }
    output_rows: list[dict] = []
    for base_name in sorted(candidate_base_names):
        source_rows = source_by_base_name.get(base_name, [])
        canonical_rows = canonical_by_base_name.get(base_name, [])
        if len(source_rows) + len(canonical_rows) < 2:
            continue
        output_rows.append(
            {
                "base_name": base_name,
                "source_record_count": len(source_rows),
                "source_record_ids_json": dumps(
                    sorted(
                        str(row["source_record_id"])
                        for row in source_rows
                    ),
                    ensure_ascii=False,
                ),
                "source_display_names_json": dumps(
                    sorted(
                        {
                            str(row["display_name"])
                            for row in source_rows
                        }
                    ),
                    ensure_ascii=False,
                ),
                "canonical_count": len(canonical_rows),
                "canonical_ids_json": dumps(
                    sorted(
                        str(row["canonical_id"])
                        for row in canonical_rows
                    ),
                    ensure_ascii=False,
                ),
                "canonical_display_names_json": dumps(
                    sorted(
                        {
                            str(row["display_name"])
                            for row in canonical_rows
                        }
                    ),
                    ensure_ascii=False,
                ),
                "entity_types_json": dumps(
                    sorted(
                        {
                            str(row["entity_type"])
                            for row in [*source_rows, *canonical_rows]
                        }
                    ),
                    ensure_ascii=False,
                ),
            }
        )
    return pd.DataFrame(
        output_rows,
        columns=[
            "base_name",
            "source_record_count",
            "source_record_ids_json",
            "source_display_names_json",
            "canonical_count",
            "canonical_ids_json",
            "canonical_display_names_json",
            "entity_types_json",
        ],
    )


def build_source_layer_expansion_tables(
    canonical_registry: pd.DataFrame,
    canonical_facts: pd.DataFrame,
    anchor_nodes: pd.DataFrame,
    anchor_facts: pd.DataFrame,
    source_nodes: pd.DataFrame,
    exam_term_links: pd.DataFrame,
    existing_swap_candidates: pd.DataFrame,
    policy: dict,
) -> tuple[dict[str, pd.DataFrame], dict]:
    """미해결 공식 소스 endpoint를 검색 후보로만 확장해 안전성을 측정한다."""
    eda_policy = policy["source_layer_expansion_eda"]
    retrieval_policy = policy["fact_retrieval"]
    retrieval_rules = retrieval_policy["retrieval"]
    projection_rules = retrieval_policy["anchor_projection"]
    accepted_exam_ids = {
        str(row["canonical_id"])
        for row in exam_term_links.to_dict("records")
        if str(row["match_status"])
        == str(eda_policy["accepted_exam_match_status"])
    }
    canonical_anchor_by_id = {
        str(row["canonical_id"]): row
        for row in anchor_nodes.to_dict("records")
        if str(row["anchor_kind"])
        == str(eda_policy["canonical_anchor_kind"])
    }
    source_candidates = [
        row
        for row in anchor_nodes.to_dict("records")
        if str(row["anchor_kind"])
        == str(eda_policy["candidate_anchor_kind"])
        and str(row["resolution_status"])
        == str(eda_policy["candidate_resolution_status"])
    ]
    source_node_by_id = {
        str(row["source_record_id"]): row
        for row in source_nodes.to_dict("records")
    }
    (
        source_by_base_name,
        canonical_by_base_name,
        full_source_name_counts,
    ) = build_name_indexes(anchor_nodes, canonical_registry, policy)
    symmetric_relation_types = {
        str(value)
        for value in projection_rules["symmetric_relation_types"]
    }
    excluded_path_relation_types = {
        str(value)
        for value in retrieval_rules[
            "excluded_graph_path_relation_types"
        ]
    }
    excluded_fact_relation_types = {
        str(value)
        for value in retrieval_rules["excluded_fact_relation_types"]
    }
    adjacency = build_anchor_adjacency(
        anchor_facts,
        excluded_path_relation_types,
    )
    relation_roles = build_relation_role_profiles(
        anchor_facts,
        symmetric_relation_types,
    )
    asserted_fact_keys: set[tuple[str, str, str]] = set()
    for row in anchor_facts.to_dict("records"):
        fact_key = (
            str(row["start_anchor_id"]),
            str(row["relation_type"]),
            str(row["end_anchor_id"]),
        )
        asserted_fact_keys.add(fact_key)
        if str(row["relation_type"]) in symmetric_relation_types:
            asserted_fact_keys.add(
                (fact_key[2], fact_key[1], fact_key[0])
            )
    maximum_hops = int(retrieval_rules["maximum_graph_hops"])
    maximum_fallback_edges = int(
        retrieval_rules["maximum_fallback_graph_edges"]
    )
    primary_search_status = str(
        projection_rules["canonical_search_status"]
    )
    path_cache: dict[str, dict[str, list[str]]] = {}
    output_rows: list[dict] = []
    eligible_correct_fact_ids: set[str] = set()
    for fact in canonical_facts.to_dict("records"):
        relation_type = str(fact["relation_type"])
        if relation_type in excluded_fact_relation_types:
            continue
        start_canonical_id = str(fact["start_canonical_id"])
        end_canonical_id = str(fact["end_canonical_id"])
        if (
            start_canonical_id not in accepted_exam_ids
            or end_canonical_id not in accepted_exam_ids
        ):
            continue
        start_anchor = canonical_anchor_by_id.get(start_canonical_id)
        end_anchor = canonical_anchor_by_id.get(end_canonical_id)
        if start_anchor is None or end_anchor is None:
            continue
        correct_fact_id = str(fact["canonical_relationship_id"])
        eligible_correct_fact_ids.add(correct_fact_id)
        for swap_dimension in ["START", "END"]:
            original_anchor = start_anchor
            fixed_anchor = end_anchor
            required_role = f"OUT:{relation_type}"
            if swap_dimension == "END":
                original_anchor = end_anchor
                fixed_anchor = start_anchor
                required_role = f"IN:{relation_type}"
            original_anchor_id = str(original_anchor["anchor_id"])
            if original_anchor_id not in path_cache:
                path_cache[original_anchor_id] = find_bounded_paths(
                    original_anchor_id,
                    adjacency,
                    maximum_hops,
                    primary_search_status,
                )
            paths = path_cache[original_anchor_id]
            original_eras = set(
                parse_json_list(original_anchor["era_ids_json"])
            )
            original_base_name = normalize_base_name(
                original_anchor["display_name"],
                policy,
            )
            fixed_base_name = normalize_base_name(
                fixed_anchor["display_name"],
                policy,
            )
            for candidate in source_candidates:
                if (
                    str(candidate["entity_type"])
                    != str(original_anchor["entity_type"])
                ):
                    continue
                candidate_anchor_id = str(candidate["anchor_id"])
                if required_role not in relation_roles.get(
                    candidate_anchor_id,
                    set(),
                ):
                    continue
                path = paths.get(candidate_anchor_id, [])
                if len(path) < 2:
                    continue
                graph_distance = len(path) - 1
                path_relation_types: list[list[str]] = []
                path_search_statuses: list[list[str]] = []
                fallback_edge_count = 0
                for left_anchor_id, right_anchor_id in zip(
                    path,
                    path[1:],
                ):
                    edge = adjacency[left_anchor_id][right_anchor_id]
                    relation_types = sorted(edge["relation_types"])
                    search_statuses = sorted(edge["search_statuses"])
                    path_relation_types.append(relation_types)
                    path_search_statuses.append(search_statuses)
                    if primary_search_status not in edge[
                        "search_statuses"
                    ]:
                        fallback_edge_count += 1
                if fallback_edge_count > maximum_fallback_edges:
                    continue
                candidate_source_id = str(
                    candidate["source_record_id"]
                )
                source_row = source_node_by_id.get(
                    candidate_source_id,
                    {},
                )
                (
                    identity_signals,
                    temporal_values,
                    candidate_eras,
                ) = collect_source_signals(source_row, policy)
                candidate_era_set = set(candidate_eras)
                era_alignment_status = "UNKNOWN"
                if original_eras and candidate_era_set:
                    era_alignment_status = "CONFLICT"
                    if original_eras.intersection(candidate_era_set):
                        era_alignment_status = "OVERLAP"
                elif original_eras:
                    era_alignment_status = "MISSING_CANDIDATE_ERA"
                elif candidate_era_set:
                    era_alignment_status = "MISSING_ORIGINAL_ERA"
                candidate_base_name = normalize_base_name(
                    candidate["display_name"],
                    policy,
                )
                source_base_rows = source_by_base_name.get(
                    candidate_base_name,
                    [],
                )
                canonical_base_rows = canonical_by_base_name.get(
                    candidate_base_name,
                    [],
                )
                full_name = normalize_history_term(
                    str(candidate["display_name"])
                )
                same_name_as_endpoint = candidate_base_name in {
                    original_base_name,
                    fixed_base_name,
                }
                proposed_fact_key = (
                    candidate_anchor_id,
                    relation_type,
                    str(fixed_anchor["anchor_id"]),
                )
                if swap_dimension == "END":
                    proposed_fact_key = (
                        str(fixed_anchor["anchor_id"]),
                        relation_type,
                        candidate_anchor_id,
                    )
                known_true = proposed_fact_key in asserted_fact_keys
                status = classify_candidate_status(
                    known_true,
                    same_name_as_endpoint,
                    len(source_base_rows),
                    int(full_source_name_counts.get(full_name, 0)),
                    len(canonical_base_rows),
                    len(identity_signals),
                    era_alignment_status,
                )
                candidate_id = create_stable_id(
                    "SLE-",
                    [
                        correct_fact_id,
                        swap_dimension,
                        candidate_source_id,
                        str(eda_policy["policy_version"]),
                    ],
                    retrieval_policy,
                )
                output_rows.append(
                    {
                        "source_layer_candidate_id": candidate_id,
                        "correct_canonical_relationship_id": (
                            correct_fact_id
                        ),
                        "swap_dimension": swap_dimension,
                        "relation_type": relation_type,
                        "original_canonical_id": str(
                            original_anchor["canonical_id"]
                        ),
                        "original_display_name": str(
                            original_anchor["display_name"]
                        ),
                        "original_entity_type": str(
                            original_anchor["entity_type"]
                        ),
                        "original_era_ids_json": dumps(
                            sorted(original_eras),
                            ensure_ascii=False,
                        ),
                        "fixed_canonical_id": str(
                            fixed_anchor["canonical_id"]
                        ),
                        "fixed_display_name": str(
                            fixed_anchor["display_name"]
                        ),
                        "candidate_anchor_id": candidate_anchor_id,
                        "candidate_source_record_id": (
                            candidate_source_id
                        ),
                        "candidate_display_name": str(
                            candidate["display_name"]
                        ),
                        "candidate_base_name": candidate_base_name,
                        "candidate_entity_type": str(
                            candidate["entity_type"]
                        ),
                        "candidate_source": str(candidate["source"]),
                        "candidate_source_urls_json": str(
                            candidate["source_urls_json"]
                        ),
                        "identity_signals_json": dumps(
                            identity_signals,
                            ensure_ascii=False,
                        ),
                        "identity_signal_count": len(identity_signals),
                        "temporal_values_json": dumps(
                            temporal_values,
                            ensure_ascii=False,
                        ),
                        "candidate_era_ids_json": dumps(
                            candidate_eras,
                            ensure_ascii=False,
                        ),
                        "era_alignment_status": (
                            era_alignment_status
                        ),
                        "source_base_name_count": len(
                            source_base_rows
                        ),
                        "source_full_name_count": int(
                            full_source_name_counts.get(full_name, 0)
                        ),
                        "canonical_name_count": len(
                            canonical_base_rows
                        ),
                        "same_name_as_endpoint": (
                            same_name_as_endpoint
                        ),
                        "known_true_in_source_graph": known_true,
                        "graph_distance": graph_distance,
                        "fallback_graph_edge_count": (
                            fallback_edge_count
                        ),
                        "graph_path_anchor_ids_json": dumps(
                            path,
                            ensure_ascii=False,
                        ),
                        "graph_path_relation_types_json": dumps(
                            path_relation_types,
                            ensure_ascii=False,
                        ),
                        "graph_path_search_statuses_json": dumps(
                            path_search_statuses,
                            ensure_ascii=False,
                        ),
                        "retrieval_safety_status": status,
                        "requires_truth_verification": (
                            not known_true
                        ),
                        "llm_used": False,
                        "neo4j_load": False,
                        "policy_version": str(
                            eda_policy["policy_version"]
                        ),
                    }
                )
    candidate_columns = [
        "source_layer_candidate_id",
        "correct_canonical_relationship_id",
        "swap_dimension",
        "relation_type",
        "original_canonical_id",
        "original_display_name",
        "original_entity_type",
        "original_era_ids_json",
        "fixed_canonical_id",
        "fixed_display_name",
        "candidate_anchor_id",
        "candidate_source_record_id",
        "candidate_display_name",
        "candidate_base_name",
        "candidate_entity_type",
        "candidate_source",
        "candidate_source_urls_json",
        "identity_signals_json",
        "identity_signal_count",
        "temporal_values_json",
        "candidate_era_ids_json",
        "era_alignment_status",
        "source_base_name_count",
        "source_full_name_count",
        "canonical_name_count",
        "same_name_as_endpoint",
        "known_true_in_source_graph",
        "graph_distance",
        "fallback_graph_edge_count",
        "graph_path_anchor_ids_json",
        "graph_path_relation_types_json",
        "graph_path_search_statuses_json",
        "retrieval_safety_status",
        "requires_truth_verification",
        "llm_used",
        "neo4j_load",
        "policy_version",
    ]
    candidates = pd.DataFrame(
        output_rows,
        columns=candidate_columns,
    )
    if not candidates.empty:
        candidates = candidates.sort_values(
            [
                "correct_canonical_relationship_id",
                "swap_dimension",
                "retrieval_safety_status",
                "graph_distance",
                "fallback_graph_edge_count",
                "source_layer_candidate_id",
            ]
        ).reset_index(drop=True)
    collision_groups = build_collision_table(
        output_rows,
        source_by_base_name,
        canonical_by_base_name,
    )
    audit_sample = candidates.copy()
    maximum_audit_rows = int(eda_policy["audit"]["maximum_rows"])
    if len(audit_sample) > maximum_audit_rows:
        status_count = max(
            1,
            audit_sample["retrieval_safety_status"].nunique(),
        )
        rows_per_status = max(1, maximum_audit_rows // status_count)
        audit_sample = (
            audit_sample.groupby(
                "retrieval_safety_status",
                group_keys=False,
            )
            .head(rows_per_status)
            .head(maximum_audit_rows)
            .reset_index(drop=True)
        )
    safe_statuses = {
        "RETRIEVAL_CANDIDATE",
        "REVIEW_HOMONYM",
        "REVIEW_WEAK_IDENTITY",
        "REVIEW_ERA_CONFLICT",
    }
    usable_mask = candidates["retrieval_safety_status"].isin(
        safe_statuses
    )
    usable_candidates = candidates[usable_mask]
    existing_correct_fact_ids = {
        str(value)
        for value in existing_swap_candidates.get(
            "correct_canonical_relationship_id",
            pd.Series(dtype=str),
        )
        if str(value)
    }
    source_usable_correct_fact_ids = {
        str(value)
        for value in usable_candidates[
            "correct_canonical_relationship_id"
        ]
    }
    new_correct_fact_ids = (
        source_usable_correct_fact_ids.difference(
            existing_correct_fact_ids
        )
    )
    combined_correct_fact_ids = existing_correct_fact_ids.union(
        source_usable_correct_fact_ids
    )
    unique_unverified_proposals = usable_candidates[
        [
            "swap_dimension",
            "candidate_source_record_id",
            "relation_type",
            "fixed_canonical_id",
        ]
    ].drop_duplicates()
    status_counts = dict(
        Counter(
            str(value)
            for value in candidates["retrieval_safety_status"]
        )
    )
    statistics = {
        "canonical_registry_count": len(canonical_registry),
        "accepted_exam_canonical_count": len(accepted_exam_ids),
        "canonical_fact_count": len(canonical_facts),
        "eligible_correct_fact_count": len(eligible_correct_fact_ids),
        "official_source_anchor_count": len(source_candidates),
        "source_layer_candidate_row_count": len(candidates),
        "candidate_correct_fact_count": int(
            candidates[
                "correct_canonical_relationship_id"
            ].nunique()
        ),
        "candidate_correct_fact_coverage": (
            float(
                candidates[
                    "correct_canonical_relationship_id"
                ].nunique()
            )
            / len(eligible_correct_fact_ids)
            if eligible_correct_fact_ids
            else 0.0
        ),
        "unique_candidate_source_record_count": int(
            candidates["candidate_source_record_id"].nunique()
        ),
        "existing_swap_candidate_row_count": len(
            existing_swap_candidates
        ),
        "existing_candidate_correct_fact_count": len(
            existing_correct_fact_ids
        ),
        "retrieval_safety_status_counts": status_counts,
        "usable_retrieval_candidate_row_count": int(
            usable_mask.sum()
        ),
        "source_usable_correct_fact_count": len(
            source_usable_correct_fact_ids
        ),
        "usable_unique_candidate_source_record_count": int(
            usable_candidates[
                "candidate_source_record_id"
            ].nunique()
        ),
        "overlapping_correct_fact_count": len(
            source_usable_correct_fact_ids.intersection(
                existing_correct_fact_ids
            )
        ),
        "new_correct_fact_count": len(new_correct_fact_ids),
        "combined_candidate_correct_fact_count": len(
            combined_correct_fact_ids
        ),
        "correct_fact_coverage_gain": (
            float(len(new_correct_fact_ids))
            / len(eligible_correct_fact_ids)
            if eligible_correct_fact_ids
            else 0.0
        ),
        "truth_verification_required_row_count": int(
            candidates["requires_truth_verification"].eq(True).sum()
        ),
        "unique_unverified_proposed_fact_count": len(
            unique_unverified_proposals
        ),
        "known_true_blocked_row_count": int(
            candidates["known_true_in_source_graph"].eq(True).sum()
        ),
        "candidate_with_identity_signal_row_count": int(
            candidates["identity_signal_count"].gt(0).sum()
        ),
        "candidate_with_temporal_metadata_row_count": int(
            candidates["temporal_values_json"].ne("[]").sum()
        ),
        "era_alignment_status_counts": dict(
            Counter(
                str(value)
                for value in candidates["era_alignment_status"]
            )
        ),
        "graph_distance_counts": dict(
            Counter(
                str(value) for value in candidates["graph_distance"]
            )
        ),
        "name_collision_group_count": len(collision_groups),
        "audit_sample_count": len(audit_sample),
        "auto_promoted_canonical_fact_count": 0,
        "llm_used": False,
        "neo4j_load": False,
    }
    return {
        "candidates": candidates,
        "collision_groups": collision_groups,
        "audit_sample": audit_sample,
    }, statistics
