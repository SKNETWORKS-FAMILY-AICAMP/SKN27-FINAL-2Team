from itertools import combinations
from json import dumps
from pathlib import Path

import pandas as pd

from common import normalize_history_term
from entity_resolution.source_entity_type import (
    resolve_source_entity_type,
)


def get_problem_count(task: dict) -> int:
    """task의 문항 수를 안전하게 정수로 변환한다."""
    value = task.get("problem_count", 0)
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def resolve_candidate_source_entity_type(
    candidate: dict,
    policy: dict,
) -> str:
    """후보에 저장된 유형이나 출처 원문 규칙으로 EntityType을 결정한다."""
    stored_type = str(
        candidate.get("source_entity_type_proposal") or ""
    ).strip()
    if stored_type:
        return stored_type
    source = str(candidate.get("source") or "")
    source_policies = policy["entity_resolution"][
        "source_feature_policy"
    ]["sources"]
    source_policy = source_policies.get(source, {})
    source_context = candidate.get("source_context") or {}
    if not isinstance(source_context, dict):
        return ""
    return resolve_source_entity_type(source_context, source_policy)


def collect_ambiguous_untyped_source_ids(
    tasks: list[dict],
    policy: dict,
) -> set[str]:
    """출처 유형 없이 여러 task 유형에 걸친 동일 레코드를 찾는다."""
    proposed_types_by_source: dict[str, set[str]] = {}
    triage_policy = policy["entity_resolution"]["semantic_review"][
        "deterministic_triage"
    ]
    allowed_matched_fields = set(
        triage_policy["allowed_entity_candidate_matched_fields"]
    )
    allowed_retrieval_methods = set(
        triage_policy["allowed_entity_candidate_retrieval_methods"]
    )
    blocked_compatibilities = set(
        triage_policy["blocked_category_compatibilities"]
    )
    for task in tasks:
        normalized_term = normalize_history_term(
            task.get("canonical_term", "")
        )
        proposed_type = str(
            task.get("entity_type_proposal") or ""
        ).strip()
        if not normalized_term or not proposed_type:
            continue
        for candidate in task.get("source_candidates", []):
            normalized_match = normalize_history_term(
                candidate.get("matched_name", "")
            )
            if normalized_match != normalized_term:
                continue
            if candidate.get("matched_field") not in allowed_matched_fields:
                continue
            if (
                candidate.get("retrieval_method")
                not in allowed_retrieval_methods
            ):
                continue
            if (
                candidate.get("category_compatibility")
                in blocked_compatibilities
            ):
                continue
            if resolve_candidate_source_entity_type(candidate, policy):
                continue
            source_record_id = str(
                candidate.get("source_record_id") or ""
            )
            if not source_record_id:
                continue
            proposed_types_by_source.setdefault(
                source_record_id,
                set(),
            ).add(proposed_type)
    return {
        source_record_id
        for source_record_id, proposed_types
        in proposed_types_by_source.items()
        if len(proposed_types) > 1
    }


def is_exact_viable_candidate(
    task: dict,
    candidate: dict,
    policy: dict,
    ambiguous_untyped_source_ids: set[str] | None = None,
) -> bool:
    """용어명·카테고리·엔티티 유형이 코드 승인 조건과 맞는지 검사한다."""
    triage_policy = policy["entity_resolution"]["semantic_review"][
        "deterministic_triage"
    ]
    normalized_term = normalize_history_term(task.get("canonical_term", ""))
    normalized_match = normalize_history_term(
        candidate.get("matched_name", "")
    )
    if not normalized_term or normalized_match != normalized_term:
        return False
    allowed_matched_fields = set(
        triage_policy["allowed_entity_candidate_matched_fields"]
    )
    if candidate.get("matched_field") not in allowed_matched_fields:
        return False
    allowed_retrieval_methods = set(
        triage_policy["allowed_entity_candidate_retrieval_methods"]
    )
    if candidate.get("retrieval_method") not in allowed_retrieval_methods:
        return False
    blocked_compatibilities = set(
        triage_policy["blocked_category_compatibilities"]
    )
    if candidate.get("category_compatibility") in blocked_compatibilities:
        return False
    source_record_id = str(
        candidate.get("source_record_id") or ""
    )
    if (
        ambiguous_untyped_source_ids
        and source_record_id in ambiguous_untyped_source_ids
    ):
        return False
    proposed_type = str(task.get("entity_type_proposal") or "")
    if triage_policy["require_entity_type_proposal"] and not proposed_type:
        return False
    source_type = resolve_candidate_source_entity_type(
        candidate,
        policy,
    )
    if proposed_type and source_type and proposed_type != source_type:
        return False
    return True


