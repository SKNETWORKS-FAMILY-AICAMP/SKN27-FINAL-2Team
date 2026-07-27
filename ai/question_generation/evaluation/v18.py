"""완성 문항을 v1.8 Gate와 10점 품질표로 평가하는 독립 LLM judge.

입력은 단일 ``question`` 체크포인트 또는 ``questions`` 배열이다. 각 문항을 한 번의
OpenAI 호출로 Gate 판정과 채점까지 처리하고 원본 JSONL과 사람용 Markdown을 저장한다.
이 모듈은 문항을 수정하거나 재생성하지 않는다.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
from collections import Counter
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from openai import OpenAI

from ai.question_generation.core.text import compact
from ai.question_generation.evaluation.fixed_choice import build_messages as build_fixed_messages
from ai.question_generation.evaluation.fixed_choice import normalize_gate as normalize_fixed_gate


PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_RUBRIC = PROJECT_ROOT / "docs" / "hanneung_sllm_eval_rubric_v1_8.md"
DEFAULT_FEW_SHOT = PROJECT_ROOT / "ai" / "question_generation" / "evaluation" / "v18_few_shot_examples.json"
DEFAULT_OUT_DIR = PROJECT_ROOT / "ai" / "question_generation" / "outputs"
DEFAULT_MODEL = "gpt-5.6-terra"
DEFAULT_MIN_ACCEPT_SCORE = 8
LABELS = ("①", "②", "③", "④", "⑤")
GATES = ("G1", "G2", "G3", "G4", "G5", "G6")


def parse_args() -> argparse.Namespace:
    """평가 입력·모델·출력 위치와 재시도 옵션을 읽는다."""
    parser = argparse.ArgumentParser(description="Evaluate assembled questions with the v1.8.6 Gate rubric.")
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--rubric", type=Path, default=DEFAULT_RUBRIC)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument("--output-prefix", type=Path)
    parser.add_argument("--model", default=os.getenv("OPENAI_EVAL_MODEL") or os.getenv("OPENAI_CHAT_MODEL") or DEFAULT_MODEL)
    parser.add_argument("--base-url", default=os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1"))
    parser.add_argument("--limit", type=int)
    parser.add_argument("--question-id")
    parser.add_argument("--sleep", type=float, default=0.0)
    parser.add_argument("--timeout", type=int, default=120)
    parser.add_argument("--max-retries", type=int, default=2)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--resume-from", type=Path)
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def records_from_assembled(data: dict[str, Any]) -> list[dict[str, Any]]:
    """단일/배치 문항 JSON을 평가 프롬프트용 공통 record 목록으로 변환한다."""
    records = []
    questions = data.get("questions") or ([data["question"]] if isinstance(data.get("question"), dict) else [])
    for index, question in enumerate(questions, start=1):
        choices = []
        verification_basis = []
        for choice in sorted(question.get("choices", []), key=lambda item: item.get("number") or 999):
            number = int(choice.get("number") or len(choices) + 1)
            label = LABELS[number - 1] if 1 <= number <= 5 else str(number)
            choices.append(
                {
                    "label": label,
                    "text": compact(choice.get("text")),
                    "is_answer": bool(choice.get("is_answer")),
                    "image_id": (choice.get("image") or {}).get("image_chunk_id"),
                }
            )
            source = choice.get("source") if isinstance(choice.get("source"), dict) else {}
            if source:
                verification_basis.append(
                    {
                        "choice": label,
                        "role": source.get("role"),
                        "owner_id": source.get("owner_id"),
                        "owner_label": source.get("owner_label"),
                        "fact_basis": [source.get("fact_basis")] if source.get("fact_basis") else [],
                        "evidence_chunk_ids": source.get("evidence_chunk_ids") or [],
                    }
                )
        answer = next((choice["label"] for choice in choices if choice["is_answer"]), "")
        record = {
                "question_id": str(question.get("seed_id") or index),
                "index": index,
                "topic": question.get("topic"),
                "target_score": question.get("target_score"),
                "material": compact(question.get("material")),
                "question": compact(question.get("question")),
                "choice_mode": str(question.get("choice_mode") or "generated"),
                "choices": choices,
                "answer_label": answer,
            }
        for key in ("relation_axis_id", "stem_pattern", "question_task_instruction"):
            record[key] = question.get(key)
        if question.get("material_source"):
            record["material_source"] = question["material_source"]
        if question.get("chronology"):
            record["chronology"] = question["chronology"]
        record["verification_basis"] = verification_basis or question.get("verification_basis")
        hash_payload = {key: value for key, value in record.items() if key != "index"}
        record["question_hash"] = hashlib.sha256(
            json.dumps(hash_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        records.append(record)
    return records


def select_records(records: list[dict[str, Any]], question_id: str | None, limit: int | None) -> list[dict[str, Any]]:
    """질문 ID와 개수 옵션에 따라 평가할 record만 남긴다."""
    if question_id:
        records = [record for record in records if record["question_id"] == question_id or str(record["index"]) == question_id]
    if limit is not None:
        records = records[:limit]
    return records


def pending_records(records: list[dict[str, Any]], rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """체크포인트에 없는 문항만 반환한다."""
    completed = {(str(row.get("question_id")), str(row.get("question_hash") or "")) for row in rows}
    return [
        record for record in records
        if (record["question_id"], str(record.get("question_hash") or "")) not in completed
    ]


def choice_text(record: dict[str, Any]) -> str:
    """5개 선택지를 번호가 붙은 평가 프롬프트 문자열로 렌더링한다."""
    return "\n".join(f"{choice['label']} {choice['text']}" for choice in record["choices"])


def verification_basis_text(record: dict[str, Any]) -> str:
    """선지별 사실 근거를 judge가 읽을 JSON 문자열로 만든다."""
    rows = record.get("verification_basis") or []
    if not rows:
        return "제공되지 않음"
    return json.dumps(rows, ensure_ascii=False, indent=2)


def generation_contract_text(record: dict[str, Any]) -> str:
    """관계축·발문 패턴·출제 지시를 평가용 계약 문자열로 만든다."""
    contract = {
        key: record.get(key)
        for key in ("relation_axis_id", "stem_pattern", "question_task_instruction")
        if record.get(key)
    }
    return json.dumps(contract, ensure_ascii=False, indent=2) if contract else "제공되지 않음"


@lru_cache(maxsize=1)
def evaluation_few_shot_messages() -> tuple[dict[str, str], ...]:
    """Gate와 점수 경계 사례를 최종 출력 스키마와 분리된 calibration 대화로 만든다."""
    data = json.loads(DEFAULT_FEW_SHOT.read_text(encoding="utf-8-sig"))
    messages: list[dict[str, str]] = []
    examples = [*(data.get("examples") or []), *(data.get("score_examples") or [])]
    for example in examples:
        messages.extend(
            [
                {
                    "role": "user",
                    "content": (
                        "다음은 Gate·점수 판정 calibration 사례다. 최종 출력 형식이 아니라 판정 기준만 학습한다.\n"
                        + json.dumps(example.get("case") or {}, ensure_ascii=False)
                    ),
                },
                {
                    "role": "assistant",
                    "content": json.dumps(
                        {"calibration_only": True, **(example.get("expected") or {})},
                        ensure_ascii=False,
                    ),
                },
            ]
        )
    return tuple(messages)


def build_messages(rubric: str, record: dict[str, Any], min_accept_score: int = DEFAULT_MIN_ACCEPT_SCORE) -> list[dict[str, Any]]:
    """문항, 선지 근거, 관계축 계약, 평가표를 하나의 judge 요청으로 조립한다."""
    if record.get("choice_mode") != "generated":
        return build_fixed_messages(record)
    system = f"""
