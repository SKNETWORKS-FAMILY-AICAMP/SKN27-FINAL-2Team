from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).parents[3]
sys.path.insert(0, str(ROOT))

from app.chatbot.rag.query_terms import tokenize


def check_core_terms_are_preserved() -> None:
    for word in ("국가", "결과", "효과", "성과", "정의", "인가", "이란"):
        assert word in tokenize(f"{word}에 대해 설명해줘"), f"{word} 소실"


if __name__ == "__main__":
    check_core_terms_are_preserved()
    print("query_terms checks passed")
