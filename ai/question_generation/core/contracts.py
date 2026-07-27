"""문제은행 basis pack을 실제 생성 단계에서 쓰는 item 계약으로 변환한다.

입력은 PostgreSQL 또는 ChoiceFact 풀에서 조립한 5선지 basis pack이다.
출력은 지문 생성, V41 SLLM 입력 생성, 최종 검증이 공통으로 읽는 generation item이다.
이 모듈은 외부 API를 호출하지 않고 구조·상태·필수 필드만 검증한다.
"""

from __future__ import annotations

from typing import Any

from ai.question_generation.core.difficulty import target_score_from_difficulty
from ai.question_generation.generation.material_rules import material_type_route_status

V41_TOPIC_TYPES = {"기타", "매체", "사건", "인물", "제도", "집단", "문화유산", "문화"}


def validate_pack(pack: dict[str, Any]) -> dict[str, Any]:
    """생성 가능한 pack인지 확인하고 slot 순서로 정렬된 사본을 반환한다."""
    items = pack.get("items")
    choice_mode = str(pack.get("choice_mode") or "generated")
    if choice_mode not in {"generated", "order", "timeline_position", "image"}:
        raise ValueError(f"unsupported choice_mode: {choice_mode}")
    if pack.get("status") != "rag_ready" or pack.get("semantic_status") != "pass":
        raise ValueError("basis pack must be rag_ready and semantic_status=pass")
    if not isinstance(items, list) or len(items) != 5:
        raise ValueError("basis pack must contain exactly five items")
    if not str(pack.get("material_clue_basis") or "").strip() or not pack.get("material_evidence_chunks"):
        raise ValueError("basis pack must contain a separate material clue basis and evidence")
    if not str(pack.get("relation_axis_id") or "").strip():
        raise ValueError("basis pack must contain relation_axis_id")
    if pack.get("topic_type") not in V41_TOPIC_TYPES:
        raise ValueError("basis pack must contain an explicit V41 topic_type")
    for field in ("question_task_instruction",):
        if not str(pack.get(field) or "").strip():
            raise ValueError(f"basis pack must contain explicit {field}")
    if choice_mode == "generated" and not str(pack.get("distractor_type") or "").strip():
        raise ValueError("generated choice pack must contain explicit distractor_type")
    if pack.get("stem_pattern") == "standard_other":
        raise ValueError("basis pack must contain a classified stem_pattern")
    route = material_type_route_status(pack)
    if route["status"] != "ok":
        raise ValueError(f"unsupported material route: {', '.join(route['errors'])}")
    slots = {int(item.get("slot_no", -1)): item for item in items}
    if set(slots) != {0, 1, 2, 3, 4} or slots[0].get("role") != "answer":
        raise ValueError("basis pack slots must contain answer 0 and distractors 1..4")
    for slot, item in slots.items():
        expected_role = "answer" if slot == 0 else "distractor"
        if (
            item.get("role") != expected_role
            or item.get("status") != "rag_ready"
            or item.get("semantic_status") != "pass"
            or not str(item.get("fact_basis") or "").strip()
            or not str(item.get("article_id") or "").strip()
            or not str(item.get("truth_owner_label") or "").strip()
            or not item.get("evidence_chunks")
        ):
            raise ValueError(f"invalid basis item at slot {slot}")
        chronology_event_ids = set((pack.get("chronology") or {}).get("event_ids") or [])
        if any(
            not isinstance(row, dict)
            or str(row.get("article_id") or "") not in (chronology_event_ids or {str(item["article_id"])})
            for row in item["evidence_chunks"]
        ):
            raise ValueError(f"basis item evidence owner mismatch at slot {slot}")
    if choice_mode == "image":
        images = [slots[slot].get("image") for slot in range(5)]
        image_ids = [str((image or {}).get("image_chunk_id") or "") for image in images]
        if "" in image_ids or len(set(image_ids)) != 5:
            raise ValueError("image choice pack must contain five distinct image IDs")
        if any(
            str(image.get("owner_id") or "") != str(slots[slot]["article_id"])
            or not (image.get("original_image_url") or image.get("thumbnail_url"))
            for slot, image in enumerate(images)
        ):
            raise ValueError("image choice must match its owner and contain a URL")
    answer_owner_id = str(slots[0]["article_id"])
    chronology = pack.get("chronology")
    if chronology:
        allowed_material_owners = set(chronology.get("material_owner_ids") or [])
        if not allowed_material_owners or any(
            not isinstance(row, dict) or str(row.get("article_id") or "") not in allowed_material_owners
            for row in pack["material_evidence_chunks"]
        ):
            raise ValueError("chronology material evidence owner mismatch")
    elif any(
        not isinstance(row, dict) or str(row.get("article_id") or "") != answer_owner_id
        for row in pack["material_evidence_chunks"]
    ):
        raise ValueError("material evidence owner mismatch")
    material_chunk_ids = {
        str(row.get("chunk_id"))
        for row in pack.get("material_evidence_chunks") or []
        if isinstance(row, dict) and row.get("chunk_id")
    }
    answer_chunk_ids = {
        str(row.get("chunk_id"))
        for row in slots[0].get("evidence_chunks") or []
        if isinstance(row, dict) and row.get("chunk_id")
    }
    if not material_chunk_ids - answer_chunk_ids and pack.get("material_fact_semantically_distinct") is not True:
        raise ValueError("shared material/answer evidence requires explicit semantic review")
    return {**pack, "choice_mode": choice_mode, "items": [dict(slots[index]) for index in range(5)]}


