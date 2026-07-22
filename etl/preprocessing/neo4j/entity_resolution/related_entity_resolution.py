from json import dumps, loads

import pandas as pd

from common import normalize_history_term
from entity_resolution.identifiers import create_stable_id


def build_related_entity_tasks(
    decisions: list[dict],
    gold_tasks: list[dict],
    policy: dict,
) -> list[dict]:
    """사람이 지정한 관련 엔티티를 독립적인 2차 ER seed task로 만든다."""
    resolution_policy = policy["entity_resolution"]
    related_policy = resolution_policy["related_entity_resolution"]
    identifier_policy = resolution_policy["identifier_policy"]
    task_by_id = {
        str(task["term_review_task_id"]): task for task in gold_tasks
    }
    related_tasks: list[dict] = []
    for decision in decisions:
        origin_task_id = str(decision["term_review_task_id"])
        origin_task = task_by_id.get(origin_task_id)
        if origin_task is None:
            raise ValueError(
                "관련 엔티티의 원본 gold task를 찾을 수 없습니다: "
                f"{origin_task_id}"
            )
        candidate_by_id = {
            str(candidate["source_candidate_id"]): candidate
            for candidate in origin_task["source_candidates"]
        }
        for related_entity in decision.get(
            "proposed_related_entities",
            [],
        ):
            related_key = str(related_entity["related_entity_key"])
            display_name = str(related_entity["display_name"])
            entity_type = str(related_entity["entity_type"])
            seed_candidate_ids = sorted(
                str(candidate_id)
                for candidate_id in related_entity[
                    "evidence_source_candidate_ids"
                ]
            )
            seed_candidates = [
                candidate_by_id[candidate_id]
                for candidate_id in seed_candidate_ids
                if candidate_id in candidate_by_id
            ]
            if len(seed_candidates) != len(seed_candidate_ids):
                raise ValueError(
                    "관련 엔티티 seed 후보가 원본 task와 일치하지 않습니다: "
                    f"{origin_task_id}/{related_key}"
                )
            normalized_name = normalize_history_term(display_name)
            related_case_id = create_stable_id(
                identifier_policy["related_resolution_case_prefix"],
                [
                    origin_task["resolution_case_id"],
                    related_key,
                    normalized_name,
                    entity_type,
                    policy["normalization_policy_version"],
                ],
                identifier_policy,
            )
            related_task_id = create_stable_id(
                identifier_policy["related_entity_task_prefix"],
                [
                    related_case_id,
                    decision["prompt_version"],
                ],
                identifier_policy,
            )
            gold_metadata = origin_task.get("gold_set_metadata", {})
            related_tasks.append(
                {
                    "related_entity_task_id": related_task_id,
                    "related_resolution_case_id": related_case_id,
                    "origin_term_review_task_id": origin_task_id,
                    "origin_resolution_case_id": origin_task[
                        "resolution_case_id"
                    ],
                    "origin_gold_case_id": gold_metadata.get(
                        "gold_case_id",
                        "",
                    ),
                    "origin_canonical_term": origin_task["canonical_term"],
                    "related_entity_key": related_key,
                    "canonical_term": display_name,
                    "normalized_term": normalized_name,
                    "entity_type_proposal": entity_type,
                    "seed_source_candidate_ids": seed_candidate_ids,
                    "seed_source_candidates": seed_candidates,
                    "origin_problem_context_samples": origin_task.get(
                        "problem_context_samples",
                        [],
                    ),
                    "reason": related_entity["reason"],
                    "queue_status": related_policy["queue_status"],
                    "annotation_prompt_version": decision["prompt_version"],
                    "resolution_policy_version": policy["policy_version"],
                }
            )
    return sorted(
        related_tasks,
        key=lambda task: (
            task["origin_term_review_task_id"],
            task["related_entity_key"],
        ),
    )


