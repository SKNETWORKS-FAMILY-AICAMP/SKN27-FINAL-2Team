"""ChoiceFact 풀 후보를 LLM으로 검수해 정답 1개와 오답 4개를 조립한다.

PostgreSQL ``qgen.choice_facts``에서 후보를 8~12개로 좁힌 뒤 LLM은 그중
오답 ID 4개만 고른다. 결과는 ``validate_pack``이 읽을 수 있는 runtime basis
pack JSON이다.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import sys
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from openai import OpenAI


PROJECT_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(PROJECT_ROOT))

from app.chatbot.rag.pgvector_retriever import connect_db
from question_generation.core.contracts import validate_pack


def parse_args() -> argparse.Namespace:
    """풀 조립 조건과 출력 경로를 읽는다."""
    parser = argparse.ArgumentParser(
        description="Assemble a runtime basis pack from the approved ChoiceFact pool."
    )
    parser.add_argument("--output", type=Path, default="C:\\Users\\Playdata\\Desktop\\문제생성 파이프라인 산출물\\outputs")
    parser.add_argument("--difficulty", choices=("쉬움", "보통", "어려움"), default="쉬움")
    parser.add_argument("--question-task")
    parser.add_argument("--stem-pattern")
    parser.add_argument("--source-pack-id")
    parser.add_argument("--selector-model", default=os.getenv("OPENAI_POOL_SELECTOR_MODEL", "gpt-5.6-terra"))
    parser.add_argument("--reasoning-effort", default="medium")
    parser.add_argument("--seed", type=int, default=20260718)
    return parser.parse_args()


def dict_rows(cursor) -> list[dict[str, Any]]:
    """DB cursor 결과를 컬럼명이 있는 dict 목록으로 변환한다."""
    columns = [column[0] for column in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]


def read_targets(conn, args: argparse.Namespace) -> list[dict[str, Any]]:
    """난이도·발문 조건과 frame 검수를 통과한 정답을 조회한다."""
    clauses = [
        "p.difficulty_label = %s",
        "p.status = 'rag_ready'",
        "p.semantic_status = 'pass'",
        "p.stem_pattern <> 'standard_other'",
        "NULLIF(BTRIM(p.material_clue_basis), '') IS NOT NULL",
        "JSONB_ARRAY_LENGTH(p.material_evidence_chunks) > 0",
    ]
    params: list[Any] = [args.difficulty]
    for column, value in (
        ("p.question_task", args.question_task),
        ("p.stem_pattern", args.stem_pattern),
        ("p.pack_id", args.source_pack_id),
    ):
        if value:
            clauses.append(f"{column} = %s")
            params.append(value)

    with conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT p.pack_id AS source_pack_id, p.target_article_id, p.target_label,
                   p.topic_type, p.question_task, p.stem_pattern, p.relation_axis_id,
                   p.material_type, p.major_type, p.minor_type, p.difficulty_label,
                   p.material_clue_basis, p.material_evidence_chunks,
                   ta.era AS target_era, ta.field AS target_field,
                   af.choice_fact_id AS answer_choice_fact_id,
                   af.article_id AS answer_article_id,
                   af.truth_owner_label AS answer_owner_label,
                   af.owner_type AS answer_owner_type,
                   af.fact_basis AS answer_fact_basis,
                   af.fact_fingerprint AS answer_fact_fingerprint,
                   af.evidence_chunks AS answer_evidence_chunks,
                   aa.era AS answer_era, aa.field AS answer_field,
                   ARRAY(
                       SELECT DISTINCT original.article_id
                       FROM qgen.basis_items original
                       WHERE original.pack_id = p.pack_id
                   ) AS original_owner_ids
            FROM qgen.basis_packs p
            JOIN qgen.composable_frames ready
              ON ready.frame_pack_id = p.pack_id
            JOIN qgen.frame_choice_compatibility answer_compat
              ON answer_compat.frame_pack_id = p.pack_id
             AND answer_compat.role = 'answer'
             AND answer_compat.status = 'pass'
            JOIN qgen.choice_facts af
              ON af.choice_fact_id = answer_compat.choice_fact_id
            JOIN rag.encykorea_articles ta ON ta.article_id = p.target_article_id
            JOIN rag.encykorea_articles aa ON aa.article_id = af.article_id
            WHERE {' AND '.join(clauses)}
              AND JSONB_ARRAY_LENGTH(af.evidence_chunks) > 0
            ORDER BY p.pack_id
            """,
            params,
        )
        return dict_rows(cur)


