from __future__ import annotations

import re
import os
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from .graph_service import build_graph_context
from .rag.evidence import has_enough_evidence
from .rag.llm_answer_generator import LLMAnswerGenerator, normalize_structured_answer, sanitize_answer
from .rag.pgvector_retriever import (
    PgVectorHybridRetriever,
    is_image_query,
    overview_focus_terms,
    result_to_payload,
    search_timeline_sources,
)


SUPPORTED_INTENTS = {"concept", "question", "image", "chat", "casual"}
NOT_FOUND_ANSWER = "검색 결과가 없습니다."
FOLLOW_UP_TERMS = ("그거", "이거", "저거", "방금", "위", "앞에서", "그 정책", "그 왕", "그 인물", "그 사건", "그 제도")
PROBLEM_CONTEXT_TERMS = ("문제", "문항", "선지", "정답", "해설", "키 포인트", "오답", "보기")
CONTEXT_ONLY_TERMS = ("업적", "정책", "활동", "과학적", "문화적", "정치적", "경제적")
CONTEXT_ONLY_FOCUS_TERMS = {"과학적", "문화적", "정치적", "경제적"}
VAGUE_FOCUS_TERMS = {"뭐가있어", "뭐가있나요", "뭐있어", "뭐있나요"}
FOLLOW_UP_FOCUS_ONLY_TERMS = CONTEXT_ONLY_FOCUS_TERMS | {"왕"}
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
SHORT_FACT_TERMS = ("이름", "본명", "시호")
SHORT_FACT_QUESTION_TERMS = ("뭐야", "무엇", "누구", "알려")


def use_timeline_sources() -> bool:
    # ponytail: source-limited RAGAS must not mix in unfiltered timeline snippets.
    return not bool(os.getenv("RAG_ALLOWED_SOURCE_TYPES", "").strip())


def normalize_intent(intent: str | None, answer_format: str) -> str:
    value = (intent or "").strip().lower()
    if value in SUPPORTED_INTENTS:
        return value
    if answer_format == "structured":
        return "concept"
    return "question"


def is_short_fact_question(question: str) -> bool:
    return any(term in question for term in SHORT_FACT_TERMS) and any(
        term in question for term in SHORT_FACT_QUESTION_TERMS
    )


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


def build_retrieval_debug(
    search_seed: str,
    enriched_question: str,
    sources: list[dict[str, Any]],
    graph_context: dict[str, Any] | None,
) -> dict[str, Any]:
    graph_terms = []
    for term in (graph_context or {}).get("terms") or []:
        graph_terms.append(
            {
                "term_name": term.get("term_name"),
                "score": term.get("score"),
                "related_terms": (term.get("related_terms") or [])[:5],
            }
        )
    selected_sources = []
    for source in sources[:8]:
        selected_sources.append(
            {
                "title": source.get("title"),
                "source_type": source.get("source_type"),
                "source_name": source.get("source_name"),
                "score": source.get("score"),
            }
        )
    return {
        "search_seed": search_seed,
        "enriched_question": enriched_question,
        "graph_max_hop": (graph_context or {}).get("max_hop"),
        "graph_terms": graph_terms,
        "selected_sources": selected_sources,
    }


def build_enriched_question(question: str, graph_context: dict[str, Any]) -> str:
    # ponytail: 원 질문의 핵심어가 이미 검색을 제한하므로 단일 개념 질문에는 그래프 후보를 덧붙이지 않습니다.
    keywords = [] if overview_focus_terms(question) else (graph_context.get("keywords") or [])
    relation_summary = graph_context.get("relation_summary") or ""
    is_relation_query = any(term in question for term in RELATION_QUERY_TERMS)
    if not keywords and not (is_relation_query and relation_summary):
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
        if value in question or any(value != item and (value in item or item in value) for item in selected):
            continue
        selected.append(value)
        if len(selected) >= 1:
            break
    keyword_text = " ".join(selected)
    relation_text = relation_summary.split("/")[0].strip() if is_relation_query else ""
    return f"{question} {keyword_text} {relation_text}".strip()