def identity_members_are_connected(
    member_ids: set[str],
    pair_signals: list[dict],
) -> bool:
    """merge 가능한 pair 간선으로 모든 identity member가 연결되는지 검사한다."""
    if not member_ids:
        return False
    adjacency = {member_id: set() for member_id in member_ids}
    for pair in pair_signals:
        left_id = str(pair.get("left_source_candidate_id") or "")
        right_id = str(pair.get("right_source_candidate_id") or "")
        if (
            left_id in member_ids
            and right_id in member_ids
            and bool(pair.get("merge_eligible"))
        ):
            adjacency[left_id].add(right_id)
            adjacency[right_id].add(left_id)
    visited: set[str] = set()
    pending = [next(iter(member_ids))]
    while pending:
        candidate_id = pending.pop()
        if candidate_id in visited:
            continue
        visited.add(candidate_id)
        pending.extend(adjacency[candidate_id].difference(visited))
    return visited == member_ids


def internal_pairs_have_no_conflict(
    member_ids: set[str],
    pair_signals: list[dict],
) -> bool:
    """승인 그룹 내부의 모든 pair가 존재하고 충돌 신호가 없는지 검사한다."""
    pair_by_ids = {
        frozenset(
            [
                str(pair.get("left_source_candidate_id") or ""),
                str(pair.get("right_source_candidate_id") or ""),
            ]
        ): pair
        for pair in pair_signals
    }
    for left_id, right_id in combinations(sorted(member_ids), 2):
        pair = pair_by_ids.get(frozenset([left_id, right_id]))
        if pair is None or pair.get("conflicts"):
            return False
    return True


def build_candidate_deduplication_keys(
    candidate: dict,
    policy: dict,
) -> list[str]:
    """같은 공식 출처 안의 동일 레코드를 찾는 메타데이터 키를 만든다."""
    source = str(candidate.get("source") or "")
    source_policy = policy["entity_resolution"][
        "source_feature_policy"
    ]["sources"].get(source, {})
    source_context = candidate.get("source_context") or {}
    if not isinstance(source_context, dict):
        return []
    strict_fields = source_policy.get(
        "identity_deduplication_fields",
        [],
    )
    rules: list[dict] = []
    if strict_fields:
        rules.append(
            {
                "name": "strict",
                "fields": strict_fields,
                "required_populated_fields": [],
                "minimum_populated_fields": source_policy.get(
                    "identity_deduplication_minimum_populated_fields",
                    len(strict_fields),
                ),
            }
        )
    rules.extend(
        source_policy.get(
            "identity_additional_deduplication_rules",
            [],
        )
    )

    keys: list[str] = []
    for rule in rules:
        field_names = [str(value) for value in rule.get("fields", [])]
        if not field_names:
            continue
        fingerprint: dict[str, object] = {}
        populated_fields: set[str] = set()
        for field_name in field_names:
            value = source_context.get(field_name)
            normalized_value: object = value
            if isinstance(value, str):
                normalized_value = value.strip()
            if normalized_value not in ["", None, []]:
                populated_fields.add(field_name)
            fingerprint[field_name] = normalized_value
        required_fields = {
            str(value)
            for value in rule.get("required_populated_fields", [])
        }
        if not required_fields.issubset(populated_fields):
            continue
        minimum_populated_fields = int(
            rule.get("minimum_populated_fields", len(field_names))
        )
        if len(populated_fields) < minimum_populated_fields:
            continue
        rule_name = str(rule.get("name") or "metadata")
        keys.append(
            f"{source}:{rule_name}:"
            f"{dumps(fingerprint, ensure_ascii=False, sort_keys=True)}"
        )
    return sorted(set(keys))


