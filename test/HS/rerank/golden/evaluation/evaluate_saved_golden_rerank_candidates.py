from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import types
from collections import defaultdict
from pathlib import Path

try:
    from dotenv import load_dotenv
except ModuleNotFoundError:
    def load_dotenv(*_args: object, **_kwargs: object) -> bool:
        return False


# ponytail: standalone so the same file runs in the project and Colab /content.
load_dotenv(Path(__file__).resolve().with_name(".env"))
load_dotenv(Path.cwd() / ".env")

HERE = Path(__file__).resolve().parent
GOLDEN_DIR = HERE.parent
DEFAULT_GOLDEN = GOLDEN_DIR / "dataset" / "golden_questions_strict_matched_444.jsonl"
DEFAULT_RRF = GOLDEN_DIR / "candidates" / "golden_rrf_candidate_scores.csv"
DEFAULT_BGE = GOLDEN_DIR / "candidates" / "golden_bge_candidate_scores.csv"
DEFAULT_OUTPUT = HERE / "golden_saved_rerank_ab_results.csv"
DEFAULT_SVG = HERE / "golden_saved_rerank_ab_evaluation.svg"
TOP_KS = (5, 10, 15, 20, 30, 40, 50)
METRICS = (("context_precision", "Context Precision"), ("context_recall", "Context Recall"))


def install_ragas_vertexai_compat() -> None:
    try:
        import langchain_community.chat_models.vertexai  # noqa: F401
    except ModuleNotFoundError:
        from langchain_openai import ChatOpenAI

        module = types.ModuleType("langchain_community.chat_models.vertexai")
        module.ChatVertexAI = ChatOpenAI
        sys.modules["langchain_community.chat_models.vertexai"] = module


def read_csv(path: Path) -> dict[str, list[dict[str, str]]]:
    if not path.exists():
        raise SystemExit(f"파일이 없습니다: {path}")
    with path.open(newline="", encoding="utf-8-sig") as file:
        rows = list(csv.DictReader(file))
    required = {"golden_id", "question", "chunk_text", "title"}
    missing = required - set(rows[0] if rows else ())
    if missing:
        raise SystemExit(f"{path.name}에 필요한 컬럼이 없습니다: {', '.join(sorted(missing))}")
    grouped: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        grouped[row["golden_id"]].append(row)
    return grouped


def read_references(path: Path) -> dict[str, str]:
    if not path.exists():
        raise SystemExit(f"골든 질문 파일이 없습니다: {path}")
    return {
        row["id"]: row.get("reference_answer") or ", ".join(row.get("expected_keywords") or [])
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
        for row in (json.loads(line),)
    }


