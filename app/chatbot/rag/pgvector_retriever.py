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

from .rag_prototype.retriever import expand_query_tokens, tokenize


PROJECT_ROOT = Path(__file__).resolve().parents[3]
TIMELINE_TERMS = ("연표", "연대", "순서", "흐름", "언제", "시기", "전개")
TIMELINE_ERAS = ("고대", "고려", "조선", "근대", "현대")
TIMELINE_FIELDS = ("인물", "사건", "조직·단체", "조직", "단체", "유물·유적", "유물", "유적")
IMAGE_QUERY_TERMS = ("이미지", "사진", "그림", "유물", "유적", "자료", "찾아줘", "보여줘", "조회")
IMAGE_TITLE_IGNORE_TERMS = set(IMAGE_QUERY_TERMS) | {"시대", "대표", "관련", "설명"}
OVERVIEW_TERMS = ("정리", "요약", "흐름", "개념", "설명", "알려", "누구", "업적", "정책")
OVERVIEW_IGNORE_TERMS = {
    "정리",
    "요약",
    "흐름",
    "개념",
    "설명",
    "설명해줘",
    "알려",
    "알려줘",
    "누구",
    "뭐",
    "무엇",
    "업적",
    "정책",
    "대해",
    "대한",
}
GENERIC_OVERVIEW_CONTEXT_TERMS = (
    "개요",
    "핵심 내용",
    "특징",
    "배경",
    "의의",
    "업적",
    "활동",
    "시대",
    "관련 내용",
    "한능검",
)
ACHIEVEMENT_CONTEXT_TERMS = (
    "창제",
    "설치",
    "정비",
    "편찬",
    "제작",
    "반포",
    "개혁",
    "발명",
    "시행",
)
ACHIEVEMENT_QUERY_TERMS = ("업적", "정책", "활동")
HONORIFIC_SUFFIXES = ("대왕",)


def overview_focus_terms(question: str) -> tuple[str, ...]:
    tokens = tokenize(question)
    terms: list[str] = []
    for token in tokens:
        normalized = token.strip()
        if len(normalized) < 2 or normalized in OVERVIEW_IGNORE_TERMS:
            continue
        if normalized.endswith("에") and len(normalized) > 2:
            normalized = normalized[:-1]
        if normalized.endswith("은") or normalized.endswith("는"):
            normalized = normalized[:-1]
        if normalized in OVERVIEW_IGNORE_TERMS:
            continue
        candidates = [normalized]
        for suffix in HONORIFIC_SUFFIXES:
            if len(normalized) > len(suffix) + 1 and normalized.endswith(suffix):
                candidates.append(normalized[: -len(suffix)])
        for candidate in candidates:
            if candidate and candidate not in terms:
                terms.append(candidate)
    return tuple(terms[:4])


def is_generic_overview_query(question: str, focus_terms: tuple[str, ...]) -> bool:
    if not focus_terms:
        return False
    return any(term in question for term in OVERVIEW_TERMS)


def build_keyword_question(
    question: str,
    focus_terms: tuple[str, ...] = (),
    extra_terms: tuple[str, ...] = GENERIC_OVERVIEW_CONTEXT_TERMS,
) -> str:
    terms = [question, *focus_terms]
    terms.extend(extra_terms)
    if any(term in question for term in ACHIEVEMENT_QUERY_TERMS):
        terms.extend(ACHIEVEMENT_CONTEXT_TERMS)
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
        normalized = normalize_compact(token)
        if normalized and normalized not in result:
            result.append(normalized)
    return result


def compact_text(text: str, max_length: int = 260) -> str:
    value = re.sub(r"\s+", " ", text or "").strip()
    if len(value) <= max_length:
        return value
    return value[: max_length - 1].rstrip() + "..."


def normalize_timeline_field(field: str) -> str:
    if field in {"조직", "단체"}:
        return "조직·단체"
    if field in {"유물", "유적"}:
        return "유물·유적"
    return field


def timeline_filters(question: str) -> tuple[str, str]:
    era = next((value for value in TIMELINE_ERAS if value in question), "")
    field = next((value for value in TIMELINE_FIELDS if value in question), "")
    return era, normalize_timeline_field(field)


