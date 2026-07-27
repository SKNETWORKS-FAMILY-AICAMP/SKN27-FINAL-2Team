from __future__ import annotations

from collections import Counter
from json import dumps, loads
from pathlib import Path

import pandas as pd

from common import load_policy_file, normalize_history_term


def load_exam_match_recovery_policy(policy_path: str) -> dict:
    """기출 용어 재매칭 정책을 읽고 필수 구성을 검증한다."""
    policy = load_policy_file(Path(policy_path))
    if "exam_match_recovery" not in policy:
        raise ValueError("exam_match_recovery 정책이 없습니다.")
    recovery_policy = policy["exam_match_recovery"]
    required_sections = {
        "policy_version",
        "active_lifecycle_status",
        "accepted_link_status",
        "ambiguous_link_status",
        "verified_era_status",
        "allowed_sources",
        "allowed_category_compatibility",
        "exact_retrieval_method",
        "minimum_retrieval_score",
        "maximum_neighbor_era_count",
        "minimum_dominant_era_votes",
        "minimum_era_vote_margin",
        "decision_statuses",
        "selection_methods",
        "outputs",
    }
    missing_sections = required_sections.difference(recovery_policy)
    if missing_sections:
        missing_text = ", ".join(sorted(missing_sections))
        raise ValueError(f"재매칭 정책 필드가 없습니다: {missing_text}")
    return policy


def parse_json_list(value: object) -> list[str]:
    """CSV의 JSON 배열을 문자열 목록으로 안전하게 읽는다."""
    if value is None or str(value).strip() == "":
        return []
    parsed = loads(str(value))
    if not isinstance(parsed, list):
        raise ValueError(f"JSON 배열이 필요합니다: {value}")
    return [str(item) for item in parsed if str(item)]


def collect_exact_source_records(
    resolution_cases: pd.DataFrame,
    source_candidates: pd.DataFrame,
    policy: dict,
) -> dict[str, set[str]]:
    """공식 출처의 정확한 이름·타입 호환 후보만 case별로 모은다."""
    recovery_policy = policy["exam_match_recovery"]
    case_by_id = {
        str(row["resolution_case_id"]): row
        for row in resolution_cases.to_dict("records")
    }
    allowed_sources = set(recovery_policy["allowed_sources"])
    allowed_compatibility = set(
        recovery_policy["allowed_category_compatibility"]
    )
    exact_method = str(recovery_policy["exact_retrieval_method"])
    minimum_score = float(recovery_policy["minimum_retrieval_score"])
    source_records_by_case: dict[str, set[str]] = {}

    for candidate in source_candidates.to_dict("records"):
        case_id = str(candidate["resolution_case_id"])
        case = case_by_id.get(case_id)
        if not case:
            continue
        if str(candidate["source"]) not in allowed_sources:
            continue
        if (
            str(candidate["category_compatibility"])
            not in allowed_compatibility
        ):
            continue
        retrieval_methods = set(
            parse_json_list(candidate["retrieval_methods_json"])
        )
        if exact_method not in retrieval_methods:
            continue
        retrieval_score = float(candidate["retrieval_score"] or 0)
        if retrieval_score < minimum_score:
            continue
        matched_name = normalize_history_term(candidate["matched_name"])
        canonical_term = normalize_history_term(case["canonical_term"])
        if matched_name != canonical_term:
            continue
        source_records_by_case.setdefault(case_id, set()).add(
            str(candidate["source_record_id"])
        )
    return source_records_by_case


