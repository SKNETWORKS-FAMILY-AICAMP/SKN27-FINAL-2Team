"""SLLM 정답·오답 출력을 5지선다 한능검 문항으로 조립한다.
모델 문장을 최소 정규화한 뒤 선지 번호를 무작위 배치하고, 중복·누락·형식 오류를
결정론적으로 검사한다. 역사 의미와 10점 평가는 별도 ``evaluation.v18`` 단계다.
"""

from __future__ import annotations

import itertools
import random
import re
from typing import Any

from ai.question_generation.core.difficulty import target_score_from_difficulty
from ai.question_generation.core.text import compact, normalize_era_markers


def normalize_question_text(text: Any) -> str:
    """발문의 연속 공백만 정리한다."""
    return compact(text)


def choice_from_model(text: str) -> tuple[str, str]:
    """모델 선지의 시대 표기와 공백만 정리하고 원문 내용은 보존한다."""
    return compact(normalize_era_markers(text)), "distractor_raw"


V18_GATE_ORDER = ("G1", "G2", "G3", "G4", "G5", "G6")
V18_GATE_LABELS = {
    "G1": "입력·형식 성립",
    "G2": "발문·선지 판독 가능성",
    "G3": "정답 성립성 및 유일성",
    "G4": "발문·자료 사실성 및 내부 일관성",
    "G5": "오답 역사 사실성",
    "G6": "정답 노출·복사·외형 편향",
}
V18_GATE_ERROR_MAP = {
    "missing_material": "G1",
    "missing_question": "G1",
    "choice_count_not_5": "G1",
    "answer_count_not_1": "G1",
    "empty_choice": "G1",
    "missing_choice_image": "G1",
    "target_score_invalid_or_missing": "G1",
    "choice_has_hanja": "G2",
    "question_mentions_missing_underline": "G2",
    "question_mentions_missing_marker": "G2",
    "duplicate_choice": "G3",
    "duplicate_choice_image": "G3",
}


def v18_gate_validation(errors: list[str]) -> dict[str, Any]:
    """로컬 구조 오류를 v1.8 Gate 형식의 PASS/FAIL 결과로 매핑한다."""
    by_gate = {gate: [] for gate in V18_GATE_ORDER}
    for error in errors:
        by_gate[V18_GATE_ERROR_MAP.get(error, "G2")].append(error)
    failed_gates = [gate for gate in V18_GATE_ORDER if by_gate[gate]]
    return {
        "rubric": "hanneung_sllm_eval_rubric_v1_8.md",
        "result": "PASS" if not failed_gates else "FAIL",
        "failed_gates": failed_gates,
        "action": "none" if not failed_gates else ("regenerate" if {"G3", "G4"} & set(failed_gates) else "repair"),
        "gates": {
            gate: {
                "label": V18_GATE_LABELS[gate],
                "status": "FAIL" if by_gate[gate] else "PASS",
                "errors": by_gate[gate],
            }
            for gate in V18_GATE_ORDER
        },
    }


def validate_question(question: dict[str, Any]) -> dict[str, Any]:
    """조립 문항을 검사하고 오류와 재생성 대상을 함께 반환한다."""
    choices = question.get("choices", [])
    choice_mode = str(question.get("choice_mode") or "generated")
    texts = [compact(choice.get("text")) for choice in choices]
    errors: list[str] = []
    repair_targets: list[str] = []

    def add_target(target: str) -> None:
        if target not in repair_targets:
            repair_targets.append(target)

    def choice_target(choice: dict[str, Any]) -> str:
        if choice_mode != "generated":
            return "assembly"
        if choice.get("is_answer"):
            return "correct"
        index = int(choice.get("distractor_index") or 0)
        return f"distractor:{index}" if index else "assembly"

    if not compact(question.get("material")):
        errors.append("missing_material")
        add_target("material")
    if not compact(question.get("question")):
        errors.append("missing_question")
        add_target("question")
    target_score = target_score_from_difficulty(
        question.get("target_score"),
        question.get("difficulty_bucket"),
        question.get("difficulty_label"),
    )
    if target_score not in {1, 2, 3}:
        errors.append("target_score_invalid_or_missing")
        add_target("assembly")
    if len(choices) != 5:
        errors.append("choice_count_not_5")
        add_target("assembly")
    if sum(1 for choice in choices if choice.get("is_answer")) != 1:
        errors.append("answer_count_not_1")
        add_target("assembly")
    if choice_mode == "image":
        image_ids = [str((choice.get("image") or {}).get("image_chunk_id") or "") for choice in choices]
        if any(not image_id or not choice.get("choice_image_path") for image_id, choice in zip(image_ids, choices)):
            errors.append("missing_choice_image")
            add_target("assembly")
        if len(set(image_ids)) != len(image_ids):
            errors.append("duplicate_choice_image")
            add_target("assembly")
    else:
        choice_checks = (
            ("empty_choice", lambda text: not text),
            ("choice_has_hanja", lambda text: bool(re.search(r"[\u4e00-\u9fff\uf900-\ufaff]", text))),
        )
        for choice, text in zip(choices, texts, strict=False):
            for error, failed in choice_checks:
                if failed(text):
                    errors.append(error)
                    add_target(choice_target(choice))
    if choice_mode != "image" and len(set(texts)) != len(texts):
        errors.append("duplicate_choice")
        seen: dict[str, dict[str, Any]] = {}
        for choice, text in zip(choices, texts, strict=False):
            if text in seen:
                add_target(choice_target(choice if not choice.get("is_answer") else seen[text]))
            else:
                seen[text] = choice
    question_text = compact(question.get("question"))
    material_text = question.get("material", "")
    if "밑줄" in question_text and not re.search(r"밑줄|_{2,}|<u>|</u>", material_text):
        errors.append("question_mentions_missing_underline")
        add_target("question")
    if any(marker not in material_text for marker in re.findall(r"\([가-힣]\)", question_text)):
        errors.append("question_mentions_missing_marker")
        add_target("question")
    errors = list(dict.fromkeys(errors))
    gate = v18_gate_validation(errors)
    return {
        "status": "ok" if gate["result"] == "PASS" else "needs_review",
        "errors": errors,
        "gate_result": gate["result"],
        "failed_gates": gate["failed_gates"],
        "gate": gate,
        "repair_targets": repair_targets,
    }


