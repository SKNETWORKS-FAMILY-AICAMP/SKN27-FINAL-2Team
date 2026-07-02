from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path
from typing import Iterable

import psycopg2
from dotenv import load_dotenv
from openai import OpenAI, RateLimitError
from psycopg2.extras import Json, execute_values


PROJECT_ROOT = Path(__file__).resolve().parents[4]
DEFAULT_PROCESSED_DIR = PROJECT_ROOT / "etl" / "preprocessing" / "history" / "processed"
DEFAULT_CHUNK_FILES = [
    "historical_sources.chunks.jsonl",
    "new_history.chunks.jsonl",
    "image_materials.chunks.jsonl",
]


def load_jsonl(path: Path) -> Iterable[dict]:
    with path.open("r", encoding="utf-8") as fp:
        for line in fp:
            line = line.strip()
            if line:
                yield json.loads(line)


def vector_literal(values: list[float]) -> str:
    return "[" + ",".join(f"{value:.8f}" for value in values) + "]"


def connect_db():
    return psycopg2.connect(
        host=os.getenv("POSTGRES_HOST", "localhost"),
        port=int(os.getenv("POSTGRES_PORT", "5432")),
        dbname=os.getenv("POSTGRES_DB", "history_rag"),
        user=os.getenv("POSTGRES_USER", "himate"),
        password=os.getenv("POSTGRES_PASSWORD", "himate1234"),
    )


def ensure_table(conn, embedding_dimensions: int) -> None:
    if embedding_dimensions <= 0:
        raise ValueError("embedding_dimensions must be positive")

    with conn.cursor() as cur:
        cur.execute("CREATE EXTENSION IF NOT EXISTS vector")
        cur.execute("CREATE SCHEMA IF NOT EXISTS rag")
        cur.execute(
            f"""
            CREATE TABLE IF NOT EXISTS rag.document_chunks (
                id BIGSERIAL PRIMARY KEY,
                chunk_id TEXT UNIQUE NOT NULL,
                document_id TEXT NOT NULL,
                source_type TEXT NOT NULL,
                source_name TEXT NOT NULL,
                title TEXT NOT NULL,
                chunk_index INTEGER NOT NULL,
                chunk_text TEXT NOT NULL,
                token_count INTEGER,
                metadata JSONB NOT NULL DEFAULT '{{}}'::jsonb,
                embedding VECTOR({embedding_dimensions}),
                embedding_model TEXT,
                embedded_at TIMESTAMPTZ,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS document_chunks_source_type_idx
            ON rag.document_chunks (source_type)
            """
        )
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS document_chunks_metadata_gin_idx
            ON rag.document_chunks USING GIN (metadata)
            """
        )
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS document_chunks_text_trgm_idx
            ON rag.document_chunks USING GIN (chunk_text gin_trgm_ops)
            """
        )
    conn.commit()


def upsert_chunks(conn, chunk_files: list[Path]) -> int:
    rows = []
    for path in chunk_files:
        for row in load_jsonl(path):
            rows.append(
                (
                    row["chunk_id"],
                    row["document_id"],
                    row["source_type"],
                    row["source_name"],
                    row["title"],
                    int(row["chunk_index"]),
                    row["chunk_text"],
                    int(row.get("token_count") or 0),
                    Json(row.get("metadata") or {}),
                )
            )

    if not rows:
        return 0

    with conn.cursor() as cur:
        execute_values(
            cur,
            """
            INSERT INTO rag.document_chunks (
                chunk_id,
                document_id,
                source_type,
                source_name,
                title,
                chunk_index,
                chunk_text,
                token_count,
                metadata
            )
            VALUES %s
            ON CONFLICT (chunk_id) DO UPDATE SET
                document_id = EXCLUDED.document_id,
                source_type = EXCLUDED.source_type,
                source_name = EXCLUDED.source_name,
                title = EXCLUDED.title,
                chunk_index = EXCLUDED.chunk_index,
                chunk_text = EXCLUDED.chunk_text,
                token_count = EXCLUDED.token_count,
                metadata = EXCLUDED.metadata,
                updated_at = NOW()
            """,
            rows,
            page_size=1000,
        )
    conn.commit()
    return len(rows)


def collect_chunk_ids_by_source_type(chunk_files: list[Path]) -> dict[str, set[str]]:
    chunk_ids_by_source_type: dict[str, set[str]] = {}
    for path in chunk_files:
        for row in load_jsonl(path):
            source_type = row["source_type"]
            chunk_ids_by_source_type.setdefault(source_type, set()).add(row["chunk_id"])
    return chunk_ids_by_source_type


def delete_missing_chunks(conn, chunk_files: list[Path]) -> int:
    chunk_ids_by_source_type = collect_chunk_ids_by_source_type(chunk_files)
    deleted = 0
    with conn.cursor() as cur:
        for source_type, chunk_ids in chunk_ids_by_source_type.items():
            cur.execute(
                """
                DELETE FROM rag.document_chunks
                WHERE source_type = %s
                  AND NOT (chunk_id = ANY(%s))
                """,
                (source_type, list(chunk_ids)),
            )
            deleted += cur.rowcount
    conn.commit()
    return deleted