def build_related_term_table(related_tasks: list[dict]) -> pd.DataFrame:
    """관련 엔티티 queue를 기존 이름 검색기가 읽는 용어 테이블로 바꾼다."""
    columns = [
        "canonical_term",
        "category",
        "entity_type_proposal",
        "count",
        "problem_ids",
        "input_resolution_case_id",
        "related_entity_task_id",
        "related_entity_origin_json",
        "extraction_model",
        "extraction_reasoning_effort",
        "extraction_policy_version",
    ]
    rows: list[dict] = []
    for task in related_tasks:
        origin = {
            "related_entity_task_id": task["related_entity_task_id"],
            "origin_term_review_task_id": task[
                "origin_term_review_task_id"
            ],
            "origin_resolution_case_id": task[
                "origin_resolution_case_id"
            ],
            "origin_gold_case_id": task["origin_gold_case_id"],
            "origin_canonical_term": task["origin_canonical_term"],
            "related_entity_key": task["related_entity_key"],
            "seed_source_candidate_ids": task[
                "seed_source_candidate_ids"
            ],
            "reason": task["reason"],
        }
        rows.append(
            {
                "canonical_term": task["canonical_term"],
                "category": "",
                "entity_type_proposal": task["entity_type_proposal"],
                "count": 0,
                "problem_ids": dumps([], ensure_ascii=False),
                "input_resolution_case_id": task[
                    "related_resolution_case_id"
                ],
                "related_entity_task_id": task["related_entity_task_id"],
                "related_entity_origin_json": dumps(
                    origin,
                    ensure_ascii=False,
                    sort_keys=True,
                ),
                "extraction_model": "human_gold_adjudication",
                "extraction_reasoning_effort": "",
                "extraction_policy_version": task[
                    "annotation_prompt_version"
                ],
            }
        )
    return pd.DataFrame(rows, columns=columns)


def inject_related_entity_seed_candidates(
    match_results: list[dict],
    related_tasks: list[dict],
    policy: dict,
) -> list[dict]:
    """사람이 확인한 관련 SourceRecord가 재검색에서 누락되지 않게 seed로 합친다."""
    related_policy = policy["entity_resolution"][
        "related_entity_resolution"
    ]
    task_by_case_id = {
        str(task["related_resolution_case_id"]): task
        for task in related_tasks
    }
    source_collection_mapping = related_policy[
        "source_collection_by_source"
    ]
    seed_method = str(related_policy["seed_retrieval_method"])
    seed_score = float(related_policy["seed_retrieval_score"])
    for match_result in match_results:
        case_id = str(match_result.get("input_resolution_case_id") or "")
        related_task = task_by_case_id.get(case_id)
        if related_task is None:
            continue
        for candidate in related_task["seed_source_candidates"]:
            source = str(candidate["source"])
            collection_name = str(
                source_collection_mapping.get(source) or ""
            )
            if not collection_name:
                raise ValueError(
                    f"관련 엔티티 seed source mapping이 없습니다: {source}"
                )
            collection = match_result[collection_name]
            source_record_id = str(candidate["source_record_id"])
            existing = next(
                (
                    item
                    for item in collection
                    if str(item.get("source_record_id") or "")
                    == source_record_id
                ),
                None,
            )
            if existing is not None:
                methods = list(existing.get("retrieval_methods", []))
                if seed_method not in methods:
                    methods.append(seed_method)
                existing["retrieval_methods"] = methods
                existing["human_related_entity_seed"] = True
                existing["related_entity_task_id"] = related_task[
                    "related_entity_task_id"
                ]
                continue
            source_context = dict(candidate.get("source_context", {}))
            collection.append(
                {
                    **source_context,
                    "source": source,
                    "source_id": "",
                    "source_release": source_record_id.rsplit(":", 1)[-1],
                    "source_record_id": source_record_id,
                    "matched_name": related_task["canonical_term"],
                    "matched_field": seed_method,
                    "retrieval_method": seed_method,
                    "retrieval_methods": [seed_method],
                    "retrieval_score": seed_score,
                    "score_components": {seed_method: seed_score},
                    "verification_status": related_policy["queue_status"],
                    "retrieval_policy_version": policy["policy_version"],
                    "category_mismatch": None,
                    "human_related_entity_seed": True,
                    "related_entity_task_id": related_task[
                        "related_entity_task_id"
                    ],
                }
            )
    return match_results


