from __future__ import annotations

import argparse
import csv
import json
import sys
from dataclasses import dataclass
from pathlib import Path

CURRENT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_PROCESSED_DIR = PROJECT_ROOT / "etl" / "preprocessing" / "history" / "processed"
for path in (CURRENT_DIR, PROJECT_ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from app.chatbot.rag.rag_prototype.config import RagPaths
from app.chatbot.rag.rag_prototype.retriever import HybridRagRetriever, SearchResult


DEFAULT_GOLDEN_PATH = CURRENT_DIR / "golden_questions.jsonl"


@dataclass
class EvaluationResult:
    question_id: str
    query: str
    passed: bool
    keyword_hit: bool
    source_type_hit: bool
    era_hit: bool
    image_hit: bool
    reciprocal_rank: float
    top_title: str
    top_source_type: str
    matched_keywords: list[str]
    top_results: list[dict]


def load_jsonl(path: Path) -> list[dict]:
    rows = []
    with path.open("r", encoding="utf-8") as fp:
        for line in fp:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def normalize_text(value: str | None) -> str:
    return "".join((value or "").lower().split())


def result_text(result: SearchResult) -> str:
    metadata = result.document.metadata
    parts = [
        result.document.title,
        result.document.chunk_text,
        " ".join(metadata.get("keywords") or []),
        " ".join(metadata.get("category_tags") or []),
        str(metadata.get("category") or ""),
        str(metadata.get("field") or ""),
    ]
    chronology = metadata.get("chronology") or {}
    parts.extend(
        str(value)
        for value in (
            chronology.get("era"),
            chronology.get("dynasty"),
            chronology.get("period_label"),
        )
        if value
    )
    return normalize_text(" ".join(parts))


def has_image_url(result: SearchResult) -> bool:
    metadata = result.document.metadata
    return bool(metadata.get("thumbnail_url") or metadata.get("original_image_url"))


def list_value(value: str | list[str]) -> list[str]:
    if isinstance(value, list):
        return [item for item in value if item]
    return [value] if value else []


def find_reciprocal_rank(question: dict, results: list[SearchResult]) -> tuple[float, list[str]]:
    expected_keywords = question.get("expected_keywords") or []
    matched_keywords: list[str] = []
    for index, result in enumerate(results, start=1):
        text = result_text(result)
        hits = [keyword for keyword in expected_keywords if normalize_text(keyword) in text]
        if hits:
            for keyword in hits:
                if keyword not in matched_keywords:
                    matched_keywords.append(keyword)
            return 1.0 / index, matched_keywords
    return 0.0, matched_keywords


def evaluate_question(question: dict, retriever: HybridRagRetriever, top_k: int) -> EvaluationResult:
    results = retriever.search(question["query"], top_k=top_k)
    expected_keywords = question.get("expected_keywords") or []
    expected_source_types = list_value(question.get("expected_source_type") or "")
    expected_eras = list_value(question.get("expected_era") or "")
    requires_image = bool(question.get("requires_image"))

    all_text = " ".join(result_text(result) for result in results)
    matched_keywords = [keyword for keyword in expected_keywords if normalize_text(keyword) in all_text]
    keyword_hit = bool(matched_keywords)
    source_type_hit = any(result.document.source_type in expected_source_types for result in results) if expected_source_types else True
    era_hit = any(normalize_text(expected_era) in all_text for expected_era in expected_eras) if expected_eras else True
    image_hit = True if not requires_image else any(
        result.document.source_type == "image_material" and has_image_url(result)
        for result in results
    )
    reciprocal_rank, rr_keywords = find_reciprocal_rank(question, results)
    if rr_keywords:
        matched_keywords = sorted(set(matched_keywords + rr_keywords), key=(matched_keywords + rr_keywords).index)

    passed = keyword_hit and source_type_hit and era_hit and image_hit
    top_result = results[0] if results else None
    return EvaluationResult(
        question_id=question["id"],
        query=question["query"],
        passed=passed,
        keyword_hit=keyword_hit,
        source_type_hit=source_type_hit,
        era_hit=era_hit,
        image_hit=image_hit,
        reciprocal_rank=reciprocal_rank,
        top_title=top_result.document.title if top_result else "",
        top_source_type=top_result.document.source_type if top_result else "",
        matched_keywords=matched_keywords,
        top_results=[
            {
                "rank": index,
                "title": result.document.title,
                "source_type": result.document.source_type,
                "score": round(result.score, 4),
                "source_url": result.document.metadata.get("source_url"),
                "thumbnail_url": result.document.metadata.get("thumbnail_url"),
                "original_image_url": result.document.metadata.get("original_image_url"),
            }
            for index, result in enumerate(results, start=1)
        ],
    )


def write_csv(path: Path, results: list[EvaluationResult]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as fp:
        writer = csv.DictWriter(
            fp,
            fieldnames=[
                "id",
                "passed",
                "keyword_hit",
                "source_type_hit",
                "era_hit",
                "image_hit",
                "reciprocal_rank",
                "query",
                "top_title",
                "top_source_type",
                "matched_keywords",
            ],
        )
        writer.writeheader()
        for result in results:
            writer.writerow(
                {
                    "id": result.question_id,
                    "passed": result.passed,
                    "keyword_hit": result.keyword_hit,
                    "source_type_hit": result.source_type_hit,
                    "era_hit": result.era_hit,
                    "image_hit": result.image_hit,
                    "reciprocal_rank": f"{result.reciprocal_rank:.4f}",
                    "query": result.query,
                    "top_title": result.top_title,
                    "top_source_type": result.top_source_type,
                    "matched_keywords": ", ".join(result.matched_keywords),
                }
            )


def write_json(path: Path, results: list[EvaluationResult]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = [
        {
            "id": result.question_id,
            "query": result.query,
            "passed": result.passed,
            "keyword_hit": result.keyword_hit,
            "source_type_hit": result.source_type_hit,
            "era_hit": result.era_hit,
            "image_hit": result.image_hit,
            "reciprocal_rank": result.reciprocal_rank,
            "matched_keywords": result.matched_keywords,
            "top_results": result.top_results,
        }
        for result in results
    ]
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Evaluate RAG retriever with golden questions")
    parser.add_argument("--golden-file", type=Path, default=DEFAULT_GOLDEN_PATH)
    parser.add_argument("--processed-dir", type=Path, default=None)
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--csv-out", type=Path, default=CURRENT_DIR / "eval_results.csv")
    parser.add_argument("--json-out", type=Path, default=CURRENT_DIR / "eval_results.json")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    paths = RagPaths(processed_dir=args.processed_dir or DEFAULT_PROCESSED_DIR)
    retriever = HybridRagRetriever(paths=paths, top_k=args.top_k)
    questions = load_jsonl(args.golden_file)
    if args.limit:
        questions = questions[: args.limit]

    results = [evaluate_question(question, retriever, args.top_k) for question in questions]
    total = len(results)
    passed = sum(result.passed for result in results)
    recall_at_k = passed / total if total else 0.0
    mrr = sum(result.reciprocal_rank for result in results) / total if total else 0.0

    write_csv(args.csv_out, results)
    write_json(args.json_out, results)

    print(f"questions={total}")
    print(f"passed={passed}")
    print(f"recall@{args.top_k}={recall_at_k:.4f}")
    print(f"mrr={mrr:.4f}")
    print(f"csv_out={args.csv_out}")
    print(f"json_out={args.json_out}")

    failed = [result for result in results if not result.passed]
    if failed:
        print("failed_ids=" + ",".join(result.question_id for result in failed))


if __name__ == "__main__":
    main()
