"""Fact Graph 후보와 owner-scoped RAG 근거로 closed pack을 만든다."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from collections import defaultdict
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from dotenv import load_dotenv
from neo4j import GraphDatabase

from ai.pack_generation.builder import validate_pack_bank
from ai.question_generation.core.contracts import V41_TOPIC_TYPES
from ai.question_generation.generation.material import chat_json
from ai.question_generation.retrieval.closed_pack_input import FRAME_FIELDS, MATERIAL_TARGET_SCOPE
from storage.fact_neo4j.load_fact_graph import load_connection_config
from storage.postgresql.connection import connect_db


PROJECT_ROOT = Path(__file__).resolve().parents[2]
REVIEW_CONTRACT_VERSION = "graph_pack_review_v2"
GRAPH_QUERY = """
MATCH (anchor)
WHERE coalesce(anchor.entity_id, anchor.topic_id, anchor.era_id) = $anchor_node_id
  AND NOT 'ProvisionalEntity' IN labels(anchor)
  AND (anchor:Topic OR (anchor:CanonicalEntity AND anchor.retrieval_eligible = true))
MATCH path = (anchor)-[*1..3]-(owner:CanonicalEntity)
WHERE length(path) = $candidate_hops
  AND owner.retrieval_eligible = true
  AND owner.entity_type = $owner_type
  AND coalesce(owner.entity_id, '') <> coalesce(anchor.entity_id, '')
  AND all(
    node IN nodes(path)
    WHERE (node:CanonicalEntity AND node.retrieval_eligible = true) OR node:Topic
  )
  AND all(
    relation IN relationships(path)
    WHERE type(relation) = 'HAS_TOPIC'
       OR (relation.retrieval_eligible = true AND relation.semantic_relation_id IS NOT NULL)
  )
  AND EXISTS {
    MATCH (owner)-[:IN_ERA]->(:Era {era_id: $era_id})
  }
  AND EXISTS {
    MATCH (owner)-[:HAS_TOPIC]->(:Topic {topic_id: $topic_id})
  }
MATCH (source:SourceRecord {source: 'AKS'})-[:RESOLVES_TO]->(owner)
RETURN DISTINCT owner.entity_id AS owner_entity_id,
       owner.display_name AS owner_label,
       owner.entity_type AS graph_owner_type,
       owner.graph_release_id AS graph_release_id,
       source.source_metadata_json AS source_metadata_json,
       $candidate_hops AS candidate_distance
