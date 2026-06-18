"""Preprocess '한국사 이미지 자료' CSV files for chatbot RAG.

Outputs:
  - image_materials.documents.jsonl
  - image_materials.chunks.jsonl
"""

from __future__ import annotations

import argparse
import csv
import json
import re
from pathlib import Path
from typing import Iterable

from rag_metadata import build_category_tags, build_chronology


SOURCE_NAME = "한국사 이미지 자료"
SOURCE_TYPE = "image_material"


def clean_text(value: str | None) -> str:
    if not value:
        return ""
    text = value.replace("\ufeff", "").replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def split_comma_values(value: str) -> list[str]:
    result: list[str] = []
    for token in re.split(r"[,|]", value or ""):
        token = token.strip()
        if token and token not in result:
            result.append(token)
    return result


def split_keywords(value: str) -> list[str]:
    result: list[str] = []
    for token in re.split(r"[,#|]", value or ""):
        token = token.strip()
        if token and token not in result:
            result.append(token)
    return result


def split_category(value: str) -> tuple[str, str]:
    parts = [part.strip() for part in clean_text(value).split(">") if part.strip()]
    main = parts[0] if parts else ""
    sub = " > ".join(parts[1:]) if len(parts) > 1 else ""
    return main, sub


def parse_related_contents(value: str) -> list[dict[str, str]]:
    contents = []
    for line in clean_text(value).splitlines():
        if "|" in line:
            title, url = line.split("|", 1)
            contents.append({"title": title.strip(), "url": url.strip()})
        elif line.strip():
            contents.append({"title": line.strip(), "url": ""})
    return contents


def iter_csv_rows(input_dir: Path) -> Iterable[dict[str, str]]:
    csv_path = input_dir / "한국사_이미지_자료.csv"
    with csv_path.open("r", encoding="utf-8-sig", newline="") as fp:
        reader = csv.DictReader(fp)
        for row in reader:
            row["_source_file"] = str(csv_path)
            yield row


def resolve_image_path(input_dir: Path, saved_path: str) -> tuple[str | None, bool]:
    saved_path = clean_text(saved_path)
    if not saved_path:
        return None, False

    relative_parts = Path(saved_path.replace("\\", "/")).parts
    if relative_parts and relative_parts[0] == "한국사 이미지 자료":
        candidate = input_dir.parent.joinpath(*relative_parts)
    else:
        candidate = input_dir / saved_path

    return str(candidate), candidate.exists() and candidate.is_file() and candidate.stat().st_size > 0


def build_document(row: dict[str, str], input_dir: Path) -> dict:
    image_id = clean_text(row.get("이미지ID"))
    title = clean_text(row.get("제목"))
    description = clean_text(row.get("설명"))
    list_summary = clean_text(row.get("목록요약"))
    content_body = description or list_summary
    periods = split_comma_values(clean_text(row.get("시대")))
    category_main, category_sub = split_category(clean_text(row.get("유형")))
    field = clean_text(row.get("분야"))
    keywords = split_keywords(clean_text(row.get("키워드")))
    image_path, image_available = resolve_image_path(input_dir, clean_text(row.get("저장이미지파일")))
    period = ", ".join(periods)
    category = clean_text(row.get("유형"))
    category_tags = build_category_tags(
        title=title,
        period=period,
        field=field,
        category=category,
        keywords=keywords,
        extra=[category_main, category_sub],
    )
    chronology = build_chronology(
        title=title,
        period=period,
        category=category,
        content=content_body,
        extra_text=clean_text(row.get("목록분류")),
    )

    content_parts = [
        title,
        content_body,
        f"시대: {', '.join(periods)}" if periods else "",
        f"유형: {category_main} {category_sub}".strip() if category_main or category_sub else "",
        f"키워드: {', '.join(keywords)}" if keywords else "",
    ]

    return {
        "doc_id": f"ki_{image_id}" if not image_id.startswith("ki_") else image_id,
        "source_type": SOURCE_TYPE,
        "source_name": SOURCE_NAME,
        "original_id": image_id,
        "title": title,
        "content": "\n".join(part for part in content_parts if part),
        "summary": content_body[:500],
        "period": period,
        "field": field,
        "category": category,
        "keywords": keywords,
        "source_url": clean_text(row.get("상세요청URL")),
        "image_path": image_path,
        "metadata": {
            "sequence": clean_text(row.get("순번")),
            "author": clean_text(row.get("작성자")),
            "periods": periods,
            "category_main": category_main,
            "category_sub": category_sub,
            "image_source": clean_text(row.get("이미지출처")),
            "license": clean_text(row.get("이용조건")),
            "related_contents": parse_related_contents(clean_text(row.get("관련콘텐츠"))),
            "list_category": clean_text(row.get("목록분류")),
            "thumbnail_url": clean_text(row.get("썸네일URL")),
            "original_image_url": clean_text(row.get("원본이미지URL")),
            "saved_image_file": clean_text(row.get("저장이미지파일")),
            "image_available": image_available,
            "source_file": row.get("_source_file"),
            "category_tags": category_tags,
            "chronology": chronology,
        },
    }


def build_chunk(document: dict) -> dict:
    return {
        "chunk_id": f"{document['doc_id']}_chunk_0000",
        "document_id": document["doc_id"],
        "source_type": document["source_type"],
        "source_name": document["source_name"],
        "title": document["title"],
        "chunk_index": 0,
        "chunk_text": document["content"],
        "token_count": len(document["content"].split()),
        "metadata": {
            **document["metadata"],
            "period": document["period"],
            "field": document["field"],
            "category": document["category"],
            "keywords": document["keywords"],
            "source_url": document["source_url"],
            "image_path": document["image_path"],
        },
    }


def write_jsonl(path: Path, rows: Iterable[dict]) -> int:
    count = 0
    with path.open("w", encoding="utf-8", newline="\n") as fp:
        for row in rows:
            fp.write(json.dumps(row, ensure_ascii=False) + "\n")
            count += 1
    return count


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-dir", type=Path, default=Path("storage/postgre/한국사 이미지 자료"))
    parser.add_argument("--output-dir", type=Path, default=Path("storage/postgre/processed"))
    args = parser.parse_args()

    args.output_dir.mkdir(parents=True, exist_ok=True)
    documents = [build_document(row, args.input_dir) for row in iter_csv_rows(args.input_dir)]
    documents = [doc for doc in documents if doc["title"] and doc["content"]]
    chunks = [build_chunk(document) for document in documents]

    doc_count = write_jsonl(args.output_dir / "image_materials.documents.jsonl", documents)
    chunk_count = write_jsonl(args.output_dir / "image_materials.chunks.jsonl", chunks)

    print(f"documents={doc_count}")
    print(f"chunks={chunk_count}")


if __name__ == "__main__":
    main()
