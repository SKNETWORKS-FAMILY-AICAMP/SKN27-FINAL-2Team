"""material_type별 프롬프트 규칙과 결정론적 형식 검사를 제공한다.

규칙의 원본은 ``material_type_prompt_rules.json``이며 이 모듈은 같은 설정을
프롬프트 문장과 코드 검사 양쪽에서 재사용한다.
"""

from __future__ import annotations

import json
import random
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_MATERIAL_PROMPT_RULES = PROJECT_ROOT / "ai" / "question_generation" / "material_type_prompt_rules.json"
DEFAULT_MATERIAL_EXAMPLES = PROJECT_ROOT / "ai" / "question_generation" / "material_few_shot_examples.json"


def load_json_dict(path: Path) -> dict[str, Any]:
    """규칙 JSON이 존재하고 객체일 때만 dict로 반환한다."""
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    return data if isinstance(data, dict) else {}


def choose_material_examples(
    examples: dict[str, list[dict[str, Any]]],
    selection: dict[str, Any],
    seed: int,
    limit: int = 2,
) -> list[dict[str, Any]]:
    """현재 topic을 제외한 같은 자료 유형 예시에서 발문 패턴·난이도가 가까운 순으로 고른다."""
    topic = re.sub(r"\s+", "", str(selection.get("topic") or ""))
    material_type = str(selection.get("material_type") or "")
    stem_pattern = str(selection.get("stem_pattern") or "")
    pool = [
        {**item, "material_type": example_material_type}
        for example_material_type, items in examples.items()
        for item in items
        if example_material_type == material_type
        and item.get("question_task") == selection.get("question_task")
        and (not topic or topic not in re.sub(r"\s+", "", str(item.get("topic") or item.get("material") or "")))
    ]
    random.Random(f"{seed}:{selection.get('seed_id')}:{selection.get('material_type')}").shuffle(pool)
    pool.sort(key=lambda item: (
        item.get("stem_pattern") != stem_pattern,
        item.get("difficulty_label") != selection.get("difficulty_label"),
    ))
    return pool[:max(0, limit)]


def material_type_rules_text(rules: dict[str, Any], material_type: str) -> str:
    """JSON 규칙과 길이 제약을 GPT 프롬프트용 bullet 문자열로 변환한다."""
    values = rules.get(material_type) or []
    if not isinstance(values, list):
        return ""
    lines = [f"- {value}" for value in values if str(value).strip()]
    constraint = material_type_constraint(rules, material_type)
    if constraint:
        minimum, maximum = constraint.get("min_chars"), constraint.get("max_chars")
        if minimum is not None and maximum is not None:
            lines.append(f"- 표시 문자열은 절대 허용 범위인 {minimum}~{maximum}자를 벗어나지 않는다.")
        min_sentences, max_sentences = constraint.get("min_sentences"), constraint.get("max_sentences")
        if min_sentences is not None and max_sentences is not None:
            lines.append(f"- 문장 수는 {min_sentences}~{max_sentences}개로 제한한다.")
        if constraint.get("max_chars_per_item") is not None:
            lines.append(f"- (가)·(나)·(다) 각 항목은 {constraint['max_chars_per_item']}자를 넘기지 않는다.")
    return "\n".join(lines)


def material_type_constraint(rules: dict[str, Any], material_type: str) -> dict[str, Any]:
    """규칙 JSON에서 지정 material_type의 구조 제약만 반환한다."""
    constraints = rules.get("_constraints") or {}
    types = constraints.get("types") if isinstance(constraints, dict) else {}
    value = types.get(material_type) if isinstance(types, dict) else None
    return value if isinstance(value, dict) else {}


@lru_cache(maxsize=1)
def default_material_rules() -> dict[str, Any]:
    """기본 규칙 JSON을 프로세스당 한 번만 읽어 캐시한다."""
    return load_json_dict(DEFAULT_MATERIAL_PROMPT_RULES)


def material_type_route_status(selection: dict[str, Any], rules: dict[str, Any] | None = None) -> dict[str, Any]:
    """선택한 question_task가 해당 material_type에서 지원되는지 검사한다."""
    material_type = str(selection.get("material_type") or "").strip()
    if not material_type:
        return {"status": "ok", "errors": []}
    constraint = material_type_constraint(rules or default_material_rules(), material_type)
    if not constraint:
        return {"status": "needs_review", "errors": ["unsupported_material_type"]}
    task = str(selection.get("question_task") or "").strip()
    allowed = [str(value) for value in constraint.get("question_tasks") or []]
    errors = ["material_type_question_task_mismatch"] if task and allowed and task not in allowed else []
    return {"status": "ok" if not errors else "needs_review", "errors": errors}


def visible_material_text(material: str) -> str:
    """HTML 밑줄 태그를 제거한 실제 표시 문자열을 반환한다."""
    return " ".join(re.sub(r"<[^>]+>", "", material or "").split())


def visible_sentence_count(material: str) -> int:
    """화면에 보이는 material의 문장 수를 센다."""
    text = visible_material_text(material)
    return len([part for part in re.split(r"(?<=[.!?])\s+|\n+", text) if part.strip()]) if text else 0


def marker_item_lengths(material: str) -> list[int]:
    """(가)·(나)·(다)로 나뉜 각 자료 조각의 표시 길이를 계산한다."""
    text = visible_material_text(material)
    matches = list(re.finditer(r"\([가-힣]\)", text))
    return [
        len(text[match.start():(matches[index + 1].start() if index + 1 < len(matches) else len(text))].strip())
        for index, match in enumerate(matches)
    ]


def material_type_format_status(
    selection: dict[str, Any], material: str, rules: dict[str, Any] | None = None
) -> dict[str, Any]:
    """생성 지문의 선언된 글자 수·문장 수·마커 길이를 검사한다."""
    rules = rules or default_material_rules()
    route = material_type_route_status(selection, rules)
    errors = list(route["errors"])
    material_type = str(selection.get("material_type") or "").strip()
    constraint = material_type_constraint(rules, material_type)
    if not material_type or not constraint:
        return {"status": "ok" if not errors else "needs_review", "errors": errors}

    text = visible_material_text(material)
    char_count = len(text)
    sentence_count = visible_sentence_count(text)
    minimum, maximum = constraint.get("min_chars"), constraint.get("max_chars")
    if minimum is not None and char_count < int(minimum):
        errors.append("material_below_type_min_chars")
    if maximum is not None and char_count > int(maximum):
        errors.append("material_above_type_max_chars")
    if constraint.get("min_sentences") is not None and sentence_count < int(constraint["min_sentences"]):
        errors.append("material_below_type_min_sentences")
    if constraint.get("max_sentences") is not None and sentence_count > int(constraint["max_sentences"]):
        errors.append("material_above_type_max_sentences")
    item_lengths = marker_item_lengths(text)
    max_item = constraint.get("max_chars_per_item")
    if max_item is not None and any(length > int(max_item) for length in item_lengths):
        errors.append("material_item_above_type_max_chars")
    return {
        "status": "ok" if not errors else "needs_review",
        "errors": list(dict.fromkeys(errors)),
        "char_count": char_count,
        "sentence_count": sentence_count,
        "item_lengths": item_lengths,
    }
