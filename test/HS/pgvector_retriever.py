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


def wants_joseon_early_politics(question: str) -> bool:
    return "조선" in question and ("전기" in question or "초기" in question) and "정치" in question


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
        overview_query = any(term in question for term in OVERVIEW_TERMS)
        joseon_early_politics = wants_joseon_early_politics(question)
        title_tokens = image_title_tokens(question) if image_query else []

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
            ) AS score
        FROM candidates
        ORDER BY score DESC
        LIMIT %s
        """

        query_params: list[Any] = [
            embedding,
            question,
            question,
            question,
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
            top_k,
        ]

        with connect_db() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(sql, query_params)
                rows = cur.fetchall()

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
