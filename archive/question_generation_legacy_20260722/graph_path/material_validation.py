"""구형 Graph/RAG 경로의 material 의미 휴리스틱을 보존한다.

검사 범위는 근거 출처, 정답 사실 누출, material 계약, 난이도용 단서 분리다.
역사적 사실의 최종 품질 평가는 여기서 하지 않고 v1.8 평가 단계에서 수행한다.
"""

from __future__ import annotations

import re
from typing import Any

from question_generation.core.difficulty import target_score_from_selection
from question_generation.core.text import compact
from question_generation.graph_path.fact_constraints import (
    DIFFICULTY_FACT_RE,
    difficulty_tokens,
    fuzzy_overlap_terms,
)


def answer_fact_overlap_status(material: str, answer_fact_basis: list[str], topic: str) -> dict[str, Any]:
    """지문과 정답 근거가 핵심 토큰을 공유해 정답을 직접 노출하는지 검사한다."""
    def normalize_token(token: str) -> str:
        return re.sub(r"(으로|에서|에게|부터|까지|이다|이며|이고|의|이|가|은|는|을|를|에|로)$", "", token)

    def topic_edge_fragments(value: str) -> set[str]:
        text = re.sub(r"[^가-힣A-Za-z0-9]", "", value or "")
        return {fragment for fragment in (text[:2], text[-2:]) if len(fragment) >= 2}

    stopwords = {
        topic,
        "신라",
        "고려",
        "조선",
        "시대",
        "왕",
        "재위",
        "자료",
        "정답",
        "사실",
        "법전",
        "제도",
        "정책",
        "사건",
        "통치",
        "국가",
        "중앙",
        "지방",
        "이후",
        "통해",
        "위해",
    }
    def useful_tokens(text: str) -> set[str]:
        tokens: set[str] = set()
        for raw in re.findall(r"[가-힣A-Za-z0-9]{2,}", text or ""):
            token = normalize_token(raw)
            if token not in stopwords and len(token) >= 2 and not re.fullmatch(r"\d+년?", token) and not re.fullmatch(r"제\d+대", token):
                tokens.add(token)
        return tokens

    material_tokens = useful_tokens(material or "")
    basis_text = " ".join(answer_fact_basis or [])
    basis_tokens = useful_tokens(basis_text)
    overlap = sorted(
        token
        for token in material_tokens & basis_tokens
    )
    material_text = material or ""
    event_overlap = (
        re.search(r"(반란|모반|난을|난이|의 난)", material_text)
        and re.search(r"(반란|모반|난을|난이|의 난)", basis_text)
        and re.search(r"(진압|평정|숙청)", material_text)
        and re.search(r"(진압|평정|숙청)", basis_text)
    )
    if event_overlap:
        overlap.append("event_action_overlap")
    topic_near_overlap = [
        token for token in overlap if any(fragment in token for fragment in topic_edge_fragments(topic))
    ]
    needs_review = "event_action_overlap" in overlap or bool(topic_near_overlap)
    return {
        "status": "needs_review" if needs_review else "ok",
        "overlap_terms": overlap,
        "topic_near_overlap": topic_near_overlap,
    }


def answer_choice_viability_status(material: str, answer_fact_basis: list[str], topic: str) -> dict[str, Any]:
    """정답 근거 중 지문과 중복되지 않아 선지로 쓸 사실이 남아 있는지 검사한다."""
    def normalize_token(token: str) -> str:
        return re.sub(r"(으로|에서|에게|부터|까지|이다|이며|이고|의|이|가|은|는|을|를|에|로)$", "", token)

    def tokens(text: str) -> set[str]:
        stopwords = {
            topic,
            "다음",
            "자료",
            "시대",
            "사실",
            "설명",
            "정답",
            "왕",
            "재위",
            "법전",
            "통치",
            "국가",
            "조선",
            "신라",
            "고려",
            "이후",
            "통해",
            "위해",
        }
        values: set[str] = set()
        for raw in re.findall(r"[가-힣A-Za-z0-9]{2,}", text or ""):
            token = normalize_token(raw)
            if token not in stopwords and len(token) >= 2 and not re.fullmatch(r"\d+년?", token):
                values.add(token)
        return values

    def topic_edge_fragments(value: str) -> set[str]:
        text = re.sub(r"[^가-힣A-Za-z0-9]", "", value or "")
        return {fragment for fragment in (text[:2], text[-2:]) if len(fragment) >= 2}

    material_tokens = tokens(material)
    candidates: list[str] = []
    for basis in answer_fact_basis or []:
        sentences = [part.strip() for part in re.split(r"[.。]", str(basis)) if part.strip()]
        candidates.extend(sentences)
        for sentence in sentences:
            candidates.extend(clause.strip() for clause in re.split(r"[,，;；]", sentence) if clause.strip())
    scored = []
    for candidate in candidates:
        cleaned = candidate.rstrip(" .")
        dated_fact_fragment = (
            len(cleaned) >= 12
            and bool(re.search(r"(?<!\d)\d{3,4}년", cleaned))
            and not re.search(r"(?:\d|\.\.\.)$", cleaned)
        )
        if not cleaned.endswith("다") and not dated_fact_fragment:
            continue
        candidate_tokens = tokens(candidate)
        if len(candidate_tokens) <= 1:
            continue
        overlap = sorted(material_tokens & candidate_tokens)
        topic_near = [
            token for token in overlap if any(fragment in token for fragment in topic_edge_fragments(topic))
        ]
        scored.append({"candidate": compact(candidate, 120), "overlap_terms": overlap, "topic_near_overlap": topic_near})
        if len(overlap) <= 2 and not topic_near:
            return {"status": "ok", "candidate_count": len(candidates), "best": scored[-1]}
    return {
        "status": "needs_review",
        "candidate_count": len(candidates),
        "best": min(scored, key=lambda item: len(item["overlap_terms"]), default={}),
    }


