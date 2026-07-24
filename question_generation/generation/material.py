"""Closed-pack의 지문 근거와 계약으로 GPT 지문·발문을 생성한다."""

from __future__ import annotations

import json
from typing import Any

from openai import OpenAI


def chat_json(
    *,
    base_url: str,
    api_key: str,
    model: str,
    messages: list[dict[str, str]],
    temperature: float,
    timeout: int,
    max_retries: int,
) -> dict[str, Any]:
    """OpenAI Chat Completions를 호출하고 JSON 객체 응답만 반환한다."""
    body: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "response_format": {"type": "json_object"},
    }
    if not model.startswith("gpt-5"):
        body["temperature"] = temperature
    client = OpenAI(api_key=api_key, base_url=base_url, timeout=timeout, max_retries=max_retries)
    response = client.chat.completions.create(**body)
    return json.loads(response.choices[0].message.content or "{}")


def material_few_shot_messages(examples: dict[str, Any] | list[dict[str, Any]] | None) -> list[dict[str, str]]:
    """실제 기출 지문을 내용이 아닌 출력 형식 예시 대화로 변환한다."""
    items = examples if isinstance(examples, list) else ([examples] if isinstance(examples, dict) else [])
    messages: list[dict[str, str]] = []
    for item in items:
        metadata = {
            key: item.get(key)
            for key in ("material_type", "question_task", "stem_pattern", "difficulty_label")
        }
        messages.extend(
            [
                {
                    "role": "user",
                    "content": (
                        "다음은 실제 한능검 기출 지문의 형식 학습 사례다. 역사 내용은 다른 문항에 재사용하지 않는다.\n"
                        f"조건: {json.dumps(metadata, ensure_ascii=False)}\nJSON material과 question을 함께 작성한다."
                    ),
                },
                {
                    "role": "assistant",
                    "content": json.dumps(
                        {"material": item.get("material", ""), "question": item.get("question", "")},
                        ensure_ascii=False,
                    ),
                },
            ]
        )
    return messages


