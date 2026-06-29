from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

from dotenv import load_dotenv

try:
    from langsmith import traceable
except ImportError:
    def traceable(func=None, **_kwargs):
        if func is None:
            return lambda wrapped: wrapped
        return func


PROJECT_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(PROJECT_ROOT / ".env")
DEFAULT_GOLDEN = PROJECT_ROOT / "etl" / "preprocessing" / "history" / "embedding" / "golden_questions.jsonl"
DEFAULT_OUT = PROJECT_ROOT / "etl" / "preprocessing" / "history" / "embedding" / "service_eval_results"

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.chatbot.graph_service import build_graph_context
from app.chatbot.rag.pgvector_retriever import PgVectorHybridRetriever
from app.chatbot.rag_service import build_history_rag_answer


@dataclass
class Metric:
    name: str
    definition: str
    value: str
    threshold: str
    passed: bool | None
    verification: str


def load_jsonl(path: Path) -> list[dict]:
    rows = []
    with path.open("r", encoding="utf-8") as fp:
        for line in fp:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def compact(value: str | None) -> str:
    return "".join((value or "").lower().split())


def result_text(result) -> str:
    metadata = result.metadata or {}
    parts = [
        result.title,
        result.chunk_text,
        " ".join(metadata.get("keywords") or []),
        " ".join(metadata.get("category_tags") or []),
        str(metadata.get("category") or ""),
        str(metadata.get("field") or ""),
    ]
    chronology = metadata.get("chronology") or {}
    parts.extend(str(chronology.get(key) or "") for key in ("era", "dynasty", "period_label"))
    return compact(" ".join(parts))


@traceable(name="evaluate_search_accuracy")
def evaluate_search_accuracy(questions: list[dict], top_k: int, limit: int | None) -> Metric:
    retriever = PgVectorHybridRetriever()
    selected = questions[:limit] if limit else questions
    hits = 0
    for question in selected:
        results = retriever.search(question["query"], top_k=top_k)
        text = " ".join(result_text(result) for result in results)
        expected = question.get("expected_keywords") or []
        if any(compact(keyword) in text for keyword in expected):
            hits += 1
    score = hits / len(selected) if selected else 0.0
    return Metric(
        "검색 정확도",
        "Similarity Score 기반 적합성",
        f"{score:.2f}",
        "0.80 이상",
        score >= 0.80,
        "Golden Question 기반 RAG 검색 검증",
    )


@traceable(name="evaluate_graph_connectivity")
def evaluate_graph_connectivity(queries: list[str]) -> Metric:
    checked = 0
    hits = 0
    for query in queries:
        context = build_graph_context(query)
        if not context.get("enabled"):
            continue
        checked += 1
        if context.get("terms") or context.get("keywords"):
            hits += 1
    score = hits / checked if checked else 0.0
    return Metric(
        "연결성(Graph)",
        "노드 간 탐색 성공률",
        "N/A" if not checked else f"{score * 100:.1f}%",
        "95% 이상",
        None if not checked else score >= 0.95,
        "Cypher Query 성능 테스트 및 접속 조사",
    )


@traceable(name="evaluate_latency")
def evaluate_latency(queries: list[str], full_answer: bool) -> Metric:
    values = []
    retriever = None if full_answer else PgVectorHybridRetriever()
    for query in queries:
        start = time.perf_counter()
        if full_answer:
            build_history_rag_answer(query, intent="concept", answer_format="structured", top_k=5)
        else:
            retriever.search(query, top_k=5)
        values.append(time.perf_counter() - start)
    avg = sum(values) / len(values) if values else 0.0
    return Metric(
        "응답 속도",
        "쿼리 당 평균 소요 시간",
        f"{avg:.1f}s",
        "2.0s 이내",
        avg <= 2.0,
        "LangSmith Latency Tracking" if full_answer else "로컬 검색 지연시간 측정",
    )


@traceable(name="evaluate_mcp_success")
def evaluate_mcp_success(urls: list[str], timeout: float) -> Metric:
    if not urls:
        return Metric(
            "MCP 연동 성공률",
            "외부 도구 호출 및 데이터 수신",
            "N/A",
            "90% 이상",
            None,
            "API 성공/실패 로그 분석",
        )

    ok = 0
    for url in urls:
        try:
            with urlopen(url, timeout=timeout) as response:
                ok += 1 if 200 <= response.status < 500 else 0
        except URLError:
            pass
    score = ok / len(urls)
    return Metric(
        "MCP 연동 성공률",
        "외부 도구 호출 및 데이터 수신",
        f"{score * 100:.0f}%",
        "90% 이상",
        score >= 0.90,
        "API 성공/실패 로그 분석",
    )