def should_search_timeline(question: str) -> bool:
    era, field = timeline_filters(question)
    return bool((era and field) or any(term in question for term in TIMELINE_TERMS))


def search_timeline_sources(question: str, limit: int = 12) -> list[dict[str, Any]]:
    if not should_search_timeline(question):
        return []

    era, field = timeline_filters(question)
    if not era and not field:
        return []

    where_parts = []
    params: list[Any] = []
    if era:
        where_parts.append("(era = %s OR age = %s)")
        params.extend([era, era])
    if field:
        where_parts.append("field = %s")
        params.append(field)
    where_sql = " AND ".join(where_parts) if where_parts else "TRUE"

    try:
        with connect_db() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(
                    f"""
                    SELECT year, title, period, era, field
                    FROM rag.history_timeline
                    WHERE {where_sql}
                    ORDER BY year, title
                    LIMIT %s
                    """,
                    [*params, limit],
                )
                rows = cur.fetchall()
    except psycopg2.Error:
        return []

    if not rows:
        return []

    title_bits = [value for value in (era, field, "연표") if value]
    snippet = " / ".join(f"{row['year']}년 {row['title']}" for row in rows)
    return [
        {
            "chunk_id": "history_timeline",
            "document_id": "history_timeline",
            "source_type": "history_timeline",
            "source_name": "한국사 연대기 연표",
            "title": " ".join(title_bits),
            "score": 1.0,
            "vector_score": 0.0,
            "keyword_score": 1.0,
            "source_url": None,
            "thumbnail_url": None,
            "original_image_url": None,
            "snippet": compact_text(snippet, 900),
        }
    ]


def diversify_rows(rows: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    seen_titles: set[str] = set()
    for row in rows:
        title = str(row.get("title") or "")
        if title in seen_titles:
            continue
        selected.append(row)
        seen_titles.add(title)
        if len(selected) >= limit:
            return selected
    for row in rows:
        if row not in selected:
            selected.append(row)
            if len(selected) >= limit:
                break
    return selected


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

        image_query = is_image_query(question)
        focus_terms = overview_focus_terms(question)
        generic_overview_query = is_generic_overview_query(question, focus_terms)
        keyword_question = build_keyword_question(
            question,
            focus_terms if generic_overview_query else (),
            GENERIC_OVERVIEW_CONTEXT_TERMS if generic_overview_query else (),
        )
        embedding_question = keyword_question if generic_overview_query else question
        embedding = vector_literal(embed_query(embedding_question, self.model, self.dimensions))
        overview_query = any(term in question for term in OVERVIEW_TERMS)
        title_tokens = image_title_tokens(question) if image_query else []
        final_limit = max(top_k * 5, top_k) if generic_overview_query else top_k

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
        elif generic_overview_query and focus_terms:
            focus_clauses = []
            for term in focus_terms:
                focus_clauses.extend(
                    [
                        "title ILIKE %s",
                        "chunk_text ILIKE %s",
                        "metadata::text ILIKE %s",
                    ]
                )
                params.extend([f"%{term}%", f"%{term}%", f"%{term}%"])
            where_parts.append("(" + " OR ".join(focus_clauses) + ")")
        where_sql = " AND ".join(where_parts)

        focus_match_sql = "FALSE"
        focus_match_params: list[Any] = []
        if generic_overview_query and focus_terms:
            focus_match_sql = " OR ".join(
                "(title ILIKE %s OR chunk_text ILIKE %s OR metadata::text ILIKE %s)" for _ in focus_terms
            )
            for term in focus_terms:
                like_term = f"%{term}%"
                focus_match_params.extend([like_term, like_term, like_term])

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
                    WHEN %s AND ({focus_match_sql}) THEN 1.8
                    ELSE 0.0
                  END
                + CASE
                    WHEN %s AND source_type = 'image_material' THEN -1.0
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
            generic_overview_query,
            *focus_match_params,
            generic_overview_query,
            final_limit,
        ]

        with connect_db() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(sql, query_params)
                rows = cur.fetchall()

        rows = diversify_rows(rows, top_k) if generic_overview_query else rows[:top_k]

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