def build_candidate_deduplication_key(
    candidate: dict,
    policy: dict,
) -> str:
    """기존 단일 키 사용처를 위해 첫 번째 메타데이터 중복 키를 반환한다."""
    keys = build_candidate_deduplication_keys(candidate, policy)
    if not keys:
        return ""
    return keys[0]


def is_reference_only_candidate(
    candidate: dict,
    policy: dict,
) -> bool:
    """원천에서 실체가 아니라 다른 분류의 색인어로 제공된 후보인지 확인한다."""
    source = str(candidate.get("source") or "")
    source_policy = policy["entity_resolution"][
        "source_feature_policy"
    ]["sources"].get(source, {})
    filter_policy = source_policy.get("reference_only_filter", {})
    if not filter_policy:
        return False
    source_context = candidate.get("source_context") or {}
    if not isinstance(source_context, dict):
        return False
    kind_value = str(
        source_context.get(filter_policy["kind_field"]) or ""
    ).strip()
    allowed_kind_values = {
        str(value).strip()
        for value in filter_policy["kind_values"]
    }
    if kind_value not in allowed_kind_values:
        return False
    remark_value = normalize_history_term(
        source_context.get(filter_policy["remark_field"], "")
    )
    allowed_remark_values = {
        normalize_history_term(value)
        for value in filter_policy["remark_values"]
    }
    return bool(remark_value and remark_value in allowed_remark_values)


def has_direct_primary_name(
    candidate: dict,
    canonical_term: str,
    policy: dict,
) -> bool:
    """후보의 대표 표제어가 시험 용어와 직접 일치하는지 확인한다."""
    source = str(candidate.get("source") or "")
    source_policy = policy["entity_resolution"][
        "source_feature_policy"
    ]["sources"].get(source, {})
    name_fields = source_policy.get("name_fields", [])
    if not name_fields:
        return False
    source_context = candidate.get("source_context") or {}
    if not isinstance(source_context, dict):
        return False
    primary_name = source_context.get(str(name_fields[0]))
    if isinstance(primary_name, list):
        return False
    return (
        normalize_history_term(primary_name)
        == normalize_history_term(canonical_term)
    )


def remove_reference_only_components(
    task: dict,
    components: list[list[str]],
    candidate_by_id: dict[str, dict],
    policy: dict,
) -> list[list[str]]:
    """직접 표제어 후보가 있을 때 분류용 색인행만 후보 컴포넌트에서 제외한다."""
    reference_components = [
        member_ids
        for member_ids in components
        if all(
            is_reference_only_candidate(
                candidate_by_id[candidate_id],
                policy,
            )
            for candidate_id in member_ids
        )
    ]
    if not reference_components:
        return components
    substantive_components = [
        member_ids
        for member_ids in components
        if member_ids not in reference_components
    ]
    if not substantive_components:
        return components
    has_direct_anchor = any(
        has_direct_primary_name(
            candidate_by_id[candidate_id],
            str(task.get("canonical_term") or ""),
            policy,
        )
        for member_ids in substantive_components
        for candidate_id in member_ids
    )
    if not has_direct_anchor:
        return components
    return substantive_components