def collect_eligible_canonicals(
    resolution_cases: pd.DataFrame,
    canonical_registry: pd.DataFrame,
    exact_source_records_by_case: dict[str, set[str]],
    policy: dict,
) -> dict[str, list[dict]]:
    """case와 정확한 공식 출처를 공유하는 활성 Canonical만 남긴다."""
    recovery_policy = policy["exam_match_recovery"]
    active_status = str(recovery_policy["active_lifecycle_status"])
    case_by_id = {
        str(row["resolution_case_id"]): row
        for row in resolution_cases.to_dict("records")
    }
    canonicals_by_case: dict[str, list[dict]] = {}

    for canonical in canonical_registry.to_dict("records"):
        if str(canonical["lifecycle_status"]) != active_status:
            continue
        member_source_ids = set(
            parse_json_list(canonical["identity_member_source_ids_json"])
        )
        for case_id in parse_json_list(
            canonical["resolution_case_ids_json"]
        ):
            case = case_by_id.get(case_id)
            if not case:
                continue
            if (
                str(canonical["entity_type"])
                != str(case["entity_type_proposal"])
            ):
                continue
            exact_source_ids = exact_source_records_by_case.get(
                case_id,
                set(),
            )
            matched_source_ids = member_source_ids.intersection(
                exact_source_ids
            )
            if not matched_source_ids:
                continue
            candidate = dict(canonical)
            candidate["matched_exact_source_ids"] = sorted(
                matched_source_ids
            )
            canonicals_by_case.setdefault(case_id, []).append(candidate)

    for candidate_rows in canonicals_by_case.values():
        candidate_rows.sort(key=lambda row: str(row["canonical_id"]))
    return canonicals_by_case


def collect_canonical_eras(
    canonical_era_relationships: pd.DataFrame,
    policy: dict,
) -> dict[str, set[str]]:
    """검증된 Canonical 시대만 모은다."""
    verified_status = str(
        policy["exam_match_recovery"]["verified_era_status"]
    )
    eras_by_canonical: dict[str, set[str]] = {}
    for relationship in canonical_era_relationships.to_dict("records"):
        if str(relationship["verification_status"]) != verified_status:
            continue
        eras_by_canonical.setdefault(
            str(relationship["canonical_id"]),
            set(),
        ).add(str(relationship["era_id"]))
    return eras_by_canonical


def collect_accepted_entities_by_problem(
    final_assignments: pd.DataFrame,
    policy: dict,
) -> dict[str, set[str]]:
    """현재 확정된 문항별 Canonical을 모은다."""
    accepted_status = str(
        policy["exam_match_recovery"]["accepted_link_status"]
    )
    entities_by_problem: dict[str, set[str]] = {}
    for assignment in final_assignments.to_dict("records"):
        if str(assignment["link_status"]) != accepted_status:
            continue
        problem_id = str(assignment["problem_id"])
        entities_by_problem.setdefault(problem_id, set()).update(
            parse_json_list(assignment["canonical_ids_json"])
        )
    return entities_by_problem


def infer_problem_era(
    accepted_canonical_ids: set[str],
    eras_by_canonical: dict[str, set[str]],
    policy: dict,
) -> tuple[str, int, int, dict[str, int]]:
    """단일 시대를 가진 확정 엔티티들의 다수결로 문항 시대를 추정한다."""
    recovery_policy = policy["exam_match_recovery"]
    maximum_era_count = int(
        recovery_policy["maximum_neighbor_era_count"]
    )
    votes: Counter[str] = Counter()
    for canonical_id in accepted_canonical_ids:
        era_ids = eras_by_canonical.get(canonical_id, set())
        if not era_ids or len(era_ids) > maximum_era_count:
            continue
        for era_id in era_ids:
            votes[era_id] += 1

    ranked = votes.most_common()
    if not ranked:
        return "", 0, 0, {}
    dominant_era_id, dominant_vote_count = ranked[0]
    second_vote_count = 0
    if len(ranked) > 1:
        second_vote_count = ranked[1][1]
    minimum_votes = int(
        recovery_policy["minimum_dominant_era_votes"]
    )
    minimum_margin = int(
        recovery_policy["minimum_era_vote_margin"]
    )
    if dominant_vote_count < minimum_votes:
        return "", dominant_vote_count, second_vote_count, dict(votes)
    if dominant_vote_count - second_vote_count < minimum_margin:
        return "", dominant_vote_count, second_vote_count, dict(votes)
    return (
        dominant_era_id,
        dominant_vote_count,
        second_vote_count,
        dict(votes),
    )


