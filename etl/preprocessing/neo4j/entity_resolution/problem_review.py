import sys
from argparse import ArgumentParser
from json import dumps, loads
from pathlib import Path
import re

import pandas as pd

sys.path.append(str(Path(__file__).resolve().parent.parent))
sys.path.append(str(Path(__file__).resolve().parent.parent / "terms"))

from common import load_pipeline_policy, normalize_history_term
from entity_resolution.identifiers import create_stable_id
from entity_resolution.semantic_review import (
    load_jsonl,
    load_resolution_package,
    write_jsonl,
)


def collect_alternative_context_features(
    member_ids: list[str],
    feature_by_candidate_id: dict[str, dict],
    entity_anchors_by_candidate_id: dict[str, list[str]],
    canonical_term: str,
) -> dict[str, list[str]]:
    """대안 선택에 쓸 시대·별칭·한자·연도·엔티티 신호를 모은다."""
    normalized_term = normalize_history_term(canonical_term)
    era_tokens: set[str] = set()
    aliases: set[str] = set()
    hanja: set[str] = set()
    years: set[str] = set()
    entity_anchors: set[str] = set()
    for member_id in member_ids:
        feature = feature_by_candidate_id.get(member_id, {})
        era_tokens.update(
            normalize_history_term(value)
            for value in loads(str(feature.get("era_tokens_json") or "[]"))
            if normalize_history_term(value)
        )
        for name in loads(str(feature.get("names_json") or "[]")):
            normalized_name = normalize_history_term(name)
            overlaps_canonical_term = (
                normalized_name in normalized_term
                or normalized_term in normalized_name
            )
            if normalized_name and not overlaps_canonical_term:
                aliases.add(normalized_name)
        hanja.update(
            normalize_history_term(value)
            for value in loads(str(feature.get("hanja_json") or "[]"))
            if normalize_history_term(value)
        )
        for field_name in ["birth_year", "death_year"]:
            year = str(feature.get(field_name) or "")
            if year:
                years.add(year)
        for anchor in entity_anchors_by_candidate_id.get(member_id, []):
            overlaps_canonical_term = (
                anchor in normalized_term
                or normalized_term in anchor
            )
            if not overlaps_canonical_term:
                entity_anchors.add(anchor)
    return {
        "era_tokens": sorted(era_tokens),
        "aliases": sorted(aliases),
        "hanja": sorted(hanja),
        "years": sorted(years),
        "entity_anchors": sorted(entity_anchors),
    }


def collect_verified_entity_anchor_terms(
    alternatives_by_case: dict[str, list[dict]],
    case_by_id: dict[str, dict],
    context_policy: dict,
) -> set[str]:
    """검증 대안이 하나뿐인 구체 엔티티의 기출 용어만 앵커로 허용한다."""
    minimum_length = int(
        context_policy["entity_anchor_minimum_length"]
    )
    allowed_entity_types = set(
        context_policy["entity_anchor_allowed_entity_types"]
    )
    types_requiring_multiple_sources = set(
        context_policy[
            "entity_anchor_types_requiring_multiple_sources"
        ]
    )
    minimum_source_system_count = int(
        context_policy["entity_anchor_minimum_source_system_count"]
    )
    anchor_terms: set[str] = set()
    for case_id, alternatives in alternatives_by_case.items():
        case = case_by_id.get(case_id, {})
        if len(alternatives) != 1:
            continue
        entity_type = str(case.get("entity_type_proposal") or "")
        if entity_type not in allowed_entity_types:
            continue
        if entity_type in types_requiring_multiple_sources:
            source_record_ids = loads(
                str(
                    alternatives[0].get(
                        "identity_member_source_ids_json"
                    )
                    or "[]"
                )
            )
            source_systems = {
                str(source_record_id).split(":", 1)[0]
                for source_record_id in source_record_ids
                if str(source_record_id)
            }
            if len(source_systems) < minimum_source_system_count:
                continue
        normalized_term = normalize_history_term(
            str(case.get("canonical_term") or "")
        )
        if len(normalized_term) >= minimum_length:
            anchor_terms.add(normalized_term)
    return anchor_terms


