"""GPT material에서 기계적으로 확정 가능한 형식 오류만 검사한다."""

from __future__ import annotations

from typing import Any

from ai.question_generation.generation.material_rules import material_type_format_status


def material_contract_status(selection: dict[str, Any], material: str) -> dict[str, Any]:
    """형식 규칙과 발문에 필요한 마커가 지문에 존재하는지 검사한다."""
    if not material:
        errors = ["missing_material"] if selection.get("material_type") else []
        return {"status": "ok" if not errors else "needs_review", "errors": errors}
    type_format = material_type_format_status(selection, material)
    errors: list[str] = list(type_format.get("errors") or [])
    task = selection.get("question_task")
    if task == "period_between" and not all(marker in material for marker in ("(가)", "(나)")):
        errors.append("period_between_missing_markers")
    if task == "timeline_position":
        positions = [str(value) for value in (selection.get("chronology") or {}).get("timeline_positions") or []]
        marker_offsets = [material.find(position) for position in positions]
        if (
            not positions
            or any(offset < 0 or material.count(position) != 1 for position, offset in zip(positions, marker_offsets))
            or marker_offsets != sorted(marker_offsets)
        ):
            errors.append("timeline_position_marker_contract_mismatch")
    if task == "order" and not all(marker in material for marker in ("(가)", "(나)", "(다)")):
        errors.append("timeline_order_missing_markers")
    if selection.get("question_task") == "standard_select" and selection.get("stem_pattern") == "fill_blank":
        if material.count("(가)") != 1:
            errors.append("fill_blank_requires_single_marker")
        if "<u>" in material or "</u>" in material:
            errors.append("fill_blank_has_underlined_reference")
    return {
        "status": "ok" if not errors else "needs_review",
        "errors": list(dict.fromkeys(errors)),
        "type_format": type_format,
    }
