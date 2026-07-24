from argparse import ArgumentParser
from collections import Counter
from json import dumps, loads
from pathlib import Path
import sys

import pandas as pd

sys.path.append(str(Path(__file__).resolve().parent.parent))

from common import load_pipeline_policy
from entity_resolution.semantic_review import (
    collect_classified_sources,
    load_jsonl,
    validate_decision_shape,
)


def safe_divide(numerator: int | float, denominator: int | float, zero: float) -> float:
    """분모가 0인 평가 지표에 정책의 값을 적용한다."""
    if denominator == 0:
        return zero
    return float(numerator) / float(denominator)


def extract_identity_clusters(decision: dict) -> set[frozenset[str]]:
    """decision 대안들을 후보 ID 집합으로 정규화한다."""
    clusters: set[frozenset[str]] = set()
    for alternative in decision.get("proposed_alternatives", []):
        member_ids = alternative.get(
            "identity_member_source_candidate_ids",
            [],
        )
        if member_ids:
            clusters.add(
                frozenset(str(candidate_id) for candidate_id in member_ids)
            )
    return clusters


def extract_cluster_pairs(
    clusters: set[frozenset[str]],
) -> set[frozenset[str]]:
    """동일 identity cluster 안의 모든 후보 쌍을 만든다."""
    pairs: set[frozenset[str]] = set()
    for cluster in clusters:
        ordered_ids = sorted(cluster)
        for left_index, left_id in enumerate(ordered_ids):
            for right_id in ordered_ids[left_index + 1:]:
                pairs.add(frozenset([left_id, right_id]))
    return pairs


def validate_evaluation_decision(
    decision: dict,
    expected_candidate_ids: set[str] | None = None,
) -> tuple[dict[str, str], list[str]]:
    """평가 전에 decision 구조와 후보 완전 분류를 검사한다."""
    errors = validate_decision_shape(decision)
    roles: dict[str, str] = {}
    if errors:
        return roles, errors
    classified, duplicate_ids = collect_classified_sources(decision)
    roles = {
        candidate_id: classified_value[0]
        for candidate_id, classified_value in classified.items()
    }
    if duplicate_ids:
        errors.append(
            "DUPLICATE_CANDIDATE_CLASSIFICATION: "
            + dumps(sorted(set(duplicate_ids)), ensure_ascii=False)
        )
    if expected_candidate_ids is not None:
        observed_ids = set(roles)
        missing_ids = expected_candidate_ids.difference(observed_ids)
        unknown_ids = observed_ids.difference(expected_candidate_ids)
        if missing_ids:
            errors.append(
                "MISSING_CANDIDATE_CLASSIFICATION: "
                + dumps(sorted(missing_ids), ensure_ascii=False)
            )
        if unknown_ids:
            errors.append(
                "UNKNOWN_SOURCE_CANDIDATE: "
                + dumps(sorted(unknown_ids), ensure_ascii=False)
            )
    return roles, errors


def derive_link_status(decision: dict, candidate_count: int) -> str:
    """term decision 구조에서 평가용 link status를 결정적으로 유도한다."""
    link_status = "UNRESOLVED"
    if decision.get("ambiguous_sources"):
        link_status = "AMBIGUOUS"
    elif decision.get("proposed_alternatives"):
        link_status = "ACCEPTED"
    elif len(decision.get("rejected_sources", [])) == candidate_count:
        link_status = "REJECTED"
    return link_status


def calculate_role_metrics(
    confusion: Counter,
    roles: list[str],
    zero_division_value: float,
) -> pd.DataFrame:
    """역할별 precision·recall·F1과 support를 계산한다."""
    rows: list[dict] = []
    for role in roles:
        true_positive = confusion[(role, role)]
        false_positive = sum(
            count
            for (gold_role, predicted_role), count in confusion.items()
            if predicted_role == role and gold_role != role
        )
        false_negative = sum(
            count
            for (gold_role, predicted_role), count in confusion.items()
            if gold_role == role and predicted_role != role
        )
        support = sum(
            count
            for (gold_role, _), count in confusion.items()
            if gold_role == role
        )
        precision = safe_divide(
            true_positive,
            true_positive + false_positive,
            zero_division_value,
        )
        recall = safe_divide(
            true_positive,
            true_positive + false_negative,
            zero_division_value,
        )
        f1 = safe_divide(
            2 * precision * recall,
            precision + recall,
            zero_division_value,
        )
        rows.append(
            {
                "role": role,
                "precision": precision,
                "recall": recall,
                "f1": f1,
                "support": support,
                "true_positive": true_positive,
                "false_positive": false_positive,
                "false_negative": false_negative,
            }
        )
    return pd.DataFrame(rows)