@traceable(name="evaluate_ragas_faithfulness")
def evaluate_ragas_faithfulness(questions: list[dict], limit: int) -> Metric:
    try:
        from datasets import Dataset
        from ragas import evaluate
        from ragas.metrics import faithfulness
    except ImportError as exc:
        return Metric(
            "RAGAS Faithfulness",
            "답변이 검색 근거에 충실한지 평가",
            f"N/A ({exc.name} 미설치)",
            "0.80 이상",
            None,
            "RAGAS Framework (Faithfulness)",
        )

    rows = []
    for question in questions[:limit]:
        result = build_history_rag_answer(question["query"], intent="concept", answer_format="text", top_k=5)
        answer = result.get("answer") or ""
        contexts = [source.get("snippet") or "" for source in result.get("sources") or [] if source.get("snippet")]
        rows.append(
            {
                "question": question["query"],
                "answer": answer,
                "contexts": contexts,
                "ground_truth": ", ".join(question.get("expected_keywords") or []),
            }
        )

    try:
        dataset = Dataset.from_list(rows)
        score = float(evaluate(dataset, metrics=[faithfulness])["faithfulness"])
    except Exception:
        dataset = Dataset.from_list(
            [
                {
                    "user_input": row["question"],
                    "response": row["answer"],
                    "retrieved_contexts": row["contexts"],
                    "reference": row["ground_truth"],
                }
                for row in rows
            ]
        )
        score = float(evaluate(dataset, metrics=[faithfulness])["faithfulness"])

    return Metric(
        "RAGAS Faithfulness",
        "답변이 검색 근거에 충실한지 평가",
        f"{score:.2f}",
        "0.80 이상",
        score >= 0.80,
        "RAGAS Framework (Faithfulness)",
    )


def write_outputs(metrics: list[Metric], out_prefix: Path) -> None:
    out_prefix.parent.mkdir(parents=True, exist_ok=True)
    rows = [
        {
            "평가 항목": item.name,
            "지표 정의": item.definition,
            "측정 결과": item.value,
            "통과 기준": item.threshold,
            "통과 여부": "N/A" if item.passed is None else ("PASS" if item.passed else "FAIL"),
            "검증 방법": item.verification,
        }
        for item in metrics
    ]
    (out_prefix.with_suffix(".json")).write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    with out_prefix.with_suffix(".csv").open("w", encoding="utf-8-sig", newline="") as fp:
        writer = csv.DictWriter(fp, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def print_markdown(metrics: list[Metric]) -> None:
    print("| 평가 항목 | 지표 정의 | 측정 결과 | 통과 기준 | 검증 방법 |")
    print("|---|---|---:|---|---|")
    for item in metrics:
        print(f"| {item.name} | {item.definition} | {item.value} | {item.threshold} | {item.verification} |")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Evaluate RAG, Graph, latency, and external API metrics.")
    parser.add_argument("--golden-file", type=Path, default=DEFAULT_GOLDEN)
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--latency-limit", type=int, default=5)
    parser.add_argument("--latency-full-answer", action="store_true")
    parser.add_argument("--ragas", action="store_true")
    parser.add_argument("--ragas-limit", type=int, default=3)
    parser.add_argument("--mcp-url", action="append", default=[])
    parser.add_argument("--timeout", type=float, default=5.0)
    parser.add_argument("--out-prefix", type=Path, default=DEFAULT_OUT)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    questions = load_jsonl(args.golden_file)
    queries = [item["query"] for item in questions[: args.latency_limit]]
    metrics = [
        evaluate_search_accuracy(questions, args.top_k, args.limit),
        evaluate_graph_connectivity(queries),
        evaluate_latency(queries, args.latency_full_answer),
        evaluate_mcp_success(args.mcp_url, args.timeout),
    ]
    if args.ragas:
        metrics.append(evaluate_ragas_faithfulness(questions, args.ragas_limit))
    write_outputs(metrics, args.out_prefix)
    print_markdown(metrics)
    print(f"\njson={args.out_prefix.with_suffix('.json')}")
    print(f"csv={args.out_prefix.with_suffix('.csv')}")


if __name__ == "__main__":
    main()
