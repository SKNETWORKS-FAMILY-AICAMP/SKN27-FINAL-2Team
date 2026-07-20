from __future__ import annotations

import re

from .query_terms import tokenize


OVERVIEW_TERMS = ("정리", "요약", "흐름", "개념", "설명", "알려", "누구", "뭐야", "무엇", "내용", "왜", "이유", "중요", "중요한", "중요성", "업적", "정책", "대해", "대한", "대해서")
REQUEST_SUFFIX_TERMS = tuple(sorted({*OVERVIEW_TERMS, "이미지", "사진", "그림", "유물", "유적", "자료", "찾아줘", "보여줘", "조회", "알려줘", "설명해줘", "정리해줘", "요약해줘", "보여달라", "보여줄래"}, key=len, reverse=True))
OVERVIEW_IGNORE_TERMS = {"정리", "정리해줘", "요약", "요약해줘", "흐름", "개념", "설명", "설명해줘", "알려", "알려줘", "누구", "뭐", "무엇", "업적", "정책", "대해", "대한", "대해서", "조회", "역사적", "역사적으로", "의미", "의의", "이유", "왜", "어떤", "있는지", "중요", "중요성", "중요한", "중요한지", "차이", "비교", "대비", "특징", "내용", "보여주", "보여주는", "사건", "사건이야", "유명한", "대표", "대표적", "대표적인", "주요", "전개", "과정"}
HISTORY_STOPWORDS = {"전기", "후기", "중기", "초기", "말기", "시대", "국가", "나라", "역사", "한국", "인물", "사건", "조직", "단체", "유물", "유적", "정리", "요약", "설명", "개념", "왕", "대왕"}
BM25_IGNORE_TERMS = HISTORY_STOPWORDS | OVERVIEW_IGNORE_TERMS
HONORIFIC_SUFFIXES = ("대왕",)
SINGLE_CHAR_FOCUS_TERMS = {"왕"}


def normalize_query_spacing(question: str) -> str:
    value = re.sub(r"\s+", " ", question or "").strip()
    for term in REQUEST_SUFFIX_TERMS:
        value = re.sub(rf"(?<=[가-힣A-Za-z0-9])({re.escape(term)})(?=$|\s|[?.!,])", r" \1", value)
    return re.sub(r"\s+", " ", value).strip()


def overview_focus_terms(question: str) -> tuple[str, ...]:
    question = normalize_query_spacing(question)
    tokens = tokenize(question)
    compact_question = re.sub(r"[^\w\s]", " ", question)
    tokens.extend(term for term in SINGLE_CHAR_FOCUS_TERMS if re.search(rf"(?<!\S){re.escape(term)}(?:은|는|이|가|을|를|의|에)?(?!\S)", compact_question))
    terms: list[str] = []
    for token in tokens:
        normalized = token.strip()
        if (len(normalized) < 2 and normalized not in SINGLE_CHAR_FOCUS_TERMS) or normalized in OVERVIEW_IGNORE_TERMS:
            continue
        if terms and re.search(rf"{re.escape(normalized)}에\s*(대해|대한|대해서)", question):
            continue
        if normalized.endswith("에") and len(normalized) > 2:
            if terms:
                continue
            normalized = normalized[:-1]
        if normalized.endswith(("은", "는")):
            normalized = normalized[:-1]
        if normalized in OVERVIEW_IGNORE_TERMS:
            continue
        candidates = [normalized]
        if len(normalized) > 3 and normalized.endswith("대왕"):
            candidates.append(normalized[:-2])
        for candidate in candidates:
            if candidate and candidate not in terms:
                terms.append(candidate)
    return tuple(terms[:4])


def is_generic_overview_query(question: str, focus_terms: tuple[str, ...]) -> bool:
    return bool(focus_terms) and any(term in normalize_query_spacing(question) for term in OVERVIEW_TERMS)


def build_bm25_query(focus_terms: tuple[str, ...], fallback: str) -> str:
    terms: list[str] = []
    for term in focus_terms:
        candidates = [term[:-2], term] if len(term) > 3 and term.endswith("대왕") else [term]
        for candidate in candidates:
            if len(candidate) >= 2 and candidate not in BM25_IGNORE_TERMS:
                terms.append(candidate)
                break
    return " ".join(dict.fromkeys(terms)) or fallback
