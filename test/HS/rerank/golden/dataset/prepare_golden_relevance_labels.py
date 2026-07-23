"""Create a test-only manual relevance-label template from automatic candidates."""

from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict
from pathlib import Path


HERE = Path(__file__).resolve().parent
DEFAULT_DOCUMENTS = HERE / "golden_matched_documents_444.json"
DEFAULT_OUTPUT = HERE / "golden_relevance_labels.csv"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--documents", type=Path, default=DEFAULT_DOCUMENTS)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()
    if args.output.exists() and not args.overwrite:
        raise SystemExit(f"이미 라벨 파일이 있습니다: {args.output} (새로 만들려면 --overwrite)")

    grouped: dict[str, list[dict]] = defaultdict(list)
    for document in json.loads(args.documents.read_text(encoding="utf-8")):
        for golden_id in document.get("matched_golden_ids") or []:
            grouped[golden_id].append(document)

    fields = ["golden_id", "document_id", "chunk_id", "source_type", "source_name", "title", "document_eras", "relevance", "note"]
    with args.output.open("w", newline="", encoding="utf-8-sig") as file:
        writer = csv.DictWriter(file, fieldnames=fields)
        writer.writeheader()
        for golden_id in sorted(grouped):
            for document in grouped[golden_id]:
                writer.writerow(
                    {
                        "golden_id": golden_id,
                        "document_id": document["document_id"],
                        "chunk_id": document.get("chunk_id") or "",
                        "source_type": document.get("source_type") or "",
                        "source_name": document.get("source_name") or "",
                        "title": document.get("title") or "",
                        "document_eras": " | ".join(document.get("document_eras") or []),
                        "relevance": "",
                        "note": "",
                    }
                )
    print(f"questions: {len(grouped)}")
    print(f"candidates: {sum(map(len, grouped.values()))}")
    print(f"csv: {args.output}")
    print("relevance에 2(핵심), 1(보조), 0(비관련)을 입력하세요. 질문당 핵심 1~3개를 권장합니다.")


if __name__ == "__main__":
    main()
