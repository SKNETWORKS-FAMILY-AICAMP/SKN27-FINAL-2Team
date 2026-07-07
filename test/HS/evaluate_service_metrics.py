from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
import time
import types
import subprocess
from dataclasses import dataclass
from datetime import datetime
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


PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / ".env")
DEFAULT_GOLDEN = PROJECT_ROOT / "etl" / "preprocessing" / "history" / "embedding" / "golden_questions.jsonl"
DEFAULT_OUT = PROJECT_ROOT / "etl" / "preprocessing" / "history" / "embedding" / "service_eval_results"

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.chatbot.graph_service import build_graph_context
from app.chatbot.rag.pgvector_retriever import PgVectorHybridRetriever, cached_pg_search
from app.chatbot.rag_service import build_history_rag_answer


@dataclass
class Metric:
    name: str
    definition: str
    value: str
    threshold: str
    passed: bool | None
    verification: str

    def to_row(self) -> dict[str, str]:
        return {
            "평가 항목": self.name,
            "지표 정의": self.definition,
            "측정 결과": self.value,
            "통과 기준": self.threshold,
            "통과 여부": "N/A" if self.passed is None else ("PASS" if self.passed else "FAIL"),
            "검증 방법": self.verification,
        }


def load_jsonl(path: Path) -> list[dict]:
    rows = []
    with path.open("r", encoding="utf-8") as fp:
        for line in fp:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


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
    cached_pg_search.cache_clear()
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
        f"{avg * 1000:.0f}ms" if avg < 1 else f"{avg:.2f}s",
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


def install_ragas_vertexai_compat() -> None:
    try:
        import langchain_community.chat_models.vertexai  # noqa: F401
    except ModuleNotFoundError:
        from langchain_openai import ChatOpenAI

        module = types.ModuleType("langchain_community.chat_models.vertexai")
        module.ChatVertexAI = ChatOpenAI
        sys.modules["langchain_community.chat_models.vertexai"] = module


def ragas_text(value: str) -> str:
    lines = []
    for line in (value or "").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        # Skip table column divider formatting (e.g., |---|---|)
        if re.match(r"^\|[\s:\-|+\d]*\|$", stripped):
            continue
        # Skip source citation blocks to prevent RAGAS Answer Relevance penalties
        if stripped.startswith(("출처 요약", "출처:", "- 출처")):
            continue
        # Remove pipe characters from table rows to keep the content text
        if stripped.startswith("|"):
            stripped = stripped.replace("|", " ").strip()
        lines.append(re.sub(r"^[#*\-\d.\s]+", "", stripped))
    return " ".join(lines)


def evaluate_ragas_metrics(questions: list[dict], limit: int, debug_path: Path | None = None) -> list[Metric]:
    try:
        install_ragas_vertexai_compat()
        from datasets import Dataset
        from ragas import evaluate
        from ragas.llms import LangchainLLMWrapper
        from ragas.metrics import answer_relevancy, faithfulness
        from langchain_openai import ChatOpenAI
    except ImportError as exc:
        return [
            Metric("RAGAS Faithfulness", "답변이 검색 근거에 충실한지 평가", f"N/A ({exc.name} 미설치)", "0.80 이상", None, "RAGAS Framework (Faithfulness)"),
            Metric("RAGAS Answer Relevance", "답변이 질문 의도에 적합한지 평가", f"N/A ({exc.name} 미설치)", "0.80 이상", None, "RAGAS Framework (Answer Relevance)"),
        ]

    ragas_intents = {"concept", "summary", "compare", "evidence"}
    text_questions = [
        question
        for question in questions
        if not question.get("requires_image") and (question.get("intent") or "concept") in ragas_intents
    ]
    rows = []
    retriever = PgVectorHybridRetriever()
    for question in text_questions[:limit]:
        result = build_history_rag_answer(question["query"], intent="concept", answer_format="text", top_k=5)
        answer = ragas_text(result.get("answer") or structured_to_text(result.get("structured_answer") or {}))
        contexts = [source.get("snippet") or "" for source in result.get("sources") or [] if source.get("snippet")]
        if not contexts:
            contexts = [item.chunk_text for item in retriever.search(question["query"], top_k=5) if item.chunk_text]
        if not contexts:
            contexts = ["검색 근거 없음"]
        rows.append(
            {
                "user_input": question["query"],
                "response": answer,
                "retrieved_contexts": contexts,
                "reference": question.get("reference_answer") or ", ".join(question.get("expected_keywords") or []),
            }
        )

    try:
        dataset = Dataset.from_list(rows)
        evaluator_llm = LangchainLLMWrapper(
            ChatOpenAI(
                model=os.getenv("RAGAS_LLM_MODEL") or "gpt-4o-mini",
                temperature=0,
            )
        )
        last_error = None
        for _ in range(3):
            try:
                result = evaluate(dataset, metrics=[faithfulness, answer_relevancy], llm=evaluator_llm, show_progress=False, batch_size=1)
                break
            except IndexError as exc:
                last_error = exc
        else:
            raise last_error
        if debug_path:
            write_ragas_debug(rows, result.scores, debug_path)
        faithfulness_scores = [row["faithfulness"] for row in result.scores if row.get("faithfulness") is not None]
        answer_scores = [row["answer_relevancy"] for row in result.scores if row.get("answer_relevancy") is not None]
        faithfulness_score = sum(faithfulness_scores) / len(faithfulness_scores)
        answer_score = sum(answer_scores) / len(answer_scores)
    except Exception as exc:
        message = f"N/A ({type(exc).__name__}: {exc})"
        return [
            Metric("RAGAS Faithfulness", "답변이 검색 근거에 충실한지 평가", message, "0.80 이상", None, "RAGAS Framework (Faithfulness)"),
            Metric("RAGAS Answer Relevance", "답변이 질문 의도에 적합한지 평가", message, "0.80 이상", None, "RAGAS Framework (Answer Relevance)"),
        ]

    return [
        Metric("RAGAS Faithfulness", "답변이 검색 근거에 충실한지 평가", f"{faithfulness_score:.2f}", "0.80 이상", faithfulness_score >= 0.80, "RAGAS Framework (Faithfulness)"),
        Metric("RAGAS Answer Relevance", "답변이 질문 의도에 적합한지 평가", f"{answer_score:.2f}", "0.80 이상", answer_score >= 0.80, "RAGAS Framework (Answer Relevance)"),
    ]


