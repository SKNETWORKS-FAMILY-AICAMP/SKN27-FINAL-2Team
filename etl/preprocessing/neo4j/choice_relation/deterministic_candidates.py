from __future__ import annotations

import re
from collections import Counter
from hashlib import new as new_hash
from itertools import combinations
from json import dumps, loads
from pathlib import Path

import pandas as pd

from common import load_policy_file, normalize_history_term


def load_exam_relation_candidate_policy(policy_path: str) -> dict:
    """기출 관계 후보 정책을 읽고 필수 구성을 검사한다."""
    policy = load_policy_file(Path(policy_path))
    if "exam_relation_candidates" not in policy:
        raise ValueError("exam_relation_candidates 정책이 없습니다.")
    candidate_policy = policy["exam_relation_candidates"]
    required_fields = {
        "policy_version",
        "supported_data_sources",
        "expected_choice_count",
        "minimum_term_length",
        "maximum_entities_per_segment",
        "accepted_link_status",
        "truth_statuses",
        "question_task_truth_policy",
        "claim_roles",
        "candidate_statuses",
        "claim_shape_patterns",
        "default_claim_shape",
        "relationship_trigger_rules",
        "identifier",
        "outputs",
    }
    missing_fields = required_fields.difference(candidate_policy)
    if missing_fields:
        missing_text = ", ".join(sorted(missing_fields))
        raise ValueError(f"기출 관계 후보 정책 필드가 없습니다: {missing_text}")
    return policy


def create_candidate_id(
    prefix: str,
    values: list[str],
    policy: dict,
) -> str:
    """입력값과 정책 버전에 고정되는 관계 후보 ID를 만든다."""
    identifier = policy["exam_relation_candidates"]["identifier"]
    hasher = new_hash(str(identifier["hash_algorithm"]))
    source = "|".join(
        [*values, policy["exam_relation_candidates"]["policy_version"]]
    )
    hasher.update(source.encode("utf-8"))
    digest_length = int(identifier["digest_length"])
    return f"{prefix}{hasher.hexdigest()[:digest_length]}"


def parse_json_list(value: object) -> list[str]:
    """CSV의 JSON 배열을 문자열 목록으로 읽는다."""
    if value is None or str(value).strip() == "":
        return []
    parsed = loads(str(value))
    if not isinstance(parsed, list):
        raise ValueError(f"JSON 배열이 필요합니다: {value}")
    return [str(item) for item in parsed if str(item)]


def classify_claim_shape(text: str, policy: dict) -> str:
    """선지를 완전 문장·매핑·장면·명사 조각으로 분류한다."""
    candidate_policy = policy["exam_relation_candidates"]
    for rule in candidate_policy["claim_shape_patterns"]:
        if re.search(str(rule["pattern"]), text):
            return str(rule["claim_shape"])
    return str(candidate_policy["default_claim_shape"])


def collect_predicate_families(text: str, policy: dict) -> list[str]:
    """설정된 서술어 표지를 관계 계열로 변환한다."""
    families: list[str] = []
    for rule in policy["exam_relation_candidates"][
        "relationship_trigger_rules"
    ]:
        patterns = [str(pattern) for pattern in rule["patterns"]]
        family_matched = False
        for pattern in patterns:
            match_start = text.find(pattern)
            while match_start >= 0:
                if (
                    match_start == 0
                    or not text[match_start - 1].isalnum()
                ):
                    family_matched = True
                    break
                match_start = text.find(pattern, match_start + 1)
            if family_matched:
                break
        if family_matched:
            families.append(str(rule["predicate_family"]))
    return sorted(set(families))


