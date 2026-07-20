from __future__ import annotations

import os
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import psycopg2
from dotenv import load_dotenv
from openai import OpenAI
from psycopg2.extras import RealDictCursor

from .korean_tokenizer import mecab_search_tokens
from .query_terms import expand_query_tokens, tokenize
from .reranker import get_reranker, rerank_results
from .retrieval_rules import (
    HISTORY_STOPWORDS,
    OVERVIEW_IGNORE_TERMS,
    OVERVIEW_TERMS,
    build_bm25_query,
    is_generic_overview_query,
    normalize_query_spacing,
    overview_focus_terms,
)


PROJECT_ROOT = Path(__file__).resolve().parents[3]
TIMELINE_TERMS = ("연표", "연대", "순서", "흐름", "언제", "시기", "전개")
TIMELINE_ERAS = ("고대", "고려", "조선", "근대", "현대")
TIMELINE_FIELDS = ("인물", "사건", "조직·단체", "조직", "단체", "유물·유적", "유물", "유적")
IMAGE_QUERY_TERMS = ("이미지", "사진", "그림", "유물", "유적", "자료", "찾아줘", "보여줘", "조회")
IMAGE_TITLE_IGNORE_TERMS = set(IMAGE_QUERY_TERMS) | {"시대", "대표", "관련", "설명"}
COMPARISON_JOIN_TERMS = ("와", "과", "이랑", "하고", "및")


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


@lru_cache(maxsize=512)
def embed_query(question: str, model: str, dimensions: int | None) -> list[float]:
    client = OpenAI()
    kwargs: dict[str, Any] = {"model": model, "input": question}
    if dimensions:
        kwargs["dimensions"] = dimensions
    response = client.embeddings.create(**kwargs)
    return response.data[0].embedding


def is_image_query(question: str) -> bool:
    question = normalize_query_spacing(question)
    return any(term in question for term in IMAGE_QUERY_TERMS)


def normalize_compact(text: str) -> str:
    return re.sub(r"\s+", "", (text or "").lower())


def image_title_tokens(question: str) -> list[str]:
    question = normalize_query_spacing(question)
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

    conn = connect_db()
    try:
        with conn:
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
    finally:
        conn.close()

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


def prioritize_focus_rows(rows: list[dict[str, Any]], focus_terms: tuple[str, ...]) -> list[dict[str, Any]]:
    terms = [
        term
        for term in focus_terms
        if term not in OVERVIEW_IGNORE_TERMS and not term.endswith(("해줘", "알려줘"))
    ]
    if not terms:
        return rows
    def focus_hits(row: dict[str, Any]) -> int:
        text = f"{row.get('title') or ''} {row.get('chunk_text') or ''}"
        return sum(term in text for term in terms)

    return sorted(rows, key=focus_hits, reverse=True)