def should_use_graph_context(question: str, intent: str) -> bool:
    if intent in {"image", "chat", "casual"}:
        return False
    if any(term in question for term in RELATION_QUERY_TERMS):
        return True
    if any(term in question for term in RELATION_JOIN_TERMS) and len(overview_focus_terms(question)) >= 2:
        return True
    
    # 인물/왕 또는 대표 역사 개념/제도 질문에 대해 Graph Context 활성화하여 Neo4j 연관 키워드 확보
    focus_terms = overview_focus_terms(question)
    if focus_terms:
        person_patterns = ("대왕", "왕", "태조", "태종", "세종", "세조", "성종", "광해", "영조", "정조", "고종", "순종", "이성계", "왕건", "궁예", "견훤", "김유신", "을지문덕", "장보고")
        if any(p in question for p in person_patterns):
            return True
        if intent == "concept":
            return True
            
    return False


def graph_hop_for_question(question: str) -> int:
    is_comparison = any(term in question for term in RELATION_JOIN_TERMS) and len(overview_focus_terms(question)) >= 2
    return 2 if any(term in question for term in RELATION_QUERY_TERMS) or is_comparison else 1


def is_problem_context_question(question: str) -> bool:
    return bool(re.search(r"\d+\s*번", question)) or any(term in question for term in PROBLEM_CONTEXT_TERMS)


def _problem_context_value(question: str, label: str) -> str:
    match = re.search(rf"\[{re.escape(label)}\]\s*(.*?)(?=\n\[[^\]]+\]|\Z)", question, re.DOTALL)
    return match.group(1).strip() if match else ""


def normalize_choice_explanations(value: dict[int, str] | None) -> dict[int, str]:
    return {
        int(number): str(explanation).strip()
        for number, explanation in (value or {}).items()
        if str(explanation or "").strip()
    }


def add_choice_explanation_context(question: str, choice_explanations: dict[int, str]) -> str:
    if not choice_explanations:
        return question
    rows = "\n".join(
        f"{number}. {explanation}"
        for number, explanation in sorted(choice_explanations.items())
    )
    return f"{question}\n\n[DB 선지별 해설]\n{rows}"


def build_problem_option_queries(
    question: str,
    choice_explanations: dict[int, str] | None = None,
) -> list[str]:
    problem = _problem_context_value(question, "문제")
    passage = _problem_context_value(question, "지문")
    category = _problem_context_value(question, "분류")
    options = _problem_context_value(question, "보기")
    context = re.sub(r"\s+", " ", " ".join(value for value in (passage, problem, category) if value)).strip()
    if not context:
        return []
    selected = re.search(r"(\d+)\s*번", _problem_context_value(question, "내 답"))
    choices = dict(re.findall(r"(?m)^\s*(\d+)\.\s*(.+)$", options))
    selected_choice = choices.get(selected.group(1)) if selected else None
    queries = [context]
    if selected_choice:
        queries.append(f"{context} {selected_choice}".strip())
    queries.extend(
        f"{context} {explanation}"
        for explanation in normalize_choice_explanations(choice_explanations).values()
    )
    return list(dict.fromkeys(queries))


def search_problem_option_sources(
    retriever: PgVectorHybridRetriever,
    question: str,
    choice_explanations: dict[int, str] | None = None,
) -> list[Any]:
    queries = build_problem_option_queries(question, choice_explanations)
    if not queries:
        return []
    with ThreadPoolExecutor(max_workers=min(len(queries), 5)) as executor:
        grouped_results = list(executor.map(lambda query: retriever.search(query, top_k=3), queries))
    deduplicated = {}
    for result in (item for group in grouped_results for item in group):
        existing = deduplicated.get(result.chunk_id)
        if existing is None or result.score > existing.score:
            deduplicated[result.chunk_id] = result
    return sorted(deduplicated.values(), key=lambda result: result.score, reverse=True)


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


def recent_topic_from_history(history: list[dict[str, str]], question: str) -> str:
    for item in reversed(history):
        if item["role"] != "user":
            continue
        content = item["content"]
        if not is_image_query(question):
            content = re.sub(r"(사진|이미지|그림|도판|조회|보여줘|보여달라|보여줄래|찾아줘|가져와|띄워줘)", " ", content)
        focus_terms = overview_focus_terms(content)
        if focus_terms and not all(term in FOLLOW_UP_FOCUS_ONLY_TERMS for term in focus_terms):
            return re.sub(r"\s+", " ", content).strip()
    return ""


