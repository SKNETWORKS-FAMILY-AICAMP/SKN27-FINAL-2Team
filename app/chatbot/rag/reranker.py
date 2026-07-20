from __future__ import annotations

import os
from functools import lru_cache
from typing import TypeVar


T = TypeVar("T")


@lru_cache(maxsize=1)
def get_reranker():
    try:
        from sentence_transformers import CrossEncoder
    except ImportError:
        return None
    return CrossEncoder(os.getenv("RAG_RERANKER_MODEL", "BAAI/bge-reranker-v2-m3"))


def score_results(question: str, rows: list[T]) -> list[tuple[T, float]] | None:
    if os.getenv("RAG_RERANKER_ENABLED", "").lower() not in {"1", "true", "yes"}:
        return None
    model = get_reranker()
    if model is None:
        return None
    pairs = [(question, f"{row.title}\n{' '.join(str(row.chunk_text).split())[:900]}") for row in rows]
    scores = model.predict(pairs)
    return [(row, float(score)) for row, score in zip(rows, scores)]


def rerank_results(question: str, rows: list[T], top_k: int) -> list[T]:
    scored = score_results(question, rows)
    if scored is None:
        return rows[:top_k]
    return [row for row, _ in sorted(scored, key=lambda item: item[1], reverse=True)[:top_k]]
