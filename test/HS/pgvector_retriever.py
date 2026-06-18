from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import psycopg2
from dotenv import load_dotenv
from openai import OpenAI
from psycopg2.extras import RealDictCursor

from rag_prototype.retriever import expand_query_tokens, tokenize


PROJECT_ROOT = Path(__file__).resolve().parents[2]
IMAGE_QUERY_TERMS = ("이미지", "사진", "그림", "유물", "유적", "자료", "찾아줘", "보여줘", "조회")
IMAGE_TITLE_IGNORE_TERMS = set(IMAGE_QUERY_TERMS) | {"시대", "대표", "관련", "설명"}
TITLE_EXPANSIONS = {
    "구석기": ["주먹도끼", "찍개", "석장리", "전곡리"],
    "신석기": ["빗살무늬", "토기", "암사동"],
    "청동기": ["고인돌", "비파형동검", "민무늬토기"],
    "팔만대장경": ["대장경", "재조대장경", "고려대장경", "해인사"],
    "대장경": ["팔만대장경", "재조대장경", "고려대장경", "해인사"],
}
OVERVIEW_TERMS = ("정리", "요약", "흐름", "개념")
PERSON_OVERVIEW_TERMS = ("업적", "정책", "정리", "요약", "개념", "설명", "누구", "뭐", "무엇", "알려")
KING_QUERY_ALIASES = {
    "세종대왕": {
        "entity_id": "joseon_sejong",
        "display_name": "조선 세종",
        "posthumous_name": "세종",
        "aliases": ("세종대왕", "조선 세종", "세종"),
    },
    "조선 세종": {
        "entity_id": "joseon_sejong",
        "display_name": "조선 세종",
        "posthumous_name": "세종",
        "aliases": ("세종대왕", "조선 세종", "세종"),
    },
    "세종": {
        "entity_id": "joseon_sejong",
        "display_name": "조선 세종",
        "posthumous_name": "세종",
        "aliases": ("세종대왕", "조선 세종", "세종"),
    },
}
KING_TOPIC_EXPANSIONS = {
    "joseon_sejong": (
        "훈민정음",
        "집현전",
        "농사직설",
        "칠정산",
        "측우기",
        "장영실",
        "4군",
        "6진",
        "대마도",
        "공법",
        "의정부서사제",
        "세종실록지리지",
    ),
}
LOW_PRIORITY_PERSON_TOPICS = ("풍수", "왕릉", "영릉", "논의")
MEDIUM_PRIORITY_PERSON_TOPICS = ("지리지", "실록")
EXCLUDED_PERSON_OVERVIEW_TITLES = (
    "국문연구",
    "중앙서리",
    "도성건설",
    "정재무",
    "근대화",
    "고려사절요",
)


def wants_joseon_early_politics(question: str) -> bool:
    return "조선" in question and ("전기" in question or "초기" in question) and "정치" in question


def detect_king_query(question: str) -> dict[str, Any] | None:
    compact_question = normalize_compact(question)
    for alias, entity in KING_QUERY_ALIASES.items():
        if normalize_compact(alias) in compact_question:
            return entity
    return None


def is_person_overview_query(question: str, king_entity: dict[str, Any] | None) -> bool:
    if not king_entity:
        return False
    compact_question = normalize_compact(question)
    aliases = [normalize_compact(alias) for alias in king_entity["aliases"]]
    alias_only = compact_question in aliases
    return alias_only or any(term in question for term in PERSON_OVERVIEW_TERMS)


def build_keyword_question(question: str, king_entity: dict[str, Any] | None) -> str:
    if not king_entity:
        return question
    terms = [question, king_entity["display_name"], *king_entity["aliases"]]
    terms.extend(KING_TOPIC_EXPANSIONS.get(king_entity["entity_id"], ()))
    return " ".join(dict.fromkeys(term for term in terms if term))


@dataclass(frozen=True)
class PgSearchResult:
    chunk_id: str
    document_id: str
    source_type: str
    source_name: str
    title: str
    chunk_text: str
    metadata: dict[str, Any]
    vector_score: float
    keyword_score: float
    score: float


def load_env() -> None:
    load_dotenv(PROJECT_ROOT / ".env")


