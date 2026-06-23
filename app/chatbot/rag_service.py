from __future__ import annotations

from typing import Any

from .graph_service import build_graph_context
from .rag.llm_answer_generator import LLMAnswerGenerator
from .rag.pgvector_retriever import PgVectorHybridRetriever, result_to_payload


SUPPORTED_INTENTS = {"concept", "question", "image", "chat", "casual"}
NOT_FOUND_ANSWER = "검색 결과가 없습니다."
INSUFFICIENT_ANSWER_TERMS = (
    "확인 불가",
    "근거 부족",
    "답변 불가",
    "답변할 수 없",
    "찾을 수 없",
    "부족합니다",
    "부족하여",
)


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


def has_enough_evidence(results: list[Any], intent: str) -> bool:
    if not results:
        return False

    best = results[0]
    if intent == "image":
        return any(
            result.source_type == "image_material"
            and (result.metadata.get("original_image_url") or result.metadata.get("thumbnail_url"))
            for result in results
        )

    best_keyword = max(float(result.keyword_score or 0.0) for result in results)
    best_score = float(best.score or 0.0)
    if best_keyword >= 0.12:
        return True
    if best_keyword >= 0.05 and best_score >= 0.35:
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
    keyword_text = " ".join(str(keyword) for keyword in keywords[:24] if keyword)
    return f"{question} {keyword_text}".strip()


def is_insufficient_structured_answer(answer: dict[str, Any] | None) -> bool:
    if not answer:
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


def build_history_rag_answer(
    question: str,
    mode: str = "history",
    intent: str = "concept",
    answer_format: str = "structured",
    follow_up: bool = False,
    top_k: int = 5,
) -> dict[str, Any]:
    intent = normalize_intent(intent, answer_format)
    if intent in {"chat", "casual"}:
        return no_rag_answer(question, intent)

    if intent in {"question", "image"}:
        answer_format = "text"
    elif intent == "concept":
        answer_format = "structured"

    graph_context = build_graph_context(question, limit=8)
    search_question = build_enriched_question(question, graph_context)

    retriever = PgVectorHybridRetriever()
    results = retriever.search(search_question, top_k=max(top_k, 8 if graph_context.get("keywords") else top_k))
    sources = [result_to_payload(result) for result in results]

    if not has_enough_evidence(results, intent):
        return not_found_answer(question, intent, graph_context)

    generator = LLMAnswerGenerator.from_env()
    if answer_format == "structured":
        structured_answer = generator.generate_structured(
            question,
            sources,
            follow_up=follow_up or mode == "question",
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