def read_candidates(conn, target: dict[str, Any]) -> list[dict[str, Any]]:
    """현재 frame에서 오답으로 직접 검수된 사실만 조회한다."""
    excluded_owner_ids = list(
        dict.fromkeys(
            [
                target["target_article_id"],
                target["answer_article_id"],
            ]
        )
    )
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT cf.choice_fact_id, cf.article_id, cf.truth_owner_label,
                   cf.owner_type, cf.relation_axis_id, cf.fact_basis,
                   cf.fact_fingerprint, cf.evidence_chunks,
                   ca.era, ca.field,
                   ARRAY_AGG(DISTINCT cp.pack_id ORDER BY cp.pack_id) AS source_pack_ids
            FROM qgen.choice_facts cf
            JOIN qgen.frame_choice_compatibility compat
              ON compat.choice_fact_id = cf.choice_fact_id
             AND compat.frame_pack_id = %s
             AND compat.role = 'distractor'
             AND compat.status = 'pass'
            LEFT JOIN qgen.choice_fact_sources cfs
              ON cfs.choice_fact_id = cf.choice_fact_id
            LEFT JOIN qgen.basis_items ci
              ON ci.basis_item_id = cfs.basis_item_id
            LEFT JOIN qgen.basis_packs cp
              ON cp.pack_id = ci.pack_id
            JOIN rag.encykorea_articles ca ON ca.article_id = cf.article_id
            WHERE cf.relation_axis_id = %s
              AND NOT (cf.article_id = ANY(%s))
              AND JSONB_ARRAY_LENGTH(cf.evidence_chunks) > 0
            GROUP BY cf.choice_fact_id, ca.era, ca.field
            ORDER BY cf.choice_fact_id
            """,
            [
                target["source_pack_id"],
                target["relation_axis_id"],
                excluded_owner_ids,
            ],
        )
        return dict_rows(cur)


def candidate_shortlist(
    candidates: list[dict[str, Any]], target: dict[str, Any], seed: int
) -> list[dict[str, Any]]:
    """소유자와 사실이 겹치지 않는 후보를 8~12개로 좁힌다."""
    selected: list[dict[str, Any]] = []
    owner_ids: set[str] = set()
    fingerprints: set[str] = set()

    def take(rows: list[dict[str, Any]]) -> None:
        random.Random(f"{seed}:{target['source_pack_id']}:12").shuffle(rows)
        for candidate in rows:
            owner_id = str(candidate["article_id"])
            fingerprint = str(candidate["fact_fingerprint"])
            if owner_id in owner_ids or fingerprint in fingerprints:
                continue
            selected.append(candidate)
            owner_ids.add(owner_id)
            fingerprints.add(fingerprint)
            if len(selected) == 12:
                return

    take(list(candidates))
    return selected if len(selected) >= 8 else []


def evidence_snippets(candidate: dict[str, Any]) -> list[str]:
    """후보 근거에서 LLM 판단에 필요한 짧은 원문만 꺼낸다."""
    chunks = candidate.get("evidence_chunks") or []
    if isinstance(chunks, str):
        chunks = json.loads(chunks)
    return [
        " ".join(str(chunk.get("exact_text") or chunk.get("snippet") or "").split())[:800]
        for chunk in chunks[:2]
        if isinstance(chunk, dict)
    ]


def select_distractors(
    client: OpenAI,
    target: dict[str, Any],
    candidates: list[dict[str, Any]],
    model: str,
    reasoning_effort: str,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """LLM이 후보 밖의 사실을 만들지 못하게 ID 4개만 선택시킨다."""
    payload = {
        "frame": {
            key: target.get(key)
            for key in (
                "target_label", "topic_type", "question_task", "stem_pattern",
                "relation_axis_id", "difficulty_label", "material_clue_basis",
            )
        },
        "answer": {
            "owner": target["answer_owner_label"],
            "fact": target["answer_fact_basis"],
            "evidence": evidence_snippets({"evidence_chunks": target["answer_evidence_chunks"]}),
        },
        "candidates": [
            {
                "id": row["choice_fact_id"],
                "owner": row["truth_owner_label"],
                "fact": row["fact_basis"],
                "evidence": evidence_snippets(row),
            }
            for row in candidates
        ],
    }
    response = client.chat.completions.create(
        model=model,
        reasoning_effort=reasoning_effort,
        response_format={"type": "json_object"},
        max_completion_tokens=800,
        messages=[
            {
                "role": "system",
                "content": (
                    "한국사 5지선다 문항의 오답 후보 선택기다. 새 사실을 쓰지 말고 제공된 candidate id 중 안전한 4개만 고른다. "
                    "각 후보 fact는 evidence에 의해 해당 owner의 참인 사실이어야 하고, 발문 조건을 target에 적용했을 때는 정답이 아니어야 한다. "
                    "정답과 복수 정답이 되거나 주체·시기·인과·변천 관계가 불명확한 후보는 제외한다. "
                    "응답 범주와 relation_axis_id를 유지하고 difficulty_label에 맞는 비교 부담을 고려한다. "
                    "안전한 4개가 없으면 selected_ids를 빈 배열로 반환한다. "
                    "JSON 객체 {\"selected_ids\":[\"id\",...],\"reason\":\"짧은 선정 근거\"}만 출력한다."
                ),
            },
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ],
    )
    result = json.loads(response.choices[0].message.content or "{}")
    selected_ids = result.get("selected_ids")
    if not isinstance(selected_ids, list) or len(selected_ids) != 4 or len(set(selected_ids)) != 4:
        raise ValueError("pool selector must return four distinct candidate ids")
    by_id = {row["choice_fact_id"]: row for row in candidates}
    if any(choice_id not in by_id for choice_id in selected_ids):
        raise ValueError("pool selector returned an id outside the candidate shortlist")
    return [by_id[choice_id] for choice_id in selected_ids], {
        "model": model,
        "reasoning_effort": reasoning_effort,
        "candidate_ids": list(by_id),
        "selected_ids": selected_ids,
        "reason": str(result.get("reason") or ""),
    }


def basis_item(slot_no: int, fact: dict[str, Any]) -> dict[str, Any]:
    """ChoiceFact 한 건을 basis pack의 answer/distractor slot 형태로 바꾼다."""
    return {
        "slot_no": slot_no,
        "role": "answer" if slot_no == 0 else "distractor",
        "choice_fact_id": fact.get("choice_fact_id"),
        "article_id": fact["article_id"],
        "truth_owner_label": fact["truth_owner_label"],
        "fact_basis": fact["fact_basis"],
        "evidence_chunks": fact["evidence_chunks"],
        "status": "rag_ready",
        "semantic_status": "pass",
    }


def build_runtime_pack(
    target: dict[str, Any], distractors: list[dict[str, Any]], seed: int
) -> dict[str, Any]:
    """선택한 정답·오답 사실을 기존 basis pack 계약 형태로 조립한다."""
    if len(distractors) != 4:
        raise ValueError("ChoiceFact pool must supply four distinct distractors")
    if any(row["relation_axis_id"] != target["relation_axis_id"] for row in distractors):
        raise ValueError("pool distractors must preserve relation_axis_id")

    selection = [target["answer_choice_fact_id"], *[row["choice_fact_id"] for row in distractors]]
    digest = hashlib.sha256("\x1f".join(selection).encode("utf-8")).hexdigest()[:24]
    answer = {
        "choice_fact_id": target["answer_choice_fact_id"],
        "article_id": target["answer_article_id"],
        "truth_owner_label": target["answer_owner_label"],
        "fact_basis": target["answer_fact_basis"],
        "evidence_chunks": target["answer_evidence_chunks"],
    }
    pack = {
        "pack_id": f"pool_pack:{digest}",
        "target_label": target["target_label"],
        "topic_type": target["topic_type"],
        "question_task": target["question_task"],
        "stem_pattern": target["stem_pattern"],
        "relation_axis_id": target["relation_axis_id"],
        "material_type": target["material_type"],
        "major_type": target["major_type"],
        "minor_type": target["minor_type"],
        "difficulty_label": target["difficulty_label"],
        "material_clue_basis": target["material_clue_basis"],
        "material_evidence_chunks": target["material_evidence_chunks"],
        "status": "rag_ready",
        "semantic_status": "pass",
        "items": [
            basis_item(0, answer),
            *[basis_item(slot, row) for slot, row in enumerate(distractors, start=1)],
        ],
        "pool_selection": {
            "strategy": "reviewed_frame_choice_compatibility_v1",
            "seed": seed,
            "source_target_pack_id": target["source_pack_id"],
            "answer_choice_fact_id": target["answer_choice_fact_id"],
            "distractor_choice_fact_ids": [row["choice_fact_id"] for row in distractors],
            "distractor_source_pack_ids": [row["source_pack_ids"] for row in distractors],
            "excluded_original_owner_ids": target["original_owner_ids"],
        },
    }
    return validate_pack(pack)


def assemble_from_pool(conn, args: argparse.Namespace, client: OpenAI) -> dict[str, Any]:
    """사용 가능한 target을 순회해 처음으로 완성 가능한 5선지 pack을 반환한다."""
    targets = read_targets(conn, args)
    random.Random(args.seed).shuffle(targets)
    for target in targets:
        candidates = candidate_shortlist(read_candidates(conn, target), target, args.seed)
        if not candidates:
            continue
        distractors, selector = select_distractors(
            client, target, candidates, args.selector_model, args.reasoning_effort
        )
        pack = build_runtime_pack(target, distractors, args.seed)
        pack["pool_selection"]["llm_selector"] = selector
        return pack
    raise ValueError("No target has four pool distractors under the requested profile")


def main() -> int:
    """ChoiceFact 풀 조립 CLI 진입점."""
    args = parse_args()
    load_dotenv(PROJECT_ROOT / ".env")
    conn = connect_db()
    try:
        pack = assemble_from_pool(conn, args, OpenAI())
    finally:
        conn.close()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(pack, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {
                "output": str(args.output.resolve()),
                "pack_id": pack["pack_id"],
                "source_target_pack_id": pack["pool_selection"]["source_target_pack_id"],
                "difficulty": pack["difficulty_label"],
                "relation_axis_id": pack["relation_axis_id"],
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
