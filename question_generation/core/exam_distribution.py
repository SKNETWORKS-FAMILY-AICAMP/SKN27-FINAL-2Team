"""공식 기출의 난이도·시대 분포를 계산한다."""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any


DIFFICULTY_LABELS = {1: "쉬움", 2: "보통", 3: "어려움"}
ERA_ORDER = ("선사·초기국가", "고대", "고려", "조선", "개항기·대한제국", "일제강점기", "현대")


def source_era(value: str) -> str | None:
    """공식 데이터와 민백 시대 경로가 한 대시대로 수렴할 때만 반환한다."""
    eras: set[str] = set()
    for part in (value or "").split("|"):
        part = part.strip()
        if part in ERA_ORDER:
            eras.add(part)
        elif part.startswith("선사/") or any(token in part for token in ("구석기", "신석기", "청동기", "철기")):
            eras.add(ERA_ORDER[0])
        elif part.startswith("고대/") or any(token in part for token in ("삼국", "남북국")):
            eras.add(ERA_ORDER[1])
        elif part == "고려" or part.startswith(("고려/", "고려 ")):
            eras.add(ERA_ORDER[2])
        elif part == "조선" or part.startswith(("조선/", "조선 ")):
            eras.add(ERA_ORDER[3])
        elif part.startswith(("근대/개항기", "근대/대한제국기")) or "개항기" in part:
            eras.add(ERA_ORDER[4])
        elif part.startswith("근대/일제강점기") or "일제강점기" in part or "일제 강점기" in part:
            eras.add(ERA_ORDER[5])
        elif part == "현대" or part.startswith("현대/"):
            eras.add(ERA_ORDER[6])
    return next(iter(eras)) if len(eras) == 1 else None


def apportion(counts: Counter[str], total: int) -> dict[str, int]:
    denominator = sum(counts.values())
    if not denominator:
        return {era: 0 for era in ERA_ORDER}
    raw = {era: counts[era] * total / denominator for era in ERA_ORDER}
    quota = {era: int(raw[era]) for era in ERA_ORDER}
    ranked = sorted(ERA_ORDER, key=lambda era: (raw[era] - quota[era], counts[era]), reverse=True)
    for era in ranked[:total - sum(quota.values())]:
        quota[era] += 1
    return quota


def official_distribution(records: list[dict[str, Any]]) -> tuple[dict[int, Counter[str]], Counter[int]]:
    counts: dict[int, Counter[str]] = defaultdict(Counter)
    unresolved: Counter[int] = Counter()
    for row in records:
        source = row.get("input") or {}
        score = int(source.get("target_score") or 0)
        era = source_era(str(source.get("era") or ""))
        if score not in DIFFICULTY_LABELS:
            continue
        if era:
            counts[score][era] += 1
        else:
            unresolved[score] += 1
    return counts, unresolved