너는 한국사능력검정시험 심화 문항을 검수하는 LLM judge이다.
아래 문서의 v1.8.6 Gate와 문제 10점 평가표만 적용한다. 별도 자체 점수표나 임의 기준을 만들지 않는다.
입력은 DB·형식·개수·마커·중복 코드 Gate를 통과했다. G1의 기계적 구조 검사는 되풀이하지 않는다.
G2에서는 각 선지가 실제 한능검 문장으로 자연스럽게 성립하는지 검수하고, G3~G5의 역사 의미와 G6의 의미 동일성 여부를 집중 검수한다.
Gate PASS이면 같은 응답에서 10점 채점을 끝낸다.

{rubric}

반드시 JSON 객체만 출력한다.
""".strip()
    user = f"""
다음 문항 1개를 v1.8.6 Gate로 먼저 평가하라.
Gate가 PASS일 때만 문제 10점(목표 난이도 4점 + 선택지 품질 6점)을 이어서 채점하라.
코드로 이미 통과한 구조 조건은 유지하고 역사적·의미적 오류의 근거가 있을 때만 FAIL로 바꾼다.

question_id: {record["question_id"]}
target_score: {record["target_score"]}
topic: {record.get("topic") or ""}

[자료]
{record["material"]}

[지문 검증 근거]
{json.dumps(record.get("material_source") or {}, ensure_ascii=False, indent=2)}

