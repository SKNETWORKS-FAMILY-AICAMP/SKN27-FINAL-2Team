"""Load normalized history timeline CSV into PostgreSQL."""

from __future__ import annotations

import argparse
import os
from pathlib import Path

import psycopg2
from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_CSV_PATH = PROJECT_ROOT / "etl" / "preprocessing" / "history" / "processed" / "history_timeline_processed.csv"


def connect_db():
    load_dotenv(PROJECT_ROOT / ".env")
    return psycopg2.connect(
        host=os.getenv("POSTGRES_HOST", "localhost"),
        port=int(os.getenv("POSTGRES_PORT", "5432")),
        dbname=os.getenv("POSTGRES_DB", "history_rag"),
        user=os.getenv("POSTGRES_USER", "himate"),
        password=os.getenv("POSTGRES_PASSWORD", "himate1234"),
    )


def ensure_table(conn) -> None:
    with conn.cursor() as cur:
        cur.execute("CREATE SCHEMA IF NOT EXISTS rag")
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS rag.history_timeline (
                age_id INTEGER,
                age TEXT,
                year INTEGER,
                title TEXT,
                period TEXT,
                era TEXT,
                field TEXT
            )
            """
        )
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS history_timeline_era_field_idx
            ON rag.history_timeline (era, field)
            """
        )
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS history_timeline_year_idx
            ON rag.history_timeline (year)
            """
        )
    conn.commit()


def load_csv(conn, csv_path: Path, replace: bool) -> int:
    if not csv_path.exists():
        raise FileNotFoundError(csv_path)

    with conn.cursor() as cur:
        if replace:
            cur.execute("TRUNCATE TABLE rag.history_timeline")
        with csv_path.open("r", encoding="utf-8-sig", newline="") as fp:
            cur.copy_expert(
                """
                COPY rag.history_timeline(age_id, age, year, title, period, era, field)
                FROM STDIN WITH (FORMAT csv, HEADER true)
                """,
                fp,
            )
        cur.execute("ANALYZE rag.history_timeline")
        cur.execute("SELECT COUNT(*) FROM rag.history_timeline")
        count = cur.fetchone()[0]
    conn.commit()
    return count


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--csv", type=Path, default=DEFAULT_CSV_PATH)
    parser.add_argument("--append", action="store_true")
    args = parser.parse_args()

    with connect_db() as conn:
        ensure_table(conn)
        count = load_csv(conn, args.csv, replace=not args.append)

    print(f"rows={count}")
    print("table=rag.history_timeline")


if __name__ == "__main__":
    main()