ORDER BY owner_label, owner_entity_id
"""


def compact(value: Any) -> str:
    return " ".join(str(value or "").split())


def is_two_sentences(value: Any) -> bool:
    sentences = re.split(r"(?<=[.!?])\s+", compact(value))
    return len(sentences) == 2 and all(sentence.endswith((".", "!", "?")) for sentence in sentences)


def owner_type_base(value: Any) -> str:
    return compact(value).split("/", 1)[0].split(",", 1)[0]


def candidate_hops_for_difficulty(difficulty: int) -> int:
    if difficulty not in {1, 2, 3}:
        raise ValueError("difficulty must be 1, 2, or 3")
    return 4 - difficulty


def validate_spec(spec: dict[str, Any]) -> None:
    required = (
        "anchor_node_id",
        "candidate_hops",
        "topic_id",
        "era_id",
        "era",
        "era_criteria",
        "owner_type",
        "rag_owner_type",
        "relation_axis_id",
        "topic_type",
        "difficulty",
        "question_frames",
    )
    missing = [field for field in required if not spec.get(field)]
    frames = spec.get("question_frames")
    if missing:
        raise ValueError(f"graph pack spec lacks {missing}")
    difficulty = int(spec["difficulty"])
    hops = int(spec["candidate_hops"])
    expected_hops = candidate_hops_for_difficulty(difficulty)
    if hops != expected_hops:
        raise ValueError("candidate_hops must be 3, 2, or 1 for difficulty 1, 2, or 3")
    if spec["topic_type"] not in V41_TOPIC_TYPES:
        raise ValueError("graph pack spec has an invalid topic_type")
    if not isinstance(frames, list) or len(frames) < 2:
        raise ValueError("graph pack spec requires at least two frames")
    for frame in frames:
        frame_missing = [field for field in FRAME_FIELDS if not compact(frame.get(field))]
        if frame_missing or frame.get("answer_owner_scope") != MATERIAL_TARGET_SCOPE:
            raise ValueError(f"invalid graph pack frame: {frame_missing}")


def article_id_from_url(value: str) -> str:
    article_id = urlparse(value).path.rstrip("/").rsplit("/", 1)[-1]
    return article_id if re.fullmatch(r"E\d{7}", article_id) else ""


def read_graph_candidates(spec: dict[str, Any]) -> list[dict[str, Any]]:
    config = load_connection_config(PROJECT_ROOT)
    with GraphDatabase.driver(config["uri"], auth=(config["user"], config["password"])) as driver:
        records = driver.execute_query(
            GRAPH_QUERY,
            anchor_node_id=spec["anchor_node_id"],
            candidate_hops=int(spec["candidate_hops"]),
            owner_type=spec["owner_type"],
            era_id=spec["era_id"],
            topic_id=spec["topic_id"],
            routing_="r",
        ).records
    candidates = []
    for record in records:
        row = dict(record)
        try:
            metadata = json.loads(row.pop("source_metadata_json") or "{}")
        except json.JSONDecodeError:
            continue
        source_url = compact(metadata.get("source_url"))
        article_id = article_id_from_url(source_url)
        if article_id:
            candidates.append({**row, "article_id": article_id, "source_url": source_url})
    return candidates


def add_rag_evidence(
    graph_rows: list[dict[str, Any]],
    *,
    rag_owner_type: str,
    max_evidence_candidates: int,
) -> list[dict[str, Any]]:
    """Graph owner와 직접 연결된 민백 문서에서 근거 후보를 읽는다."""
    article_ids = sorted({row["article_id"] for row in graph_rows})
    if not article_ids:
        return []

    conn = connect_db()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT article_id, title, source_url, contents_type
                FROM rag.encykorea_articles
                WHERE article_id = ANY(%s)
                """,
                (article_ids,),
            )
            articles = {
                row[0]: {
                    "title": row[1],
                    "source_url": row[2],
                    "owner_type": row[3],
                }
                for row in cursor.fetchall()
            }
            cursor.execute(
                """
                SELECT article_id, chunk_id, chunk_text, source_url, section_path
                FROM rag.encykorea_chunks
                WHERE article_id = ANY(%s)
                ORDER BY article_id, chunk_index
                """,
                (article_ids,),
            )
            chunks: dict[str, list[dict[str, Any]]] = defaultdict(list)
            for article_id, chunk_id, text, source_url, section_path in cursor.fetchall():
                if compact(text):
                    chunks[article_id].append(
                        {
                            "snippet": text,
                            "chunk_id": chunk_id,
                            "article_id": article_id,
                            "exact_text": text,
                            "source_url": source_url,
                            "section_path": section_path,
                        }
                    )
    finally:
        conn.close()

    rows_by_owner: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    for row in graph_rows:
        rows_by_owner[row["owner_entity_id"]][row["article_id"]] = row

    candidates = []
    for rows in rows_by_owner.values():
        matched = []
        for article_id, row in rows.items():
            article = articles.get(article_id)
            if (
                article
                and article["title"] == row["owner_label"]
                and article["source_url"] == row["source_url"]
                and owner_type_base(article["owner_type"]) == rag_owner_type
            ):
                matched.append((row, article))
        if len(matched) != 1:
            continue
        row, article = matched[0]
        evidence = chunks[row["article_id"]]
        unique_evidence = []
        seen_texts = set()
        for item in evidence:
            text = compact(item["exact_text"])
            if text and text not in seen_texts:
                seen_texts.add(text)
                unique_evidence.append(item)
            if len(unique_evidence) == max_evidence_candidates:
                break
        if len(unique_evidence) >= 2:
            candidates.append(
                {
                    **row,
                    "owner_type": article["owner_type"],
                    "evidence_candidates": unique_evidence,
                }
            )
    return candidates


