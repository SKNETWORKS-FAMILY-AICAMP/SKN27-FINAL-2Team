"""9-member closed pack을 현행 5선지 generation pack으로 변환한다."""

from __future__ import annotations

import hashlib
import itertools
import random
from itertools import combinations
from typing import Any

from ai.question_generation.core.contracts import V41_TOPIC_TYPES

DIFFICULTY_LABELS = {1: "쉬움", 2: "보통", 3: "어려움"}
MATERIAL_TARGET_SCOPE = "material_target"
FRAME_ANSWER_SCOPE = "frame_answer"
FINAL_REVIEW_STATUS = "final_reviewed"
CHRONOLOGY_TASKS = {"period_between", "timeline_position", "order"}
FRAME_FIELDS = (
    "question_task",
    "stem_pattern",
    "material_type",
    "major_type",
    "minor_type",
    "question_task_instruction",
    "distractor_type",
)
MEMBER_FIELDS = (
    "choice_fact_id",
    "owner_id",
    "owner_label",
    "owner_type",
    "fact_basis",
    "fact_evidence_chunks",
    "material_clue_basis",
    "material_evidence_chunks",
)
CHRONOLOGY_MEMBER_FIELDS = (
    "event_id",
    "choice_fact_id",
    "owner_id",
    "owner_label",
    "owner_type",
    "fact_basis",
    "fact_evidence_chunks",
    "time_label",
    "sort_key",
)


def is_chronology_source(source: dict[str, Any]) -> bool:
    frames = source.get("question_frames") or []
    return bool(frames) and all(frame.get("question_task") in CHRONOLOGY_TASKS for frame in frames)


def _validate_chronology_frame(frame: dict[str, Any], members_by_owner: dict[str, dict[str, Any]], family_id: str) -> None:
    task = str(frame["question_task"])
    expected_mode = {"period_between": "generated", "timeline_position": "timeline_position", "order": "order"}[task]
    if frame.get("choice_mode") != expected_mode:
        raise ValueError(f"chronology frame choice_mode must be {expected_mode}: {family_id}")
    owner_ids = set(members_by_owner)
    event_ids = frame.get("event_ids")
    labels = frame.get("event_labels")
    answer_id = str(frame.get("answer_owner_id") or "")
    distractor_ids = frame.get("distractor_owner_ids")
    material_owner_ids = frame.get("material_owner_ids")
    if frame.get("answer_owner_scope") != FRAME_ANSWER_SCOPE:
        raise ValueError(f"chronology frame must use {FRAME_ANSWER_SCOPE}: {family_id}")
    if int(frame.get("difficulty") or 0) not in DIFFICULTY_LABELS:
        raise ValueError(f"chronology frame lacks difficulty: {family_id}")
    if not str(frame.get("relation_axis_id") or "").strip():
        raise ValueError(f"chronology frame lacks relation_axis_id: {family_id}")
    if not isinstance(event_ids, list) or not event_ids or len(set(event_ids)) != len(event_ids):
        raise ValueError(f"chronology frame has invalid event_ids: {family_id}")
    if any(event_id not in owner_ids for event_id in event_ids):
        raise ValueError(f"chronology frame references an unknown event: {family_id}")
    if not isinstance(labels, dict) or set(labels) != set(event_ids) or len(set(labels.values())) != len(labels):
        raise ValueError(f"chronology frame has invalid event_labels: {family_id}")
    if answer_id not in owner_ids or not isinstance(distractor_ids, list) or len(distractor_ids) != 4:
        raise ValueError(f"chronology frame has invalid answer choices: {family_id}")
    if len(set(distractor_ids)) != 4 or answer_id in distractor_ids or any(value not in owner_ids for value in distractor_ids):
        raise ValueError(f"chronology frame has non-unique answer choices: {family_id}")
    if not isinstance(material_owner_ids, list) or not material_owner_ids or any(value not in owner_ids for value in material_owner_ids):
        raise ValueError(f"chronology frame has invalid material owners: {family_id}")
    material_evidence = frame.get("material_evidence_chunks")
    if not str(frame.get("material_clue_basis") or "").strip() or not isinstance(material_evidence, list) or not material_evidence:
        raise ValueError(f"chronology frame lacks material evidence: {family_id}")
    if any(str(row.get("article_id") or "") not in material_owner_ids for row in material_evidence if isinstance(row, dict)):
        raise ValueError(f"chronology material evidence owner mismatch: {family_id}")
    material_evidence_ids = {str(row.get("chunk_id") or "") for row in material_evidence if isinstance(row, dict)}
    frame_evidence_ids = set(frame.get("frame_evidence_chunk_ids") or [])
    required_evidence_ids = {
        str(row.get("chunk_id") or "")
        for event_id in event_ids
        for row in members_by_owner[event_id].get("fact_evidence_chunks") or []
        if isinstance(row, dict)
    }
    if "" in material_evidence_ids or not material_evidence_ids.issubset(frame_evidence_ids) or not required_evidence_ids.issubset(frame_evidence_ids):
        raise ValueError(f"chronology frame evidence IDs do not match payloads: {family_id}")
    if task == "period_between":
        anchors = frame.get("anchor_event_ids")
        if not isinstance(anchors, list) or len(anchors) != 2 or frame.get("correct_position") != "between":
            raise ValueError(f"invalid period_between frame: {family_id}")
    elif task == "timeline_position":
        positions = frame.get("timeline_positions")
        if not isinstance(positions, list) or len(positions) != 5 or frame.get("correct_position") not in positions:
            raise ValueError(f"invalid timeline_position frame: {family_id}")
    else:
        correct_order = frame.get("correct_order")
        if not isinstance(correct_order, list) or len(correct_order) != 3 or set(correct_order) != set(event_ids):
            raise ValueError(f"invalid order frame: {family_id}")


