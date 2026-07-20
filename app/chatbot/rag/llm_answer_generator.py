from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
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


@dataclass(frozen=True)
class LLMConfig:
    provider: str
    model: str
    temperature: float = 0.0
    ollama_base_url: str = "http://localhost:11434"


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
        f"snippet: {source.get('snippet', '')}",
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
예: 관계 질문은 "1. 관계 개요", "2. 연결 근거", "3. 시험 포인트"처럼 구성하세요.
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
        if any(term in stripped for term in ("근거는 부족", "근거가 부족", "근거 부족", "설명할 만큼의 근거", "충분한 근거")):
            continue
        lines.append(line.rstrip())
    return "\n".join(lines).strip()


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
        "title": str(value.get("title") or "한국사 개념 정리"),
        "summary": sanitize_answer(str(value.get("summary") or "")),
        "sections": value.get("sections") if isinstance(value.get("sections"), list) else [],
        "exam_points": value.get("exam_points") if isinstance(value.get("exam_points"), list) else [],
        "highlights": value.get("highlights") if isinstance(value.get("highlights"), list) else [],
        "source_titles": value.get("source_titles") if isinstance(value.get("source_titles"), list) else [],
    }


class LLMAnswerGenerator:
    def __init__(self, config: LLMConfig) -> None:
        self.config = config

    @classmethod
    def from_env(cls, provider: str | None = None, model: str | None = None) -> "LLMAnswerGenerator":
        load_llm_env()
        selected_provider = (provider or os.getenv("CHAT_LLM_PROVIDER", "openai")).lower()
        if selected_provider == "openai":
            selected_model = model or os.getenv("OPENAI_CHAT_MODEL", "gpt-5.4-mini")
        elif selected_provider == "ollama":
            selected_model = model or os.getenv("OLLAMA_CHAT_MODEL", "gemma4:2b")
        else:
            raise ValueError("provider는 openai 또는 ollama만 지원합니다.")

        return cls(
            LLMConfig(
                provider=selected_provider,
                model=selected_model,
                temperature=float(os.getenv("CHAT_TEMPERATURE", "0")),
                ollama_base_url=os.getenv("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/"),
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
    ) -> str:
        system_prompt = FOLLOW_UP_SYSTEM_PROMPT if follow_up else SYSTEM_PROMPT
        user_prompt = build_user_prompt(question, sources, style, follow_up, history, include_source_summary)
        if self.config.provider == "openai":
            return sanitize_answer(self._generate_openai(system_prompt, user_prompt))
        if self.config.provider == "ollama":
            return sanitize_answer(self._generate_ollama(system_prompt, user_prompt))
        raise ValueError(f"지원하지 않는 provider입니다: {self.config.provider}")

    def generate_structured(
        self,
        question: str,
        sources: list[dict[str, Any]],
        follow_up: bool = False,
        history: list[dict[str, str]] | None = None,
    ) -> dict[str, Any]:
        user_prompt = build_structured_prompt(question, sources, follow_up, history)
        if self.config.provider == "openai":
            raw_answer = self._generate_openai(STRUCTURED_SYSTEM_PROMPT, user_prompt, json_mode=True)
        elif self.config.provider == "ollama":
            raw_answer = self._generate_ollama(STRUCTURED_SYSTEM_PROMPT, user_prompt)
        else:
            raise ValueError(f"지원하지 않는 provider입니다: {self.config.provider}")
        return normalize_structured_answer(extract_json_object(raw_answer))

    def generate_structured_stream(
        self, question: str, sources: list[dict[str, Any]], follow_up: bool = False, history: list[dict[str, str]] | None = None
    ) -> Iterator[dict[str, Any]]:
        prompt = build_stream_structured_prompt(question, sources, follow_up, history)
        if self.config.provider == "openai":
            chunks = OpenAI().chat.completions.create(
                model=self.config.model,
                temperature=self.config.temperature,
                stream=True,
                messages=[{"role": "system", "content": STREAM_STRUCTURED_SYSTEM_PROMPT}, {"role": "user", "content": prompt}],
            )
            fragments = (chunk.choices[0].delta.content or "" for chunk in chunks if chunk.choices)
        elif self.config.provider == "ollama":
            fragments = self._generate_ollama_stream(STREAM_STRUCTURED_SYSTEM_PROMPT, prompt)
        else:
            raise ValueError(f"지원하지 않는 provider입니다: {self.config.provider}")
        yield from self._parse_stream_events(fragments)

    @staticmethod
    def _parse_stream_events(fragments: Iterator[str]) -> Iterator[dict[str, Any]]:
        buffer = ""
        for fragment in fragments:
            buffer += fragment
            while "\n" in buffer:
                line, buffer = buffer.split("\n", 1)
                try:
                    event = json.loads(line.strip())
                except json.JSONDecodeError:
                    continue
                if isinstance(event, dict) and event.get("type") in {"meta", "section", "row", "sources", "done"}:
                    yield event

    def _generate_ollama_stream(self, system_prompt: str, user_prompt: str) -> Iterator[str]:
        payload = {"model": self.config.model, "stream": True, "messages": [{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}], "options": {"temperature": self.config.temperature}}
        request = urllib.request.Request(f"{self.config.ollama_base_url}/api/chat", data=json.dumps(payload, ensure_ascii=False).encode("utf-8"), headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(request, timeout=120) as response:
            for line in response:
                data = json.loads(line.decode("utf-8"))
                yield data.get("message", {}).get("content") or ""

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

    def _generate_ollama(self, system_prompt: str, user_prompt: str) -> str:
        payload = {
            "model": self.config.model,
            "stream": False,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "options": {"temperature": self.config.temperature},
        }
        request = urllib.request.Request(
            f"{self.config.ollama_base_url}/api/chat",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                data = json.loads(response.read().decode("utf-8"))
        except urllib.error.URLError as exc:
            raise RuntimeError(
                f"Ollama 호출에 실패했습니다. Ollama 서버와 모델({self.config.model})이 준비됐는지 확인해 주세요."
            ) from exc
        return (data.get("message", {}).get("content") or "").strip()
