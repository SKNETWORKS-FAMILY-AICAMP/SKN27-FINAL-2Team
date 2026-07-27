"""검증된 ChoiceFact만으로 9-member 폐쇄형 pack 후보를 만든다."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from app.chatbot.rag.pgvector_retriever import connect_db
from ai.question_generation.core.contracts import V41_TOPIC_TYPES
from ai.question_generation.core.exam_distribution import (
    DIFFICULTY_LABELS,
    ERA_ORDER,
    apportion,
    official_distribution,
    source_era,
)


AXIS_STEMS = {
    "common.definition_feature": {"target_description", "fill_blank"},
    "concept.effect_significance": {"result_effect"},
    "economy.production_distribution_tax": {"economic_condition"},
    "event.background_cause": {"background_cause"},
    "event.result_effect": {"result_effect"},
    "inquiry.topic_selection": {"appropriate_inquiry"},
    "organization.function": {"activity_achievement", "fill_blank"},
    "person.activity_achievement": {"activity_achievement", "fill_blank"},
    "person.policy_reform": {"policy_system"},
    "policy.background_purpose": {"background_cause"},
    "policy.content_method": {"policy_system", "content_function"},
    "policy.operation_finance": {"economic_condition"},
    "policy.result_effect": {"result_effect"},
    "state.economy_tax": {"economic_condition"},
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build reviewed closed-pack candidates without LLM calls.")
    parser.add_argument("--official-data", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--selection", type=Path, required=True)
    return parser.parse_args()


def direct_evidence(chunks: Any, owner_id: str) -> bool:
    return bool(chunks) and isinstance(chunks, list) and all(
        isinstance(chunk, dict) and chunk.get("article_id") == owner_id for chunk in chunks
    )


def evidence_ids(chunks: Any) -> set[str]:
    return {
        str(chunk.get("chunk_id"))
        for chunk in chunks if isinstance(chunk, dict) and chunk.get("chunk_id")
    } if isinstance(chunks, list) else set()


def different_text(fact: str, clue: str) -> bool:
    """완전히 같은 문장을 별도 단서로 쓰는 경우만 기계적으로 거부한다."""
    fact = " ".join(fact.split())
    clue = " ".join(clue.split())
    return bool(fact and clue and fact != clue)


def read_members(conn) -> list[dict[str, Any]]:
    """직접 owner 근거와 별도 material clue가 있는 사실만 읽는다."""
    with conn.cursor() as cursor:
        cursor.execute(
            """
            SELECT cf.choice_fact_id, cf.article_id, cf.truth_owner_label,
                   cf.owner_type, cf.relation_axis_id, cf.fact_basis,
                   cf.evidence_chunks, article.era,
                   ARRAY_AGG(DISTINCT source_pack.difficulty_label) AS difficulty_labels,
                   JSONB_AGG(DISTINCT JSONB_BUILD_OBJECT(
                       'source_pack_id', clue_pack.pack_id,
                       'basis', clue_pack.material_clue_basis,
                       'evidence', clue_pack.material_evidence_chunks
                   )) AS clues
            FROM qgen.choice_facts cf
            JOIN qgen.choice_fact_sources source USING (choice_fact_id)
            JOIN qgen.basis_items source_item USING (basis_item_id)
            JOIN qgen.basis_packs source_pack USING (pack_id)
            JOIN rag.encykorea_articles article ON article.article_id = cf.article_id
            JOIN qgen.basis_packs clue_pack
              ON clue_pack.target_article_id = cf.article_id
             AND clue_pack.status = 'rag_ready'
             AND clue_pack.semantic_status = 'pass'
             AND NULLIF(BTRIM(clue_pack.material_clue_basis), '') IS NOT NULL
             AND JSONB_ARRAY_LENGTH(clue_pack.material_evidence_chunks) > 0
            WHERE source_pack.status = 'rag_ready'
              AND source_pack.semantic_status = 'pass'
              AND source_item.status = 'rag_ready'
              AND source_item.semantic_status = 'pass'
              AND JSONB_ARRAY_LENGTH(cf.evidence_chunks) > 0
            GROUP BY cf.choice_fact_id, article.era
            ORDER BY cf.choice_fact_id
            """
        )
        columns = [column[0] for column in cursor.description]
        rows = [dict(zip(columns, row)) for row in cursor.fetchall()]

    members: list[dict[str, Any]] = []
    for row in rows:
        era = source_era(str(row["era"] or ""))
        if not era or not direct_evidence(row["evidence_chunks"], row["article_id"]):
            continue
        clues = [
            clue for clue in row["clues"]
            if direct_evidence(clue.get("evidence"), row["article_id"])
            and different_text(row["fact_basis"], str(clue.get("basis") or ""))
        ]
        if not clues:
            continue
        fact_evidence_ids = evidence_ids(row["evidence_chunks"])
        clue = min(clues, key=lambda item: (
            not fact_evidence_ids.isdisjoint(evidence_ids(item.get("evidence"))),
            len(str(item["basis"])),
            item["source_pack_id"],
        ))
        for label in row["difficulty_labels"]:
            score = next((score for score, value in DIFFICULTY_LABELS.items() if value == label), None)
            if score:
                members.append({
                    "difficulty": score,
                    "era": era,
                    "relation_axis_id": row["relation_axis_id"],
                    "choice_fact_id": row["choice_fact_id"],
                    "owner_id": row["article_id"],
                    "owner_label": row["truth_owner_label"],
                    "owner_type": row["owner_type"],
                    "fact_basis": row["fact_basis"],
                    "fact_evidence_chunks": row["evidence_chunks"],
                    "material_clue_basis": clue["basis"],
                    "material_evidence_chunks": clue["evidence"],
                    "material_source_pack_id": clue["source_pack_id"],
                    "material_evidence_disjoint": fact_evidence_ids.isdisjoint(evidence_ids(clue["evidence"])),
                })
    return members


def read_frames(conn) -> tuple[dict[tuple[int, str, str], list[dict[str, str]]], list[dict[str, Any]]]:
    """공식 기출 source pack에서 일반 fact family가 사용할 수 있는 frame만 집계한다."""
    with conn.cursor() as cursor:
        cursor.execute(
            """
            SELECT pack.difficulty_label, article.era, pack.relation_axis_id,
                   pack.question_task, pack.stem_pattern, pack.material_type, COUNT(*)
            FROM qgen.basis_packs pack
            JOIN rag.encykorea_articles article ON article.article_id = pack.target_article_id
            WHERE pack.status = 'rag_ready' AND pack.semantic_status = 'pass'
            GROUP BY pack.difficulty_label, article.era, pack.relation_axis_id,
                     pack.question_task, pack.stem_pattern, pack.material_type
            """
        )
        rows = cursor.fetchall()

    observed: list[dict[str, Any]] = []
    grouped: dict[tuple[int, str, str], Counter[tuple[str, str, str]]] = defaultdict(Counter)
    for label, raw_era, axis, task, stem, material_type, count in rows:
        difficulty = next((score for score, value in DIFFICULTY_LABELS.items() if value == label), None)
        era = source_era(str(raw_era or ""))
        if not difficulty or not era:
            continue
        observed.append({
            "difficulty": difficulty,
            "era": era,
            "relation_axis_id": axis,
            "question_task": task,
            "stem_pattern": stem,
            "material_type": material_type,
            "count": count,
        })
        if (
            task != "standard_select"
            or stem not in AXIS_STEMS.get(axis, set())
            or material_type == "시각 자료 설명"
        ):
            continue
        frame = (task, stem, material_type)
        grouped[(difficulty, era, axis)][frame] += count

    frames: dict[tuple[int, str, str], list[dict[str, str]]] = {}
    for key, counts in grouped.items():
        selected = [frame for frame, _ in counts.most_common(3)]
        if len(selected) >= 2:
            frames[key] = [
                {"question_task": task, "stem_pattern": stem, "material_type": material_type}
                for task, stem, material_type in selected
            ]
    return frames, observed


def read_evidence_chunks(conn, chunk_ids: set[str]) -> dict[str, dict[str, Any]]:
    """검수자가 고른 민백 chunk를 기존 evidence payload 형식으로 읽는다."""
    if not chunk_ids:
        return {}
    with conn.cursor() as cursor:
        cursor.execute(
            """
            SELECT chunk_id, article_id, chunk_text, source_url, section_path
            FROM rag.encykorea_chunks
            WHERE chunk_id = ANY(%s)
            """,
            (list(chunk_ids),),
        )
        return {
            chunk_id: {
                "snippet": text,
                "chunk_id": chunk_id,
                "article_id": article_id,
                "exact_text": text,
                "source_url": source_url,
                "section_path": section_path,
            }
            for chunk_id, article_id, text, source_url, section_path in cursor.fetchall()
        }


def quota_for_limit(distribution: dict[int, Counter[str]], limit: int) -> dict[tuple[int, str], int]:
    difficulty_counts = Counter({1: 20, 2: 60, 3: 20})
    raw = {score: count * limit / 100 for score, count in difficulty_counts.items()}
    difficulty_quota = {score: int(value) for score, value in raw.items()}
    for score in sorted(raw, key=lambda value: raw[value] - difficulty_quota[value], reverse=True)[:limit - sum(difficulty_quota.values())]:
        difficulty_quota[score] += 1
    return {
        (score, era): count
        for score, total in difficulty_quota.items()
        for era, count in apportion(distribution[score], total).items()
        if count
    }


def build_reviewed_packs(
    members: list[dict[str, Any]],
    selection: dict[str, Any],
    evidence: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """사람이 전수 검수한 ChoiceFact 조합을 동일 계약의 pack으로 확정한다."""
    by_id = {member["choice_fact_id"]: member for member in members}
    used_facts: set[str] = set()
    packs: list[dict[str, Any]] = []
    for spec in selection.get("packs", []):
        difficulty = int(spec["difficulty"])
        era = spec["era"]
        axis = spec["relation_axis_id"]
        topic_type = spec.get("topic_type")
        if topic_type not in V41_TOPIC_TYPES:
            raise ValueError(f"Reviewed pack lacks topic_type: {(difficulty, era, axis)}")
        key = (difficulty, era, axis)
        pack_frames = spec.get("question_frames") or []
        if len(pack_frames) < 2:
            raise ValueError(f"Fewer than two compatible frames: {key}")
        if any(
            frame.get("question_task") != "standard_select"
            or frame.get("answer_owner_scope") != "material_target"
            or frame.get("stem_pattern") not in AXIS_STEMS.get(axis, set())
            or frame.get("material_type") == "시각 자료 설명"
            for frame in pack_frames
        ):
            raise ValueError(f"Incompatible reviewed frame: {key}")

        ids = list(spec.get("choice_fact_ids", []))
        additions = spec.get("additional_members", [])
        if len(ids) + len(additions) != 9 or len(set(ids)) != len(ids):
            raise ValueError(f"Exactly nine unique members are required: {key}")
        if used_facts.intersection(ids):
            raise ValueError(f"ChoiceFact reused across packs: {key}")

        selected: list[dict[str, Any]] = []
        overrides = spec.get("material_overrides", {})
        for choice_fact_id in ids:
            if choice_fact_id not in by_id:
                raise ValueError(f"Unknown or ineligible ChoiceFact: {choice_fact_id}")
            member = dict(by_id[choice_fact_id])
            if (member["difficulty"], member["era"], member["relation_axis_id"]) != key:
                raise ValueError(f"ChoiceFact contract mismatch: {choice_fact_id}")
            override = overrides.get(choice_fact_id)
            if override:
                chunk_ids = override.get("material_evidence_chunk_ids", [])
                chunks = [evidence[chunk_id] for chunk_id in chunk_ids if chunk_id in evidence]
                if len(chunks) != len(chunk_ids) or not direct_evidence(chunks, member["owner_id"]):
                    raise ValueError(f"Invalid material evidence override: {choice_fact_id}")
                member["material_clue_basis"] = override["material_clue_basis"]
                member["material_evidence_chunks"] = chunks
                member["material_source_pack_id"] = None
                member["material_evidence_disjoint"] = evidence_ids(member["fact_evidence_chunks"]).isdisjoint(
                    evidence_ids(chunks)
                )
            selected.append(member)

        for addition in additions:
            member_id = str(addition["member_id"])
            if member_id in used_facts:
                raise ValueError(f"Reviewed RAG fact reused across packs: {member_id}")
            fact_chunks = [evidence[chunk_id] for chunk_id in addition.get("fact_evidence_chunk_ids", []) if chunk_id in evidence]
            material_chunks = [
                evidence[chunk_id]
                for chunk_id in addition.get("material_evidence_chunk_ids", [])
                if chunk_id in evidence
            ]
            owner_id = str(addition["owner_id"])
            if not direct_evidence(fact_chunks, owner_id) or not direct_evidence(material_chunks, owner_id):
                raise ValueError(f"RAG member lacks direct owner evidence: {member_id}")
            if not different_text(addition["fact_basis"], addition["material_clue_basis"]):
                raise ValueError(f"RAG member fact and clue overlap: {member_id}")
            selected.append({
                "choice_fact_id": member_id,
                "owner_id": owner_id,
                "owner_label": addition["owner_label"],
                "owner_type": addition["owner_type"],
                "fact_basis": addition["fact_basis"],
                "fact_evidence_chunks": fact_chunks,
                "material_clue_basis": addition["material_clue_basis"],
                "material_evidence_chunks": material_chunks,
                "material_source_pack_id": None,
                "material_evidence_disjoint": evidence_ids(fact_chunks).isdisjoint(evidence_ids(material_chunks)),
                "source_type": "reviewed_rag",
            })
            ids.append(member_id)

        if len({member["owner_id"] for member in selected}) != 9 or len({member["owner_label"] for member in selected}) != 9:
            raise ValueError(f"Pack members must have nine distinct owners: {key}")
        used_facts.update(ids)
        digest = hashlib.sha256("\x1f".join(ids).encode()).hexdigest()[:20]
        packs.append({
            "family_id": f"closed_pack:{digest}",
            "difficulty": difficulty,
            "era": era,
            "relation_axis_id": axis,
            "topic_type": topic_type,
            "question_frames": pack_frames,
            "status": "pending_user_review",
            "members": [
                {name: value for name, value in member.items() if name not in {"difficulty", "era", "relation_axis_id"}}
                for member in selected
            ],
        })
    return packs


def main() -> int:
    args = parse_args()
    load_dotenv(Path(__file__).resolve().parents[3] / ".env")
    records = json.loads(args.official_data.read_text(encoding="utf-8"))
    distribution, unresolved = official_distribution(records)
    quota_100 = {
        str(score): apportion(distribution[score], total)
        for score, total in ((1, 20), (2, 60), (3, 20))
    }
    quota = quota_for_limit(distribution, args.limit)
    selection = json.loads(args.selection.read_text(encoding="utf-8"))
    override_chunk_ids = {
        chunk_id
        for pack in selection.get("packs", [])
        for override in pack.get("material_overrides", {}).values()
        for chunk_id in override.get("material_evidence_chunk_ids", [])
    }
    override_chunk_ids.update(
        chunk_id
        for pack in selection.get("packs", [])
        for member in pack.get("additional_members", [])
        for field in ("fact_evidence_chunk_ids", "material_evidence_chunk_ids")
        for chunk_id in member.get(field, [])
    )
    conn = connect_db()
    try:
        members = read_members(conn)
        frames, frame_distribution = read_frames(conn)
        override_evidence = read_evidence_chunks(conn, override_chunk_ids)
    finally:
        conn.close()
    packs = build_reviewed_packs(members, selection, override_evidence)
    capacity: Counter[tuple[int, str, str]] = Counter()
    owners: dict[tuple[int, str, str], set[str]] = defaultdict(set)
    for member in members:
        key = (member["difficulty"], member["era"], member["relation_axis_id"])
        if key not in frames:
            continue
        owners[key].add(member["owner_id"])
    for key, owner_ids in owners.items():
        capacity[key] = len(owner_ids) // 9
    result = {
        "official_distribution": {
            str(score): {era: distribution[score][era] for era in ERA_ORDER}
            for score in DIFFICULTY_LABELS
        },
        "official_unresolved": dict(unresolved),
        "quota_100": quota_100,
        "pilot_quota": {f"{score}:{era}": count for (score, era), count in quota.items()},
        "official_frame_distribution": frame_distribution,
        "capacity": [
            {"difficulty": key[0], "era": key[1], "relation_axis_id": key[2], "pack_capacity": count}
            for key, count in sorted(capacity.items()) if count
        ],
        "packs": packs,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"output": str(args.output.resolve()), "packs": len(packs)}, ensure_ascii=False))
    expected = len(selection.get("packs", []))
    return 0 if len(packs) == expected else 1


if __name__ == "__main__":
    raise SystemExit(main())