def classify_choice_context(
    question_task: str,
    is_answer_key: bool,
    policy: dict,
) -> tuple[str, str, str]:
    """문항 유형과 정답 여부로 선지의 문항 내 진릿값을 정한다."""
    candidate_policy = policy["exam_relation_candidates"]
    task_policy = candidate_policy["question_task_truth_policy"].get(
        question_task
    )
    if not task_policy:
        return (
            "UNSUPPORTED_TASK",
            "CONTEXT_DEPENDENT",
            candidate_policy["claim_roles"]["context_dependent"],
        )
    truth_key = "non_answer_truth"
    if is_answer_key:
        truth_key = "answer_truth"
    truth_status = str(task_policy[truth_key])
    role_key = "context_dependent"
    truth_statuses = candidate_policy["truth_statuses"]
    if truth_status == truth_statuses["true"]:
        role_key = "contextually_true"
    elif truth_status == truth_statuses["false"]:
        role_key = "contextually_false"
    return (
        str(task_policy["polarity"]),
        truth_status,
        str(candidate_policy["claim_roles"][role_key]),
    )


def collect_problem_assignments(
    resolution_cases: pd.DataFrame,
    final_assignments: pd.DataFrame,
) -> dict[str, list[dict]]:
    """문항별 추출 용어와 최종 Canonical 배정을 결합한다."""
    case_by_id = {
        str(row["resolution_case_id"]): row
        for row in resolution_cases.to_dict("records")
    }
    assignments_by_problem: dict[str, list[dict]] = {}
    for assignment in final_assignments.to_dict("records"):
        case_id = str(assignment["resolution_case_id"])
        case = case_by_id.get(case_id)
        if not case:
            continue
        term = str(case["canonical_term"])
        assignments_by_problem.setdefault(
            str(assignment["problem_id"]),
            [],
        ).append(
            {
                "resolution_case_id": case_id,
                "term": term,
                "normalized_term": normalize_history_term(term),
                "category": str(case["category"]),
                "entity_type": str(case["entity_type_proposal"]),
                "link_status": str(assignment["link_status"]),
                "canonical_ids": parse_json_list(
                    assignment["canonical_ids_json"]
                ),
            }
        )
    for assignments in assignments_by_problem.values():
        assignments.sort(
            key=lambda row: (
                -len(str(row["normalized_term"])),
                str(row["term"]),
            )
        )
    return assignments_by_problem


def match_segment_terms(
    text: str,
    assignments: list[dict],
    policy: dict,
) -> tuple[list[dict], list[str], list[str]]:
    """한 구간에 실제 등장하는 추출 용어와 Canonical을 찾는다."""
    minimum_length = int(
        policy["exam_relation_candidates"]["minimum_term_length"]
    )
    normalized_text = normalize_history_term(text)
    matched_assignments: list[dict] = []
    accepted_canonical_ids: set[str] = set()
    unresolved_terms: set[str] = set()
    observed_case_ids: set[str] = set()
    accepted_link_status = str(
        policy["exam_relation_candidates"]["accepted_link_status"]
    )
    for assignment in assignments:
        normalized_term = str(assignment["normalized_term"])
        if len(normalized_term) < minimum_length:
            continue
        if normalized_term not in normalized_text:
            continue
        case_id = str(assignment["resolution_case_id"])
        if case_id in observed_case_ids:
            continue
        observed_case_ids.add(case_id)
        matched_assignments.append(assignment)
        if str(assignment["link_status"]) == accepted_link_status:
            accepted_canonical_ids.update(assignment["canonical_ids"])
        elif str(assignment["link_status"]) != accepted_link_status:
            unresolved_terms.add(str(assignment["term"]))
    return (
        matched_assignments,
        sorted(accepted_canonical_ids),
        sorted(unresolved_terms),
    )


