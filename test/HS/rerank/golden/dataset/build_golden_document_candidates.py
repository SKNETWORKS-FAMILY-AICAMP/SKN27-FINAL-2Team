"""Label sampled documents as golden-question answer candidates.

A document can be a candidate only when its era overlaps the question's
expected era.  Era mismatch is always rejected, regardless of keyword hits.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[5]
HERE = Path(__file__).resolve().parent
DEFAULT_GOLDEN = ROOT / "etl" / "preprocessing" / "history" / "embedding" / "golden_questions.jsonl"
DEFAULT_DOCUMENTS = HERE / "random_documents_100.json"
DEFAULT_OUTPUT = HERE / "golden_document_candidates.json"

ERA_ALIASES = (
    ("조선전기", "조선 전기"),
    ("조선중기", "조선 중기"),
    ("조선후기", "조선 후기"),
    ("고려전기", "고려 전기"),
    ("고려후기", "고려 후기"),
    ("일제강점기", "일제강점기"),
    ("통일신라", "통일 신라"),
    ("고구려", "고구려"),
    ("백제", "백제"),
    ("신라", "신라"),
    ("가야", "가야"),
    ("발해", "발해"),
    ("선사", "선사"),
    ("삼국", "삼국"),
    ("고려", "고려"),
    ("조선", "조선"),
    ("개항기", "개항기"),
    ("근대", "근대"),
    ("현대", "현대"),
)
ERA_FAMILIES = {
    "조선": {"조선", "조선 전기", "조선 중기", "조선 후기"},
    "고려": {"고려", "고려 전기", "고려 후기"},
    "근대": {"근대", "개항기", "일제강점기"},
    "삼국": {"삼국", "고구려", "백제", "신라", "가야"},
}
SPECIFIC_ERAS = {"조선 전기", "조선 중기", "조선 후기", "고려 전기", "고려 후기", "개항기", "일제강점기"}


def compact(value: str) -> str:
    return re.sub(r"\s+", "", value or "")


def era_values(value: Any) -> set[str]:
    """Normalize a golden/document era to comparable, specific era labels."""
    if isinstance(value, list):
        raw = "|".join(str(item) for item in value)
    else:
        raw = str(value or "")
    normalized = compact(raw)
    labels = {label for alias, label in ERA_ALIASES if alias in normalized}
    if labels & SPECIFIC_ERAS:
        labels -= {"조선", "고려", "근대"}
    if "통일신라와발해" in normalized:
        labels.update({"통일 신라", "발해"})
    if "삼국이전" in normalized:
        labels.add("선사")
    return labels


def document_eras(document: dict[str, Any]) -> set[str]:
    metadata = document.get("metadata") or {}
    return era_values([metadata.get("era"), metadata.get("period"), metadata.get("periods")])


def era_matches(expected_era: Any, document: dict[str, Any]) -> bool:
    expected = era_values(expected_era)
    actual = document_eras(document)
    return any(
        label in actual or (label in ERA_FAMILIES and bool(ERA_FAMILIES[label] & actual))
        for label in expected
    )


def matched_keywords(question: dict[str, Any], document: dict[str, Any]) -> list[str]:
    metadata = document.get("metadata") or {}
    text = compact(" ".join(str(value) for value in (
        document.get("title", ""), document.get("chunk_text", ""), metadata.get("aliases", ""), metadata.get("keywords", ""),
    )))
    return [keyword for keyword in question.get("expected_keywords", []) if compact(keyword) in text]


def label_question(
    question: dict[str, Any], documents: list[dict[str, Any]], min_keyword_hits: int, require_all_keywords: bool = False
) -> dict[str, Any]:
    candidates = []
    era_rejected = 0
    for document in documents:
        if not era_matches(question.get("expected_era"), document):
            era_rejected += 1
            continue
        keywords = matched_keywords(question, document)
        if not keywords:
            continue
        required_hits = len(question["expected_keywords"]) if require_all_keywords else min(min_keyword_hits, len(question["expected_keywords"]))
        candidates.append({
            "document_id": document["document_id"],
            "title": document.get("title", ""),
            "document_eras": sorted(document_eras(document)),
            "matched_keywords": keywords,
            "keyword_coverage": round(len(keywords) / len(question["expected_keywords"]), 3),
            "is_answer_candidate": len(keywords) >= required_hits,
        })
    candidates.sort(key=lambda item: (-len(item["matched_keywords"]), item["title"]))
    return {
        "id": question["id"],
        "query": question["query"],
        "expected_era": question["expected_era"],
        "answer_candidate_document_ids": [item["document_id"] for item in candidates if item["is_answer_candidate"]],
        "candidates": candidates,
        "era_rejected_document_count": era_rejected,
    }


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_review_csv(labels: list[dict[str, Any]], path: Path) -> None:
    rows = []
    for label in labels:
        candidates = label["candidates"] or [{}]
        for candidate in candidates:
            rows.append({
                "golden_id": label["id"],
                "question": label["query"],
                "expected_era": " | ".join(label["expected_era"]) if isinstance(label["expected_era"], list) else label["expected_era"],
                "judgment": "정답 후보" if candidate.get("is_answer_candidate") else "시대 일치·키워드 부족",
                "document_id": candidate.get("document_id", ""),
                "document_title": candidate.get("title", "후보 없음"),
                "document_eras": " | ".join(candidate.get("document_eras", [])),
                "matched_keywords": " | ".join(candidate.get("matched_keywords", [])),
                "keyword_coverage": candidate.get("keyword_coverage", 0),
                "era_rejected_document_count": label["era_rejected_document_count"],
            })
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--golden", type=Path, default=DEFAULT_GOLDEN)
    parser.add_argument("--documents", type=Path, default=DEFAULT_DOCUMENTS)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--review-csv", type=Path, help="Excel-friendly question-by-question review table")
    parser.add_argument("--min-keyword-hits", type=int, default=2)
    parser.add_argument("--require-all-keywords", action="store_true", help="Approve only documents containing every expected keyword")
    args = parser.parse_args()
    if args.min_keyword_hits < 1:
        parser.error("--min-keyword-hits must be positive")

    questions = load_jsonl(args.golden)
    documents = json.loads(args.documents.read_text(encoding="utf-8"))
    labels = [label_question(question, documents, args.min_keyword_hits, args.require_all_keywords) for question in questions]
    args.output.write_text(json.dumps(labels, ensure_ascii=False, indent=2), encoding="utf-8")
    review_csv = args.review_csv or args.output.with_suffix(".csv")
    write_review_csv(labels, review_csv)
    answer_count = sum(len(row["answer_candidate_document_ids"]) for row in labels)
    print(f"{len(labels)} questions, {answer_count} answer candidates saved to {args.output} and {review_csv}")


if __name__ == "__main__":
    main()
