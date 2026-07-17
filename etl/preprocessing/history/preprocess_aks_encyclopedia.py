"""Preprocess 한국민족문화대백과사전 detail JSONL for chatbot RAG."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Iterable


SOURCE_NAME = "한국민족문화대백과사전"
SOURCE_TYPE = "aks_encyclopedia"


def clean_text(value: str | None) -> str:
    text = (value or "").replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def chunk_text(text: str, chunk_size: int, overlap: int) -> list[str]:
    paragraphs = [part.strip() for part in re.split(r"\n\s*\n", clean_text(text)) if part.strip()]
    chunks: list[str] = []
    current = ""
    for paragraph in paragraphs:
        if len(paragraph) > chunk_size:
            if current:
                chunks.append(current)
                current = ""
            for start in range(0, len(paragraph), max(1, chunk_size - overlap)):
                chunks.append(paragraph[start : start + chunk_size])
            continue
        candidate = f"{current}\n\n{paragraph}".strip() if current else paragraph
        if len(candidate) <= chunk_size:
            current = candidate
        else:
            chunks.append(current)
            current = paragraph
    if current:
        chunks.append(current)
    return chunks


def iter_rows(path: Path) -> Iterable[dict]:
    decoder = json.JSONDecoder(strict=False)
    buffer = ""
    with path.open("r", encoding="utf-8") as fp:
        for line in fp:
            buffer += line
            while buffer.strip():
                candidate = buffer.lstrip()
                try:
                    row, end = decoder.raw_decode(candidate)
                except json.JSONDecodeError:
                    buffer = candidate
                    break
                yield row
                buffer = candidate[end:]
    if buffer.strip():
        raise ValueError(f"완전하지 않은 JSON 레코드가 있습니다: {path}")


def build_document(row: dict) -> dict | None:
    eid = clean_text(row.get("eid"))
    headword = clean_text(row.get("headword"))
    origin = clean_text(row.get("origin"))
    title = clean_text(row.get("headwordOrigin")) or headword
    definition = clean_text(row.get("definition"))
    summary = clean_text(row.get("summary"))
    body = clean_text(row.get("body"))
    content = body or summary or definition
    if not eid or not title or not content:
        return None
    aliases = list(dict.fromkeys(value for value in (headword, origin) if value and value != title))
    secondary_type = clean_text(row.get("secondaryType"))
    return {
        "document_id": f"aks_{eid}",
        "title": title,
        "aliases": aliases,
        "definition": definition,
        "summary": summary,
        "body": content,
        "metadata": {
            "era": clean_text(row.get("era")),
            "field": clean_text(row.get("field")),
            "primary_type": clean_text(row.get("primaryType")),
            "contents_type": clean_text(row.get("contentsType")),
            **({"aliases": aliases} if aliases else {}),
            **({"secondary_type": secondary_type} if secondary_type and secondary_type != "NONE" else {}),
        },
    }


def iter_chunks(document: dict, chunk_size: int, overlap: int) -> Iterable[dict]:
    prefix = f"제목: {document['title']}"
    if document["definition"]:
        prefix += f"\n정의: {document['definition']}"
    if document["aliases"]:
        prefix += f"\n별칭: {', '.join(document['aliases'])}"
    for index, body_chunk in enumerate(chunk_text(document["body"], chunk_size, overlap)):
        parts = [prefix]
        if index == 0 and document["summary"] and document["summary"] not in {document["definition"], document["body"]}:
            parts.append(f"요약: {document['summary']}")
        parts.append(f"본문: {body_chunk}")
        chunk_text_value = "\n".join(parts)
        yield {
            "chunk_id": f"{document['document_id']}_chunk_{index:04d}",
            "document_id": document["document_id"],
            "source_type": SOURCE_TYPE,
            "source_name": SOURCE_NAME,
            "title": document["title"],
            "chunk_index": index,
            "chunk_text": chunk_text_value,
            "token_count": len(chunk_text_value.split()),
            "metadata": document["metadata"],
        }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-file", type=Path, default=Path("etl/raw_data/한국민족문화대백과사전/articles_detail.jsonl"))
    parser.add_argument("--output-dir", type=Path, default=Path("etl/preprocessing/history/processed"))
    parser.add_argument("--chunk-size", type=int, default=1200)
    parser.add_argument("--chunk-overlap", type=int, default=150)
    args = parser.parse_args()

    args.output_dir.mkdir(parents=True, exist_ok=True)
    document_path = args.output_dir / "aks_encyclopedia.documents.jsonl"
    chunk_path = args.output_dir / "aks_encyclopedia.chunks.jsonl"
    documents = chunks = 0
    with document_path.open("w", encoding="utf-8", newline="\n") as document_fp, chunk_path.open("w", encoding="utf-8", newline="\n") as chunk_fp:
        for row in iter_rows(args.input_file):
            document = build_document(row)
            if not document:
                continue
            document_fp.write(json.dumps(document, ensure_ascii=False) + "\n")
            documents += 1
            for chunk in iter_chunks(document, args.chunk_size, args.chunk_overlap):
                chunk_fp.write(json.dumps(chunk, ensure_ascii=False) + "\n")
                chunks += 1
    print(f"documents={documents}")
    print(f"chunks={chunks}")


if __name__ == "__main__":
    main()
