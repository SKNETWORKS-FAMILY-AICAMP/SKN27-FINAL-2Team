"""구형 Graph/RAG 검색 결과를 material 근거와 연표 자료로 조립한다."""
from __future__ import annotations

import re
from typing import Any

from question_generation.core.text import compact
from question_generation.graph_path.graph import (
    graph_comparison_sources,
    graph_inquiry_sources,
    graph_sources,
    graph_timeline_sources,
    retrieve_encykorea_sources,
    retrieve_material_sources,
    source_contradicts_context,
)
from question_generation.graph_path.query_plan import retrieval_plan_source, source_slots, text_mentions


def fallback_identity_material(selection: dict[str, Any], context: dict[str, Any], plan: dict[str, Any]) -> str:
    """API 지문이 없을 때 Graph 식별 단서 한 개를 안전하게 사용한다."""
    if selection.get("question_task") == "standard_select" or plan.get("intent") in {
        "timeline_order",
        "timeline_position",
        "period_between",
    }:
        return ""
    for clue in context.get("required_clues", []):
        text = compact(str(clue), 220)
        if text and not text_mentions(text, [selection["topic"]]):
            return text
    return ""


def build_material_sources(
    *,
    driver: Any,
    retriever: Any,
    selection: dict[str, Any],
    context: dict[str, Any],
    plan: dict[str, Any],
    top_k: int,
    encykorea_api_key: str,
    timeout: int,
    encykorea_sources: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """여러 Graph/RAG 출처를 지문 슬롯 순서에 맞춰 합친다."""
    sources = [retrieval_plan_source(plan), *graph_sources(context, plan)]
    if plan.get("needs_timeline"):
        sources.extend(graph_timeline_sources(driver, selection, context, plan))
    if plan.get("intent") == "comparison":
        sources.extend(graph_comparison_sources(driver, selection, context))
    if plan.get("intent") == "inquiry":
        sources.extend(graph_inquiry_sources(context))
    evidence_slots = [
        slot
        for slot in plan.get("slots", [])
        if slot not in {"before_event", "after_event", "period_start", "period_end", "sequence_events"}
    ]
    raw_encykorea_sources = encykorea_sources
    if raw_encykorea_sources is None:
        raw_encykorea_sources = retrieve_encykorea_sources(selection["topic"], encykorea_api_key, timeout)
    for cached_source in raw_encykorea_sources:
        source = dict(cached_source)
        if source_contradicts_context(source, context, selection):
            continue
        source["source_alignment"] = "graph_anchor"
        source["retrieval_slots"] = evidence_slots
        sources.append(source)
    for source in retrieve_material_sources(retriever, selection, top_k):
        if source_contradicts_context(source, context, selection):
            continue
        source["retrieval_slots"] = evidence_slots
        sources.append(source)
    return sources


def strip_timeline_prefix(text: str) -> str:
    """연표 근거 앞의 번호·기호를 제거한다."""
    return re.sub(r"^(이전|이후|동시기)\s*사실:\s*", "", compact(text, 300))


def timeline_source_is_specific(source: dict[str, Any]) -> bool:
    """연표 조각이 순서 판단에 충분한 구체 사건인지 판정한다."""
    text = strip_timeline_prefix(str(source.get("snippet") or ""))
    name, _, description = text.partition(" - ")
    name = re.sub(r"\([^)]*\)", "", name).strip()
    description = description.strip()
    if re.search(r"(시대|시기|년대|기)(정치)?(사건|정책|운동|문화|사회)$", name):
        return False
    if re.search(r"(기에|시기에|년대|때에).*?(발생한|있었던|여러).*?(사건|정책|운동)", description):
        return False
    return True


def first_source_for_slot(sources: list[dict[str, Any]], slot: str) -> dict[str, Any] | None:
    """검색 계획의 특정 슬롯에 배정된 첫 근거를 반환한다."""
    return next(
        (source for source in sources if slot in source_slots(source) and source.get("source_type") != "retrieval_plan"),
        None,
    )


def structured_timeline_material(sources: list[dict[str, Any]], plan: dict[str, Any]) -> dict[str, Any] | None:
    """순서·두 사건 사이 유형을 LLM 없이 구조화된 연표 material로 조립한다."""
    timeline_sources = [source for source in sources if timeline_source_is_specific(source)]
    if plan.get("intent") == "period_between":
        start = first_source_for_slot(timeline_sources, "period_start")
        target = first_source_for_slot(timeline_sources, "target_between")
        end = first_source_for_slot(timeline_sources, "period_end")
        if start and target and end:
            return {
                "material": f"(가) {strip_timeline_prefix(start['snippet'])}\n(나) {strip_timeline_prefix(end['snippet'])}",
                "answer_fact_basis": [strip_timeline_prefix(target["snippet"])],
            }
    if plan.get("intent") == "timeline_position":
        before = first_source_for_slot(timeline_sources, "before_event")
        target = first_source_for_slot(timeline_sources, "target_event")
        after = first_source_for_slot(timeline_sources, "after_event")
        if before and target and after:
            return {
                "material": f"{strip_timeline_prefix(before['snippet'])}\n(가)\n{strip_timeline_prefix(after['snippet'])}",
                "answer_fact_basis": [strip_timeline_prefix(target["snippet"])],
            }
    if plan.get("intent") == "timeline_order":
        phase_sources = [
            first_source_for_slot(
                [source for source in timeline_sources if source.get("retrieval_slot") == phase],
                "sequence_events",
            )
            for phase in ("before", "during", "after")
        ]
        sequence = [source for source in phase_sources if source]
        if len(sequence) < 3:
            sequence = [
                source
                for source in timeline_sources
                if "sequence_events" in source_slots(source) and source.get("source_type") != "retrieval_plan"
            ][:3]
        if len(sequence) == 3:
            labels = ("가", "나", "다")
            material = "\n".join(
                f"({label}) {strip_timeline_prefix(source['snippet'])}"
                for label, source in zip(labels, sequence, strict=True)
            )
            return {
                "material": material,
                "answer_fact_basis": ["(가) - (나) - (다) 순서로 배열한다."],
            }
    return None
