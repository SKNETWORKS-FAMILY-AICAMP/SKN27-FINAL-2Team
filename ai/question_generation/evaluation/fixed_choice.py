"""검수된 고정 선택지를 쓰는 문항의 생성 텍스트만 평가한다."""

from __future__ import annotations

import json
from typing import Any


GATES = ("G2", "G3", "G4", "G6")


def build_messages(record: dict[str, Any]) -> list[dict[str, str]]:
    """선지 품질표 없이 지문·발문 의미만 확인하는 짧은 Judge 요청을 만든다."""
    source = record.get("material_source") or {}
    chronology = record.get("chronology") or {}
    context = {
        "choice_mode": record["choice_mode"],
        "material": record["material"],
        "material_basis": source.get("basis"),
        "question": record["question"],
        "choices": [
            {key: choice.get(key) for key in ("label", "text", "image_id") if choice.get(key)}
            for choice in record["choices"]
        ],
        "answer_label": record["answer_label"],
        "verification_basis": [
            {key: row.get(key) for key in ("choice", "owner_label", "fact_basis") if row.get(key)}
            for row in record.get("verification_basis") or []
        ],
        "chronology": {
            key: chronology.get(key)
            for key in ("event_labels", "correct_order", "timeline_positions", "correct_position")
            if chronology.get(key)
        },
        "relation_axis_id": record.get("relation_axis_id"),
        "stem_pattern": record.get("stem_pattern"),
        "question_task_instruction": record.get("question_task_instruction"),
    }
    return [
        {
            "role": "system",
            "content": (
                "너는 검수된 고정 선택지를 사용하는 한국사 문항의 최종 Judge다. "
                "선택지는 pack과 코드가 검증했으므로 다시 채점하거나 10점 점수표를 적용하지 않는다. "
                "LLM이 생성한 지문과 발문만 검사하고 JSON 객체만 출력한다."
            ),
        },
        {
            "role": "user",
            "content": f"""
다음 문항에서 필요한 네 항목만 검사하라.
- G2: 지문과 발문이 자연스럽고 서로 맞는가
- G3: 지문을 기준으로 표시 정답이 유일한가
- G4: 지문과 발문이 제공된 근거를 왜곡하지 않는가
- G6: 대상명·정답 순서·정답 위치가 지문이나 발문에 직접 노출되지 않는가

order와 timeline_position의 순서·위치 및 image의 이미지-owner 대응은 이미 코드로 검증됐다. 이를 다시 평가하지 마라.
지문의 화자·시제·서술 형식이 일관되는지 확인하라. 사료·활동지·보고서 형식이 설정되지 않았는데 기록·조사·명령하는 문장으로 갑자기 바뀌거나, 지시어의 대상 또는 행동 주체가 불명확하면 G2를 FAIL로 판정하라.
실패 원인이 지문이면 material, 발문이면 question만 수리 대상으로 지정하라.

입력:
{json.dumps(context, ensure_ascii=False, indent=2)}

출력 형식:
{{
  "evaluation_profile": "fixed_choice",
  "gate_result": "PASS|FAIL|uncertain",
  "failed_gates": ["G2|G3|G4|G6"],
  "gate": {{
    "G2": {{"status": "PASS|FAIL|uncertain", "reason": "..."}},
    "G3": {{"status": "PASS|FAIL|uncertain", "reason": "..."}},
    "G4": {{"status": "PASS|FAIL|uncertain", "reason": "..."}},
    "G6": {{"status": "PASS|FAIL|uncertain", "reason": "..."}}
  }},
  "problem_score": null,
  "repair_targets": ["material|question"],
  "final_decision": "accept|repair|needs_verification",
  "target_feedback": {{"material|question": "해당 부분만 고치는 구체적인 지시"}}
}}
""".strip(),
        },
    ]


def normalize_gate(parsed: dict[str, Any]) -> dict[str, Any]:
    """고정 선택지 Judge의 스키마와 상태 전이를 검증한다."""
    errors: list[str] = []
    gate = parsed.get("gate") if isinstance(parsed.get("gate"), dict) else {}
    statuses = {name: str((gate.get(name) or {}).get("status") or "").upper() for name in GATES}
    invalid = [name for name, status in statuses.items() if status not in {"PASS", "FAIL", "UNCERTAIN"}]
    errors.extend(f"missing_or_invalid_{name}" for name in invalid)
    failed = [name for name, status in statuses.items() if status == "FAIL"]
    uncertain = [name for name, status in statuses.items() if status == "UNCERTAIN"]
    result = "FAIL" if failed else "uncertain" if invalid or uncertain else "PASS"
    if parsed.get("evaluation_profile") != "fixed_choice":
        errors.append("evaluation_profile_is_invalid")
    if str(parsed.get("gate_result") or "").upper() != result.upper():
        errors.append("gate_result_is_inconsistent")
    declared_failed = parsed.get("failed_gates")
    if (
        not isinstance(declared_failed, list)
        or any(not isinstance(name, str) for name in declared_failed)
        or set(declared_failed) != set(failed)
    ):
        errors.append("failed_gates_are_inconsistent")
    targets = parsed.get("repair_targets")
    if (
        not isinstance(targets, list)
        or any(not isinstance(target, str) for target in targets)
        or len(targets) != len(set(targets))
        or any(target not in {"material", "question"} for target in targets)
    ):
        errors.append("repair_targets_are_invalid")
        targets = []
    decision = parsed.get("final_decision")
    expected_decision = "accept" if result == "PASS" else "repair" if result == "FAIL" else "needs_verification"
    if decision != expected_decision:
        errors.append("final_decision_is_inconsistent")
    if (result == "PASS" or result == "uncertain") and targets:
        errors.append("non_failed_result_must_not_have_repair_targets")
    if result == "FAIL" and not targets:
        errors.append("failed_result_requires_repair_target")
    feedback = parsed.get("target_feedback")
    if not isinstance(feedback, dict) or set(feedback) != set(targets) or any(not str(value).strip() for value in feedback.values()):
        errors.append("target_feedback_is_invalid")
    if parsed.get("problem_score") is not None:
        errors.append("fixed_choice_must_not_have_problem_score")
    if errors:
        parsed.update({
            "judge_output_errors": list(dict.fromkeys(errors)),
            "gate_result": "uncertain",
            "failed_gates": [],
            "problem_score": None,
            "repair_targets": [],
            "final_decision": "needs_verification",
            "target_feedback": {},
        })
        return parsed
    parsed["gate_result"] = result
    parsed["failed_gates"] = failed
    parsed["problem_score"] = None
    return parsed
