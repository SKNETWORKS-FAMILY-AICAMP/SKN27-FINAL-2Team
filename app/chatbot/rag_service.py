from __future__ import annotations

import re
from typing import Any

from .graph_service import build_graph_context
from .rag.llm_answer_generator import LLMAnswerGenerator
from .rag.pgvector_retriever import (
    PgVectorHybridRetriever,
    is_image_query,
    overview_focus_terms,
    result_to_payload,
    search_timeline_sources,
)


SUPPORTED_INTENTS = {"concept", "question", "image", "chat", "casual"}
NOT_FOUND_ANSWER = "검색 결과가 없습니다."
MIN_KEYWORD_SCORE = 0.12
MIN_COMBINED_SCORE = 0.70
FOLLOW_UP_MIN_COMBINED_SCORE = 0.50
FOLLOW_UP_TERMS = ("그거", "이거", "저거", "그럼", "좀더", "자세", "설명", "의의", "역사적", "왜", "어떻게", "차이", "비교", "누가", "누구", "발명", "만든", "만들", "했는데", "인데")
CONTEXT_ONLY_TERMS = ("업적", "정책", "활동", "과학적", "문화적", "정치적", "경제적")
CONTEXT_ONLY_FOCUS_TERMS = {"과학적", "문화적", "정치적", "경제적"}
KEYWORD_BLOCK_TERMS = ("업적", "정책", "정리", "요약", "설명", "설명해줘", "알려", "누구", "무엇", "뭐", "조회", "역사적", "의미", "어떤", "있는지")
PERIOD_ONLY_SUFFIXES = ("시대", "전기", "후기")
RELATION_QUERY_TERMS = ("관계", "관련", "연관", "사이", "부모", "어머니", "아버지", "아들", "딸", "부인", "아내", "남편", "스승", "제자", "문하", "가족")
RELATION_JOIN_TERMS = ("와", "과", "이랑", "하고", "및")
INSUFFICIENT_ANSWER_TERMS = (
    "확인 불가",
    "근거 부족",
    "답변 불가",
    "답변할 수 없",
    "찾을 수 없",
    "부족합니다",
    "부족하여",
    "충분히 제시되어 있지",
    "단정하기 어렵",
    "확정하기 어렵",
    "구체적으로 나오지 않",
    "직접 제시되어 있지",
)
IMAGE_REQUEST_TERMS_PATTERN = r"(사진|이미지|그림|도판|자료|조회|보여줘|보여달라|보여줄래|찾아줘|가져와|띄워줘|좀|의|에|대한|관련)"


def normalize_intent(intent: str | None, answer_format: str) -> str:
    value = (intent or "").strip().lower()
    if value in SUPPORTED_INTENTS:
        return value
    if answer_format == "structured":
        return "concept"
    return "question"


def no_rag_answer(question: str, intent: str) -> dict[str, Any]:
    if intent == "chat":
        answer = "현재 챗봇은 한국사 학습 질문과 문제 해설을 중심으로 답변합니다."
    elif intent == "casual":
        answer = "안녕하세요. 한국사 개념 정리, 문제 해설, 이미지 자료 조회를 도와드릴 수 있습니다."
    else:
        answer = NOT_FOUND_ANSWER
    return {
        "question": question,
        "mode": "auto",
        "intent": intent,
        "answer_format": "text",
        "answer": answer,
        "structured_answer": None,
        "not_found": intent not in {"chat", "casual"},
        "llm": None,
        "sources": [],
        "graph_context": None,
    }


def has_enough_evidence(
    results: list[Any],
    intent: str,
    extra_sources: list[dict[str, Any]] | None = None,
    follow_up: bool = False,
) -> bool:
    if not results:
        return bool(extra_sources and intent != "image")

    if extra_sources and intent != "image":
        return True

    best = results[0]
    if intent == "image":
        return any(
            result.source_type == "image_material"
            and (result.metadata.get("original_image_url") or result.metadata.get("thumbnail_url"))
            for result in results
        )

    best_keyword = max(float(result.keyword_score or 0.0) for result in results)
    best_score = float(best.score or 0.0)
    min_score = FOLLOW_UP_MIN_COMBINED_SCORE if follow_up else MIN_COMBINED_SCORE
    if best_keyword >= MIN_KEYWORD_SCORE and best_score >= min_score:
        return True
    return False