def generate_material(
    *,
    selection: dict[str, Any],
    sources: list[dict[str, Any]],
    material_example: dict[str, Any] | list[dict[str, Any]] | None,
    material_rules: str,
    model: str,
    base_url: str,
    api_key: str,
    temperature: float,
    timeout: int,
    max_retries: int,
    answer_fact_hints: list[str] | None = None,
    material_contract: dict[str, Any] | None = None,
    retry_feedback: str = "",
) -> dict[str, Any]:
    """지문 전용 근거·정답 원문·형식 계약을 프롬프트로 묶어 GPT 지문을 생성한다."""
    source_text = "\n".join(
        f"- [{source.get('chunk_id') or 'unknown'}] {source['title']}: {source['snippet']}"
        for source in sources
    )
    answer_fact_hints = [hint for hint in (answer_fact_hints or []) if str(hint).strip()]
    material_contract = material_contract or {}
    stem_pattern = str(selection.get("stem_pattern") or "")
    difficulty_rule = {
        1: (
            "- 대표 활동·사건·시기처럼 널리 알려진 식별 단서를 분명하게 제시한다.\n"
            "- 단서를 과도하게 숨기거나 지엽적인 사실만 사용하지 않는다.\n"
            "- 수험자가 자료에서 대상을 쉽게 식별한 뒤 선지의 대표 사실을 판단할 수 있게 쓴다."
        ),
        2: (
            "- 대상명이나 정답 선지의 사실을 직접 밝히지 않고, 관련 단서를 해석해 대상을 식별하게 한다.\n"
            "- 하나의 단어만 보고 즉시 답이 나오지 않도록 시대·상황·활동 중 서로 보완되는 단서를 선택한다.\n"
            "- 대상을 식별한 뒤 선지의 별도 사실을 한 번 더 판단하게 하되, 지엽적인 암기를 요구하지 않는다."
        ),
        3: (
            "- 대상명·정확한 연도·대표 사건명을 한꺼번에 노출하지 않고 사료식 표현이나 우회 단서를 사용한다.\n"
            "- 단서를 해석해 대상을 식별한 뒤, 선지에서 가까운 사실들을 다시 비교해야 풀리도록 지문과 정답 사실을 분리한다.\n"
            "- 정답 선지의 핵심 표현을 바꾸어 반복하거나, 자료만 읽고 정답 선지까지 즉시 확정되게 쓰지 않는다."
        ),
    }.get(selection.get("target_score"), "")
    material_rules = material_rules or "- 정답명을 직접 노출하지 않고, topic을 추론하게 쓴다."
    if selection.get("material_type"):
        focus_rule = ""
        if selection.get("question_task") == "standard_select":
            focus_rule = """
- 하나의 사료·장면·대화·기록만 골라 끝까지 유지한다.
- 서로 독립된 역사 사실은 최대 2개만 사용한다. 같은 사실을 표현만 바꿔 반복하지 않는다.
- 연도·인물·장소·원인·전개·결과·의의를 한꺼번에 나열하지 않는다.
- 자료 뒤에 해설, 정리, 결론, 역사적 의의를 덧붙이지 않는다.
""".strip()
        safe_seed = {
            key: selection.get(key)
            for key in (
                "seed_id", "topic", "topic_type", "material_type", "major_type", "minor_type",
                "question_task", "question_task_instruction", "difficulty_label", "target_score",
                "stem_pattern", "relation_axis_id",
            )
        }
        if selection.get("question_task") == "standard_select" and stem_pattern == "fill_blank":
            reference_rule = (
                "- material 안에 (가)를 정확히 한 번 넣고, 정답 사실이 들어갈 위치가 드러나게 앞뒤 맥락을 구성한다.\n"
                "- <u>...</u> 밑줄 표시는 사용하지 않는다."
            )
        else:
            reference_rule = ""
        messages = [
            {
                "role": "system",
                "content": (
                    "너는 한국사능력검정시험 심화형 문항의 지문과 발문을 만드는 도구다. "
                    "few-shot 기출과 비슷한 완성도·정보 밀도·구체성·표시 길이로 쓰되 문체와 구조만 참고하고 역사 내용은 재사용하지 않는다. "
                    "material과 question 모두 현재 요청에 제공된 지문 단서만 사용한다. "
                    "근거에 없는 대상·사실·시점·전후·원인·결과·영향·범위·표시 조건을 만들거나 추론해 추가하지 않는다. "
                    "반드시 JSON 객체만 출력한다."
                ),
            },
            *material_few_shot_messages(material_example),
            {
                "role": "user",
                "content": f"""
다음 seed와 지문 전용 RAG 근거만 사용해 SLLM 입력용 material과 question을 만들어라.

규칙:
- 난이도별 지문 규칙: {difficulty_rule}
- forbidden_answer_facts 금지는 다른 모든 작성 조건보다 우선한다.
- forbidden_answer_facts의 문장뿐 아니라 동의어 치환, 일반화, 축약, 주어 생략, 원인·결과 변환도 material에 사용하지 않는다.
- 지문 전용 RAG 근거가 forbidden_answer_facts와 같은 사실을 말하면 그 근거는 사용하지 않는다. 안전한 단서가 없으면 금지 사실을 바꿔 쓰지 말고 material을 빈 문자열로 반환한다.
- 제공된 지문 단서에서 식별 단서만 고른다. 외부 지식이나 새로운 업적·정책·결과·활동 사실을 추가하지 않는다.
- material_contract.constraints를 모두 지키고 관계축과 풀이 구조를 유지하되, 정답 사실 자체를 지문에 쓰지 않는다.
- 근거 길이와 개수로 난이도를 만들지 않는다. 필요한 근거만 선택하며 최소 단서 개수는 없다.
- used_evidence_ids에는 실제 사용한 지문 근거의 chunk_id만 적는다.
{reference_rule}
- question은 material에 명시된 대상과 조건만 자연스럽게 가리킨다. material에 없는 사실·시점·전후·원인·결과·영향·범위·표시를 추가하지 않는다.
- question에는 HTML 태그를 출력하지 않는다.
- material_type 규칙과 문장 수·길이 제한을 반드시 지킨다.
- 지문은 현대 해설문이 아니라 당시 기록·교서·보고·회고처럼 읽히는 사료체로 쓴다.
- 근거에 없는 직접 인용·1인칭·고어를 만들지 않는다.
- 백과사전식 전체 요약이나 문제를 설명하는 메타 문장을 쓰지 않는다.
{focus_rule}

material_type별 작성 규칙({selection['material_type']}):
{material_rules}

seed:
{json.dumps(safe_seed, ensure_ascii=False, indent=2)}

forbidden_answer_facts:
{json.dumps(answer_fact_hints, ensure_ascii=False, indent=2)}

지문 전용 RAG 근거:
{source_text}

material_contract:
{json.dumps(material_contract, ensure_ascii=False, indent=2)}

재시도 피드백:
{retry_feedback or "없음"}

출력 형식:
{{
  "material": "...",
  "question": "...",
  "used_evidence_ids": ["chunk_id"]
}}
""".strip(),
            },
        ]
        result = chat_json(
            base_url=base_url,
            api_key=api_key,
            model=model,
            messages=messages,
            temperature=temperature,
            timeout=timeout,
            max_retries=max_retries,
        )
        used_ids = result.get("used_evidence_ids")
        return {
            "material": str(result.get("material") or "").strip(),
            "question": str(result.get("question") or "").strip(),
            "used_evidence_ids": [str(value) for value in used_ids] if isinstance(used_ids, list) else [],
        }

    raise ValueError("material_type is required")
