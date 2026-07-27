from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any


PROBLEM_SCORE_MAX = {
    "target_difficulty_fit": 4,
    "choice_quality": 6,
}
EXPLANATION_SCORE_KEYS = [
    "correct_answer_reason",
    "clue_usage",
    "distractor_elimination",
    "answer_explanation_match",
    "explanation_factuality",
]
RELATION_CLAIM_MARKERS = [
    "발전",
    "해체",
    "이어",
    "계승",
    "통합",
    "전환",
    "계기",
    "원인",
    "결과",
    "이후",
    "뒤",
    "후에",
    "지원을 받아",
    "탄압으로",
]
GENERIC_G6_SHARED_UNITS = {
    "정부",
    "단체",
    "인물",
    "사건",
    "제도",
    "정책",
    "활동",
    "운동",
    "시기",
    "시대",
    "주체",
    "대상",
    "배경",
}
GENERIC_G6_SHARED_SUFFIXES = (
    "정부",
    "단체",
    "인물",
    "기관",
    "국가",
)
def script_dir() -> Path:
    return Path(__file__).resolve().parent


def latest_result_file(results_dir: Path) -> Path:
    files = sorted(results_dir.glob("eval_run_*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not files:
        raise FileNotFoundError(f"No eval_run_*.jsonl found in {results_dir}")
    return files[0]


def score_or_none(value: Any, minimum: int, maximum: int) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)) and int(value) == value:
        score = int(value)
        if minimum <= score <= maximum:
            return score
    return None


def decision_from_total(total: int) -> str:
    if total >= 14:
        return "accept"
    if total >= 12:
        return "accept_with_warning"
    if total >= 9:
        return "revise"
    return "regenerate"


def failed_gate_ids(parsed: dict[str, Any]) -> list[str]:
    gate = parsed.get("gate")
    if not isinstance(gate, dict):
        return []
    failed = []
    for key in sorted(gate):
        value = gate.get(key)
        if isinstance(value, dict) and status_text(value.get("status")) == "fail":
            failed.append(str(key))
    return failed


def repair_targets_from_gates(parsed: dict[str, Any]) -> list[str]:
    targets = []
    for gate_id in failed_gate_ids(parsed):
        if gate_id in {"G1", "G2"}:
            targets.append(f"{gate_id}: 형식/추출 텍스트 정리")
        elif gate_id == "G5":
            targets.append("G5: 역사 오류 오답 선지 교체")
        elif gate_id == "G6":
            targets.append("G6: 발문 단서 또는 정답 선지 표현 재작성")
        elif gate_id == "G3":
            targets.append("G3: 정답 후보 수 재설계")
        elif gate_id == "G4":
            targets.append("G4: 발문/자료 고증 재작성")
        else:
            targets.append(f"{gate_id}: 원인 확인")
    return targets


def decision_from_gate_failure(parsed: dict[str, Any]) -> str:
    failed = set(failed_gate_ids(parsed))
    if not failed:
        return "regenerate"
    if failed & {"G3", "G4"}:
        return "regenerate"
    if failed <= {"G1", "G2", "G5", "G6"}:
        return "repair"
    return "regenerate"


def status_text(value: Any) -> str:
    return str(value or "").strip().lower()


