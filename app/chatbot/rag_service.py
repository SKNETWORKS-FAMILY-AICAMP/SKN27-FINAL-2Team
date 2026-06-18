from __future__ import annotations

import sys
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]
HS_RAG_DIR = PROJECT_ROOT / "test" / "HS"
if str(HS_RAG_DIR) not in sys.path:
    sys.path.insert(0, str(HS_RAG_DIR))

from llm_answer_generator import LLMAnswerGenerator  # noqa: E402
from pgvector_retriever import PgVectorHybridRetriever, result_to_payload  # noqa: E402


def build_history_rag_answer(
    question: str,
    mode: str = "history",
    answer_format: str = "structured",
    follow_up: bool = False,
    top_k: int = 5,
) -> dict[str, Any]:
    retriever = PgVectorHybridRetriever()
    results = retriever.search(question, top_k=top_k)
    sources = [result_to_payload(result) for result in results]

    generator = LLMAnswerGenerator.from_env()
    if answer_format == "structured":
        structured_answer = generator.generate_structured(
            question,
            sources,
            follow_up=follow_up or mode == "question",
        )
        answer = None
    else:
        structured_answer = None
        answer = generator.generate(
            question,
            sources,
            style="textbook",
            follow_up=follow_up or mode == "question",
        )

    return {
        "question": question,
        "mode": mode,
        "answer_format": answer_format,
        "answer": answer,
        "structured_answer": structured_answer,
        "llm": {
            "provider": generator.config.provider,
            "model": generator.config.model,
            "temperature": generator.config.temperature,
        },
        "sources": sources,
    }