class PgVectorHybridRetriever:
    def __init__(
        self,
        model: str | None = None,
        dimensions: int | None = None,
        candidate_pool: int | None = None,
        rerank_pool: int | None = None,
    ) -> None:
        load_env()
        self.model = model or os.getenv("EMBEDDING_MODEL", "text-embedding-3-small")
        self.dimensions = dimensions if dimensions is not None else int(os.getenv("EMBEDDING_DIMENSIONS", "1536"))
        self.candidate_pool = candidate_pool if candidate_pool is not None else int(os.getenv("RAG_RETRIEVAL_CANDIDATE_POOL", "50"))
        configured_rerank_pool = rerank_pool if rerank_pool is not None else int(os.getenv("RAG_RERANK_CANDIDATE_POOL", "0"))
        self.rerank_pool = configured_rerank_pool or None

    def search_images(self, question: str, top_k: int = 5) -> list[PgSearchResult]:
        title_tokens = image_title_tokens(question)
        where_parts = [
            "source_type = 'image_material'",
            "(NULLIF(metadata->>'original_image_url', '') IS NOT NULL OR NULLIF(metadata->>'thumbnail_url', '') IS NOT NULL)",
        ]
        params: list[Any] = []
        if title_tokens:
            title_clauses = []
            for token in title_tokens:
                title_clauses.append("regexp_replace(lower(title), '\\s+', '', 'g') LIKE %s")
                params.append(f"%{token}%")
            where_parts.append("(" + " OR ".join(title_clauses) + ")")

        sql = f"""
        SELECT
            chunk_id,
            document_id,
            source_type,
            source_name,
            title,
            chunk_text,
            metadata,
            0.0 AS vector_score,
            (
                similarity(title, %s) * 3.0
                + similarity(chunk_text, %s)
                + CASE WHEN title ILIKE %s THEN 3.0 ELSE 0.0 END
            ) AS keyword_score
        FROM rag.document_chunks
        WHERE {" AND ".join(where_parts)}
        ORDER BY keyword_score DESC, title
        LIMIT %s
        """
        query_params = [question, question, f"%{question}%", *params, top_k]

        conn = connect_db()
        try:
            with conn:
                with conn.cursor(cursor_factory=RealDictCursor) as cur:
                    cur.execute(sql, query_params)
                    rows = cur.fetchall()
        finally:
            conn.close()

        return [
            PgSearchResult(
                chunk_id=row["chunk_id"],
                document_id=row["document_id"],
                source_type=row["source_type"],
                source_name=row["source_name"],
                title=row["title"],
                chunk_text=row["chunk_text"],
                metadata=row["metadata"] or {},
                vector_score=0.0,
                keyword_score=float(row["keyword_score"] or 0.0),
                score=float(row["keyword_score"] or 0.0),
            )
            for row in rows
        ]

    def search(self, question: str, top_k: int = 5) -> list[PgSearchResult]:
        question = normalize_query_spacing(question.strip())
        if is_image_query(question):
            return self._search_uncached(question, top_k)
        return list(
            cached_pg_search(
                question,
                top_k,
                self.model,
                self.dimensions,
                self.candidate_pool,
                self.rerank_pool,
            )
        )

    def _search_uncached(self, question: str, top_k: int = 5) -> list[PgSearchResult]:
        question = normalize_query_spacing(question.strip())
        if not question:
            return []

        image_query = is_image_query(question)
        if image_query:
            return self.search_images(question, top_k)

        focus_terms = overview_focus_terms(question)
        generic_overview_query = is_generic_overview_query(question, focus_terms)
        keyword_filter = focus_terms[0] if focus_terms else question
        embedding_question = question  # 임베딩용 벡터 쿼리는 순수 원본 질문 사용하여 희석 방지
        embedding = vector_literal(embed_query(embedding_question, self.model, self.dimensions))
        overview_query = any(term in question for term in OVERVIEW_TERMS)
        use_reranker = os.getenv("RAG_RERANKER_ENABLED", "").lower() in {"1", "true", "yes"}
        use_bm25 = os.getenv("RAG_BM25_ENABLED", "true").lower() in {"1", "true", "yes"}
        final_limit = max(top_k, self.rerank_pool) if use_reranker and self.rerank_pool else (
            max(top_k * 5, top_k) if generic_overview_query or use_reranker else top_k
        )
        bm25_candidate_pool = self.candidate_pool

        # 불용어(Stopwords)를 걸러낸 정밀한 focus_terms 추출
        filtered_focus_terms = tuple(term for term in focus_terms if term not in HISTORY_STOPWORDS)
        comparison_query = any(term in question for term in COMPARISON_JOIN_TERMS) and len(focus_terms) >= 2
        bm25_query = mecab_search_tokens(
            build_bm25_query(filtered_focus_terms or focus_terms, keyword_filter)
        ) or keyword_filter
        bm25_tsquery_function = "plainto_tsquery"
        if comparison_query:
            # 비교 대상은 보통 다른 청크에 있으므로 FTS에서 둘 중 하나를 후보로 수집합니다.
            bm25_query = " OR ".join(bm25_query.split())
            bm25_tsquery_function = "websearch_to_tsquery"

        where_sql = "embedding IS NOT NULL"

        focus_match_sql = "FALSE"
        focus_match_params: list[Any] = []
        if generic_overview_query and filtered_focus_terms:
            focus_match_sql = " OR ".join(
                "(title ILIKE %s OR chunk_text ILIKE %s)" for _ in filtered_focus_terms
            )
            for term in filtered_focus_terms:
                like_term = f"%{term}%"
                focus_match_params.extend([like_term, like_term])
        bm25_focus_hit = "1" if generic_overview_query and filtered_focus_terms else "0"
        bm25_cte_sql = ""
        bm25_union_sql = ""
        if use_bm25:
            bm25_cte_sql = f"""
        ,
        bm25_candidates AS (
            SELECT
                id,
                chunk_id,
                source_type,
                0.0::float AS vector_score,
                ts_rank_cd(search_vector, {bm25_tsquery_function}('simple', %s)) * 2.0 AS keyword_score,
                {bm25_focus_hit} AS focus_hit
            FROM rag.document_chunks
            WHERE {where_sql}
              AND search_vector @@ {bm25_tsquery_function}('simple', %s)
            ORDER BY keyword_score DESC
            LIMIT %s
        ),
        bm25_ranked AS (
            SELECT *, row_number() OVER (ORDER BY keyword_score DESC) AS channel_rank
            FROM bm25_candidates
        )
            """
            bm25_union_sql = """
            UNION ALL
            SELECT * FROM bm25_ranked
            """

        sql = f"""
        WITH vector_candidates AS (
            SELECT
                id,
                chunk_id,
                source_type,
                1 - (embedding <=> %s::vector) AS vector_score,
                0.0::float AS keyword_score,
                CASE WHEN %s AND ({focus_match_sql}) THEN 1 ELSE 0 END AS focus_hit
            FROM rag.document_chunks
            WHERE embedding IS NOT NULL
            ORDER BY embedding <=> %s::vector
            LIMIT %s
        ),
        vector_ranked AS (
            SELECT *, row_number() OVER (ORDER BY vector_score DESC) AS channel_rank
            FROM vector_candidates
        )
        {bm25_cte_sql},
        merged_candidates AS (
            SELECT * FROM vector_ranked
            {bm25_union_sql}
        ),
        candidates AS (
            SELECT
                id,
                chunk_id,
                max(source_type) AS source_type,
                max(vector_score) AS vector_score,
                max(keyword_score) AS keyword_score,
                max(focus_hit) AS focus_hit,
                sum(1.0 / (60 + channel_rank)) AS rrf_score
            FROM merged_candidates
            GROUP BY id, chunk_id
        ),
        ranked AS (
            SELECT
                id,
                chunk_id,
                source_type,
                vector_score,
                keyword_score,
                (
                    rrf_score
                    + CASE
                        WHEN %s AND source_type = 'aks_encyclopedia' THEN 0.0005
                        WHEN %s AND source_type = 'historical_source' THEN -0.00015
                        ELSE 0.0
                      END
                    + CASE
                        WHEN %s AND focus_hit = 1 THEN 0.0018
                        ELSE 0.0
                      END
                ) AS score
            FROM candidates
            ORDER BY score DESC
            LIMIT %s
        )
        SELECT
            d.chunk_id,
            d.document_id,
            d.source_type,
            d.source_name,
            d.title,
            d.chunk_text,
            d.metadata,
            ranked.vector_score,
            ranked.keyword_score,
            ranked.score
        FROM ranked
        JOIN rag.document_chunks d ON d.id = ranked.id
        ORDER BY ranked.score DESC
        """

        def query_params() -> list[Any]:
            return [
                embedding,
                generic_overview_query,
                *focus_match_params,
                embedding,
                self.candidate_pool,
                *(
                    [
                        bm25_query,
                        bm25_query,
                        bm25_candidate_pool,
                    ]
                    if use_bm25
                    else []
                ),
                overview_query,
                overview_query,
                generic_overview_query,
                final_limit,
            ]

        conn = connect_db()
        try:
            with conn:
                with conn.cursor(cursor_factory=RealDictCursor) as cur:
                    cur.execute("SET LOCAL hnsw.ef_search = 120")
                    cur.execute(sql, query_params())
                    rows = cur.fetchall()
        finally:
            conn.close()

        if generic_overview_query:
            diversity_limit = final_limit if use_reranker and self.rerank_pool else top_k
            rows = diversify_rows(prioritize_focus_rows(rows, focus_terms), diversity_limit)
        results = [
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
        return rerank_results(question, results, top_k)


@lru_cache(maxsize=256)
def cached_pg_search(
    question: str,
    top_k: int,
    model: str,
    dimensions: int | None,
    candidate_pool: int,
    rerank_pool: int | None,
) -> tuple[PgSearchResult, ...]:
    if not question:
        return ()
    return tuple(PgVectorHybridRetriever(model, dimensions, candidate_pool, rerank_pool)._search_uncached(question, top_k))


def result_to_payload(result: PgSearchResult) -> dict[str, Any]:
    metadata = dict(result.metadata or {})
    snippet = compact_text(result.chunk_text)
    if result.source_type == "image_material":
        image = dict(metadata.get("image") or {})
        image.setdefault("source", metadata.get("image_source") or result.source_name)
        metadata["image"] = image
        snippet = re.sub(r"\s+", " ", result.chunk_text or "").strip()
    return {
        "chunk_id": result.chunk_id,
        "document_id": result.document_id,
        "source_type": result.source_type,
        "source_name": result.source_name,
        "title": result.title,
        "score": round(result.score, 4),
        "vector_score": round(result.vector_score, 4),
        "keyword_score": round(result.keyword_score, 4),
        "source_url": metadata.get("source_url"),
        "thumbnail_url": metadata.get("thumbnail_url"),
        "original_image_url": metadata.get("original_image_url"),
        "metadata": metadata,
        "snippet": snippet,
    }