def build_search_question(question: str, history: list[dict[str, str]], intent: str = "concept") -> str:
    if not history:
        return question
    if intent == "question" and not is_problem_context_question(question):
        return question
    needs_context = intent == "question" and is_problem_context_question(question)
    needs_context = needs_context or any(term in question for term in FOLLOW_UP_TERMS)
    focus_terms = overview_focus_terms(question)
    effective_focus_terms = tuple(term for term in focus_terms if term not in VAGUE_FOCUS_TERMS)
    needs_context = needs_context or (
        any(term in question for term in CONTEXT_ONLY_TERMS)
        and (
            not effective_focus_terms
            or all(term in CONTEXT_ONLY_FOCUS_TERMS for term in effective_focus_terms)
        )
    )
    needs_context = needs_context or bool(
        effective_focus_terms and all(term in FOLLOW_UP_FOCUS_ONLY_TERMS for term in effective_focus_terms)
    )
    if not needs_context:
        return question
    recent_user_text = recent_topic_from_history(history, question)
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


def stream_concept_rag_answer(
    question: str, mode: str = "history", top_k: int = 20, history: list[dict[str, Any]] | None = None
) -> Iterator[dict[str, Any]]:
    """기존 개념 검색 흐름을 유지하고 LLM 표 행만 즉시 내보냅니다."""
    conversation_history = normalize_history(history)
    search_seed = build_search_question(question, conversation_history, "concept")
    is_contextual_follow_up = search_seed != question
    graph_context = build_graph_context(search_seed, limit=8, max_hop=graph_hop_for_question(search_seed)) if should_use_graph_context(search_seed, "concept") else None
    search_question = build_enriched_question(search_seed, graph_context) if graph_context else search_seed
    retriever = PgVectorHybridRetriever()
    results = retriever.search(search_question, top_k=max(top_k, 8 if graph_context and graph_context.get("keywords") else top_k))
    sources = [result_to_payload(result) for result in results]
    if use_timeline_sources():
        sources.extend(search_timeline_sources(search_question))
    retrieval_debug = build_retrieval_debug(search_seed, search_question, sources, graph_context)
    if not has_enough_evidence(results, "concept"):
        yield {"type": "done", "data": not_found_answer(question, "concept", graph_context)}
        return

    generator = LLMAnswerGenerator.from_env()
    answer: dict[str, Any] = {"answer_type": "textbook_note", "title": "한국사 개념 정리", "summary": "", "sections": [], "exam_points": [], "highlights": [], "source_titles": []}
    current_section: dict[str, Any] | None = None
    for event in generator.generate_structured_stream(question, sources, follow_up=is_contextual_follow_up, history=conversation_history, explanation_level="concept"):
        event_type = event["type"]
        if event_type == "meta":
            answer["title"] = str(event.get("title") or answer["title"])
            answer["summary"] = sanitize_answer(str(event.get("summary") or ""))
        elif event_type == "section":
            current_section = {"heading": str(event.get("heading") or ""), "items": []}
            answer["sections"].append(current_section)
        elif event_type == "row" and current_section is not None:
            row = {"term": str(event.get("term") or ""), "content": str(event.get("content") or "")}
            current_section["items"].append(row)
        elif event_type == "sources":
            answer["source_titles"] = event.get("source_titles") if isinstance(event.get("source_titles"), list) else []
        if event_type != "done":
            yield event

    result = {
        "question": question, "mode": mode, "intent": "concept", "answer_format": "structured", "answer": None,
        "structured_answer": normalize_structured_answer(answer), "not_found": False,
        "llm": {"provider": generator.config.provider, "model": generator.config.model, "temperature": generator.config.temperature},
        "sources": sources, "graph_context": graph_context, "search_seed": search_seed, "enriched_question": search_question, "retrieval_debug": retrieval_debug,
    }
    if is_insufficient_structured_answer(result["structured_answer"]):
        result = not_found_answer(question, "concept", graph_context)
    yield {"type": "done", "data": result}