def build_segment_row(
    problem: dict,
    segment_type: str,
    segment_index: int,
    text: str,
    is_answer_key: bool | None,
    assignments: list[dict],
    policy: dict,
) -> dict:
    """제시문 또는 선지를 관계 추출용 claim 행으로 만든다."""
    candidate_policy = policy["exam_relation_candidates"]
    problem_id = str(problem["problem_id"])
    identifier = candidate_policy["identifier"]
    segment_id = create_candidate_id(
        str(identifier["segment_prefix"]),
        [problem_id, segment_type, str(segment_index)],
        policy,
    )
    polarity = "SOURCE_CONTEXT"
    truth_status = "CONTEXT_DEPENDENT"
    claim_role = str(candidate_policy["claim_roles"]["material"])
    if segment_type == "CHOICE":
        polarity, truth_status, claim_role = classify_choice_context(
            str(problem.get("question_task") or ""),
            bool(is_answer_key),
            policy,
        )
    matched, canonical_ids, unresolved_terms = match_segment_terms(
        text,
        assignments,
        policy,
    )
    predicate_families = collect_predicate_families(text, policy)
    matched_terms = sorted(
        {str(assignment["term"]) for assignment in matched}
    )
    matched_case_ids = sorted(
        {
            str(assignment["resolution_case_id"])
            for assignment in matched
        }
    )
    matched_entity_types = sorted(
        {
            str(assignment["entity_type"])
            for assignment in matched
            if assignment["canonical_ids"]
        }
    )
    answer_key_value: str | bool = ""
    if is_answer_key is not None:
        answer_key_value = bool(is_answer_key)
    return {
        "claim_segment_id": segment_id,
        "problem_id": problem_id,
        "data_source": str(problem.get("data_source") or ""),
        "question_task": str(problem.get("question_task") or ""),
        "question_polarity": polarity,
        "segment_type": segment_type,
        "segment_index": segment_index,
        "is_answer_key": answer_key_value,
        "contextual_truth_status": truth_status,
        "claim_role": claim_role,
        "claim_shape": classify_claim_shape(text, policy),
        "text": text,
        "matched_term_count": len(matched_terms),
        "matched_terms_json": dumps(
            matched_terms,
            ensure_ascii=False,
        ),
        "matched_resolution_case_ids_json": dumps(
            matched_case_ids,
            ensure_ascii=False,
        ),
        "accepted_canonical_count": len(canonical_ids),
        "accepted_canonical_ids_json": dumps(
            canonical_ids,
            ensure_ascii=False,
        ),
        "matched_entity_types_json": dumps(
            matched_entity_types,
            ensure_ascii=False,
        ),
        "unresolved_term_count": len(unresolved_terms),
        "unresolved_terms_json": dumps(
            unresolved_terms,
            ensure_ascii=False,
        ),
        "predicate_family_count": len(predicate_families),
        "predicate_families_json": dumps(
            predicate_families,
            ensure_ascii=False,
        ),
        "topic_hint": str(problem.get("topic") or ""),
        "policy_version": candidate_policy["policy_version"],
    }


def select_candidate_status(
    segment: dict,
    candidate_kind: str,
    policy: dict,
) -> str:
    """claim 역할에 맞는 안전한 후보 상태를 정한다."""
    candidate_policy = policy["exam_relation_candidates"]
    statuses = candidate_policy["candidate_statuses"]
    if segment["claim_role"] == candidate_policy["claim_roles"][
        "contextually_true"
    ]:
        if candidate_kind == "RELATION_FRAGMENT_NEEDS_ENDPOINT":
            return str(statuses["target_resolution_required"])
        return str(statuses["official_corroboration_required"])
    if segment["claim_role"] == candidate_policy["claim_roles"][
        "contextually_false"
    ]:
        return str(statuses["blocked_false_context"])
    return str(statuses["discovery_only"])