def select_recovery_candidate(
    candidate_rows: list[dict],
    dominant_era_id: str,
    eras_by_canonical: dict[str, set[str]],
    policy: dict,
) -> tuple[str, str, str]:
    """정확명 단일 후보 또는 시대가 유일한 후보만 선택한다."""
    recovery_policy = policy["exam_match_recovery"]
    methods = recovery_policy["selection_methods"]
    statuses = recovery_policy["decision_statuses"]
    if not candidate_rows:
        return (
            "",
            statuses["review_required"],
            methods["no_candidate"],
        )
    if len(candidate_rows) == 1:
        return (
            str(candidate_rows[0]["canonical_id"]),
            statuses["auto_accept"],
            methods["unique_exact"],
        )
    if not dominant_era_id:
        return (
            "",
            statuses["review_required"],
            methods["insufficient_context"],
        )

    era_candidates = [
        row
        for row in candidate_rows
        if dominant_era_id
        in eras_by_canonical.get(str(row["canonical_id"]), set())
    ]
    if len(era_candidates) == 1:
        return (
            str(era_candidates[0]["canonical_id"]),
            statuses["auto_accept"],
            methods["problem_era"],
        )
    if not era_candidates:
        return (
            "",
            statuses["review_required"],
            methods["era_conflict"],
        )
    return (
        "",
        statuses["review_required"],
        methods["duplicate_or_homonym"],
    )


def collect_fact_edges(
    relationships: pd.DataFrame,
    excluded_relation_types: set[str],
    allowed_candidate_statuses: set[str] | None = None,
) -> set[tuple[str, str]]:
    """관계 CSV에서 선택 가능한 Canonical 간선만 읽는다."""
    edges: set[tuple[str, str]] = set()
    for relationship in relationships.to_dict("records"):
        relation_type = str(relationship["relation_type"])
        if relation_type in excluded_relation_types:
            continue
        if allowed_candidate_statuses is not None:
            candidate_status = str(
                relationship.get("candidate_status") or ""
            )
            if candidate_status not in allowed_candidate_statuses:
                continue
        start_id = str(relationship["start_canonical_id"])
        end_id = str(relationship["end_canonical_id"])
        if start_id and end_id and start_id != end_id:
            edges.add((start_id, end_id))
    return edges


def count_internal_fact_edges(
    canonical_ids: set[str],
    fact_edges: set[tuple[str, str]],
) -> int:
    """한 문항에 연결된 엔티티끼리의 사실 간선 수를 센다."""
    return sum(
        1
        for start_id, end_id in fact_edges
        if start_id in canonical_ids and end_id in canonical_ids
    )


