from __future__ import annotations

import csv
import json
import os
import shutil
from collections import Counter
from datetime import datetime
from pathlib import Path

try:
    import psycopg2
except ImportError:
    psycopg2 = None


PROJECT_ROOT = Path(__file__).resolve().parents[3]
CSV_PATH = PROJECT_ROOT / "etl" / "raw_data" / "한국사 이미지 자료" / "한국사_이미지_자료.csv"
SOURCE_LINKS_PATH = (
    PROJECT_ROOT
    / "etl"
    / "raw_data"
    / "한국사 이미지 자료"
    / "국사편찬위원회_제외_이미지출처_확인링크.csv"
)
PROCESSED_DIR = PROJECT_ROOT / "etl" / "preprocessing" / "history" / "processed"
DOCUMENTS_PATH = PROCESSED_DIR / "image_materials.documents.jsonl"
CHUNKS_PATH = PROCESSED_DIR / "image_materials.chunks.jsonl"

ID_COLUMN = "이미지ID"
TITLE_COLUMN = "제목"
SOURCE_COLUMN = "이미지출처"


ALLOWED_SOURCES = {
    "e뮤지엄(국립경주박물관)",
    "e뮤지엄(국립국악원)",
    "e뮤지엄(국립부여박물관)",
    "e뮤지엄(국립전주박물관)",
    "e뮤지엄(국립중앙박물관)",
    "e뮤지엄(국립청주박물관)",
    "e뮤지엄(국립춘천박물관)",
    "국가기록원",
    "국가유산청 궁능유적본부",
    "국립경주박물관",
    "국립고궁박물관",
    "국립공주박물관",
    "국립민속박물관",
    "국립중앙도서관",
    "국립중앙도서관 신문아카이브",
    "국립청주박물관",
    "국립한글박물관",
    "문화재청",
    "문화재청 국가문화유산포털",
    "문화재청 국립문화유산포털",
    "문화재청 덕수궁관리소",
    "미국국립문서기록관리청",
}

BLOCKED_SOURCES = {
    "e뮤지엄(국립김해박물관)",
    "e영상역사관",
    "e영상역사관 정부기록사진집",
    "국가보훈처 현충시설정보서비스",
    "국가유산청",
    "국가유산청 국가유산포털",
    "국가유산청 칠백의총관리소",
    "국립가야문화유산연구소",
    "국립경주문화유산연구소",
    "국립광주박물관",
    "국립문화유산연구원",
    "국립문화유산연구원 국가유산 지식이음",
    "국립문화재연구소 문화유산 연구지식포털",
    "국립문화재연구원",
    "국립문화재연구원 문화유산 연구지식포털",
    "국립문화재연구원 문화유산연구지식포털",
    "국립부여박물관",
    "국립일제강제동원역사관",
    "국립제주박물관",
    "국립중앙박물관",
    "국립중앙박물관/삼한문화재연구원",
    "국립진주박물관",
    "국립해양문화재연구소",
    "국립해양박물관",
    "대만 국립고궁박물관",
    "대통령기록관",
    "대한민국역사박물관",
    "보건복지부",
    "부산박물관, 국립중앙박물관, 복천박물관",
    "외교부 외교사료관",
    "일본 국립공문서관, 미국 국회도서관",
    "중앙선거관리위원회 사이버선거역사관",
    "프랑스 국립도서관",
    "한국정책방송원(e영상역사관)",
}

# Same source can contain materials with different terms. These title checks
# override the source-level rule above.
ALLOWED_TITLE_KEYWORDS = (
    "충북 제천 점말 동굴",
    "덕수궁",
)
BLOCKED_TITLE_KEYWORDS = (
    "훈민정음",
    "혜례본",
    "해례본",
)


def load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.lstrip().startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


def normalize(value: str | None) -> str:
    return (value or "").strip()


def is_nikh_source(source: str) -> bool:
    return "국사편찬위원회" in source


