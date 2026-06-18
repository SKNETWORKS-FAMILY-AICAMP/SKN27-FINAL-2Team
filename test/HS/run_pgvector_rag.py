from __future__ import annotations

import argparse
import json
import os
import re

from dotenv import load_dotenv
from llm_answer_generator import LLMAnswerGenerator
from pgvector_retriever import PgVectorHybridRetriever, result_to_payload


IMPORTANT_TERMS = (
    "관전법",
    "위화도 회군",
    "조선 건국",
    "한양 천도",
    "정도전",
    "6조 직계제",
    "의정부 서사제",
    "집현전",
    "훈민정음",
    "경국대전",
    "대동법",
    "전시과",
    "팔만대장경",
)


def make_preview_answer(question: str, sources: list[dict]) -> str:
    if not sources:
        return "관련 근거를 찾지 못했습니다. 시대, 인물, 사건명을 조금 더 구체적으로 입력해 주세요."

    top = sources[0]
    lines = [
        f"# {top['title']}",
        "",
        "## 핵심 근거",
        f"- {top['snippet']}",
        "",
        "## 검색 메모",
        f"- source_type: {top['source_type']}",
        f"- vector_score: {top['vector_score']}",
        f"- keyword_score: {top['keyword_score']}",
    ]
    if top.get("original_image_url"):
        lines.extend(["", "## 이미지", f"- {top['original_image_url']}"])
    return "\n".join(lines)


def clean_sentence(text: str) -> str:
    value = re.sub(r"\s+", " ", text or "").strip()
    value = re.sub(r"\[[^\]]+\]", "", value)
    value = re.sub(r"^.*?>\s*", "", value)
    value = re.sub(r"^\d+\)\s*", "", value)
    return value.strip()


def split_sentences(text: str, limit: int = 12) -> list[str]:
    compact = clean_sentence(text)
    raw_sentences = re.split(r"(?:\.|\?|!|。)\s+", compact)
    sentences = []
    for sentence in raw_sentences:
        sentence = sentence.strip(" -")
        if len(sentence) < 12:
            continue
        if sentence not in sentences:
            sentences.append(sentence)
        if len(sentences) >= limit:
            break
    return sentences


def infer_textbook_title(question: str, sources: list[dict]) -> str:
    if "조선" in question and "정치" in question:
        return "조선 전기(정치)"
    if sources:
        return sources[0]["title"].split(">")[-1].strip()
    return question.strip()


def highlight_terms(text: str, terms: list[str]) -> str:
    highlighted = text
    for term in sorted(terms, key=len, reverse=True):
        if not term:
            continue
        highlighted = highlighted.replace(term, term)
    return highlighted


def collect_terms(question: str, sources: list[dict]) -> list[str]:
    terms = [term for term in IMPORTANT_TERMS if term in question]
    for source in sources:
        for term in IMPORTANT_TERMS:
            if term in source["title"] or term in source["snippet"]:
                terms.append(term)
    result = []
    for term in terms:
        if term not in result:
            result.append(term)
    return result[:10]


def make_textbook_answer(question: str, sources: list[dict]) -> str:
    if not sources:
        return make_preview_answer(question, sources)

    ordered_sources = sorted(
        sources[:6],
        key=lambda source: (
            0 if source["source_type"] == "historical_overview" else 1,
            source["document_id"],
            source["chunk_id"],
        ),
    )
    title = infer_textbook_title(question, sources)
    terms = collect_terms(question, sources)
    all_sentences: list[str] = []
    for source in ordered_sources[:4]:
        all_sentences.extend(split_sentences(source["snippet"], limit=5))
    all_sentences = list(dict.fromkeys(all_sentences))

    first = all_sentences[:3]
    second = all_sentences[3:9]
    extra = all_sentences[9:12]

    lines = [
        f"# {title}",
        "",
        "## 1. 핵심 흐름",
    ]

    if first:
        for index, sentence in enumerate(first, start=1):
            lines.append(f"| {index} | {highlight_terms(sentence, terms)} |")
    else:
        lines.append("| 1 | 검색된 근거를 바탕으로 핵심 흐름을 정리합니다. |")

    lines.extend(["", "## 2. 통치 기반 마련과 제도 정비"])
    if second:
        for sentence in second[:6]:
            lines.append(f"- {highlight_terms(sentence, terms)}")
    else:
        lines.append("- 핵심 제도, 왕, 사건을 시대순으로 연결해 정리합니다.")

    lines.extend(["", "## 3. 한능검 포인트"])
    if terms:
        for term in terms[:8]:
            lines.append(f"- {term}")
    else:
        lines.append("- 왕, 제도, 사건, 결과를 한 줄로 묶어서 암기하세요.")

    if extra:
        lines.extend(["", "## 4. 보충"])
        for sentence in extra:
            lines.append(f"- {highlight_terms(sentence, terms)}")

    lines.extend(["", "## 출처"])
    for source in sources[:3]:
        lines.append(f"- {source['title']} ({source['source_type']})")
    return "\n".join(lines)


