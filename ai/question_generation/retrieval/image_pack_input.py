"""9-member 이미지 pack을 기존 단일 문항 생성 입력으로 변환한다."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from ai.question_generation.core.contracts import V41_TOPIC_TYPES


DIFFICULTY_LABELS = {1: "쉬움", 2: "보통", 3: "어려움"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build five-choice generation inputs from reviewed image packs.")
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    return parser.parse_args()


def generation_item(member: dict[str, Any], slot: int, include_image: bool = False) -> dict[str, Any]:
    clue = next(iter(member.get("material_clue_sources") or []), {})
    image = member.get("image") or member.get("choice_image") or {}
    evidence = member.get("evidence_chunks") or [
        {
            "chunk_id": chunk_id,
            "article_id": member["owner_id"],
            "snippet": clue.get("basis", ""),
            "exact_text": clue.get("basis", ""),
            "source_url": image.get("source_url", ""),
        }
        for chunk_id in clue.get("evidence_chunk_ids") or []
    ]
    item = {
        "slot_no": slot,
        "role": "answer" if slot == 0 else "distractor",
        "basis_item_id": member.get("choice_fact_id") or clue.get("clue_source_id") or image["image_chunk_id"],
        "article_id": member["owner_id"],
        "truth_owner_label": member["owner_label"],
        "fact_basis": member.get("fact_basis") or clue.get("basis"),
        "evidence_chunks": evidence,
        "status": "rag_ready",
        "semantic_status": "pass",
    }
    if include_image:
        item["image"] = {**image, "owner_id": member["owner_id"]}
    return item


def build_input(source: dict[str, Any]) -> dict[str, Any]:
    if int(source["difficulty"]) == 3:
        validation = source.get("validation", {})
        reviewed_nine = (
            validation.get("three_point_close_nine") is True
            or (
                source.get("choice_mode") == "image"
                and validation.get("member_count") == 9
                and validation.get("distinct_owner_count") == 9
                and validation.get("distinct_image_count") == 9
                and validation.get("unique_answer_contract") == "pass"
            )
        )
        if not reviewed_nine:
            raise ValueError(f"3-point image pack requires nine reviewed close members: {source['family_id']}")
    members = source["members"]
    by_owner = {str(member["owner_id"]): member for member in members}
    answer_id = str(source.get("answer_owner_id") or "")
    distractor_ids = source.get("distractor_owner_ids")
    if answer_id not in by_owner or not isinstance(distractor_ids, list) or len(distractor_ids) != 4:
        raise ValueError(f"image pack requires one answer_owner_id and four distractor_owner_ids: {source['family_id']}")
    if len(set(distractor_ids)) != 4 or answer_id in distractor_ids or any(owner_id not in by_owner for owner_id in distractor_ids):
        raise ValueError(f"image pack contains invalid selected owners: {source['family_id']}")
    answer = by_owner[answer_id]
    distractors = [by_owner[owner_id] for owner_id in distractor_ids]
    frame_id = str(source.get("frame_id") or "")
    frame = next((frame for frame in source["question_frames"] if str(frame.get("frame_id") or "") == frame_id), None)
    if frame is None:
        raise ValueError(f"image pack requires an explicit frame_id: {source['family_id']}")
    required_frame_fields = (
        "choice_mode", "question_task", "stem_pattern", "relation_axis_id", "material_type",
        "major_type", "minor_type", "question_task_instruction",
    )
    missing = [field for field in required_frame_fields if not str(frame.get(field) or "").strip()]
    if missing:
        raise ValueError(f"image frame lacks {missing}: {source['family_id']}")
    if source.get("topic_type") not in V41_TOPIC_TYPES:
        raise ValueError(f"image pack lacks an explicit V41 topic_type: {source['family_id']}")
    choice_mode = str(frame["choice_mode"])
    if choice_mode not in {"generated", "image"}:
        raise ValueError(f"unsupported image choice_mode: {choice_mode}")
    image = answer.get("image") or answer.get("choice_image") or {}
    if choice_mode == "image":
        clue = next(iter(answer.get("material_clue_sources") or []), {})
        visual_basis = str(clue.get("basis") or "").strip()
        material_evidence = [
            {
                "snippet": visual_basis,
                "chunk_id": chunk_id,
                "article_id": answer["owner_id"],
                "exact_text": visual_basis,
                "source_url": image.get("source_url", ""),
                "section_path": ["검수된 이미지 식별 단서"],
            }
            for chunk_id in clue.get("evidence_chunk_ids") or []
        ]
    else:
        visual_basis = str(image["visual_clue_basis"]).strip()
        material_evidence = [{
            "snippet": visual_basis,
            "chunk_id": image["image_chunk_id"],
            "article_id": answer["owner_id"],
            "exact_text": visual_basis,
            "source_url": image["source_url"],
            "section_path": ["시각 자료"],
        }]
    if not visual_basis or not material_evidence:
        raise ValueError(f"image pack lacks a reviewed material clue: {source['family_id']}")
    lines = visual_basis.splitlines()
    if lines and lines[0].strip() == answer["owner_label"]:
        raise ValueError(f"visual clue basis leaks the answer label: {source['family_id']}")
    result = {
        "pack_id": f"image_generation_pack:{source['family_id'].split(':', 1)[-1]}:{answer['owner_id']}",
        "family_id": source["family_id"],
        "era": answer.get("era") or source.get("era"),
        "service_era": answer.get("service_era"),
        "service_topic": answer.get("service_topic"),
        "service_question_type": frame.get("service_question_type"),
        "service_question_subtype": frame.get("service_question_subtype"),
        "variant_key": f"image:{source['family_id']}:{answer['owner_id']}",
        "target_label": answer["owner_label"],
        "topic_type": source["topic_type"],
        "question_task": frame["question_task"],
        "choice_mode": choice_mode,
        "stem_pattern": frame["stem_pattern"],
        "relation_axis_id": frame["relation_axis_id"],
        "material_type": frame["material_type"],
        "major_type": frame["major_type"],
        "minor_type": frame["minor_type"],
        "difficulty_label": DIFFICULTY_LABELS[int(source["difficulty"])],
        "question_task_instruction": frame["question_task_instruction"],
        "material_clue_basis": visual_basis,
        "material_evidence_chunks": material_evidence,
        "material_fact_semantically_distinct": choice_mode == "image",
        "status": "rag_ready",
        "semantic_status": "pass",
        "items": [
            generation_item(answer, 0, choice_mode == "image"),
            *(generation_item(member, index, choice_mode == "image") for index, member in enumerate(distractors, 1)),
        ],
    }
    if choice_mode == "generated":
        result["distractor_type"] = frame["distractor_type"]
        result["image"] = {**image, "frame_id": frame["frame_id"], "owner_id": answer["owner_id"]}
    return result


def build_inputs(source: dict[str, Any]) -> list[dict[str, Any]]:
    if source.get("choice_mode") != "image" or source.get("answer_owner_id"):
        return [build_input(source)]
    frames = source.get("question_frames") or []
    rotations = source.get("rotation_compatibility") or []
    if not frames or len(rotations) != len(source.get("members") or []):
        raise ValueError(f"image choice pack lacks reviewed rotations: {source.get('family_id')}")
    outputs = []
    for index, rotation in enumerate(rotations):
        distractors = list(rotation.get("eligible_distractor_owner_ids") or [])
        if rotation.get("status") != "pass" or len(distractors) < 4:
            raise ValueError(f"image choice rotation is not reviewed: {source.get('family_id')}")
        start = index % len(distractors)
        selected = (distractors + distractors)[start:start + 4]
        outputs.append(build_input({
            **source,
            "answer_owner_id": rotation["answer_owner_id"],
            "distractor_owner_ids": selected,
            "frame_id": frames[index % len(frames)]["frame_id"],
        }))
    return outputs


def main() -> None:
    args = parse_args()
    data = json.loads(args.input.read_text(encoding="utf-8-sig"))
    args.output_dir.mkdir(parents=True, exist_ok=True)
    outputs = []
    for source in data["packs"]:
        packs = build_inputs(source)
        for pack in packs:
            name = source["family_id"] if len(packs) == 1 else pack["pack_id"]
            path = args.output_dir / f"{name.replace(':', '_')}.json"
            path.write_text(json.dumps(pack, ensure_ascii=False, indent=2), encoding="utf-8")
            outputs.append(path)
    manifest = args.output_dir / "image_generation_pack_manifest.json"
    manifest.write_text(json.dumps({
        "version": "image_generation_pack_manifest_v2",
        "pack_count": len(outputs),
        "packs": [
            {
                "path": path.name,
                "era": json.loads(path.read_text(encoding="utf-8"))["era"],
            }
            for path in outputs
        ],
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"count": len(outputs), "outputs": [str(path) for path in outputs], "manifest": str(manifest)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
