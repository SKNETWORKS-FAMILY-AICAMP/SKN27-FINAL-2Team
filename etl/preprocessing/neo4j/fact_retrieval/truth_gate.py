from __future__ import annotations

from collections import defaultdict
from json import dumps, loads

import pandas as pd

from fact_retrieval.build import create_stable_id, parse_json_list


def normalize_fact_key(
    start_canonical_id: str,
    relation_type: str,
    end_canonical_id: str,
    symmetric_relation_types: set[str],
) -> tuple[str, str, str]:
    """대칭 관계만 endpoint 순서를 통일한다."""
    if (
        relation_type in symmetric_relation_types
        and end_canonical_id < start_canonical_id
    ):
        return (
            end_canonical_id,
            relation_type,
            start_canonical_id,
        )
    return (
        start_canonical_id,
        relation_type,
        end_canonical_id,
    )


def evaluate_distractor_truth_gate(
    swap_candidates: pd.DataFrame,
    canonical_facts: pd.DataFrame,
    policy: dict,
) -> tuple[pd.DataFrame, list[dict]]:
    """교체 후보가 이미 참인지 차단하고 나머지를 외부 검증으로 보낸다."""
    gate_policy = policy["truth_gate"]
    identifier_policy = policy["identifier"]
    trusted_statuses = {
        str(value)
        for value in gate_policy["trusted_verification_statuses"]
    }
    symmetric_relation_types = {
        str(value)
        for value in policy["anchor_projection"][
            "symmetric_relation_types"
        ]
    }
    inverse_relation_types = {
        str(relation_type): str(inverse_relation_type)
        for relation_type, inverse_relation_type in gate_policy[
            "inverse_relation_types"
        ].items()
    }
    trusted_facts = canonical_facts[
        canonical_facts["verification_status"].isin(trusted_statuses)
    ]
    fact_by_id = {
        str(row["canonical_relationship_id"]): row
        for row in canonical_facts.to_dict("records")
    }
    fact_ids_by_triple: dict[tuple[str, str, str], set[str]] = (
        defaultdict(set)
    )
    objects_by_subject_relation: dict[
        tuple[str, str],
        set[str],
    ] = defaultdict(set)
    for fact in trusted_facts.to_dict("records"):
        direct_key = normalize_fact_key(
            str(fact["start_canonical_id"]),
            str(fact["relation_type"]),
            str(fact["end_canonical_id"]),
            symmetric_relation_types,
        )
        semantic_keys = [direct_key]
        inverse_relation_type = inverse_relation_types.get(
            str(fact["relation_type"])
        )
        if inverse_relation_type:
            semantic_keys.append(
                normalize_fact_key(
                    str(fact["end_canonical_id"]),
                    inverse_relation_type,
                    str(fact["start_canonical_id"]),
                    symmetric_relation_types,
                )
            )
        for semantic_key in semantic_keys:
            fact_ids_by_triple[semantic_key].add(
                str(fact["canonical_relationship_id"])
            )
            objects_by_subject_relation[
                (semantic_key[0], semantic_key[1])
            ].add(semantic_key[2])

    exclusive_relation_types = {
        str(value)
        for value in gate_policy[
            "exclusive_object_relation_types"
        ]
    }
    result_rows: list[dict] = []
    external_task_groups: dict[
        tuple[str, str, str],
        dict[str, object],
    ] = {}
    for candidate in swap_candidates.to_dict("records"):
        proposed_key = normalize_fact_key(
            str(candidate["proposed_start_canonical_id"]),
            str(candidate["relation_type"]),
            str(candidate["proposed_end_canonical_id"]),
            symmetric_relation_types,
        )
        existing_fact_ids = sorted(
            fact_ids_by_triple.get(proposed_key, set())
        )
        gate_status = str(
            gate_policy["external_verification_status"]
        )
        gate_reason = (
            "그래프 부재만으로 거짓을 확정하지 않고 외부 사실 검증이 필요합니다."
        )
        if existing_fact_ids:
            gate_status = str(gate_policy["known_true_status"])
            gate_reason = (
                "제안된 오답 관계가 현재 신뢰 사실 그래프에 존재합니다."
            )
        elif (
            str(candidate["swap_dimension"]) == "END"
            and str(candidate["relation_type"])
            in exclusive_relation_types
        ):
            known_objects = objects_by_subject_relation.get(
                (proposed_key[0], proposed_key[1]),
                set(),
            )
            if (
                known_objects
                and proposed_key[2] not in known_objects
            ):
                gate_status = str(
                    gate_policy[
                        "exclusive_contradiction_status"
                    ]
                )
                gate_reason = (
                    "단일값 관계의 신뢰된 기존 대상과 다른 후보입니다."
                )
        gate_id = create_stable_id(
            str(identifier_policy["truth_gate_prefix"]),
            [
                str(candidate["swap_candidate_id"]),
                gate_status,
                str(policy["policy_version"]),
            ],
            policy,
        )
        result_row = {
            **candidate,
            "truth_gate_id": gate_id,
            "truth_gate_status": gate_status,
            "truth_gate_reason": gate_reason,
            "existing_fact_ids_json": dumps(
                existing_fact_ids,
                ensure_ascii=False,
            ),
        }
        result_rows.append(result_row)
        if (
            gate_status
            == str(gate_policy["external_verification_status"])
        ):
            proposed_start_display_name = str(
                candidate["correct_start_display_name"]
            )
            if str(candidate["swap_dimension"]) == "START":
                proposed_start_display_name = str(
                    candidate["candidate_display_name"]
                )
            proposed_end_display_name = str(
                candidate["correct_end_display_name"]
            )
            if str(candidate["swap_dimension"]) == "END":
                proposed_end_display_name = str(
                    candidate["candidate_display_name"]
                )
            correct_fact_id = str(
                candidate["correct_canonical_relationship_id"]
            )
            correct_fact = fact_by_id.get(correct_fact_id, {})
            task_id = create_stable_id(
                str(identifier_policy["external_task_prefix"]),
                [
                    proposed_key[0],
                    proposed_key[1],
                    proposed_key[2],
                    str(policy["policy_version"]),
                ],
                policy,
            )
            representative_task = {
                "external_verification_task_id": task_id,
                "relation_type": str(candidate["relation_type"]),
                "proposed_start_canonical_id": proposed_key[0],
                "proposed_start_display_name": (
                    proposed_start_display_name
                ),
                "proposed_end_canonical_id": proposed_key[2],
                "proposed_end_display_name": (
                    proposed_end_display_name
                ),
                "representative_truth_gate_id": gate_id,
                "representative_swap_candidate_id": str(
                    candidate["swap_candidate_id"]
                ),
                "representative_correct_fact_id": correct_fact_id,
                "retrieval_evidence": {
                    "shared_topic_ids": parse_json_list(
                        candidate["shared_topic_ids_json"]
                    ),
                    "shared_era_ids": parse_json_list(
                        candidate["shared_era_ids_json"]
                    ),
                    "graph_distance": str(
                        candidate["graph_distance"]
                    ),
                    "graph_path_anchor_ids": parse_json_list(
                        candidate[
                            "graph_path_anchor_ids_json"
                        ]
                    ),
                    "graph_path_relation_types": loads(
                        str(
                            candidate[
                                "graph_path_relation_types_json"
                            ]
                        )
                    ),
                    "graph_path_search_statuses": loads(
                        str(
                            candidate[
                                "graph_path_search_statuses_json"
                            ]
                        )
                    ),
                    "fallback_graph_edge_count": int(
                        candidate["fallback_graph_edge_count"]
                    ),
                    "retrieval_score": str(
                        candidate["retrieval_score"]
                    ),
                    "candidate_rank": str(
                        candidate["candidate_rank"]
                    ),
                },
                "correct_fact_evidence": {
                    "verification_status": str(
                        correct_fact.get(
                            "verification_status",
                            "",
                        )
                    ),
                    "evidence_sentences": parse_json_list(
                        correct_fact.get(
                            "evidence_sentences_json",
                            "",
                        )
                    ),
                    "evidence_urls": parse_json_list(
                        correct_fact.get(
                            "evidence_urls_json",
                            "",
                        )
                    ),
                    "detail_urls": parse_json_list(
                        correct_fact.get(
                            "detail_urls_json",
                            "",
                        )
                    ),
                    "source_datasets": parse_json_list(
                        correct_fact.get(
                            "source_datasets_json",
                            "",
                        )
                    ),
                },
                "required_decision": (
                    "TRUE_RELATION, FALSE_RELATION, or UNVERIFIABLE"
                ),
                "policy_version": str(policy["policy_version"]),
            }
            task_priority = (
                -float(candidate["retrieval_score"]),
                str(candidate["swap_candidate_id"]),
            )
            group = external_task_groups.get(proposed_key)
            if group is None:
                external_task_groups[proposed_key] = {
                    "representative": representative_task,
                    "priority": task_priority,
                    "truth_gate_ids": {gate_id},
                    "swap_candidate_ids": {
                        str(candidate["swap_candidate_id"])
                    },
                    "correct_fact_ids": {correct_fact_id},
                }
                continue
            group["truth_gate_ids"].add(gate_id)
            group["swap_candidate_ids"].add(
                str(candidate["swap_candidate_id"])
            )
            group["correct_fact_ids"].add(correct_fact_id)
            if task_priority < group["priority"]:
                group["representative"] = representative_task
                group["priority"] = task_priority
    external_tasks: list[dict] = []
    for group in external_task_groups.values():
        task = dict(group["representative"])
        task["supporting_truth_gate_ids"] = sorted(
            group["truth_gate_ids"]
        )
        task["supporting_swap_candidate_ids"] = sorted(
            group["swap_candidate_ids"]
        )
        task["supporting_correct_fact_ids"] = sorted(
            group["correct_fact_ids"]
        )
        task["candidate_occurrence_count"] = len(
            group["swap_candidate_ids"]
        )
        external_tasks.append(task)
    external_tasks.sort(
        key=lambda task: (
            -float(
                task["retrieval_evidence"]["retrieval_score"]
            ),
            task["representative_swap_candidate_id"],
        )
    )
    results = pd.DataFrame(result_rows)
    if results["truth_gate_id"].duplicated().any():
        raise ValueError("오답 사실 검증 gate ID가 중복됐습니다.")
    return results, external_tasks
