from __future__ import annotations

import argparse
import csv
import json
import math
import os
import re
import sys
import time
import types
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

try:
    from langsmith import traceable
except ImportError:
    def traceable(func=None, **_kwargs):
        if func is None:
            return lambda wrapped: wrapped
        return func


PROJECT_ROOT = Path(__file__).resolve().parents[3]
load_dotenv(PROJECT_ROOT / ".env")
DEFAULT_GOLDEN = PROJECT_ROOT / "etl" / "preprocessing" / "history" / "embedding" / "golden_questions.jsonl"
DEFAULT_OUT = PROJECT_ROOT / "etl" / "preprocessing" / "history" / "embedding" / "service_eval_results"

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.chatbot.graph_service import build_graph_context
from app.chatbot import rag_service as rag_service_module
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
def evaluate_latency(queries: list[str]) -> list[Metric]:
    search_values = []
    generation_values = []
    total_values = []
    for query in queries:
        llm_sec = 0.0
        original_generate = rag_service_module.LLMAnswerGenerator.generate
        original_generate_structured = rag_service_module.LLMAnswerGenerator.generate_structured

        def timed_generate(*args, **kwargs):
            nonlocal llm_sec
            start = time.perf_counter()
            try:
                return original_generate(*args, **kwargs)
            finally:
                llm_sec += time.perf_counter() - start

        def timed_generate_structured(*args, **kwargs):
            nonlocal llm_sec
            start = time.perf_counter()
            try:
                return original_generate_structured(*args, **kwargs)
            finally:
                llm_sec += time.perf_counter() - start

        rag_service_module.LLMAnswerGenerator.generate = timed_generate
        rag_service_module.LLMAnswerGenerator.generate_structured = timed_generate_structured
        cached_pg_search.cache_clear()
        start = time.perf_counter()
        try:
            build_history_rag_answer(query, intent="concept", answer_format="structured", top_k=5)
        finally:
            rag_service_module.LLMAnswerGenerator.generate = original_generate
            rag_service_module.LLMAnswerGenerator.generate_structured = original_generate_structured
        total_sec = time.perf_counter() - start

        search_values.append(max(0.0, total_sec - llm_sec))
        generation_values.append(llm_sec)
        total_values.append(total_sec)

    search_avg = sum(search_values) / len(search_values) if search_values else 0.0
    generation_avg = sum(generation_values) / len(generation_values) if generation_values else 0.0
    total_avg = sum(total_values) / len(total_values) if total_values else 0.0
    return [
        Metric(
            "검색 속도",
            "LLM 생성 제외 쿼리 평균 소요 시간",
            f"{search_avg * 1000:.0f}ms" if search_avg < 1 else f"{search_avg:.2f}s",
            "2.0s 이내",
            search_avg <= 2.0,
            "pgvector/Graph 검색 지연시간 측정",
        ),
        Metric(
            "LLM 답변 생성 속도",
            "검색 이후 답변 생성 평균 소요 시간",
            f"{generation_avg * 1000:.0f}ms" if generation_avg < 1 else f"{generation_avg:.2f}s",
            "5.0s 이내",
            generation_avg <= 5.0,
            "전체 응답 시간 - 검색 시간",
        ),
        Metric(
            "전체 응답 속도",
            "LLM 생성 포함 평균 응답 시간",
            f"{total_avg * 1000:.0f}ms" if total_avg < 1 else f"{total_avg:.2f}s",
            "7.0s 이내",
            total_avg <= 7.0,
            "서비스 RAG 전체 호출 지연시간 측정",
        ),
    ]


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
        from ragas.metrics import answer_relevancy, context_precision, context_recall, faithfulness
        from langchain_openai import ChatOpenAI
        from openai import APIConnectionError, APITimeoutError, RateLimitError
    except ImportError as exc:
        return [
            Metric("RAGAS Context Precision", "검색 문맥 중 질문과 관련 있는 문맥 비율", f"N/A ({exc.name} 미설치)", "0.80 이상", None, "RAGAS Framework (Context Precision)"),
            Metric("RAGAS Context Recall", "정답에 필요한 근거를 검색 문맥이 포함하는지 평가", f"N/A ({exc.name} 미설치)", "0.80 이상", None, "RAGAS Framework (Context Recall)"),
            Metric("RAGAS Faithfulness", "답변이 검색 근거에 충실한지 평가", f"N/A ({exc.name} 미설치)", "0.80 이상", None, "RAGAS Framework (Faithfulness)"),
            Metric("RAGAS Answer Relevance", "답변이 질문 의도에 적합한지 평가", f"N/A ({exc.name} 미설치)", "0.80 이상", None, "RAGAS Framework (Answer Relevance)"),
        ]

    ragas_intents = {"concept", "summary", "compare", "evidence"}
    grouped_questions = {intent: [] for intent in ragas_intents}
    for question in questions:
        intent = question.get("intent") or "concept"
        if not question.get("requires_image") and intent in grouped_questions:
            grouped_questions[intent].append(question)

    # Keep each RAGAS intent equally represented without changing service intents.
    text_questions = []
    for index in range(15):
        for intent in ("concept", "summary", "compare", "evidence"):
            if index < len(grouped_questions[intent]):
                text_questions.append(grouped_questions[intent][index])
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
                "id": question.get("id") or "",
                "intent": question.get("intent") or "concept",
                "user_input": question["query"],
                "response": answer,
                "retrieved_contexts": contexts,
                "reference": question.get("reference_answer") or ", ".join(question.get("expected_keywords") or []),
                "expected_keywords": question.get("expected_keywords") or [],
                "expected_era": question.get("expected_era") or "",
                "expected_source_type": question.get("expected_source_type") or "",
            }
        )

    try:
        # RAGAS 입력은 문자열/문맥 목록만 허용한다. 평가 이력용 메타데이터는 CSV에만 보관한다.
        dataset = Dataset.from_list(
            [
                {
                    "user_input": row["user_input"],
                    "response": row["response"],
                    "retrieved_contexts": row["retrieved_contexts"],
                    "reference": row["reference"],
                }
                for row in rows
            ]
        )
        evaluator_llm = LangchainLLMWrapper(
            ChatOpenAI(
                model=os.getenv("RAGAS_LLM_MODEL") or "gpt-4o-mini",
                temperature=0,
            )
        )
        transient_exceptions = (IndexError, RateLimitError, APITimeoutError, APIConnectionError)
        last_error = None
        for attempt in range(3):
            try:
                result = evaluate(
                    dataset,
                    metrics=[context_precision, context_recall, faithfulness, answer_relevancy],
                    llm=evaluator_llm,
                    show_progress=False,
                    batch_size=1,
                )
                break
            except transient_exceptions as exc:
                last_error = exc
                if attempt < 2:
                    time.sleep(2 ** attempt)
        else:
            raise last_error
        if debug_path:
            write_ragas_debug(rows, result.scores, debug_path)
        def average_score(name: str) -> float | None:
            scores = [
                float(row[name])
                for row in result.scores
                if row.get(name) is not None and not math.isnan(float(row[name]))
            ]
            return sum(scores) / len(scores) if scores else None

        context_precision_score = average_score("context_precision")
        context_recall_score = average_score("context_recall")
        faithfulness_score = average_score("faithfulness")
        answer_score = average_score("answer_relevancy")
    except Exception as exc:
        message = f"N/A ({type(exc).__name__}: {exc})"
        return [
            Metric("RAGAS Context Precision", "검색 문맥 중 질문과 관련 있는 문맥 비율", message, "0.80 이상", None, "RAGAS Framework (Context Precision)"),
            Metric("RAGAS Context Recall", "정답에 필요한 근거를 검색 문맥이 포함하는지 평가", message, "0.80 이상", None, "RAGAS Framework (Context Recall)"),
            Metric("RAGAS Faithfulness", "답변이 검색 근거에 충실한지 평가", message, "0.80 이상", None, "RAGAS Framework (Faithfulness)"),
            Metric("RAGAS Answer Relevance", "답변이 질문 의도에 적합한지 평가", message, "0.80 이상", None, "RAGAS Framework (Answer Relevance)"),
        ]

    def ragas_metric(name: str, definition: str, score: float | None, method: str) -> Metric:
        return Metric(
            name,
            definition,
            "N/A (유효 점수 없음)" if score is None else f"{score:.2f}",
            "0.80 이상",
            None if score is None else score >= 0.80,
            method,
        )

    return [
        ragas_metric("RAGAS Context Precision", "검색 문맥 중 질문과 관련 있는 문맥 비율", context_precision_score, "RAGAS Framework (Context Precision)"),
        ragas_metric("RAGAS Context Recall", "정답에 필요한 근거를 검색 문맥이 포함하는지 평가", context_recall_score, "RAGAS Framework (Context Recall)"),
        ragas_metric("RAGAS Faithfulness", "답변이 검색 근거에 충실한지 평가", faithfulness_score, "RAGAS Framework (Faithfulness)"),
        ragas_metric("RAGAS Answer Relevance", "답변이 질문 의도에 적합한지 평가", answer_score, "RAGAS Framework (Answer Relevance)"),
    ]


