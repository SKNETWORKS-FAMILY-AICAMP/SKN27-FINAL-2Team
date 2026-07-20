from __future__ import annotations

from typing import Any


MIN_KEYWORD_SCORE = 0.12
MIN_VECTOR_SCORE = 0.35


def has_enough_evidence(results: list[Any], intent: str) -> bool:
    if intent == "image":
        return any(result.source_type == "image_material" and (result.metadata.get("original_image_url") or result.metadata.get("thumbnail_url")) for result in results)
    if not results:
        return False
    best_keyword = max(float(result.keyword_score or 0.0) for result in results)
    best_vector = max(float(result.vector_score or 0.0) for result in results)
    return best_keyword >= MIN_KEYWORD_SCORE or best_vector >= MIN_VECTOR_SCORE
