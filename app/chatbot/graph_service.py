from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from neo4j import GraphDatabase


PROJECT_ROOT = Path(__file__).resolve().parents[2]
TOKEN_RE = re.compile(r"[가-힣A-Za-z0-9]+")
FILTER_INTENT_TERMS = {"문화", "문화재", "인물", "사건", "시대", "관련", "관계"}
STOPWORDS = {
    "관련",
    "관계",
    "대해",
    "대한",
    "정리",
    "요약",
    "설명",
    "설명해줘",
    "알려줘",
    "알려",
    "조회",
    "역사적",
    "의미",
    "어떤",
    "있는지",
    "뭐야",
    "무엇",
    "누구",
    "하고",
    "이랑",
    "그리고",
    "차이",
    "업적",
    "정책",
} | FILTER_INTENT_TERMS
TOKEN_SUFFIXES = ("인가요", "이야", "인가", "이랑", "하고", "에게", "에서", "으로", "부터", "까지", "와", "과", "은", "는", "이", "가", "을", "를", "의", "에", "야")
HONORIFIC_SUFFIXES = ("대왕",)


def load_env() -> None:
    load_dotenv(PROJECT_ROOT / ".env")


def compact_text(value: str | None, max_length: int = 180) -> str:
    text = re.sub(r"\s+", " ", value or "").strip()
    if len(text) <= max_length:
        return text
    return text[: max_length - 1].rstrip() + "..."