[발문]
{record["question"]}

[선택지]
{choice_text(record)}

[표시 정답]
{record["answer_label"]}

[문제은행 선지 검증 근거]
{verification_basis_text(record)}

[출제 관계축 계약]
{generation_contract_text(record)}

각 선지의 역사 사실성은 위 fact_basis와의 주체·행위·시기 관계를 우선 대조하라. 근거에 없는 관계를 임의로 보완하지 마라.
발문이 출제 관계축 계약과 다른 행동을 요구하면 G2 또는 G3을 FAIL로 판정하라.
지문의 화자·시제·서술 형식이 일관되는지 확인하라. 사료·활동지·보고서 형식이 설정되지 않았는데 기록·조사·명령하는 문장으로 갑자기 바뀌거나, 지시어의 대상 또는 행동 주체가 불명확하면 G2를 FAIL로 판정하라.
각 선지의 주어·서술어 호응, 조사·목적어 결합, 동사와 대상의 의미 관계를 별도로 확인하라.
역사적 의도를 추정할 수 있어도 객관적으로 비문이면 G2를 FAIL로 판정하되, 단순한 문체 취향이나 더 자연스러운 대안이 있다는 이유만으로 FAIL시키지 마라.
fact_basis에 맞춰 잘못된 조사·목적어·수식 관계를 마음속으로 고치거나 생략된 말을 보충하지 마라. 선지 원문 그대로 성립하는지를 판정하라.

출력 JSON 형식:
{{
  "question_id": "{record["question_id"]}",
  "target_score": {json.dumps(record["target_score"], ensure_ascii=False)},
  "gate_result": "PASS|FAIL|uncertain",
  "failed_gates": ["G1|G2|G3|G4|G5|G6"],
  "gate": {{
    "G1": {{"status": "PASS|FAIL|uncertain", "reason": "..."}},
    "G2": {{"status": "PASS|FAIL|uncertain", "reason": "..."}},
    "G3": {{"status": "PASS|FAIL|uncertain", "reason": "..."}},
    "G4": {{"status": "PASS|FAIL|uncertain", "reason": "..."}},
    "G5": {{"status": "PASS|FAIL|uncertain", "reason": "..."}},
    "G6": {{"status": "PASS|FAIL|uncertain", "reason": "..."}}
  }},
  "pre_gate_risk_scan": [
    {{"gate": "G3|G4|G5|G6", "risk": "...", "evidence": "...", "if_true_consequence": "FAIL|uncertain|score_only"}}
  ],
  "choice_verification_summary": [
    {{"choice": "①", "historically_valid": "yes|no|uncertain", "satisfies_stem_condition": "yes|no|uncertain", "g5_should_fail": false, "reason": "..."}}
  ],
  "g6_claim_equivalence_check": {{
    "relation": "none|weak_keyword|target_name_exposure|partial_same_claim|same_core_claim|direct_copy|external_bias",
    "can_answer_by_text_matching_without_history": false,
    "g6_should_fail": false,
    "reason": "..."
  }},
  "problem_score": null 또는 {{
    "actual_difficulty": 1,
    "difficulty_score": 0,
    "choice_quality_score": 0,
    "total_score": 0,
    "difficulty_evidence": {{
      "clue_bundle_count": 0,
      "clue_type": "A|B|C",
      "solving_step_type": "direct_select|identify_compare|relation_judgment",
      "solving_steps": "...",
      "knowledge_depth": "representative|standard_advanced|fine_grained",
      "matched_elements": 0,
      "reason": "..."
    }},
    "choice_evidence": {{
      "same_period_or_topic_count": 0,
      "attractive_distractor_count": 0,
      "category_violations": [],
      "overlap_pairs": [],
      "answer_style_bias": "none|weak|strong",
      "deductions": [{{"reason": "...", "points": 0}}],
      "reason": "..."
    }},
    "revision_targets": ["material|question|correct|choice:①|choice:②|choice:③|choice:④|choice:⑤"]
  }},
  "repair_targets": ["material|question|correct|choice:①|choice:②|choice:③|choice:④|choice:⑤"],
  "final_decision": "accept|repair|regenerate|needs_verification",
  "target_feedback": {{"material|question|correct|choice:①|choice:②|choice:③|choice:④|choice:⑤": "..."}}
}}