def boolish(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return status_text(value) in {"true", "yes", "pass"}


def list_from_value(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def has_relation_claim_marker(text: str) -> bool:
    return any(marker in str(text or "") for marker in RELATION_CLAIM_MARKERS)


def g5_classification_requires_fail(classification: Any, choice_text: str | None = None) -> bool:
    if not isinstance(classification, dict):
        return False
    kind = status_text(classification.get("type"))
    should_fail = bool(classification.get("should_fail_g5"))
    actual_subject = str(classification.get("actual_subject_if_valid_other_fact") or "").strip()
    valid_other_status = status_text(classification.get("valid_other_fact_status"))
    is_fake_or_mixed = bool(classification.get("is_fake_or_mixed_fact"))
    has_relation_marker = bool(choice_text and has_relation_claim_marker(choice_text))
    if kind in {"valid_other_fact", "uncertain"}:
        return False
    if actual_subject or valid_other_status in {"yes", "plausible", "uncertain"}:
        return False
    if kind in {"false_actor_action", "false_time"} and not has_relation_marker and not is_fake_or_mixed:
        return False
    hard_fail_kinds = {
        "nonexistent_term_or_fact",
        "mixed_fact_hybrid",
        "fabricated_relation",
        "false_causality",
        "false_sequence",
        "false_result",
    }
    if kind in hard_fail_kinds:
        return should_fail or is_fake_or_mixed
    if kind in {"false_actor_action", "false_time"}:
        return should_fail and (is_fake_or_mixed or has_relation_marker or valid_other_status == "no")
    return should_fail and is_fake_or_mixed


def relation_claim_has_unsupported_parts(item: dict[str, Any], choice_text: str | None = None) -> bool:
    if not choice_text or not has_relation_claim_marker(choice_text):
        return False
    classification = item.get("g5_error_classification")
    if isinstance(classification, dict):
        kind = status_text(classification.get("type"))
        actual_subject = str(classification.get("actual_subject_if_valid_other_fact") or "").strip()
        valid_other_status = status_text(classification.get("valid_other_fact_status"))
        is_fake_or_mixed = bool(classification.get("is_fake_or_mixed_fact"))
        if kind in {"valid_other_fact", "uncertain"}:
            return False
        if actual_subject or valid_other_status in {"yes", "plausible", "uncertain"}:
            return False
        if not is_fake_or_mixed and kind not in {
            "nonexistent_term_or_fact",
            "mixed_fact_hybrid",
            "fabricated_relation",
            "false_causality",
            "false_sequence",
            "false_result",
        }:
            return False
    validity = item.get("whole_claim_validity")
    if not isinstance(validity, dict):
        return False
    return bool(list_from_value(validity.get("false_or_unsupported_parts")))


def meaningful_partial_g6_shared_units(g6_equivalence: dict[str, Any]) -> list[str]:
    meaningful: list[str] = []
    for raw_unit in list_from_value(g6_equivalence.get("shared_meaning_units")):
        unit = str(raw_unit or "").strip()
        if not unit:
            continue
        compact = unit.replace(" ", "")
        if compact in GENERIC_G6_SHARED_UNITS:
            continue
        if "관련" in compact:
            continue
        if any(compact.endswith(suffix) for suffix in GENERIC_G6_SHARED_SUFFIXES):
            continue
        meaningful.append(unit)
    return meaningful


def has_partial_g6_text_anchor(parsed: dict[str, Any]) -> bool:
    exposure = parsed.get("_client_answer_exposure_check")
    if not isinstance(exposure, dict):
        return False
    shared_tokens = list_from_value(exposure.get("shared_tokens"))
    answer_lcs = score_or_none(exposure.get("answer_lcs"), 0, 999)
    max_other_lcs = score_or_none(exposure.get("max_other_lcs"), 0, 999)
    return bool(shared_tokens) and answer_lcs is not None and max_other_lcs is not None and answer_lcs > max_other_lcs


def recompute_gate_result(parsed: dict[str, Any]) -> None:
    gate = parsed.get("gate")
    if not isinstance(gate, dict):
        return
    statuses = [
        status_text(item.get("status"))
        for item in gate.values()
        if isinstance(item, dict)
    ]
    if "fail" in statuses:
        parsed["gate_result"] = "FAIL"
    elif "uncertain" in statuses:
        parsed["gate_result"] = "uncertain"
    elif statuses:
        parsed["gate_result"] = "PASS"


def apply_client_gate_checks(parsed: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    issues: list[str] = []
    gate = parsed.setdefault("gate", {})

    has_choice_verification = any(isinstance(item, dict) for item in list_from_value(parsed.get("choice_verification")))
    verified_g5_failure_detected = False
    g5_fail_choices = []
    for item in list_from_value(parsed.get("choice_verification")):
        if not isinstance(item, dict):
            continue
        classification = item.get("g5_error_classification")
        choice_text = str(item.get("original_choice_text") or "")
        if g5_classification_requires_fail(classification, choice_text) or relation_claim_has_unsupported_parts(
            item, choice_text
        ):
            g5_fail_choices.append(str(item.get("choice")))
    if g5_fail_choices:
        gate["G5"] = {
            "status": "FAIL",
            "reason": "client consistency check: choice_verification에서 G5 FAIL 오답이 보고됨. "
            f"g5_fail_choices={g5_fail_choices}",
        }
        parsed["gate_result"] = "FAIL"
        issues.append("G5 set by client consistency check")
        verified_g5_failure_detected = True

    condition_check = parsed.get("stem_condition_check")
    if isinstance(condition_check, dict):
        count = score_or_none(condition_check.get("satisfying_choice_count"), 0, 5)
        if count is not None and count != 1:
            gate["G3"] = {
                "status": "FAIL",
                "reason": "client consistency check: 발문 조건 충족 선택지 수가 1개가 아님. "
                f"satisfying_choice_count={count}, choices={condition_check.get('satisfying_choices')}",
            }
            parsed["gate_result"] = "FAIL"
            issues.append("G3 set by client consistency check")

    g6_equivalence = parsed.get("g6_claim_equivalence_check")
    if isinstance(g6_equivalence, dict):
        relation = status_text(g6_equivalence.get("relation"))
        should_fail = bool(g6_equivalence.get("g6_should_fail"))
        text_match = bool(g6_equivalence.get("can_answer_by_text_matching_without_history"))
        meaningful_shared = meaningful_partial_g6_shared_units(g6_equivalence)
        partial_text_anchor = has_partial_g6_text_anchor(parsed)
        if should_fail or relation in {"same_core_claim", "direct_copy", "external_bias"} or (
            relation == "partial_same_claim" and (text_match or (bool(meaningful_shared) and partial_text_anchor))
        ):
            gate["G6"] = {
                "status": "FAIL",
                "reason": "client consistency check: g6_claim_equivalence_check가 G6 FAIL 조건을 보고함. "
                f"relation={g6_equivalence.get('relation')}, "
                f"g6_should_fail={g6_equivalence.get('g6_should_fail')}, "
                f"can_answer_by_text_matching_without_history={g6_equivalence.get('can_answer_by_text_matching_without_history')}, "
                f"meaningful_shared_units={meaningful_shared}, "
                f"partial_text_anchor={partial_text_anchor}",
            }
            parsed["gate_result"] = "FAIL"
            issues.append("G6 set by client consistency check")

    gate_consistency = parsed.get("gate_consistency_check")
    if isinstance(gate_consistency, dict):
        reported_g5_fail = [str(item) for item in list_from_value(gate_consistency.get("g5_fail_choices"))]
        verified_reported_g5_fail = []
        if reported_g5_fail:
            by_label = {
                str(item.get("choice")): item
                for item in list_from_value(parsed.get("choice_verification"))
                if isinstance(item, dict)
            }
            for label in reported_g5_fail:
                item = by_label.get(label)
                if not item:
                    continue
                choice_text = str(item.get("original_choice_text") or "")
                if g5_classification_requires_fail(
                    item.get("g5_error_classification"), choice_text
                ) or relation_claim_has_unsupported_parts(item, choice_text):
                    verified_reported_g5_fail.append(label)
        reported_count = score_or_none(gate_consistency.get("satisfying_choice_count_from_choice_verification"), 0, 5)
        reported_g6 = bool(gate_consistency.get("g6_equivalence_requires_fail"))
        if verified_reported_g5_fail:
            gate["G5"] = {
                "status": "FAIL",
                "reason": "client consistency check: gate_consistency_check가 G5 FAIL 오답을 보고함. "
                f"g5_fail_choices={verified_reported_g5_fail}",
            }
            parsed["gate_result"] = "FAIL"
            issues.append("G5 set by gate consistency check")
            verified_g5_failure_detected = True
        elif reported_g5_fail:
            issues.append(f"ignored unverified G5 choices from gate consistency check: {reported_g5_fail}")
        if reported_count is not None and reported_count != 1:
            gate["G3"] = {
                "status": "FAIL",
                "reason": "client consistency check: gate_consistency_check의 정답 후보 수가 1개가 아님. "
                f"count={reported_count}",
            }
            parsed["gate_result"] = "FAIL"
            issues.append("G3 set by gate consistency check")
        if reported_g6:
            gate["G6"] = {
                "status": "FAIL",
                "reason": "client consistency check: gate_consistency_check가 G6 FAIL 필요를 보고함.",
            }
            parsed["gate_result"] = "FAIL"
            issues.append("G6 set by gate consistency check")

    g5_status = gate.get("G5")
    if (
        has_choice_verification
        and not verified_g5_failure_detected
        and isinstance(g5_status, dict)
        and status_text(g5_status.get("status")) == "fail"
    ):
        gate["G5"] = {
            "status": "PASS",
            "reason": "client consistency check: 선택지 구조화 검증에서 확인된 G5 FAIL 오답이 없어 모델의 G5 FAIL을 해제함.",
        }
        issues.append("G5 model fail cleared by client consistency check")

    recompute_gate_result(parsed)
    return parsed, issues


def effective_distractor_score(target_score: int, effective_count: int) -> int:
    if target_score == 1:
        if effective_count >= 3:
            return 4
        if effective_count == 2:
            return 3
        if effective_count == 1:
            return 2
        return 0
    if target_score == 2:
        if effective_count >= 2:
            return 4
        if effective_count == 1:
            return 3
        return 0
    if target_score == 3:
        if effective_count >= 3:
            return 4
        if effective_count == 2:
            return 3
        if effective_count == 1:
            return 2
        return 0
    return 0


def count_effective_distractors(parsed: dict[str, Any], target_score: int | None = None) -> int | None:
    detail = parsed.get("problem_score_detail") or {}
    choice_quality = detail.get("choice_quality")
    if isinstance(choice_quality, dict):
        count = score_or_none(choice_quality.get("effective_count"), 0, 4)
        if count is not None:
            return count

    analysis = parsed.get("effective_distractor_analysis")
    if isinstance(analysis, dict):
        if target_score == 1:
            return sum(
                1
                for item in analysis.values()
                if isinstance(item, dict)
                and boolish(item.get("historically_valid"))
                and boolish(item.get("category_or_period_accessible"))
            )
        return sum(1 for item in analysis.values() if isinstance(item, dict) and boolish(item.get("is_effective_attractive")))
    return None


def target_name_exposure_difficulty_cap(parsed: dict[str, Any]) -> int | None:
    target_score = score_or_none(parsed.get("target_score"), 1, 3)
    if target_score not in {2, 3}:
        return None

    g6_equivalence = parsed.get("g6_claim_equivalence_check")
    relation = status_text(g6_equivalence.get("relation")) if isinstance(g6_equivalence, dict) else ""
    g6_overlap = parsed.get("g6_overlap_check")
    overlap = status_text(g6_overlap.get("overlap_type")) if isinstance(g6_overlap, dict) else ""
    if "target_name_exposure" not in {relation, overlap}:
        return None
    return 2 if target_score == 2 else 1


def normalize(parsed: dict[str, Any]) -> dict[str, Any]:
    issues: list[str] = []
    parsed, gate_check_issues = apply_client_gate_checks(parsed)
    issues.extend(gate_check_issues)
    gate_result = str(parsed.get("gate_result") or "").strip().lower()

    if gate_result == "fail":
        parsed["problem_score"] = None
        parsed["explanation_score"] = None
        parsed["total_score"] = None
        parsed["failed_gates"] = failed_gate_ids(parsed)
        parsed["repair_targets"] = repair_targets_from_gates(parsed)
        parsed["final_decision"] = decision_from_gate_failure(parsed)
    elif gate_result == "uncertain":
        parsed["problem_score"] = None
        parsed["explanation_score"] = None
        parsed["total_score"] = None
        parsed["final_decision"] = "needs_verification"
    elif gate_result == "pass":
        problem_detail = parsed.get("problem_score_detail") or {}
        difficulty = problem_detail.get("target_difficulty_fit")
        if isinstance(difficulty, dict):
            cap = target_name_exposure_difficulty_cap(parsed)
            raw_difficulty = score_or_none(difficulty.get("score"), 0, 4)
            if cap is not None and raw_difficulty is not None and raw_difficulty > cap:
                issues.append(f"target_difficulty_fit capped by target_name_exposure: {raw_difficulty} -> {cap}")
                difficulty["score"] = cap
                reason = str(difficulty.get("reason") or "").strip()
                suffix = f"client normalized: 대상명 직접 노출로 목표 난이도 최대 {cap}점 적용."
                difficulty["reason"] = f"{reason} {suffix}".strip()

        choice_quality = problem_detail.get("choice_quality")
        if isinstance(choice_quality, dict):
            response_score = score_or_none(choice_quality.get("response_category_fit_score"), 0, 1)
            duplicate_score = score_or_none(choice_quality.get("no_duplicate_or_inclusion_score"), 0, 1)
            target_score = score_or_none(parsed.get("target_score"), 1, 3)
            effective_count = count_effective_distractors(parsed, target_score)
            effective_score = None
            if effective_count is not None and target_score is not None:
                effective_score = effective_distractor_score(target_score, effective_count)
                if choice_quality.get("effective_attractive_distractor_score") != effective_score:
                    issues.append(
                        "effective_attractive_distractor_score recalculated: "
                        f"{choice_quality.get('effective_attractive_distractor_score')} -> {effective_score}"
                    )
                choice_quality["effective_attractive_distractor_score"] = effective_score
            if response_score is not None and duplicate_score is not None and effective_score is not None:
                recalculated = response_score + duplicate_score + effective_score
                if choice_quality.get("score") != recalculated:
                    issues.append(f"choice_quality recalculated: {choice_quality.get('score')} -> {recalculated}")
                choice_quality["score"] = recalculated
                choice_quality["reason"] = (
                    "client normalized: "
                    f"응답 범주 {response_score}/1, 중복·포함 관계 {duplicate_score}/1, "
                    f"오답 품질 기준 충족 {effective_count}개 -> {effective_score}/4"
                )

        problem_scores = []
        for key, max_score in PROBLEM_SCORE_MAX.items():
            score = score_or_none((problem_detail.get(key) or {}).get("score"), 0, max_score)
            if score is None:
                issues.append(f"invalid problem score: {key}")
            else:
                problem_scores.append(score)

        explanation_detail = parsed.get("explanation_score_detail") or {}
        explanation_scores = []
        for key in EXPLANATION_SCORE_KEYS:
            score = score_or_none((explanation_detail.get(key) or {}).get("score"), 0, 1)
            if score is None:
                issues.append(f"invalid explanation score: {key}")
            else:
                explanation_scores.append(score)

        if len(problem_scores) == len(PROBLEM_SCORE_MAX):
            recalculated = sum(problem_scores)
            if parsed.get("problem_score") != recalculated:
                issues.append(f"problem_score recalculated: {parsed.get('problem_score')} -> {recalculated}")
            parsed["problem_score"] = recalculated

        if len(explanation_scores) == len(EXPLANATION_SCORE_KEYS):
            recalculated = sum(explanation_scores)
            if parsed.get("explanation_score") != recalculated:
                issues.append(f"explanation_score recalculated: {parsed.get('explanation_score')} -> {recalculated}")
            parsed["explanation_score"] = recalculated

        if isinstance(parsed.get("problem_score"), int) and isinstance(parsed.get("explanation_score"), int):
            total = parsed["problem_score"] + parsed["explanation_score"]
            if parsed.get("total_score") != total:
                issues.append(f"total_score recalculated: {parsed.get('total_score')} -> {total}")
            parsed["total_score"] = total
            decision = decision_from_total(total)
            if parsed.get("final_decision") != decision:
                issues.append(f"final_decision recalculated: {parsed.get('final_decision')} -> {decision}")
            parsed["final_decision"] = decision
    else:
        issues.append(f"unknown gate_result: {parsed.get('gate_result')}")

    parsed["_client_validation"] = {"normalized": bool(issues), "issues": issues}
    return parsed


def weak_reasons(detail: dict[str, Any], max_scores: dict[str, int]) -> str:
    reasons = []
    for key, value in detail.items():
        if not isinstance(value, dict):
            continue
        score = value.get("score")
        max_score = max_scores.get(key)
        if max_score is not None and isinstance(score, int) and score < max_score:
            reason = str(value.get("reason") or "").replace("\n", " ")
            reasons.append(f"{key} {score}: {reason}")
    return " / ".join(reasons)


def gate_fail_reasons(parsed: dict[str, Any]) -> str:
    gate = parsed.get("gate")
    if not isinstance(gate, dict):
        return ""
    reasons = []
    for key, value in gate.items():
        if not isinstance(value, dict):
            continue
        if str(value.get("status") or "").strip().lower() == "fail":
            reason = str(value.get("reason") or "").replace("\n", " ")
            reasons.append(f"{key}: {reason}")
    return " / ".join(reasons)


def main() -> int:
    parser = argparse.ArgumentParser(description="Summarize and normalize API evaluation results.")
    parser.add_argument("--input", type=Path, default=None)
    parser.add_argument("--out-dir", type=Path, default=script_dir() / "results")
    args = parser.parse_args()

    input_path = args.input or latest_result_file(args.out_dir)
    rows = []
    normalized_jsonl = args.out_dir / f"{input_path.stem}_normalized.jsonl"
    summary_csv = args.out_dir / f"{input_path.stem}_summary.csv"
    summary_md = args.out_dir / f"{input_path.stem}_summary.md"

    with input_path.open("r", encoding="utf-8-sig") as src, normalized_jsonl.open("w", encoding="utf-8") as norm:
        for line in src:
            outer = json.loads(line)
            parsed = outer.get("parsed")
            if not isinstance(parsed, dict):
                continue
            parsed = normalize(parsed)
            outer["parsed"] = parsed
            norm.write(json.dumps(outer, ensure_ascii=False) + "\n")

            gate_result = str(parsed.get("gate_result") or "").strip().lower()
            validation_parts = [
                " / ".join(parsed.get("_client_validation", {}).get("issues", [])),
                gate_fail_reasons(parsed),
            ]
            validation_issues = " / ".join(part for part in validation_parts if part)
            problem_weak = ""
            explanation_weak = ""
            if gate_result == "pass":
                problem_weak = weak_reasons(parsed.get("problem_score_detail") or {}, PROBLEM_SCORE_MAX)
                explanation_weak = weak_reasons(
                    parsed.get("explanation_score_detail") or {},
                    {key: 1 for key in EXPLANATION_SCORE_KEYS},
                )

            rows.append(
                {
                    "question_id": parsed.get("question_id"),
                    "target_score": parsed.get("target_score"),
                    "gate_result": parsed.get("gate_result"),
                    "failed_gates": ", ".join(failed_gate_ids(parsed)),
                    "repair_targets": " / ".join(repair_targets_from_gates(parsed)),
                    "problem_score": parsed.get("problem_score"),
                    "explanation_score": parsed.get("explanation_score"),
                    "total_score": parsed.get("total_score"),
                    "final_decision": parsed.get("final_decision"),
                    "problem_weak": problem_weak,
                    "explanation_weak": explanation_weak,
                    "validation_issues": validation_issues or gate_fail_reasons(parsed),
                    "usage_total_tokens": (outer.get("usage") or {}).get("total_tokens"),
                }
            )

    with summary_csv.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()) if rows else [])
        writer.writeheader()
        writer.writerows(rows)

    lines = [
        f"# Evaluation Summary: {input_path.name}",
        "",
        "| 문항 | 배점 | Gate | 판정 | 문제 | 해설 | 총점 | 실패 Gate | 수리/재생성 대상 | 주요 감점/검증 |",
        "|---:|---:|---|---|---:|---:|---:|---|---|---|",
    ]
    for row in rows:
        note = row["problem_weak"] or row["explanation_weak"] or row["validation_issues"] or ""
        lines.append(
            "| {question_id} | {target_score} | {gate_result} | {final_decision} | {problem_score} | "
            "{explanation_score} | {total_score} | {failed_gates} | {repair_targets} | {note} |".format(
                **row,
                note=note.replace("|", "/"),
            )
        )
    summary_md.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(f"normalized: {normalized_jsonl}")
    print(f"summary_csv: {summary_csv}")
    print(f"summary_md: {summary_md}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