def material_generation_constraints(pack: dict[str, Any]) -> list[str]:
    """정답 사실이 지문에 누출되지 않도록 pack별 지문 작성 제약을 만든다."""
    task = str(pack.get("question_task") or "")
    stem = str(pack.get("stem_pattern") or "")
    constraints = [
        "relation_axis_id는 선지에서 판단할 사실축이므로 지문에서 그 사실축의 정답 내용을 설명하지 않는다.",
    ]
    if task == "timeline_position":
        positions = [str(value) for value in (pack.get("chronology") or {}).get("timeline_positions") or []]
        if positions:
            constraints.append(
                f"연표에는 검수된 구간 표식 {' · '.join(positions)}을 이 순서로 각각 정확히 한 번 포함한다."
            )
    if task in {"period_between", "timeline_position", "order"} or stem in {"same_period", "before_after"}:
        constraints.append(
            "지문은 기준 대상·사건의 시기를 추론할 근거만 제공하고, 정답 사실의 연도·순서·전후 위치는 밝히지 않는다."
        )
    else:
        constraints.append(
            "지문은 관계축과 다른 식별 사실로 대상을 찾게 하고, 식별 뒤 선지의 별도 사실을 판단하는 구조를 보존한다."
        )
    if "자료의 분석" in str(pack.get("major_type") or ""):
        constraints.append(
            "근거를 백과사전식으로 전부 요약하지 말고, 선택한 단서를 사료·기록처럼 구성해 해석할 여지를 남긴다."
        )
    if target_score_from_difficulty(str(pack.get("difficulty_label") or "")) == 3:
        constraints.append(
            "3점 난이도는 발문축의 주된 판단에서 확보한다. 대상명·정확한 연도·대표 사건을 한꺼번에 나열해 판단을 없애지 않는다."
        )
    return constraints


def build_material_contract(pack: dict[str, Any]) -> dict[str, Any]:
    """허용 지문 근거와 금지 정답 근거를 명시한 지문 생성 계약을 만든다."""
    answer = pack["items"][0]
    allowed = [
        str(row.get("chunk_id"))
        for row in pack.get("material_evidence_chunks") or []
        if isinstance(row, dict) and row.get("chunk_id")
    ]
    forbidden = [
        str(row.get("chunk_id"))
        for row in answer.get("evidence_chunks") or []
        if isinstance(row, dict) and row.get("chunk_id")
    ]
    shared_reviewed = pack.get("material_fact_semantically_distinct") is True
    allowed_ids = list(dict.fromkeys(allowed if shared_reviewed else (value for value in allowed if value not in forbidden)))
    return {
        "version": "material_contract_v2",
        "allowed_evidence_ids": allowed_ids,
        "forbidden_answer_evidence_ids": list(dict.fromkeys(value for value in forbidden if value not in allowed_ids)),
        "constraints": [
            "topic 이름과 정답 사실을 지문에 직접 노출하지 않는다.",
            "관계축 판단의 기준이 되는 대상이나 사건은 지문에서 추론할 수 있어야 한다.",
            "근거 길이 또는 고정 단서 개수로 난이도를 만들지 않는다.",
            *material_generation_constraints(pack),
        ],
    }


