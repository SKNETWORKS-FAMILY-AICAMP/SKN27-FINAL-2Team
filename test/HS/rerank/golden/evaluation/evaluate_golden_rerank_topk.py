from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time
import types
from pathlib import Path

from dotenv import load_dotenv


ROOT = Path(__file__).resolve().parents[5]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")

from app.chatbot.rag.pgvector_retriever import PgSearchResult, PgVectorHybridRetriever, cached_pg_search
from app.chatbot.rag.reranker import score_results


HERE = Path(__file__).resolve().parent
GOLDEN_DIR = HERE.parent
DEFAULT_GOLDEN = GOLDEN_DIR / "dataset" / "golden_questions_strict_matched_444.jsonl"
OUT_PREFIX = HERE / "golden_rerank_topk"
CANDIDATE_CSV = GOLDEN_DIR / "candidates" / "golden_rrf_candidate_scores.csv"
BGE_CANDIDATE_CSV = GOLDEN_DIR / "candidates" / "golden_bge_candidate_scores.csv"
TOP_KS = (5, 10, 15, 20, 30, 40, 50)
SAVED_RRF_LIMIT = max(TOP_KS)
TEXT_INTENTS = {"concept", "summary", "compare", "evidence"}
METRICS = (("context_precision", "Context Precision"), ("context_recall", "Context Recall"))


def install_ragas_vertexai_compat() -> None:
    try:
        import langchain_community.chat_models.vertexai  # noqa: F401
    except ModuleNotFoundError:
        from langchain_openai import ChatOpenAI

        module = types.ModuleType("langchain_community.chat_models.vertexai")
        module.ChatVertexAI = ChatOpenAI
        sys.modules["langchain_community.chat_models.vertexai"] = module


def load_questions(path: Path, limit: int) -> list[dict]:
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    rows = [row for row in rows if not row.get("requires_image") and row.get("intent") in TEXT_INTENTS]
    return rows[:limit] if limit else rows


def collect_candidates(question: str, candidate_pool: int, rerank_pool: int) -> tuple[list[PgSearchResult], list[PgSearchResult], list[tuple[PgSearchResult, float]], float, float]:
    os.environ["RAG_RERANKER_ENABLED"] = "false"
    cached_pg_search.cache_clear()
    start = time.perf_counter()
    rrf = PgVectorHybridRetriever(candidate_pool=candidate_pool, rerank_pool=rerank_pool).search(question, top_k=rerank_pool)
    retrieval_sec = time.perf_counter() - start

    os.environ["RAG_RERANKER_ENABLED"] = "true"
    start = time.perf_counter()
    scored = score_results(question, rrf)
    rerank_sec = time.perf_counter() - start
    if scored is None:
        raise RuntimeError("BGE 리랭커를 불러오지 못했습니다.")
    ranked = sorted(scored, key=lambda item: item[1], reverse=True)
    bge = [result for result, _ in ranked]
    return rrf, bge, ranked, retrieval_sec, rerank_sec


def make_rows(question: dict, rrf: list[PgSearchResult], bge: list[PgSearchResult], retrieval_sec: float, rerank_sec: float, top_ks: tuple[int, ...]) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for top_k in top_ks:
        for condition, results in (("rrf_before", rrf), ("bge_after", bge)):
            selected = results[:top_k]
            rows.append(
                {
                    "id": question["id"],
                    "question": question["query"],
                    "condition": condition,
                    "top_k": top_k,
                    "retrieval_sec": round(retrieval_sec, 3),
                    "rerank_sec": round(rerank_sec if condition == "bge_after" else 0.0, 3),
                    "reference": question.get("reference_answer") or ", ".join(question.get("expected_keywords") or []),
                    "contexts": [result.chunk_text for result in selected],
                    "titles": [result.title for result in selected],
                }
            )
    return rows


def write_candidate_rows(path: Path, question: dict, results: list[PgSearchResult], retrieval_sec: float) -> None:
    fields = ["golden_id", "question", "rrf_rank", "rrf_score", "vector_score", "keyword_score", "chunk_id", "document_id", "source_type", "source_name", "title", "chunk_text", "retrieval_sec"]
    write_header = not path.exists()
    with path.open("a", newline="", encoding="utf-8-sig") as file:
        writer = csv.DictWriter(file, fieldnames=fields)
        if write_header:
            writer.writeheader()
        for rank, result in enumerate(results, 1):
            writer.writerow(
                {
                    "golden_id": question["id"],
                    "question": question["query"],
                    "rrf_rank": rank,
                    "rrf_score": round(result.score, 6),
                    "vector_score": round(result.vector_score, 6),
                    "keyword_score": round(result.keyword_score, 6),
                    "chunk_id": result.chunk_id,
                    "document_id": result.document_id,
                    "source_type": result.source_type,
                    "source_name": result.source_name,
                    "title": result.title,
                    "chunk_text": result.chunk_text,
                    "retrieval_sec": round(retrieval_sec, 3),
                }
            )