def replace_order_choices(question: dict[str, Any], rng: random.Random) -> None:
    """순서형 문항의 선택지를 가능한 배열 조합으로 교체하고 정답을 표시한다."""
    if question.get("choice_mode") != "order":
        return
    chronology = question.get("chronology") or {}
    labels = chronology.get("event_labels") or {}
    correct_order = chronology.get("correct_order") or []
    answer = tuple(labels.get(event_id) for event_id in correct_order)
    if len(answer) != 3 or any(not value for value in answer):
        raise ValueError("timeline_order requires explicit correct_order and event_labels")
    answer_text = " - ".join(answer)
    wrongs = [" - ".join(order) for order in itertools.permutations(answer) if order != answer]
    rng.shuffle(wrongs)
    source = next((choice.get("source", {}) for choice in question.get("choices", []) if choice.get("is_answer")), {})
    choices = [{"text": answer_text, "is_answer": True, "source_role": "deterministic_order", "source": source}]
    choices.extend(
        {"text": text, "is_answer": False, "source_role": "deterministic_order", "source": source}
        for text in wrongs[:4]
    )
    rng.shuffle(choices)
    for index, choice in enumerate(choices, start=1):
        choice["number"] = index
    question["choices"] = choices
    question["answer_number"] = next(choice["number"] for choice in choices if choice["is_answer"])
    question["choices_replaced"] = "timeline_order_permutations"


def replace_timeline_position_choices(question: dict[str, Any], rng: random.Random) -> None:
    """명시된 연표 위치 다섯 개로 선택지를 결정론적으로 교체한다."""
    if question.get("choice_mode") != "timeline_position":
        return
    chronology = question.get("chronology") or {}
    positions = chronology.get("timeline_positions") or []
    correct = chronology.get("correct_position")
    if len(positions) != 5 or len(set(positions)) != 5 or correct not in positions:
        raise ValueError("timeline_position requires five explicit positions and one correct_position")
    source = next((choice.get("source", {}) for choice in question.get("choices", []) if choice.get("is_answer")), {})
    choices = [
        {
            "text": position,
            "is_answer": position == correct,
            "source_role": "deterministic_timeline_position",
            "source": source,
        }
        for position in positions
    ]
    rng.shuffle(choices)
    for index, choice in enumerate(choices, start=1):
        choice["number"] = index
    question["choices"] = choices
    question["answer_number"] = next(choice["number"] for choice in choices if choice["is_answer"])
    question["choices_replaced"] = "timeline_positions"


def replace_image_choices(question: dict[str, Any], pack_item: dict[str, Any], rng: random.Random) -> None:
    """검수된 pack 이미지 다섯 개를 그대로 선택지로 배치한다."""
    if question.get("choice_mode") != "image":
        return
    bases = [pack_item["answer_basis"], *pack_item["distractors"]]
    choices = []
    for basis in bases:
        image = basis.get("image") or {}
        path = image.get("original_image_url") or image.get("thumbnail_url")
        if not image.get("image_chunk_id") or not path:
            raise ValueError("image choice requires image_chunk_id and URL")
        choices.append({
            "text": "",
            "choice_image_path": path,
            "image": image,
            "is_answer": basis.get("role") == "answer",
            "source_role": "reviewed_image",
            "source": basis_source(basis),
        })
    rng.shuffle(choices)
    for index, choice in enumerate(choices, start=1):
        choice["number"] = index
    question["choices"] = choices
    question["answer_number"] = next(choice["number"] for choice in choices if choice["is_answer"])
    question["choices_replaced"] = "reviewed_images"


