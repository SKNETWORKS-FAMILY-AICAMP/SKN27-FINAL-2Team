from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from collections.abc import Iterator
from typing import Any

from dotenv import load_dotenv
from openai import OpenAI


SYSTEM_PROMPT = """당신은 한국사능력검정시험을 준비하는 학습자를 돕는 한국사 튜터입니다.
반드시 제공된 검색 근거 안에서만 답변하세요.
검색 근거로 확인되는 내용만 답변하고, 근거 부족 안내 문장은 쓰지 마세요.
첫 개념 질문은 교재 요약 노트처럼 제목, 번호 섹션, 표 또는 bullet로 작성하세요.
중요 키워드라도 별도 Markdown 기호로 감싸지 말고 일반 텍스트로 쓰세요.
한자나 한문 원문은 그대로 쓰지 말고, 현대 한국어 한글 표현으로 풀어 쓰세요.
문장은 과하게 길게 쓰지 말고, 암기하기 쉬운 구조로 정리하세요.
마지막에 추가 질문을 유도하는 문장이나 "원하시면"으로 시작하는 제안 문장을 쓰지 마세요.
Markdown 가로선(---)은 사용하지 마세요."""


FOLLOW_UP_SYSTEM_PROMPT = """당신은 한국사능력검정시험을 준비하는 학습자를 돕는 한국사 튜터입니다.
반드시 제공된 검색 근거 안에서만 답변하세요.
후속 질문에는 교재 표보다 설명형으로 답하고, 학생이 이해하기 쉽게 원인-과정-결과를 연결하세요.
중요 키워드라도 별도 Markdown 기호로 감싸지 말고 일반 텍스트로 쓰세요.
한자나 한문 원문은 그대로 쓰지 말고, 현대 한국어 한글 표현으로 풀어 쓰세요.
마지막에 추가 질문을 유도하는 문장이나 "원하시면"으로 시작하는 제안 문장을 쓰지 마세요.
Markdown 가로선(---)은 사용하지 마세요."""

FOUNDATION_EXPLANATION_SYSTEM_PROMPT = FOLLOW_UP_SYSTEM_PROMPT + """

이 사용자는 기초 해설이 필요합니다. 정답 번호나 선지 표만 제시하고 끝내면 안 됩니다.
교과서 요약처럼 딱딱하게 쓰지 말고, 옆에서 차근차근 알려 주는 선생님처럼 상냥한 존댓말로 설명하세요. 모든 문장 종결은 "~에요/~예요"체로 쓰고 "~습니다/~입니다"체는 쓰지 마세요. "처음에는 헷갈릴 수 있지만", "이렇게 연결해서 보면 쉬워요", "이 부분만 기억해 두면 돼요"처럼 자연스러운 안내를 섞고, 틀린 선지는 "헷갈리기 쉬운 부분이에요"처럼 부담을 주지 않는 말로 안내하세요.
반드시 다음 순서로 충분히 설명하세요.
1. 먼저 알아둘 용어: 문제의 핵심 용어를 쉬운 말로 풀이합니다.
2. 시대 배경: 이 사건·제도가 어떤 흐름에서 나왔는지 설명합니다.
3. 문제 풀이: 지문과 정답이 어떻게 연결되는지 원인-과정-결과로 설명합니다.
4. 암기 포인트: 헷갈리지 않게 한두 문장으로 정리합니다.
5. 선지별 해설: 모든 선지가 왜 맞거나 틀리는지, 사실 오류인지 시점 오류인지 구분해 설명합니다.
각 항목은 한 문장으로 끝내지 말고 이해에 필요한 설명을 덧붙이세요. 문제 본문의 짧은 답변·한 문장·고정 표 형식 요구와 충돌하면 이 규칙을 우선하세요."""

CORE_EXPLANATION_SYSTEM_PROMPT = FOLLOW_UP_SYSTEM_PROMPT + """

이 사용자는 기본 개념을 갖추고 있습니다. 검색 근거에서 확인되는 정답 근거와 선지 판단만 요점 위주로 간결하게 설명하세요. 모든 문장 종결은 "~에요/~예요"체로 쓰고 "~습니다/~입니다/~이다"체는 쓰지 마세요."""