def connect_db():
    return psycopg2.connect(
        host=os.getenv("POSTGRES_HOST", "localhost"),
        port=int(os.getenv("POSTGRES_PORT", "5432")),
        dbname=os.getenv("POSTGRES_DB", "history_rag"),
        user=os.getenv("POSTGRES_USER", "himate"),
        password=os.getenv("POSTGRES_PASSWORD", "himate1234"),
    )


def vector_literal(values: list[float]) -> str:
    return "[" + ",".join(f"{value:.8f}" for value in values) + "]"


def embed_query(question: str, model: str, dimensions: int | None) -> list[float]:
    client = OpenAI()
    kwargs: dict[str, Any] = {"model": model, "input": question}
    if dimensions:
        kwargs["dimensions"] = dimensions
    response = client.embeddings.create(**kwargs)
    return response.data[0].embedding


def is_image_query(question: str) -> bool:
    return any(term in question for term in IMAGE_QUERY_TERMS)


def normalize_compact(text: str) -> str:
    return re.sub(r"\s+", "", (text or "").lower())


def image_title_tokens(question: str) -> list[str]:
    tokens = expand_query_tokens(question, tokenize(question))
    result: list[str] = []
    for token in tokens:
        if token in IMAGE_TITLE_IGNORE_TERMS or len(token) < 2:
            continue
        candidates = [token, *TITLE_EXPANSIONS.get(token, [])]
        for candidate in candidates:
            normalized = normalize_compact(candidate)
            if normalized and normalized not in result:
                result.append(normalized)
    return result


def compact_text(text: str, max_length: int = 260) -> str:
    value = re.sub(r"\s+", " ", text or "").strip()
    if len(value) <= max_length:
        return value
    return value[: max_length - 1].rstrip() + "..."


def rerank_person_overview_rows(rows: list[dict[str, Any]], king_entity: dict[str, Any]) -> list[dict[str, Any]]:
    topic_terms = KING_TOPIC_EXPANSIONS.get(king_entity["entity_id"], ())
    aliases = king_entity["aliases"]

    def adjusted_score(row: dict[str, Any]) -> float:
        title = row.get("title") or ""
        chunk_text = row.get("chunk_text") or ""
        combined = f"{title} {chunk_text}"
        score = float(row.get("score") or 0.0) * 0.1
        topic_hit = any(term in combined for term in topic_terms)
        alias_hit = any(alias in combined for alias in aliases)
        title_topic_hit = any(term in title for term in topic_terms)

        if title_topic_hit:
            score += 8.0
        elif topic_hit and alias_hit:
            score += 6.0
        elif topic_hit:
            score += 3.0
        elif alias_hit:
            score += 1.5
        else:
            score -= 8.0
        if alias_hit:
            score += 0.4
        if "훈민정음" in combined or "한글" in combined:
            score += 1.2
        if "측우기" in combined:
            score += 0.8
        if "창제자" in title:
            score += 1.0
        if any(term in title for term in LOW_PRIORITY_PERSON_TOPICS):
            score -= 2.0
        if any(term in title for term in MEDIUM_PRIORITY_PERSON_TOPICS):
            score -= 0.8
        if any(term in title for term in EXCLUDED_PERSON_OVERVIEW_TITLES):
            score -= 6.0
        return score

    deduped: dict[str, dict[str, Any]] = {}
    for row in sorted(rows, key=adjusted_score, reverse=True):
        document_id = row.get("document_id")
        if document_id in deduped:
            continue
        row["score"] = adjusted_score(row)
        deduped[document_id] = row
    return list(deduped.values())


