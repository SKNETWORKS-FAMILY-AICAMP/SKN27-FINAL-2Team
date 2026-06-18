"""Preprocess '신편 한국사 csv' files for chatbot RAG.

Outputs:
  - new_history.documents.jsonl
  - new_history.chunks.jsonl
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
from pathlib import Path
from typing import Iterable

from rag_metadata import build_category_tags, build_chronology


SOURCE_NAME = "신편 한국사"
SOURCE_TYPE = "historical_overview"


def clean_text(value: str | None) -> str:
    if not value:
        return ""
    text = value.replace("\ufeff", "").replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def stable_id(*parts: str) -> str:
    raw = "::".join(parts)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]


def normalize_heading(value: str) -> str:
    value = clean_text(value)
    value = re.sub(r"^[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ]+\.?\s*", "", value)
    value = re.sub(r"^\d+\)\s*", "", value)
    value = re.sub(r"^\d+\.?\s*", "", value)
    value = re.sub(r"^\(\d+\)\s*", "", value)
    return value.strip(" -")


def parse_title_hierarchy(title: str) -> dict:
    parts = [normalize_heading(part) for part in clean_text(title).split(">")]
    parts = [part for part in parts if part]
    keys = ["chapter", "section", "subsection", "topic"]
    return {key: parts[index] if index < len(parts) else "" for index, key in enumerate(keys)}


def split_keywords(*values: str) -> list[str]:
    tokens: list[str] = []
    for value in values:
        for token in re.split(r"[>,/#|,\s·－-]+", value or ""):
            token = token.strip(" `[]()")
            if len(token) >= 2 and token not in tokens:
                tokens.append(token)
    return tokens[:30]


def chunk_text(text: str, chunk_size: int, overlap: int) -> list[str]:
    text = clean_text(text)
    if not text:
        return []

    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    chunks: list[str] = []
    current = ""

    for paragraph in paragraphs:
        if len(paragraph) > chunk_size:
            if current:
                chunks.append(current.strip())
                current = ""
            start = 0
            while start < len(paragraph):
                end = start + chunk_size
                chunks.append(paragraph[start:end].strip())
                start = max(end - overlap, end)
            continue

        candidate = f"{current}\n\n{paragraph}".strip() if current else paragraph
        if len(candidate) <= chunk_size:
            current = candidate
        else:
            if current:
                chunks.append(current.strip())
            current = paragraph

    if current:
        chunks.append(current.strip())

    if overlap <= 0 or len(chunks) <= 1:
        return chunks

    overlapped: list[str] = []
    previous_tail = ""
    for chunk in chunks:
        merged = f"{previous_tail}\n{chunk}".strip() if previous_tail else chunk
        overlapped.append(merged)
        previous_tail = chunk[-overlap:]
    return overlapped


def iter_csv_rows(input_dir: Path) -> Iterable[dict[str, str]]:
    for csv_path in sorted(input_dir.glob("*.csv")):
        with csv_path.open("r", encoding="utf-8-sig", newline="") as fp:
            reader = csv.DictReader(fp)
            for row in reader:
                row["_source_file"] = str(csv_path)
                yield row


def infer_period(book_title: str, title: str) -> str:
    text = f"{book_title} {title}"
    period_candidates = [
        "선사",
        "고조선",
        "삼국",
        "통일신라",
        "발해",
        "고려",
        "조선 전기",
        "조선 후기",
        "근대",
        "현대",
    ]
    for period in period_candidates:
        if period in text:
            return period
    return ""


def build_document(row: dict[str, str]) -> dict:
    book_no = clean_text(row.get("권번호"))
    book_title = clean_text(row.get("권명"))
    page_order = clean_text(row.get("페이지순서"))
    title = clean_text(row.get("제목"))
    body = clean_text(row.get("본문"))
    footnote = clean_text(row.get("각주"))
    image_description = clean_text(row.get("이미지설명"))
    hierarchy = parse_title_hierarchy(title)
    period = infer_period(book_title, title)
    original_id = clean_text(row.get("페이지ID")) or f"nh_{book_no}_{page_order}_{stable_id(title)}"
    keywords = split_keywords(book_title, title, period, image_description)
    category = " > ".join(part for part in hierarchy.values() if part)
    category_tags = build_category_tags(
        title=title,
        period=period,
        field="",
        category=category,
        keywords=keywords,
        extra=[book_title],
    )
    chronology = build_chronology(
        title=title,
        period=period,
        category=f"{book_title} > {category}",
        content=body,
        extra_text=image_description,
    )

    content_parts = [body]
    if footnote:
        content_parts.append(f"[각주]\n{footnote}")
    if image_description:
        content_parts.append(f"[이미지 설명]\n{image_description}")

    return {
        "doc_id": original_id if original_id.startswith("nh_") else f"nh_{original_id}",
        "source_type": SOURCE_TYPE,
        "source_name": SOURCE_NAME,
        "original_id": original_id,
        "title": title,
        "content": "\n\n".join(part for part in content_parts if part),
        "summary": body[:500],
        "period": period,
        "field": "",
        "category": category,
        "keywords": keywords,
        "source_url": clean_text(row.get("원본URL")),
        "image_path": clean_text(row.get("이미지파일")) or None,
        "metadata": {
            "book_no": book_no,
            "book_title": book_title,
            "page_order": page_order,
            "page_id": clean_text(row.get("페이지ID")),
            "image_url": clean_text(row.get("이미지URL")),
            "image_description": image_description,
            "source_file": row.get("_source_file"),
            "category_tags": category_tags,
            "chronology": chronology,
            **hierarchy,
        },
    }


def build_chunks(document: dict, chunk_size: int, overlap: int) -> list[dict]:
    chunks = []
    prefix = f"{document['title']}\n"
    for index, text in enumerate(chunk_text(document["content"], chunk_size, overlap)):
        chunk_text_value = f"{prefix}{text}".strip()
        chunks.append(
            {
                "chunk_id": f"{document['doc_id']}_chunk_{index:04d}",
                "document_id": document["doc_id"],
                "source_type": document["source_type"],
                "source_name": document["source_name"],
                "title": document["title"],
                "chunk_index": index,
                "chunk_text": chunk_text_value,
                "token_count": len(chunk_text_value.split()),
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
        )
    return chunks


def write_jsonl(path: Path, rows: Iterable[dict]) -> int:
    count = 0
    with path.open("w", encoding="utf-8", newline="\n") as fp:
        for row in rows:
            fp.write(json.dumps(row, ensure_ascii=False) + "\n")
            count += 1
    return count


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-dir", type=Path, default=Path("etl/preprocessing/history/raw_data/신편 한국사 csv"))
    parser.add_argument("--output-dir", type=Path, default=Path("etl/preprocessing/history/processed"))
    parser.add_argument("--chunk-size", type=int, default=1200)
    parser.add_argument("--chunk-overlap", type=int, default=150)
    args = parser.parse_args()

    args.output_dir.mkdir(parents=True, exist_ok=True)
    documents = [build_document(row) for row in iter_csv_rows(args.input_dir)]
    documents = [doc for doc in documents if doc["title"] and doc["content"]]

    chunks = []
    for document in documents:
        chunks.extend(build_chunks(document, args.chunk_size, args.chunk_overlap))

    doc_count = write_jsonl(args.output_dir / "new_history.documents.jsonl", documents)
    chunk_count = write_jsonl(args.output_dir / "new_history.chunks.jsonl", chunks)

    print(f"documents={doc_count}")
    print(f"chunks={chunk_count}")


if __name__ == "__main__":
    main()