def build_problem_recovery(
    resolution_cases: pd.DataFrame,
    source_candidates: pd.DataFrame,
    canonical_registry: pd.DataFrame,
    final_assignments: pd.DataFrame,
    canonical_era_relationships: pd.DataFrame,
    current_fact_relationships: pd.DataFrame,
    staged_fact_relationships: pd.DataFrame,
    policy: dict,
) -> tuple[
    pd.DataFrame,
    dict[str, set[str]],
    set[tuple[str, str]],
    set[tuple[str, str]],
    set[tuple[str, str]],
]:
    """보류된 문항 배정의 안전한 재매칭 후보를 만든다."""
    recovery_policy = policy["exam_match_recovery"]
    cases_by_id = {
        str(row["resolution_case_id"]): row
        for row in resolution_cases.to_dict("records")
    }
    exact_sources_by_case = collect_exact_source_records(
        resolution_cases,
        source_candidates,
        policy,
    )
    canonicals_by_case = collect_eligible_canonicals(
        resolution_cases,
        canonical_registry,
        exact_sources_by_case,
        policy,
    )
    eras_by_canonical = collect_canonical_eras(
        canonical_era_relationships,
        policy,
    )
    accepted_by_problem = collect_accepted_entities_by_problem(
        final_assignments,
        policy,
    )
    classification_types = set(
        recovery_policy.get(
            "current_fact_classification_relation_types",
            [],
        )
    )
    current_edges = collect_fact_edges(
        current_fact_relationships,
        classification_types,
    )
    current_all_edges = collect_fact_edges(
        current_fact_relationships,
        set(),
    )
    staged_edges = collect_fact_edges(
        staged_fact_relationships,
        set(),
        set(recovery_policy.get("staged_fact_ready_statuses", [])),
    )
    current_endpoints = {
        canonical_id for edge in current_edges for canonical_id in edge
    }
    staged_endpoints = {
        canonical_id for edge in staged_edges for canonical_id in edge
    }
    ambiguous_status = str(recovery_policy["ambiguous_link_status"])
    rows: list[dict] = []

    for assignment in final_assignments.to_dict("records"):
        if str(assignment["link_status"]) != ambiguous_status:
            continue
        case_id = str(assignment["resolution_case_id"])
        case = cases_by_id[case_id]
        problem_id = str(assignment["problem_id"])
        candidate_rows = canonicals_by_case.get(case_id, [])
        (
            dominant_era_id,
            dominant_vote_count,
            second_vote_count,
            era_votes,
        ) = infer_problem_era(
            accepted_by_problem.get(problem_id, set()),
            eras_by_canonical,
            policy,
        )
        selected_id, recovery_status, selection_method = (
            select_recovery_candidate(
                candidate_rows,
                dominant_era_id,
                eras_by_canonical,
                policy,
            )
        )
        selected_candidate = next(
            (
                row
                for row in candidate_rows
                if str(row["canonical_id"]) == selected_id
            ),
            {},
        )
        rows.append(
            {
                "problem_assignment_id": assignment[
                    "problem_assignment_id"
                ],
                "problem_id": problem_id,
                "resolution_case_id": case_id,
                "canonical_term": case["canonical_term"],
                "category": case["category"],
                "entity_type_proposal": case["entity_type_proposal"],
                "current_link_status": assignment["link_status"],
                "recovery_status": recovery_status,
                "selected_canonical_id": selected_id,
                "selection_method": selection_method,
                "eligible_candidate_count": len(candidate_rows),
                "eligible_canonical_ids_json": dumps(
                    [
                        str(row["canonical_id"])
                        for row in candidate_rows
                    ],
                    ensure_ascii=False,
                ),
                "dominant_era_id": dominant_era_id,
                "dominant_era_vote_count": dominant_vote_count,
                "second_era_vote_count": second_vote_count,
                "context_era_votes_json": dumps(
                    era_votes,
                    ensure_ascii=False,
                ),
                "selected_candidate_era_ids_json": dumps(
                    sorted(eras_by_canonical.get(selected_id, set())),
                    ensure_ascii=False,
                ),
                "evidence_source_record_ids_json": dumps(
                    selected_candidate.get(
                        "matched_exact_source_ids",
                        [],
                    ),
                    ensure_ascii=False,
                ),
                "connected_to_current_core_fact_graph": (
                    selected_id in current_endpoints
                ),
                "connected_to_staged_fact_graph": (
                    selected_id in staged_endpoints
                ),
                "policy_version": recovery_policy["policy_version"],
            }
        )
    return (
        pd.DataFrame(rows),
        accepted_by_problem,
        current_edges,
        current_all_edges,
        staged_edges,
    )