def canonical_owner_type(member: dict[str, Any]) -> str:
    """owner_type의 기존 ``base/detail`` 형식에서 base만 반환한다."""
    return str(member.get("owner_type") or "").split("/", 1)[0].strip()


def evidence_belongs_to_owner(member: dict[str, Any], field: str) -> bool:
    """근거 전부가 해당 member의 owner ID에 직접 귀속되는지 확인한다."""
    owner_id = str(member.get("owner_id") or "")
    rows = member.get(field)
    return bool(owner_id and rows) and all(
        isinstance(row, dict) and str(row.get("article_id") or "") == owner_id
        for row in rows
    )


def answer_evidence_is_safe(member: dict[str, Any]) -> bool:
    """지문·정답 근거가 분리됐거나 동일 chunk의 사실 분리가 명시 검수됐는지 확인한다."""
    fact_ids = {
        str(row.get("chunk_id"))
        for row in member.get("fact_evidence_chunks") or []
        if isinstance(row, dict) and row.get("chunk_id")
    }
    material_ids = {
        str(row.get("chunk_id"))
        for row in member.get("material_evidence_chunks") or []
        if isinstance(row, dict) and row.get("chunk_id")
    }
    return bool(fact_ids and material_ids) and (
        fact_ids.isdisjoint(material_ids)
        or member.get("material_fact_semantically_distinct") is True
    )