def prepare_rows(
    rrf: dict[str, list[dict[str, str]]],
    bge: dict[str, list[dict[str, str]]],
    references: dict[str, str],
    top_ks: tuple[int, ...],
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    shared_ids = [golden_id for golden_id in references if golden_id in rrf and golden_id in bge]
    missing = set(references) - set(shared_ids)
    if missing:
        print(f"skipped_missing_candidates: {len(missing)}")
    for golden_id in shared_ids:
        before = sorted(rrf[golden_id], key=lambda row: int(row["rrf_rank"]))
        after = sorted(bge[golden_id], key=lambda row: int(row["bge_rank"]))
        if min(len(before), len(after)) < max(top_ks):
            print(f"skipped_short_candidates: {golden_id}")
            continue
        for top_k in top_ks:
            for condition, candidates in (("rrf_before", before), ("bge_after", after)):
                selected = candidates[:top_k]
                rows.append(
                    {
                        "golden_id": golden_id,
                        "question": selected[0]["question"],
                        "condition": condition,
                        "top_k": top_k,
                        "reference": references[golden_id],
                        "contexts": [row["chunk_text"] for row in selected],
                        "titles": [row["title"] for row in selected],
                    }
                )
    if not rows:
        raise SystemExit("평가 가능한 RRF/BGE 후보 쌍이 없습니다.")
    return rows


def score_rows(rows: list[dict[str, object]]) -> None:
    # ponytail: 40 questions x 14 variants can exceed LangSmith trace quota.
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
    llm = LangchainLLMWrapper(ChatOpenAI(model=os.getenv("RAGAS_LLM_MODEL") or "gpt-4o-mini", temperature=0))
    result = evaluate(dataset, metrics=[context_precision, context_recall], llm=llm, show_progress=True, batch_size=1)
    for row, score in zip(rows, result.scores):
        for name, _ in METRICS:
            row[name] = score.get(name)


def summarize(rows: list[dict[str, object]], top_ks: tuple[int, ...]) -> list[dict[str, object]]:
    summary: list[dict[str, object]] = []
    for top_k in top_ks:
        for condition in ("rrf_before", "bge_after"):
            selected = [row for row in rows if row["top_k"] == top_k and row["condition"] == condition]
            summary.append(
                {
                    "top_k": top_k,
                    "condition": condition,
                    "question_count": len(selected),
                    **{name: round(sum(float(row[name] or 0) for row in selected) / len(selected), 4) for name, _ in METRICS},
                }
            )
    return summary


def build_svg(summary: list[dict[str, object]], top_ks: tuple[int, ...], count: int) -> str:
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
            paths.append(f'<polyline points="{" ".join(f"{px:.1f},{py:.1f}" for px, py in points)}" class="{css}"/>')
            paths.extend(f'<circle cx="{px:.1f}" cy="{py:.1f}" r="3" class="{css}"/><text x="{px:.1f}" y="{py + offset:.1f}" text-anchor="middle" class="value {css}-label">{value:.2f}</text>' for (px, py), value in zip(points, values))
        grid = "".join(f'<line x1="{left}" y1="{y(value):.1f}" x2="{left + width}" y2="{y(value):.1f}" class="grid"/>' for value in (0, .4, .8, 1))
        ticks = "".join(f'<text x="{x(top_k):.1f}" y="{top + height + 20}" text-anchor="middle" class="axis">{top_k}</text>' for top_k in top_ks)
        panels.append(f'<text x="{left}" y="{top - 12}" class="panel-title">{title}</text>{grid}<line x1="{left}" y1="{y(.8):.1f}" x2="{left + width}" y2="{y(.8):.1f}" class="threshold"/><line x1="{left}" y1="{top + height}" x2="{left + width}" y2="{top + height}" class="axis-line"/>{"".join(paths)}{ticks}')
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="1040" height="420" viewBox="0 0 1040 420">
<style>text {{ font-family: Arial, sans-serif; fill: #202124; }} .title {{ font-size: 20px; font-weight: bold; }} .panel-title {{ font-size: 16px; font-weight: bold; }} .axis {{ font-size: 12px; fill: #5f6368; }} .value {{ font-size: 11px; }} .before-label {{ fill: #5f6368; }} .after-label {{ fill: #166b8f; }} .grid {{ stroke: #dfe3e7; }} .axis-line {{ stroke: #697277; }} .threshold {{ stroke: #c04b30; stroke-dasharray: 5 4; }} .before {{ fill: none; stroke: #73818a; stroke-width: 2; }} circle.before {{ fill: #73818a; }} .after {{ fill: none; stroke: #166b8f; stroke-width: 2; }} circle.after {{ fill: #166b8f; }}</style>
<text x="80" y="35" class="title">Golden questions: RRF vs BGE context quality by final top-k</text><line x1="80" y1="63" x2="104" y2="63" class="before"/><text x="112" y="67" class="axis">RRF top-k</text><line x1="210" y1="63" x2="234" y2="63" class="after"/><text x="242" y="67" class="axis">BGE top-k</text><text x="80" y="385" class="axis">{count}개 질문 평균 · 점선: 통과 기준 0.80 · 저장된 후보만 사용</text>{"".join(panels)}</svg>'''


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate saved RRF/BGE candidates for every golden question without DB retrieval.")
    parser.add_argument("--golden-file", type=Path, default=DEFAULT_GOLDEN)
    parser.add_argument("--rrf-csv", type=Path, default=DEFAULT_RRF)
    parser.add_argument("--bge-csv", type=Path, default=DEFAULT_BGE)
    parser.add_argument("--output-csv", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--output-svg", type=Path, default=DEFAULT_SVG)
    parser.add_argument("--top-ks", default="5,10,15,20,30,40,50")
    args = parser.parse_args()
    try:
        top_ks = tuple(sorted({int(value.strip()) for value in args.top_ks.split(",")}))
    except ValueError:
        parser.error("top-ks must be comma-separated integers")
    if not top_ks or top_ks[0] < 1:
        parser.error("top-ks must be positive")

    rows = prepare_rows(read_csv(args.rrf_csv), read_csv(args.bge_csv), read_references(args.golden_file), top_ks)
    print(f"prepared_rows: {len(rows)}")
    score_rows(rows)
    summary = summarize(rows, top_ks)
    fields = ["golden_id", "question", "condition", "top_k", *(name for name, _ in METRICS), "reference", "titles"]
    with args.output_csv.open("w", newline="", encoding="utf-8-sig") as file:
        writer = csv.DictWriter(file, fieldnames=fields)
        writer.writeheader()
        writer.writerows({field: " | ".join(row["titles"]) if field == "titles" else row.get(field) for field in fields} for row in rows)
    args.output_svg.write_text(build_svg(summary, top_ks, summary[0]["question_count"]), encoding="utf-8")
    for top_k in top_ks:
        before, after = (next(row for row in summary if row["condition"] == condition and row["top_k"] == top_k) for condition in ("rrf_before", "bge_after"))
        print(f"top_k={top_k}: " + ", ".join(f"{name} {float(before[name]):.2f}->{float(after[name]):.2f}" for name, _ in METRICS))
    print(f"csv: {args.output_csv}")
    print(f"svg: {args.output_svg}")


if __name__ == "__main__":
    main()
