from __future__ import annotations

import json
import math
import re
from collections import Counter
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Iterable

from .config import RagPaths


TOKEN_PATTERN = re.compile(r"[가-힣A-Za-z0-9一-龥]+")
STOPWORDS = {
    "설명",
    "알려줘",
    "정리",
    "무엇",
    "뭐야",
    "대한",
    "관련",
    "차이",
    "한국사",
    "한능검",
}

QUERY_EXPANSIONS = {
    "6조": ["육조", "六曹"],
    "육조": ["6조", "六曹"],
    "직계제": ["직계", "直啓", "직계아문"],
    "직계": ["직계제", "直啓", "직계아문"],
    "의정부서사제": ["의정부", "서사제", "署事"],
    "조선전기": ["조선 초기", "조선 초기의"],
    "전기": ["초기"],
    "정치": ["정치구조", "통치", "관료", "의정부", "육조"],
}


@dataclass(frozen=True)
class RagDocument:
    chunk_id: str
    document_id: str
    source_type: str
    source_name: str
    title: str
    chunk_text: str
    metadata: dict


@dataclass(frozen=True)
class SearchResult:
    document: RagDocument
    score: float
    keyword_score: float
    vector_score: float


def tokenize(text: str) -> list[str]:
    tokens = [token.lower() for token in TOKEN_PATTERN.findall(text or "")]
    normalized = []
    for token in tokens:
        token = re.sub(r"(이야|인가|이란|은|는|이|가|을|를|의|에|와|과)$", "", token)
        if len(token) > 1 and token not in STOPWORDS:
            normalized.append(token)
    return normalized


def expand_query_tokens(query: str, tokens: list[str]) -> list[str]:
    expanded = list(tokens)
    compact_query = re.sub(r"\s+", "", query)
    for key, values in QUERY_EXPANSIONS.items():
        if key in query or key in compact_query or key.lower() in tokens:
            for value in values:
                for token in tokenize(value):
                    if token not in expanded:
                        expanded.append(token)
    return expanded


def compact_text(text: str, max_length: int = 420) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    if len(text) <= max_length:
        return text
    return text[: max_length - 1].rstrip() + "..."


def load_jsonl(path: Path) -> Iterable[dict]:
    if not path.exists():
        return
    with path.open("r", encoding="utf-8") as fp:
        for line in fp:
            line = line.strip()
            if line:
                yield json.loads(line)


def make_cache_key(paths: RagPaths) -> tuple[str, str, str]:
    return (
        str(paths.historical_chunks),
        str(paths.new_history_chunks),
        str(paths.image_material_chunks),
    )


@lru_cache(maxsize=4)
def load_rag_documents(cache_key: tuple[str, str, str]) -> tuple[RagDocument, ...]:
    documents: list[RagDocument] = []
    for path_text in cache_key:
        path = Path(path_text)
        for row in load_jsonl(path):
            documents.append(
                RagDocument(
                    chunk_id=row.get("chunk_id", ""),
                    document_id=row.get("document_id", ""),
                    source_type=row.get("source_type", ""),
                    source_name=row.get("source_name", ""),
                    title=row.get("title", ""),
                    chunk_text=row.get("chunk_text", ""),
                    metadata=row.get("metadata") or {},
                )
            )
    return tuple(documents)


@lru_cache(maxsize=4)
def document_token_index(cache_key: tuple[str, str, str]) -> tuple[tuple[RagDocument, Counter[str]], ...]:
    index = []
    for document in load_rag_documents(cache_key):
        metadata = document.metadata
        keywords = " ".join(metadata.get("keywords") or [])
        category_tags = " ".join(metadata.get("category_tags") or [])
        chronology = metadata.get("chronology") or {}
        chronology_text = " ".join(
            str(value)
            for value in (
                chronology.get("era"),
                chronology.get("dynasty"),
                chronology.get("period_label"),
            )
            if value
        )
        category = metadata.get("category", "")
        weighted_text = f"{document.title} {document.title} {keywords} {category_tags} {chronology_text} {category} {document.chunk_text}"
        index.append((document, Counter(tokenize(weighted_text))))
    return tuple(index)