def not_found_answer(question: str, intent: str, graph_context: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "question": question,
        "mode": "auto",
        "intent": intent,
        "answer_format": "text",
        "answer": NOT_FOUND_ANSWER,
        "structured_answer": None,
        "not_found": True,
        "llm": None,
        "sources": [],
        "graph_context": graph_context,
    }


def build_enriched_question(question: str, graph_context: dict[str, Any]) -> str:
    keywords = graph_context.get("keywords") or []
    if not keywords:
        return question
    selected = []
    for keyword in keywords:
        value = str(keyword or "").strip()
        if len(value) < 2 or any(term in value for term in KEYWORD_BLOCK_TERMS):
            continue
        if not all("가" <= char <= "힣" for char in value):
            continue
        if value.endswith(PERIOD_ONLY_SUFFIXES):
            continue
        if any(value != item and value in item for item in selected):
            selected = [item for item in selected if value not in item]
        if any(item != value and item in value for item in selected):
            continue
        selected.append(value)
        if len(selected) >= 4:
            break
    keyword_text = " ".join(selected)
    return f"{question} {keyword_text}".strip()


def should_use_graph_context(question: str, intent: str) -> bool:
    if intent in {"image", "chat", "casual"}:
        return False
    if any(term in question for term in RELATION_QUERY_TERMS):
        return True
    return any(term in question for term in RELATION_JOIN_TERMS) and len(overview_focus_terms(question)) >= 2


def normalize_history(history: list[dict[str, Any]] | None, max_turns: int = 5) -> list[dict[str, str]]:
    if not history:
        return []
    normalized = []
    for item in history[-max_turns * 2 :]:
        role = str(item.get("role") or "").strip()
        content = str(item.get("content") or "").strip()
        if role not in {"user", "assistant"} or not content:
            continue
        normalized.append({"role": role, "content": content[:800]})
    return normalized


def build_search_question(question: str, history: list[dict[str, str]]) -> str:
    if not history:
        return question
    needs_context = any(term in question for term in FOLLOW_UP_TERMS)
    focus_terms = overview_focus_terms(question)
    needs_context = needs_context or (
        any(term in question for term in CONTEXT_ONLY_TERMS)
        and (not focus_terms or all(term in CONTEXT_ONLY_FOCUS_TERMS for term in focus_terms))
    )
    if not needs_context:
        return question
    recent_user_text = next((item["content"] for item in reversed(history) if item["role"] == "user"), "")
    if not is_image_query(question):
        recent_user_text = re.sub(r"(사진|이미지|그림|도판|조회|보여줘|보여달라|보여줄래|찾아줘|가져와|띄워줘)", " ", recent_user_text)
    return f"{recent_user_text} {question}".strip()


def is_insufficient_structured_answer(answer: dict[str, Any] | None) -> bool:
    if not answer:
        return False
    section_items = [
        item
        for section in answer.get("sections") or []
        if isinstance(section, dict)
        for item in section.get("items") or []
        if isinstance(item, dict) and (item.get("term") or item.get("content"))
    ]
    if len(section_items) >= 2:
        return False
    title = str(answer.get("title") or "")
    summary = str(answer.get("summary") or "")
    exam_points = " ".join(str(point) for point in answer.get("exam_points") or [])
    sections = " ".join(
        f"{section.get('heading', '')} "
        + " ".join(f"{item.get('term', '')} {item.get('content', '')}" for item in section.get("items") or [])
        for section in answer.get("sections") or []
        if isinstance(section, dict)
    )
    combined = f"{title} {summary} {exam_points} {sections}"
    return any(term in combined for term in INSUFFICIENT_ANSWER_TERMS)


def is_insufficient_text_answer(answer: str | None) -> bool:
    combined = str(answer or "")
    return any(term in combined for term in INSUFFICIENT_ANSWER_TERMS)


def image_answer_title(question: str, fallback: str) -> str:
    title = re.sub(IMAGE_REQUEST_TERMS_PATTERN, " ", question)
    title = re.sub(r"\s+", " ", title).strip()
    return title or re.sub(r"\s*(사진|이미지|그림)\s*$", "", fallback).strip() or fallback