def build_relation_candidate_rows(
    segment: dict,
    policy: dict,
) -> list[dict]:
    """구간의 Canonical 쌍 또는 미완성 관계 조각을 후보로 만든다."""
    candidate_policy = policy["exam_relation_candidates"]
    identifier = candidate_policy["identifier"]
    canonical_ids = parse_json_list(
        segment["accepted_canonical_ids_json"]
    )
    predicate_families = parse_json_list(
        segment["predicate_families_json"]
    )
    maximum_entities = int(
        candidate_policy["maximum_entities_per_segment"]
    )
    if len(canonical_ids) > maximum_entities:
        return []
    candidate_kind = "RELATION_FRAGMENT_NEEDS_ENDPOINT"
    endpoint_pairs: list[tuple[str, str]] = [("", "")]
    if len(canonical_ids) == 1:
        endpoint_pairs = [(canonical_ids[0], "")]
    if len(canonical_ids) >= 2:
        endpoint_pairs = list(combinations(sorted(canonical_ids), 2))
        candidate_kind = "ENTITY_COOCCURRENCE_CANDIDATE"
        if predicate_families:
            candidate_kind = "TRIGGERED_ENTITY_PAIR_CANDIDATE"
    elif not predicate_families:
        return []

    candidate_status = select_candidate_status(
        segment,
        candidate_kind,
        policy,
    )
    rows: list[dict] = []
    for pair_index, (start_id, end_id) in enumerate(
        endpoint_pairs,
        start=1,
    ):
        candidate_id = create_candidate_id(
            str(identifier["candidate_prefix"]),
            [
                str(segment["claim_segment_id"]),
                str(pair_index),
                start_id,
                end_id,
            ],
            policy,
        )
        rows.append(
            {
                "exam_relation_candidate_id": candidate_id,
                "claim_segment_id": segment["claim_segment_id"],
                "problem_id": segment["problem_id"],
                "segment_type": segment["segment_type"],
                "segment_index": segment["segment_index"],
                "is_answer_key": segment["is_answer_key"],
                "question_task": segment["question_task"],
                "question_polarity": segment["question_polarity"],
                "contextual_truth_status": segment[
                    "contextual_truth_status"
                ],
                "claim_role": segment["claim_role"],
                "claim_shape": segment["claim_shape"],
                "candidate_kind": candidate_kind,
                "candidate_status": candidate_status,
                "start_canonical_id": start_id,
                "end_canonical_id": end_id,
                "orientation_status": "UNRESOLVED",
                "predicate_families_json": segment[
                    "predicate_families_json"
                ],
                "matched_terms_json": segment["matched_terms_json"],
                "unresolved_terms_json": segment[
                    "unresolved_terms_json"
                ],
                "topic_hint": segment["topic_hint"],
                "evidence_text": segment["text"],
                "must_not_project_as_fact": True,
                "policy_version": candidate_policy["policy_version"],
            }
        )
    return rows