def select_review_candidates(
    spec: dict[str, Any], candidates: list[dict[str, Any]], limit: int
) -> list[dict[str, Any]]:
    """큰 Graph 후보군을 재현 가능한 비용 상한으로 줄인다."""
    anchor = str(spec["anchor_node_id"])
    # ponytail: deterministic cap controls review cost; raise --candidate-limit if nine safe owners cannot be selected.
    return sorted(
        candidates,
        key=lambda row: hashlib.sha256(f"{anchor}:{row['owner_entity_id']}".encode()).digest(),
    )[:limit]


def review_candidates(
    candidates: list[dict[str, Any]],
    *,
    relation_axis_id: str,
    topic_type: str,
    era: str,
    era_criteria: str,
    review_feedback: str,
    model: str,
    base_url: str,
    api_key: str,
    timeout: int,
    max_retries: int,
) -> dict[str, Any]:
    """Graph 후보 전체를 한 번 호출해 RAG 근거 두 종류를 검수한다."""
    payload = [
        {
            "owner_id": candidate["article_id"],
            "owner_label": candidate["owner_label"],
            "candidate_distance": candidate["candidate_distance"],
            "evidence_candidates": [
                {"chunk_id": row["chunk_id"], "text": row["exact_text"]}
                for row in candidate["evidence_candidates"]
            ],
        }
        for candidate in candidates
    ]
    return chat_json(
        base_url=base_url,
        api_key=api_key,
        model=model,
        temperature=0.0,
        timeout=timeout,
        max_retries=max_retries,
        messages=[
            {
                "role": "system",
                "content": (
                    "너는 한국사 문제은행 closed-pack의 보수적인 근거 검수자다. "
                    "제공된 민백 RAG 원문 밖의 지식을 사용하지 말고 JSON 객체만 출력한다."
                ),
            },
            {
                "role": "user",
                "content": f"""
문항 관계축은 {relation_axis_id}, topic_type은 {topic_type}, 시대는 {era}이다.
시대 판정 기준은 다음과 같다: {era_criteria}
이전 검수 피드백: {review_feedback or "없음"}
GraphDB가 검색한 아래 owner 후보 중 정확히 9개를 선택하라.

각 승인 member 규칙:
- Graph 경로나 관계를 사실 근거로 사용하지 않는다.
- fact_basis와 material_clue_basis는 해당 owner의 evidence_candidates만 직접 지지해야 한다.
- fact_basis는 관계축에 맞는 사실을 정확히 2문장으로 쓴다.
- material_clue_basis는 같은 owner를 식별하는 별도 사실을 정확히 2문장으로 쓴다.
- 두 basis가 같은 핵심 사실을 반복하거나 정답을 노출하면 승인하지 않는다.
- fact와 material은 서로 다른 chunk_id를 우선 사용한다. 한 chunk에 서로 다른 두 사실이 함께 있을 때만 같은 chunk_id를 허용하며, 두 basis는 같은 핵심 사실을 반복해서는 안 된다.
- 같은 chunk_id를 하나라도 공유하면서 두 basis가 별도 사실이면 material_fact_semantically_distinct를 true로, 공유하지 않으면 false로 반환한다.
- 선택한 사실 자체가 문항 시대에 속해야 한다.
- 다른 인물·사건·제도의 사실을 owner에게 잘못 귀속하지 않는다.
- OCR 잡음, 목차, 표, 각주 조각은 사용하지 않는다.
- Graph, RAG, 근거, 후보, 제공, 관계 같은 작업 과정의 메타 표현을 쓰지 않는다.
- 안전한 owner 9개를 고를 수 없으면 members를 빈 배열로 반환한다.

후보:
{json.dumps(payload, ensure_ascii=False)}

출력:
{{
  "members": [
    {{
      "owner_id": "E0000000",
      "fact_basis": "정확히 2문장",
      "fact_evidence_chunk_ids": ["..."],
      "material_clue_basis": "정확히 2문장",
      "material_evidence_chunk_ids": ["..."],
      "material_fact_semantically_distinct": false,
      "approved": true
    }}
  ]
}}
""".strip(),
            },
        ],
    )


