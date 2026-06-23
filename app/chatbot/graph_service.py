from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from neo4j import GraphDatabase


PROJECT_ROOT = Path(__file__).resolve().parents[2]
TOKEN_RE = re.compile(r"[가-힣A-Za-z0-9]+")
STOPWORDS = {
    "관련",
    "관계",
    "대해",
    "대한",
    "정리",
    "요약",
    "설명",
    "알려줘",
    "알려",
    "뭐야",
    "무엇",
    "누구",
    "하고",
    "이랑",
    "그리고",
    "차이",
}
QUERY_ALIASES = {
    "세종대왕": ("세종대왕", "세종", "조선 세종"),
    "세종": ("세종", "세종대왕", "조선 세종"),
    "장영실": ("장영실", "앙부일구", "자격루", "측우기", "혼천의", "해시계", "물시계", "천문"),
}
TOKEN_SUFFIXES = ("이랑", "하고", "와", "과", "에게", "에서", "으로", "부터", "까지", "은", "는", "이", "가", "을", "를", "의", "에")


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
    for suffix in TOKEN_SUFFIXES:
        if len(value) > len(suffix) + 1 and value.endswith(suffix):
            return value[: -len(suffix)]
    return value


def extract_query_tokens(question: str) -> list[str]:
    tokens = [normalize_token(token) for token in TOKEN_RE.findall(question or "") if len(token) >= 2]
    filtered = [token for token in tokens if token not in STOPWORDS]
    expanded = list(filtered)
    compact_question = re.sub(r"\s+", "", question or "")
    for trigger, aliases in QUERY_ALIASES.items():
        if trigger in compact_question:
            expanded.extend(aliases)
    return unique_values(expanded)[:24]


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
MATCH (t:TermName)
WITH t,
     reduce(score = 0, token IN $tokens |
        score
        + CASE WHEN t.term_name = token THEN 10 ELSE 0 END
        + CASE WHEN t.term_name CONTAINS token THEN 5 ELSE 0 END
        + CASE WHEN t.term_desc CONTAINS token THEN 1 ELSE 0 END
        + CASE WHEN t.term_times CONTAINS token THEN 1 ELSE 0 END
     ) AS match_score
WHERE match_score > 0
OPTIONAL MATCH (t)-[:IN_PERIOD]->(period:TermTimes)
OPTIONAL MATCH (path:TermLink)-[:HAS_TERM]->(t)
OPTIONAL MATCH (path)-[:HAS_TERM]->(related:TermName)
WHERE related <> t
WITH t,
     match_score,
     collect(DISTINCT period.name) AS periods,
     collect(DISTINCT path.name) AS categories,
     collect(DISTINCT path.value) AS paths,
     collect(DISTINCT related.term_name)[0..10] AS related_terms
RETURN
    t.term_name AS term_name,
    t.term_ch AS term_ch,
    t.term_year AS term_year,
    t.term_times AS term_times,
    t.term_desc AS term_desc,
    periods,
    categories,
    paths,
    related_terms,
    match_score
ORDER BY match_score DESC, size(related_terms) DESC, term_name ASC
LIMIT $limit
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


def build_graph_context(question: str, limit: int = 6) -> dict[str, Any]:
    tokens = extract_query_tokens(question)
    if not tokens:
        return disabled_context("no_query_tokens")

    config = neo4j_config()
    if not config:
        return disabled_context("neo4j_password_missing")

    uri, user, password = config
    try:
        with GraphDatabase.driver(uri, auth=(user, password)) as driver:
            with driver.session() as session:
                rows = list(session.run(GRAPH_QUERY, tokens=tokens, limit=limit))
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
            ]
        )

    keywords = [value for value in unique_values(keyword_values) if value]
    return {
        "enabled": True,
        "reason": "",
        "terms": terms,
        "keywords": keywords[:36],
        "relation_summary": build_relation_summary(terms),
    }
