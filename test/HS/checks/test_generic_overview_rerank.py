import os
import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).parents[3]
sys.path.insert(0, str(ROOT))

from app.chatbot.rag.pgvector_retriever import HISTORY_STOPWORDS, PgSearchResult, PgVectorHybridRetriever, build_bm25_query, is_image_query, prioritize_focus_rows, rerank_results
from app.chatbot.rag.reranker import score_results
from app.chatbot.rag.evidence import has_enough_evidence
from app.chatbot.rag.llm_answer_generator import normalize_structured_answer
from app.chatbot.rag_service import build_history_rag_answer


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
        "app.chatbot.rag.reranker.get_reranker", return_value=Reranker()
    ):
        ranked = rerank_results("질문", [row("first", 0.9), row("second", 0.1)], 2)
        scored = score_results("질문", [row("first", 0.9), row("second", 0.1)])

    assert [item.title for item in ranked] == ["second", "first"]
    assert [item.score for item in ranked] == [0.1, 0.9]
    assert scored is not None and [score for _, score in scored] == [0.1, 0.9]


def check_focus_rows_are_prioritized() -> None:
    rows = [
        {"title": "주생활", "chunk_text": "고구려 주거"},
        {"title": "광개토대왕", "chunk_text": "고구려의 전성기"},
    ]
    assert prioritize_focus_rows(rows, ("고구려", "전성기"))[0]["title"] == "광개토대왕"


def check_bm25_ignores_summary_instruction_terms() -> None:
    assert build_bm25_query(("임진왜란", "전개", "과정", "요약해줘"), "fallback") == "임진왜란"


def check_dynasty_names_remain_search_terms() -> None:
    assert not {"고구려", "신라", "백제", "고려", "조선", "발해", "가야"} & HISTORY_STOPWORDS


def check_evidence_gate_keeps_rrf_compatibility() -> None:
    row = PgSearchResult("id", "doc", "test", "test", "title", "text", {}, 0.4, 0.2, 0.02)
    assert has_enough_evidence([row], "concept")
    weak_row = PgSearchResult("id", "doc", "test", "test", "title", "text", {}, 0.1, 0.01, 0.02)
    assert not has_enough_evidence([weak_row], "concept")
    assert not has_enough_evidence([], "concept")
    assert not has_enough_evidence([], "image")


def check_rerank_pool_is_configurable() -> None:
    retriever = PgVectorHybridRetriever(candidate_pool=1000, rerank_pool=100)
    assert retriever.candidate_pool == 1000
    assert retriever.rerank_pool == 100


def check_exam_points_are_not_filtered() -> None:
    answer = normalize_structured_answer({"exam_points": ["고조선은 여러 부족이 연합한 국가였다."]})
    assert answer["exam_points"] == ["고조선은 여러 부족이 연합한 국가였다."]


def check_image_answer_skips_llm() -> None:
    image = PgSearchResult(
        "image-1", "doc-1", "image_material", "한국사 이미지", "세종대왕 사진", "세종대왕 초상", 
        {"original_image_url": "https://example.com/sejong.jpg", "image": {"source": "국립박물관"}}, 0.0, 0.0, 0.02,
    )
    with patch("app.chatbot.rag_service.PgVectorHybridRetriever") as retriever, patch(
        "app.chatbot.rag_service.search_timeline_sources", return_value=[]
    ), patch("app.chatbot.rag_service.LLMAnswerGenerator.from_env", side_effect=AssertionError("LLM 호출")):
        retriever.return_value.search.return_value = [image]
        result = build_history_rag_answer("세종대왕 사진 보여줘", intent="image")

    assert result["llm"] is None
    assert "국립박물관" in result["answer"]


def check_image_query_normalization() -> None:
    assert is_image_query("세종대왕 사진 보여줘")


if __name__ == "__main__":
    check_generic_overview_uses_reranker()
    check_rerank_preserves_retrieval_scores()
    check_focus_rows_are_prioritized()
    check_bm25_ignores_summary_instruction_terms()
    check_dynasty_names_remain_search_terms()
    check_evidence_gate_keeps_rrf_compatibility()
    check_rerank_pool_is_configurable()
    check_exam_points_are_not_filtered()
    check_image_answer_skips_llm()
    check_image_query_normalization()