def collect_candidate_entity_anchors(
    candidate_rows: list[dict],
    anchor_terms: set[str],
    context_policy: dict,
) -> dict[str, list[str]]:
    """공식 설명에 실제로 등장하는 검증 엔티티 앵커를 후보별로 찾는다."""
    if not anchor_terms:
        return {}
    definition_fields_by_source = context_policy[
        "entity_anchor_definition_fields_by_source"
    ]
    sorted_anchor_terms = sorted(
        anchor_terms,
        key=lambda value: (-len(value), value),
    )
    anchor_pattern = re.compile(
        "(?=("
        + "|".join(re.escape(value) for value in sorted_anchor_terms)
        + "))"
    )
    anchors_by_candidate_id: dict[str, list[str]] = {}
    for row in candidate_rows:
        source = str(row.get("source") or "")
        definition_fields = definition_fields_by_source.get(source, [])
        if not definition_fields:
            continue
        try:
            metadata = loads(str(row.get("source_metadata_json") or "{}"))
        except (TypeError, ValueError):
            continue
        definition_text = " ".join(
            str(metadata.get(field_name) or "")
            for field_name in definition_fields
        )
        normalized_definition = normalize_history_term(definition_text)
        if not normalized_definition:
            continue
        matched_anchors = sorted(
            {
                match.group(1)
                for match in anchor_pattern.finditer(normalized_definition)
            }
        )
        if matched_anchors:
            anchors_by_candidate_id[
                str(row["source_candidate_id"])
            ] = matched_anchors
    return anchors_by_candidate_id


def extract_local_term_contexts(
    problem_text: str,
    canonical_term: str,
    context_radius: int,
    boundary_characters: list[str],
) -> list[str]:
    """용어가 나온 문장 경계를 넘지 않는 주변 문맥을 반환한다."""
    local_contexts: list[str] = []
    search_offset = 0
    term_index = problem_text.find(canonical_term, search_offset)
    while term_index >= 0:
        start_index = max(0, term_index - context_radius)
        end_index = min(
            len(problem_text),
            term_index + len(canonical_term) + context_radius,
        )
        for boundary in boundary_characters:
            boundary_index = problem_text.rfind(
                boundary,
                start_index,
                term_index,
            )
            if boundary_index >= start_index:
                start_index = boundary_index + len(boundary)
        boundary_end_indexes = [
            problem_text.find(
                boundary,
                term_index + len(canonical_term),
                end_index,
            )
            for boundary in boundary_characters
        ]
        valid_end_indexes = [
            boundary_index
            for boundary_index in boundary_end_indexes
            if boundary_index >= 0
        ]
        if valid_end_indexes:
            end_index = min(valid_end_indexes)
        local_contexts.append(problem_text[start_index:end_index])
        search_offset = term_index + len(canonical_term)
        term_index = problem_text.find(canonical_term, search_offset)
    return local_contexts


