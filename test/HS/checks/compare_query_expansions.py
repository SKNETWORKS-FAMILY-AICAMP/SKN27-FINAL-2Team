from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).parents[3]
sys.path.insert(0, str(ROOT))

from app.chatbot.rag.pgvector_retriever import PgVectorHybridRetriever, image_title_tokens


QUERIES = ("6조 이미지", "육조 이미지", "3.1운동 이미지", "삼일운동 이미지", "을사늑약 이미지", "을사조약 이미지")


def titles(retriever: PgVectorHybridRetriever, question: str, expand: bool) -> tuple[list[str], list[str]]:
    if expand:
        return image_title_tokens(question), [item.title for item in retriever.search_images(question, top_k=5)]
    with patch("app.chatbot.rag.pgvector_retriever.expand_query_tokens", lambda _query, tokens: tokens):
        return image_title_tokens(question), [item.title for item in retriever.search_images(question, top_k=5)]


if __name__ == "__main__":
    retriever = PgVectorHybridRetriever()
    for question in QUERIES:
        on_tokens, on_titles = titles(retriever, question, expand=True)
        off_tokens, off_titles = titles(retriever, question, expand=False)
        print(f"\n{question}")
        print(f"  확장 켬 토큰: {on_tokens}")
        print(f"  확장 끔 토큰: {off_tokens}")
        print(f"  확장 켬 결과: {on_titles}")
        print(f"  확장 끔 결과: {off_titles}")