def build_stratum_metrics(
    case_results: pd.DataFrame,
    zero_division_value: float,
) -> pd.DataFrame:
    """category와 난이도 층별 case·candidate 정확도를 집계한다."""
    dimensions = [
        "category",
        "candidate_count_bucket",
        "retrieval_profile",
        "multi_source_supported",
        "conflict_present",
    ]
    rows: list[dict] = []
    if case_results.empty:
        return pd.DataFrame(
            columns=[
                "dimension",
                "value",
                "gold_case_count",
                "valid_prediction_count",
                "prediction_coverage",
                "candidate_role_accuracy",
                "role_exact_case_rate",
                "cluster_exact_case_rate",
            ]
        )
    for dimension in dimensions:
        for value, group in case_results.groupby(dimension, dropna=False):
            valid_group = group[group["prediction_valid"]]
            correct_candidates = int(valid_group["correct_candidate_count"].sum())
            candidate_count = int(valid_group["candidate_count"].sum())
            rows.append(
                {
                    "dimension": dimension,
                    "value": str(value),
                    "gold_case_count": len(group),
                    "valid_prediction_count": len(valid_group),
                    "prediction_coverage": safe_divide(
                        len(valid_group),
                        len(group),
                        zero_division_value,
                    ),
                    "candidate_role_accuracy": safe_divide(
                        correct_candidates,
                        candidate_count,
                        zero_division_value,
                    ),
                    "role_exact_case_rate": safe_divide(
                        int(valid_group["role_exact_match"].sum()),
                        len(valid_group),
                        zero_division_value,
                    ),
                    "cluster_exact_case_rate": safe_divide(
                        int(valid_group["cluster_exact_match"].sum()),
                        len(valid_group),
                        zero_division_value,
                    ),
                }
            )
    return pd.DataFrame(rows)