def build_problem_review_inputs(
    resolution_tables: dict[str, pd.DataFrame],
    term_decision_tables: dict[str, pd.DataFrame],
    policy: dict,
) -> tuple[list[dict], pd.DataFrame]:
    """검증된 term 대안으로 문항별 선택 task와 단일 대안 배정을 만든다."""
    resolution_policy = policy["entity_resolution"]
    semantic_policy = resolution_policy["semantic_review"]
    context_policy = semantic_policy["problem_context_rule"]
    identifier_policy = resolution_policy["identifier_policy"]
    cases = resolution_tables["resolution_cases"]
    contexts = resolution_tables["problem_contexts"]
    assignments = resolution_tables["problem_resolution_assignments"]
    reviewed_alternatives = term_decision_tables[
        "reviewed_canonical_alternatives"
    ]
    feature_table = resolution_tables.get(
        "source_candidate_features",
        pd.DataFrame(),
    )
    feature_by_candidate_id = {
        str(row["source_candidate_id"]): row
        for row in feature_table.to_dict("records")
    }
    verified_case_ids = set(
        reviewed_alternatives[
            reviewed_alternatives["verification_status"] == "VERIFIED"
        ]["resolution_case_id"]
    )
    case_by_id = {
        str(row["resolution_case_id"]): row
        for row in cases.to_dict("records")
    }
    context_column = ""
    if "extraction_text" in contexts.columns:
        context_column = "extraction_text"
    elif "full_text" in contexts.columns:
        context_column = "full_text"
    if not context_column:
        raise ValueError("problem_contexts에 extraction_text가 없습니다.")
    context_by_problem = {
        str(row["problem_id"]): str(row[context_column])
        for row in contexts.to_dict("records")
    }
    alternatives_by_case: dict[str, list[dict]] = {}
    for row in reviewed_alternatives.to_dict("records"):
        if row["verification_status"] != "VERIFIED":
            continue
        alternatives_by_case.setdefault(row["resolution_case_id"], []).append(
            row
        )
    for alternative_rows in alternatives_by_case.values():
        alternative_rows.sort(key=lambda row: row["canonical_alternative_id"])
    entity_anchor_terms = collect_verified_entity_anchor_terms(
        alternatives_by_case,
        case_by_id,
        context_policy,
    )
    source_candidate_table = resolution_tables.get(
        "source_record_candidates",
        pd.DataFrame(),
    )
    entity_anchors_by_candidate_id = collect_candidate_entity_anchors(
        source_candidate_table.to_dict("records"),
        entity_anchor_terms,
        context_policy,
    )

    tasks: list[dict] = []
    deterministic_rows: list[dict] = []
    for assignment in assignments.to_dict("records"):
        case_id = assignment["resolution_case_id"]
        if case_id not in verified_case_ids:
            continue
        alternatives = alternatives_by_case.get(case_id, [])
        alternative_items = [
            {
                "canonical_alternative_id": row["canonical_alternative_id"],
                "display_name": row["display_name_proposal"],
                "entity_type": row["entity_type_proposal"],
                "identity_member_source_record_ids": loads(
                    row["identity_member_source_ids_json"]
                ),
                "reason": row["decision_reason"],
                "context_features": collect_alternative_context_features(
                    loads(
                        str(
                            row.get("source_candidate_ids_json")
                            or "[]"
                        )
                    ),
                    feature_by_candidate_id,
                    entity_anchors_by_candidate_id,
                    case_by_id[case_id]["canonical_term"],
                ),
            }
            for row in alternatives
        ]
        if len(alternatives) <= 1:
            selected_ids = [
                row["canonical_alternative_id"] for row in alternatives
            ]
            selection_mode = "NONE"
            if len(selected_ids) == 1:
                selection_mode = "SINGLE"
            deterministic_rows.append(
                {
                    "problem_assignment_id": assignment[
                        "problem_assignment_id"
                    ],
                    "problem_id": assignment["problem_id"],
                    "resolution_case_id": case_id,
                    "selected_canonical_alternative_ids_json": dumps(
                        selected_ids,
                        ensure_ascii=False,
                    ),
                    "selection_mode": selection_mode,
                    "resolution_method": "structured_rule",
                    "verification_status": "VERIFIED",
                    "problem_decision_id": "",
                    "decision_reason": "검증된 canonical 대안 수에 따른 결정적 배정",
                    "resolution_policy_version": policy["policy_version"],
                }
            )
            continue

        case = case_by_id[case_id]
        task_id = create_stable_id(
            identifier_policy["problem_review_task_prefix"],
            [
                assignment["problem_assignment_id"],
                semantic_policy["problem_prompt_version"],
            ],
            identifier_policy,
        )
        tasks.append(
            {
                "problem_review_task_id": task_id,
                "problem_assignment_id": assignment["problem_assignment_id"],
                "problem_id": assignment["problem_id"],
                "resolution_case_id": case_id,
                "canonical_term": case["canonical_term"],
                "category": case["category"],
                "problem_full_text": context_by_problem.get(
                    assignment["problem_id"],
                    "",
                ),
                "canonical_alternatives": alternative_items,
                "required_decision_status": semantic_policy[
                    "decision_status_input"
                ],
                "review_model": semantic_policy["problem_model"]["model"],
                "prompt_version": semantic_policy["problem_prompt_version"],
                "resolution_policy_version": policy["policy_version"],
            }
        )
    columns = [
        "problem_assignment_id",
        "problem_id",
        "resolution_case_id",
        "selected_canonical_alternative_ids_json",
        "selection_mode",
        "resolution_method",
        "verification_status",
        "problem_decision_id",
        "decision_reason",
        "resolution_policy_version",
    ]
    deterministic_df = pd.DataFrame(deterministic_rows, columns=columns)
    return tasks, deterministic_df


