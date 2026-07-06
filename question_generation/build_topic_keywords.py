from __future__ import annotations

import argparse
import json
import os
from collections import Counter
from pathlib import Path
from typing import Any

import pandas as pd
from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = PROJECT_ROOT / "question_generation" / "outputs" / "topic_keywords_seed.csv"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build topic keyword seed CSV from PDFs and textbook terms.")
    parser.add_argument("--pdf", action="append", type=Path, required=True, help="Source PDF path. Repeatable.")
    parser.add_argument("--term-csv", type=Path, default=None, help="Textbook terms CSV path.")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--top-n", type=int, default=300)
    parser.add_argument("--min-hits", type=int, default=1)
    parser.add_argument("--neo4j", action="store_true", help="Enrich matched terms with Neo4j theme/era/entity metadata.")
    return parser.parse_args()


def discover_term_csv() -> Path:
    paths = sorted((PROJECT_ROOT / "etl" / "raw_data").rglob("textbook_terms.csv"))
    if not paths:
        raise FileNotFoundError("Could not find etl/raw_data/**/textbook_terms.csv")
    return paths[0]


def extract_pdf_text(path: Path) -> str:
    try:
        import fitz  # type: ignore

        with fitz.open(path) as doc:
            return "\n".join(page.get_text("text") for page in doc)
    except Exception:
        import pdfplumber

        chunks: list[str] = []
        with pdfplumber.open(path) as pdf:
            for page in pdf.pages:
                chunks.append(page.extract_text() or "")
        return "\n".join(chunks)


def compact_source_name(path: Path) -> str:
    stem = path.stem
    return "".join(ch if ch.isalnum() else "_" for ch in stem)[:48]


def count_terms(term_names: list[str], pdf_paths: list[Path]) -> tuple[pd.DataFrame, dict[str, Any]]:
    source_texts = {path: extract_pdf_text(path) for path in pdf_paths}
    source_columns = {path: f"hits_{compact_source_name(path)}" for path in pdf_paths}
    rows: list[dict[str, Any]] = []

    for term in term_names:
        counts = {source_columns[path]: source_texts[path].count(term) for path in pdf_paths}
        total = sum(counts.values())
        source_count = sum(1 for value in counts.values() if value > 0)
        rows.append(
            {
                "topic": term,
                "pdf_hit_total": total,
                "pdf_source_count": source_count,
                "pdf_score": total + source_count * 20,
                **counts,
            }
        )

    summary = {
        "pdfs": [
            {
                "path": str(path),
                "chars_extracted": len(source_texts[path]),
                "count_column": source_columns[path],
            }
            for path in pdf_paths
        ]
    }
    return pd.DataFrame(rows), summary


def neo4j_config() -> tuple[str, str, str] | None:
    load_dotenv(PROJECT_ROOT / ".env")
    password = os.getenv("NEO4J_PASSWORD", "")
    if not password:
        return None
    uri = os.getenv("NEO4J_URI") or f"bolt://localhost:{os.getenv('NEO4J_BOLT_PORT', '7687')}"
    user = os.getenv("NEO4J_USER", "neo4j")
    return uri, user, password


def enrich_with_neo4j(term_names: list[str]) -> dict[str, dict[str, str]]:
    config = neo4j_config()
    if not config:
        return {}

    from neo4j import GraphDatabase

    uri, user, password = config
    with GraphDatabase.driver(uri, auth=(user, password)) as driver:
        with driver.session() as session:
            labels = {row["label"] for row in session.run("CALL db.labels() YIELD label RETURN label")}
            if {"Term", "Theme", "Era"}.issubset(labels):
                rows = list(session.run(MODERN_META_QUERY, names=term_names))
            elif "TermName" in labels:
                rows = list(session.run(TERM_NAME_META_QUERY, names=term_names))
            else:
                return {}

    result: dict[str, dict[str, str]] = {}
    for row in rows:
        categories = [value for value in row.get("categories", []) if value]
        paths = [value for value in row.get("paths", []) if value]
        entity_types = [value for value in row.get("entity_types", []) if value]
        themes = [value for value in row.get("themes", []) if value]
        eras = [value for value in row.get("eras", []) if value]
        result[row["name"]] = {
            "topic_type": infer_topic_type(entity_types, themes, categories, paths),
            "neo4j_entity_types": "|".join(entity_types),
            "neo4j_themes": "|".join(themes),
            "neo4j_eras": "|".join(eras),
        }
    return result


