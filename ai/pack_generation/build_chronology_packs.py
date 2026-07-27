"""명시적으로 검수한 사건 계획을 DB evidence가 결합된 연대기 pack으로 만든다."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from itertools import permutations
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from ai.pack_generation.builder import validate_pack_bank
from ai.question_generation.core.contracts import generation_item, validate_pack
from ai.question_generation.retrieval.closed_pack_input import build_generation_pack
from storage.postgresql.connection import connect_db

DIFFICULTY_LABELS = {1: "쉬움", 2: "보통", 3: "어려움"}
EVENT_LABELS = tuple(f"({value})" for value in "가나다라마바사아자")
POSITION_LABELS = EVENT_LABELS[:5]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    return parser.parse_args()


def read_db_events(conn: Any, specs: list[dict[str, Any]]) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    article_ids = [spec["article_id"] for spec in specs]
    with conn.cursor() as cursor:
        cursor.execute(
            """
            SELECT article_id, title, era, source_url
            FROM rag.encykorea_articles
            WHERE article_id = ANY(%s)
            """,
            (article_ids,),
        )
        articles = {
            row[0]: {"title": row[1], "era": row[2], "source_url": row[3]}
            for row in cursor.fetchall()
        }
        cursor.execute(
            """
            SELECT article_id, chunk_id, section_path, chunk_index, chunk_text, source_url
            FROM rag.encykorea_chunks
            WHERE article_id = ANY(%s)
            ORDER BY article_id, chunk_index
            """,
            (article_ids,),
        )
        chunks: dict[str, list[tuple[Any, ...]]] = {article_id: [] for article_id in article_ids}
        for row in cursor.fetchall():
            chunks[row[0]].append(row)

    events: dict[str, dict[str, Any]] = {}
    selections: list[dict[str, Any]] = []
    missing: list[str] = []
    for spec in specs:
        article_id = spec["article_id"]
        article = articles.get(article_id)
        if not article or article["title"] != spec["topic"]:
            raise ValueError(f"article title mismatch: {article_id} {spec['topic']}")
        markers = list(spec["evidence_markers"])
        chunk_ids = list(spec.get("evidence_chunk_ids") or [spec.get("evidence_chunk_id")])
        selected_rows = [row for chunk_id in chunk_ids for row in chunks[article_id] if row[1] == chunk_id]
        combined_text = "\n".join(row[4] for row in selected_rows)
        if len(selected_rows) != len(chunk_ids) or any(marker not in combined_text for marker in markers):
            missing.append(f"{article_id} {spec['topic']}: {chunk_ids} {markers}")
            continue
        evidence = [
            {
                "snippet": spec["material_clue"],
                "chunk_id": chunk_id,
                "article_id": article_id,
                "exact_text": chunk_text,
                "source_url": source_url,
                "section_path": section_path,
            }
            for _, chunk_id, section_path, _, chunk_text, source_url in selected_rows
        ]
        events[article_id] = {
            "event_id": article_id,
            "choice_fact_id": f"chronology:{article_id}:{spec['sort_key']}",
            "owner_id": article_id,
            "owner_label": spec["topic"],
            "owner_type": "사건",
            "topic": spec["topic"],
            "topic_type": "사건",
            "fact_basis": f"{spec['time_label']}에 {spec['fact_summary']}",
            "fact_evidence_chunks": evidence,
            "time_label": spec["time_label"],
            "sort_key": int(spec["sort_key"]),
            "material_clue": spec["material_clue"],
        }
        selections.append({
            "event_id": article_id,
            "topic": spec["topic"],
            "chunk_ids": chunk_ids,
            "evidence_markers": markers,
            "candidate_count": len(chunk_ids),
        })
    if missing:
        raise ValueError("no direct evidence matching plan:\n" + "\n".join(missing))
    return events, selections


def evidence_rows(event_ids: list[str], events: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        dict(row)
        for event_id in event_ids
        for row in events[event_id]["fact_evidence_chunks"]
    ]


def labels(event_ids: list[str]) -> dict[str, str]:
    return {event_id: EVENT_LABELS[index] for index, event_id in enumerate(event_ids)}


def make_frame(
    pack_id: str,
    frame_no: int,
    task: str,
    difficulty: int,
    pattern: dict[str, Any],
    ordered: list[dict[str, Any]],
) -> dict[str, Any]:
    event = lambda index: ordered[index]
    frame_id = f"{pack_id}:frame:{frame_no:02d}"
    common = {
        "frame_id": frame_id,
        "question_task": task,
        "difficulty": difficulty,
        "major_type": "연대기의 파악",
        "answer_owner_scope": "frame_answer",
        "material_fact_semantically_distinct": True,
    }
    if task == "period_between":
        anchors = [event(index)["owner_id"] for index in pattern["anchor_indices"]]
        answer_id = event(pattern["correct_index"])["owner_id"]
        distractor_ids = [event(index)["owner_id"] for index in pattern["distractor_indices"]]
        event_ids = [*anchors, answer_id, *distractor_ids]
        frame_labels = labels(event_ids)
        common.update({
            "choice_mode": "generated",
            "stem_pattern": "before_after",
            "relation_axis_id": "event.chronology.period_between",
            "material_type": "연표 자료",
            "minor_type": "전후 시기 판단",
            "question_task_instruction": "두 사건이나 자료 사이의 시기를 판단하게 하는 발문을 만들고, 그 사이에 해당하는 answer_fact_basis의 사실을 정답 선지로 만든다.",
            "distractor_type": "outside_period",
            "event_ids": event_ids,
            "event_labels": frame_labels,
            "answer_owner_id": answer_id,
            "distractor_owner_ids": distractor_ids,
            "anchor_event_ids": anchors,
            "material_owner_ids": anchors,
            "correct_position": "between",
            "material_clue_basis": " ".join(
                f"{frame_labels[event_id]} {next(row['material_clue'] for row in ordered if row['owner_id'] == event_id)}"
                for event_id in anchors
            ),
        })
    elif task == "timeline_position":
        target_id = event(pattern["target_index"])["owner_id"]
        reference_ids = [event(index)["owner_id"] for index in pattern["reference_indices"]]
        event_ids = [target_id, *reference_ids]
        frame_labels = labels(event_ids)
        sorted_references = sorted((events for events in ordered if events["owner_id"] in reference_ids), key=lambda row: row["sort_key"])
        target_sort = next(row["sort_key"] for row in ordered if row["owner_id"] == target_id)
        interval = next(
            index for index in range(len(sorted_references) - 1)
            if sorted_references[index]["sort_key"] < target_sort < sorted_references[index + 1]["sort_key"]
        )
        positions = list(POSITION_LABELS)
        distractor_ids = [row["owner_id"] for index, row in enumerate(sorted_references) if index != interval][:4]
        common.update({
            "choice_mode": "timeline_position",
            "stem_pattern": "event_period",
            "relation_axis_id": "event.chronology.timeline_position",
            "material_type": "연표 자료",
            "minor_type": "연표·흐름 빈칸",
            "question_task_instruction": "material의 사건이 연표의 어느 구간에 해당하는지 묻는 발문을 만들고, answer_fact_basis의 위치 근거를 정답 기호로 만든다.",
            "distractor_type": "wrong_timeline_position",
            "event_ids": event_ids,
            "event_labels": frame_labels,
            "answer_owner_id": target_id,
            "distractor_owner_ids": distractor_ids,
            "reference_event_ids": [row["owner_id"] for row in sorted_references],
            "material_owner_ids": event_ids,
            "timeline_positions": positions,
            "correct_position": positions[interval],
            "material_clue_basis": (
                f"{next(row['material_clue'] for row in ordered if row['owner_id'] == target_id)} "
                + " - ".join(
                    f"{row['time_label']} {row['owner_label']}" for row in sorted_references
                )
            ),
        })
    else:
        display = [event(index) for index in pattern["display_indices"]]
        event_ids = [row["owner_id"] for row in display]
        frame_labels = labels(event_ids)
        correct_order = [row["owner_id"] for row in sorted(display, key=lambda row: row["sort_key"])]
        remaining = [row["owner_id"] for row in ordered if row["owner_id"] not in event_ids]
        common.update({
            "choice_mode": "order",
            "stem_pattern": "chronological_order",
            "relation_axis_id": "event.chronology.order",
            "material_type": "사건 배열 자료",
            "minor_type": "사건·자료 순서 배열",
            "question_task_instruction": "material에 제시된 사건들의 시간 순서를 묻는 발문을 만들고, answer_fact_basis의 순서 근거를 정답 형식으로 만든다.",
            "distractor_type": "wrong_sequence",
            "event_ids": event_ids,
            "event_labels": frame_labels,
            "answer_owner_id": correct_order[0],
            "distractor_owner_ids": remaining[:4],
            "material_owner_ids": event_ids,
            "correct_order": correct_order,
            "material_clue_basis": " ".join(
                f"{frame_labels[row['owner_id']]} {row['material_clue']}" for row in display
            ),
        })
    material_ids = common["material_owner_ids"]
    common["material_evidence_chunks"] = evidence_rows(material_ids, {row["owner_id"]: row for row in ordered})
    common["frame_evidence_chunk_ids"] = [
        row["chunk_id"] for row in evidence_rows(common["event_ids"], {row["owner_id"]: row for row in ordered})
    ]
    return common


def build(plan: dict[str, Any], events: dict[str, dict[str, Any]]) -> dict[str, Any]:
    patterns = plan["frame_patterns"]
    pattern_offsets: Counter[str] = Counter()
    packs = []
    for spec in plan["packs"]:
        ordered = sorted((events[event["article_id"]] for event in spec["events"]), key=lambda row: row["sort_key"])
        frames = []
        for frame_no, (task, difficulty) in enumerate(zip(spec["frame_tasks"], spec["frame_difficulties"], strict=True), 1):
            pattern_list = patterns[task]
            pattern = pattern_list[pattern_offsets[task] % len(pattern_list)]
            pattern_offsets[task] += 1
            frames.append(make_frame(spec["family_id"], frame_no, task, difficulty, pattern, ordered))
        packs.append({
            "family_id": spec["family_id"],
            "difficulty": 2,
            "era": spec["era"],
            "topic": spec["topic"],
            "topic_type": "사건",
            "relation_axis_id": "event.chronology",
            "question_frames": frames,
            "status": "final_reviewed",
            "members": ordered,
        })
    return {"production_plan": plan["production_plan"], "pack_count": len(packs), "packs": packs}


def validate_frame_answers(pack: dict[str, Any]) -> int:
    """명시된 정렬값으로 각 frame의 정답이 하나뿐인지 검증한다."""
    sort_keys = {member["event_id"]: member["sort_key"] for member in pack["members"]}
    if len(set(sort_keys.values())) != len(sort_keys):
        raise ValueError(f"ambiguous event order: {pack['family_id']}")

    checked = 0
    for frame in pack["question_frames"]:
        task = frame["question_task"]
        answer_id = frame["answer_owner_id"]
        if task == "period_between":
            start_id, end_id = frame["anchor_event_ids"]
            start, end = sorted((sort_keys[start_id], sort_keys[end_id]))
            if not start < sort_keys[answer_id] < end:
                raise ValueError(f"answer is outside period: {frame['frame_id']}")
            if any(start < sort_keys[event_id] < end for event_id in frame["distractor_owner_ids"]):
                raise ValueError(f"distractor is inside period: {frame['frame_id']}")
        elif task == "timeline_position":
            references = frame["reference_event_ids"]
            if references != sorted(references, key=sort_keys.__getitem__):
                raise ValueError(f"timeline references are not ordered: {frame['frame_id']}")
            target = sort_keys[answer_id]
            matching = [
                index
                for index, (left, right) in enumerate(zip(references, references[1:]))
                if sort_keys[left] < target < sort_keys[right]
            ]
            if len(matching) != 1 or frame["correct_position"] != frame["timeline_positions"][matching[0]]:
                raise ValueError(f"timeline answer is not unique: {frame['frame_id']}")
        elif task == "order":
            expected = sorted(frame["event_ids"], key=sort_keys.__getitem__)
            if frame["correct_order"] != expected:
                raise ValueError(f"incorrect explicit order: {frame['frame_id']}")
        else:
            raise ValueError(f"unsupported chronology task: {task}")
        checked += 1
    return checked


def validate_outcome_coverage(frames: list[dict[str, Any]]) -> None:
    """충분한 프레임이 있으면 정답 위치·배열을 가능한 결과에 고르게 배분한다."""
    timeline = [frame for frame in frames if frame["question_task"] == "timeline_position"]
    positions = set(position for frame in timeline for position in frame["timeline_positions"])
    if positions and len(timeline) >= len(positions):
        counts = Counter(frame["correct_position"] for frame in timeline)
        if set(counts) != positions or max(counts.values()) - min(counts.values()) > 1:
            raise ValueError(f"timeline position distribution is biased: {dict(counts)}")

    ordered = [frame for frame in frames if frame["question_task"] == "order"]
    possible_orders = set()
    actual_orders = []
    for frame in ordered:
        labels = tuple(frame["event_labels"][event_id] for event_id in frame["event_ids"])
        possible_orders.update(permutations(labels))
        actual_orders.append(tuple(frame["event_labels"][event_id] for event_id in frame["correct_order"]))
    if possible_orders and len(ordered) >= len(possible_orders):
        counts = Counter(actual_orders)
        if set(counts) != possible_orders or max(counts.values()) - min(counts.values()) > 1:
            raise ValueError(f"order distribution is biased: {dict(counts)}")


def validate_result(result: dict[str, Any], plan: dict[str, Any]) -> dict[str, Any]:
    packs = result["packs"]
    validate_pack_bank(packs)
    frames = [frame for pack in packs for frame in pack["question_frames"]]
    task_counts = Counter(frame["question_task"] for frame in frames)
    difficulty_counts = Counter(frame["difficulty"] for frame in frames)
    expected_tasks = {key: int(value) for key, value in plan["production_plan"]["task_quota"].items()}
    expected_difficulties = {int(key): int(value) for key, value in plan["production_plan"]["difficulty_quota"].items()}
    if len(packs) != plan["production_plan"]["pack_count"] or len(frames) != plan["production_plan"]["frame_count"]:
        raise ValueError("pack or frame count does not match production plan")
    if dict(task_counts) != expected_tasks or dict(difficulty_counts) != expected_difficulties:
        raise ValueError("task or difficulty distribution does not match production plan")
    validate_outcome_coverage(frames)
    generation_items = []
    answer_checks = 0
    for pack in packs:
        answer_checks += validate_frame_answers(pack)
        for index, frame in enumerate(pack["question_frames"]):
            generated_pack = validate_pack(build_generation_pack(pack, frame_index=index))
            generation_items.append(generation_item(generated_pack))
            if generated_pack["chronology"]["frame_id"] != frame["frame_id"]:
                raise ValueError("source/runtime frame mismatch")
    return {
        "status": "pass",
        "pack_count": len(packs),
        "frame_count": len(frames),
        "task_counts": dict(task_counts),
        "difficulty_counts": {str(key): value for key, value in difficulty_counts.items()},
        "generation_contract_pass": len(generation_items),
        "unique_answer_pass": answer_checks,
    }


def main() -> int:
    args = parse_args()
    plan = json.loads(args.plan.read_text(encoding="utf-8"))
    specs = [event for pack in plan["packs"] for event in pack["events"]]
    if len({event["article_id"] for event in specs}) != len(specs):
        raise ValueError("events must not be reused across chronology packs")
    load_dotenv(Path(__file__).resolve().parents[2] / ".env")
    conn = connect_db()
    try:
        events, evidence_selections = read_db_events(conn, specs)
    finally:
        conn.close()
    result = build(plan, events)
    validation = validate_result(result, plan)
    report = {**validation, "evidence_selections": evidence_selections}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"output": str(args.output.resolve()), "report": str(args.report.resolve()), **validation}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