def build_exact_candidate_components(
    task: dict,
    policy: dict,
    ambiguous_untyped_source_ids: set[str] | None = None,
) -> list[list[str]]:
    """exact 공식 출처 후보를 안전한 병합 그룹 또는 단독 엔티티로 나눈다."""
    triage_policy = policy["entity_resolution"]["semantic_review"][
        "deterministic_triage"
    ]
    candidate_by_id = {
        str(candidate["source_candidate_id"]): candidate
        for candidate in task.get("source_candidates", [])
    }
    exact_candidate_ids = {
        str(candidate["source_candidate_id"])
        for candidate in task.get("source_candidates", [])
        if is_exact_viable_candidate(
            task,
            candidate,
            policy,
            ambiguous_untyped_source_ids,
        )
    }
    grouped_candidate_ids: set[str] = set()
    components: list[list[str]] = []
    pair_signals = task.get("relevant_pair_signals", [])
    alternatives = sorted(
        task.get("code_canonical_alternatives", []),
        key=lambda alternative: (
            -len(alternative.get("source_candidate_ids", [])),
            str(alternative.get("canonical_alternative_id") or ""),
        ),
    )
    for alternative in alternatives:
        if alternative.get("confidence_tier") != triage_policy[
            "required_confidence_tier"
        ]:
            continue
        member_ids = {
            str(candidate_id)
            for candidate_id in alternative.get(
                "source_candidate_ids",
                [],
            )
        }
        if len(member_ids) < int(triage_policy["minimum_member_count"]):
            continue
        if not member_ids.issubset(exact_candidate_ids):
            continue
        distinct_sources = {
            str(candidate_by_id[candidate_id].get("source") or "")
            for candidate_id in member_ids
        }
        if len(distinct_sources) < int(
            triage_policy["minimum_distinct_source_count"]
        ):
            continue
        if member_ids.intersection(grouped_candidate_ids):
            continue
        if not internal_pairs_have_no_conflict(member_ids, pair_signals):
            continue
        if not identity_members_are_connected(member_ids, pair_signals):
            continue
        components.append(sorted(member_ids))
        grouped_candidate_ids.update(member_ids)
    all_candidate_ids = sorted(exact_candidate_ids)
    duplicate_parent_by_id = {
        candidate_id: candidate_id
        for candidate_id in all_candidate_ids
    }

    def find_duplicate_root(candidate_id: str) -> str:
        root_id = candidate_id
        while duplicate_parent_by_id[root_id] != root_id:
            root_id = duplicate_parent_by_id[root_id]
        while duplicate_parent_by_id[candidate_id] != candidate_id:
            parent_id = duplicate_parent_by_id[candidate_id]
            duplicate_parent_by_id[candidate_id] = root_id
            candidate_id = parent_id
        return root_id

    for member_ids in components:
        root_id = find_duplicate_root(member_ids[0])
        for candidate_id in member_ids[1:]:
            candidate_root_id = find_duplicate_root(candidate_id)
            if candidate_root_id != root_id:
                duplicate_parent_by_id[candidate_root_id] = root_id

    duplicate_groups: dict[str, list[str]] = {}
    for candidate_id in all_candidate_ids:
        for deduplication_key in build_candidate_deduplication_keys(
            candidate_by_id[candidate_id],
            policy,
        ):
            duplicate_groups.setdefault(
                deduplication_key,
                [],
            ).append(candidate_id)

    for duplicate_member_ids in duplicate_groups.values():
        if len(duplicate_member_ids) < 2:
            continue
        root_id = find_duplicate_root(duplicate_member_ids[0])
        for candidate_id in duplicate_member_ids[1:]:
            candidate_root_id = find_duplicate_root(candidate_id)
            if candidate_root_id != root_id:
                duplicate_parent_by_id[candidate_root_id] = root_id
    merged_components: dict[str, list[str]] = {}
    for candidate_id in all_candidate_ids:
        root_id = find_duplicate_root(candidate_id)
        merged_components.setdefault(root_id, []).append(candidate_id)
    components = [
        sorted(member_ids)
        for member_ids in merged_components.values()
    ]
    components = remove_reference_only_components(
        task,
        components,
        candidate_by_id,
        policy,
    )
    components.sort(key=lambda member_ids: (-len(member_ids), member_ids))
    return components