def score_problem_context_alternatives(
    task: dict,
    policy: dict,
) -> tuple[str, dict[str, int], dict[str, dict[str, list[str]]]]:
    """대안별 독점 문맥 신호를 점수화하고 안전한 단일 선택만 반환한다."""
    context_policy = policy["entity_resolution"]["semantic_review"][
        "problem_context_rule"
    ]
    problem_text = str(task["problem_full_text"])
    canonical_term = str(task["canonical_term"])
    context_radius = int(context_policy["term_context_radius"])
    local_contexts = extract_local_term_contexts(
        problem_text,
        canonical_term,
        context_radius,
        context_policy["context_boundary_characters"],
    )
    local_context_text = " ".join(local_contexts)
    normalized_text = normalize_history_term(local_context_text)
    signal_weights = {
        signal_type: int(weight)
        for signal_type, weight in context_policy[
            "signal_weights"
        ].items()
    }
    blocked_era_tokens = {
        normalize_history_term(value)
        for value in context_policy["blocked_era_tokens"]
    }
    normalized_canonical_term = normalize_history_term(
        task["canonical_term"]
    )
    minimum_signal_length = int(
        context_policy["minimum_signal_length"]
    )
    value_owners: dict[tuple[str, str], set[str]] = {}
    values_by_alternative: dict[str, dict[str, set[str]]] = {}
    for alternative in task["canonical_alternatives"]:
        alternative_id = str(alternative["canonical_alternative_id"])
        features = alternative.get("context_features", {})
        values_by_signal: dict[str, set[str]] = {}
        for signal_type in signal_weights:
            values = {
                normalize_history_term(value)
                for value in features.get(signal_type, [])
                if len(normalize_history_term(value))
                >= minimum_signal_length
            }
            if signal_type == "era_tokens":
                values.difference_update(blocked_era_tokens)
                values.discard(normalized_canonical_term)
            values_by_signal[signal_type] = values
            for value in values:
                value_owners.setdefault(
                    (signal_type, value),
                    set(),
                ).add(alternative_id)
        values_by_alternative[alternative_id] = values_by_signal

    scores: dict[str, int] = {}
    evidence: dict[str, dict[str, list[str]]] = {}
    for alternative_id, values_by_signal in values_by_alternative.items():
        alternative_score = 0
        alternative_evidence: dict[str, list[str]] = {}
        for signal_type, values in values_by_signal.items():
            def signal_is_in_context(value: str) -> bool:
                if signal_type != "aliases":
                    return value in normalized_text
                alias_pattern = (
                    r"(?<![0-9A-Za-z가-힣\u3400-\u9fff])"
                    + re.escape(value)
                    + r"(?![0-9A-Za-z가-힣\u3400-\u9fff])"
                )
                return re.search(alias_pattern, local_context_text) is not None

            exclusive_matches = sorted(
                value
                for value in values
                if signal_is_in_context(value)
                and len(
                    value_owners.get((signal_type, value), set())
                )
                == 1
            )
            if exclusive_matches:
                alternative_evidence[signal_type] = exclusive_matches
                alternative_score += (
                    len(exclusive_matches) * signal_weights[signal_type]
                )
        scores[alternative_id] = alternative_score
        evidence[alternative_id] = alternative_evidence

    ranked = sorted(
        scores.items(),
        key=lambda item: (-item[1], item[0]),
    )
    if not ranked:
        return "", scores, evidence
    best_id, best_score = ranked[0]
    second_score = 0
    if len(ranked) > 1:
        second_score = ranked[1][1]
    matched_signal_count = sum(
        len(values) for values in evidence[best_id].values()
    )
    if best_score < int(context_policy["minimum_score"]):
        return "", scores, evidence
    if best_score - second_score < int(
        context_policy["minimum_score_margin"]
    ):
        return "", scores, evidence
    if matched_signal_count < int(
        context_policy["minimum_exclusive_signal_count"]
    ):
        return "", scores, evidence
    return best_id, scores, evidence


