from __future__ import annotations

from collections import defaultdict
from heapq import heappop, heappush
from json import dumps

import pandas as pd

from fact_retrieval.build import create_stable_id, parse_json_list


def build_anchor_adjacency(
    anchor_facts: pd.DataFrame,
    excluded_relation_types: set[str],
) -> dict[str, dict[str, dict[str, set[str]]]]:
    """허용된 사실 관계와 상태를 보존한 무방향 인접 목록을 만든다."""
    adjacency: dict[
        str,
        dict[str, dict[str, set[str]]],
    ] = defaultdict(dict)
    for row in anchor_facts.to_dict("records"):
        relation_type = str(row["relation_type"])
        if relation_type in excluded_relation_types:
            continue
        start_anchor_id = str(row["start_anchor_id"])
        end_anchor_id = str(row["end_anchor_id"])
        search_status = str(row["search_status"])
        for left_anchor_id, right_anchor_id in [
            (start_anchor_id, end_anchor_id),
            (end_anchor_id, start_anchor_id),
        ]:
            edge = adjacency[left_anchor_id].setdefault(
                right_anchor_id,
                {
                    "relation_types": set(),
                    "search_statuses": set(),
                },
            )
            edge["relation_types"].add(relation_type)
            edge["search_statuses"].add(search_status)
    return adjacency


def find_bounded_paths(
    start_anchor_id: str,
    adjacency: dict[str, dict[str, dict[str, set[str]]]],
    maximum_hops: int,
    primary_search_status: str,
) -> dict[str, list[str]]:
    """최단 홉을 우선하고 같은 홉에서는 FALLBACK이 적은 경로를 찾는다."""
    paths = {start_anchor_id: [start_anchor_id]}
    best_costs = {start_anchor_id: (0, 0)}
    queue: list[tuple[int, int, tuple[str, ...], str]] = [
        (0, 0, (start_anchor_id,), start_anchor_id)
    ]
    while queue:
        (
            hop_count,
            fallback_edge_count,
            path_tuple,
            current_anchor_id,
        ) = heappop(queue)
        if best_costs.get(current_anchor_id) != (
            hop_count,
            fallback_edge_count,
        ):
            continue
        if hop_count >= maximum_hops:
            continue
        for neighbor_id in sorted(
            adjacency.get(current_anchor_id, {})
        ):
            edge = adjacency[current_anchor_id][neighbor_id]
            edge_is_fallback = (
                primary_search_status
                not in edge["search_statuses"]
            )
            neighbor_cost = (
                hop_count + 1,
                fallback_edge_count + int(edge_is_fallback),
            )
            existing_cost = best_costs.get(neighbor_id)
            if (
                existing_cost is not None
                and existing_cost <= neighbor_cost
            ):
                continue
            neighbor_path = (*path_tuple, neighbor_id)
            best_costs[neighbor_id] = neighbor_cost
            paths[neighbor_id] = list(neighbor_path)
            heappush(
                queue,
                (
                    neighbor_cost[0],
                    neighbor_cost[1],
                    neighbor_path,
                    neighbor_id,
                ),
            )
    return paths


def build_relation_role_profiles(
    anchor_facts: pd.DataFrame,
    symmetric_relation_types: set[str],
) -> dict[str, set[str]]:
    """각 Anchor가 사실 관계에서 맡는 방향별 역할을 계산한다."""
    roles: dict[str, set[str]] = defaultdict(set)
    for row in anchor_facts.to_dict("records"):
        relation_type = str(row["relation_type"])
        start_anchor_id = str(row["start_anchor_id"])
        end_anchor_id = str(row["end_anchor_id"])
        roles[start_anchor_id].add(f"OUT:{relation_type}")
        roles[end_anchor_id].add(f"IN:{relation_type}")
        if relation_type in symmetric_relation_types:
            roles[start_anchor_id].add(f"IN:{relation_type}")
            roles[end_anchor_id].add(f"OUT:{relation_type}")
    return roles