MODERN_META_QUERY = """
UNWIND $names AS name
MATCH (t:Term {term_name: name})
OPTIONAL MATCH (t)-[:HAS_THEME]->(theme:Theme)
OPTIONAL MATCH (t)-[:IN_ERA]->(era:Era)
OPTIONAL MATCH (t)-[:HAS_ENTITY_TYPE]->(entity:EntityType)
WITH name,
     collect(DISTINCT theme.name)[0..8] AS themes,
     collect(DISTINCT era.name)[0..8] AS eras,
     collect(DISTINCT entity.name)[0..8] AS entity_types
RETURN name, themes, eras, entity_types, [] AS categories, [] AS paths
"""


TERM_NAME_META_QUERY = """
UNWIND $names AS name
MATCH (t:TermName {term_name: name})
OPTIONAL MATCH (t)-[:IN_PERIOD]->(period:TermTimes)
OPTIONAL MATCH (path:TermLink)-[:HAS_TERM]->(t)
WITH name,
     collect(DISTINCT period.name)[0..8] AS eras,
     collect(DISTINCT path.name)[0..8] AS categories,
     collect(DISTINCT path.value)[0..8] AS paths
RETURN name, [] AS themes, eras, [] AS entity_types, categories, paths
"""


def infer_topic_type(entity_types: list[str], themes: list[str], categories: list[str], paths: list[str]) -> str:
    if entity_types:
        return entity_types[0]
    if themes:
        return themes[0]

    text = "|".join(categories + paths)
    for needle, topic_type in [
        ("인명", "인물"),
        ("인물", "인물"),
        ("유물", "문화재"),
        ("유적", "문화재"),
        ("문화재", "문화재"),
        ("지명", "장소"),
        ("서명", "문헌"),
        ("문헌", "문헌"),
        ("사건", "사건"),
        ("제도", "제도"),
        ("단체", "단체"),
        ("사상", "사상·종교"),
        ("종교", "사상·종교"),
        ("행사", "행사"),
        ("작품", "문화"),
    ]:
        if needle in text:
            return topic_type
    return "기타"


def main() -> None:
    args = parse_args()
    pdf_paths = [path.resolve() for path in args.pdf]
    missing = [str(path) for path in pdf_paths if not path.exists()]
    if missing:
        raise FileNotFoundError(f"Missing PDFs: {missing}")

    term_csv = args.term_csv.resolve() if args.term_csv else discover_term_csv()
    terms = pd.read_csv(term_csv)
    terms = terms[["term_id", "term_name", "era"]].dropna(subset=["term_name"]).drop_duplicates("term_name")
    terms["term_name"] = terms["term_name"].astype(str).str.strip()
    terms = terms[terms["term_name"].str.len() >= 2]
    term_names = terms["term_name"].astype(str).tolist()

    counts, summary = count_terms(term_names, pdf_paths)
    merged = terms.rename(columns={"term_name": "topic", "era": "source_era"}).merge(counts, on="topic", how="inner")
    merged = merged[merged["pdf_hit_total"] >= args.min_hits].copy()
    merged = merged.sort_values(["pdf_score", "pdf_source_count", "pdf_hit_total", "topic"], ascending=[False, False, False, True])
    merged.insert(0, "rank", range(1, len(merged) + 1))

    if args.neo4j and not merged.empty:
        meta = enrich_with_neo4j(merged["topic"].head(args.top_n).astype(str).tolist())
        for column in ["topic_type", "neo4j_entity_types", "neo4j_themes", "neo4j_eras"]:
            merged[column] = merged["topic"].map(lambda value: meta.get(value, {}).get(column, ""))
        merged["topic_type"] = merged["topic_type"].replace("", "기타")
    else:
        merged["topic_type"] = "기타"
        merged["neo4j_entity_types"] = ""
        merged["neo4j_themes"] = ""
        merged["neo4j_eras"] = ""

    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    final = merged.head(args.top_n)
    final.to_csv(output, index=False, encoding="utf-8-sig")

    summary.update(
        {
            "term_csv": str(term_csv),
            "total_terms": int(len(terms)),
            "matched_terms": int(len(merged)),
            "written_terms": int(len(final)),
            "output": str(output),
            "top_topic_type_counts": Counter(final["topic_type"]).most_common(),
        }
    )
    summary_path = output.with_name(output.stem + "_summary.json")
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