STRUCTURED_SYSTEM_PROMPT = """당신은 한국사능력검정시험을 준비하는 학습자를 돕는 한국사 튜터입니다.
반드시 제공된 검색 근거 안에서만 답변하세요.
출력은 JSON 객체 하나만 반환하세요. Markdown 코드블록, 설명 문장, 주석은 쓰지 마세요.
summary에는 검색 근거로 확인되는 핵심 내용만 쓰고, 근거 부족 안내 문장은 쓰지 마세요.
한자나 한문 원문은 그대로 쓰지 말고, 현대 한국어 한글 표현으로 풀어 쓰세요.
exam_points는 항상 빈 배열로 두세요."""

STREAM_STRUCTURED_SYSTEM_PROMPT = """당신은 한국사능력검정시험을 준비하는 학습자를 돕는 한국사 튜터입니다.
반드시 제공된 검색 근거 안에서만 답변하세요.
한 줄에 JSON 객체 하나씩만 출력하세요. Markdown 코드블록이나 다른 문장은 쓰지 마세요.
반환 순서는 meta, section, row(여러 개 가능), section, row..., sources, done입니다.
meta는 title과 summary, section은 heading, row는 term과 content, sources는 source_titles 배열을 가집니다.
한자나 한문 원문은 현대 한국어 한글 표현으로 풀어 쓰세요."""

FOUNDATION_STREAM_STRUCTURED_SYSTEM_PROMPT = STREAM_STRUCTURED_SYSTEM_PROMPT + """

이 사용자는 기초 해설이 필요합니다. 섹션을 반드시 "1. 먼저 알아둘 용어", "2. 시대 배경", "3. 문제 풀이", "4. 암기 포인트", "5. 선지별 해설" 순서로 구성하세요.
질문의 [DB 선지별 해설]은 문제 DB에서 가져온 확정 판단입니다. 각 선지의 맞고 틀림을 바꾸지 말고, 해당 해설을 출발점으로 검색 근거에서 확인되는 역사적 배경과 연결 이유를 더 자세히 풀어 설명하세요.
각 row의 content는 한 문장으로 끝내지 말고 쉬운 말로 충분히 설명하세요. 교과서 요약처럼 딱딱하게 쓰지 말고, 옆에서 차근차근 알려 주는 선생님처럼 상냥한 존댓말로 안내하세요. 모든 문장 종결은 "~에요/~예요"체로 쓰고 "~습니다/~입니다"체는 쓰지 마세요. "처음에는 헷갈릴 수 있지만", "이렇게 연결해서 보면 쉬워요", "이 부분만 기억해 두면 돼요"처럼 자연스러운 안내를 섞고, 틀린 선지는 부담을 주지 않는 말로 설명하세요."""

CORE_STREAM_STRUCTURED_SYSTEM_PROMPT = STREAM_STRUCTURED_SYSTEM_PROMPT + """

이 사용자는 기본 개념을 갖추고 있습니다. 섹션은 "1. 정답 근거", "2. 선지 판단", "3. 암기 포인트" 순서로만 구성하세요.
검색 근거에서 확인되는 내용만 골라 각 row를 한두 문장으로 간결하게 설명하세요. 모든 문장 종결은 "~에요/~예요"체로 쓰고 "~습니다/~입니다/~이다"체는 쓰지 마세요."""

CONCEPT_STREAM_STRUCTURED_SYSTEM_PROMPT = STREAM_STRUCTURED_SYSTEM_PROMPT + """

개념을 처음 배우는 학습자에게 차근차근 알려 주듯 설명하세요. 모든 문장 종결은 "~에요/~예요"체로 쓰고 "~습니다/~입니다/~이다"체는 쓰지 마세요.
일반 개념 질문은 "1. 먼저 알아둘 용어", "2. 시대 배경", "3. 핵심 내용", "4. 암기 포인트" 순서로 구성하고, 각 row에는 쉬운 뜻과 왜 중요한지를 함께 설명하세요.
비교 질문은 대상별 기초 설명을 먼저 쓰고 공통점·차이점을 설명하세요. 관계 질문은 반드시 "1. 무슨 관계인지", "2. 관계의 근거", "3. 시험 포인트" 순서로 쓰고, 둘째 섹션에서 검색 근거를 바탕으로 연결 이유를 자세히 설명하세요. 검색 근거에 없는 사실은 덧붙이지 마세요."""