def make_structured_fallback(question: str, sources: list[dict]) -> dict:
    title = infer_textbook_title(question, sources)
    terms = collect_terms(question, sources)
    sections = []
    for index, source in enumerate(sources[:3], start=1):
        sections.append(
            {
                "heading": f"{index}. {source['title'].split('>')[-1].strip()}",
                "items": [
                    {
                        "term": source["title"].split(">")[-1].strip(),
                        "content": source["snippet"],
                    }
                ],
            }
        )

    return {
        "answer_type": "textbook_note",
        "title": title,
        "summary": "검색된 근거를 바탕으로 핵심 내용을 정리했습니다.",
        "sections": sections,
        "exam_points": terms,
        "highlights": terms,
        "source_titles": [source["title"] for source in sources[:3]],
    }


def main() -> None:
    load_dotenv()
    default_llm_provider = os.getenv("CHAT_LLM_PROVIDER", "openai").lower()
    if default_llm_provider not in {"openai", "ollama", "none"}:
        default_llm_provider = "openai"

    parser = argparse.ArgumentParser(description="Experiment with PostgreSQL pgvector history RAG")
    parser.add_argument("question", help="한국사 질문")
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--raw", action="store_true", help="검색 결과만 JSON으로 출력")
    parser.add_argument("--style", choices=["preview", "textbook"], default="textbook")
    parser.add_argument(
        "--answer-format",
        choices=["markdown", "structured"],
        default="markdown",
        help="챗봇 응답 형식. markdown은 answer 문자열, structured는 structured_answer 객체를 반환",
    )
    parser.add_argument(
        "--llm",
        choices=["none", "openai", "ollama"],
        default=default_llm_provider,
        help="답변 생성에 사용할 LLM provider. 기본값은 CHAT_LLM_PROVIDER",
    )
    parser.add_argument("--llm-model", help="LLM 모델명. 예: gpt-4.1-mini, gemma4:2b")
    parser.add_argument("--follow-up", action="store_true", help="후속 질문처럼 설명형 답변을 생성")
    args = parser.parse_args()

    retriever = PgVectorHybridRetriever()
    results = retriever.search(args.question, top_k=args.top_k)
    sources = [result_to_payload(result) for result in results]
    answer = None
    structured_answer = None
    llm_info = None
    if not args.raw:
        if args.llm == "none":
            if args.answer_format == "structured":
                structured_answer = make_structured_fallback(args.question, sources)
            else:
                answer = (
                    make_textbook_answer(args.question, sources)
                    if args.style == "textbook"
                    else make_preview_answer(args.question, sources)
                )
        else:
            generator = LLMAnswerGenerator.from_env(provider=args.llm, model=args.llm_model)
            if args.answer_format == "structured":
                structured_answer = generator.generate_structured(
                    args.question,
                    sources,
                    follow_up=args.follow_up,
                )
            else:
                answer = generator.generate(
                    args.question,
                    sources,
                    style=args.style,
                    follow_up=args.follow_up,
                )
            llm_info = {
                "provider": generator.config.provider,
                "model": generator.config.model,
                "temperature": generator.config.temperature,
            }

    payload = {
        "question": args.question,
        "answer_format": args.answer_format,
        "answer": answer,
        "structured_answer": structured_answer,
        "llm": llm_info,
        "sources": sources,
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