def generation_item(pack: dict[str, Any]) -> dict[str, Any]:
    """basis pack을 GPT 지문 생성과 V41 SLLM 호출에 필요한 단일 item으로 펼친다."""
    target_score = target_score_from_difficulty(pack["difficulty_label"])
    if target_score is None:
        raise ValueError(f"Unsupported difficulty_label: {pack['difficulty_label']}")
    answer = pack["items"][0]
    material_contract = build_material_contract(pack)
    allowed_material_ids = set(material_contract["allowed_evidence_ids"])
    material_evidence = [
        evidence
        for evidence in pack.get("material_evidence_chunks") or []
        if str(evidence.get("chunk_id") or "") in allowed_material_ids
    ]
    sources = []
    for index, evidence in enumerate(material_evidence):
        snippet = str(evidence.get("snippet") or evidence.get("exact_text") or "").strip()
        if index == 0:
            if pack.get("chronology"):
                snippet = f"검수된 지문 단서: {pack['material_clue_basis']}"
            else:
                snippet = f"정리된 지문 단서: {pack['material_clue_basis']}\n근거 원문: {snippet}".strip()
        sources.append(
            {
                "chunk_id": evidence.get("chunk_id"),
                "source_type": "encykorea_material_clue",
                "title": pack["target_label"],
                "snippet": snippet or pack["material_clue_basis"],
                "url": evidence.get("source_url", ""),
            }
        )
    if not sources:
        raise ValueError("material clue basis has no evidence")
    def basis_input(basis: dict[str, Any]) -> dict[str, Any]:
        evidence = basis.get("evidence_chunks") or []
        result = {
            "slot": int(basis["slot_no"]),
            "role": basis["role"],
            "basis_item_id": basis["basis_item_id"],
            "owner_id": basis["article_id"],
            "owner_label": basis["truth_owner_label"],
            "fact_basis": basis["fact_basis"],
            "evidence_chunk_ids": [
                str(row["chunk_id"])
                for row in evidence
                if isinstance(row, dict) and row.get("chunk_id")
            ],
        }
        if basis.get("image"):
            result["image"] = dict(basis["image"])
        return result

    item: dict[str, Any] = {
        "seed_id": pack["pack_id"],
        "family_id": pack.get("family_id") or pack["pack_id"],
        "variant_key": pack.get("variant_key"),
        "era": pack.get("era"),
        "service_era": pack.get("service_era"),
        "service_topic": pack.get("service_topic"),
        "service_question_type": pack.get("service_question_type"),
        "service_question_subtype": pack.get("service_question_subtype"),
        "topic": pack["target_label"],
        "topic_type": pack["topic_type"],
        "material_type": pack["material_type"],
        "major_type": pack["major_type"],
        "minor_type": pack["minor_type"],
        "question_task": pack["question_task"],
        "choice_mode": pack["choice_mode"],
        "question_task_instruction": pack["question_task_instruction"],
        "distractor_type": pack.get("distractor_type"),
        "difficulty_label": pack["difficulty_label"],
        "target_score": target_score,
        "stem_pattern": pack["stem_pattern"],
        "relation_axis_id": pack["relation_axis_id"],
        "material_contract": material_contract,
        "answer_basis": basis_input(answer),
        "material_clue_basis": [pack["material_clue_basis"]],
        "material_clue_evidence": material_evidence,
        "material_sources": sources,
        "distractors": [basis_input(basis) for basis in pack["items"][1:]],
    }
    if pack.get("image"):
        item["image"] = pack["image"]
    if pack.get("chronology"):
        item["chronology"] = dict(pack["chronology"])
    return item