def fetch_unembedded_chunks(conn, embedding_model: str, limit: int) -> list[tuple[str, str]]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT chunk_id, chunk_text
            FROM rag.document_chunks
            WHERE embedding IS NULL
               OR embedding_model IS DISTINCT FROM %s
            ORDER BY id
            LIMIT %s
            """,
            (embedding_model, limit),
        )
        return cur.fetchall()


def embed_texts(client: OpenAI, model: str, texts: list[str], dimensions: int | None) -> list[list[float]]:
    kwargs = {"model": model, "input": texts}
    if dimensions:
        kwargs["dimensions"] = dimensions
    response = client.embeddings.create(**kwargs)
    return [item.embedding for item in response.data]


def update_embeddings(
    conn,
    chunk_ids: list[str],
    embeddings: list[list[float]],
    embedding_model: str,
) -> None:
    rows = [
        (chunk_id, vector_literal(embedding), embedding_model)
        for chunk_id, embedding in zip(chunk_ids, embeddings, strict=True)
    ]
    with conn.cursor() as cur:
        execute_values(
            cur,
            """
            UPDATE rag.document_chunks AS target
            SET
                embedding = data.embedding::vector,
                embedding_model = data.embedding_model,
                embedded_at = NOW(),
                updated_at = NOW()
            FROM (VALUES %s) AS data(chunk_id, embedding, embedding_model)
            WHERE target.chunk_id = data.chunk_id
            """,
            rows,
            template="(%s, %s, %s)",
            page_size=500,
        )
    conn.commit()


def create_vector_index(conn, index_lists: int) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS document_chunks_embedding_cosine_idx
            ON rag.document_chunks
            USING hnsw (embedding vector_cosine_ops)
            WHERE embedding IS NOT NULL
            """
        )
        cur.execute("ANALYZE rag.document_chunks")
    conn.commit()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Embed processed RAG chunks into PostgreSQL pgvector")
    parser.add_argument("--processed-dir", type=Path, default=DEFAULT_PROCESSED_DIR)
    parser.add_argument("--chunk-file", action="append", default=None, help="JSONL chunk filename. Can be repeated.")
    parser.add_argument("--model", default=os.getenv("EMBEDDING_MODEL", "text-embedding-3-small"))
    parser.add_argument("--dimensions", type=int, default=int(os.getenv("EMBEDDING_DIMENSIONS", "1536")))
    parser.add_argument("--batch-size", type=int, default=20)
    parser.add_argument("--limit", type=int, default=1000, help="Maximum chunks to embed in this run.")
    parser.add_argument("--skip-upsert", action="store_true", help="Do not load JSONL chunks before embedding.")
    parser.add_argument(
        "--delete-missing",
        action="store_true",
        help="Delete existing DB chunks for loaded source types when their chunk_id is no longer present in JSONL.",
    )
    parser.add_argument("--create-index", action="store_true", help="Create hnsw vector index after embedding.")
    parser.add_argument("--index-lists", type=int, default=100)
    parser.add_argument("--sleep", type=float, default=0.0, help="Seconds to sleep between embedding batches.")
    parser.add_argument("--rate-limit-sleep", type=float, default=65.0, help="Seconds to wait after OpenAI 429 errors.")
    return parser.parse_args()


def main() -> None:
    load_dotenv(PROJECT_ROOT / ".env")
    args = parse_args()

    chunk_names = args.chunk_file or DEFAULT_CHUNK_FILES
    chunk_files = [args.processed_dir / name for name in chunk_names]
    missing = [path for path in chunk_files if not path.exists()]
    if missing:
        raise FileNotFoundError(f"Missing chunk files: {missing}")

    conn = connect_db()
    client = OpenAI()

    ensure_table(conn, args.dimensions)

    if args.delete_missing:
        deleted = delete_missing_chunks(conn, chunk_files)
        print(f"deleted_missing_chunks={deleted}")

    if not args.skip_upsert:
        loaded = upsert_chunks(conn, chunk_files)
        print(f"upserted_chunks={loaded}")

    embedded = 0
    current_batch_size = args.batch_size
    while embedded < args.limit:
        batch_limit = min(current_batch_size, args.limit - embedded)
        rows = fetch_unembedded_chunks(conn, args.model, batch_limit)
        if not rows:
            break

        chunk_ids = [row[0] for row in rows]
        texts = [row[1] for row in rows]
        try:
            embeddings = embed_texts(client, args.model, texts, args.dimensions)
        except RateLimitError as exc:
            current_batch_size = max(1, current_batch_size // 2)
            print(
                "rate_limit=hit "
                f"next_batch_size={current_batch_size} "
                f"sleep_seconds={args.rate_limit_sleep}"
            )
            time.sleep(args.rate_limit_sleep)
            continue

        update_embeddings(conn, chunk_ids, embeddings, args.model)

        embedded += len(rows)
        print(f"embedded_chunks={embedded} batch_size={len(rows)}")
        if args.sleep:
            time.sleep(args.sleep)

    if args.create_index:
        create_vector_index(conn, args.index_lists)
        print("vector_index=created")

    conn.close()
    print("done")


if __name__ == "__main__":
    main()
