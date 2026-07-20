from __future__ import annotations

import re

from .korean_tokenizer import mecab_search_tokens

STOPWORDS = {
    "설명",
    "알려줘",
    "정리",
    "무엇",
    "뭐야",
    "대한",
    "관련",
    "차이",
    "한국사",
    "한능검",
    "이유",
    "배경",
    "목적",
    "만든",
}
QUERY_EXPANSIONS = {
    "6조": ["육조", "六曹"],
    "육조": ["6조", "六曹"],
    "직계제": ["직계", "直啓", "직계아문"],
    "직계": ["직계제", "直啓", "직계아문"],
    "의정부서사제": ["의정부", "서사제", "署事"],
    "조선전기": ["조선 초기", "조선 초기의"],
    "정치": ["정치구조", "통치", "관료", "의정부", "육조"],
    "전성기": ["광개토", "광개토대왕", "장수왕", "남진"],
    "고구려": ["광개토", "광개토대왕", "장수왕"],
    "을사늑약": ["을사조약", "제2차 한일협약", "외교권", "통감부"],
    "3.1": ["3·1운동", "삼일운동", "민족 자결주의", "대한민국 임시정부"],
    "3·1": ["3.1운동", "삼일운동", "민족 자결주의", "대한민국 임시정부"],
    "삼일운동": ["3.1운동", "3·1운동", "민족 자결주의", "대한민국 임시정부"],
    "6월": ["6월 민주 항쟁", "6·10", "6.10", "직선제", "민주화"],
    "민주항쟁": ["6월 민주 항쟁", "6·10", "직선제", "민주화"],
    "이미지": ["사진", "그림", "유물", "유적", "자료"],
    "사진": ["이미지", "그림", "유물", "유적", "자료"],
    "구석기": ["주먹도끼", "찍개", "석장리", "전곡리"],
    "신석기": ["빗살무늬", "토기", "암사동"],
    "청동기": ["고인돌", "비파형동검", "민무늬토기"],
    "팔만대장경": ["대장경", "재조대장경", "고려대장경", "몽골", "해인사"],
    "대장경": ["팔만대장경", "재조대장경", "고려대장경", "몽골", "해인사"],
}


def tokenize(text: str) -> list[str]:
    return [token for token in mecab_search_tokens(text or "").split() if token not in STOPWORDS]


def expand_query_tokens(query: str, tokens: list[str]) -> list[str]:
    expanded = list(tokens)
    compact_query = re.sub(r"\s+", "", query)
    for key, values in QUERY_EXPANSIONS.items():
        if key in query or key in compact_query or key.lower() in tokens:
            for value in values:
                for token in tokenize(value):
                    if token not in expanded:
                        expanded.append(token)
    return expanded
