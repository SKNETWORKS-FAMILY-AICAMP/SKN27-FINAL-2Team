"""문제은행 pack 한 개를 최종 5지선다 문항으로 만드는 운영 오케스트레이터.

실행 순서:
1. 5선지 ``--pack-input`` JSON을 검증하고 generation item으로 변환
2. OpenAI API로 material·question 생성 및 계약 검사
3. RunPod V41 SLLM으로 정답 1개와 오답 4개 생성
4. 최종 문항 조립과 로컬 구조 검사
5. 모든 중간 상태와 실제 SLLM 입출력을 ``--output`` 체크포인트에 저장

최종 v1.8 의미 평가와 10점 채점은 이 파일이 아니라 ``evaluation.v18``이 담당한다.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import time
import urllib.error
from functools import lru_cache
from pathlib import Path
from typing import Any

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parents[3]

from ai.question_generation.generation.assemble import assemble_question
from ai.question_generation.generation.material import chat_json, generate_material
from ai.question_generation.generation.material_rules import (
    DEFAULT_MATERIAL_EXAMPLES,
    DEFAULT_MATERIAL_PROMPT_RULES,
    choose_material_examples,
    load_json_dict,
    material_type_rules_text,
)
from ai.question_generation.generation.material_validation import material_contract_status
from ai.question_generation.generation.sllm_inputs import correct_record, distractor_record
from ai.question_generation.generation.sllm_transport import DEFAULT_MODEL as DEFAULT_RUNPOD_MODEL, call_chat, clean_model_text
from ai.question_generation.core.contracts import (
    generation_item,
    validate_pack,
)
# 1. 실행 설정과 체크포인트 상태 관리

class PipelineLimitError(RuntimeError):
    """호출 횟수 또는 문항 처리 시간 예산을 소진했을 때 발생한다."""

    pass


class CallBudget:
    """한 번의 실행이 무한 재시도하지 않도록 호출 수와 경과 시간을 제한한다."""

    def __init__(self, max_calls: int, max_seconds: int, calls: int = 0, elapsed: float = 0.0) -> None:
        """실행 한도와 checkpoint에 기록할 이전 누적 사용량을 초기화한다."""
        self.max_calls = max_calls
        self.max_seconds = max_seconds
        self.calls = 0
        self.previous_calls = calls
        self.previous_elapsed = elapsed
        self.started = time.monotonic()

    def elapsed(self) -> float:
        """현재 실행의 경과 초를 반환한다."""
        return time.monotonic() - self.started

    def total_calls(self) -> int:
        """이전 실행을 포함한 누적 호출 수를 반환한다."""
        return self.previous_calls + self.calls

    def total_elapsed(self) -> float:
        """이전 실행을 포함한 누적 경과 초를 반환한다."""
        return self.previous_elapsed + self.elapsed()

    def claim(self, label: str) -> None:
        """LLM 호출 한 건을 예약하고 한도 초과 시 예외를 발생시킨다."""
        if self.calls >= self.max_calls:
            raise PipelineLimitError(f"LLM call limit reached before {label}: {self.max_calls}")
        if self.elapsed() >= self.max_seconds:
            raise PipelineLimitError(f"Question time limit reached before {label}: {self.max_seconds}s")
        self.calls += 1

    def timeout(self, requested: int) -> int:
        """전체 시간 예산을 넘지 않는 이번 HTTP 호출 timeout을 계산한다."""
        remaining = int(self.max_seconds - self.elapsed())
        if remaining <= 0:
            raise PipelineLimitError(f"Question time limit reached: {self.max_seconds}s")
        return max(1, min(requested, remaining))


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """단일 문항의 입력 pack, 출력 체크포인트, 모델·한도 옵션을 읽는다."""
    parser = argparse.ArgumentParser(description="Generate one question-bank item and run deterministic structure checks.")
    parser.add_argument("--pack-input", type=Path, required=True)
    parser.add_argument("--family-id", default="")
    parser.add_argument("--variant-key", default="")
    parser.add_argument("--answer-owner-id", default="")
    parser.add_argument("--distractor-owner-id", action="append", default=[])
    parser.add_argument("--frame-index", type=int, default=0)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--openai-model", default=os.getenv("OPENAI_CHAT_MODEL", ""))
    parser.add_argument("--base-url", default=os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1"))
    parser.add_argument("--runpod-model", default=os.getenv("RUNPOD_SLLM_MODEL", ""))
    parser.add_argument("--request-timeout", type=int, default=int(os.getenv("QGEN_REQUEST_TIMEOUT", "60")))
    parser.add_argument("--transport-retries", type=int, default=int(os.getenv("QGEN_TRANSPORT_RETRIES", "1")))
    parser.add_argument("--max-stage-attempts", type=int, default=int(os.getenv("QGEN_MAX_STAGE_ATTEMPTS", "4")))
    parser.add_argument("--max-gate-cycles", type=int, default=int(os.getenv("QGEN_MAX_GATE_CYCLES", "2")))
    parser.add_argument("--max-total-calls", type=int, default=int(os.getenv("QGEN_MAX_TOTAL_CALLS", "28")))
    parser.add_argument("--max-seconds", type=int, default=int(os.getenv("QGEN_MAX_SECONDS", "600")))
    parser.add_argument("--seed", type=int, default=20260715)
    return parser.parse_args(argv)


def empty_component(feedback: str = "", evaluation_repairs: int = 0, previous_response: Any = None) -> dict[str, Any]:
    """한 생성 컴포넌트의 요청·응답·검증 상태를 만든다."""
    return {
        "attempts": 0,
        "evaluation_repairs": evaluation_repairs,
        "previous_response": previous_response,
        "request": None,
        "response": None,
        "gate": None,
        "feedback": feedback,
    }


def new_state(pack: dict[str, Any], item: dict[str, Any]) -> dict[str, Any]:
    """재시작 가능한 단일 문항 체크포인트의 최초 상태를 만든다."""
    components = {"material": empty_component()}
    if item.get("choice_mode") == "generated":
        components.update({
            "correct": empty_component(),
            "distractors": {str(row["slot"]): empty_component() for row in item["distractors"]},
        })
    return {
        "schema_version": "question_bank_generation_run_v2",
        "pack_id": pack["pack_id"],
        "status": "prepared",
        "input": item,
        "components": components,
        "question_selection": None,
        "question_selection_feedback": "",
        "repair_history": [],
        "assembly_attempts": 0,
        "question": None,
        "total_llm_calls": 0,
        "elapsed_seconds": 0.0,
        "error": "",
    }


def checkpoint(path: Path, state: dict[str, Any], budget: CallBudget) -> None:
    """현재 단계, 호출 횟수, 중간 결과를 원자적으로 JSON 파일에 기록한다."""
    state["total_llm_calls"] = budget.total_calls()
    state["elapsed_seconds"] = round(budget.total_elapsed(), 3)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)


def component_attempt(component: dict[str, Any], key: str, maximum: int) -> int:
    """단계별 시도 횟수를 증가시키고 한도를 넘으면 즉시 중단한다."""
    attempts = int(component.get("attempts", 0))
    if attempts >= maximum:
        raise PipelineLimitError(f"Stage attempt limit reached: {key}={maximum}")
    component["attempts"] = attempts + 1
    return attempts + 1


# 2. 지문 근거 계약 검사와 GPT 지문 생성

def material_evidence_usage_status(contract: dict[str, Any], used_ids: list[str]) -> dict[str, Any]:
    """GPT가 보고한 근거 ID가 material 계약의 허용 범위 안인지 검사한다."""
    allowed = set(contract.get("allowed_evidence_ids") or [])
    forbidden = set(contract.get("forbidden_answer_evidence_ids") or [])
    used = {str(value) for value in used_ids if str(value).strip()}
    errors = []
    if not used:
        errors.append("material_evidence_id_missing")
    if used - allowed:
        errors.append("material_uses_unapproved_evidence")
    if used & forbidden:
        errors.append("material_uses_answer_evidence")
    return {"status": "ok" if not errors else "needs_review", "errors": errors, "used_evidence_ids": sorted(used)}


def material_gate(item: dict[str, Any], text: str, used_ids: list[str] | None = None) -> dict[str, Any]:
    """지문 형식 계약과 허용 근거 사용 여부를 한 결과로 합친다."""
    checks = {
        "contract": material_contract_status(item, text),
        "evidence_usage": material_evidence_usage_status(item["material_contract"], used_ids or []),
    }
    errors = [error for check in checks.values() for error in check.get("errors", [])]
    errors.extend(
        f"material_{name}_failed"
        for name, check in checks.items()
        if check.get("status") != "ok" and not check.get("errors")
    )
    return {"status": "PASS" if all(check.get("status") == "ok" for check in checks.values()) else "FAIL", "errors": errors, "checks": checks}


def generate_material_stage(
    state: dict[str, Any], args: argparse.Namespace, budget: CallBudget
) -> None:
    """GPT 지문 생성과 로컬 material Gate를 통과할 때까지 제한 횟수 내 재시도한다."""
    item = state["input"]
    component = state["components"]["material"]
    feedback = str(component.get("feedback") or "")
    material_examples = choose_material_examples(
        load_json_dict(DEFAULT_MATERIAL_EXAMPLES), item, args.seed
    )
    while True:
        attempt = component_attempt(component, "material", args.max_stage_attempts)
        component["request"] = {
            "model": args.openai_model,
            "feedback": feedback,
            "source_ids": item["material_contract"]["allowed_evidence_ids"],
            "few_shot_source_ids": [example.get("source_id") for example in material_examples],
        }
        budget.claim("material")
        material = generate_material(
            selection=item,
            sources=item["material_sources"],
            material_example=material_examples,
            material_rules=material_type_rules_text(load_json_dict(DEFAULT_MATERIAL_PROMPT_RULES), item["material_type"]),
            model=args.openai_model,
            base_url=args.base_url,
            api_key=os.environ["OPENAI_API_KEY"],
            temperature=0.2,
            timeout=budget.timeout(args.request_timeout),
            max_retries=args.transport_retries,
            answer_fact_hints=[] if item.get("choice_mode") == "image" else [item["answer_basis"]["fact_basis"]],
            material_contract=item["material_contract"],
            retry_feedback=feedback,
        )
        text = str(material.get("material") or "").strip()
        used_ids = [str(value) for value in material.get("used_evidence_ids") or []]
        gate = material_gate(item, text, used_ids)
        component["response"] = material
        component["gate"] = {"attempt": attempt, **gate}
        if gate["status"] == "PASS":
            return
        feedback = " ".join(gate["errors"]) or "material Gate FAIL"
        component["feedback"] = feedback


# 3. SLLM 정답 생성과 오답 후보별 개별 생성

def material_question_error(material: str, question: str) -> str:
    """GPT 발문이 지문에 없는 표시를 만들거나 HTML 태그를 노출했는지 검사한다."""
    if not question.strip():
        return "missing_question"
    if re.search(r"</?u>", question):
        return "question_has_html_tag"
    if "밑줄" in question and not re.search(r"<u>.*?</u>", material):
        return "question_mentions_missing_underline"
    if any(marker not in material for marker in re.findall(r"\([가-힣]\)", question)):
        return "question_mentions_missing_marker"
    return ""

def runpod_args(args: argparse.Namespace, budget: CallBudget) -> argparse.Namespace:
    """파이프라인 옵션과 환경변수를 RunPod 어댑터 인자 형태로 변환한다."""
    return argparse.Namespace(
        endpoint_id=os.environ["RUNPOD_ENDPOINT_ID"],
        api_key=os.environ["RUNPOD_API_KEY"],
        model=args.runpod_model or os.getenv("RUNPOD_SLLM_MODEL", DEFAULT_RUNPOD_MODEL),
        temperature=0.1,
        max_tokens=512,
        timeout=budget.timeout(args.request_timeout),
    )


def call_sllm(record: dict[str, Any], args: argparse.Namespace, budget: CallBudget, label: str) -> dict[str, Any]:
    """V41 record 하나를 RunPod에 보내고 네트워크 오류에 한해서만 재시도한다."""
    for attempt in range(args.transport_retries + 1):
        budget.claim(label)
        try:
            return call_chat(runpod_args(args, budget), record)
        except urllib.error.HTTPError:
            raise
        except (TimeoutError, urllib.error.URLError, ConnectionError) as exc:
            if attempt >= args.transport_retries:
                raise RuntimeError(f"RunPod transport failed after {attempt + 1} attempts: {exc}") from exc
            time.sleep(min(2 ** attempt, 5))
    raise RuntimeError("RunPod transport retry loop ended unexpectedly")


def call_choice_model(
    record: dict[str, Any], component: dict[str, Any], args: argparse.Namespace, budget: CallBudget, label: str
) -> dict[str, Any]:
    """SLLM 재생성 2회 또는 제한된 전송 실패 뒤 같은 컴포넌트만 GPT로 수리한다."""
    if int(component.get("evaluation_repairs") or 0) <= 2:
        component["backend"] = "sllm"
        try:
            return call_sllm(record, args, budget, label)
        except RuntimeError as exc:
            component["transport_error"] = str(exc)
            component["backend"] = "llm_transport_fallback"
    else:
        component["backend"] = "llm_repair"
    budget.claim(f"{label}_llm_repair")
    repaired = chat_json(
        base_url=args.base_url,
        api_key=os.environ["OPENAI_API_KEY"],
        model=args.openai_model,
        messages=[
            {"role": "system", "content": f"{record['system']} 평가에서 실패한 현재 구성 요소만 고쳐 JSON으로 출력한다."},
            {
                "role": "user",
                "content": (
                    f"{record['instruction']}\n\n입력:\n{json.dumps(record['input'], ensure_ascii=False, indent=2)}\n\n"
                    f"직전 실패 출력:\n{json.dumps(component.get('previous_response'), ensure_ascii=False, indent=2)}"
                ),
            },
        ],
        temperature=0.0,
        timeout=budget.timeout(args.request_timeout),
        max_retries=args.transport_retries,
    )
    return {"json": repaired}


def correct_output_error(output: dict[str, Any]) -> str:
    """정답 호출에서 발문 또는 정답 선지가 비었는지 검사한다."""
    data = output.get("json") or {}
    data["question"] = clean_model_text(str(data.get("question") or ""))
    data["answer_choice"] = clean_model_text(str(data.get("answer_choice") or ""))
    if not data["question"] or not data["answer_choice"]:
        return "missing_question_or_answer_choice"
    return ""


def distractor_output_error(output: dict[str, Any]) -> str:
    """오답 호출에서 distractor_choice가 비었는지 검사한다."""
    data = output.get("json") or {}
    data["distractor_choice"] = clean_model_text(str(data.get("distractor_choice") or ""))
    return "" if data["distractor_choice"] else "empty_distractor_choice"


def material_text(state: dict[str, Any]) -> str:
    """material 컴포넌트의 확정 지문을 반환한다."""
    return str((state["components"]["material"].get("response") or {}).get("material") or "").strip()


def selected_question(state: dict[str, Any]) -> str:
    """발문 선택 단계가 확정한 문장을 반환한다."""
    selection = state.get("question_selection") or {}
    if selection.get("selected_question"):
        return str(selection["selected_question"]).strip()
    raise RuntimeError("Question has not been selected")


def select_question_stage(state: dict[str, Any], args: argparse.Namespace, budget: CallBudget) -> None:
    """GPT 발문을 우선 사용하고 구조 오류 때만 SLLM 발문 또는 LLM 수리를 사용한다."""
    if state.get("question_selection"):
        return
    material = material_text(state)
    correct = (state["components"].get("correct") or {}).get("response") or {}
    answer_choice = str((correct.get("json") or {}).get("answer_choice") or "").strip()
    candidates = [
        ("gpt", str((state["components"]["material"].get("response") or {}).get("question") or "").strip()),
    ]
    sllm_question = str((correct.get("json") or {}).get("question") or "").strip()
    if sllm_question:
        candidates.append(("sllm", sllm_question))
    valid = [(source, question) for source, question in candidates if not material_question_error(material, question)]
    feedback = str(state.get("question_selection_feedback") or "").strip()
    if feedback or not valid:
        _, original = next(((source, question) for source, question in candidates if question), ("", ""))
        if not original:
            raise RuntimeError("Question candidates are empty")
        budget.claim("question_repair")
        response = chat_json(
            base_url=args.base_url,
            api_key=os.environ["OPENAI_API_KEY"],
            model=args.openai_model,
            messages=[
                {
                    "role": "system",
                    "content": "한국사 문항의 지문과 선택지는 바꾸지 않고 발문만 고쳐 JSON으로 출력한다.",
                },
                {
                    "role": "user",
                    "content": (
                        f"지문:\n{material}\n\n정답 선지:\n{answer_choice}\n\n기존 발문:\n{original}\n\n"
                        f"출제 계약:\n{json.dumps({key: state['input'].get(key) for key in ('choice_mode', 'relation_axis_id', 'stem_pattern', 'question_task_instruction')}, ensure_ascii=False)}\n\n"
                        f"수정 사유:\n{feedback or '지문에 없는 표식이나 조건을 제거한다.'}\n\n"
                        '출력: {"question":"수정한 발문"}'
                    ),
                },
            ],
            temperature=0.0,
            timeout=budget.timeout(args.request_timeout),
            max_retries=args.transport_retries,
        )
        repaired = clean_model_text(str(response.get("question") or ""))
        error = material_question_error(material, repaired)
        if error:
            raise RuntimeError(f"Repaired question failed structure check: {error}")
        selected_source, question, reason, attempts = "llm_repair", repaired, feedback or "candidate_structure_error", 1
    else:
        selected_source, question = valid[0]
        reason, attempts = ("gpt_primary" if selected_source == "gpt" else "gpt_structure_error"), 0
    state["question_selection"] = {
        "selected_source": selected_source,
        "selected_question": question,
        "reason": reason,
        "attempts": attempts,
    }
    state["question_selection_feedback"] = ""


def generate_correct(state: dict[str, Any], args: argparse.Namespace, budget: CallBudget) -> None:
    """정답 컴포넌트만 생성한다."""
    item = state["input"]
    component = state["components"]["correct"]
    while True:
        attempt = component_attempt(component, "correct", args.max_stage_attempts)
        record = correct_record(item, material_text(state), component.get("feedback", ""))
        component["request"] = record
        output = call_choice_model(record, component, args, budget, "v41_correct")
        error = correct_output_error(output)
        component["response"] = output
        component["gate"] = {
            "attempt": attempt,
            "status": "PASS" if not error else "FAIL",
            "errors": [error] if error else [],
        }
        if not error:
            return


def generate_distractor(state: dict[str, Any], slot: int, args: argparse.Namespace, budget: CallBudget) -> None:
    """지정한 오답 컴포넌트 하나만 생성한다."""
    item = state["input"]
    component = state["components"]["distractors"][str(slot)]
    basis = next(row for row in item["distractors"] if int(row["slot"]) == slot)
    correct = dict(state["components"]["correct"]["response"]["json"])
    correct["question"] = selected_question(state)
    while True:
        attempt = component_attempt(component, f"distractor:{slot}", args.max_stage_attempts)
        record = distractor_record(item, material_text(state), basis, correct, component.get("feedback", ""))
        component["request"] = record
        output = call_choice_model(record, component, args, budget, f"v41_distractor_{slot}")
        error = distractor_output_error(output)
        component["response"] = output
        component["gate"] = {
            "attempt": attempt,
            "status": "PASS" if not error else "FAIL",
            "errors": [error] if error else [],
        }
        if not error:
            return


def generate_v41_question_stage(state: dict[str, Any], args: argparse.Namespace, budget: CallBudget) -> None:
    """비어 있는 정답 또는 오답 컴포넌트만 생성한다."""
    if state["input"].get("choice_mode") != "generated":
        select_question_stage(state, args, budget)
        return
    if not state["components"]["correct"].get("response"):
        generate_correct(state, args, budget)
    select_question_stage(state, args, budget)
    for slot, component in state["components"]["distractors"].items():
        if not component.get("response"):
            generate_distractor(state, int(slot), args, budget)


def invalidate(
    state: dict[str, Any], targets: list[str], feedback: str | dict[str, str] = "", *, evaluation: bool = False
) -> None:
    """실패 컴포넌트와 그 하위 의존성만 초기화한다."""
    default_feedback = " ".join((state.get("question") or {}).get("validation", {}).get("errors") or [])

    def target_feedback(target: str) -> str:
        return str(feedback.get(target) or "") if isinstance(feedback, dict) else str(feedback or default_feedback)

    def reset(component: dict[str, Any], target: str) -> dict[str, Any]:
        repairs = int(component.get("evaluation_repairs") or 0) + (1 if evaluation else 0)
        previous = component.get("response") if evaluation else component.get("previous_response")
        return empty_component(target_feedback(target), repairs, previous)

    if state["input"].get("choice_mode") != "generated":
        if any(target not in {"material", "question"} for target in targets):
            raise ValueError("fixed-choice evaluation may repair only material or question")
        if evaluation:
            for target in targets:
                component = state["components"].get(target)
                state.setdefault("repair_history", []).append({
                    "target": target,
                    "feedback": target_feedback(target),
                    "request": (component or {}).get("request"),
                    "response": (component or {}).get("response"),
                    "evaluation_repairs": int((component or {}).get("evaluation_repairs") or 0),
                })
        if "material" in targets:
            state["components"]["material"] = reset(state["components"]["material"], "material")
            state["question_selection"] = None
        elif "question" in targets:
            state["question_selection"] = None
            state["question_selection_feedback"] = target_feedback("question")
        state["question"] = None
        return

    if evaluation:
        for target in targets:
            component = (
                state["components"].get(target)
                if target in {"material", "correct"}
                else state["components"]["distractors"].get(target.split(":", 1)[1])
                if target.startswith("distractor:")
                else None
            )
            state.setdefault("repair_history", []).append({
                "target": target,
                "feedback": target_feedback(target),
                "request": (component or {}).get("request"),
                "response": (component or {}).get("response"),
                "backend": (component or {}).get("backend"),
                "evaluation_repairs": int((component or {}).get("evaluation_repairs") or 0),
            })

    if "material" in targets:
        state["components"]["material"] = reset(state["components"]["material"], "material")
        state["question_selection"] = None
        state["question_selection_feedback"] = ""
        if not evaluation:
            state["components"]["correct"] = empty_component()
            state["components"]["distractors"] = {
                str(row["slot"]): empty_component() for row in state["input"]["distractors"]
            }
    elif "correct" in targets:
        state["components"]["correct"] = reset(state["components"]["correct"], "correct")
        state["question_selection"] = None
        state["question_selection_feedback"] = ""
        state["components"]["distractors"] = {
            str(row["slot"]): empty_component() for row in state["input"]["distractors"]
        }
    elif "question" in targets:
        state["question_selection"] = None
        state["question_selection_feedback"] = target_feedback("question")
        state["components"]["distractors"] = {
            str(row["slot"]): empty_component() for row in state["input"]["distractors"]
        }
    else:
        for target in targets:
            if target.startswith("distractor:"):
                slot = target.split(":", 1)[1]
                state["components"]["distractors"][slot] = reset(state["components"]["distractors"][slot], target)
    if "correct" in targets:
        state["components"]["correct"]["feedback"] = target_feedback("correct")
    if "question" in targets:
        state["question_selection_feedback"] = target_feedback("question")
    for target in targets:
        if target.startswith("distractor:"):
            slot = target.split(":", 1)[1]
            state["components"]["distractors"][slot]["feedback"] = target_feedback(target)
    state["question"] = None


def prepare_failed_resume(state: dict[str, Any]) -> None:
    """실패 체크포인트에서 Gate가 실패한 컴포넌트만 새 시도 상태로 되돌린다."""
    if state.get("status") not in {"failed", "generation_exhausted"}:
        return
    components = state["components"]
    candidates = [("material", components["material"])]
    if state["input"].get("choice_mode") == "generated":
        candidates.extend([("correct", components["correct"])])
        candidates.extend(
            (f"distractor:{slot}", component)
            for slot, component in components["distractors"].items()
        )
    failed = [
        target
        for target, component in candidates
        if (component.get("gate") or {}).get("status") == "FAIL"
    ]
    if failed:
        invalidate(
            state,
            failed,
            {target: str(component.get("feedback") or "") for target, component in candidates},
        )
    state["status"] = "prepared"
    state["assembly_attempts"] = 0
    state.pop("error", None)


# 6. 환경 검증과 단일 문항 E2E 실행

def ensure_config(args: argparse.Namespace, choice_mode: str = "generated") -> None:
    """실행 전 API 키·모델·점수 범위 등 필수 설정을 검증한다."""
    required = {
        "OPENAI_API_KEY": os.getenv("OPENAI_API_KEY"),
        "OPENAI_CHAT_MODEL/--openai-model": args.openai_model,
    }
    if choice_mode == "generated":
        required.update({
            "RUNPOD_ENDPOINT_ID": os.getenv("RUNPOD_ENDPOINT_ID"),
            "RUNPOD_API_KEY": os.getenv("RUNPOD_API_KEY"),
        })
    missing = [key for key, value in required.items() if not value]
    if missing and not args.dry_run:
        raise RuntimeError(f"Missing configuration: {', '.join(missing)}")


@lru_cache(maxsize=8)
def read_pack(path: str) -> dict[str, Any]:
    """배치 안에서 같은 pack JSON을 한 번만 읽는다."""
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def main(argv: list[str] | None = None) -> int:
    """단일 문항 생성 CLI의 전체 단계와 resume/checkpoint 흐름을 실행한다."""
    load_dotenv(PROJECT_ROOT / ".env")
    args = parse_args(argv)
    try:
        source_pack = read_pack(str(args.pack_input.resolve()))
        if "members" in source_pack or "packs" in source_pack:
            from ai.question_generation.retrieval.closed_pack_input import build_generation_pack, select_closed_pack

            source_pack = build_generation_pack(
                select_closed_pack(source_pack, args.family_id),
                answer_owner_id=args.answer_owner_id,
                distractor_owner_ids=args.distractor_owner_id or None,
                frame_index=args.frame_index,
                seed=args.seed,
            )
        else:
            source_pack = dict(source_pack)
        if args.family_id and source_pack.get("family_id") != args.family_id:
            raise ValueError(f"pack family_id does not match request: {args.family_id}")
        if args.variant_key:
            source_pack["variant_key"] = args.variant_key
        pack = validate_pack(source_pack)
    except (RuntimeError, ValueError, KeyError) as exc:
        print(json.dumps({"status": "pack_rejected", "error": str(exc)}, ensure_ascii=False))
        return 2
    ensure_config(args, pack["choice_mode"])
    if args.resume and args.output.exists():
        state = json.loads(args.output.read_text(encoding="utf-8-sig"))
        if state.get("pack_id") != pack["pack_id"]:
            raise ValueError("checkpoint pack_id does not match requested pack")
        if state.get("schema_version") != "question_bank_generation_run_v2":
            raise ValueError("legacy checkpoint cannot resume; start a new v2 output")
        prepare_failed_resume(state)
    else:
        state = new_state(pack, generation_item(pack))
    budget = CallBudget(args.max_total_calls, args.max_seconds, int(state.get("total_llm_calls", 0)), float(state.get("elapsed_seconds", 0)))
    if args.dry_run:
        checkpoint(args.output, state, budget)
        print(json.dumps({"status": "prepared", "output": str(args.output), "pack_id": pack["pack_id"]}, ensure_ascii=False))
        return 0
    if state.get("status") == "complete":
        print(json.dumps({"status": "complete", "output": str(args.output)}, ensure_ascii=False))
        return 0

    try:
        while int(state.get("assembly_attempts", 0)) < args.max_gate_cycles:
            if not state["components"]["material"].get("response"):
                generate_material_stage(state, args, budget)
                checkpoint(args.output, state, budget)
            generated_choices = state["input"].get("choice_mode") == "generated"
            if not state.get("question_selection") or generated_choices and (
                not state["components"]["correct"].get("response")
                or any(not component.get("response") for component in state["components"]["distractors"].values())
            ):
                generate_v41_question_stage(state, args, budget)
                checkpoint(args.output, state, budget)
            state["assembly_attempts"] = int(state.get("assembly_attempts", 0)) + 1
            question = assemble_question(state["input"], state["components"], args.seed, selected_question(state))
            state["question"] = question
            local_gate = question.get("validation") or {}
            if local_gate.get("gate_result") != "PASS":
                targets = list(local_gate.get("repair_targets") or [])
                if not targets:
                    raise RuntimeError(f"Local deterministic Gate failed without repair target: {local_gate.get('errors')}")
                invalidate(state, targets)
                checkpoint(args.output, state, budget)
                continue
            state["status"] = "complete"
            checkpoint(args.output, state, budget)
            print(json.dumps({"status": "complete", "output": str(args.output)}, ensure_ascii=False))
            return 0
        state["status"] = "generation_exhausted"
        state["error"] = "Local deterministic Gate cycle limit reached"
        checkpoint(args.output, state, budget)
        return 2
    except (PipelineLimitError, RuntimeError, ValueError, KeyError) as exc:
        if isinstance(exc, PipelineLimitError):
            state["status"] = "generation_exhausted"
        else:
            state["status"] = "failed"
        state["error"] = str(exc)
        checkpoint(args.output, state, budget)
        print(json.dumps({"status": state["status"], "error": state["error"], "output": str(args.output)}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