def classify_term_task(
    task: dict,
    policy: dict,
    ambiguous_untyped_source_ids: set[str] | None = None,
) -> dict[str, object]:
    """term task를 단일 연결·문맥 선택·Term-only로 분류한다."""
    triage_policy = policy["entity_resolution"]["semantic_review"][
        "deterministic_triage"
    ]
    dispositions = triage_policy["dispositions"]
    candidate_components = build_exact_candidate_components(
        task,
        policy,
        ambiguous_untyped_source_ids,
    )
    blocked_source_record_ids = sorted(
        {
            str(candidate.get("source_record_id") or "")
            for candidate in task.get("source_candidates", [])
            if (
                ambiguous_untyped_source_ids
                and str(candidate.get("source_record_id") or "")
                in ambiguous_untyped_source_ids
            )
        }
    )
    disposition = dispositions["term_only"]
    reason_code = triage_policy["reason_codes"]["insufficient_code_evidence"]
    if blocked_source_record_ids:
        reason_code = triage_policy["reason_codes"][
            "source_entity_type_ambiguous"
        ]
    if len(candidate_components) == 1:
        disposition = dispositions["single_candidate"]
        reason_code = triage_policy["reason_codes"][
            "single_exact_official_candidate"
        ]
    elif len(candidate_components) >= int(
        triage_policy["minimum_competing_alternative_count"]
    ):
        disposition = dispositions["multiple_candidates"]
        reason_code = triage_policy["reason_codes"]["exact_name_ambiguity"]

    return {
        "term_review_task_id": task["term_review_task_id"],
        "resolution_case_id": task["resolution_case_id"],
        "canonical_term": task["canonical_term"],
        "category": task["category"],
        "problem_count": get_problem_count(task),
        "candidate_count": len(task.get("source_candidates", [])),
        "exact_candidate_component_count": len(candidate_components),
        "ambiguous_source_type_count": len(blocked_source_record_ids),
        "ambiguous_source_record_ids": blocked_source_record_ids,
        "disposition": disposition,
        "reason_code": reason_code,
        "candidate_components": candidate_components,
    }


def build_code_decision(
    task: dict,
    triage_row: dict[str, object],
    policy: dict,
) -> dict:
    """코드 승인 결과를 기존 semantic gate가 다시 검증할 결정 형식으로 만든다."""
    semantic_policy = policy["entity_resolution"]["semantic_review"]
    triage_policy = semantic_policy["deterministic_triage"]
    components = [
        sorted(
            {
                str(candidate_id)
                for candidate_id in component
            }
        )
        for component in triage_row["candidate_components"]
    ]
    identity_member_ids = {
        str(candidate_id)
        for component in components
        for candidate_id in component
    }
    rejected_sources = [
        {
            "source_candidate_id": str(candidate["source_candidate_id"]),
            "reason": triage_policy["rejected_candidate_reason"],
        }
        for candidate in task.get("source_candidates", [])
        if str(candidate["source_candidate_id"]) not in identity_member_ids
    ]
    return {
        "term_review_task_id": task["term_review_task_id"],
        "resolution_case_id": task["resolution_case_id"],
        "decision_status": semantic_policy["decision_status_input"],
        "review_model": triage_policy["review_model"],
        "prompt_version": semantic_policy["prompt_version"],
        "proposed_alternatives": [
            {
                "display_name": task["canonical_term"],
                "entity_type": task["entity_type_proposal"],
                "identity_member_source_candidate_ids": member_ids,
                "reason": triage_policy["accepted_alternative_reason"],
            }
            for member_ids in components
        ],
        "proposed_related_entities": [],
        "evidence_only_sources": [],
        "rejected_sources": rejected_sources,
        "ambiguous_sources": [],
        "decision_reason": triage_policy["accepted_decision_reason"],
    }


