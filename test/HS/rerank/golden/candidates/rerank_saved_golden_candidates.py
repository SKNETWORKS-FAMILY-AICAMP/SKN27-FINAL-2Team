from __future__ import annotations

import argparse
import csv
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


ROOT = Path(__file__).resolve().parents[5]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")

from app.chatbot.rag.reranker import score_results


HERE = Path(__file__).resolve().parent
DEFAULT_INPUT = HERE / "golden_rrf_candidate_scores.csv"
DEFAULT_OUTPUT = HERE / "golden_bge_candidate_scores.csv"


@dataclass
class Candidate:
    row: dict[str, str]
    title: str
    chunk_text: str


def main() -> None:
    parser = argparse.ArgumentParser(description="BGE-rerank saved golden RRF candidates without querying PostgreSQL.")
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    with args.input.open(newline="", encoding="utf-8-sig") as file:
        input_rows = list(csv.DictReader(file))
    required = {"golden_id", "question", "rrf_rank", "chunk_text", "title"}
    missing = required - set(input_rows[0] if input_rows else ())
    if missing:
        raise SystemExit(f"RRF CSV에 필요한 컬럼이 없습니다: {', '.join(sorted(missing))}")

    grouped: dict[str, list[dict[str, str]]] = {}
    for row in input_rows:
        grouped.setdefault(row["golden_id"], []).append(row)

    os.environ["RAG_RERANKER_ENABLED"] = "true"
    output_rows: list[dict[str, object]] = []
    for index, (golden_id, rows) in enumerate(grouped.items(), 1):
        rows.sort(key=lambda row: int(row["rrf_rank"]))
        candidates = [Candidate(row, row["title"], row["chunk_text"]) for row in rows]
        start = time.perf_counter()
        scored = score_results(rows[0]["question"], candidates)
        if scored is None:
            raise SystemExit("BGE 리랭커를 불러오지 못했습니다. sentence-transformers와 RAG_RERANKER_ENABLED를 확인하세요.")
        ranked = sorted(scored, key=lambda item: item[1], reverse=True)
        rank = {id(candidate): position for position, (candidate, _) in enumerate(ranked, 1)}
        for candidate, score in scored:
            output_rows.append({**candidate.row, "bge_score": round(score, 6), "bge_rank": rank[id(candidate)], "rerank_sec": round(time.perf_counter() - start, 3)})
        print(f"reranked: {index}/{len(grouped)} {golden_id} candidates={len(candidates)}")

    fields = [*input_rows[0].keys(), "bge_score", "bge_rank", "rerank_sec"]
    with args.output.open("w", newline="", encoding="utf-8-sig") as file:
        writer = csv.DictWriter(file, fieldnames=fields)
        writer.writeheader()
        writer.writerows(output_rows)
    print(f"questions: {len(grouped)}")
    print(f"csv: {args.output}")


if __name__ == "__main__":
    main()