def validate_closed_pack_source(source: dict[str, Any]) -> dict[str, Any]:
    """builder와 runtime이 함께 쓰는 9-member closed-pack 출제 계약을 검증한다."""
    family_id = str(source.get("family_id") or "")
    members = source.get("members")
    frames = source.get("question_frames")
    if source.get("status") != FINAL_REVIEW_STATUS:
        raise ValueError(f"closed pack must be {FINAL_REVIEW_STATUS}: {family_id}")
    if not family_id or not isinstance(members, list) or len(members) != 9:
        raise ValueError(f"closed pack must contain a family_id and nine members: {family_id}")
    if int(source.get("difficulty") or 0) not in DIFFICULTY_LABELS:
        raise ValueError(f"closed pack must contain difficulty 1, 2, or 3: {family_id}")
    if not str(source.get("era") or "").strip() or not str(source.get("relation_axis_id") or "").strip():
        raise ValueError(f"closed pack must contain era and relation_axis_id: {family_id}")
    owner_ids = [str(member.get("owner_id") or "") for member in members]
    if "" in owner_ids or len(set(owner_ids)) != 9:
        raise ValueError(f"closed pack must contain nine distinct owner IDs: {family_id}")
    fact_ids = [str(member.get("choice_fact_id") or "") for member in members]
    if "" in fact_ids or len(set(fact_ids)) != 9:
        raise ValueError(f"closed pack must contain nine distinct choice facts: {family_id}")
    chronology = is_chronology_source(source)
    required_member_fields = CHRONOLOGY_MEMBER_FIELDS if chronology else MEMBER_FIELDS
    for member in members:
        missing = [field for field in required_member_fields if not member.get(field)]
        if missing:
            raise ValueError(f"closed pack member lacks {missing}: {member.get('choice_fact_id', '')}")
        if not evidence_belongs_to_owner(member, "fact_evidence_chunks"):
            raise ValueError(f"fact evidence owner mismatch: {member['choice_fact_id']}")
        if not chronology and not evidence_belongs_to_owner(member, "material_evidence_chunks"):
            raise ValueError(f"material evidence owner mismatch: {member['choice_fact_id']}")
    owner_types = {canonical_owner_type(member) for member in members}
    if "" in owner_types or len(owner_types) != 1:
        raise ValueError(f"closed pack members must share one owner type: {family_id}")
    if source.get("topic_type") not in V41_TOPIC_TYPES:
        raise ValueError(f"closed pack lacks an explicit V41 topic_type: {family_id}")
    if not isinstance(frames, list) or len(frames) < 2:
        raise ValueError(f"closed pack must contain at least two frames: {family_id}")
    frame_keys = {str(frame.get("frame_id") or "") if chronology else tuple(
        str(frame.get(field) or "") for field in (*FRAME_FIELDS, "answer_owner_scope")
    ) for frame in frames if isinstance(frame, dict)}
    if len(frame_keys) != len(frames):
        raise ValueError(f"closed pack frames must be distinct: {family_id}")
    for frame in frames:
        missing = [field for field in FRAME_FIELDS if not str(frame.get(field) or "").strip()]
        if missing:
            raise ValueError(f"invalid closed-pack frame {missing}: {family_id}")
        if chronology:
            _validate_chronology_frame(frame, {str(member["owner_id"]): member for member in members}, family_id)
        elif frame.get("answer_owner_scope") != MATERIAL_TARGET_SCOPE:
            raise ValueError(f"invalid closed-pack frame scope: {family_id}")
    if chronology:
        return source
    eligible = source.get("answer_eligible_owner_ids")
    if (
        not isinstance(eligible, list)
        or not eligible
        or len(set(eligible)) != len(eligible)
        or any(owner_id not in owner_ids for owner_id in eligible)
    ):
        raise ValueError(f"closed pack must contain valid answer_eligible_owner_ids: {family_id}")
    members_by_owner = {str(member["owner_id"]): member for member in members}
    unsafe = [owner_id for owner_id in eligible if not answer_evidence_is_safe(members_by_owner[owner_id])]
    if unsafe:
        raise ValueError(f"answer-eligible owners lack reviewed material/fact separation: {unsafe}")
    return source


def variant_key(family_id: str, answer_owner_id: str, distractor_owner_ids: list[str]) -> str:
    """동일 정답 owner와 오답 4개 조합의 재사용을 막는 고유 키를 만든다."""
    return ":".join((family_id, answer_owner_id, ",".join(sorted(distractor_owner_ids))))


