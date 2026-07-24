"""구형 Graph 검색 계획에 필요한 근거 슬롯 검사를 제공한다."""
from __future__ import annotations

from typing import Any

from question_generation.graph_path.query_plan import source_slots


def material_source_status(plan: dict[str, Any], sources: list[dict[str, Any]]) -> dict[str, Any]:
    """검색 계획이 요구한 지문 근거 슬롯이 모두 채워졌는지 검사한다."""
    covered = sorted(set().union(*(source_slots(source) for source in sources))) if sources else []
    missing = [slot for slot in plan.get("slots", []) if slot not in covered]
    return {"status": "ok" if not missing else "needs_review", "covered_slots": covered, "missing_slots": missing}
