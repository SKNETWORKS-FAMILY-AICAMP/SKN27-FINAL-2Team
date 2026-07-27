"""동일 축의 서로 다른 owner 9개를 전역 사실 중복 없이 pack으로 조립한다."""

from __future__ import annotations

from typing import Any

from ai.question_generation.retrieval.closed_pack_input import validate_closed_pack_source


def normalized_fact(value: Any) -> str:
    """완전 동일 사실 비교를 위해 공백만 정규화한다."""
    return " ".join(str(value or "").split())


def fact_fingerprint(member: dict[str, Any]) -> str:
    """상류가 제공한 의미 식별자를 우선하고, 없으면 정규화한 사실 문장을 사용한다."""
    return str(member.get("fact_fingerprint") or normalized_fact(member.get("fact_basis")))


def validate_member(member: dict[str, Any]) -> None:
    """공통 출제 계약 외에 fact와 material 문장 자체의 중복만 검사한다."""
    if member.get("material_clue_basis") and normalized_fact(member["fact_basis"]) == normalized_fact(member["material_clue_basis"]):
        raise ValueError(f"fact and material clue are identical: {member['choice_fact_id']}")


def validate_pack_bank(packs: list[dict[str, Any]]) -> None:
    """팩 구조와 전체 팩 사이 ChoiceFact·사실 문장 중복을 검사한다."""
    family_ids: set[str] = set()
    fact_ids: set[str] = set()
    fact_texts: set[str] = set()
    fact_fingerprints: set[str] = set()
    for pack in packs:
        validate_closed_pack_source(pack)
        family_id = str(pack.get("family_id") or "")
        members = pack.get("members") or []
        if not family_id or family_id in family_ids:
            raise ValueError(f"missing or duplicate family_id: {family_id}")
        owner_ids = [str(member.get("owner_id") or "") for member in members]
        owner_labels = [str(member.get("owner_label") or "") for member in members]
        if "" in owner_ids or len(set(owner_ids)) != 9 or "" in owner_labels or len(set(owner_labels)) != 9:
            raise ValueError(f"closed pack must contain nine distinct owners: {family_id}")
        for member in members:
            validate_member(member)
            fact_id = str(member.get("choice_fact_id") or "")
            fact_text = normalized_fact(member.get("fact_basis"))
            fingerprint = fact_fingerprint(member)
            if not fact_id or not fact_text:
                raise ValueError(f"closed pack member lacks a fact: {family_id}")
            if fact_id in fact_ids or fact_text in fact_texts or fingerprint in fact_fingerprints:
                raise ValueError(f"fact reused across closed packs: {fact_id}")
            fact_ids.add(fact_id)
            fact_texts.add(fact_text)
            fact_fingerprints.add(fingerprint)
        family_ids.add(family_id)