def build_swap_candidates(
    canonical_facts: pd.DataFrame,
    anchor_nodes: pd.DataFrame,
    anchor_facts: pd.DataFrame,
    policy: dict,
    exam_canonical_ids: set[str] | None = None,
) -> pd.DataFrame:
    """정답 사실 endpoint와 구조적으로 유사한 교체 후보를 검색한다."""
    retrieval_policy = policy["retrieval"]
    projection_policy = policy["anchor_projection"]
    identifier_policy = policy["identifier"]
    canonical_anchors = anchor_nodes[
        anchor_nodes["anchor_kind"].eq(
            str(projection_policy["canonical_anchor_kind"])
        )
    ].copy()
    anchor_by_canonical_id = {
        str(row["canonical_id"]): row
        for row in canonical_anchors.to_dict("records")
    }
    candidate_anchor_rows = canonical_anchors.to_dict("records")
    symmetric_relation_types = {
        str(value)
        for value in projection_policy["symmetric_relation_types"]
    }
    relation_roles = build_relation_role_profiles(
        anchor_facts,
        symmetric_relation_types,
    )
    excluded_graph_path_relation_types = {
        str(value)
        for value in retrieval_policy[
            "excluded_graph_path_relation_types"
        ]
    }
    adjacency = build_anchor_adjacency(
        anchor_facts,
        excluded_graph_path_relation_types,
    )
    path_cache: dict[str, dict[str, list[str]]] = {}
    excluded_relation_types = {
        str(value)
        for value in retrieval_policy[
            "excluded_fact_relation_types"
        ]
    }
    maximum_hops = int(retrieval_policy["maximum_graph_hops"])
    primary_search_status = str(
        projection_policy["canonical_search_status"]
    )
    maximum_candidates = int(retrieval_policy["maximum_candidates"])
    minimum_signal_count = int(
        retrieval_policy["minimum_signal_count"]
    )
    require_same_entity_type = bool(
        retrieval_policy["require_same_entity_type"]
    )
    require_same_relation_role = bool(
        retrieval_policy["require_same_relation_role"]
    )
    require_graph_path = bool(
        retrieval_policy["require_graph_path"]
    )
    maximum_fallback_graph_edges = int(
        retrieval_policy["maximum_fallback_graph_edges"]
    )
    effective_exam_canonical_ids = exam_canonical_ids or set()
    require_exam_correct_endpoints = bool(
        retrieval_policy[
            "require_exam_term_correct_fact_endpoints"
        ]
    )
    require_exam_candidate = bool(
        retrieval_policy["require_exam_term_candidate"]
    )
    if (
        require_exam_correct_endpoints or require_exam_candidate
    ) and not effective_exam_canonical_ids:
        raise ValueError(
            "기출 엔티티 제한이 활성화됐지만 승인된 기출 canonical ID가 "
            "없습니다."
        )
    exclude_same_normalized_name = bool(
        retrieval_policy["exclude_same_normalized_name"]
    )
    maximum_distance_without_shared_era = int(
        retrieval_policy[
            "require_shared_era_or_maximum_graph_distance"
        ]
    )
    weights = retrieval_policy["weights"]
    output_rows: list[dict] = []
    for fact in canonical_facts.to_dict("records"):
        relation_type = str(fact["relation_type"])
        if relation_type in excluded_relation_types:
            continue
        start_canonical_id = str(fact["start_canonical_id"])
        end_canonical_id = str(fact["end_canonical_id"])
        if (
            require_exam_correct_endpoints
            and (
                start_canonical_id
                not in effective_exam_canonical_ids
                or end_canonical_id
                not in effective_exam_canonical_ids
            )
        ):
            continue
        start_anchor = anchor_by_canonical_id.get(start_canonical_id)
        end_anchor = anchor_by_canonical_id.get(end_canonical_id)
        if start_anchor is None or end_anchor is None:
            continue
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
            original_topics = set(
                parse_json_list(original_anchor["topic_ids_json"])
            )
            original_eras = set(
                parse_json_list(original_anchor["era_ids_json"])
            )
            ranked_rows: list[dict] = []
            for candidate_anchor in candidate_anchor_rows:
                candidate_anchor_id = str(
                    candidate_anchor["anchor_id"]
                )
                candidate_canonical_id = str(
                    candidate_anchor["canonical_id"]
                )
                if (
                    require_exam_candidate
                    and candidate_canonical_id
                    not in effective_exam_canonical_ids
                ):
                    continue
                if candidate_anchor_id == original_anchor_id:
                    continue
                if candidate_anchor_id == str(
                    fixed_anchor["anchor_id"]
                ):
                    continue
                if (
                    require_same_entity_type
                    and str(candidate_anchor["entity_type"])
                    != str(original_anchor["entity_type"])
                ):
                    continue
                if (
                    exclude_same_normalized_name
                    and str(
                        candidate_anchor.get("normalized_name") or ""
                    )
                    and str(
                        candidate_anchor.get("normalized_name") or ""
                    )
                    == str(original_anchor.get("normalized_name") or "")
                ):
                    continue
                candidate_topics = set(
                    parse_json_list(
                        candidate_anchor["topic_ids_json"]
                    )
                )
                candidate_eras = set(
                    parse_json_list(candidate_anchor["era_ids_json"])
                )
                shared_topics = sorted(
                    original_topics.intersection(candidate_topics)
                )
                shared_eras = sorted(
                    original_eras.intersection(candidate_eras)
                )
                same_relation_role = required_role in relation_roles.get(
                    candidate_anchor_id,
                    set(),
                )
                if (
                    require_same_relation_role
                    and not same_relation_role
                ):
                    continue
                path = paths.get(candidate_anchor_id, [])
                graph_distance = -1
                if path:
                    graph_distance = len(path) - 1
                if require_graph_path and graph_distance < 1:
                    continue
                path_relation_types: list[list[str]] = []
                path_search_statuses: list[list[str]] = []
                fallback_graph_edge_count = 0
                for left_anchor_id, right_anchor_id in zip(
                    path,
                    path[1:],
                ):
                    edge = adjacency[left_anchor_id][right_anchor_id]
                    relation_types = sorted(edge["relation_types"])
                    search_statuses = sorted(edge["search_statuses"])
                    path_relation_types.append(relation_types)
                    path_search_statuses.append(search_statuses)
                    if (
                        primary_search_status
                        not in edge["search_statuses"]
                    ):
                        fallback_graph_edge_count += 1
                if (
                    fallback_graph_edge_count
                    > maximum_fallback_graph_edges
                ):
                    continue
                if (
                    not shared_eras
                    and (
                        graph_distance < 1
                        or graph_distance
                        > maximum_distance_without_shared_era
                    )
                ):
                    continue
                signal_count = (
                    int(same_relation_role)
                    + int(bool(shared_topics))
                    + int(bool(shared_eras))
                    + int(graph_distance > 0)
                )
                if signal_count < minimum_signal_count:
                    continue
                score = 0.0
                if same_relation_role:
                    score += float(weights["same_relation_role"])
                score += len(shared_topics) * float(
                    weights["shared_topic"]
                )
                score += len(shared_eras) * float(
                    weights["shared_era"]
                )
                if graph_distance > 0:
                    score += float(weights["graph_path"]) / graph_distance
                score -= fallback_graph_edge_count * float(
                    weights["fallback_graph_edge_penalty"]
                )
                proposed_start_id = candidate_canonical_id
                proposed_end_id = end_canonical_id
                if swap_dimension == "END":
                    proposed_start_id = start_canonical_id
                    proposed_end_id = candidate_canonical_id
                candidate_id = create_stable_id(
                    str(identifier_policy["swap_candidate_prefix"]),
                    [
                        str(fact["canonical_relationship_id"]),
                        swap_dimension,
                        candidate_canonical_id,
                        str(policy["policy_version"]),
                    ],
                    policy,
                )
                graph_distance_value = ""
                if graph_distance > 0:
                    graph_distance_value = str(graph_distance)
                ranked_rows.append(
                    {
                        "swap_candidate_id": candidate_id,
                        "correct_canonical_relationship_id": str(
                            fact["canonical_relationship_id"]
                        ),
                        "swap_dimension": swap_dimension,
                        "relation_type": relation_type,
                        "correct_start_canonical_id": start_canonical_id,
                        "correct_start_display_name": str(
                            start_anchor["display_name"]
                        ),
                        "correct_end_canonical_id": end_canonical_id,
                        "correct_end_display_name": str(
                            end_anchor["display_name"]
                        ),
                        "replaced_canonical_id": str(
                            original_anchor["canonical_id"]
                        ),
                        "candidate_canonical_id": (
                            candidate_canonical_id
                        ),
                        "candidate_display_name": str(
                            candidate_anchor["display_name"]
                        ),
                        "candidate_entity_type": str(
                            candidate_anchor["entity_type"]
                        ),
                        "proposed_start_canonical_id": (
                            proposed_start_id
                        ),
                        "proposed_end_canonical_id": proposed_end_id,
                        "same_relation_role": str(
                            same_relation_role
                        ).lower(),
                        "shared_topic_ids_json": dumps(
                            shared_topics,
                            ensure_ascii=False,
                        ),
                        "shared_era_ids_json": dumps(
                            shared_eras,
                            ensure_ascii=False,
                        ),
                        "graph_distance": graph_distance_value,
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
                        "fallback_graph_edge_count": str(
                            fallback_graph_edge_count
                        ),
                        "signal_count": str(signal_count),
                        "retrieval_score": f"{score:.6f}",
                        "fixed_anchor_id": str(
                            fixed_anchor["anchor_id"]
                        ),
                        "policy_version": str(
                            policy["policy_version"]
                        ),
                    }
                )
            ranked_rows.sort(
                key=lambda row: (
                    -float(row["retrieval_score"]),
                    create_stable_id(
                        "",
                        [
                            row["swap_candidate_id"],
                            str(policy["policy_version"]),
                        ],
                        policy,
                    ),
                )
            )
            for rank, row in enumerate(
                ranked_rows[:maximum_candidates],
                start=1,
            ):
                row["candidate_rank"] = str(rank)
                output_rows.append(row)
    columns = [
        "swap_candidate_id",
        "correct_canonical_relationship_id",
        "swap_dimension",
        "relation_type",
        "correct_start_canonical_id",
        "correct_start_display_name",
        "correct_end_canonical_id",
        "correct_end_display_name",
        "replaced_canonical_id",
        "candidate_canonical_id",
        "candidate_display_name",
        "candidate_entity_type",
        "proposed_start_canonical_id",
        "proposed_end_canonical_id",
        "same_relation_role",
        "shared_topic_ids_json",
        "shared_era_ids_json",
        "graph_distance",
        "graph_path_anchor_ids_json",
        "graph_path_relation_types_json",
        "graph_path_search_statuses_json",
        "fallback_graph_edge_count",
        "signal_count",
        "retrieval_score",
        "fixed_anchor_id",
        "policy_version",
        "candidate_rank",
    ]
    candidates = pd.DataFrame(output_rows, columns=columns)
    if candidates["swap_candidate_id"].duplicated().any():
        raise ValueError("RAG 교체 후보 ID가 중복됐습니다.")
    return candidates
