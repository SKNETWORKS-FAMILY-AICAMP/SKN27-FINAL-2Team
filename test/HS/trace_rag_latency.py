from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

try:
    from langsmith import traceable
except ImportError:
    def traceable(func=None, **_kwargs):
        if func is None:
            return lambda wrapped: wrapped
        return func


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
load_dotenv(PROJECT_ROOT / ".env")

from app.chatbot.graph_service import build_graph_context
from app.chatbot.rag.llm_answer_generator import LLMAnswerGenerator
from app.chatbot.rag_service import (
    build_enriched_question,
    result_to_payload,
    should_use_graph_context,
)
from app.chatbot.rag import pgvector_retriever as retriever_module
from app.chatbot.rag.pgvector_retriever import PgVectorHybridRetriever


def seconds(start: float) -> float:
    return round(time.perf_counter() - start, 3)


@traceable(name="trace_history_rag_latency")
def trace_query(question: str, top_k: int) -> dict[str, float | int | str]:
    timings: dict[str, float | int | str] = {"question": question}
    original_embed_query = retriever_module.embed_query

    def timed_embed_query(*args, **kwargs):
        start = time.perf_counter()
        try:
            return original_embed_query(*args, **kwargs)
        finally:
            timings["embedding_sec"] = seconds(start)

    retriever_module.embed_query = timed_embed_query
    try:
        start = time.perf_counter()
        graph_context = build_graph_context(question, limit=8) if should_use_graph_context(question, "concept") else None
        timings["neo4j_sec"] = seconds(start)

        search_question = build_enriched_question(question, graph_context) if graph_context else question
        retriever = PgVectorHybridRetriever()
        start = time.perf_counter()
        results = retriever.search(search_question, top_k=top_k)
        timings["pgvector_search_sec"] = seconds(start)
        timings.setdefault("embedding_sec", 0.0)

        sources = [result_to_payload(result) for result in results]
        generator = LLMAnswerGenerator.from_env()
        start = time.perf_counter()
        answer = generator.generate(question, sources, style="textbook", include_source_summary=False)
        timings["llm_generation_sec"] = seconds(start)
        timings["total_sec"] = round(
            float(timings["neo4j_sec"])
            + float(timings["pgvector_search_sec"])
            + float(timings["llm_generation_sec"]),
            3,
        )
        timings["source_count"] = len(sources)
        timings["answer_chars"] = len(answer)
        return timings
    finally:
        retriever_module.embed_query = original_embed_query


def main() -> None:
    parser = argparse.ArgumentParser(description="Trace RAG latency by stage.")
    parser.add_argument("question", nargs="?", default="세종대왕 업적 알려줘")
    parser.add_argument("--top-k", type=int, default=5)
    args = parser.parse_args()

    result = trace_query(args.question, args.top_k)
    for key, value in result.items():
        print(f"{key}: {value}")


if __name__ == "__main__":
    main()