def build_term_recovery(
    exam_term_nodes: pd.DataFrame,
    exam_term_relationships: pd.DataFrame,
    problem_recovery: pd.DataFrame,
    policy: dict,
) -> pd.DataFrame:
    """문항별 재매칭이 기출 용어 매칭률에 주는 영향을 집계한다."""
    recovery_policy = policy["exam_match_recovery"]
    auto_status = str(
        recovery_policy["decision_statuses"]["auto_accept"]
    )
    accepted_status = str(recovery_policy["accepted_link_status"])
    current_canonical_ids_by_term: dict[str, set[str]] = {}
    for relationship in exam_term_relationships.to_dict("records"):
        if str(relationship["match_status"]) != accepted_status:
            continue
        current_canonical_ids_by_term.setdefault(
            str(relationship["exam_term_id"]),
            set(),
        ).add(str(relationship["canonical_id"]))

    recovery_by_case: dict[str, list[dict]] = {}
    for row in problem_recovery.to_dict("records"):
        recovery_by_case.setdefault(
            str(row["resolution_case_id"]),
            [],
        ).append(row)

    rows: list[dict] = []
    for term in exam_term_nodes.to_dict("records"):
        term_id = str(term["exam_term_id"])
        case_ids = parse_json_list(term["resolution_case_ids_json"])
        recovery_rows = [
            row
            for case_id in case_ids
            for row in recovery_by_case.get(case_id, [])
        ]
        accepted_recovery_rows = [
            row
            for row in recovery_rows
            if str(row["recovery_status"]) == auto_status
        ]
        recovered_ids = {
            str(row["selected_canonical_id"])
            for row in accepted_recovery_rows
            if str(row["selected_canonical_id"])
        }
        current_ids = current_canonical_ids_by_term.get(term_id, set())
        projected_ids = current_ids.union(recovered_ids)
        projected_status = "PENDING"
        if len(projected_ids) == 1:
            projected_status = accepted_status
        elif len(projected_ids) > 1:
            projected_status = "MULTIPLE_ACCEPTED"
        rows.append(
            {
                "exam_term_id": term_id,
                "term": term["term"],
                "categories_json": term["categories_json"],
                "current_source_link_status": term[
                    "source_link_status"
                ],
                "current_canonical_ids_json": dumps(
                    sorted(current_ids),
                    ensure_ascii=False,
                ),
                "recovered_canonical_ids_json": dumps(
                    sorted(recovered_ids),
                    ensure_ascii=False,
                ),
                "projected_canonical_ids_json": dumps(
                    sorted(projected_ids),
                    ensure_ascii=False,
                ),
                "recovered_problem_assignment_count": len(
                    accepted_recovery_rows
                ),
                "remaining_review_assignment_count": (
                    len(recovery_rows) - len(accepted_recovery_rows)
                ),
                "projected_source_link_status": projected_status,
                "policy_version": recovery_policy["policy_version"],
            }
        )
    return pd.DataFrame(rows)


def build_problem_fact_coverage(
    final_assignments: pd.DataFrame,
    problem_contexts: pd.DataFrame,
    problem_recovery: pd.DataFrame,
    accepted_by_problem: dict[str, set[str]],
    current_core_edges: set[tuple[str, str]],
    current_all_edges: set[tuple[str, str]],
    staged_edges: set[tuple[str, str]],
    policy: dict,
) -> pd.DataFrame:
    """재매칭 전후의 문항별 사실 그래프 연결 정도를 계산한다."""
    recovery_policy = policy["exam_match_recovery"]
    auto_status = str(
        recovery_policy["decision_statuses"]["auto_accept"]
    )
    all_problem_ids = sorted(
        {
            str(problem_id)
            for problem_id in final_assignments["problem_id"].tolist()
        }.union(
            {
                str(problem_id)
                for problem_id in problem_contexts["problem_id"].tolist()
            }
        )
    )
    recovered_by_problem: dict[str, set[str]] = {}
    for row in problem_recovery.to_dict("records"):
        if str(row["recovery_status"]) != auto_status:
            continue
        recovered_by_problem.setdefault(
            str(row["problem_id"]),
            set(),
        ).add(str(row["selected_canonical_id"]))

    current_core_endpoints = {
        canonical_id
        for edge in current_core_edges
        for canonical_id in edge
    }
    current_all_endpoints = {
        canonical_id
        for edge in current_all_edges
        for canonical_id in edge
    }
    staged_endpoints = {
        canonical_id for edge in staged_edges for canonical_id in edge
    }
    combined_core_edges = current_core_edges.union(staged_edges)
    combined_all_edges = current_all_edges.union(staged_edges)
    combined_core_endpoints = current_core_endpoints.union(
        staged_endpoints
    )
    combined_all_endpoints = current_all_endpoints.union(
        staged_endpoints
    )
    rows: list[dict] = []
    for problem_id in all_problem_ids:
        current_ids = accepted_by_problem.get(problem_id, set())
        recovered_ids = recovered_by_problem.get(problem_id, set())
        projected_ids = current_ids.union(recovered_ids)
        rows.append(
            {
                "problem_id": problem_id,
                "current_canonical_count": len(current_ids),
                "recovered_canonical_count": len(recovered_ids),
                "projected_canonical_count": len(projected_ids),
                "current_core_fact_endpoint_count": len(
                    current_ids.intersection(current_core_endpoints)
                ),
                "projected_core_fact_endpoint_count": len(
                    projected_ids.intersection(current_core_endpoints)
                ),
                "projected_core_with_staged_fact_endpoint_count": len(
                    projected_ids.intersection(combined_core_endpoints)
                ),
                "current_all_fact_endpoint_count": len(
                    current_ids.intersection(current_all_endpoints)
                ),
                "projected_all_fact_endpoint_count": len(
                    projected_ids.intersection(current_all_endpoints)
                ),
                "projected_all_with_staged_fact_endpoint_count": len(
                    projected_ids.intersection(combined_all_endpoints)
                ),
                "current_internal_core_fact_count": (
                    count_internal_fact_edges(
                        current_ids,
                        current_core_edges,
                    )
                ),
                "projected_internal_core_fact_count": (
                    count_internal_fact_edges(
                        projected_ids,
                        current_core_edges,
                    )
                ),
                "projected_internal_core_fact_count_with_staged": (
                    count_internal_fact_edges(
                        projected_ids,
                        combined_core_edges,
                    )
                ),
                "current_internal_all_fact_count": (
                    count_internal_fact_edges(
                        current_ids,
                        current_all_edges,
                    )
                ),
                "projected_internal_all_fact_count": (
                    count_internal_fact_edges(
                        projected_ids,
                        current_all_edges,
                    )
                ),
                "projected_internal_all_fact_count_with_staged": (
                    count_internal_fact_edges(
                        projected_ids,
                        combined_all_edges,
                    )
                ),
                "policy_version": recovery_policy["policy_version"],
            }
        )
    return pd.DataFrame(rows)


