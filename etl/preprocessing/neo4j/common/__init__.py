from __future__ import annotations

import re
import unicodedata
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from langchain_openai import ChatOpenAI


def get_historyterm_llm() -> ChatOpenAI:
    """기출문제에서 역사 용어를 추출하는 LLM을 반환한다."""
    from dotenv import load_dotenv
    from langchain_openai import ChatOpenAI

    load_dotenv()
    llm = ChatOpenAI(model="gpt-5.4-mini", temperature=0)
    return llm


def normalize_history_term(term: str) -> str:
    """용어 비교용으로 유니코드와 공백 차이를 제거한다."""
    normalized = unicodedata.normalize("NFC", str(term)).casefold()
    return re.sub(r"\s+", "", normalized)