repair_targets와 revision_targets는 실제 문제가 있는 구성 요소만 쓴다. 문제가 없으면 빈 배열로 둔다.
target_feedback은 두 target 배열의 합집합과 같은 key를 가진 객체로 쓰고 각 대상에 해당하는 수정 지시만 적는다. 대상이 없으면 빈 객체로 둔다.
지문은 material, 발문은 question, 정답 선지는 correct, 오답 선지는 해당 choice를 지정한다.
발문이 자료에 없는 시점·전후·원인·결과·영향·범위·대상을 추가해 G2 또는 G3 문제가 생기면 question을 지정한다.
정답 선지의 주체·행위·대상 관계가 잘못되면 correct를, 오답 선지가 근거와 다르면 해당 choice를 지정한다.
정답 선지가 비문이면 correct를, 오답 선지가 비문이면 해당 choice를 지정하고 fact_basis를 보존한 자연스러운 한국어로 고치도록 target_feedback을 작성한다.
Gate 번호만으로 repair target을 고정하지 말고 실제 오류가 발생한 구성 요소를 지정한다.
choice_quality_score는 반드시 `6 - choice_evidence.deductions의 points 합계`로 계산한다.
actual_difficulty는 목표 점수와 무관하게 완성 문항 자체를 1|2|3으로 판정한다. 3점은 사료·우회 단서 해석, 정밀한 연대·관계 판단, 세부 지식, 가까운 선지 비교 중 하나가 주된 난이도로 실제 작동하면 성립할 수 있다.
2점은 대표 추론, 식별 후 표준 사실 비교, 넓은 시기·전후 판단, 중간 수준의 가까운 선지 비교 중 하나가 주된 난이도로 실제 작동하면 성립할 수 있다.
난이도 감점은 choice_evidence.deductions에 넣지 않는다.
선택지 감점은 범주 위반, 중복, 약한 정답 외형 편향만 사용한다.
target_score 2·3점은 가까운 오답 수만으로 선택지 품질을 감점하지 않는다. 가까운 선지 비교가 주된 난이도 기제일 때만 그 수를 난이도 판정에 사용한다.
answer_style_bias는 선지의 길이·구체성·문장 형식만 비교한다. 대표 업적이라는 이유는 외형 편향이 아니다.
총점이 {min_accept_score}점 미만이면 revision_targets 또는 repair_targets를 최소 1개 지정한다.
""".strip()
    few_shots = list(evaluation_few_shot_messages())
    last_example = few_shots[-1]
    few_shots[-1] = {
        "role": last_example["role"],
        "content": [{
            "type": "text",
            "text": last_example["content"],
            "prompt_cache_breakpoint": {"mode": "explicit"},
        }],
    }
    return [
        {"role": "system", "content": system},
        *few_shots,
        {"role": "user", "content": user},
    ]


def post_chat(api_key: str, base_url: str, model: str, messages: list[dict[str, Any]], timeout: int, max_retries: int) -> dict[str, Any]:
    """OpenAI 호환 평가 모델을 호출하고 전송 오류를 제한 횟수만 재시도한다."""
    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "response_format": {"type": "json_object"},
    }
    if model.startswith("gpt-5.6"):
        prefix = json.dumps(messages[:-1], ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        payload["prompt_cache_key"] = f"qgen-eval:{hashlib.sha256(prefix.encode()).hexdigest()[:24]}"
        payload["extra_body"] = {"prompt_cache_options": {"mode": "explicit"}}
    if not model.startswith("gpt-5"):
        payload["temperature"] = 0
    client = OpenAI(api_key=api_key, base_url=base_url, timeout=timeout, max_retries=max_retries)
    return client.chat.completions.create(**payload).model_dump()


def parse_response(response: dict[str, Any]) -> dict[str, Any]:
    """Chat Completions 응답 본문에서 judge JSON 객체를 추출한다."""
    content = response["choices"][0]["message"]["content"] or "{}"
    return json.loads(content)


def normalize_gate(parsed: dict[str, Any], min_accept_score: int = DEFAULT_MIN_ACCEPT_SCORE) -> dict[str, Any]:
    """모델 판정의 스키마와 자기 일관성을 검증하고 계산값만 정규화한다."""
    gate = parsed.get("gate") if isinstance(parsed.get("gate"), dict) else {}
    errors: list[str] = []
    choice_g5_failure = False
    stem_values: list[str] = []
    choices = parsed.get("choice_verification_summary")
    if not isinstance(choices, list) or len(choices) != 5:
        errors.append("choice_verification_summary_must_have_five_rows")
    else:
        labels = [str(row.get("choice") or "") for row in choices if isinstance(row, dict)]
        if set(labels) != set(LABELS):
            errors.append("choice_verification_summary_labels_must_be_①_to_⑤")
        for row in choices:
            if not isinstance(row, dict):
                errors.append("choice_verification_summary_row_is_invalid")
                continue
            if str(row.get("historically_valid") or "").lower() not in {"yes", "no", "uncertain"}:
                errors.append("choice_historically_valid_is_invalid")
            if str(row.get("satisfies_stem_condition") or "").lower() not in {"yes", "no", "uncertain"}:
                errors.append("choice_satisfies_stem_condition_is_invalid")
            if not isinstance(row.get("g5_should_fail"), bool):
                errors.append("choice_g5_should_fail_is_invalid")
            elif row["g5_should_fail"]:
                choice_g5_failure = True
            stem_values.append(str(row.get("satisfies_stem_condition") or "").lower())
    g6 = parsed.get("g6_claim_equivalence_check")
    if not isinstance(g6, dict):
        errors.append("g6_claim_equivalence_check_is_invalid")
    else:
        if g6.get("relation") not in {
            "none", "weak_keyword", "target_name_exposure", "partial_same_claim",
            "same_core_claim", "direct_copy", "external_bias",
        }:
            errors.append("g6_relation_is_invalid")
        for field in ("can_answer_by_text_matching_without_history", "g6_should_fail"):
            if not isinstance(g6.get(field), bool):
                errors.append(f"g6_{field}_is_invalid")
    statuses = {key: str((gate.get(key) or {}).get("status", "")).upper() for key in GATES}
    invalid = [key for key, status in statuses.items() if status not in {"PASS", "FAIL", "UNCERTAIN"}]
    errors.extend(f"missing_or_invalid_{key}" for key in invalid)
    if choice_g5_failure and statuses.get("G5") != "FAIL":
        errors.append("G5_is_inconsistent_with_choice_verification")
    if stem_values and "uncertain" not in stem_values and stem_values.count("yes") != 1 and statuses.get("G3") != "FAIL":
        errors.append("G3_is_inconsistent_with_choice_verification")
    if isinstance(g6, dict) and isinstance(g6.get("g6_should_fail"), bool):
        if g6["g6_should_fail"] and statuses.get("G6") != "FAIL":
            errors.append("G6_is_inconsistent_with_claim_equivalence")
    failed = [key for key, status in statuses.items() if status == "FAIL"]
    uncertain = [key for key, status in statuses.items() if status == "UNCERTAIN"]
    computed_result = "FAIL" if failed else "uncertain" if uncertain or invalid else "PASS"
    declared_result = str(parsed.get("gate_result") or "").upper()
    if declared_result not in {"PASS", "FAIL", "UNCERTAIN"} or declared_result != computed_result.upper():
        errors.append("gate_result_is_inconsistent")
    declared_failed = parsed.get("failed_gates")
    if (
        not isinstance(declared_failed, list)
        or len(declared_failed) != len(set(declared_failed))
        or set(declared_failed) != set(failed)
    ):
        errors.append("failed_gates_are_inconsistent")
    allowed_targets = {"material", "question", "correct", *(f"choice:{label}" for label in LABELS)}
    repair_targets = parsed.get("repair_targets")
    if (
        not isinstance(repair_targets, list)
        or len(repair_targets) != len(set(repair_targets))
        or any(target not in allowed_targets for target in repair_targets)
    ):
        errors.append("repair_targets_are_invalid")
    decision = parsed.get("final_decision")
    if decision not in {"accept", "repair", "regenerate", "needs_verification"}:
        errors.append("final_decision_is_invalid")
    if computed_result == "FAIL" and decision not in {"repair", "regenerate"}:
        errors.append("final_decision_is_inconsistent")
    if computed_result == "uncertain" and decision != "needs_verification":
        errors.append("final_decision_is_inconsistent")

    score = parsed.get("problem_score")
    if computed_result == "PASS":
        if not isinstance(score, dict):
            errors.append("gate_pass_result_is_missing_problem_score")
        else:
            difficulty = score.get("difficulty_score")
            choice_score = score.get("choice_quality_score")
            if not isinstance(difficulty, int) or not 0 <= difficulty <= 4:
                errors.append("difficulty_score_is_invalid")
            if not isinstance(choice_score, int) or not 0 <= choice_score <= 6:
                errors.append("choice_quality_score_is_invalid")
            if isinstance(difficulty, int) and isinstance(choice_score, int):
                total = difficulty + choice_score
                if score.get("total_score") not in {None, total}:
                    errors.append("total_score_is_inconsistent")
                score["total_score"] = total
                expected_decision = "accept" if total >= min_accept_score else "repair"
                if decision != expected_decision:
                    errors.append("final_decision_is_inconsistent")
            revisions = score.get("revision_targets")
            if (
                not isinstance(revisions, list)
                or len(revisions) != len(set(revisions))
                or any(target not in allowed_targets for target in revisions)
            ):
                errors.append("revision_targets_are_invalid")
            if (
                isinstance(difficulty, int)
                and isinstance(choice_score, int)
                and difficulty + choice_score < min_accept_score
                and not (repair_targets or revisions)
            ):
                errors.append("low_score_requires_repair_target")
    elif score is not None:
        errors.append("failed_or_uncertain_gate_must_not_have_problem_score")

    revisions = score.get("revision_targets") if isinstance(score, dict) else []
    expected_feedback = set(repair_targets or []) | set(revisions or [])
    target_feedback = parsed.get("target_feedback")
    if (
        not isinstance(target_feedback, dict)
        or set(target_feedback) != expected_feedback
        or any(not isinstance(value, str) or not value.strip() for value in target_feedback.values())
    ):
        errors.append("target_feedback_is_invalid")

    if errors:
        parsed["judge_output_errors"] = list(dict.fromkeys(errors))
        parsed["gate_result"] = "uncertain"
        parsed["failed_gates"] = []
        parsed["final_decision"] = "needs_verification"
        parsed["problem_score"] = None
        parsed["repair_targets"] = []
        parsed["target_feedback"] = {}
        return parsed
    parsed["gate_result"] = computed_result
    parsed["failed_gates"] = failed
    if computed_result != "PASS":
        parsed["problem_score"] = None
    return parsed


def write_summary(rows: list[dict[str, Any]], path: Path) -> None:
    """문항별 Gate·점수 결과를 사람이 확인할 Markdown 표로 저장한다."""
    gate_counts = Counter(row.get("parsed", {}).get("gate_result", "raw") for row in rows)
    failed_gate_counts = Counter(gate for row in rows for gate in row.get("parsed", {}).get("failed_gates", []))
    decision_counts = Counter(row.get("parsed", {}).get("final_decision", "raw") for row in rows)
    scores = [
        row["parsed"]["problem_score"]["total_score"]
        for row in rows
        if isinstance(row.get("parsed", {}).get("problem_score"), dict)
    ]
    lines = [
        "# v1.8 Gate Judge Report",
        "",
        f"- evaluated: {len(rows)}",
        f"- gate_result: {dict(gate_counts)}",
        f"- failed_gates: {dict(failed_gate_counts) if failed_gate_counts else {}}",
        f"- final_decision: {dict(decision_counts)}",
        f"- problem_score_average: {round(sum(scores) / len(scores), 2) if scores else 'not_scored'}",
        "",
        "| # | topic | target_score | gate | problem_score | failed | decision |",
        "|---:|---|---:|---|---:|---|---|",
    ]
    for row in rows:
        parsed = row.get("parsed") or {}
        lines.append(
            f"| {row['index']} | {row.get('topic') or ''} | {row.get('target_score') or ''} | "
            f"{parsed.get('gate_result') or 'raw'} | "
            f"{(parsed.get('problem_score') or {}).get('total_score', '-')} | "
            f"{', '.join(parsed.get('failed_gates') or []) or '-'} | "
            f"{parsed.get('final_decision') or '-'} |"
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    """문항별 평가를 실행해 JSONL 상세 결과와 Markdown 요약을 기록한다."""
    load_dotenv(PROJECT_ROOT / ".env")
    args = parse_args()
    assembled = json.loads(args.input.read_text(encoding="utf-8-sig"))
    rubric = args.rubric.read_text(encoding="utf-8")
    records = select_records(records_from_assembled(assembled), args.question_id, args.limit)
    args.out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    if args.dry_run:
        dry_path = args.out_dir / f"v18_gate_judge_dry_run_{stamp}.json"
        dry_path.write_text(
            json.dumps(
                [
                    {
                        "record": record,
                        "messages": build_messages(rubric, record),
                    }
                    for record in records
                ],
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        print(json.dumps({"dry_run": str(dry_path), "count": len(records)}, ensure_ascii=False))
        return 0

    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not set in environment or .env")

    if args.output_prefix:
        args.output_prefix.parent.mkdir(parents=True, exist_ok=True)
        jsonl_path = Path(f"{args.output_prefix}.jsonl")
        md_path = Path(f"{args.output_prefix}.md")
    else:
        jsonl_path = args.out_dir / f"v18_gate_judge_{stamp}.jsonl"
        md_path = args.out_dir / f"v18_gate_judge_{stamp}.md"
    resume_paths = [path for path in (jsonl_path, args.resume_from) if args.resume and path and path.exists()]
    prior_rows = [
        json.loads(line)
        for path in resume_paths
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    current_hashes = {record["question_id"]: record["question_hash"] for record in records}
    matching = {
        (str(row.get("question_id")), str(row.get("question_hash") or "")): row
        for row in prior_rows
        if current_hashes.get(str(row.get("question_id"))) == str(row.get("question_hash") or "")
    }
    rows = [
        matching[(record["question_id"], record["question_hash"])]
        for record in records
        if (record["question_id"], record["question_hash"]) in matching
    ]
    with jsonl_path.open("w", encoding="utf-8") as out:
        for row in rows:
            out.write(json.dumps(row, ensure_ascii=False) + "\n")
        for record in pending_records(records, rows):
            messages = build_messages(rubric, record)
            response = post_chat(api_key, args.base_url, args.model, messages, args.timeout, args.max_retries)
            raw = parse_response(response)
            parsed = normalize_fixed_gate(raw) if record.get("choice_mode") != "generated" else normalize_gate(raw)
            row = {
                "index": record["index"],
                "question_id": record["question_id"],
                "question_hash": record["question_hash"],
                "topic": record.get("topic"),
                "target_score": record.get("target_score"),
                "model": args.model,
                "parsed": parsed,
                "usage": response.get("usage"),
            }
            rows.append(row)
            out.write(json.dumps(row, ensure_ascii=False) + "\n")
            out.flush()
            if args.sleep:
                time.sleep(args.sleep)
    write_summary(rows, md_path)
    print(json.dumps({"jsonl": str(jsonl_path), "md": str(md_path), "count": len(rows)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
