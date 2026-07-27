"""검수된 owner 근거로 실패한 체크포인트의 해당 컴포넌트만 다시 연다."""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from ai.question_generation.workflows.closed_pack_batch import read_json, write_json_atomic
from ai.question_generation.workflows.question_pipeline import invalidate
from storage.postgresql.connection import connect_db


PROJECT_ROOT = Path(__file__).resolve().parents[3]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Apply evidence-backed source repairs to question checkpoints.")
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--overrides", type=Path, required=True)
    return parser.parse_args()


def read_evidence(chunk_ids: set[str]) -> dict[str, dict[str, Any]]:
    conn = connect_db()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT chunk_id, article_id, truth_owner_label, chunk_text, source_url, section_path
                FROM rag.encykorea_chunks
                WHERE chunk_id = ANY(%s)
                """,
                (list(chunk_ids),),
            )
            return {
                row[0]: {
                    "chunk_id": row[0], "article_id": row[1], "owner_label": row[2],
                    "snippet": row[3], "exact_text": row[3], "source_url": row[4], "section_path": row[5],
                }
                for row in cursor.fetchall()
            }
    finally:
        conn.close()


def apply_override(state: dict[str, Any], override: dict[str, Any], evidence: dict[str, dict[str, Any]]) -> None:
    target = str(override["target"])
    basis = str(override["basis"]).strip()
    chunk_ids = [str(value) for value in override["evidence_chunk_ids"]]
    if not basis or not chunk_ids or any(chunk_id not in evidence for chunk_id in chunk_ids):
        raise ValueError(f"missing basis or evidence: {target}")

    item = state["input"]
    if target == "material":
        owner = item["answer_basis"]
    elif target == "correct":
        owner = item["answer_basis"]
    elif target.startswith("distractor:"):
        slot = int(target.split(":", 1)[1])
        owner = next((row for row in item["distractors"] if int(row["slot"]) == slot), None)
        if owner is None:
            raise ValueError(f"unknown distractor slot: {slot}")
    else:
        raise ValueError(f"invalid source repair target: {target}")
    supporting_article_ids = {str(value) for value in override.get("supporting_article_ids", [])}
    if any(
        evidence[chunk_id]["article_id"] != owner["owner_id"]
        and evidence[chunk_id]["article_id"] not in supporting_article_ids
        for chunk_id in chunk_ids
    ):
        raise ValueError(f"evidence owner mismatch: {target}")

    if target == "material":
        if set(chunk_ids) & set(item["answer_basis"]["evidence_chunk_ids"]):
            raise ValueError("material and answer evidence must be disjoint")
        chunks = [evidence[chunk_id] for chunk_id in chunk_ids]
        item["material_clue_basis"] = [basis]
        item["material_clue_evidence"] = chunks
        item["material_sources"] = [
            {
                "chunk_id": chunk["chunk_id"],
                "source_type": "encykorea_material_clue",
                "title": owner["owner_label"],
                "snippet": f"정리된 지문 단서: {basis}\n근거 원문: {chunk['exact_text']}",
                "url": chunk["source_url"],
            }
            for chunk in chunks
        ]
        item["material_contract"]["allowed_evidence_ids"] = chunk_ids
    else:
        owner["fact_basis"] = basis
        owner["evidence_chunk_ids"] = chunk_ids
        if target == "correct":
            allowed = set(item["material_contract"]["allowed_evidence_ids"])
            if allowed & set(chunk_ids):
                raise ValueError("material and answer evidence must be disjoint")
            item["material_contract"]["forbidden_answer_evidence_ids"] = chunk_ids

    invalidate(state, [target], {target: str(override.get("feedback") or "검수된 owner 근거로 다시 생성한다.")}, evaluation=True)
    state["status"] = "prepared"
    state["assembly_attempts"] = 0
    state.pop("error", None)


def main() -> int:
    load_dotenv(PROJECT_ROOT / ".env")
    args = parse_args()
    data = read_json(args.overrides)
    rows = data.get("items", [])
    grouped: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[int(row["index"])].append(row)
    chunk_ids = {str(chunk_id) for row in rows for chunk_id in row.get("evidence_chunk_ids", [])}
    evidence = read_evidence(chunk_ids)
    if set(evidence) != chunk_ids:
        raise ValueError("one or more evidence_chunk_ids do not exist")

    for index, overrides in grouped.items():
        path = args.run_dir / "items" / f"{index:04d}.json"
        state = read_json(path)
        for override in overrides:
            apply_override(state, override, evidence)
        write_json_atomic(path, state)
    print(json.dumps({"updated": len(grouped), "overrides": len(rows)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
