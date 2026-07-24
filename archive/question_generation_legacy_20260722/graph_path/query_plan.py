"""question_task와 stem_pattern을 필요한 검색 슬롯으로 변환하는 구형 Graph/RAG 계획기.

고정 문제은행/ChoiceFact 경로는 이미 저장된 근거를 사용하므로 새 검색을 하지 않는다.
이 모듈은 ``legacy_pack``이 Graph/RAG에서 지문·오답 근거를 즉시 조회할 때 사용한다.
"""

from __future__ import annotations

import re
from typing import Any

from question_generation.core.text import compact


def query_terms(*values: str) -> str:
    """여러 검색어 문자열을 순서 보존·중복 제거해 하나로 합친다."""
    seen: set[str] = set()
    terms: list[str] = []
    for value in values:
        for term in str(value or "").split():
            if term and term not in seen:
                seen.add(term)
                terms.append(term)
    return " ".join(terms)


def v41_intent_terms(selection: dict[str, Any]) -> str:
    """V41 유형 필드를 Graph/RAG 검색 의도 보조어로 변환한다."""
    task = str(selection.get("question_task") or "")
    material_type = str(selection.get("material_type") or "")
    axis = f"{selection.get('major_type', '')} {selection.get('minor_type', '')}"

    material_terms = {
        "자료 제시문": "사료 배경 주체 행동",
        "짧은 설명 자료": "대표 사실 핵심 단서",
        "연표 자료": "연도 사건 전후",
        "탐구 자료": "탐구 조사 장소 자료",
        "사건 배열 자료": "사건 순서 전개",
    }.get(material_type, "")

    if task == "order":
        task_terms = "사건 순서 전개"
    elif task in {"period_between", "timeline_position"}:
        task_terms = "시기 전후 연도"
    elif task == "negative_select":
        task_terms = "특징 내용 비교"
    elif task == "multi_select_combo":
        task_terms = "공통점 특징 비교"
    elif task == "map_location":
        task_terms = "지역 위치 장소 지도"
    elif "전후 시기 판단" in axis:
        task_terms = "시기 전후 재위 사실"
    elif "자료 기반 시대·대상 추론" in axis:
        task_terms = "대상 배경 특징 기능"
    elif "기본 사실·개념 확인" in axis:
        task_terms = "개념 정의 특징"
    elif "의의·영향·결과 평가" in axis:
        task_terms = "의의 영향 결과"
    elif "비교·공통점 도출" in axis:
        task_terms = "비교 공통점 차이"
    else:
        task_terms = "핵심 사실 특징"

    return query_terms(material_terms, task_terms)


def material_query(selection: dict[str, Any]) -> str:
    """topic·시대·유형·의도를 합친 지문 근거 검색 문자열을 만든다."""
    return query_terms(
        str(selection.get("topic") or ""),
        str(selection.get("era") or ""),
        str(selection.get("topic_type") or ""),
        v41_intent_terms(selection),
    )


def retrieval_plan(selection: dict[str, Any]) -> dict[str, Any]:
    """문제 유형에 따라 필요한 근거 슬롯과 검색 의도를 결정한다."""
    task = str(selection.get("question_task") or "")
    material_type = str(selection.get("material_type") or "")
    axis = f"{selection.get('major_type', '')} {selection.get('minor_type', '')}"

    if task == "order" or "사건·자료 순서 배열" in axis:
        intent = "timeline_order"
        slots = ["sequence_events", "order_basis"]
    elif task == "timeline_position" or "연표·흐름 빈칸" in axis:
        intent = "timeline_position"
        slots = ["before_event", "target_event", "after_event", "position_basis"]
    elif task == "period_between":
        intent = "period_between"
        slots = ["period_start", "target_between", "period_end"]
    elif "전후 시기 판단" in axis:
        intent = "timeline_compare"
        slots = ["identity_clue", "during_fact", "before_after_context"]
    elif "의의·영향·결과 평가" in axis:
        intent = "effect"
        slots = ["identity_clue", "effect_basis"]
    elif "비교·공통점 도출" in axis:
        intent = "comparison"
        slots = ["identity_clue", "comparison_basis"]
    elif "기본 사실·개념 확인" in axis:
        intent = "concept"
        slots = ["definition", "core_feature"]
    elif "탐구" in material_type or "탐구" in axis:
        intent = "inquiry"
        slots = ["inquiry_target", "search_keyword", "answer_basis"]
    else:
        intent = "identity"
        slots = ["identity_clue", "answer_basis"]

    return {
        "intent": intent,
        "slots": slots,
        "needs_timeline": intent in {"timeline_order", "timeline_position", "period_between", "timeline_compare"},
        "needs_graph": True,
        "needs_vector": True,
    }


def text_mentions(text: str, terms: list[str]) -> bool:
    """공백을 무시하고 텍스트가 검색어 중 하나를 포함하는지 확인한다."""
    compacted = (text or "").replace(" ", "")
    return any(term and term.replace(" ", "") in compacted for term in terms)


def retrieval_plan_source(plan: dict[str, Any]) -> dict[str, Any]:
    """검색 계획 자체를 추적 가능한 가상 source record로 만든다."""
    return {
        "chunk_id": f"retrieval_plan:{plan['intent']}",
        "source_type": "retrieval_plan",
        "title": "v41 retrieval plan",
        "score": 1.0,
        "snippet": f"intent={plan['intent']}; required_slots={', '.join(plan['slots'])}",
    }


def source_slots(source: dict[str, Any]) -> set[str]:
    """source metadata에서 담당하는 근거 슬롯 집합을 읽는다."""
    slots = set(source.get("retrieval_slots") or [])
    if not slots and source.get("retrieval_slot"):
        slots.add(str(source["retrieval_slot"]))
    return {slot for slot in slots if slot}