def select_seed_backed_alternatives(
    resolution_tables: dict[str, pd.DataFrame],
    term_decision_tables: dict[str, pd.DataFrame],
    policy: dict,
) -> pd.DataFrame:
    """사람이 지정한 seed SourceRecord가 속한 검증 대안 하나를 선택한다."""
    related_policy = policy["entity_resolution"][
        "related_entity_resolution"
    ]
    selection_method = related_policy["final_identity"][
        "selection_method"
    ]
    cases = resolution_tables["resolution_cases"]
    candidates = resolution_tables["source_record_candidates"]
    alternatives = term_decision_tables[
        "reviewed_canonical_alternatives"
    ]
    roles = term_decision_tables["reviewed_source_roles"]
    identity_role_by_candidate = {
        str(row["source_candidate_id"]): str(
            row["canonical_alternative_id"]
        )
        for row in roles.to_dict("records")
        if row["verification_status"] == "VERIFIED"
        and row["verified_role"] == "IDENTITY_MEMBER"
    }
    alternative_by_id = {
        str(row["canonical_alternative_id"]): row
        for row in alternatives.to_dict("records")
        if row["verification_status"] == "VERIFIED"
    }
    seed_ids_by_case: dict[str, list[str]] = {}
    for candidate in candidates.to_dict("records"):
        metadata = loads(candidate["source_metadata_json"])
        if not metadata.get("human_related_entity_seed"):
            continue
        case_id = str(candidate["resolution_case_id"])
        seed_ids_by_case.setdefault(case_id, []).append(
            str(candidate["source_candidate_id"])
        )

    rows: list[dict] = []
    for case in cases.to_dict("records"):
        case_id = str(case["resolution_case_id"])
        seed_ids = sorted(set(seed_ids_by_case.get(case_id, [])))
        selected_alternative_ids = sorted(
            {
                identity_role_by_candidate[candidate_id]
                for candidate_id in seed_ids
                if candidate_id in identity_role_by_candidate
            }
        )
        selected_alternative_id = ""
        display_name = ""
        entity_type = ""
        selection_status = "NEEDS_MANUAL_REVIEW"
        review_reason = "SEED_NOT_IN_VERIFIED_IDENTITY_ALTERNATIVE"
        if len(selected_alternative_ids) == 1:
            selected_alternative_id = selected_alternative_ids[0]
            selected_alternative = alternative_by_id.get(
                selected_alternative_id
            )
            if selected_alternative is not None:
                display_name = str(
                    selected_alternative["display_name_proposal"]
                )
                entity_type = str(
                    selected_alternative["entity_type_proposal"]
                )
                selection_status = "VERIFIED"
                review_reason = "SEED_SOURCE_RESOLVED_TO_SINGLE_ALTERNATIVE"
            elif selected_alternative is None:
                review_reason = "SEED_ALTERNATIVE_NOT_VERIFIED"
        elif len(selected_alternative_ids) > 1:
            review_reason = "SEEDS_MAP_TO_MULTIPLE_ALTERNATIVES"
        rows.append(
            {
                "resolution_case_id": case_id,
                "canonical_term": case["canonical_term"],
                "canonical_alternative_id": selected_alternative_id,
                "display_name": display_name,
                "entity_type": entity_type,
                "seed_source_candidate_ids_json": dumps(
                    seed_ids,
                    ensure_ascii=False,
                ),
                "selection_status": selection_status,
                "selection_method": selection_method,
                "review_reason": review_reason,
                "resolution_policy_version": policy["policy_version"],
            }
        )
    columns = [
        "resolution_case_id",
        "canonical_term",
        "canonical_alternative_id",
        "display_name",
        "entity_type",
        "seed_source_candidate_ids_json",
        "selection_status",
        "selection_method",
        "review_reason",
        "resolution_policy_version",
    ]
    return pd.DataFrame(rows, columns=columns)
