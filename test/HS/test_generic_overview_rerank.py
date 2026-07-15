import os
import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).parents[2]
sys.path.insert(0, str(ROOT))

from app.chatbot.rag.pgvector_retriever import PgSearchResult, rerank_results


def check_generic_overview_uses_reranker() -> None:
    source = (ROOT / "app/chatbot/rag/pgvector_retriever.py").read_text(
        encoding="utf-8"
    )
    assert "return rerank_results(question, results, top_k)" in source
    assert "if generic_overview_query:\n            return results[:top_k]" not in source


def check_rerank_preserves_retrieval_scores() -> None:
    def row(title: str, score: float) -> PgSearchResult:
        return PgSearchResult(title, title, "test", "test", title, "text", {}, 0.0, 0.0, score)

    class Reranker:
        def predict(self, pairs: list[tuple[str, str]]) -> list[float]:
            return [0.1, 0.9]

    with patch.dict(os.environ, {"RAG_RERANKER_ENABLED": "true"}), patch(
        "app.chatbot.rag.pgvector_retriever.get_reranker", return_value=Reranker()
    ):
        ranked = rerank_results("질문", [row("first", 0.9), row("second", 0.1)], 2)

    assert [item.title for item in ranked] == ["second", "first"]
    assert [item.score for item in ranked] == [0.1, 0.9]


if __name__ == "__main__":
    check_generic_overview_uses_reranker()
    check_rerank_preserves_retrieval_scores()