class PgVectorHybridRetriever:
    def __init__(
        self,
        model: str | None = None,
        dimensions: int | None = None,
        candidate_pool: int = 80,
    ) -> None:
        load_env()
        self.model = model or os.getenv("EMBEDDING_MODEL", "text-embedding-3-small")
        self.dimensions = dimensions if dimensions is not None else int(os.getenv("EMBEDDING_DIMENSIONS", "1536"))
        self.candidate_pool = candidate_pool

    def search(self, question: str, top_k: int = 5) -> list[PgSearchResult]:
        question = question.strip()
        if not question:
            return []

        embedding = vector_literal(embed_query(question, self.model, self.dimensions))
        image_query = is_image_query(question)
        king_entity = detect_king_query(question)
        person_overview_query = is_person_overview_query(question, king_entity)
        keyword_question = build_keyword_question(question, king_entity if person_overview_query else None)
        overview_query = any(term in question for term in OVERVIEW_TERMS)
        joseon_early_politics = wants_joseon_early_politics(question)
        title_tokens = image_title_tokens(question) if image_query else []
        king_topic_terms = KING_TOPIC_EXPANSIONS.get(king_entity["entity_id"], ()) if person_overview_query else ()
        final_limit = max(top_k * 5, top_k) if person_overview_query else top_k

        where_parts = ["embedding IS NOT NULL"]
        params: list[Any] = []
        if image_query:
            where_parts.append("source_type = 'image_material'")
            if title_tokens:
                title_clauses = []
                for token in title_tokens:
                    title_clauses.append("regexp_replace(lower(title), '\\s+', '', 'g') LIKE %s")
                    params.append(f"%{token}%")
                where_parts.append("(" + " OR ".join(title_clauses) + ")")
        elif person_overview_query and king_entity:
            king_clauses = [
                "metadata::text ILIKE %s",
                "metadata::text ILIKE %s",
                "title ILIKE %s",
                "chunk_text ILIKE %s",
            ]
            params.extend(
                [
                    f"%{king_entity['entity_id']}%",
                    f"%{king_entity['display_name']}%",
                    f"%{king_entity['posthumous_name']}%",
                    f"%{king_entity['posthumous_name']}%",
                ]
            )
            where_parts.append("(" + " OR ".join(king_clauses) + ")")
        elif joseon_early_politics:
            where_parts.append(
                """
                (
                    metadata::text ILIKE '%%조선 초기의 정치구조%%'
                    OR metadata::text ILIKE '%%정치%%'
                    OR title ILIKE '%%정치%%'
                    OR title ILIKE '%%통치%%'
                    OR chunk_text ILIKE '%%의정부%%'
                    OR chunk_text ILIKE '%%육조%%'
                    OR chunk_text ILIKE '%%관료%%'
                    OR chunk_text ILIKE '%%집현전%%'
                    OR chunk_text ILIKE '%%경국대전%%'
                )
                """
            )
        where_sql = " AND ".join(where_parts)
        king_topic_sql = "FALSE"
        king_topic_params: list[Any] = []
        if king_topic_terms:
            king_topic_sql = " OR ".join("(title ILIKE %s OR chunk_text ILIKE %s)" for _ in king_topic_terms)
            for term in king_topic_terms:
                king_topic_params.extend([f"%{term}%", f"%{term}%"])

        sql = f"""
        WITH base AS (
            SELECT
                id,
                chunk_id,
                document_id,
                source_type,
                source_name,
                title,
                chunk_text,
                metadata,
                1 - (embedding <=> %s::vector) AS vector_score,
                (
                    similarity(title, %s) * 3.0
                    + similarity(chunk_text, %s)
                    + similarity(metadata::text, %s) * 1.2
                    + CASE WHEN title ILIKE %s THEN 2.0 ELSE 0.0 END
                    + CASE WHEN chunk_text ILIKE %s THEN 0.8 ELSE 0.0 END
                    + CASE WHEN metadata::text ILIKE %s THEN 1.2 ELSE 0.0 END
                ) AS keyword_score
            FROM rag.document_chunks
            WHERE {where_sql}
        ),
        vector_candidates AS (
            SELECT *
            FROM base
            ORDER BY vector_score DESC
            LIMIT %s
        ),
        keyword_candidates AS (
            SELECT *
            FROM base
            ORDER BY keyword_score DESC
            LIMIT %s
        ),
        candidates AS (
            SELECT DISTINCT ON (chunk_id) *
            FROM (
                SELECT * FROM vector_candidates
                UNION ALL
                SELECT * FROM keyword_candidates
            ) merged
            ORDER BY chunk_id, vector_score DESC, keyword_score DESC
        )
        SELECT
            chunk_id,
            document_id,
            source_type,
            source_name,
            title,
            chunk_text,
            metadata,
            vector_score,
            keyword_score,
            (
                vector_score * 0.65
                + keyword_score * 0.35
                + CASE
                    WHEN %s AND source_type = 'image_material' THEN 1.2
                    WHEN %s AND source_type <> 'image_material' THEN -1.0
                    WHEN %s AND source_type = 'historical_overview' THEN 0.5
                    WHEN %s AND source_type = 'historical_source' THEN -0.15
                    ELSE 0.0
                  END
                + CASE
                    WHEN %s AND metadata::text ILIKE '%%조선 초기의 정치구조%%' THEN 2.0
                    WHEN %s AND metadata::text ILIKE '%%정치%%' THEN 0.8
                    ELSE 0.0
                  END
                + CASE
                    WHEN %s AND metadata::text ILIKE %s THEN 2.4
                    WHEN %s AND metadata::text ILIKE %s THEN 1.4
                    WHEN %s AND title ILIKE %s THEN 1.2
                    WHEN %s AND chunk_text ILIKE %s THEN 0.6
                    ELSE 0.0
                  END
                + CASE
                    WHEN %s AND ({king_topic_sql}) THEN 1.6
                    ELSE 0.0
                  END
                + CASE
                    WHEN %s AND source_type = 'image_material' THEN -1.0
                    WHEN %s AND title ILIKE %s THEN -0.8
                    WHEN %s AND title ILIKE %s THEN -0.7
                    WHEN %s AND title ILIKE %s THEN -0.4
                    WHEN %s AND title ILIKE %s THEN -0.5
                    WHEN %s AND title ILIKE %s THEN -0.4
                    WHEN %s AND NOT ({king_topic_sql}) THEN -2.0
                    ELSE 0.0
                  END
            ) AS score
        FROM candidates
        ORDER BY score DESC
        LIMIT %s
        """

        query_params: list[Any] = [
            embedding,
            keyword_question,
            keyword_question,
            keyword_question,
            f"%{question}%",
            f"%{question}%",
            f"%{question}%",
            *params,
            self.candidate_pool,
            self.candidate_pool,
            image_query,
            image_query,
            overview_query,
            overview_query,
            joseon_early_politics,
            joseon_early_politics,
            person_overview_query,
            f"%{king_entity['entity_id']}%" if king_entity else "",
            person_overview_query,
            f"%{king_entity['display_name']}%" if king_entity else "",
            person_overview_query,
            f"%{king_entity['posthumous_name']}%" if king_entity else "",
            person_overview_query,
            f"%{king_entity['posthumous_name']}%" if king_entity else "",
            person_overview_query,
            *king_topic_params,
            person_overview_query,
            person_overview_query,
            f"%{LOW_PRIORITY_PERSON_TOPICS[0]}%",
            person_overview_query,
            f"%{LOW_PRIORITY_PERSON_TOPICS[1]}%",
            person_overview_query,
            f"%{LOW_PRIORITY_PERSON_TOPICS[2]}%",
            person_overview_query,
            f"%{MEDIUM_PRIORITY_PERSON_TOPICS[0]}%",
            person_overview_query,
            f"%{MEDIUM_PRIORITY_PERSON_TOPICS[1]}%",
            person_overview_query,
            *king_topic_params,
            final_limit,
        ]

        with connect_db() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(sql, query_params)
                rows = cur.fetchall()

        if person_overview_query and king_entity:
            rows = rerank_person_overview_rows(list(rows), king_entity)[:top_k]
        else:
            rows = rows[:top_k]

        return [
            PgSearchResult(
                chunk_id=row["chunk_id"],
                document_id=row["document_id"],
                source_type=row["source_type"],
                source_name=row["source_name"],
                title=row["title"],
                chunk_text=row["chunk_text"],
                metadata=row["metadata"] or {},
                vector_score=float(row["vector_score"] or 0.0),
                keyword_score=float(row["keyword_score"] or 0.0),
                score=float(row["score"] or 0.0),
            )
            for row in rows
        ]


def result_to_payload(result: PgSearchResult) -> dict[str, Any]:
    return {
        "chunk_id": result.chunk_id,
        "document_id": result.document_id,
        "source_type": result.source_type,
        "source_name": result.source_name,
        "title": result.title,
        "score": round(result.score, 4),
        "vector_score": round(result.vector_score, 4),
        "keyword_score": round(result.keyword_score, 4),
        "source_url": result.metadata.get("source_url"),
        "thumbnail_url": result.metadata.get("thumbnail_url"),
        "original_image_url": result.metadata.get("original_image_url"),
        "snippet": compact_text(result.chunk_text),
    }