def material_answer_leak_status(selection: dict[str, Any], material: str, plan: dict[str, Any]) -> dict[str, Any]:
    """구형 선택 정보의 정답 힌트가 material에 직접 노출됐는지 검사한다."""
    if plan.get("intent") != "timeline_compare" or selection.get("topic_type") == "사건":
        return {"status": "ok", "leak_terms": []}
    leak_terms = re.findall(r"반란|모반|의 난|난이|난을|설치|시행|제정|창건|편찬|체결|파견|정비|폐지|개혁", material or "")
    return {"status": "needs_review" if leak_terms else "ok", "leak_terms": sorted(set(leak_terms))}


def clue_unit_count(text: str) -> int:
    """쉼표와 문장 경계를 기준으로 독립 단서 묶음 수를 센다."""
    units = [part for part in re.split(r"[.。!?！？;；]|(?:,\s*)|(?:고\s)|(?:며\s)", text or "") if len(compact(part)) >= 8]
    return len(units)


def has_separate_answer_fact(material: str, answer_fact_basis: list[str], plan: dict[str, Any]) -> bool:
    """지문 식별 단서와 별개로 판단할 정답 사실이 존재하는지 확인한다."""
    if plan.get("intent") == "timeline_order":
        return any("순서" in str(basis) and "(가)" in str(basis) for basis in answer_fact_basis)
    material_tokens = difficulty_tokens(material)
    for basis in answer_fact_basis or []:
        for candidate in [part.strip() for part in re.split(r"[.。;；]|(?:,\s*)", str(basis)) if part.strip()]:
            candidate_tokens = difficulty_tokens(candidate)
            if len(candidate_tokens) < 3 or not DIFFICULTY_FACT_RE.search(candidate):
                continue
            overlap = material_tokens & candidate_tokens
            if len(overlap) <= 1 or (len(candidate_tokens - overlap) >= 3 and len(overlap) / max(len(candidate_tokens), 1) <= 0.35):
                return True
    return False


def difficulty_generation_status(
    selection: dict[str, Any],
    material: str,
    answer_fact_basis: list[str],
    plan: dict[str, Any],
) -> dict[str, Any]:
    """지문 단서와 정답 사실이 분리돼 목표 난이도를 만들 수 있는지 진단한다."""
    score = target_score_from_selection(selection)
    errors: list[str] = []
    basis_text = " ".join(answer_fact_basis or [])
    clues = max(clue_unit_count(material), len(re.findall(r"\([가-힣]\)", material or "")))
    has_comparable_fact = has_separate_answer_fact(material, answer_fact_basis, plan)
    if score >= 2 and clues < 2:
        errors.append("difficulty_material_needs_two_clues")
    if (score >= 2 or selection.get("question_task") == "standard_select") and not has_comparable_fact:
        errors.append("difficulty_answer_needs_comparable_fact")
    if score >= 2 and plan.get("needs_timeline") and re.search(r"(시기를|전개된 시기|활동 시기)", basis_text):
        errors.append("difficulty_answer_should_be_period_fact_not_period_label")
    return {
        "status": "ok" if not errors else "needs_review",
        "target_score": score or None,
        "clue_unit_count": clues,
        "errors": errors,
    }


def difficulty_retry_feedback(status: dict[str, Any]) -> str:
    """난이도 진단 오류를 GPT 재작성용 짧은 한국어 지시로 변환한다."""
    if status.get("status") == "ok":
        return ""
    overlap_terms = [str(term) for term in status.get("answer_material_overlap_terms", []) if str(term).strip()]
    errors = [str(error) for error in status.get("errors", []) if str(error).strip()]
    if not overlap_terms and not errors:
        return ""
    return (
        "이전 출력은 난이도 계약을 통과하지 못했다. "
        f"errors={errors}, material/answer_fact_basis overlap_terms={overlap_terms[:12]}. "
        "overlap_terms를 material과 answer_fact_basis 양쪽에 동시에 쓰지 마라. "
        "식별 단서는 material에 남기고, 정답 선지로 쓸 대표 사실은 answer_fact_basis에 별도로 둔다."
    )


def material_retry_feedback(
    material_contract: dict[str, Any],
    material_leak_status: dict[str, Any],
    difficulty_status: dict[str, Any],
    selection: dict[str, Any] | None = None,
) -> str:
    """여러 결정론 검사 오류를 GPT가 이해할 수 있는 재작성 지시로 합친다."""
    parts: list[str] = []
    if material_contract.get("status") != "ok":
        parts.append(
            f"material_contract_errors={material_contract.get('errors', [])}. "
            "material에는 '옳은 것은', '누구인가', 물음표 같은 발문 문장을 쓰지 말고 자료 지문만 쓴다."
        )
        if "standard_select_missing_underlined_reference" in material_contract.get("errors", []):
            reference_noun = {
                "인물": "인물",
                "제도": "제도",
                "사건": "사건",
                "매체": "자료",
                "문화유산": "문화유산",
                "집단": "단체",
            }.get(str((selection or {}).get("topic_type") or ""), "대상")
            parts.append(f"material의 자연스러운 문장 안에 <u>이 {reference_noun}</u>를 정확히 한 번 반드시 넣는다.")
    if material_leak_status.get("status") != "ok":
        parts.append(
            f"material_leak_terms={material_leak_status.get('leak_terms', [])}. "
            "material에는 정답 선지로 쓸 사건·정책·활동명을 직접 노출하지 않는다."
        )
    difficulty_feedback = difficulty_retry_feedback(difficulty_status)
    if difficulty_feedback:
        parts.append(difficulty_feedback)
    return " ".join(parts)