def build_duplicate_review(
    problem_recovery: pd.DataFrame,
    policy: dict,
) -> pd.DataFrame:
    """같은 시대 후보가 여러 개인 case를 별도 검토 목록으로 집계한다."""
    recovery_policy = policy["exam_match_recovery"]
    duplicate_method = str(
        recovery_policy["selection_methods"]["duplicate_or_homonym"]
    )
    duplicate_rows = problem_recovery[
        problem_recovery["selection_method"].eq(duplicate_method)
    ]
    rows: list[dict] = []
    for case_id, group in duplicate_rows.groupby("resolution_case_id"):
        first = group.iloc[0]
        rows.append(
            {
                "resolution_case_id": case_id,
                "canonical_term": first["canonical_term"],
                "category": first["category"],
                "entity_type_proposal": first["entity_type_proposal"],
                "affected_problem_count": int(group["problem_id"].nunique()),
                "candidate_count": int(
                    group["eligible_candidate_count"].max()
                ),
                "eligible_canonical_ids_json": first[
                    "eligible_canonical_ids_json"
                ],
                "observed_dominant_era_ids_json": dumps(
                    sorted(
                        {
                            str(value)
                            for value in group["dominant_era_id"]
                            if str(value)
                        }
                    ),
                    ensure_ascii=False,
                ),
                "review_reason": duplicate_method,
                "policy_version": recovery_policy["policy_version"],
            }
        )
    return pd.DataFrame(rows)