def write_bge_candidate_rows(path: Path, question: dict, ranked: list[tuple[PgSearchResult, float]], rerank_sec: float) -> None:
    fields = ["golden_id", "question", "bge_rank", "bge_score", "rrf_score", "vector_score", "keyword_score", "chunk_id", "document_id", "source_type", "source_name", "title", "chunk_text", "rerank_sec"]
    write_header = not path.exists()
    with path.open("a", newline="", encoding="utf-8-sig") as file:
        writer = csv.DictWriter(file, fieldnames=fields)
        if write_header:
            writer.writeheader()
        for rank, (result, bge_score) in enumerate(ranked, 1):
            writer.writerow(
                {
                    "golden_id": question["id"],
                    "question": question["query"],
                    "bge_rank": rank,
                    "bge_score": round(bge_score, 6),
                    "rrf_score": round(result.score, 6),
                    "vector_score": round(result.vector_score, 6),
                    "keyword_score": round(result.keyword_score, 6),
                    "chunk_id": result.chunk_id,
                    "document_id": result.document_id,
                    "source_type": result.source_type,
                    "source_name": result.source_name,
                    "title": result.title,
                    "chunk_text": result.chunk_text,
                    "rerank_sec": round(rerank_sec, 3),
                }
            )


def score_rows(rows: list[dict[str, object]]) -> list[dict[str, object]]:
    # ponytail: 490 RAGAS 행은 LangSmith trace 한도를 넘으므로 이 배치 평가는 로컬 CSV만 남긴다.
    os.environ["LANGSMITH_TRACING"] = "false"
    os.environ["LANGCHAIN_TRACING_V2"] = "false"
    install_ragas_vertexai_compat()
    from datasets import Dataset
    from langchain_openai import ChatOpenAI
    from ragas import evaluate
    from ragas.llms import LangchainLLMWrapper
    from ragas.metrics import context_precision, context_recall

    dataset = Dataset.from_list(
        [{"user_input": row["question"], "retrieved_contexts": row["contexts"], "reference": row["reference"]} for row in rows]
    )
    evaluator = LangchainLLMWrapper(ChatOpenAI(model=os.getenv("RAGAS_LLM_MODEL") or "gpt-4o-mini", temperature=0))
    result = evaluate(dataset, metrics=[context_precision, context_recall], llm=evaluator, show_progress=False, batch_size=1)
    for row, score in zip(rows, result.scores):
        for name, _ in METRICS:
            row[name] = score.get(name)
    return rows


def write_outputs(rows: list[dict[str, object]], top_ks: tuple[int, ...]) -> None:
    samples_path = OUT_PREFIX.with_name(f"{OUT_PREFIX.name}_samples.csv")
    summary_path = OUT_PREFIX.with_name(f"{OUT_PREFIX.name}_summary.csv")
    fields = ["id", "question", "condition", "top_k", "retrieval_sec", "rerank_sec", *(name for name, _ in METRICS), "reference", "titles"]
    with samples_path.open("w", newline="", encoding="utf-8-sig") as file:
        writer = csv.DictWriter(file, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: " | ".join(row["titles"]) if field == "titles" else row.get(field) for field in fields})

    summary = []
    for top_k in top_ks:
        for condition in ("rrf_before", "bge_after"):
            selected = [row for row in rows if row["top_k"] == top_k and row["condition"] == condition]
            summary.append(
                {
                    "top_k": top_k,
                    "condition": condition,
                    "question_count": len(selected),
                    **{name: round(sum(float(row[name]) for row in selected) / len(selected), 4) for name, _ in METRICS},
                }
            )
    with summary_path.open("w", newline="", encoding="utf-8-sig") as file:
        writer = csv.DictWriter(file, fieldnames=summary[0].keys())
        writer.writeheader()
        writer.writerows(summary)
    OUT_PREFIX.with_suffix(".svg").write_text(build_svg(summary, top_ks), encoding="utf-8")
    print(f"samples_csv: {samples_path}")
    print(f"summary_csv: {summary_path}")
    print(f"svg: {OUT_PREFIX.with_suffix('.svg')}")


