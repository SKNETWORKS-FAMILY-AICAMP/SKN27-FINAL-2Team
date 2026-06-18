"""Preprocess '사료로 본 한국사' CSV files for chatbot RAG.

Outputs:
  - historical_sources.documents.jsonl
  - historical_sources.chunks.jsonl
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


SOURCE_NAME = "사료로 본 한국사"
SOURCE_TYPE = "historical_source"


def clean_text(value: str | None) -> str:
    if not value:
        return ""
    text = value.replace("\ufeff", "").replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def split_keywords(*values: str) -> list[str]:
    tokens: list[str] = []
    for value in values:
        for token in re.split(r"[>,/#|,\s]+", value or ""):
            token = token.strip(" -_`[]()")
            if len(token) >= 2 and token not in tokens:
                tokens.append(token)
    return tokens[:30]


def stable_id(*parts: str) -> str:
    raw = "::".join(parts)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]


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
    csv_dir = input_dir / "csv"
    files = sorted(csv_dir.glob("*.csv"))
    if not files:
        index_csv = input_dir / "index.csv"
        files = [index_csv] if index_csv.exists() else []

    for csv_path in files:
        with csv_path.open("r", encoding="utf-8-sig", newline="") as fp:
            reader = csv.DictReader(fp)
            for row in reader:
                row["_source_file"] = str(csv_path)
                yield row


def build_document(row: dict[str, str]) -> dict:
    original_id = clean_text(row.get("자료ID")) or stable_id(row.get("제목", ""), row.get("상세URL", ""))
    title = clean_text(row.get("제목"))
    period = clean_text(row.get("시대"))
    field = clean_text(row.get("분야"))
    toc_path = clean_text(row.get("목차경로"))
    korean = clean_text(row.get("국문"))
    original = clean_text(row.get("원문"))
    explanation = clean_text(row.get("해설"))
    references = clean_text(row.get("참고자료"))

    content_parts = []
    if korean:
        content_parts.append(f"[국문]\n{korean}")
    if explanation:
        content_parts.append(f"[해설]\n{explanation}")
    if original:
        content_parts.append(f"[원문]\n{original}")

    content = "\n\n".join(content_parts)
    keywords = split_keywords(title, period, field, toc_path)
    category_tags = build_category_tags(
        title=title,
        period=period,
        field=field,
        category=toc_path,
        keywords=keywords,
    )
    chronology = build_chronology(
        title=title,
        period=period,
        category=toc_path,
        content=content,
    )

    return {
        "doc_id": f"hm_{original_id}" if not original_id.startswith("hm_") else original_id,
        "source_type": SOURCE_TYPE,
        "source_name": SOURCE_NAME,
        "original_id": original_id,
        "title": title,
        "content": content,
        "summary": explanation[:500] if explanation else korean[:500],
        "period": period,
        "field": field,
        "category": toc_path,
        "keywords": keywords,
        "source_url": clean_text(row.get("상세URL")),
        "image_path": None,
        "metadata": {
            "period_code": clean_text(row.get("시대코드")),
            "field_code": clean_text(row.get("분야코드")),
            "toc_path": toc_path,
            "markdown_file": clean_text(row.get("Markdown파일")),
            "reference": references,
            "source_file": row.get("_source_file"),
            "has_original_text": bool(original),
            "has_explanation": bool(explanation),
            "category_tags": category_tags,
            "chronology": chronology,
        },
    }


def build_chunks(document: dict, chunk_size: int, overlap: int) -> list[dict]:
    sections = []
    content = document["content"]
    for name in ("국문", "해설", "원문"):
        match = re.search(rf"\[{name}\]\n(.*?)(?=\n\n\[(?:국문|해설|원문)\]\n|\Z)", content, re.S)
        if match:
            sections.append((name, match.group(1)))

    if not sections:
        sections = [("본문", content)]

    chunks = []
    chunk_index = 0
    for section_name, section_text in sections:
        for text in chunk_text(section_text, chunk_size, overlap):
            chunk_id = f"{document['doc_id']}_chunk_{chunk_index:04d}"
            chunks.append(
                {
                    "chunk_id": chunk_id,
                    "document_id": document["doc_id"],
                    "source_type": document["source_type"],
                    "source_name": document["source_name"],
                    "title": document["title"],
                    "chunk_index": chunk_index,
                    "chunk_text": text,
                    "token_count": len(text.split()),
                    "metadata": {
                        **document["metadata"],
                        "section": section_name,
                        "period": document["period"],
                        "field": document["field"],
                        "category": document["category"],
                        "keywords": document["keywords"],
                        "source_url": document["source_url"],
                    },
                }
            )
            chunk_index += 1
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
    parser.add_argument("--input-dir", type=Path, default=Path("storage/postgre/사료로 본 한국사"))
    parser.add_argument("--output-dir", type=Path, default=Path("storage/postgre/processed"))
    parser.add_argument("--chunk-size", type=int, default=1200)
    parser.add_argument("--chunk-overlap", type=int, default=150)
    args = parser.parse_args()

    args.output_dir.mkdir(parents=True, exist_ok=True)
    documents = [build_document(row) for row in iter_csv_rows(args.input_dir)]
    documents = [doc for doc in documents if doc["title"] and doc["content"]]

    chunks = []
    for document in documents:
        chunks.extend(build_chunks(document, args.chunk_size, args.chunk_overlap))

    doc_count = write_jsonl(args.output_dir / "historical_sources.documents.jsonl", documents)
    chunk_count = write_jsonl(args.output_dir / "historical_sources.chunks.jsonl", chunks)

    print(f"documents={doc_count}")
    print(f"chunks={chunk_count}")


if __name__ == "__main__":
    main()
