"""난이도 라벨을 점수와 지문 작성 지시로 정규화한다."""

from __future__ import annotations

from typing import Any


def target_score_from_difficulty(*values: Any) -> int | None:
    """여러 난이도 표현 중 첫 유효 값을 1~3점으로 바꾼다."""
    labels = {
        "1": 1, "1점": 1,
        "쉬움": 1, "easy": 1, "하": 1,
        "2": 2, "2점": 2,
        "보통": 2, "medium": 2, "normal": 2, "중": 2,
        "3": 3, "3점": 3,
        "어려움": 3, "hard": 3, "상": 3,
    }
    for value in values:
        if value in {1, 2, 3}:
            return int(value)
        text = str(value or "").strip().lower()
        if text in labels:
            return labels[text]
    return None