def resolve_problem_tasks_by_context(
    tasks: list[dict],
    policy: dict,
) -> tuple[list[dict], pd.DataFrame, pd.DataFrame]:
    """경쟁 대안을 코드 문맥 신호로 선택하고 나머지만 LLM 후보로 남긴다."""
    remaining_tasks: list[dict] = []
    assignment_rows: list[dict] = []
    audit_rows: list[dict] = []
    context_policy = policy["entity_resolution"]["semantic_review"][
        "problem_context_rule"
    ]
    for task in tasks:
        selected_id, scores, evidence = score_problem_context_alternatives(
            task,
            policy,
        )
        resolution_status = context_policy["deferred_status"]
        if selected_id:
            resolution_status = context_policy["resolved_status"]
            assignment_rows.append(
                {
                    "problem_assignment_id": task[
                        "problem_assignment_id"
                    ],
                    "problem_id": task["problem_id"],
                    "resolution_case_id": task["resolution_case_id"],
                    "selected_canonical_alternative_ids_json": dumps(
                        [selected_id],
                        ensure_ascii=False,
                    ),
                    "selection_mode": context_policy["selection_mode"],
                    "resolution_method": context_policy[
                        "resolution_method"
                    ],
                    "verification_status": context_policy[
                        "resolved_verification_status"
                    ],
                    "problem_decision_id": "",
                    "decision_reason": context_policy["decision_reason"],
                    "resolution_policy_version": policy["policy_version"],
                }
            )
        elif not selected_id:
            remaining_tasks.append(task)
            assignment_rows.append(
                {
                    "problem_assignment_id": task[
                        "problem_assignment_id"
                    ],
                    "problem_id": task["problem_id"],
                    "resolution_case_id": task["resolution_case_id"],
                    "selected_canonical_alternative_ids_json": dumps(
                        [],
                        ensure_ascii=False,
                    ),
                    "selection_mode": context_policy[
                        "deferred_selection_mode"
                    ],
                    "resolution_method": context_policy[
                        "deferred_resolution_method"
                    ],
                    "verification_status": context_policy[
                        "deferred_verification_status"
                    ],
                    "problem_decision_id": "",
                    "decision_reason": context_policy[
                        "deferred_decision_reason"
                    ],
                    "resolution_policy_version": policy["policy_version"],
                }
            )
        audit_rows.append(
            {
                "problem_review_task_id": task[
                    "problem_review_task_id"
                ],
                "problem_assignment_id": task[
                    "problem_assignment_id"
                ],
                "problem_id": task["problem_id"],
                "resolution_case_id": task["resolution_case_id"],
                "canonical_term": task["canonical_term"],
                "candidate_count": len(task["canonical_alternatives"]),
                "resolution_status": resolution_status,
                "selected_canonical_alternative_id": selected_id,
                "scores_json": dumps(scores, ensure_ascii=False),
                "evidence_json": dumps(evidence, ensure_ascii=False),
                "resolution_policy_version": policy["policy_version"],
            }
        )
    assignment_columns = [
        "problem_assignment_id",
        "problem_id",
        "resolution_case_id",
        "selected_canonical_alternative_ids_json",
        "selection_mode",
        "resolution_method",
        "verification_status",
        "problem_decision_id",
        "decision_reason",
        "resolution_policy_version",
    ]
    audit_columns = [
        "problem_review_task_id",
        "problem_assignment_id",
        "problem_id",
        "resolution_case_id",
        "canonical_term",
        "candidate_count",
        "resolution_status",
        "selected_canonical_alternative_id",
        "scores_json",
        "evidence_json",
        "resolution_policy_version",
    ]
    return (
        remaining_tasks,
        pd.DataFrame(assignment_rows, columns=assignment_columns),
        pd.DataFrame(audit_rows, columns=audit_columns),
    )


def validate_problem_decision_shape(decision: dict) -> list[str]:
    """문항 선택 결정의 핵심 JSON Schema 구조를 검사한다."""
    messages: list[str] = []
    required_strings = [
        "problem_review_task_id",
        "problem_assignment_id",
        "resolution_case_id",
        "decision_status",
        "review_model",
        "prompt_version",
        "selection_mode",
        "reason",
    ]
    for field_name in required_strings:
        if not isinstance(decision.get(field_name), str) or not decision.get(
            field_name
        ):
            messages.append(f"{field_name}: 비어 있지 않은 문자열이 필요합니다.")
    selected_ids = decision.get("selected_canonical_alternative_ids")
    if not isinstance(selected_ids, list):
        messages.append("selected_canonical_alternative_ids: 배열이 필요합니다.")
    return messages


