from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from typing import Any

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
load_dotenv(PROJECT_ROOT / ".env")

from app.chatbot.graph_service import build_graph_context
from app.chatbot.rag import pgvector_retriever as retriever_module
from app.chatbot.rag.llm_answer_generator import LLMAnswerGenerator
from app.chatbot.rag.pgvector_retriever import PgVectorHybridRetriever, result_to_payload, search_timeline_sources
from app.chatbot.rag_service import (
    build_enriched_question,
    build_search_question,
    has_enough_evidence,
    normalize_history,
    normalize_intent,
    should_use_graph_context,
)


def elapsed(start: float) -> float:
    return round(time.perf_counter() - start, 3)


def measure(question: str, intent: str, answer_format: str, top_k: int) -> dict[str, Any]:
    timings: dict[str, Any] = {"question": question, "intent": intent}
    total_start = time.perf_counter()
    original_embed_query = retriever_module.embed_query

    def timed_embed_query(*args, **kwargs):
        start = time.perf_counter()
        try:
            return original_embed_query(*args, **kwargs)
        finally:
            timings["embedding_sec"] = elapsed(start)

    retriever_module.embed_query = timed_embed_query
    try:
        start = time.perf_counter()
        intent = normalize_intent(intent, answer_format)
        history = normalize_history([])
        search_seed = build_search_question(question, history)
        timings["prepare_sec"] = elapsed(start)

        start = time.perf_counter()
        graph_context = build_graph_context(search_seed, limit=8) if should_use_graph_context(search_seed, intent) else None
        timings["graph_sec"] = elapsed(start)

        search_question = build_enriched_question(search_seed, graph_context) if graph_context else search_seed

        start = time.perf_counter()
        results = PgVectorHybridRetriever().search(search_question, top_k=top_k)
        timings["pgvector_search_sec"] = elapsed(start)
        timings.setdefault("embedding_sec", 0.0)
        timings["db_and_rerank_sec"] = round(timings["pgvector_search_sec"] - timings["embedding_sec"], 3)

        start = time.perf_counter()
        sources = [result_to_payload(result) for result in results]
        timeline_sources = search_timeline_sources(search_question)
        sources.extend(timeline_sources)
        timings["timeline_sec"] = elapsed(start)

        start = time.perf_counter()
        enough = has_enough_evidence(results, intent)
        timings["evidence_check_sec"] = elapsed(start)
        if not enough:
            timings["llm_generation_sec"] = 0.0
            timings["total_sec"] = elapsed(total_start)
            timings["source_count"] = len(sources)
            timings["not_found"] = True
            return timings

        generator = LLMAnswerGenerator.from_env()
        start = time.perf_counter()
        if answer_format == "structured" and intent == "concept":
            answer = generator.generate_structured(question, sources, history=[])
            answer_size = len(str(answer))
        else:
            answer = generator.generate(question, sources, style="textbook", history=[])
            answer_size = len(answer)
        timings["llm_generation_sec"] = elapsed(start)

        timings["total_sec"] = elapsed(total_start)
        timings["source_count"] = len(sources)
        timings["answer_size"] = answer_size
        timings["not_found"] = False
        return timings
    finally:
        retriever_module.embed_query = original_embed_query


def main() -> None:
    parser = argparse.ArgumentParser(description="Measure RAG latency by stage.")
    parser.add_argument("question", nargs="?", default="세종대왕 업적 알려줘")
    parser.add_argument("--intent", default="concept", choices=["concept", "question", "image"])
    parser.add_argument("--answer-format", default="structured", choices=["structured", "text"])
    parser.add_argument("--top-k", type=int, default=5)
    args = parser.parse_args()

    result = measure(args.question, args.intent, args.answer_format, args.top_k)
    for key, value in result.items():
        print(f"{key}: {value}")


if __name__ == "__main__":
    main()
