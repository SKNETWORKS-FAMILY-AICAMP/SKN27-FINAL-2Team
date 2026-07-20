"""Randomly export up to 200 DB documents that strictly match a golden question."""

from __future__ import annotations

import argparse
import csv
import json
import random
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[5]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.chatbot.rag.pgvector_retriever import connect_db, load_env
from app.chatbot.rag.korean_tokenizer import mecab_search_tokens
from build_golden_document_candidates import document_eras, era_matches, load_jsonl
from psycopg2.extras import RealDictCursor


DEFAULT_GOLDEN = ROOT / "etl" / "preprocessing" / "history" / "embedding" / "golden_questions.jsonl"
DEFAULT_OUTPUT = Path(__file__).resolve().with_name("golden_matched_documents_200.json")


def load_documents(document_ids: list[str]) -> dict[str, dict[str, Any]]:
    if not document_ids:
        return {}
    query = """
        SELECT document_id, chunk_id, source_type, source_name, title, chunk_text, metadata
        FROM rag.document_chunks
        WHERE document_id = ANY(%s)
    """
    documents: dict[str, dict[str, Any]] = {}
    conn = connect_db()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute(query, (document_ids,))
            for row in cursor:
                document_id = row["document_id"]
                document = documents.setdefault(document_id, {
                    "document_id": document_id,
                    "chunk_id": row["chunk_id"],
                    "source_type": row["source_type"],
                    "source_name": row["source_name"],
                    "title": row["title"],
                    "chunk_text": [],
                    "metadata": row["metadata"] or {},
                })
                document["chunk_text"].append(row["chunk_text"] or "")
    finally:
        conn.close()
    for document in documents.values():
        document["chunk_text"] = "\n".join(document["chunk_text"])
    return documents


def matching_document_ids(question: dict[str, Any], conn) -> set[str]:
    tokens = []
    for keyword in question["expected_keywords"]:
        try:
            tokens.append(mecab_search_tokens(keyword) or keyword)
        except (ImportError, OSError):
            tokens.append(keyword)
    query = """
        SELECT DISTINCT document_id
        FROM rag.document_chunks
        WHERE search_vector @@ plainto_tsquery('simple', %s)
    """
    with conn.cursor() as cursor:
        cursor.execute(query, (" ".join(tokens),))
        return {row[0] for row in cursor.fetchall()}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--golden", type=Path, default=DEFAULT_GOLDEN)
    parser.add_argument("--limit", type=int, default=200)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--csv", type=Path, help="Excel-friendly document list")
    args = parser.parse_args()
    if args.limit < 1:
        parser.error("--limit must be positive")

    load_env()
    questions = load_jsonl(args.golden)
    matched_questions: dict[str, list[str]] = defaultdict(list)
    conn = connect_db()
    try:
        question_matches = {question["id"]: matching_document_ids(question, conn) for question in questions}
    finally:
        conn.close()
    documents = load_documents(sorted(set().union(*question_matches.values())))
    for question in questions:
        for document_id in question_matches[question["id"]]:
            document = documents[document_id]
            if era_matches(question["expected_era"], document):
                matched_questions[document_id].append(question["id"])

    selected = random.sample(list(matched_questions), min(args.limit, len(matched_questions)))
    result = [{
        **{key: documents[document_id][key] for key in ("document_id", "chunk_id", "source_type", "source_name", "title", "metadata")},
        "document_eras": sorted(document_eras(documents[document_id])),
        "matched_golden_ids": matched_questions[document_id],
    } for document_id in selected]
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    csv_path = args.csv or args.output.with_suffix(".csv")
    with csv_path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=(
            "golden_id", "question", "expected_era", "expected_keywords", "judgment", "document_id", "chunk_id",
            "source_type", "source_name", "title", "document_eras", "matched_keywords",
        ))
        writer.writeheader()
        questions_by_id = {question["id"]: question for question in questions}
        for row in result:
            for golden_id in row["matched_golden_ids"]:
                question = questions_by_id[golden_id]
                writer.writerow({
                    "golden_id": golden_id,
                    "question": question["query"],
                    "expected_era": " | ".join(question["expected_era"]) if isinstance(question["expected_era"], list) else question["expected_era"],
                    "expected_keywords": " | ".join(question["expected_keywords"]),
                    "judgment": "정답 후보 (시대·기대 키워드 전체 일치)",
                    **{key: row[key] for key in ("document_id", "chunk_id", "source_type", "source_name", "title")},
                    "document_eras": " | ".join(row["document_eras"]),
                    "matched_keywords": " | ".join(question["expected_keywords"]),
                })
    print(f"{len(matched_questions)} strictly matched documents; {len(result)} sampled to {args.output} and {csv_path}")


if __name__ == "__main__":
    main()
