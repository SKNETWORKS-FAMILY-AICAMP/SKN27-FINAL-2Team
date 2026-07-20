"""Export 100 distinct random RAG documents for golden-question labeling."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[5]
sys.path.insert(0, str(ROOT))

from app.chatbot.rag.pgvector_retriever import connect_db, load_env
from psycopg2.extras import RealDictCursor


DEFAULT_OUTPUT = Path(__file__).resolve().with_name("random_documents_100.json")


def sample_documents(limit: int) -> list[dict]:
    query = """
        WITH candidate_document_ids AS (
            SELECT document_id
            FROM rag.document_chunks TABLESAMPLE SYSTEM (5)
            GROUP BY document_id
        ), sampled_document_ids AS (
            SELECT document_id
            FROM candidate_document_ids
            ORDER BY random()
            LIMIT %s
        )
        SELECT DISTINCT ON (d.document_id)
            d.document_id,
            d.chunk_id,
            d.source_type,
            d.source_name,
            d.title,
            d.chunk_text,
            d.metadata
        FROM rag.document_chunks d
        JOIN sampled_document_ids s ON s.document_id = d.document_id
        ORDER BY d.document_id, d.chunk_id
    """
    conn = connect_db()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute(query, (limit,))
            return [dict(row) for row in cursor.fetchall()]
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    if args.limit < 1:
        parser.error("--limit must be positive")

    load_env()
    documents = sample_documents(args.limit)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(documents, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"{len(documents)} documents saved to {args.output}")


if __name__ == "__main__":
    main()