@dataclass(frozen=True)
class LLMConfig:
    model: str
    temperature: float = 0.0
    provider: str = "openai"


PROMPT_SNIPPET_MAX_CHARS = 160


def prompt_snippet(value: object) -> str:
    text = str(value or "").strip()
    return text if len(text) <= PROMPT_SNIPPET_MAX_CHARS else text[: PROMPT_SNIPPET_MAX_CHARS - 1].rstrip() + "…"


def load_llm_env() -> None:
    load_dotenv()


def compact_source(source: dict[str, Any], index: int) -> str:
    source_url = source.get("source_url") or ""
    image_url = source.get("original_image_url") or source.get("thumbnail_url") or ""
    metadata = source.get("metadata") or {}
    image_source = (metadata.get("image") or {}).get("source") or metadata.get("image_source") or ""
    parts = [
        f"[근거 {index}]",
        f"title: {source.get('title', '')}",
        f"source_type: {source.get('source_type', '')}",
        f"source_name: {source.get('source_name', '')}",
        f"snippet: {prompt_snippet(source.get('snippet'))}",
    ]
    if source_url:
        parts.append(f"source_url: {source_url}")
    if image_url:
        parts.append(f"image_url: {image_url}")
    if image_source:
        parts.append(f"image_source: {image_source}")
    return "\n".join(parts)


def compact_history(history: list[dict[str, str]] | None) -> str:
    if not history:
        return ""
    lines = []
    for item in history[-10:]:
        role = "사용자" if item.get("role") == "user" else "챗봇"
        content = re.sub(r"\s+", " ", str(item.get("content") or "")).strip()
        if content:
            lines.append(f"- {role}: {content[:500]}")
    return "\n".join(lines)


def prompt_context(sources: list[dict[str, Any]], history: list[dict[str, str]] | None) -> tuple[str, str]:
    context = "\n\n".join(compact_source(source, index) for index, source in enumerate(sources, start=1))
    return context or "검색 근거 없음", compact_history(history) or "이전 대화 없음"


def build_user_prompt(
    question: str,
    sources: list[dict[str, Any]],
    style: str,
    follow_up: bool,
    history: list[dict[str, str]] | None = None,
    include_source_summary: bool = True,
) -> str:
    context, history_context = prompt_context(sources, history)

    if style == "short":
        output_instruction = "출력 형식: 핵심 답만 1~2문장으로 짧게 답하세요. 제목, 표, 번호 섹션은 쓰지 마세요."
    else:
        output_instruction = (
            f"출력 형식: 교재 요약 노트 Markdown. 큰 제목, 1/2/3번 섹션, 표 또는 bullet{', 출처 요약' if include_source_summary else ''}을 포함하세요."
            if style == "textbook" and not follow_up
            else f"출력 형식: 설명형 Markdown. 핵심 답변, 이유/배경{', 출처 요약' if include_source_summary else ''}을 포함하세요."
        )
    source_rule = "- 출처 요약에는 사용한 title을 1~3개만 적고, 이미지 자료는 title 대신 image_source만 적으세요." if include_source_summary else "- 출처 요약은 쓰지 마세요."

    return f"""질문:
{question}

최근 대화:
{history_context}

검색 근거:
{context}

요구사항:
- {output_instruction}
- 근거에 없는 세부 사실을 새로 만들지 마세요.
- 근거 부족, 확인 불가, 전체를 설명하기 부족하다는 안내 문장은 쓰지 마세요.
- 최근 대화는 대명사와 후속 질문 해석에만 참고하고, 사실 답변은 검색 근거를 우선하세요.
- 검색 근거의 문장을 그대로 길게 베끼지 말고 학습용으로 재구성하세요.
- 한자나 한문 원문은 그대로 쓰지 말고, 현대 한국어 한글 표현으로 풀어 쓰세요.
{source_rule}
- 답변 본문만 출력하고, 후속 작업 제안이나 대화형 마무리 문장은 쓰지 마세요.
- Markdown 가로선(---)은 쓰지 마세요."""