def is_allowed(row: dict[str, str]) -> bool:
    source = normalize(row.get(SOURCE_COLUMN))
    title = normalize(row.get(TITLE_COLUMN))

    if any(keyword in title for keyword in BLOCKED_TITLE_KEYWORDS):
        return False
    if any(keyword in title for keyword in ALLOWED_TITLE_KEYWORDS):
        return True
    if is_nikh_source(source):
        return True
    if source in ALLOWED_SOURCES:
        return True
    if source in BLOCKED_SOURCES:
        return False
    return False


def backup_files() -> Path:
    backup_dir = PROJECT_ROOT / "etl" / "backups" / f"image_license_filter_{datetime.now():%Y%m%d_%H%M%S}"
    backup_dir.mkdir(parents=True, exist_ok=True)
    for path in (CSV_PATH, SOURCE_LINKS_PATH, DOCUMENTS_PATH, CHUNKS_PATH):
        if path.exists():
            shutil.copy2(path, backup_dir / path.name)
    return backup_dir


def filter_csv() -> tuple[set[str], int, int, Counter[str], Counter[str]]:
    with CSV_PATH.open("r", encoding="utf-8-sig", newline="") as fp:
        reader = csv.DictReader(fp)
        fieldnames = reader.fieldnames or []
        rows = list(reader)

    kept_rows = [row for row in rows if is_allowed(row)]
    removed_rows = [row for row in rows if not is_allowed(row)]
    kept_ids = {normalize(row.get(ID_COLUMN)) for row in kept_rows if normalize(row.get(ID_COLUMN))}

    with CSV_PATH.open("w", encoding="utf-8-sig", newline="") as fp:
        writer = csv.DictWriter(fp, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(kept_rows)

    return (
        kept_ids,
        len(rows),
        len(kept_rows),
        Counter(normalize(row.get(SOURCE_COLUMN)) for row in kept_rows),
        Counter(normalize(row.get(SOURCE_COLUMN)) for row in removed_rows),
    )


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


def rebuild_source_links() -> int:
    with CSV_PATH.open("r", encoding="utf-8-sig", newline="") as fp:
        rows = list(csv.DictReader(fp))

    by_source: dict[str, str] = {}
    for row in rows:
        source = normalize(row.get(SOURCE_COLUMN))
        if not source or is_nikh_source(source) or source in by_source:
            continue
        by_source[source] = normalize(row.get("상세요청URL"))

    with SOURCE_LINKS_PATH.open("w", encoding="utf-8-sig", newline="") as fp:
        writer = csv.DictWriter(fp, fieldnames=["이름", "링크"])
        writer.writeheader()
        for source in sorted(by_source):
            writer.writerow({"이름": source, "링크": by_source[source]})

    return len(by_source)


def connect_db():
    if psycopg2 is None:
        raise RuntimeError("psycopg2 is not installed")
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


def print_counter(label: str, counter: Counter[str]) -> None:
    print(label)
    for source, count in counter.most_common():
        print(f"  {count:>4} {source}")


def main() -> None:
    load_dotenv(PROJECT_ROOT / ".env")
    backup_dir = backup_files()
    kept_ids, csv_total, csv_kept, kept_sources, removed_sources = filter_csv()
    documents_total, documents_kept = filter_jsonl(DOCUMENTS_PATH, kept_ids, ("doc_id", "original_id"))
    chunks_total, chunks_kept = filter_jsonl(CHUNKS_PATH, kept_ids, ("document_id",))
    source_link_count = rebuild_source_links()
    try:
        db_before, db_deleted, db_after = sync_db(kept_ids)
        db_status = f"db_before={db_before} db_deleted={db_deleted} db_after={db_after}"
    except Exception as error:
        db_status = f"db_skipped={error}"

    print(f"backup_dir={backup_dir}")
    print(f"csv_total={csv_total} csv_kept={csv_kept} csv_removed={csv_total - csv_kept}")
    print(
        f"documents_total={documents_total} documents_kept={documents_kept} "
        f"documents_removed={documents_total - documents_kept}"
    )
    print(f"chunks_total={chunks_total} chunks_kept={chunks_kept} chunks_removed={chunks_total - chunks_kept}")
    print(f"source_links={source_link_count}")
    print(db_status)
    print_counter("kept_sources:", kept_sources)
    print_counter("removed_sources:", removed_sources)


if __name__ == "__main__":
    main()
