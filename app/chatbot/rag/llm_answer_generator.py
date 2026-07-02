from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
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


def build_user_prompt(
    question: str,
    sources: list[dict[str, Any]],
    style: str,
    follow_up: bool,
    history: list[dict[str, str]] | None = None,
    include_source_summary: bool = True,
) -> str:
    context = "\n\n".join(compact_source(source, index) for index, source in enumerate(sources, start=1))
    if not context:
        context = "검색 근거 없음"
    history_context = compact_history(history) or "이전 대화 없음"

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
    context = "\n\n".join(compact_source(source, index) for index, source in enumerate(sources, start=1))
    if not context:
        context = "검색 근거 없음"
    history_context = compact_history(history) or "이전 대화 없음"

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
개념 정리 답변은 항상 3개 섹션, 각 섹션 2개 항목을 기본으로 작성하세요.
섹션 heading은 "1. 정치와 제도", "2. 문화와 업적", "3. 시험 핵심 정리"처럼 번호와 제목을 함께 쓰세요.
각 항목의 설명은 반드시 해당 섹션 제목의 범주와 맞아야 합니다.
정치와 제도에는 통치, 왕권, 관제, 법, 군사, 영토 확장 내용을 넣고, 문화와 업적에는 불교, 예술, 건축, 편찬, 과학, 문화재 내용을 넣으세요.
섹션 범주와 맞지 않는 근거는 억지로 넣지 말고 더 맞는 섹션으로 옮기거나 제외하세요.

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


EXAM_POINT_BLOCK_TERMS = (
    "단정",
    "근거",
    "부족",
    "확실",
    "추정",
    "이름",
    "부인",
    "어머니",
    "아버지",
    "낳았다",
    "나온다",
    "제공된",
    "검색",
    "출처",
)
EXAM_POINT_ALLOW_TERMS = (
    "한능검",
    "시험",
    "출제",
    "암기",
    "시대",
    "왕",
    "정책",
    "제도",
    "사건",
    "개혁",
    "전쟁",
    "운동",
    "조약",
    "문화",
    "경제",
    "정치",
    "사회",
    "불교",
    "유교",
    "토지",
    "세금",
    "관청",
    "업적",
    "비교",
    "순서",
    "흐름",
)


def filter_exam_points(points: list[Any]) -> list[str]:
    filtered: list[str] = []
    for point in points:
        text = re.sub(r"\s+", " ", str(point or "")).strip()
        if not text:
            continue
        if any(term in text for term in EXAM_POINT_BLOCK_TERMS) and not any(
            term in text for term in EXAM_POINT_ALLOW_TERMS
        ):
            continue
        if text not in filtered:
            filtered.append(text)
    return filtered[:5]


def normalize_structured_answer(value: dict[str, Any]) -> dict[str, Any]:
    exam_points = value.get("exam_points") if isinstance(value.get("exam_points"), list) else []
    return {
        "answer_type": str(value.get("answer_type") or "textbook_note"),
        "title": str(value.get("title") or "한국사 개념 정리"),
        "summary": sanitize_answer(str(value.get("summary") or "")),
        "sections": value.get("sections") if isinstance(value.get("sections"), list) else [],
        "exam_points": filter_exam_points(exam_points),
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
            raw_answer = self._generate_openai(STRUCTURED_SYSTEM_PROMPT, user_prompt)
        elif self.config.provider == "ollama":
            raw_answer = self._generate_ollama(STRUCTURED_SYSTEM_PROMPT, user_prompt)
        else:
            raise ValueError(f"지원하지 않는 provider입니다: {self.config.provider}")
        return normalize_structured_answer(extract_json_object(raw_answer))

    def _generate_openai(self, system_prompt: str, user_prompt: str) -> str:
        client = OpenAI()
        response = client.chat.completions.create(
            model=self.config.model,
            temperature=self.config.temperature,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        )
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