def build_structured_prompt(
    question: str,
    sources: list[dict[str, Any]],
    follow_up: bool,
    history: list[dict[str, str]] | None = None,
) -> str:
    context, history_context = prompt_context(sources, history)

    mode = "follow_up_explanation" if follow_up else "textbook_note"
    return f"""질문:
{question}

최근 대화:
{history_context}

검색 근거:
{context}

아래 JSON 스키마를 정확히 지켜서 JSON 객체 하나만 반환하세요.
최근 대화는 대명사와 후속 질문 해석에만 참고하고, 사실 답변은 검색 근거를 우선하세요.
문자열 값 안에서 중요한 키워드는 별도 Markdown 없이 원문 키워드만 쓰세요.
한자나 한문 원문은 그대로 쓰지 말고, 현대 한국어 한글 표현으로 풀어 쓰세요.
없는 내용은 만들지 말고 빈 배열로 처리하세요. 근거 부족 안내 문장은 쓰지 마세요.
질문 의도에 맞게 필요한 수의 섹션을 직접 구성하세요.
섹션 heading에는 번호와 질문에 맞는 제목을 함께 쓰세요.
관계 질문은 반드시 "1. 무슨 관계인지", "2. 관계의 근거", "3. 시험 포인트" 순서로 구성하세요. 첫 섹션에서는 두 대상의 관계를 쉬운 말로 먼저 밝히고, 둘째 섹션에서는 검색 근거를 바탕으로 연결 이유를 자세히 설명하세요.
인물 단독 질문은 "1. 개요", "2. 주요 업적", "3. 역사적 역할" 순서로 구성하세요. 비교·관계 질문에는 이 규칙보다 해당 질문 형식을 우선하세요.
인물 단독 질문의 답변 title은 인물명만 쓰세요. "인물명 개요", "인물명 정리"처럼 다른 말을 덧붙이지 마세요.
인물 단독 질문의 각 표 행은 핵심 사실만 나열하지 말고, 검색 근거가 있으면 배경·내용·영향을 연결해 1~2문장으로 설명하세요.
검색 근거에 해당 내용이 전혀 없을 때만 그 섹션을 생략하고, 빈 표를 만들지 마세요.
예: 비교 질문은 비교 대상별 설명을 먼저 배치한 뒤 공통점과 차이점을 정리하세요.
비교 대상이 3개라면 "1. 첫 번째 키워드", "2. 두 번째 키워드", "3. 세 번째 키워드", "4. 공통점", "5. 차이점" 순서로 구성하세요.
각 비교 대상 섹션에는 그 대상의 핵심 내용만, 공통점과 차이점 섹션에는 대상들을 직접 비교한 내용만 넣으세요.
예: 개념 질문은 검색 근거에 맞는 주제별 제목으로 구성하세요.
각 항목의 설명은 반드시 해당 섹션 제목과 직접 관련된 내용만 쓰세요.

{{
  "answer_type": "{mode}",
  "title": "답변 제목",
  "summary": "한두 문장 요약",
  "sections": [
    {{
      "heading": "1. 섹션 제목",
      "items": [
        {{"term": "핵심어", "content": "설명"}}
      ]
    }}
  ],
  "exam_points": [],
  "highlights": ["강조할 핵심 키워드"],
  "source_titles": ["사용한 출처 title"]
}}"""