def stable_start(seed: int, family_id: str, size: int) -> int:
    """동일 seed와 family에서 재현 가능한 회전 시작 위치를 반환한다."""
    return int.from_bytes(hashlib.sha256(f"{seed}:{family_id}".encode()).digest()[:8], "big") % size


def plan_variants(
    source: dict[str, Any], count: int, seed: int = 20260715, used_keys: set[str] | None = None
) -> list[dict[str, Any]]:
    """9개 owner를 먼저 한 번씩 순환하며 중복 없는 파생 문항을 계획한다."""
    validate_closed_pack_source(source)
    if is_chronology_source(source):
        frames = source["question_frames"]
        if count > len(frames):
            raise ValueError(f"requested variants exceed chronology frame count: {count}/{len(frames)}")
        variants = []
        for index, frame in enumerate(frames[:count]):
            variants.append({
                "answer_owner_id": frame["answer_owner_id"],
                "distractor_owner_ids": list(frame["distractor_owner_ids"]),
                "frame_index": index,
                "variant_key": f"{source['family_id']}:frame:{frame['frame_id']}",
            })
        return variants
    members = source["members"]
    frames = source["question_frames"]
    if count < 0:
        raise ValueError("variant count must be non-negative")
    owner_ids = [str(member["owner_id"]) for member in members]
    eligible_owner_ids = source["answer_eligible_owner_ids"]
    start = stable_start(seed, str(source["family_id"]), len(eligible_owner_ids))
    answer_order = eligible_owner_ids[start:] + eligible_owner_ids[:start]

    combinations_by_owner: dict[str, list[tuple[str, ...]]] = {}
    for answer_owner_id in answer_order:
        choices = list(combinations((owner_id for owner_id in owner_ids if owner_id != answer_owner_id), 4))
        random.Random(f"{seed}:{source['family_id']}:{answer_owner_id}").shuffle(choices)
        combinations_by_owner[answer_owner_id] = choices

    capacity = len(answer_order) * len(next(iter(combinations_by_owner.values())))
    if count > capacity:
        raise ValueError(f"requested variants exceed closed-pack capacity: {count}/{capacity}")

    variants = []
    used_keys = used_keys or set()
    for index in range(capacity):
        answer_owner_id = answer_order[index % len(answer_order)]
        cycle = index // len(answer_order)
        frame_index = cycle % len(frames)
        distractor_owner_ids = list(combinations_by_owner[answer_owner_id][cycle])
        key = variant_key(source["family_id"], answer_owner_id, distractor_owner_ids)
        if key in used_keys:
            continue
        variants.append({
            "answer_owner_id": answer_owner_id,
            "distractor_owner_ids": distractor_owner_ids,
            "frame_index": frame_index,
            "variant_key": key,
        })
        if len(variants) == count:
            break
    if len(variants) != count:
        raise ValueError(f"insufficient unused closed-pack variants: {len(variants)}/{count}")
    if len({variant["variant_key"] for variant in variants}) != len(variants):
        raise ValueError("duplicate closed-pack variant planned")
    return variants


def select_closed_pack(data: dict[str, Any], family_id: str = "") -> dict[str, Any]:
    """단일 pack 또는 collection에서 요청한 family를 반환한다."""
    if isinstance(data.get("members"), list):
        pack = data
    else:
        packs = data.get("packs")
        if not isinstance(packs, list) or not packs:
            raise ValueError("closed pack input must contain members or a non-empty packs array")
        if not family_id and len(packs) != 1:
            raise ValueError("--family-id is required when closed pack input contains multiple packs")
        pack = next((row for row in packs if row.get("family_id") == family_id), None) if family_id else packs[0]
        if pack is None:
            raise ValueError(f"closed pack family not found: {family_id}")
    if family_id and pack.get("family_id") != family_id:
        raise ValueError(f"closed pack family not found: {family_id}")
    return pack


