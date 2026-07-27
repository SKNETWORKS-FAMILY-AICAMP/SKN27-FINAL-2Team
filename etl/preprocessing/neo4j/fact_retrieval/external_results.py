from __future__ import annotations

from json import dumps

import pandas as pd

from fact_retrieval.build import parse_json_list


def apply_external_verification_results(
    truth_gate_results: pd.DataFrame,
    external_task_backlog: list[dict],
    verification_results: list[dict],
    policy: dict,
) -> tuple[pd.DataFrame, dict[str, object]]:
    """외부 사실 검증 결과를 연결된 모든 교체 후보 gate에 반영한다."""
    gate_policy = policy["truth_gate"]
    decision_statuses = {
        str(decision): str(status)
        for decision, status in gate_policy[
            "external_decision_statuses"
        ].items()
    }
    unverifiable_decision = str(
        gate_policy["unverifiable_decision"]
    )
    task_by_id = {
        str(task["external_verification_task_id"]): task
        for task in external_task_backlog
    }
    result_by_task_id: dict[str, dict] = {}
    errors: list[str] = []
    for result in verification_results:
        task_id = str(
            result.get("external_verification_task_id") or ""
        )
        decision = str(result.get("decision") or "")
        reason = str(result.get("reason") or "").strip()
        raw_evidence_urls = result.get("evidence_urls", [])
        evidence_urls: list[str] = []
        if isinstance(raw_evidence_urls, list):
            evidence_urls = [
                str(value)
                for value in raw_evidence_urls
                if str(value).strip()
            ]
        elif raw_evidence_urls:
            evidence_urls = parse_json_list(raw_evidence_urls)
        if not task_id:
            errors.append("외부 검증 결과에 task ID가 없습니다.")
            continue
        if task_id not in task_by_id:
            errors.append(f"외부 검증 backlog에 없는 task입니다: {task_id}")
            continue
        if task_id in result_by_task_id:
            errors.append(f"외부 검증 task 결과가 중복됐습니다: {task_id}")
            continue
        if decision not in decision_statuses:
            errors.append(
                f"허용되지 않은 외부 검증 decision입니다: {decision}"
            )
            continue
        if not reason:
            errors.append(f"외부 검증 reason이 없습니다: {task_id}")
            continue
        if decision != unverifiable_decision and not evidence_urls:
            errors.append(
                f"참·거짓 판정에 evidence URL이 없습니다: {task_id}"
            )
            continue
        result_by_task_id[task_id] = {
            **result,
            "external_verification_task_id": task_id,
            "decision": decision,
            "reason": reason,
            "evidence_urls": evidence_urls,
        }
    if errors:
        raise ValueError(
            "외부 사실 검증 결과가 유효하지 않습니다: "
            + " | ".join(errors)
        )

    final_results = truth_gate_results.copy()
    final_results["external_verification_task_id"] = ""
    final_results["external_decision"] = ""
    final_results["external_evidence_urls_json"] = "[]"
    final_results["external_reason"] = ""
    final_results["external_verifier"] = ""
    final_results["external_verified_at"] = ""
    updated_gate_ids: set[str] = set()
    decision_counts: dict[str, int] = {}
    for task_id, result in result_by_task_id.items():
        task = task_by_id[task_id]
        decision = str(result["decision"])
        decision_counts[decision] = decision_counts.get(decision, 0) + 1
        gate_ids = {
            str(gate_id)
            for gate_id in task["supporting_truth_gate_ids"]
        }
        matched_mask = final_results["truth_gate_id"].isin(gate_ids)
        matched_gate_ids = set(
            final_results.loc[matched_mask, "truth_gate_id"]
        )
        missing_gate_ids = gate_ids.difference(matched_gate_ids)
        if missing_gate_ids:
            raise ValueError(
                "외부 검증 task가 없는 truth gate를 참조합니다: "
                + ", ".join(sorted(missing_gate_ids))
            )
        final_results.loc[
            matched_mask,
            "truth_gate_status",
        ] = decision_statuses[decision]
        final_results.loc[
            matched_mask,
            "truth_gate_reason",
        ] = str(result["reason"])
        final_results.loc[
            matched_mask,
            "external_verification_task_id",
        ] = task_id
        final_results.loc[
            matched_mask,
            "external_decision",
        ] = decision
        final_results.loc[
            matched_mask,
            "external_evidence_urls_json",
        ] = dumps(result["evidence_urls"], ensure_ascii=False)
        final_results.loc[
            matched_mask,
            "external_reason",
        ] = str(result["reason"])
        final_results.loc[
            matched_mask,
            "external_verifier",
        ] = str(result.get("verifier") or "")
        final_results.loc[
            matched_mask,
            "external_verified_at",
        ] = str(result.get("verified_at") or "")
        updated_gate_ids.update(matched_gate_ids)
    return final_results, {
        "status": "COMPLETED",
        "input_result_count": len(verification_results),
        "applied_task_count": len(result_by_task_id),
        "updated_gate_count": len(updated_gate_ids),
        "decision_counts": decision_counts,
        "remaining_task_count": (
            len(external_task_backlog) - len(result_by_task_id)
        ),
    }