def triage_term_tasks(
    tasks: list[dict],
    policy: dict,
) -> tuple[pd.DataFrame, list[dict], list[dict]]:
    """전체 task를 분류하고 코드 결정과 문맥 선택 필요 task를 반환한다."""
    triage_policy = policy["entity_resolution"]["semantic_review"][
        "deterministic_triage"
    ]
    dispositions = triage_policy["dispositions"]
    task_by_id = {
        str(task["term_review_task_id"]): task for task in tasks
    }
    ambiguous_untyped_source_ids = collect_ambiguous_untyped_source_ids(
        tasks,
        policy,
    )
    rows = [
        classify_term_task(
            task,
            policy,
            ambiguous_untyped_source_ids,
        )
        for task in tasks
    ]
    code_decisions = [
        build_code_decision(
            task_by_id[str(row["term_review_task_id"])],
            row,
            policy,
        )
        for row in rows
        if row["disposition"] != dispositions["term_only"]
    ]
    context_required_tasks = [
        task_by_id[str(row["term_review_task_id"])]
        for row in rows
        if row["disposition"] == dispositions["multiple_candidates"]
    ]
    context_required_tasks.sort(
        key=lambda task: (
            -get_problem_count(task),
            len(dumps(task, ensure_ascii=False)),
            str(task["canonical_term"]),
        )
    )
    columns = [
        "term_review_task_id",
        "resolution_case_id",
        "canonical_term",
        "category",
        "problem_count",
        "candidate_count",
        "exact_candidate_component_count",
        "ambiguous_source_type_count",
        "ambiguous_source_record_ids",
        "disposition",
        "reason_code",
        "candidate_components",
    ]
    return (
        pd.DataFrame(rows, columns=columns),
        code_decisions,
        context_required_tasks,
    )


def resolve_llm_task_limit(
    requested_limit: int,
    executor_policy: dict,
) -> int:
    """명시 limit이 없으면 비용 보호용 기본 limit을 사용한다."""
    if requested_limit < 0:
        raise ValueError("LLM 실행 limit은 0 이상이어야 합니다.")
    if requested_limit > 0:
        return requested_limit
    return int(executor_policy["default_task_limit"])


def select_budgeted_tasks(
    tasks: list[dict],
    requested_limit: int,
    executor_policy: dict,
) -> tuple[list[dict], int]:
    """비용 한도 안에서 우선순위가 높은 task만 선택한다."""
    effective_limit = resolve_llm_task_limit(
        requested_limit,
        executor_policy,
    )
    if effective_limit <= 0:
        return [], effective_limit
    return tasks[:effective_limit], effective_limit


def write_triage_outputs(
    triage_table: pd.DataFrame,
    context_required_tasks: list[dict],
    output_dir: str,
    policy: dict,
) -> dict[str, str]:
    """triage 결과와 LLM 후보 목록을 감사 가능한 파일로 저장한다."""
    from entity_resolution.semantic_review import write_jsonl

    triage_policy = policy["entity_resolution"]["semantic_review"][
        "deterministic_triage"
    ]
    output_files = triage_policy["output_files"]
    output_directory = Path(output_dir)
    output_directory.mkdir(parents=True, exist_ok=True)
    report_path = output_directory / output_files["report"]
    context_task_path = output_directory / output_files["context_tasks"]
    output_table = triage_table.copy()
    output_table["candidate_components"] = output_table[
        "candidate_components"
    ].map(lambda value: dumps(value, ensure_ascii=False))
    output_table["ambiguous_source_record_ids"] = output_table[
        "ambiguous_source_record_ids"
    ].map(lambda value: dumps(value, ensure_ascii=False))
    output_table.to_csv(report_path, index=False, encoding="utf-8-sig")
    write_jsonl(context_required_tasks, str(context_task_path))
    return {
        "report": str(report_path),
        "context_tasks": str(context_task_path),
    }