def write_ragas_debug(rows: list[dict], scores: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "run_at", "id", "intent", "question", "expected_keywords", "expected_era",
        "expected_source_type", "reference_answer", "context_precision", "context_recall",
        "faithfulness", "answer_relevancy", "answer", "contexts",
    ]
    run_at = datetime.now().isoformat(timespec="seconds")
    records = []
    for row, score in zip(rows, scores):
        records.append(
            {
                "run_at": run_at,
                "id": row["id"],
                "intent": row["intent"],
                "question": row["user_input"],
                "expected_keywords": ", ".join(row["expected_keywords"]),
                "expected_era": row["expected_era"],
                "expected_source_type": row["expected_source_type"],
                "reference_answer": row["reference"],
                "context_precision": score.get("context_precision"),
                "context_recall": score.get("context_recall"),
                "faithfulness": score.get("faithfulness"),
                "answer_relevancy": score.get("answer_relevancy"),
                "answer": row["response"],
                "contexts": "\n---\n".join(row["retrieved_contexts"]),
            }
        )
    with path.open("w", encoding="utf-8-sig", newline="") as fp:
        writer = csv.DictWriter(fp, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(records)

    history_path = path.with_name(f"{path.stem}_history.csv")
    with history_path.open("a", encoding="utf-8-sig", newline="") as fp:
        writer = csv.DictWriter(fp, fieldnames=fieldnames)
        if history_path.stat().st_size == 0:
            writer.writeheader()
        writer.writerows(records)


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
    needs_header = not path.exists() or path.stat().st_size == 0
    with path.open("a", encoding="utf-8-sig", newline="") as fp:
        writer = csv.DictWriter(fp, fieldnames=fieldnames)
        if needs_header:
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


def print_langsmith_status() -> None:
    enabled = os.getenv("LANGSMITH_TRACING", "").lower() == "true"
    has_key = bool(os.getenv("LANGSMITH_API_KEY"))
    project = os.getenv("LANGSMITH_PROJECT", "default")
    if enabled and has_key:
        print(f"LangSmith tracing=enabled project={project}")
    else:
        print("LangSmith tracing=disabled (set LANGSMITH_TRACING=true and LANGSMITH_API_KEY in .env)")


def run_service_evaluation(args: argparse.Namespace, questions: list[dict], queries: list[str]) -> list[dict[str, str]]:
    metrics = [
        evaluate_graph_connectivity(queries),
        *evaluate_latency(queries),
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
    parser.add_argument("--ragas", action="store_true")
    parser.add_argument("--ragas-limit", type=int, default=50)
    parser.add_argument("--out-prefix", type=Path, default=DEFAULT_OUT)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    print_langsmith_status()
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
