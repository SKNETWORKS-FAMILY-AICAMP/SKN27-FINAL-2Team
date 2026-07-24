"""구형 Graph/RAG source에서 정답 사실 후보를 고르는 보조 함수."""

from __future__ import annotations

import re
from typing import Any

from question_generation.core.difficulty import target_score_from_selection
from question_generation.core.text import compact


DIFFICULTY_FACT_RE = re.compile(
    r"실시|설치|제정|반포|편찬|창제|밝혀|수록|제작|출판|저술|집필|남겼|표기|보완|정리|통합|창건|건립|천도|도읍|폐지|주장|추진|주도|역임|계획|기획|구현|정착|협력|훈련|양성|전개|조직|창설|창간|폐간|체결|파견|점령|공격|평정|진압|참여|활동|발행|개편|수립|공포|운영|개혁|도입|작성|선포|표방|개칭|멸망|경쟁|확대|강화|장악|장려|연구|조사|폐단|수탈|개항|자칭|허용|박탈|임명|책록|그려|시해|옹립|대승|패배|귀부|항복|정비"
)

def difficulty_tokens(text: str) -> set[str]:
    """구형 Graph 비교용 한국어 토큰을 단순 조사 제거 방식으로 만든다."""
    stopwords = {"그리고", "하지만", "이후", "통해", "위해", "가운데", "하나", "인물", "자료", "정답", "사실"}
    tokens: set[str] = set()
    for raw in re.findall(r"[가-힣A-Za-z0-9]{2,}", text or ""):
        token = re.sub(
            r"(으로|에서|에게|부터|까지|이다|이며|이고|하였으며|하였다|되었다|하였다|된|한|의|이|가|은|는|을|를|에|로)$",
            "",
            raw,
        )
        if token and token not in stopwords and len(token) >= 2 and not re.fullmatch(r"\d+년?", token):
            tokens.add(token)
    return tokens


def fuzzy_overlap_terms(left: set[str], right: set[str]) -> set[str]:
    """구형 Graph 검사용 부분 문자열 중복 집합을 반환한다."""
    overlaps: set[str] = set()
    for ltoken in left:
        for rtoken in right:
            if ltoken == rtoken or (
                len(ltoken) >= 2
                and len(rtoken) >= 2
                and (ltoken in rtoken or rtoken in ltoken)
            ):
                overlaps.add(min((ltoken, rtoken), key=len))
    return overlaps


def answer_fact_hint_sentences(
    selection: dict[str, Any],
    sources: list[dict[str, Any]],
    plan: dict[str, Any],
    limit: int = 2,
) -> list[str]:
    """Graph/RAG source에서 정답 선지로 쓸 만한 사실 문장을 점수화해 고른다."""
    if plan.get("intent") == "timeline_order":
        return []
    scored: list[tuple[int, str]] = []
    seen: set[str] = set()
    topic_key = re.sub(r"\s+", "", str(selection.get("topic") or ""))
    identity_tokens = difficulty_tokens(
        " ".join(
            str(source.get("snippet") or "")
            for source in sources
            if source.get("source_type") == "neo4j_graph_fact"
        )
    )
    avoid_identity_fact = target_score_from_selection(selection) >= 2 and plan.get("intent") == "identity"
    for source in sources:
        if source.get("source_type") == "retrieval_plan":
            continue
        snippet = re.sub(
            r"\[[^\]]+\]\([^)]+\)",
            lambda match: match.group(0).split("]")[0].lstrip("["),
            str(source.get("snippet") or ""),
        )
        for sentence in re.split(r"[.。!?！？]\s*|#\s*", snippet):
            sentence = compact(
                re.sub(r"^(개설|생애 및 활동사항|주요 활동|활동사항|내용|정의)\s*", "", sentence.strip()),
                220,
            )
            if len(sentence) < 12 or not DIFFICULTY_FACT_RE.search(sentence):
                continue
            key = re.sub(r"\s+", "", sentence)
            if key in seen:
                continue
            seen.add(key)
            action_count = len(DIFFICULTY_FACT_RE.findall(sentence))
            source_score = 10
            if str(source.get("source_type") or "").startswith("encykorea"):
                source_score = 30 if re.search(r":2$", str(source.get("chunk_id") or "")) else 20
            topic_bonus = 40 if topic_key and topic_key in key else 0
            definition_penalty = (
                50
                if topic_key
                and re.search(rf"{re.escape(str(selection.get('topic') or ''))}.{{0,50}}(?:이다|가리킨다)", sentence)
                else 0
            )
            related_subject_penalty = (
                40
                if topic_key
                and topic_key not in key
                and re.search(r"(?:조처|조건|협정|규정)(?:이었|였|이었다|였다|이다)$", sentence)
                else 0
            )
            named_work_bonus = (
                15
                if len(sentence) <= 120
                and re.search(r"『[^』]{2,}』", sentence)
                and re.search(r"저술|집필|편찬|남겼", sentence)
                else 0
            )
            source_debate_penalty = 25 if re.search(r"대해서는|기록하였지만|연구자|견해|논쟁|추정|보기도", sentence) else 0
            negative_claim_penalty = 40 if re.search(r"아니다|아니었다|불리지 않는다|없었다|해당하지 않는다", sentence) else 0
            incomplete_sentence_penalty = 60 if not sentence.rstrip(" .").endswith("다") else 0
            length_penalty = 30 if len(sentence) > 180 else 0
            identity_overlap_penalty = (
                min(90, len(fuzzy_overlap_terms(identity_tokens, difficulty_tokens(sentence))) * 18)
                if avoid_identity_fact
                else 0
            )
            scored.append(
                (
                    source_score
                    + topic_bonus
                    + min(action_count, 2) * 10
                    + min(len(difficulty_tokens(sentence)), 8)
                    + named_work_bonus
                    - definition_penalty
                    - related_subject_penalty
                    - source_debate_penalty
                    - negative_claim_penalty
                    - incomplete_sentence_penalty
                    - length_penalty
                    - identity_overlap_penalty,
                    sentence,
                )
            )
    scored.sort(reverse=True)
    return [sentence for _, sentence in scored[:limit]]