def basis_source(basis: dict[str, Any]) -> dict[str, Any]:
    """선지에서 원본 사실과 소유자를 직접 추적할 source 객체를 만든다."""
    return {
        "slot": basis.get("slot"),
        "role": basis.get("role"),
        "basis_item_id": basis.get("basis_item_id"),
        "owner_id": basis.get("owner_id"),
        "owner_label": basis.get("owner_label"),
        "fact_basis": basis.get("fact_basis"),
        "evidence_chunk_ids": list(basis.get("evidence_chunk_ids") or []),
    }


def assemble_item(
    pack_item: dict[str, Any],
    material: str,
    correct_output: dict[str, Any],
    distractor_outputs: dict[int, dict[str, Any]],
    rng: random.Random,
    question_text: str = "",
) -> dict[str, Any]:
    """불변 input과 컴포넌트 출력을 최종 question 객체 하나로 만든다."""
    choice_mode = str(pack_item.get("choice_mode") or "generated")
    correct_json = correct_output.get("json", {}) if choice_mode == "generated" else {}
    question_text = normalize_question_text(question_text or correct_json.get("question"))
    choices = [{"is_answer": True, "source": basis_source(pack_item["answer_basis"])}]
    if choice_mode == "generated":
        choices[0].update({"text": compact(correct_json.get("answer_choice")), "source_role": "correct_raw"})
        for basis in pack_item["distractors"]:
            distractor_index = int(basis["slot"])
            output = distractor_outputs[distractor_index]
            distractor, source_role = choice_from_model(compact(output.get("json", {}).get("distractor_choice")))
            choices.append(
                {
                "text": distractor,
                "is_answer": False,
                "source_role": source_role,
                "distractor_index": distractor_index,
                "source": basis_source(basis),
                }
            )
        rng.shuffle(choices)
        for index, choice in enumerate(choices, start=1):
            choice["number"] = index

    question = {
        "seed_id": pack_item.get("seed_id"),
        "family_id": pack_item.get("family_id"),
        "variant_key": pack_item.get("variant_key"),
        "era": pack_item.get("era"),
        "service_era": pack_item.get("service_era"),
        "service_topic": pack_item.get("service_topic"),
        "service_question_type": pack_item.get("service_question_type"),
        "service_question_subtype": pack_item.get("service_question_subtype"),
        "topic": pack_item.get("topic"),
        "topic_type": pack_item.get("topic_type"),
        "difficulty_label": pack_item.get("difficulty_label"),
        "difficulty_bucket": pack_item.get("difficulty_bucket"),
        "target_score": target_score_from_difficulty(
            pack_item.get("target_score"),
            pack_item.get("difficulty_bucket"),
            pack_item.get("difficulty_label"),
        ),
        "question_task": pack_item.get("question_task"),
        "choice_mode": choice_mode,
        "stem_pattern": pack_item.get("stem_pattern"),
        "relation_axis_id": pack_item.get("relation_axis_id"),
        "question_task_instruction": pack_item.get("question_task_instruction"),
        "minor_type": pack_item.get("minor_type"),
        "material": normalize_era_markers(material),
        "material_source": {
            "owner_id": pack_item["answer_basis"].get("owner_id"),
            "owner_label": pack_item["answer_basis"].get("owner_label"),
            "basis": [normalize_era_markers(value) for value in pack_item.get("material_clue_basis") or []],
            "evidence_chunk_ids": [
                source.get("chunk_id") for source in pack_item.get("material_clue_evidence") or [] if source.get("chunk_id")
            ],
        },
        "question": question_text,
        "choices": choices,
        "answer_number": next((choice.get("number") for choice in choices if choice.get("is_answer")), None),
        "answer_fact_basis": [normalize_era_markers(pack_item["answer_basis"]["fact_basis"])],
    }
    if pack_item.get("image"):
        question["image"] = pack_item["image"]
    if pack_item.get("chronology"):
        question["chronology"] = dict(pack_item["chronology"])
    replace_order_choices(question, rng)
    replace_timeline_position_choices(question, rng)
    replace_image_choices(question, pack_item, rng)
    return question


def assemble_question(item: dict[str, Any], components: dict[str, Any], seed: int, question_text: str = "") -> dict[str, Any]:
    """체크포인트의 컴포넌트를 중간 run_item 없이 바로 조립한다."""
    question = assemble_item(
        item,
        str((components["material"]["response"] or {}).get("material") or ""),
        (components.get("correct") or {}).get("response") or {},
        {int(slot): component["response"] for slot, component in (components.get("distractors") or {}).items()},
        random.Random(f"{seed}:{item.get('seed_id', '')}"),
        question_text,
    )
    question["validation"] = validate_question(question)
    return question