def keyword_score(query: str, query_tokens: list[str], document: RagDocument, token_counts: Counter[str]) -> float:
    if not query_tokens:
        return 0.0
    title = document.title.lower()
    keywords = " ".join(document.metadata.get("keywords") or []).lower()
    category_tags = " ".join(document.metadata.get("category_tags") or []).lower()
    chronology = document.metadata.get("chronology") or {}
    chronology_text = " ".join(
        str(value)
        for value in (
            chronology.get("era"),
            chronology.get("dynasty"),
            chronology.get("period_label"),
        )
        if value
    ).lower()
    category = str(document.metadata.get("category", "")).lower()
    book_title = str(document.metadata.get("book_title", "")).lower()
    field = str(document.metadata.get("field", "")).lower()
    chunk = document.chunk_text.lower()
    compact_query = re.sub(r"\s+", "", query.lower())
    metadata_text = f"{document.title} {book_title} {field} {category} {category_tags} {chronology_text} {' '.join(document.metadata.get('keywords') or [])}".lower()
    compact_doc = re.sub(r"\s+", "", f"{metadata_text} {document.chunk_text}".lower())

    score = 0.0
    for token in query_tokens:
        if token in title:
            score += 3.0
        if token in keywords:
            score += 2.5
        if token in category:
            score += 1.5
        if token in chunk:
            score += 0.8
        score += min(token_counts.get(token, 0), 3) * 0.8

    if len(compact_query) >= 4 and compact_query in compact_doc:
        score += 5.0

    rare_hits = sum(1 for token in set(query_tokens) if len(token) >= 3 and token in compact_doc)
    if rare_hits >= 2:
        score += rare_hits * 1.2

    period_match = ("조선" in query and ("전기" in query or "초기" in query) and ("조선 초기" in metadata_text or "조선 전기" in metadata_text))
    field_match = "정치" in query and ("정치" in metadata_text or "통치" in metadata_text)
    if period_match:
        score += 2.0
    if field_match:
        score += 2.0
    if period_match and field_match:
        score += 4.0
    if "정치" in query and "정치구조" in book_title:
        score += 5.0
    if "조선업" in metadata_text and "선박" not in query and "배" not in query:
        score -= 4.0
    return score / max(len(query_tokens), 1)


def vector_score(query_counts: Counter[str], token_counts: Counter[str]) -> float:
    if not query_counts or not token_counts:
        return 0.0
    intersection = set(query_counts) & set(token_counts)
    dot = sum(query_counts[token] * token_counts[token] for token in intersection)
    query_norm = math.sqrt(sum(value * value for value in query_counts.values()))
    doc_norm = math.sqrt(sum(value * value for value in token_counts.values()))
    if not query_norm or not doc_norm:
        return 0.0
    return dot / (query_norm * doc_norm)


def source_weight(source_type: str, image_query: bool = False, overview_query: bool = False) -> float:
    if image_query and source_type == "image_material":
        return 1.25
    if overview_query:
        weights = {
            "historical_source": 0.9,
            "historical_overview": 1.25,
            "image_material": 0.55,
        }
        return weights.get(source_type, 1.0)
    weights = {
        "historical_source": 1.2,
        "historical_overview": 1.0,
        "image_material": 0.65,
    }
    return weights.get(source_type, 1.0)


class HybridRagRetriever:
    def __init__(self, paths: RagPaths | None = None, top_k: int = 5) -> None:
        self.paths = paths or RagPaths()
        self.cache_key = make_cache_key(self.paths)
        self.top_k = top_k

    def search(self, query: str, top_k: int | None = None) -> list[SearchResult]:
        query_tokens = expand_query_tokens(query, tokenize(query))
        query_counts = Counter(query_tokens)
        image_query = any(token in query for token in ("이미지", "사진", "그림", "유물"))
        overview_query = any(token in query for token in ("정리", "요약", "흐름", "개념"))
        results: list[SearchResult] = []

        for document, token_counts in document_token_index(self.cache_key):
            k_score = keyword_score(query, query_tokens, document, token_counts)
            v_score = vector_score(query_counts, token_counts)
            score = ((k_score * 0.65) + (v_score * 0.35)) * source_weight(document.source_type, image_query, overview_query)
            if score > 0:
                results.append(SearchResult(document, score, k_score, v_score))

        results.sort(key=lambda result: result.score, reverse=True)
        deduped: list[SearchResult] = []
        seen_documents: set[str] = set()
        for result in results:
            if result.document.document_id in seen_documents:
                continue
            deduped.append(result)
            seen_documents.add(result.document.document_id)
            if len(deduped) >= (top_k or self.top_k):
                break
        return deduped


def extract_keywords(results: list[SearchResult], limit: int = 10) -> list[str]:
    keywords: list[str] = []
    for result in results:
        for keyword in result.document.metadata.get("keywords") or []:
            if keyword not in keywords:
                keywords.append(keyword)
            if len(keywords) >= limit:
                return keywords
    return keywords


def source_payload(results: list[SearchResult]) -> list[dict]:
    sources = []
    for result in results:
        document = result.document
        sources.append(
            {
                "chunk_id": document.chunk_id,
                "document_id": document.document_id,
                "source_type": document.source_type,
                "source_name": document.source_name,
                "title": document.title,
                "score": round(result.score, 4),
                "source_url": document.metadata.get("source_url"),
                "image_path": document.metadata.get("image_path"),
                "snippet": compact_text(document.chunk_text),
            }
        )
    return sources
