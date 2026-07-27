"""Evaluate saved RRF/BGE rankings with manually confirmed document labels."""

from __future__ import annotations

import argparse
import csv
import math
from collections import defaultdict
from pathlib import Path


HERE = Path(__file__).resolve().parent
GOLDEN_DIR = HERE.parent
DEFAULT_LABELS = GOLDEN_DIR / "dataset" / "golden_relevance_labels.csv"
DEFAULT_RRF = GOLDEN_DIR / "candidates" / "golden_rrf_candidate_scores.csv"
DEFAULT_BGE = GOLDEN_DIR / "candidates" / "golden_bge_candidate_scores.csv"
DEFAULT_SUMMARY = HERE / "golden_classical_ir_summary.csv"
DEFAULT_DETAIL = HERE / "golden_classical_ir_detail.csv"


def read_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        raise SystemExit(f"파일이 없습니다: {path}")
    with path.open(newline="", encoding="utf-8-sig") as file:
        return list(csv.DictReader(file))


def labels_by_question(rows: list[dict[str, str]]) -> dict[str, dict[str, int]]:
    labels: dict[str, dict[str, int]] = defaultdict(dict)
    for row in rows:
        value = (row.get("relevance") or "").strip()
        if not value:
            continue
        try:
            relevance = int(value)
        except ValueError:
            raise SystemExit(f"relevance는 0, 1, 2 중 하나여야 합니다: {row.get('golden_id')} / {row.get('document_id')}")
        if relevance > 0:
            labels[row["golden_id"]][row["document_id"]] = relevance
    return labels


def rankings_by_question(rows: list[dict[str, str]], rank_key: str) -> dict[str, list[str]]:
    grouped: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        grouped[row["golden_id"]].append(row)
    rankings: dict[str, list[str]] = {}
    for golden_id, candidates in grouped.items():
        seen: set[str] = set()
        documents: list[str] = []
        for row in sorted(candidates, key=lambda item: int(item[rank_key])):
            document_id = row["document_id"]
            if document_id not in seen:
                seen.add(document_id)
                documents.append(document_id)
        rankings[golden_id] = documents
    return rankings


def score(ranking: list[str], labels: dict[str, int], top_k: int) -> dict[str, float]:
    selected = ranking[:top_k]
    hits = [labels.get(document_id, 0) for document_id in selected]
    binary_hits = [int(value > 0) for value in hits]
    relevant_count = len(labels)
    precision = sum(binary_hits) / top_k
    recall = sum(binary_hits) / relevant_count
    hit_rate = float(any(binary_hits))
    first_rank = next((index for index, hit in enumerate(binary_hits, 1) if hit), 0)
    reciprocal_rank = 1 / first_rank if first_rank else 0.0
    ap = sum(sum(binary_hits[:index]) / index for index, hit in enumerate(binary_hits, 1) if hit) / relevant_count
    dcg = sum((2**grade - 1) / math.log2(index + 1) for index, grade in enumerate(hits, 1))
    ideal = sorted(labels.values(), reverse=True)[:top_k]
    idcg = sum((2**grade - 1) / math.log2(index + 1) for index, grade in enumerate(ideal, 1))
    return {
        "precision_at_k": precision,
        "recall_at_k": recall,
        "hit_rate_at_k": hit_rate,
        "mrr_at_k": reciprocal_rank,
        "map_at_k": ap,
        "ndcg_at_k": dcg / idcg if idcg else 0.0,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--labels", type=Path, default=DEFAULT_LABELS)
    parser.add_argument("--rrf-csv", type=Path, default=DEFAULT_RRF)
    parser.add_argument("--bge-csv", type=Path, default=DEFAULT_BGE)
    parser.add_argument("--top-ks", default="1,3,5")
    parser.add_argument("--summary", type=Path, default=DEFAULT_SUMMARY)
    parser.add_argument("--detail", type=Path, default=DEFAULT_DETAIL)
    args = parser.parse_args()
    try:
        top_ks = tuple(sorted({int(value.strip()) for value in args.top_ks.split(",")}))
    except ValueError:
        parser.error("top-ks must be comma-separated positive integers")
    if not top_ks or top_ks[0] < 1:
        parser.error("top-ks must be positive")

    labels = labels_by_question(read_rows(args.labels))
    if not labels:
        raise SystemExit("확정된 관련 문서가 없습니다. relevance 1 또는 2를 먼저 입력하세요.")
    conditions = (
        ("rrf_before", rankings_by_question(read_rows(args.rrf_csv), "rrf_rank")),
        ("bge_after", rankings_by_question(read_rows(args.bge_csv), "bge_rank")),
    )
    details: list[dict[str, object]] = []
    for condition, rankings in conditions:
        shared_ids = sorted(set(labels) & set(rankings))
        for golden_id in shared_ids:
            for top_k in top_ks:
                details.append({"golden_id": golden_id, "condition": condition, "top_k": top_k, **score(rankings[golden_id], labels[golden_id], top_k)})
        print(f"{condition}: evaluated_questions={len(shared_ids)}")

    metric_names = ("precision_at_k", "recall_at_k", "hit_rate_at_k", "mrr_at_k", "map_at_k", "ndcg_at_k")
    summary: list[dict[str, object]] = []
    for condition, _ in conditions:
        for top_k in top_ks:
            rows = [row for row in details if row["condition"] == condition and row["top_k"] == top_k]
            summary.append({"condition": condition, "top_k": top_k, "evaluated_questions": len(rows), **{name: round(sum(float(row[name]) for row in rows) / len(rows), 4) for name in metric_names}})
    for path, rows in ((args.detail, details), (args.summary, summary)):
        with path.open("w", newline="", encoding="utf-8-sig") as file:
            writer = csv.DictWriter(file, fieldnames=rows[0].keys())
            writer.writeheader()
            writer.writerows(rows)
    for row in summary:
        print(row)
    print(f"detail: {args.detail}")
    print(f"summary: {args.summary}")


if __name__ == "__main__":
    main()