def build_exam_relation_candidate_tables(
    problem_records: list[dict],
    resolution_cases: pd.DataFrame,
    final_assignments: pd.DataFrame,
    policy: dict,
) -> tuple[dict[str, pd.DataFrame], dict[str, object]]:
    """전체 기출의 구간 claim과 코드 기반 관계 후보를 만든다."""
    candidate_policy = policy["exam_relation_candidates"]
    supported_sources = set(candidate_policy["supported_data_sources"])
    expected_choice_count = int(candidate_policy["expected_choice_count"])
    assignments_by_problem = collect_problem_assignments(
        resolution_cases,
        final_assignments,
    )
    segment_rows: list[dict] = []
    relation_rows: list[dict] = []
    excluded_problem_count = 0
    invalid_choice_problem_count = 0

    for problem in problem_records:
        if str(problem.get("data_source") or "") not in supported_sources:
            excluded_problem_count += 1
            continue
        choices = problem.get("choices")
        if not isinstance(choices, list):
            invalid_choice_problem_count += 1
            continue
        if len(choices) != expected_choice_count:
            invalid_choice_problem_count += 1
            continue
        problem_id = str(problem["problem_id"])
        assignments = assignments_by_problem.get(problem_id, [])
        material = str(problem.get("material") or "").strip()
        if material:
            material_row = build_segment_row(
                problem,
                "MATERIAL",
                0,
                material,
                None,
                assignments,
                policy,
            )
            segment_rows.append(material_row)
            relation_rows.extend(
                build_relation_candidate_rows(material_row, policy)
            )
        for choice_index, choice in enumerate(choices, start=1):
            if not isinstance(choice, dict):
                invalid_choice_problem_count += 1
                continue
            choice_text = str(choice.get("content") or "").strip()
            if not choice_text:
                invalid_choice_problem_count += 1
                continue
            choice_row = build_segment_row(
                problem,
                "CHOICE",
                choice_index,
                choice_text,
                bool(choice.get("is_answer") is True),
                assignments,
                policy,
            )
            segment_rows.append(choice_row)
            relation_rows.extend(
                build_relation_candidate_rows(choice_row, policy)
            )

    segment_claims = pd.DataFrame(segment_rows)
    relation_candidates = pd.DataFrame(relation_rows)
    choice_claims = segment_claims[
        segment_claims["segment_type"].eq("CHOICE")
    ]
    problem_polarity = (
        choice_claims[
            ["problem_id", "question_polarity"]
        ]
        .drop_duplicates()
        ["question_polarity"]
        .value_counts()
    )
    relation_status_counts: Counter[str] = Counter()
    relation_kind_counts: Counter[str] = Counter()
    if not relation_candidates.empty:
        relation_status_counts.update(
            str(value)
            for value in relation_candidates["candidate_status"]
        )
        relation_kind_counts.update(
            str(value)
            for value in relation_candidates["candidate_kind"]
        )
    true_choice_candidate_segment_count = 0
    false_choice_candidate_segment_count = 0
    negative_answer_candidate_segment_count = 0
    if not relation_candidates.empty:
        true_choice_candidate_segment_count = int(
            relation_candidates[
                relation_candidates["contextual_truth_status"].eq(
                    candidate_policy["truth_statuses"]["true"]
                )
            ]["claim_segment_id"].nunique()
        )
        false_choice_candidate_segment_count = int(
            relation_candidates[
                relation_candidates["contextual_truth_status"].eq(
                    candidate_policy["truth_statuses"]["false"]
                )
            ]["claim_segment_id"].nunique()
        )
        negative_answer_candidate_segment_count = int(
            relation_candidates[
                relation_candidates["question_polarity"].eq(
                    "NEGATIVE_SELECT"
                )
                & relation_candidates["is_answer_key"].eq(True)
            ]["claim_segment_id"].nunique()
        )
    relation_candidate_problem_count = 0
    if not relation_candidates.empty:
        relation_candidate_problem_count = int(
            relation_candidates["problem_id"].nunique()
        )
    statistics: dict[str, object] = {
        "input_problem_count": len(problem_records),
        "excluded_problem_count": excluded_problem_count,
        "invalid_choice_problem_count": invalid_choice_problem_count,
        "processed_problem_count": int(
            segment_claims["problem_id"].nunique()
        ),
        "segment_claim_count": len(segment_claims),
        "material_claim_count": int(
            segment_claims["segment_type"].eq("MATERIAL").sum()
        ),
        "choice_claim_count": int(
            segment_claims["segment_type"].eq("CHOICE").sum()
        ),
        "question_polarity_counts": {
            str(key): int(value)
            for key, value in problem_polarity.items()
        },
        "contextual_truth_status_counts": {
            str(key): int(value)
            for key, value in segment_claims[
                "contextual_truth_status"
            ].value_counts().items()
        },
        "claim_shape_counts": {
            str(key): int(value)
            for key, value in segment_claims[
                "claim_shape"
            ].value_counts().items()
        },
        "segment_with_accepted_entity_count": int(
            segment_claims["accepted_canonical_count"].gt(0).sum()
        ),
        "segment_with_two_or_more_accepted_entities_count": int(
            segment_claims["accepted_canonical_count"].ge(2).sum()
        ),
        "segment_with_predicate_trigger_count": int(
            segment_claims["predicate_family_count"].gt(0).sum()
        ),
        "relation_candidate_count": len(relation_candidates),
        "relation_candidate_problem_count": (
            relation_candidate_problem_count
        ),
        "relation_candidate_status_counts": dict(
            sorted(relation_status_counts.items())
        ),
        "relation_candidate_kind_counts": dict(
            sorted(relation_kind_counts.items())
        ),
        "true_choice_candidate_segment_count": (
            true_choice_candidate_segment_count
        ),
        "false_choice_candidate_segment_count": (
            false_choice_candidate_segment_count
        ),
        "negative_answer_candidate_segment_count": (
            negative_answer_candidate_segment_count
        ),
        "negative_answer_template_count": int(
            (
                segment_claims["question_polarity"].eq(
                    "NEGATIVE_SELECT"
                )
                & segment_claims["is_answer_key"].eq(True)
                & segment_claims["claim_role"].eq(
                    candidate_policy["claim_roles"][
                        "contextually_false"
                    ]
                )
            ).sum()
        ),
        "llm_used": False,
        "neo4j_load": False,
    }
    return (
        {
            "segment_claims": segment_claims,
            "relation_candidates": relation_candidates,
        },
        statistics,
    )