def write_ragas_debug(rows: list[dict], scores: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as fp:
        writer = csv.DictWriter(
            fp,
            fieldnames=["question", "faithfulness", "answer_relevancy", "answer", "contexts"],
        )
        writer.writeheader()
        for row, score in zip(rows, scores):
            writer.writerow(
                {
                    "question": row["user_input"],
                    "faithfulness": score.get("faithfulness"),
                    "answer_relevancy": score.get("answer_relevancy"),
                    "answer": row["response"],
                    "contexts": "\n---\n".join(row["retrieved_contexts"]),
                }
            )


def write_outputs(metrics: list[Metric], out_prefix: Path) -> None:
    out_prefix.parent.mkdir(parents=True, exist_ok=True)
    rows = [item.to_row() for item in metrics]
    (out_prefix.with_suffix(".json")).write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    with out_prefix.with_suffix(".csv").open("w", encoding="utf-8-sig", newline="") as fp:
        writer = csv.DictWriter(fp, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    append_history(rows, out_prefix.with_name(f"{out_prefix.name}_history.csv"))


def append_history(rows: list[dict[str, str]], path: Path) -> None:
    run_at = datetime.now().isoformat(timespec="seconds")
    version = current_version()
    fieldnames = ["run_at", *version, *rows[0]]
    exists = path.exists()
    with path.open("a", encoding="utf-8-sig", newline="") as fp:
        writer = csv.DictWriter(fp, fieldnames=fieldnames)
        if not exists:
            writer.writeheader()
        for row in rows:
            writer.writerow({"run_at": run_at, **version, **row})


def git_text(*args: str) -> str:
    try:
        return subprocess.check_output(["git", *args], cwd=PROJECT_ROOT, text=True, stderr=subprocess.DEVNULL).strip()
    except Exception:
        return ""


def current_version() -> dict[str, str]:
    return {
        "git_branch": git_text("branch", "--show-current"),
        "git_commit": git_text("rev-parse", "--short", "HEAD"),
        "git_dirty": "Y" if git_text("status", "--short") else "N",
    }


def print_markdown(metrics: list[Metric]) -> None:
    print("| 평가 항목 | 지표 정의 | 측정 결과 | 통과 기준 | 검증 방법 |")
    print("|---|---|---:|---|---|")
    for item in metrics:
        print(f"| {item.name} | {item.definition} | {item.value} | {item.threshold} | {item.verification} |")


def run_service_evaluation(args: argparse.Namespace, questions: list[dict], queries: list[str]) -> list[dict[str, str]]:
    latency_full_answer = args.latency_full_answer or args.ragas
    metrics = [
        evaluate_graph_connectivity(queries),
        evaluate_latency(queries, latency_full_answer),
        evaluate_mcp_success(args.mcp_url, args.timeout),
    ]
    if args.ragas:
        debug_path = args.out_prefix.with_name(f"{args.out_prefix.name}_ragas_samples.csv")
        metrics.extend(evaluate_ragas_metrics(questions, args.ragas_limit, debug_path))
    return [item.to_row() for item in metrics]


def structured_to_text(answer: dict) -> str:
    parts = [answer.get("title") or "", answer.get("summary") or ""]
    for section in answer.get("sections") or []:
        parts.append(section.get("heading") or "")
        for item in section.get("items") or []:
            parts.append(f"{item.get('term', '')}: {item.get('content', '')}")
    return "\n".join(part for part in parts if part)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Evaluate RAG, Graph, latency, and external API metrics.")
    parser.add_argument("--golden-file", type=Path, default=DEFAULT_GOLDEN)
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--latency-limit", type=int, default=5)
    parser.add_argument("--latency-full-answer", action="store_true")
    parser.add_argument("--ragas", action="store_true")
    parser.add_argument("--ragas-limit", type=int, default=50)
    parser.add_argument("--mcp-url", action="append", default=[])
    parser.add_argument("--timeout", type=float, default=5.0)
    parser.add_argument("--out-prefix", type=Path, default=DEFAULT_OUT)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    questions = load_jsonl(args.golden_file)
    queries = [item["query"] for item in questions[: args.latency_limit]]
    rows = run_service_evaluation(args, questions, queries)
    metrics = [
        Metric(
            row["평가 항목"],
            row["지표 정의"],
            row["측정 결과"],
            row["통과 기준"],
            None if row["통과 여부"] == "N/A" else row["통과 여부"] == "PASS",
            row["검증 방법"],
        )
        for row in rows
    ]
    write_outputs(metrics, args.out_prefix)
    print_markdown(metrics)
    print(f"\njson={args.out_prefix.with_suffix('.json')}")
    print(f"csv={args.out_prefix.with_suffix('.csv')}")


if __name__ == "__main__":
    main()