def review_cache_key(spec: dict[str, Any], candidates: list[dict[str, Any]], model: str) -> str:
    payload = {
        "spec": spec,
        "model": model,
        "review_contract": REVIEW_CONTRACT_VERSION,
        "candidates": [
            {
                "owner_entity_id": row["owner_entity_id"],
                "article_id": row["article_id"],
                "evidence_ids": [chunk["chunk_id"] for chunk in row["evidence_candidates"]],
            }
            for row in candidates
        ],
    }
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def build_pack(
    spec: dict[str, Any],
    candidates: list[dict[str, Any]],
    review: dict[str, Any],
    model: str,
) -> dict[str, Any]:
    by_owner = {row["article_id"]: row for row in candidates}
    selected = review.get("members")
    if not isinstance(selected, list) or len(selected) != 9:
        raise ValueError("LLM review did not approve exactly nine members")

    members = []
    owner_ids: set[str] = set()
    for result in selected:
        owner_id = str(result.get("owner_id") or "")
        candidate = by_owner.get(owner_id)
        fact_ids = list(result.get("fact_evidence_chunk_ids") or [])
        material_ids = list(result.get("material_evidence_chunk_ids") or [])
        available = {row["chunk_id"]: row for row in candidate["evidence_candidates"]} if candidate else {}
        evidence_disjoint = set(fact_ids).isdisjoint(material_ids)
        if (
            not candidate
            or result.get("approved") is not True
            or owner_id in owner_ids
            or not is_two_sentences(result.get("fact_basis"))
            or not is_two_sentences(result.get("material_clue_basis"))
            or compact(result["fact_basis"]) == compact(result["material_clue_basis"])
            or not fact_ids
            or not material_ids
            or len(set(fact_ids)) != len(fact_ids)
            or len(set(material_ids)) != len(material_ids)
            or any(chunk_id not in available for chunk_id in [*fact_ids, *material_ids])
            or (not evidence_disjoint and result.get("material_fact_semantically_distinct") is not True)
        ):
            raise ValueError("LLM review returned an invalid or unsafe member")
        owner_ids.add(owner_id)
        fact_basis = compact(result["fact_basis"])
        fact_signature = "\x1f".join((owner_id, *sorted(fact_ids), fact_basis))
        choice_fact_id = f"rag_fact:{hashlib.sha256(fact_signature.encode()).hexdigest()[:20]}"
        members.append(
            {
                "choice_fact_id": choice_fact_id,
                "owner_id": owner_id,
                "owner_label": candidate["owner_label"],
                "owner_type": candidate["owner_type"],
                "fact_basis": fact_basis,
                "fact_evidence_chunks": [available[chunk_id] for chunk_id in fact_ids],
                "material_clue_basis": compact(result["material_clue_basis"]),
                "material_evidence_chunks": [available[chunk_id] for chunk_id in material_ids],
                "material_evidence_disjoint": evidence_disjoint,
                "material_fact_semantically_distinct": not evidence_disjoint,
                "fact_fingerprint": choice_fact_id,
                "source_relation_axis_id": spec["relation_axis_id"],
                "graph_owner_id": candidate["owner_entity_id"],
                "graph_candidate_distance": candidate["candidate_distance"],
                "curation_source": f"fact_graph_candidates+owner_scoped_rag:{model}",
                **{
                    key: spec[key]
                    for key in ("service_era", "service_topic")
                    if spec.get(key)
                },
            }
        )

    fact_ids = [member["choice_fact_id"] for member in members]
    digest = hashlib.sha256("\x1f".join(sorted(fact_ids)).encode()).hexdigest()[:20]
    return {
        "family_id": f"graph_pack:{digest}",
        "status": "final_reviewed",
        "difficulty": int(spec["difficulty"]),
        "era": spec["era"],
        "relation_axis_id": spec["relation_axis_id"],
        "topic_type": spec["topic_type"],
        "answer_eligible_owner_ids": [member["owner_id"] for member in members],
        "question_frames": spec["question_frames"],
        "members": members,
        "graph_source": {
            "anchor_node_id": spec["anchor_node_id"],
            "candidate_hops": int(spec["candidate_hops"]),
            "topic_id": spec["topic_id"],
            "era_id": spec["era_id"],
            "owner_type": spec["owner_type"],
            "graph_release_id": candidates[0]["graph_release_id"],
            "review_model": model,
        },
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build closed packs from Graph candidates and owner RAG.")
    parser.add_argument("--spec", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--existing-bank", type=Path)
    parser.add_argument("--model", default=os.getenv("OPENAI_PACK_MODEL") or os.getenv("OPENAI_CHAT_MODEL"))
    parser.add_argument("--base-url", default=os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1"))
    parser.add_argument("--max-evidence-candidates", type=int, default=6)
    parser.add_argument("--candidate-limit", type=int, default=18)
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--max-retries", type=int, default=1)
    return parser.parse_args()


def main() -> int:
    load_dotenv(PROJECT_ROOT / ".env")
    args = parse_args()
    if not args.model:
        raise ValueError("--model or OPENAI_PACK_MODEL is required")
    if args.max_evidence_candidates < 2 or args.candidate_limit < 9:
        raise ValueError("evidence candidates must be at least 2 and candidate limit at least 9")
    api_key = os.getenv("OPENAI_API_KEY", "")

    data = json.loads(args.spec.read_text(encoding="utf-8-sig"))
    specs = data.get("packs")
    if not isinstance(specs, list) or not specs:
        raise ValueError("spec must contain a non-empty packs array")
    for spec in specs:
        validate_spec(spec)

    cache_path = args.output.with_name(f"{args.output.stem}.reviews.json")
    cache = json.loads(cache_path.read_text(encoding="utf-8")) if cache_path.exists() else {}
    packs = []
    for spec in specs:
        candidates = add_rag_evidence(
            read_graph_candidates(spec),
            rag_owner_type=spec["rag_owner_type"],
            max_evidence_candidates=args.max_evidence_candidates,
        )
        candidates = select_review_candidates(spec, candidates, args.candidate_limit)
        if len(candidates) < 9:
            raise ValueError(
                f"fewer than nine Graph/RAG owners for {spec['anchor_node_id']}: {len(candidates)}"
            )
        cache_key = review_cache_key(spec, candidates, args.model)
        review = cache.get(cache_key)
        if not isinstance(review, dict):
            if not api_key:
                raise ValueError("OPENAI_API_KEY is required for an uncached review")
            review = review_candidates(
                candidates,
                relation_axis_id=spec["relation_axis_id"],
                topic_type=spec["topic_type"],
                era=spec["era"],
                era_criteria=spec["era_criteria"],
                review_feedback=compact(spec.get("review_feedback")),
                model=args.model,
                base_url=args.base_url,
                api_key=api_key,
                timeout=args.timeout,
                max_retries=args.max_retries,
            )
            cache[cache_key] = review
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")
        packs.append(build_pack(spec, candidates, review, args.model))

    existing = []
    if args.existing_bank:
        existing_data = json.loads(args.existing_bank.read_text(encoding="utf-8-sig"))
        existing = list(existing_data.get("packs") or existing_data)
    validate_pack_bank([*existing, *packs])
    result = {"pack_count": len(packs), "packs": packs}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"output": str(args.output.resolve()), "packs": len(packs)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