def _basis_item(member: dict[str, Any], slot: int) -> dict[str, Any]:
    return {
        "slot_no": slot,
        "role": "answer" if slot == 0 else "distractor",
        "basis_item_id": member["choice_fact_id"],
        "article_id": member["owner_id"],
        "truth_owner_label": member["owner_label"],
        "fact_basis": member["fact_basis"],
        "evidence_chunks": member["fact_evidence_chunks"],
        "status": "rag_ready",
        "semantic_status": "pass",
    }


def _chronology_basis_item(
    frame: dict[str, Any], members_by_owner: dict[str, dict[str, Any]], slot: int, fact_basis: str
) -> dict[str, Any]:
    answer = members_by_owner[str(frame["answer_owner_id"])]
    evidence = [
        row
        for event_id in frame["event_ids"]
        for row in members_by_owner[event_id]["fact_evidence_chunks"]
    ]
    return {
        "slot_no": slot,
        "role": "answer" if slot == 0 else "distractor",
        "basis_item_id": f"{frame['frame_id']}:choice:{slot}",
        "article_id": answer["owner_id"],
        "truth_owner_label": answer["owner_label"],
        "fact_basis": fact_basis,
        "evidence_chunks": evidence,
        "status": "rag_ready",
        "semantic_status": "pass",
    }


