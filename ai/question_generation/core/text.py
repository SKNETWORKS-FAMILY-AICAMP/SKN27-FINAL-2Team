"""파이프라인 전 계층에서 공유하는 문자열 정규화 함수."""

from __future__ import annotations

import re
from typing import Any


def compact(text: Any) -> str:
    """연속 공백을 하나로 줄인다."""
    return " ".join(str(text or "").split())


def normalize_era_markers(text: Any) -> str:
    """B.C./A.D. 시대 표기를 한국어 표기로 정규화한다."""
    value = re.sub(r"\bB\.\s*C\.?\s*", "기원전 ", str(text or ""), flags=re.IGNORECASE)
    return compact(re.sub(r"\bA\.\s*D\.?\s*", "서기 ", value, flags=re.IGNORECASE))