def build_stream_structured_prompt(question: str, sources: list[dict[str, Any]], follow_up: bool, history: list[dict[str, str]] | None = None) -> str:
    prompt = build_structured_prompt(question, sources, follow_up, history)
    return prompt.replace(
        "아래 JSON 스키마를 정확히 지켜서 JSON 객체 하나만 반환하세요.",
        "아래 정보를 JSON Lines 이벤트로 나누어 반환하세요. 이벤트마다 한 줄의 JSON 객체만 반환하세요.",
    ) + """

이벤트 예시:
{"type":"meta","title":"답변 제목","summary":"한두 문장 요약"}
{"type":"section","heading":"1. 섹션 제목"}
{"type":"row","term":"핵심어","content":"설명"}
{"type":"sources","source_titles":["사용한 출처 title"]}
{"type":"done"}"""


def sanitize_answer(answer: str) -> str:
    answer = re.sub(r"==([^=\n]+)==", r"\1", answer)
    lines = []
    for line in answer.splitlines():
        stripped = line.strip()
        if re.fullmatch(r"-{3,}|\*{3,}|_{3,}", stripped):
            continue
        if stripped.startswith("원하시면"):
            continue
        if any(term in stripped for term in DISCLAIMER_PATTERNS):
            continue
        lines.append(line.rstrip())
    return "\n".join(lines).strip()


# 모델이 검색 근거 부족을 이유로 답변을 회피하는 문구만 제거합니다.
DISCLAIMER_PATTERNS = (
    "근거는 부족",
    "근거가 부족",
    "근거 부족",
    "설명할 만큼의 근거",
    "충분한 근거가 없",
    "충분한 근거를 찾지",
    "충분한 근거를 확인할 수 없",
    "충분한 근거를 확인하지 못",
    "충분한 근거를 확인하기 어렵",
)


def _sanitize_value(value: Any) -> Any:
    if isinstance(value, str):
        return sanitize_answer(value)
    if isinstance(value, list):
        return [_sanitize_value(item) for item in value]
    if isinstance(value, dict):
        return {key: _sanitize_value(item) for key, item in value.items()}
    return value


def extract_json_object(text: str) -> dict[str, Any]:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?\s*", "", stripped)
        stripped = re.sub(r"\s*```$", "", stripped)

    try:
        parsed = json.loads(stripped)
    except json.JSONDecodeError:
        start = stripped.find("{")
        end = stripped.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise
        parsed = json.loads(stripped[start : end + 1])

    if not isinstance(parsed, dict):
        raise ValueError("LLM structured response must be a JSON object.")
    return parsed


def normalize_structured_answer(value: dict[str, Any]) -> dict[str, Any]:
    return {
        "answer_type": str(value.get("answer_type") or "textbook_note"),
        "title": sanitize_answer(str(value.get("title") or "한국사 개념 정리")),
        "summary": sanitize_answer(str(value.get("summary") or "")),
        "sections": _sanitize_value(value.get("sections")) if isinstance(value.get("sections"), list) else [],
        "exam_points": value.get("exam_points") if isinstance(value.get("exam_points"), list) else [],
        "highlights": _sanitize_value(value.get("highlights")) if isinstance(value.get("highlights"), list) else [],
        "source_titles": value.get("source_titles") if isinstance(value.get("source_titles"), list) else [],
    }