def validate_problem_decisions(
    decisions: list[dict],
    tasks: list[dict],
    deterministic_assignments: pd.DataFrame,
    policy: dict,
) -> dict[str, pd.DataFrame]:
    """문항별 LLM 선택을 검증하고 확정 가능한 배정만 평탄화한다."""
    resolution_policy = policy["entity_resolution"]
    semantic_policy = resolution_policy["semantic_review"]
    identifier_policy = resolution_policy["identifier_policy"]
    task_by_id = {task["problem_review_task_id"]: task for task in tasks}
    decision_rows: list[dict] = []
    model_assignment_ids = {
        str(decision.get("problem_assignment_id") or "")
        for decision in decisions
    }
    verified_rows = [
        row
        for row in deterministic_assignments.to_dict("records")
        if str(row["problem_assignment_id"]) not in model_assignment_ids
    ]
    error_rows: list[dict] = []
    observed_task_ids: set[str] = set()
    allowed_modes = {"SINGLE", "MULTIPLE", "AMBIGUOUS", "NONE"}

    for decision_sequence, decision in enumerate(decisions, start=1):
        task_id = str(decision.get("problem_review_task_id") or "")
        assignment_id = str(decision.get("problem_assignment_id") or "")
        case_id = str(decision.get("resolution_case_id") or "")
        decision_id = create_stable_id(
            identifier_policy["problem_decision_prefix"],
            [
                task_id,
                semantic_policy["problem_prompt_version"],
                str(decision_sequence),
            ],
            identifier_policy,
        )
        invalid = False
        manual_review = False
        shape_errors = validate_problem_decision_shape(decision)
        for message in shape_errors:
            error_rows.append(
                {
                    "problem_decision_id": decision_id,
                    "problem_assignment_id": assignment_id,
                    "resolution_case_id": case_id,
                    "severity": "INVALID",
                    "error_code": "DECISION_SCHEMA_ERROR",
                    "message": message,
                }
            )
        if shape_errors:
            invalid = True
        task = task_by_id.get(task_id)
        if task is None:
            error_rows.append(
                {
                    "problem_decision_id": decision_id,
                    "problem_assignment_id": assignment_id,
                    "resolution_case_id": case_id,
                    "severity": "INVALID",
                    "error_code": "UNKNOWN_PROBLEM_REVIEW_TASK",
                    "message": "등록되지 않은 problem review task입니다.",
                }
            )
            invalid = True
        elif task_id in observed_task_ids:
            error_rows.append(
                {
                    "problem_decision_id": decision_id,
                    "problem_assignment_id": assignment_id,
                    "resolution_case_id": case_id,
                    "severity": "INVALID",
                    "error_code": "DUPLICATE_PROBLEM_DECISION",
                    "message": "동일 task에 대한 결정이 중복되었습니다.",
                }
            )
            invalid = True
        elif task_id not in observed_task_ids:
            observed_task_ids.add(task_id)

        option_ids: set[str] = set()
        if task is not None:
            option_ids = {
                row["canonical_alternative_id"]
                for row in task["canonical_alternatives"]
            }
            if assignment_id != task["problem_assignment_id"] or case_id != task[
                "resolution_case_id"
            ]:
                error_rows.append(
                    {
                        "problem_decision_id": decision_id,
                        "problem_assignment_id": assignment_id,
                        "resolution_case_id": case_id,
                        "severity": "INVALID",
                        "error_code": "TASK_REFERENCE_MISMATCH",
                        "message": "task와 결정의 assignment 또는 case ID가 다릅니다.",
                    }
                )
                invalid = True
        if decision.get("decision_status") != semantic_policy[
            "decision_status_input"
        ]:
            invalid = True
            error_rows.append(
                {
                    "problem_decision_id": decision_id,
                    "problem_assignment_id": assignment_id,
                    "resolution_case_id": case_id,
                    "severity": "INVALID",
                    "error_code": "INVALID_DECISION_STATUS",
                    "message": "LLM 입력 결정 상태는 PROPOSED여야 합니다.",
                }
            )
        if decision.get("review_model") != semantic_policy["problem_model"][
            "model"
        ]:
            invalid = True
            error_rows.append(
                {
                    "problem_decision_id": decision_id,
                    "problem_assignment_id": assignment_id,
                    "resolution_case_id": case_id,
                    "severity": "INVALID",
                    "error_code": "REVIEW_MODEL_MISMATCH",
                    "message": "정책에 지정된 problem review model이 아닙니다.",
                }
            )
        if decision.get("prompt_version") != semantic_policy[
            "problem_prompt_version"
        ]:
            invalid = True
            error_rows.append(
                {
                    "problem_decision_id": decision_id,
                    "problem_assignment_id": assignment_id,
                    "resolution_case_id": case_id,
                    "severity": "INVALID",
                    "error_code": "PROMPT_VERSION_MISMATCH",
                    "message": "task와 결정의 prompt version이 다릅니다.",
                }
            )

        selected_ids = decision.get("selected_canonical_alternative_ids")
        if not isinstance(selected_ids, list):
            selected_ids = []
        selected_id_set = set(selected_ids)
        if len(selected_id_set) != len(selected_ids):
            invalid = True
            error_rows.append(
                {
                    "problem_decision_id": decision_id,
                    "problem_assignment_id": assignment_id,
                    "resolution_case_id": case_id,
                    "severity": "INVALID",
                    "error_code": "DUPLICATE_ALTERNATIVE_SELECTION",
                    "message": "같은 canonical 대안을 중복 선택했습니다.",
                }
            )
        unknown_ids = selected_id_set.difference(option_ids)
        if unknown_ids:
            invalid = True
            error_rows.append(
                {
                    "problem_decision_id": decision_id,
                    "problem_assignment_id": assignment_id,
                    "resolution_case_id": case_id,
                    "severity": "INVALID",
                    "error_code": "UNKNOWN_CANONICAL_ALTERNATIVE",
                    "message": dumps(sorted(unknown_ids), ensure_ascii=False),
                }
            )
        selection_mode = decision.get("selection_mode", "")
        if selection_mode not in allowed_modes:
            invalid = True
            error_rows.append(
                {
                    "problem_decision_id": decision_id,
                    "problem_assignment_id": assignment_id,
                    "resolution_case_id": case_id,
                    "severity": "INVALID",
                    "error_code": "INVALID_SELECTION_MODE",
                    "message": selection_mode,
                }
            )
        cardinality_invalid = False
        if selection_mode == "SINGLE" and len(selected_ids) != 1:
            cardinality_invalid = True
        elif selection_mode == "MULTIPLE" and len(selected_ids) < 2:
            cardinality_invalid = True
        elif selection_mode == "NONE" and selected_ids:
            cardinality_invalid = True
        elif selection_mode == "AMBIGUOUS":
            manual_review = True
            error_rows.append(
                {
                    "problem_decision_id": decision_id,
                    "problem_assignment_id": assignment_id,
                    "resolution_case_id": case_id,
                    "severity": "NEEDS_MANUAL_REVIEW",
                    "error_code": "AMBIGUOUS_PROBLEM_SELECTION",
                    "message": "문항 문맥으로 canonical 대안을 확정하지 못했습니다.",
                }
            )
        if cardinality_invalid:
            invalid = True
            error_rows.append(
                {
                    "problem_decision_id": decision_id,
                    "problem_assignment_id": assignment_id,
                    "resolution_case_id": case_id,
                    "severity": "INVALID",
                    "error_code": "SELECTION_MODE_CARDINALITY_MISMATCH",
                    "message": "selection_mode과 선택한 대안 수가 맞지 않습니다.",
                }
            )
        verification_status = "VERIFIED"
        if manual_review:
            verification_status = "NEEDS_MANUAL_REVIEW"
        if invalid:
            verification_status = "INVALID"
        decision_rows.append(
            {
                "problem_decision_id": decision_id,
                "problem_review_task_id": task_id,
                "problem_assignment_id": assignment_id,
                "resolution_case_id": case_id,
                "selection_mode": selection_mode,
                "selected_canonical_alternative_ids_json": dumps(
                    selected_ids,
                    ensure_ascii=False,
                ),
                "input_decision_status": decision.get("decision_status", ""),
                "verification_status": verification_status,
                "decision_reason": decision.get("reason", ""),
                "review_model": decision.get("review_model", ""),
                "prompt_version": decision.get("prompt_version", ""),
                "resolution_policy_version": policy["policy_version"],
            }
        )
        if verification_status != "VERIFIED":
            continue
        verified_rows.append(
            {
                "problem_assignment_id": assignment_id,
                "problem_id": task["problem_id"],
                "resolution_case_id": case_id,
                "selected_canonical_alternative_ids_json": dumps(
                    selected_ids,
                    ensure_ascii=False,
                ),
                "selection_mode": selection_mode,
                "resolution_method": "llm_per_problem",
                "verification_status": "VERIFIED",
                "problem_decision_id": decision_id,
                "decision_reason": decision.get("reason", ""),
                "resolution_policy_version": policy["policy_version"],
            }
        )

    decision_columns = [
        "problem_decision_id",
        "problem_review_task_id",
        "problem_assignment_id",
        "resolution_case_id",
        "selection_mode",
        "selected_canonical_alternative_ids_json",
        "input_decision_status",
        "verification_status",
        "decision_reason",
        "review_model",
        "prompt_version",
        "resolution_policy_version",
    ]
    assignment_columns = list(deterministic_assignments.columns)
    error_columns = [
        "problem_decision_id",
        "problem_assignment_id",
        "resolution_case_id",
        "severity",
        "error_code",
        "message",
    ]
    return {
        "problem_resolution_decisions": pd.DataFrame(
            decision_rows,
            columns=decision_columns,
        ),
        "verified_problem_assignments": pd.DataFrame(
            verified_rows,
            columns=assignment_columns,
        ),
        "problem_decision_validation_errors": pd.DataFrame(
            error_rows,
            columns=error_columns,
        ),
    }