def unique_values(values: list[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        value = (value or "").strip()
        if value and value not in result:
            result.append(value)
    return result


def normalize_token(token: str) -> str:
    value = (token or "").strip()
    changed = True
    while changed:
        changed = False
        for suffix in TOKEN_SUFFIXES:
            if len(value) > len(suffix) + 1 and value.endswith(suffix):
                value = value[: -len(suffix)]
                changed = True
                break
    return value


def token_variants(token: str) -> list[str]:
    values = [token]
    for suffix in HONORIFIC_SUFFIXES:
        if len(token) > len(suffix) + 1 and token.endswith(suffix):
            values.append(token[: -len(suffix)])
    return unique_values(values)


def extract_query_tokens(question: str) -> list[str]:
    tokens = [normalize_token(token) for token in TOKEN_RE.findall(question or "") if len(token) >= 2]
    base_tokens = [token for token in tokens if len(token) >= 2 and token not in STOPWORDS]
    filtered = []
    for size in (3, 2):
        for index in range(len(base_tokens) - size + 1):
            phrase_tokens = base_tokens[index : index + size]
            filtered.extend([" ".join(phrase_tokens), "".join(phrase_tokens)])
    for token in base_tokens:
        if len(token) < 2:
            continue
        filtered.extend(token_variants(token))
    return unique_values(filtered)[:24]


def neo4j_config() -> tuple[str, str, str] | None:
    load_env()
    uri = os.getenv("NEO4J_URI") or f"bolt://localhost:{os.getenv('NEO4J_BOLT_PORT', '7687')}"
    user = os.getenv("NEO4J_USER", "neo4j")
    password = os.getenv("NEO4J_PASSWORD", "")
    if not password:
        return None
    return uri, user, password


def disabled_context(reason: str) -> dict[str, Any]:
    return {
        "enabled": False,
        "reason": reason,
        "terms": [],
        "keywords": [],
        "relation_summary": "",
    }


GRAPH_QUERY = """
CALL () {
    WITH $tokens AS tokens
    MATCH (t:Term)
    WITH t,
         reduce(score = 0, token IN tokens |
            score
            + CASE WHEN t.name = token THEN 10 ELSE 0 END
            + CASE WHEN t.name CONTAINS token THEN 5 ELSE 0 END
            + CASE WHEN t.description CONTAINS token THEN 1 ELSE 0 END
            + CASE WHEN t.period_text CONTAINS token THEN 1 ELSE 0 END
            + CASE WHEN t.category_text CONTAINS token THEN 1 ELSE 0 END
         ) AS match_score
    WHERE match_score > 0
    OPTIONAL MATCH (t)-[:IN_PERIOD]->(period:Period)
    OPTIONAL MATCH (t)-[:RELATED_TO|REFERS_TO]-(related:Term)
    RETURN t.name AS term_name,
           t.hanja AS term_ch,
           t.year_text AS term_year,
           t.period_text AS term_times,
           t.description AS term_desc,
           collect(DISTINCT period.name) AS periods,
           collect(DISTINCT t.category_text) AS categories,
           [] AS paths,
           collect(DISTINCT related.name)[0..10] AS related_terms,
           match_score
    UNION ALL
    WITH $tokens AS tokens
    MATCH (p:Person)
    WITH p,
         reduce(score = 0, token IN tokens |
            score
            + CASE WHEN p.name CONTAINS token THEN 8 ELSE 0 END
            + CASE WHEN p.name_candidates CONTAINS token THEN 4 ELSE 0 END
         ) AS match_score
    WHERE match_score > 0
    OPTIONAL MATCH (p)-[:RELATED_TO]-(related:Person)
    RETURN p.name AS term_name,
           "" AS term_ch,
           "" AS term_year,
           "" AS term_times,
           "" AS term_desc,
           [] AS periods,
           ["인물"] AS categories,
           [] AS paths,
           collect(DISTINCT related.name)[0..10] AS related_terms,
           match_score
    UNION ALL
    WITH $tokens AS tokens
    MATCH (e:Event)
    WITH e,
         reduce(score = 0, token IN tokens |
            score
            + CASE WHEN e.name CONTAINS token THEN 8 ELSE 0 END
            + CASE WHEN e.related_event_name CONTAINS token THEN 4 ELSE 0 END
            + CASE WHEN e.period_text CONTAINS token THEN 1 ELSE 0 END
            + CASE WHEN e.subject_category CONTAINS token THEN 1 ELSE 0 END
         ) AS match_score
    WHERE match_score > 0
    OPTIONAL MATCH (e)-[:INVOLVED_IN]-(person:Person)
    OPTIONAL MATCH (e)-[:REFERS_TO]-(term:Term)
    OPTIONAL MATCH (e)-[:IN_ERA]-(era:Era)
    RETURN e.name AS term_name,
           "" AS term_ch,
           e.event_date AS term_year,
           e.period_text AS term_times,
           e.related_event_name AS term_desc,
           collect(DISTINCT era.name) AS periods,
           collect(DISTINCT e.subject_category) AS categories,
           [] AS paths,
           (collect(DISTINCT person.name) + collect(DISTINCT term.name))[0..10] AS related_terms,
           match_score
}
WITH *
ORDER BY match_score DESC, size(related_terms) DESC, term_name ASC
LIMIT $limit
RETURN term_name,
       term_ch,
       term_year,
       term_times,
       term_desc,
       periods,
       categories,
       paths,
       related_terms,
       match_score
"""


def build_relation_summary(terms: list[dict[str, Any]]) -> str:
    if not terms:
        return ""
    pieces: list[str] = []
    for term in terms[:4]:
        description = term.get("description") or ""
        categories = ", ".join((term.get("categories") or [])[:2])
        if description:
            pieces.append(f"{term['term_name']}: {description}")
        elif categories:
            pieces.append(f"{term['term_name']} 분류: {categories}")
        else:
            pieces.append(term["term_name"])
    return " / ".join(pieces)


def build_graph_context(question: str, limit: int = 6, max_hop: int = 1) -> dict[str, Any]:
    tokens = extract_query_tokens(question)
    if not tokens:
        return disabled_context("no_query_tokens")

    config = neo4j_config()
    if not config:
        return disabled_context("neo4j_password_missing")

    uri, user, password = config
    query = GRAPH_QUERY
    if max_hop >= 2:
        query = query.replace("[:RELATED_TO|REFERS_TO]-(related:Term)", "[:RELATED_TO|REFERS_TO*1..2]-(related:Term)")
        query = query.replace("[:RELATED_TO]-(related:Person)", "[:RELATED_TO*1..2]-(related:Person)")
    try:
        with GraphDatabase.driver(uri, auth=(user, password)) as driver:
            with driver.session() as session:
                rows = list(session.run(query, tokens=tokens, limit=limit))
    except Exception as exc:
        return disabled_context(f"neo4j_unavailable: {type(exc).__name__}")

    terms: list[dict[str, Any]] = []
    keyword_values: list[str] = list(tokens)
    for row in rows:
        item = {
            "term_name": row["term_name"],
            "term_ch": row["term_ch"],
            "term_year": row["term_year"],
            "term_times": row["term_times"],
            "description": compact_text(row["term_desc"]),
            "periods": unique_values(row["periods"] or []),
            "categories": unique_values(row["categories"] or []),
            "paths": unique_values(row["paths"] or []),
            "related_terms": unique_values(row["related_terms"] or []),
            "score": int(row["match_score"] or 0),
        }
        terms.append(item)
        keyword_values.extend(
            [
                item["term_name"],
                item["term_ch"],
                item["term_year"],
                item["term_times"],
                *item["periods"],
                *item["related_terms"],
            ]
        )

    keywords = [value for value in unique_values(keyword_values) if value]
    return {
        "enabled": True,
        "reason": "",
        "terms": terms,
        "keywords": keywords[:36],
        "relation_summary": build_relation_summary(terms),
        "max_hop": max_hop,
    }