def evaluate_term_decisions(
    gold_decisions: list[dict],
    predicted_decisions: list[dict],
    gold_tasks: list[dict],
    gold_case_outcomes: pd.DataFrame,
    policy: dict,
    verified_decision_tables: dict[str, pd.DataFrame] | None = None,
) -> dict[str, object]:
    """사람 gold와 LLM 제안 및 검증 게이트 결과를 단계별로 비교한다."""
    gold_policy = policy["entity_resolution"]["semantic_review"]["gold_set"]
    evaluation_policy = policy["entity_resolution"]["semantic_review"][
        "gold_evaluation"
    ]
    identity_pair_gate_policy = policy["entity_resolution"][
        "semantic_review"
    ]["identity_pair_gate"]
    zero_value = float(evaluation_policy["zero_division_value"])
    role_vocabulary = gold_policy["annotation_vocabulary"]["candidate_roles"]
    task_by_id = {
        task["term_review_task_id"]: task for task in gold_tasks
    }
    outcome_by_task_id: dict[str, dict] = {}
    if not gold_case_outcomes.empty:
        outcome_by_task_id = {
            str(row["term_review_task_id"]): row
            for row in gold_case_outcomes.to_dict("records")
        }
    prediction_by_task_id: dict[str, dict] = {}
    errors: list[dict] = []
    for decision in predicted_decisions:
        task_id = str(decision.get("term_review_task_id") or "")
        if task_id in prediction_by_task_id:
            errors.append(
                {
                    "term_review_task_id": task_id,
                    "severity": "ERROR",
                    "error_code": "DUPLICATE_PREDICTED_DECISION",
                    "message": "동일 task의 예측 decision이 중복되었습니다.",
                }
            )
            continue
        prediction_by_task_id[task_id] = decision
    gold_by_task_id: dict[str, dict] = {}
    for decision in gold_decisions:
        task_id = str(decision.get("term_review_task_id") or "")
        if task_id in gold_by_task_id:
            errors.append(
                {
                    "term_review_task_id": task_id,
                    "severity": "ERROR",
                    "error_code": "DUPLICATE_GOLD_DECISION",
                    "message": "동일 task의 gold decision이 중복되었습니다.",
                }
            )
            continue
        gold_by_task_id[task_id] = decision
    for task_id in prediction_by_task_id:
        if task_id in gold_by_task_id:
            continue
        errors.append(
            {
                "term_review_task_id": task_id,
                "severity": "ERROR",
                "error_code": "UNKNOWN_PREDICTED_TASK",
                "message": "골든셋에 없는 task의 예측 decision입니다.",
            }
        )

    gate_evaluation_available = verified_decision_tables is not None
    gate_status_by_task_id: dict[str, str] = {}
    accepted_clusters_by_case_id: dict[str, set[frozenset[str]]] = {}
    gate_error_codes_by_case_id: dict[str, list[str]] = {}
    if verified_decision_tables is not None:
        gate_decisions = verified_decision_tables[
            "term_resolution_decisions"
        ]
        for row in gate_decisions.to_dict("records"):
            gate_status_by_task_id[str(row["term_review_task_id"])] = str(
                row["verification_status"]
            )
        accepted_alternatives = verified_decision_tables[
            "reviewed_canonical_alternatives"
        ]
        for row in accepted_alternatives.to_dict("records"):
            case_id = str(row["resolution_case_id"])
            member_ids = frozenset(
                str(candidate_id)
                for candidate_id in loads(row["source_candidate_ids_json"])
            )
            accepted_clusters_by_case_id.setdefault(case_id, set()).add(
                member_ids
            )
        gate_errors = verified_decision_tables[
            "term_decision_validation_errors"
        ]
        for row in gate_errors.to_dict("records"):
            case_id = str(row["resolution_case_id"])
            gate_error_codes_by_case_id.setdefault(case_id, []).append(
                str(row["error_code"])
            )

    confusion: Counter = Counter()
    case_rows: list[dict] = []
    pair_true_positive = 0
    pair_false_positive = 0
    pair_false_negative = 0
    verified_pair_true_positive = 0
    verified_pair_false_positive = 0
    verified_pair_false_negative = 0
    blocked_proposal_false_positive = 0
    gold_identity_pair_count = 0
    deferred_gold_identity_pairs = 0
    deferred_gold_pair_case_count = 0
    deferred_gold_pair_gate_status_counts: Counter = Counter()
    deferred_gold_pair_error_case_counts: Counter = Counter()
    deferred_gold_pair_error_pair_counts: Counter = Counter()
    gate_status_counts: Counter = Counter()
    for task_id, gold_decision in gold_by_task_id.items():
        task = task_by_id.get(task_id)
        if task is None:
            errors.append(
                {
                    "term_review_task_id": task_id,
                    "severity": "ERROR",
                    "error_code": "UNKNOWN_GOLD_TASK",
                    "message": "gold decision에 대응하는 원본 task가 없습니다.",
                }
            )
            continue
        metadata = task.get("gold_set_metadata", {})
        expected_candidate_ids = {
            str(candidate["source_candidate_id"])
            for candidate in task.get("source_candidates", [])
        }
        gold_roles, gold_errors = validate_evaluation_decision(
            gold_decision,
            expected_candidate_ids,
        )
        if gold_decision.get("resolution_case_id") != task.get(
            "resolution_case_id"
        ):
            gold_errors.append("GOLD_RESOLUTION_CASE_ID_MISMATCH")
        if gold_errors:
            errors.append(
                {
                    "term_review_task_id": task_id,
                    "severity": "ERROR",
                    "error_code": "INVALID_GOLD_DECISION",
                    "message": "; ".join(gold_errors),
                }
            )
            continue
        gold_clusters = extract_identity_clusters(gold_decision)
        gold_pairs = extract_cluster_pairs(gold_clusters)
        gold_identity_pair_count += len(gold_pairs)
        predicted_decision = prediction_by_task_id.get(task_id)
        base_case_row = {
            "term_review_task_id": task_id,
            "resolution_case_id": gold_decision["resolution_case_id"],
            "canonical_term": task.get("canonical_term", ""),
            "category": task.get("category", ""),
            "candidate_count_bucket": metadata.get(
                "candidate_count_bucket",
                "",
            ),
            "retrieval_profile": metadata.get("retrieval_profile", ""),
            "multi_source_supported": metadata.get(
                "multi_source_supported",
                "",
            ),
            "conflict_present": metadata.get("conflict_present", ""),
            "candidate_count": len(expected_candidate_ids),
            "prediction_present": predicted_decision is not None,
            "prediction_valid": False,
            "correct_candidate_count": 0,
            "role_exact_match": False,
            "cluster_exact_match": False,
            "pair_true_positive": 0,
            "pair_false_positive": 0,
            "pair_false_negative": 0,
            "gold_link_status": "",
            "predicted_link_status": "",
            "link_status_match": False,
            "gold_requires_problem_review": "",
            "predicted_requires_problem_review": "",
            "problem_review_match": False,
            "gate_evaluated": False,
            "gate_verification_status": "",
            "gate_error_codes_json": "[]",
            "accepted_cluster_exact_match": "",
            "accepted_pair_true_positive": "",
            "accepted_pair_false_positive": "",
            "accepted_pair_false_negative": "",
            "blocked_proposal_false_merge_count": "",
            "deferred_gold_identity_pair_count": "",
        }
        if predicted_decision is None:
            errors.append(
                {
                    "term_review_task_id": task_id,
                    "severity": "INCOMPLETE",
                    "error_code": "MISSING_PREDICTED_DECISION",
                    "message": "LLM 예측 decision이 없습니다.",
                }
            )
            case_rows.append(base_case_row)
            continue
        predicted_roles, predicted_errors = validate_evaluation_decision(
            predicted_decision,
            expected_candidate_ids,
        )
        if predicted_decision.get("resolution_case_id") != task.get(
            "resolution_case_id"
        ):
            predicted_errors.append("PREDICTED_RESOLUTION_CASE_ID_MISMATCH")
        if predicted_errors:
            errors.append(
                {
                    "term_review_task_id": task_id,
                    "severity": "ERROR",
                    "error_code": "INVALID_PREDICTED_DECISION",
                    "message": "; ".join(predicted_errors),
                }
            )
            case_rows.append(base_case_row)
            continue

        correct_candidate_count = 0
        for candidate_id in sorted(expected_candidate_ids):
            gold_role = gold_roles[candidate_id]
            predicted_role = predicted_roles[candidate_id]
            confusion[(gold_role, predicted_role)] += 1
            if gold_role == predicted_role:
                correct_candidate_count += 1
        predicted_clusters = extract_identity_clusters(predicted_decision)
        predicted_pairs = extract_cluster_pairs(predicted_clusters)
        case_pair_true_positive = len(gold_pairs.intersection(predicted_pairs))
        case_pair_false_positive = len(predicted_pairs.difference(gold_pairs))
        case_pair_false_negative = len(gold_pairs.difference(predicted_pairs))
        pair_true_positive += case_pair_true_positive
        pair_false_positive += case_pair_false_positive
        pair_false_negative += case_pair_false_negative
        outcome = outcome_by_task_id.get(task_id, {})
        gold_link_status = str(outcome.get("gold_link_status") or "")
        predicted_link_status = derive_link_status(
            predicted_decision,
            len(expected_candidate_ids),
        )
        gold_problem_review = str(
            outcome.get("requires_problem_review") or ""
        )
        predicted_problem_review = "NO"
        if len(predicted_clusters) > 1:
            predicted_problem_review = "YES"
        gate_values: dict[str, object] = {}
        if gate_evaluation_available:
            case_id = str(gold_decision["resolution_case_id"])
            gate_status = gate_status_by_task_id.get(
                task_id,
                "MISSING_GATE_RESULT",
            )
            gate_status_counts[gate_status] += 1
            case_gate_error_codes = sorted(
                set(gate_error_codes_by_case_id.get(case_id, []))
            )
            accepted_clusters = accepted_clusters_by_case_id.get(
                case_id,
                set(),
            )
            accepted_pairs = extract_cluster_pairs(accepted_clusters)
            accepted_true_positive = len(
                gold_pairs.intersection(accepted_pairs)
            )
            accepted_false_positive = len(
                accepted_pairs.difference(gold_pairs)
            )
            accepted_false_negative: int | str = ""
            accepted_cluster_exact: bool | str = ""
            deferred_pair_count = 0
            if gate_status == "VERIFIED":
                accepted_false_negative = len(
                    gold_pairs.difference(accepted_pairs)
                )
                accepted_cluster_exact = gold_clusters == accepted_clusters
                verified_pair_true_positive += accepted_true_positive
                verified_pair_false_positive += accepted_false_positive
                verified_pair_false_negative += accepted_false_negative
            elif gate_status != "VERIFIED":
                deferred_pair_count = len(gold_pairs)
                deferred_gold_identity_pairs += deferred_pair_count
                if deferred_pair_count:
                    deferred_gold_pair_case_count += 1
                    deferred_gold_pair_gate_status_counts[gate_status] += 1
                    for error_code in case_gate_error_codes:
                        deferred_gold_pair_error_case_counts[
                            error_code
                        ] += 1
                        deferred_gold_pair_error_pair_counts[
                            error_code
                        ] += deferred_pair_count
            blocked_false_positive = max(
                0,
                case_pair_false_positive - accepted_false_positive,
            )
            blocked_proposal_false_positive += blocked_false_positive
            gate_values = {
                "gate_evaluated": True,
                "gate_verification_status": gate_status,
                "gate_error_codes_json": dumps(
                    case_gate_error_codes,
                    ensure_ascii=False,
                ),
                "accepted_cluster_exact_match": accepted_cluster_exact,
                "accepted_pair_true_positive": accepted_true_positive,
                "accepted_pair_false_positive": accepted_false_positive,
                "accepted_pair_false_negative": accepted_false_negative,
                "blocked_proposal_false_merge_count": (
                    blocked_false_positive
                ),
                "deferred_gold_identity_pair_count": deferred_pair_count,
            }
        base_case_row.update(
            {
                "prediction_valid": True,
                "correct_candidate_count": correct_candidate_count,
                "role_exact_match": gold_roles == predicted_roles,
                "cluster_exact_match": gold_clusters == predicted_clusters,
                "pair_true_positive": case_pair_true_positive,
                "pair_false_positive": case_pair_false_positive,
                "pair_false_negative": case_pair_false_negative,
                "gold_link_status": gold_link_status,
                "predicted_link_status": predicted_link_status,
                "link_status_match": bool(gold_link_status)
                and gold_link_status == predicted_link_status,
                "gold_requires_problem_review": gold_problem_review,
                "predicted_requires_problem_review": predicted_problem_review,
                "problem_review_match": bool(gold_problem_review)
                and gold_problem_review == predicted_problem_review,
            }
        )
        base_case_row.update(gate_values)
        case_rows.append(base_case_row)

    case_result_columns = [
        "term_review_task_id",
        "resolution_case_id",
        "canonical_term",
        "category",
        "candidate_count_bucket",
        "retrieval_profile",
        "multi_source_supported",
        "conflict_present",
        "candidate_count",
        "prediction_present",
        "prediction_valid",
        "correct_candidate_count",
        "role_exact_match",
        "cluster_exact_match",
        "pair_true_positive",
        "pair_false_positive",
        "pair_false_negative",
        "gold_link_status",
        "predicted_link_status",
        "link_status_match",
        "gold_requires_problem_review",
        "predicted_requires_problem_review",
        "problem_review_match",
        "gate_evaluated",
        "gate_verification_status",
        "gate_error_codes_json",
        "accepted_cluster_exact_match",
        "accepted_pair_true_positive",
        "accepted_pair_false_positive",
        "accepted_pair_false_negative",
        "blocked_proposal_false_merge_count",
        "deferred_gold_identity_pair_count",
    ]
    case_results = pd.DataFrame(case_rows, columns=case_result_columns)
    valid_case_results = case_results.loc[
        case_results["prediction_valid"].astype(bool)
    ]
    role_metrics = calculate_role_metrics(
        confusion,
        role_vocabulary,
        zero_value,
    )
    total_candidates = sum(confusion.values())
    correct_candidates = sum(
        count
        for (gold_role, predicted_role), count in confusion.items()
        if gold_role == predicted_role
    )
    pair_precision = safe_divide(
        pair_true_positive,
        pair_true_positive + pair_false_positive,
        zero_value,
    )
    pair_recall = safe_divide(
        pair_true_positive,
        pair_true_positive + pair_false_negative,
        zero_value,
    )
    pair_f1 = safe_divide(
        2 * pair_precision * pair_recall,
        pair_precision + pair_recall,
        zero_value,
    )
    verified_pair_precision = safe_divide(
        verified_pair_true_positive,
        verified_pair_true_positive + verified_pair_false_positive,
        zero_value,
    )
    verified_pair_recall = safe_divide(
        verified_pair_true_positive,
        verified_pair_true_positive + verified_pair_false_negative,
        zero_value,
    )
    verified_pair_f1 = safe_divide(
        2 * verified_pair_precision * verified_pair_recall,
        verified_pair_precision + verified_pair_recall,
        zero_value,
    )
    auto_accepted_pair_count = (
        verified_pair_true_positive + verified_pair_false_positive
    )
    auto_accepted_pair_recall = safe_divide(
        verified_pair_true_positive,
        gold_identity_pair_count,
        zero_value,
    )
    auto_accepted_pair_f1 = safe_divide(
        2 * verified_pair_precision * auto_accepted_pair_recall,
        verified_pair_precision + auto_accepted_pair_recall,
        zero_value,
    )
    deferred_gold_identity_pair_rate = safe_divide(
        deferred_gold_identity_pairs,
        gold_identity_pair_count,
        zero_value,
    )
    macro_f1 = zero_value
    supported_role_metrics = role_metrics[role_metrics["support"] > 0]
    if not supported_role_metrics.empty:
        macro_f1 = float(supported_role_metrics["f1"].mean())
    weighted_f1 = zero_value
    if total_candidates:
        weighted_f1 = float(
            (
                supported_role_metrics["f1"]
                * supported_role_metrics["support"]
            ).sum()
            / total_candidates
        )
    excluded_macro_roles = set(
        evaluation_policy["macro_f1_excluded_roles"]
    )
    non_excluded_role_metrics = supported_role_metrics.loc[
        ~supported_role_metrics["role"].isin(excluded_macro_roles)
    ]
    non_excluded_macro_f1 = zero_value
    if not non_excluded_role_metrics.empty:
        non_excluded_macro_f1 = float(
            non_excluded_role_metrics["f1"].mean()
        )
    role_support_counts = {
        str(row["role"]): int(row["support"])
        for row in role_metrics.to_dict("records")
    }
    link_rows = valid_case_results[
        valid_case_results["gold_link_status"] != ""
    ]
    problem_rows = valid_case_results[
        valid_case_results["gold_requires_problem_review"] != ""
    ]
    metrics = {
        "gold_case_count": len(gold_by_task_id),
        "evaluable_gold_case_count": len(case_results),
        "predicted_decision_count": len(prediction_by_task_id),
        "valid_prediction_count": len(valid_case_results),
        "prediction_coverage": safe_divide(
            len(valid_case_results),
            len(case_results),
            zero_value,
        ),
        "candidate_role_accuracy": safe_divide(
            correct_candidates,
            total_candidates,
            zero_value,
        ),
        "candidate_role_macro_f1": macro_f1,
        "candidate_role_weighted_f1": weighted_f1,
        "candidate_role_macro_f1_without_excluded_roles": (
            non_excluded_macro_f1
        ),
        "candidate_role_macro_f1_excluded_roles": sorted(
            excluded_macro_roles
        ),
        "candidate_role_support_counts": role_support_counts,
        "role_exact_case_rate": safe_divide(
            int(valid_case_results["role_exact_match"].sum()),
            len(valid_case_results),
            zero_value,
        ),
        "cluster_exact_case_rate": safe_divide(
            int(valid_case_results["cluster_exact_match"].sum()),
            len(valid_case_results),
            zero_value,
        ),
        "identity_pair_precision": pair_precision,
        "identity_pair_recall": pair_recall,
        "identity_pair_f1": pair_f1,
        "false_merge_pair_count": pair_false_positive,
        "false_split_pair_count": pair_false_negative,
        "proposal_identity_pair_precision": pair_precision,
        "proposal_identity_pair_recall": pair_recall,
        "proposal_identity_pair_f1": pair_f1,
        "proposal_identity_pair_true_positive_count": pair_true_positive,
        "proposal_false_merge_pair_count": pair_false_positive,
        "proposal_false_split_pair_count": pair_false_negative,
        "gate_evaluation_available": gate_evaluation_available,
        "identity_pair_gate_policy_version": (
            identity_pair_gate_policy["policy_version"]
        ),
        "identity_pair_gate_evidence_mode": (
            identity_pair_gate_policy["active_evidence_mode"]
        ),
        "gate_verification_status_counts": dict(gate_status_counts),
        "gold_identity_pair_count": gold_identity_pair_count,
        "verified_identity_pair_precision": verified_pair_precision,
        "verified_identity_pair_recall": verified_pair_recall,
        "verified_identity_pair_f1": verified_pair_f1,
        "conditional_verified_identity_pair_precision": (
            verified_pair_precision
        ),
        "conditional_verified_identity_pair_recall": (
            verified_pair_recall
        ),
        "conditional_verified_identity_pair_f1": verified_pair_f1,
        "auto_accepted_identity_pair_count": auto_accepted_pair_count,
        "auto_accepted_identity_pair_true_positive_count": (
            verified_pair_true_positive
        ),
        "auto_accepted_identity_pair_precision": (
            verified_pair_precision
        ),
        "auto_accepted_identity_pair_recall": (
            auto_accepted_pair_recall
        ),
        "auto_accepted_identity_pair_f1": auto_accepted_pair_f1,
        "verified_false_merge_pair_count": verified_pair_false_positive,
        "verified_false_split_pair_count": verified_pair_false_negative,
        "blocked_proposal_false_merge_pair_count": (
            blocked_proposal_false_positive
        ),
        "deferred_gold_identity_pair_count": deferred_gold_identity_pairs,
        "deferred_gold_identity_pair_rate": (
            deferred_gold_identity_pair_rate
        ),
        "deferred_gold_pair_case_count": deferred_gold_pair_case_count,
        "deferred_gold_pair_gate_status_counts": dict(
            sorted(deferred_gold_pair_gate_status_counts.items())
        ),
        "deferred_gold_pair_error_case_counts": dict(
            sorted(deferred_gold_pair_error_case_counts.items())
        ),
        "deferred_gold_pair_error_pair_counts": dict(
            sorted(deferred_gold_pair_error_pair_counts.items())
        ),
        "link_status_accuracy": safe_divide(
            int(link_rows["link_status_match"].sum()),
            len(link_rows),
            zero_value,
        ),
        "problem_review_accuracy": safe_divide(
            int(problem_rows["problem_review_match"].sum()),
            len(problem_rows),
            zero_value,
        ),
    }
    error_columns = [
        "term_review_task_id",
        "severity",
        "error_code",
        "message",
    ]
    return {
        "metrics": metrics,
        "case_results": case_results,
        "role_metrics": role_metrics,
        "stratum_metrics": build_stratum_metrics(
            case_results,
            zero_value,
        ),
        "evaluation_errors": pd.DataFrame(errors, columns=error_columns),
    }