def build_image_answer(question: str, sources: list[dict[str, Any]]) -> str:
    source = sources[0]
    metadata = source.get("metadata") or {}
    image = metadata.get("image") or {}
    title = image_answer_title(question, str(source.get("title") or "이미지 자료"))
    source_title = re.sub(r"\s*(사진|이미지|그림)\s*$", "", str(source.get("title") or title)).strip()
    image_source = image.get("source") or metadata.get("image_source") or source.get("source_name") or "한국사 이미지 자료"
    snippet = re.sub(r"https?://\S+", "", str(source.get("snippet") or ""))
    snippet = re.sub(r"\s+", " ", snippet.replace(source_title, "", 1)).strip()
    period = metadata.get("period") or ", ".join(metadata.get("periods") or [])
    category = metadata.get("category") or " ".join(
        value for value in (metadata.get("category_main"), metadata.get("category_sub")) if value
    )

    lines = [
        f"# {title}",
        "",
        "1. 사진",
        f"- 자료명: {source_title}",
        f"- 출처: {image_source}",
    ]
    if snippet:
        lines.extend(["", "2. 설명", f"- {snippet}"])
    info = [("시대", period), ("유형", category)]
    info = [(key, value) for key, value in info if value]
    if info:
        lines.extend(["", "3. 자료 정보", "| 항목 | 내용 |", "|---|---|"])
        lines.extend(f"| {key} | {value} |" for key, value in info)
    return "\n".join(lines)


def build_history_rag_answer(
    question: str,
    mode: str = "history",
    intent: str = "concept",
    answer_format: str = "structured",
    follow_up: bool = False,
    top_k: int = 5,
    history: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    intent = normalize_intent(intent, answer_format)
    conversation_history = normalize_history(history)
    if intent in {"chat", "casual"}:
        return no_rag_answer(question, intent)

    if intent in {"question", "image"}:
        answer_format = "text"
    elif intent == "concept":
        answer_format = "structured"

    search_seed = build_search_question(question, conversation_history)
    is_contextual_follow_up = search_seed != question
    graph_context = build_graph_context(search_seed, limit=8) if should_use_graph_context(search_seed, intent) else None
    search_question = build_enriched_question(search_seed, graph_context) if graph_context else search_seed
    generation_history = conversation_history if is_contextual_follow_up else []

    retriever = PgVectorHybridRetriever()
    results = retriever.search(search_question, top_k=max(top_k, 8 if graph_context and graph_context.get("keywords") else top_k))
    sources = [result_to_payload(result) for result in results]
    timeline_sources = search_timeline_sources(search_question)
    sources.extend(timeline_sources)

    if not has_enough_evidence(results, intent, timeline_sources, is_contextual_follow_up):
        return not_found_answer(question, intent, graph_context)

    if intent == "image":
        generator = LLMAnswerGenerator.from_env()
        answer = generator.generate(
            question,
            sources,
            style="textbook",
            follow_up=False,
            history=generation_history,
            include_source_summary=False,
        )
        answer = re.sub(r"https?://\S+", "", answer).strip() or build_image_answer(question, sources)
        return {
            "question": question,
            "mode": mode,
            "intent": intent,
            "answer_format": "text",
            "answer": answer,
            "structured_answer": None,
            "not_found": False,
            "llm": {
                "provider": generator.config.provider,
                "model": generator.config.model,
                "temperature": generator.config.temperature,
            },
            "sources": sources,
            "graph_context": graph_context,
        }

    generator = LLMAnswerGenerator.from_env()
    if answer_format == "structured":
        structured_answer = generator.generate_structured(
            question,
            sources,
            follow_up=follow_up or mode == "question",
            history=generation_history,
        )
        if is_insufficient_structured_answer(structured_answer):
            return not_found_answer(question, intent, graph_context)
        answer = None
    else:
        structured_answer = None
        answer = generator.generate(
            question,
            sources,
            style="textbook",
            follow_up=follow_up or mode == "question",
            history=generation_history,
            include_source_summary=intent not in {"question", "image"},
        )
        if is_insufficient_text_answer(answer):
            return not_found_answer(question, intent, graph_context)

    return {
        "question": question,
        "mode": mode,
        "intent": intent,
        "answer_format": answer_format,
        "answer": answer,
        "structured_answer": structured_answer,
        "not_found": False,
        "llm": {
            "provider": generator.config.provider,
            "model": generator.config.model,
            "temperature": generator.config.temperature,
        },
        "sources": sources,
        "graph_context": graph_context,
    }