def build_svg(summary: list[dict[str, object]], top_ks: tuple[int, ...]) -> str:
    lookup = {(row["condition"], row["top_k"]): row for row in summary}
    panels = []
    for index, (metric, title) in enumerate(METRICS):
        left, top, width, height = 80 + index * 510, 100, 390, 220
        x = lambda value: left + top_ks.index(value) / (len(top_ks) - 1) * width
        y = lambda value: top + height - value * height
        paths = []
        for condition, css, offset in (("rrf_before", "before", -8), ("bge_after", "after", 16)):
            values = [float(lookup[(condition, top_k)][metric]) for top_k in top_ks]
            points = [(x(top_k), y(value)) for top_k, value in zip(top_ks, values)]
            point_text = " ".join(f"{px:.1f},{py:.1f}" for px, py in points)
            paths.append(f'<polyline points="{point_text}" class="{css}"/>')
            paths.extend(f'<circle cx="{px:.1f}" cy="{py:.1f}" r="3" class="{css}"/><text x="{px:.1f}" y="{py+offset:.1f}" text-anchor="middle" class="value {css}-label">{value:.2f}</text>' for (px, py), value in zip(points, values))
        grid = "".join(f'<line x1="{left}" y1="{y(value):.1f}" x2="{left+width}" y2="{y(value):.1f}" class="grid"/>' for value in (0, 0.4, 0.8, 1))
        ticks = "".join(f'<text x="{x(top_k):.1f}" y="{top+height+20}" text-anchor="middle" class="axis">{top_k}</text>' for top_k in top_ks)
        panels.append(f'<text x="{left}" y="{top-12}" class="panel-title">{title}</text>{grid}<line x1="{left}" y1="{y(.8):.1f}" x2="{left+width}" y2="{y(.8):.1f}" class="threshold"/><line x1="{left}" y1="{top+height}" x2="{left+width}" y2="{top+height}" class="axis-line"/>{"".join(paths)}{ticks}')
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="1040" height="420" viewBox="0 0 1040 420" role="img" aria-label="Golden questions RRF and BGE context metrics by top-k">
<style>text {{ font-family: Arial, sans-serif; fill: #202124; }} .title {{ font-size: 20px; font-weight: bold; }} .panel-title {{ font-size: 16px; font-weight: bold; }} .axis {{ font-size: 12px; fill: #5f6368; }} .value {{ font-size: 11px; }} .before-label {{ fill: #5f6368; }} .after-label {{ fill: #166b8f; }} .grid {{ stroke: #dfe3e7; }} .axis-line {{ stroke: #697277; }} .threshold {{ stroke: #c04b30; stroke-dasharray: 5 4; }} .before {{ fill: none; stroke: #73818a; stroke-width: 2; }} circle.before {{ fill: #73818a; }} .after {{ fill: none; stroke: #166b8f; stroke-width: 2; }} circle.after {{ fill: #166b8f; }}</style>
<text x="80" y="35" class="title">Golden questions: RRF vs BGE context quality by final top-k</text><line x1="80" y1="63" x2="104" y2="63" class="before"/><text x="112" y="67" class="axis">RRF top-k</text><line x1="210" y1="63" x2="234" y2="63" class="after"/><text x="242" y="67" class="axis">BGE top-k</text><text x="80" y="385" class="axis">평균 점수 · 점선: 통과 기준 0.80 · 이미지·학습팁 질문 제외</text>{''.join(panels)}</svg>'''


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare RRF and BGE Context Precision/Recall over golden questions.")
    parser.add_argument("--golden-file", type=Path, default=DEFAULT_GOLDEN)
    parser.add_argument("--limit", type=int, default=0, help="0 means all eligible golden questions.")
    parser.add_argument("--candidate-pool", type=int, default=1000)
    parser.add_argument("--rerank-pool", type=int, default=50)
    parser.add_argument("--candidate-out", type=Path, default=CANDIDATE_CSV)
    parser.add_argument("--bge-candidate-out", type=Path, default=BGE_CANDIDATE_CSV)
    args = parser.parse_args()
    if args.rerank_pool < max(TOP_KS):
        parser.error("rerank-pool must be at least 50")

    questions = load_questions(args.golden_file, args.limit)
    print(f"eligible_questions: {len(questions)}")
    args.candidate_out.unlink(missing_ok=True)
    args.bge_candidate_out.unlink(missing_ok=True)
    rows: list[dict[str, object]] = []
    for index, question in enumerate(questions, 1):
        rrf, bge, ranked, retrieval_sec, rerank_sec = collect_candidates(question["query"], args.candidate_pool, args.rerank_pool)
        if min(len(rrf), len(bge)) < max(TOP_KS):
            print(f"skipped: {question['id']} (candidates={min(len(rrf), len(bge))})")
            continue
        write_candidate_rows(args.candidate_out, question, rrf[:SAVED_RRF_LIMIT], retrieval_sec)
        write_bge_candidate_rows(args.bge_candidate_out, question, ranked, rerank_sec)
        rows.extend(make_rows(question, rrf, bge, retrieval_sec, rerank_sec, TOP_KS))
        print(f"collected: {index}/{len(questions)} {question['id']} retrieval={retrieval_sec:.2f}s rerank={rerank_sec:.2f}s")
    if not rows:
        raise SystemExit("평가 가능한 후보가 없습니다.")
    rows = score_rows(rows)
    write_outputs(rows, TOP_KS)
    print(f"rrf_candidate_csv: {args.candidate_out}")
    print(f"bge_candidate_csv: {args.bge_candidate_out}")


if __name__ == "__main__":
    main()