class LLMAnswerGenerator:
    def __init__(self, config: LLMConfig) -> None:
        self.config = config

    @classmethod
    def from_env(cls, model: str | None = None) -> "LLMAnswerGenerator":
        load_llm_env()
        selected_model = model or os.getenv("OPENAI_CHAT_MODEL", "gpt-5.4-mini")

        return cls(
            LLMConfig(
                model=selected_model,
                temperature=float(os.getenv("CHAT_TEMPERATURE", "0")),
            )
        )

    def generate(
        self,
        question: str,
        sources: list[dict[str, Any]],
        style: str,
        follow_up: bool = False,
        history: list[dict[str, str]] | None = None,
        include_source_summary: bool = True,
        explanation_level: str = "",
    ) -> str:
        system_prompt = (
            FOUNDATION_EXPLANATION_SYSTEM_PROMPT if explanation_level == "foundation"
            else CORE_EXPLANATION_SYSTEM_PROMPT if explanation_level == "core"
            else FOLLOW_UP_SYSTEM_PROMPT if follow_up else SYSTEM_PROMPT
        )
        user_prompt = build_user_prompt(question, sources, style, follow_up, history, include_source_summary)
        return sanitize_answer(self._generate_openai(system_prompt, user_prompt))

    def generate_stream(
        self,
        question: str,
        sources: list[dict[str, Any]],
        style: str,
        follow_up: bool = False,
        history: list[dict[str, str]] | None = None,
        include_source_summary: bool = True,
        explanation_level: str = "",
    ) -> Iterator[str]:
        system_prompt = (
            FOUNDATION_EXPLANATION_SYSTEM_PROMPT if explanation_level == "foundation"
            else CORE_EXPLANATION_SYSTEM_PROMPT if explanation_level == "core"
            else FOLLOW_UP_SYSTEM_PROMPT if follow_up else SYSTEM_PROMPT
        )
        user_prompt = build_user_prompt(question, sources, style, follow_up, history, include_source_summary)
        chunks = OpenAI().chat.completions.create(
            model=self.config.model,
            temperature=self.config.temperature,
            stream=True,
            messages=[{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}],
        )
        for chunk in chunks:
            if chunk.choices:
                yield chunk.choices[0].delta.content or ""

    def generate_structured(
        self,
        question: str,
        sources: list[dict[str, Any]],
        follow_up: bool = False,
        history: list[dict[str, str]] | None = None,
    ) -> dict[str, Any]:
        user_prompt = build_structured_prompt(question, sources, follow_up, history)
        raw_answer = self._generate_openai(STRUCTURED_SYSTEM_PROMPT, user_prompt, json_mode=True)
        return normalize_structured_answer(extract_json_object(raw_answer))

    def generate_structured_stream(
        self, question: str, sources: list[dict[str, Any]], follow_up: bool = False, history: list[dict[str, str]] | None = None, explanation_level: str = ""
    ) -> Iterator[dict[str, Any]]:
        prompt = build_stream_structured_prompt(question, sources, follow_up, history)
        system_prompt = (
            FOUNDATION_STREAM_STRUCTURED_SYSTEM_PROMPT if explanation_level == "foundation"
            else CORE_STREAM_STRUCTURED_SYSTEM_PROMPT if explanation_level == "core"
            else CONCEPT_STREAM_STRUCTURED_SYSTEM_PROMPT if explanation_level == "concept"
            else STREAM_STRUCTURED_SYSTEM_PROMPT
        )
        chunks = OpenAI().chat.completions.create(
            model=self.config.model,
            temperature=self.config.temperature,
            stream=True,
            messages=[{"role": "system", "content": system_prompt}, {"role": "user", "content": prompt}],
        )
        fragments = (chunk.choices[0].delta.content or "" for chunk in chunks if chunk.choices)
        yield from self._parse_stream_events(fragments)

    @staticmethod
    def _parse_stream_events(fragments: Iterator[str]) -> Iterator[dict[str, Any]]:
        buffer = ""
        for fragment in fragments:
            buffer += fragment
            while "\n" in buffer:
                line, buffer = buffer.split("\n", 1)
                event = LLMAnswerGenerator._try_parse_event(line)
                if event is not None:
                    yield event
        if buffer.strip():
            event = LLMAnswerGenerator._try_parse_event(buffer)
            if event is not None:
                yield event

    @staticmethod
    def _try_parse_event(line: str) -> dict[str, Any] | None:
        try:
            event = json.loads(line.strip())
        except json.JSONDecodeError:
            return None
        return event if isinstance(event, dict) and event.get("type") in {"meta", "section", "row", "sources", "done"} else None

    def _generate_openai(self, system_prompt: str, user_prompt: str, json_mode: bool = False) -> str:
        client = OpenAI()
        kwargs: dict[str, Any] = {
            "model": self.config.model,
            "temperature": self.config.temperature,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        }
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        response = client.chat.completions.create(**kwargs)
        return (response.choices[0].message.content or "").strip()
