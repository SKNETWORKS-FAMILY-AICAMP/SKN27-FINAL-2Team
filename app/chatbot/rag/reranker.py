from __future__ import annotations

import os
from threading import Lock
from typing import TypeVar


T = TypeVar("T")
_reranker = None
_reranker_loaded = False
_reranker_lock = Lock()


def get_reranker():
    global _reranker, _reranker_loaded
    if _reranker_loaded:
        return _reranker
    with _reranker_lock:
        if not _reranker_loaded:
            try:
                from sentence_transformers import CrossEncoder
            except ImportError:
                _reranker = None
            else:
                _reranker = CrossEncoder(os.getenv("RAG_RERANKER_MODEL", "BAAI/bge-reranker-v2-m3"))
            _reranker_loaded = True
    return _reranker


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
