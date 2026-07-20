from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time
from pathlib import Path

from dotenv import load_dotenv


ROOT = Path(__file__).resolve().parents[5]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")

from app.chatbot.rag.pgvector_retriever import PgVectorHybridRetriever, cached_pg_search


HERE = Path(__file__).resolve().parent
DEFAULT_GOLDEN = HERE.parent / "dataset" / "golden_questions_strict_matched_444.jsonl"
OUTPUT_CSV = HERE / "golden_rrf_candidate_scores.csv"
TEXT_INTENTS = {"concept", "summary", "compare", "evidence"}


def load_questions(path: Path, limit: int) -> list[dict]:
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    rows = [row for row in rows if not row.get("requires_image") and row.get("intent") in TEXT_INTENTS]
    return rows[:limit] if limit else rows


def main() -> None:
    parser = argparse.ArgumentParser(description="Save RRF top candidates for golden questions without BGE reranking.")
    parser.add_argument("--golden-file", type=Path, default=DEFAULT_GOLDEN)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--candidate-pool", type=int, default=1000)
    parser.add_argument("--top-k", type=int, default=50)
    parser.add_argument("--output", type=Path, default=OUTPUT_CSV)
    args = parser.parse_args()
    if args.top_k < 1:
        parser.error("top-k must be positive")

    questions = load_questions(args.golden_file, args.limit)
    fields = ["golden_id", "question", "rrf_rank", "rrf_score", "vector_score", "keyword_score", "chunk_id", "document_id", "source_type", "source_name", "title", "chunk_text", "retrieval_sec"]
    os.environ["RAG_RERANKER_ENABLED"] = "false"
    with args.output.open("w", newline="", encoding="utf-8-sig") as file:
        writer = csv.DictWriter(file, fieldnames=fields)
        writer.writeheader()
        for index, question in enumerate(questions, 1):
            cached_pg_search.cache_clear()
            start = time.perf_counter()
            results = PgVectorHybridRetriever(candidate_pool=args.candidate_pool, rerank_pool=args.top_k).search(question["query"], top_k=args.top_k)
            retrieval_sec = time.perf_counter() - start
            for rank, result in enumerate(results, 1):
                writer.writerow({"golden_id": question["id"], "question": question["query"], "rrf_rank": rank, "rrf_score": round(result.score, 6), "vector_score": round(result.vector_score, 6), "keyword_score": round(result.keyword_score, 6), "chunk_id": result.chunk_id, "document_id": result.document_id, "source_type": result.source_type, "source_name": result.source_name, "title": result.title, "chunk_text": result.chunk_text, "retrieval_sec": round(retrieval_sec, 3)})
            print(f"saved: {index}/{len(questions)} {question['id']} candidates={len(results)} sec={retrieval_sec:.2f}")
    print(f"csv: {args.output}")


if __name__ == "__main__":
    main()