def build_exam_match_recovery_tables(
    resolution_cases: pd.DataFrame,
    source_candidates: pd.DataFrame,
    canonical_registry: pd.DataFrame,
    final_assignments: pd.DataFrame,
    problem_contexts: pd.DataFrame,
    canonical_era_relationships: pd.DataFrame,
    exam_term_nodes: pd.DataFrame,
    exam_term_relationships: pd.DataFrame,
    current_fact_relationships: pd.DataFrame,
    staged_fact_relationships: pd.DataFrame,
    policy: dict,
) -> tuple[dict[str, pd.DataFrame], dict[str, object]]:
    """기출 용어 재매칭과 사실 관계 커버리지 표를 함께 만든다."""
    (
        problem_recovery,
        accepted_by_problem,
        current_core_edges,
        current_all_edges,
        staged_edges,
    ) = build_problem_recovery(
        resolution_cases,
        source_candidates,
        canonical_registry,
        final_assignments,
        canonical_era_relationships,
        current_fact_relationships,
        staged_fact_relationships,
        policy,
    )
    term_recovery = build_term_recovery(
        exam_term_nodes,
        exam_term_relationships,
        problem_recovery,
        policy,
    )
    fact_coverage = build_problem_fact_coverage(
        final_assignments,
        problem_contexts,
        problem_recovery,
        accepted_by_problem,
        current_core_edges,
        current_all_edges,
        staged_edges,
        policy,
    )
    duplicate_review = build_duplicate_review(
        problem_recovery,
        policy,
    )
    recovery_policy = policy["exam_match_recovery"]
    auto_status = str(
        recovery_policy["decision_statuses"]["auto_accept"]
    )
    accepted_statuses = {str(recovery_policy["accepted_link_status"])}
    matched_before = int(
        term_recovery["current_source_link_status"]
        .isin(accepted_statuses.union({"MULTIPLE_ACCEPTED"}))
        .sum()
    )
    matched_after = int(
        term_recovery["projected_source_link_status"]
        .isin(accepted_statuses.union({"MULTIPLE_ACCEPTED"}))
        .sum()
    )
    total_terms = len(term_recovery)
    auto_rows = problem_recovery[
        problem_recovery["recovery_status"].eq(auto_status)
    ]
    matched_rate_before = 0.0
    matched_rate_after = 0.0
    if total_terms:
        matched_rate_before = matched_before / total_terms
        matched_rate_after = matched_after / total_terms
    statistics: dict[str, object] = {
        "ambiguous_problem_assignment_count": len(problem_recovery),
        "auto_accept_candidate_count": len(auto_rows),
        "remaining_review_assignment_count": (
            len(problem_recovery) - len(auto_rows)
        ),
        "recovered_resolution_case_count": int(
            auto_rows["resolution_case_id"].nunique()
        ),
        "selection_method_counts": {
            str(method): int(count)
            for method, count in problem_recovery[
                "selection_method"
            ].value_counts().items()
        },
        "exam_term_count": total_terms,
        "matched_exam_term_count_before": matched_before,
        "matched_exam_term_count_after": matched_after,
        "matched_exam_term_rate_before": matched_rate_before,
        "matched_exam_term_rate_after": matched_rate_after,
        "problem_count": len(fact_coverage),
        "problem_with_current_core_fact_endpoint_count": int(
            fact_coverage["current_core_fact_endpoint_count"].gt(0).sum()
        ),
        "problem_with_projected_core_fact_endpoint_count": int(
            fact_coverage[
                "projected_core_fact_endpoint_count"
            ].gt(0).sum()
        ),
        "problem_with_projected_staged_core_fact_endpoint_count": int(
            fact_coverage[
                "projected_core_with_staged_fact_endpoint_count"
            ].gt(0).sum()
        ),
        "problem_with_current_all_fact_endpoint_count": int(
            fact_coverage["current_all_fact_endpoint_count"].gt(0).sum()
        ),
        "problem_with_projected_all_fact_endpoint_count": int(
            fact_coverage[
                "projected_all_fact_endpoint_count"
            ].gt(0).sum()
        ),
        "problem_with_projected_staged_all_fact_endpoint_count": int(
            fact_coverage[
                "projected_all_with_staged_fact_endpoint_count"
            ].gt(0).sum()
        ),
        "problem_with_current_internal_core_fact_count": int(
            fact_coverage["current_internal_core_fact_count"].gt(0).sum()
        ),
        "problem_with_projected_internal_core_fact_count": int(
            fact_coverage[
                "projected_internal_core_fact_count"
            ].gt(0).sum()
        ),
        "problem_with_projected_internal_core_fact_count_with_staged": int(
            fact_coverage[
                "projected_internal_core_fact_count_with_staged"
            ].gt(0).sum()
        ),
        "problem_with_current_internal_all_fact_count": int(
            fact_coverage[
                "current_internal_all_fact_count"
            ].gt(0).sum()
        ),
        "problem_with_projected_internal_all_fact_count": int(
            fact_coverage[
                "projected_internal_all_fact_count"
            ].gt(0).sum()
        ),
        "problem_with_projected_internal_all_fact_count_with_staged": int(
            fact_coverage[
                "projected_internal_all_fact_count_with_staged"
            ].gt(0).sum()
        ),
        "duplicate_review_case_count": len(duplicate_review),
        "llm_used": False,
        "neo4j_load": False,
    }
    tables = {
        "problem_recovery": problem_recovery,
        "term_recovery": term_recovery,
        "problem_fact_coverage": fact_coverage,
        "duplicate_review": duplicate_review,
    }
    return tables, statistics