def _chronology_items(frame: dict[str, Any], members_by_owner: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    task = frame["question_task"]
    if task == "timeline_position":
        positions = frame["timeline_positions"]
        correct = frame["correct_position"]
        ordered = [correct, *(position for position in positions if position != correct)]
        return [
            _chronology_basis_item(
                frame,
                members_by_owner,
                slot,
                f"연표 위치 판단 정보이다. 대상 사건은 연표의 {position} 구간에 놓인다. "
                "연표 앞뒤의 기준 사건을 비교해 해당 사건이 어느 시점 사이에 들어가는지를 판단한다.",
            )
            for slot, position in enumerate(ordered)
        ]
    if task == "order":
        labels = frame["event_labels"]
        correct = tuple(frame["correct_order"])
        orders = [correct, *(order for order in itertools.permutations(frame["event_ids"]) if order != correct)][:5]
        return [
            _chronology_basis_item(
                frame,
                members_by_owner,
                slot,
                f"{' - '.join(labels[event_id] for event_id in order)} 배열은 자료에 제시된 사건을 해당 순서로 "
                "배치하는 시간 순서 판단 정보이다. 각 사건의 검증된 발생 시기를 비교해 차례로 판단한다.",
            )
            for slot, order in enumerate(orders)
        ]
    answer = members_by_owner[str(frame["answer_owner_id"])]
    distractors = [members_by_owner[event_id] for event_id in frame["distractor_owner_ids"]]
    return [_basis_item(answer, 0), *(_basis_item(member, slot) for slot, member in enumerate(distractors, 1))]


def build_generation_pack(
    source: dict[str, Any], *, answer_owner_id: str = "", distractor_owner_ids: list[str] | None = None,
    frame_index: int = 0, seed: int = 20260715
) -> dict[str, Any]:
    """closed pack의 owner 하나를 정답으로 회전해 기존 5선지 입력을 만든다."""
    validate_closed_pack_source(source)
    members = source["members"]
    frames = source["question_frames"]
    if not 0 <= frame_index < len(frames):
        raise ValueError(f"frame index out of range: {frame_index}")
    frame = frames[frame_index]
    if is_chronology_source(source):
        frame_answer_id = str(frame["answer_owner_id"])
        frame_distractor_ids = list(frame["distractor_owner_ids"])
        if answer_owner_id and answer_owner_id != frame_answer_id:
            raise ValueError("chronology answer must match the selected frame")
        if distractor_owner_ids is not None and distractor_owner_ids != frame_distractor_ids:
            raise ValueError("chronology distractors must match the selected frame")
        answer_owner_id = frame_answer_id
        distractor_owner_ids = frame_distractor_ids
        eligible_owner_ids = [frame_answer_id]
    else:
        eligible_owner_ids = source["answer_eligible_owner_ids"]
    eligible_answers = [member for member in members if member["owner_id"] in eligible_owner_ids]
    answer = next((member for member in members if member["owner_id"] == answer_owner_id), None) if answer_owner_id else None
    if answer_owner_id and answer is None:
        raise ValueError(f"answer owner not found in closed pack: {answer_owner_id}")
    if answer is None:
        answer = eligible_answers[stable_start(seed, str(source["family_id"]), len(eligible_answers))]
    if answer["owner_id"] not in eligible_owner_ids:
        raise ValueError(f"answer owner is not generation eligible: {answer['owner_id']}")

    distractors = [member for member in members if member["owner_id"] != answer["owner_id"]]
    if distractor_owner_ids is not None:
        if len(distractor_owner_ids) != 4 or len(set(distractor_owner_ids)) != 4 or answer["owner_id"] in distractor_owner_ids:
            raise ValueError("exactly four distinct non-answer distractor owner IDs are required")
        by_owner = {member["owner_id"]: member for member in distractors}
        if any(owner_id not in by_owner for owner_id in distractor_owner_ids):
            raise ValueError("distractor owner not found in closed pack")
        distractors = [by_owner[owner_id] for owner_id in distractor_owner_ids]
    else:
        random.Random(f"{seed}:{source['family_id']}:{answer['owner_id']}:{frame_index}").shuffle(distractors)
    axis = str(frame.get("relation_axis_id") or source.get("relation_axis_id") or "")
    task = str(frame.get("question_task") or "")
    stem = str(frame.get("stem_pattern") or "")
    major_type = str(frame.get("major_type") or "").strip()
    minor_type = str(frame.get("minor_type") or "").strip()
    instruction = str(frame.get("question_task_instruction") or "").strip()
    distractor_type = str(frame.get("distractor_type") or "").strip()
    difficulty = int(frame.get("difficulty") or source.get("difficulty") or 0)

    selected_distractor_ids = [member["owner_id"] for member in distractors[:4]]
    key = variant_key(source["family_id"], answer["owner_id"], selected_distractor_ids)
    result = {
        "pack_id": f"{key}:frame:{frame_index}",
        "family_id": source["family_id"],
        "era": source.get("era"),
        "service_era": answer.get("service_era"),
        "service_topic": answer.get("service_topic"),
        "service_question_type": frame.get("service_question_type"),
        "service_question_subtype": frame.get("service_question_subtype"),
        "target_label": answer["owner_label"],
        "topic_type": source["topic_type"],
        "question_task": task,
        "choice_mode": frame.get("choice_mode", "generated"),
        "stem_pattern": stem,
        "relation_axis_id": axis,
        "material_type": frame["material_type"],
        "major_type": major_type,
        "minor_type": minor_type,
        "difficulty_label": DIFFICULTY_LABELS[difficulty],
        "question_task_instruction": instruction,
        "distractor_type": distractor_type,
        "material_clue_basis": frame.get("material_clue_basis") or answer.get("material_clue_basis"),
        "material_evidence_chunks": frame.get("material_evidence_chunks") or answer.get("material_evidence_chunks"),
        "material_fact_semantically_distinct": frame.get("material_fact_semantically_distinct") is True or answer.get("material_fact_semantically_distinct") is True,
        "status": "rag_ready",
        "semantic_status": "pass",
        "variant_key": key,
        "items": (
            _chronology_items(frame, {str(member["owner_id"]): member for member in members})
            if is_chronology_source(source)
            else [_basis_item(answer, 0), *(_basis_item(member, slot) for slot, member in enumerate(distractors[:4], 1))]
        ),
    }
    if is_chronology_source(source):
        result["chronology"] = {
            field: frame[field]
            for field in (
                "frame_id", "event_ids", "event_labels", "material_owner_ids",
                "frame_evidence_chunk_ids", "answer_owner_id", "distractor_owner_ids",
                "anchor_event_ids", "reference_event_ids", "timeline_positions", "correct_position", "correct_order",
            )
            if field in frame
        }
    return result
