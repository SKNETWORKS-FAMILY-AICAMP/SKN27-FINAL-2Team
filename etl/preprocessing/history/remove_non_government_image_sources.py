from __future__ import annotations

import csv
import json
import os
import shutil
from datetime import datetime
from pathlib import Path

import psycopg2


PROJECT_ROOT = Path(__file__).resolve().parents[3]
CSV_PATH = PROJECT_ROOT / "etl" / "raw_data" / "한국사 이미지 자료" / "한국사_이미지_자료.csv"
PROCESSED_DIR = PROJECT_ROOT / "etl" / "preprocessing" / "history" / "processed"
DOCUMENTS_PATH = PROCESSED_DIR / "image_materials.documents.jsonl"
CHUNKS_PATH = PROCESSED_DIR / "image_materials.chunks.jsonl"

SOURCE_COLUMN = "이미지출처"
ID_COLUMN = "이미지ID"

GOVERNMENT_SOURCE_PATTERNS = [
    "국사편찬위원회",
    "문화재청",
    "국가유산청",
    "국가기록원",
    "국립",
    "대한민국역사박물관",
    "한국정책방송원",
    "e영상역사관",
    "외교부",
    "보건복지부",
    "대통령기록관",
    "중앙선거관리위원회",
    "국가보훈처",
    "미국국립문서기록관리청",
    "일본 국립공문서관",
    "프랑스 국립도서관",
    "대만 국립고궁박물관",
]


def load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.lstrip().startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


def is_government_source(source: str | None) -> bool:
    source = (source or "").strip()
    return any(pattern in source for pattern in GOVERNMENT_SOURCE_PATTERNS)


def backup_files() -> Path:
    backup_dir = PROJECT_ROOT / "etl" / "backups" / f"image_source_filter_{datetime.now():%Y%m%d_%H%M%S}"
    backup_dir.mkdir(parents=True, exist_ok=True)
    for path in (CSV_PATH, DOCUMENTS_PATH, CHUNKS_PATH):
        shutil.copy2(path, backup_dir / path.name)
    return backup_dir


def filter_csv() -> tuple[set[str], int, int]:
    with CSV_PATH.open("r", encoding="utf-8-sig", newline="") as fp:
        reader = csv.DictReader(fp)
        fieldnames = reader.fieldnames or []
        rows = list(reader)

    kept_rows = [row for row in rows if is_government_source(row.get(SOURCE_COLUMN))]
    kept_ids = {row.get(ID_COLUMN, "").strip() for row in kept_rows if row.get(ID_COLUMN, "").strip()}

    with CSV_PATH.open("w", encoding="utf-8-sig", newline="") as fp:
        writer = csv.DictWriter(fp, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(kept_rows)

    return kept_ids, len(rows), len(kept_rows)


def filter_jsonl(path: Path, kept_ids: set[str], id_fields: tuple[str, ...]) -> tuple[int, int]:
    total = 0
    kept = 0
    tmp_path = path.with_suffix(path.suffix + ".tmp")

    with path.open("r", encoding="utf-8") as src, tmp_path.open("w", encoding="utf-8", newline="\n") as dst:
        for line in src:
            line = line.strip()
            if not line:
                continue
            total += 1
            obj = json.loads(line)
            obj_id = next((str(obj.get(field) or "") for field in id_fields if obj.get(field)), "")
            if obj_id in kept_ids:
                dst.write(json.dumps(obj, ensure_ascii=False) + "\n")
                kept += 1

    tmp_path.replace(path)
    return total, kept


def connect_db():
    return psycopg2.connect(
        host=os.getenv("POSTGRES_HOST", "localhost"),
        port=int(os.getenv("POSTGRES_PORT", "5432")),
        dbname=os.getenv("POSTGRES_DB", "history_rag"),
        user=os.getenv("POSTGRES_USER", "himate"),
        password=os.getenv("POSTGRES_PASSWORD", "himate1234"),
    )


def sync_db(kept_ids: set[str]) -> tuple[int, int, int]:
    conn = connect_db()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM rag.document_chunks WHERE source_type = 'image_material'")
                before = cur.fetchone()[0]
                cur.execute(
                    """
                    DELETE FROM rag.document_chunks
                    WHERE source_type = 'image_material'
                      AND NOT (document_id = ANY(%s))
                    """,
                    (list(kept_ids),),
                )
                deleted = cur.rowcount
                cur.execute("SELECT COUNT(*) FROM rag.document_chunks WHERE source_type = 'image_material'")
                after = cur.fetchone()[0]
                cur.execute("ANALYZE rag.document_chunks")
        return before, deleted, after
    finally:
        conn.close()


def main() -> None:
    load_dotenv(PROJECT_ROOT / ".env")
    backup_dir = backup_files()
    kept_ids, csv_total, csv_kept = filter_csv()
    doc_total, doc_kept = filter_jsonl(DOCUMENTS_PATH, kept_ids, ("doc_id", "original_id"))
    chunk_total, chunk_kept = filter_jsonl(CHUNKS_PATH, kept_ids, ("document_id",))
    db_before, db_deleted, db_after = sync_db(kept_ids)

    print(f"backup_dir={backup_dir}")
    print(f"csv_total={csv_total} csv_kept={csv_kept} csv_removed={csv_total - csv_kept}")
    print(f"documents_total={doc_total} documents_kept={doc_kept} documents_removed={doc_total - doc_kept}")
    print(f"chunks_total={chunk_total} chunks_kept={chunk_kept} chunks_removed={chunk_total - chunk_kept}")
    print(f"db_before={db_before} db_deleted={db_deleted} db_after={db_after}")


if __name__ == "__main__":
    main()