def build_history_rag_answer(
    question: str,
    mode: str = "history",
    intent: str = "concept",
    answer_format: str = "structured",
    follow_up: bool = False,
    top_k: int = 20,
    history: list[dict[str, Any]] | None = None,
    explanation_level: str = "",
    choice_explanations: dict[int, str] | None = None,
) -> dict[str, Any]:
    intent = normalize_intent(intent, answer_format)
    conversation_history = normalize_history(history)
    short_fact = is_short_fact_question(question)
    if intent in {"chat", "casual"}:
        return no_rag_answer(question, intent)

    if intent in {"question", "image"} or short_fact:
        answer_format = "text"
    elif intent == "concept":
        answer_format = "structured"

    search_seed = build_search_question(question, conversation_history, intent)
    is_contextual_follow_up = search_seed != question
    graph_context = (
        build_graph_context(search_seed, limit=8, max_hop=graph_hop_for_question(search_seed))
        if should_use_graph_context(search_seed, intent)
        else None
    )
    search_question = build_enriched_question(search_seed, graph_context) if graph_context else search_seed
    resolved_choice_explanations = normalize_choice_explanations(choice_explanations)
    generation_question = add_choice_explanation_context(
        search_seed if is_contextual_follow_up else question,
        resolved_choice_explanations,
    )

    retriever = PgVectorHybridRetriever()
    results = (
        search_problem_option_sources(retriever, search_question, resolved_choice_explanations)
        if intent == "question"
        else []
    )
    if not results:
        results = retriever.search(search_question, top_k=max(top_k, 8 if graph_context and graph_context.get("keywords") else top_k))
    sources = [result_to_payload(result) for result in results]
    timeline_sources = search_timeline_sources(search_question) if use_timeline_sources() else []
    sources.extend(timeline_sources)
    retrieval_debug = build_retrieval_debug(search_seed, search_question, sources, graph_context)

    if not has_enough_evidence(results, intent):
        result = not_found_answer(question, intent, graph_context)
        result["search_seed"] = search_seed
        result["enriched_question"] = search_question
        result["retrieval_debug"] = retrieval_debug
        return result

    if intent == "image":
        answer = build_image_answer(question, sources)
        return {
            "question": question,
            "mode": mode,
            "intent": intent,
            "answer_format": "text",
            "answer": answer,
            "structured_answer": None,
            "not_found": False,
            "llm": None,
            "sources": sources,
            "graph_context": graph_context,
            "search_seed": search_seed,
            "enriched_question": search_question,
            "retrieval_debug": retrieval_debug,
        }

    generator = LLMAnswerGenerator.from_env()
    if answer_format == "structured":
        structured_answer = generator.generate_structured(
            generation_question,
            sources,
            follow_up=follow_up or mode == "question" or is_contextual_follow_up,
            history=conversation_history,
        )
        if is_insufficient_structured_answer(structured_answer):
            result = not_found_answer(question, intent, graph_context)
            result["search_seed"] = search_seed
            result["enriched_question"] = search_question
            result["retrieval_debug"] = retrieval_debug
            return result
        answer = None
    else:
        structured_answer = None
        answer = generator.generate(
            generation_question,
            sources,
            style="short" if short_fact else "textbook",
            follow_up=follow_up or mode == "question" or is_contextual_follow_up,
            history=conversation_history,
            include_source_summary=not short_fact and intent not in {"question", "image"},
            explanation_level=explanation_level,
        )
        if is_insufficient_text_answer(answer):
            result = not_found_answer(question, intent, graph_context)
            result["search_seed"] = search_seed
            result["enriched_question"] = search_question
            result["retrieval_debug"] = retrieval_debug
            return result

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
        "search_seed": search_seed,
        "enriched_question": search_question,
        "retrieval_debug": retrieval_debug,
    }