def write_problem_decision_tables(
    tables: dict[str, pd.DataFrame],
    output_dir: str,
    policy: dict,
) -> dict[str, str]:
    """문항 선택 gate 결과를 정책 파일명으로 저장한다."""
    output_directory = Path(output_dir)
    output_directory.mkdir(parents=True, exist_ok=True)
    output_files = policy["entity_resolution"]["semantic_review"][
        "problem_decision_output_files"
    ]
    written: dict[str, str] = {}
    for table_name, table in tables.items():
        output_path = output_directory / output_files[table_name]
        output_path.parent.mkdir(parents=True, exist_ok=True)
        table.to_csv(output_path, index=False, encoding="utf-8-sig")
        written[table_name] = str(output_path)
    return written


def load_term_decision_tables(
    review_dir: str,
    policy: dict,
) -> dict[str, pd.DataFrame]:
    """term-level gate가 생성한 CSV 중 문항 배정에 필요한 테이블을 읽는다."""
    review_directory = Path(review_dir)
    output_files = policy["entity_resolution"]["semantic_review"][
        "term_decision_output_files"
    ]
    table_names = [
        "term_resolution_decisions",
        "reviewed_canonical_alternatives",
    ]
    tables: dict[str, pd.DataFrame] = {}
    for table_name in table_names:
        input_path = review_directory / output_files[table_name]
        if not input_path.is_file():
            raise FileNotFoundError(f"term decision CSV를 찾을 수 없습니다: {input_path}")
        tables[table_name] = pd.read_csv(input_path, dtype=str).fillna("")
    return tables