def write_evaluation_outputs(
    outputs: dict[str, object],
    output_dir: str,
    policy: dict,
) -> dict[str, str]:
    """평가 지표와 case·role·층별 상세 CSV를 저장한다."""
    evaluation_policy = policy["entity_resolution"]["semantic_review"][
        "gold_evaluation"
    ]
    output_directory = Path(output_dir)
    output_directory.mkdir(parents=True, exist_ok=True)
    output_paths = {
        name: output_directory / filename
        for name, filename in evaluation_policy["output_files"].items()
    }
    output_paths["metrics"].write_text(
        dumps(outputs["metrics"], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    for table_name in [
        "case_results",
        "role_metrics",
        "stratum_metrics",
        "evaluation_errors",
    ]:
        outputs[table_name].to_csv(
            output_paths[table_name],
            index=False,
            encoding="utf-8-sig",
        )
    return {name: str(path) for name, path in output_paths.items()}


if __name__ == "__main__":
    parser = ArgumentParser(
        description="term-level LLM decision을 사람 골든셋과 비교 평가"
    )
    parser.add_argument("gold_decisions", help="human gold decision JSONL")
    parser.add_argument("predicted_decisions", help="LLM decision JSONL")
    parser.add_argument("gold_tasks", help="gold term task JSONL")
    parser.add_argument("gold_case_outcomes", help="gold case outcome CSV")
    parser.add_argument("output_dir", help="평가 결과 출력 폴더")
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
    gold_records = load_jsonl(cli_args.gold_decisions)
    predicted_records = load_jsonl(cli_args.predicted_decisions)
    task_records = load_jsonl(cli_args.gold_tasks)
    outcome_table = pd.read_csv(
        cli_args.gold_case_outcomes,
        dtype=str,
    ).fillna("")
    evaluation_outputs = evaluate_term_decisions(
        gold_records,
        predicted_records,
        task_records,
        outcome_table,
        pipeline_policy,
    )
    written = write_evaluation_outputs(
        evaluation_outputs,
        cli_args.output_dir,
        pipeline_policy,
    )
    print(dumps(written, ensure_ascii=False, indent=2))
    print(dumps(evaluation_outputs["metrics"], ensure_ascii=False, indent=2))