def stream_question_rag_answer(
    question: str,
    mode: str = "history",
    top_k: int = 8,
    history: list[dict[str, Any]] | None = None,
    explanation_level: str = "",
    choice_explanations: dict[int, str] | None = None,
) -> Iterator[dict[str, Any]]:
    conversation_history = normalize_history(history)
    search_seed = build_search_question(question, conversation_history, "question")
    is_contextual_follow_up = search_seed != question
    graph_context = build_graph_context(search_seed, limit=8, max_hop=graph_hop_for_question(search_seed)) if should_use_graph_context(search_seed, "question") else None
    search_question = build_enriched_question(search_seed, graph_context) if graph_context else search_seed
    resolved_choice_explanations = normalize_choice_explanations(choice_explanations)
    generation_question = add_choice_explanation_context(question, resolved_choice_explanations)
    retriever = PgVectorHybridRetriever()
    results = search_problem_option_sources(retriever, search_question, resolved_choice_explanations)
    if not results:
        results = retriever.search(search_question, top_k=top_k)
    sources = [result_to_payload(result) for result in results]
    if use_timeline_sources():
        sources.extend(search_timeline_sources(search_question))
    retrieval_debug = build_retrieval_debug(search_seed, search_question, sources, graph_context)
    if not has_enough_evidence(results, "question") and not (
        explanation_level == "core" and resolved_choice_explanations
    ):
        yield {"type": "done", "data": not_found_answer(question, "question", graph_context)}
        return

    generator = LLMAnswerGenerator.from_env()
    answer = {"answer_type": "follow_up_explanation", "title": "문제 해설", "summary": "", "sections": [], "exam_points": [], "highlights": [], "source_titles": []}
    current_section = None
    fixed_choice_section = False
    choice_section_emitted = False
    for event in generator.generate_structured_stream(generation_question, sources, follow_up=True, history=conversation_history, explanation_level=explanation_level):
        if event["type"] == "meta":
            answer["title"] = str(event.get("title") or answer["title"])
            answer["summary"] = sanitize_answer(str(event.get("summary") or ""))
        elif event["type"] == "section":
            current_section = {"heading": str(event.get("heading") or ""), "items": []}
            answer["sections"].append(current_section)
            fixed_choice_section = (
                explanation_level == "core"
                and bool(resolved_choice_explanations)
                and "선지 판단" in current_section["heading"]
            )
            if fixed_choice_section:
                choice_section_emitted = True
                yield event
                for number, explanation in sorted(resolved_choice_explanations.items()):
                    row = {"type": "row", "term": f"{number}번", "content": explanation}
                    current_section["items"].append({"term": row["term"], "content": explanation})
                    yield row
                continue
        elif event["type"] == "row" and current_section is not None:
            if fixed_choice_section:
                continue
            current_section["items"].append({"term": str(event.get("term") or ""), "content": str(event.get("content") or "")})
        elif event["type"] == "sources":
            answer["source_titles"] = event.get("source_titles") if isinstance(event.get("source_titles"), list) else []
        elif event["type"] == "done" and explanation_level == "core" and resolved_choice_explanations and not choice_section_emitted:
            current_section = {"heading": "2. 선지 판단", "items": []}
            answer["sections"].append(current_section)
            yield {"type": "section", "heading": current_section["heading"]}
            for number, explanation in sorted(resolved_choice_explanations.items()):
                row = {"type": "row", "term": f"{number}번", "content": explanation}
                current_section["items"].append({"term": row["term"], "content": explanation})
                yield row
        if event["type"] != "done":
            yield event
    structured_answer = normalize_structured_answer(answer)
    result = {
        "question": question, "mode": mode, "intent": "question", "answer_format": "structured", "answer": None,
        "structured_answer": structured_answer,
        "not_found": is_insufficient_structured_answer(structured_answer) and not (
            explanation_level == "core" and resolved_choice_explanations
        ),
        "explanation_level": explanation_level,
        "llm": {"provider": generator.config.provider, "model": generator.config.model, "temperature": generator.config.temperature},
        "sources": sources, "graph_context": graph_context, "search_seed": search_seed,
        "enriched_question": search_question, "retrieval_debug": retrieval_debug,
    }
    if result["not_found"]:
        result = not_found_answer(question, "question", graph_context)
    yield {"type": "done", "data": result}