if __name__ == "__main__":
    parser = ArgumentParser(
        description="검증된 canonical 대안을 기출문항별로 선택하는 review task·gate"
    )
    parser.add_argument("resolution_dir", help="ER staging CSV 폴더")
    parser.add_argument("review_dir", help="term·problem review 출력 폴더")
    parser.add_argument(
        "--decisions",
        default="",
        help="검증할 problem_resolution_decisions.jsonl 경로",
    )
    parser.add_argument(
        "--policy",
        default=str(
            Path(__file__).resolve().parent.parent
            / "config"
            / "resolution_policy.json"
        ),
        help="Entity Resolution 정책 JSON 경로",
    )
    cli_args = parser.parse_args()
    pipeline_policy = load_pipeline_policy(cli_args.policy)
    base_tables = load_resolution_package(
        cli_args.resolution_dir,
        pipeline_policy,
    )
    reviewed_term_tables = load_term_decision_tables(
        cli_args.review_dir,
        pipeline_policy,
    )
    problem_tasks, deterministic_assignments = build_problem_review_inputs(
        base_tables,
        reviewed_term_tables,
        pipeline_policy,
    )
    initial_problem_task_count = len(problem_tasks)
    (
        problem_tasks,
        context_assignments,
        context_audit,
    ) = resolve_problem_tasks_by_context(
        problem_tasks,
        pipeline_policy,
    )
    deterministic_assignments = pd.concat(
        [deterministic_assignments, context_assignments],
        ignore_index=True,
    )
    semantic_policy = pipeline_policy["entity_resolution"]["semantic_review"]
    context_audit_path = Path(cli_args.review_dir) / semantic_policy[
        "problem_context_rule"
    ]["audit_file"]
    context_audit.to_csv(
        context_audit_path,
        index=False,
        encoding="utf-8-sig",
    )
    resolved_status = semantic_policy["problem_context_rule"][
        "resolved_status"
    ]
    code_resolved_count = int(
        (context_audit["resolution_status"] == resolved_status).sum()
    )
    task_path = Path(cli_args.review_dir) / semantic_policy[
        "problem_task_file"
    ]
    write_jsonl(problem_tasks, str(task_path))
    print(
        f"initial problem review task: {initial_problem_task_count}건, "
        f"code resolved: {code_resolved_count}건, "
        f"remaining task: {len(problem_tasks)}건, "
        f"deterministic assignment: {len(deterministic_assignments)}건"
    )
    proposed_decisions: list[dict] = []
    if cli_args.decisions:
        proposed_decisions = load_jsonl(cli_args.decisions)
    decision_tables = validate_problem_decisions(
        proposed_decisions,
        problem_tasks,
        deterministic_assignments,
        pipeline_policy,
    )
    paths = write_problem_decision_tables(
        decision_tables,
        cli_args.review_dir,
        pipeline_policy,
    )
    print(dumps(paths, ensure_ascii=False, indent=2))
