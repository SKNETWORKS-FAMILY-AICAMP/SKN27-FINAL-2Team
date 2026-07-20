"""Evaluate normal RRF retrieval against the 444 strict golden documents."""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[5]
HERE = Path(__file__).resolve().parent
DATASET_DIR = HERE.parent / "dataset"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(DATASET_DIR))

from app.chatbot.rag.pgvector_retriever import PgVectorHybridRetriever
from build_golden_document_candidates import load_jsonl


DEFAULT_GOLDEN = ROOT / "etl" / "preprocessing" / "history" / "embedding" / "golden_questions.jsonl"
DEFAULT_DOCUMENTS = DATASET_DIR / "golden_matched_documents_444.json"
DEFAULT_DETAIL = HERE / "rrf_golden_444_detail.csv"
DEFAULT_SUMMARY = HERE / "rrf_golden_444_summary.csv"
TOP_KS = (1, 3, 5)


def relevant_documents(path: Path) -> dict[str, set[str]]:
    result: dict[str, set[str]] = {}
    for document in json.loads(path.read_text(encoding="utf-8")):
        for golden_id in document["matched_golden_ids"]:
            result.setdefault(golden_id, set()).add(document["document_id"])
    return result


def unique_documents(results, limit: int = 5):
    selected = []
    seen = set()
    for result in results:
        if result.document_id in seen:
            continue
        seen.add(result.document_id)
        selected.append(result)
        if len(selected) == limit:
            break
    return selected


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--golden", type=Path, default=DEFAULT_GOLDEN)
    parser.add_argument("--documents", type=Path, default=DEFAULT_DOCUMENTS)
    parser.add_argument("--detail", type=Path, default=DEFAULT_DETAIL)
    parser.add_argument("--summary", type=Path, default=DEFAULT_SUMMARY)
    args = parser.parse_args()

    os.environ["RAG_RERANKER_ENABLED"] = "false"
    questions = load_jsonl(args.golden)
    relevant_by_question = relevant_documents(args.documents)
    retriever = PgVectorHybridRetriever()
    details = []
    metrics = {top_k: [] for top_k in TOP_KS}
    for index, question in enumerate(questions, 1):
        relevant = relevant_by_question.get(question["id"], set())
        if not relevant:
            continue
        results = unique_documents(retriever.search(question["query"], top_k=25))
        ranks = {result.document_id: rank for rank, result in enumerate(results, 1)}
        for top_k in TOP_KS:
            hits = sum(document_id in relevant for document_id in list(ranks)[:top_k])
            first_hit = next((rank for document_id, rank in ranks.items() if document_id in relevant), 0)
            metrics[top_k].append({
                "precision": hits / top_k,
                "recall": hits / len(relevant),
                "reciprocal_rank": 1 / first_hit if first_hit else 0,
            })
        for rank, result in enumerate(results, 1):
            details.append({
                "golden_id": question["id"],
                "question": question["query"],
                "rank": rank,
                "document_id": result.document_id,
                "title": result.title,
                "rrf_score": round(result.score, 8),
                "is_relevant": result.document_id in relevant,
                "relevant_document_count": len(relevant),
            })
        print(f"[{index}/{len(questions)}] {question['id']}", flush=True)

    with args.detail.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=details[0].keys())
        writer.writeheader()
        writer.writerows(details)
    summary = []
    for top_k, values in metrics.items():
        summary.append({
            "top_k": top_k,
            "evaluated_questions": len(values),
            "precision": round(sum(value["precision"] for value in values) / len(values), 6),
            "recall": round(sum(value["recall"] for value in values) / len(values), 6),
            "mrr": round(sum(value["reciprocal_rank"] for value in values) / len(values), 6),
        })
    with args.summary.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=summary[0].keys())
        writer.writeheader()
        writer.writerows(summary)
    print(f"detail: {args.detail}")
    print(f"summary: {args.summary}")


if __name__ == "__main__":
    main()
