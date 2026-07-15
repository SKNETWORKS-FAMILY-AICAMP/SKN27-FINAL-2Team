from __future__ import annotations

from functools import lru_cache


NOUN_POS = {"NNG", "NNP", "NNB", "NP", "SL", "SN"}


@lru_cache(maxsize=1)
def get_mecab_tagger():
    from mecab_ko import Tagger

    return Tagger()


def mecab_search_tokens(text: str) -> str:
    """MeCab 명사 토큰을 PostgreSQL simple FTS/BM25 입력으로 변환합니다."""
    tokens: list[str] = []
    parsed = get_mecab_tagger().parse(text or "") or ""
    for line in parsed.splitlines():
        if "\t" not in line:
            continue
        surface, features = line.split("\t", 1)
        if features.split(",", 1)[0] in NOUN_POS:
            tokens.append(surface)
    return " ".join(dict.fromkeys(tokens))


if __name__ == "__main__":
    tokens = mecab_search_tokens("세종대왕이 훈민정음을 창제했다")
    assert all(term in tokens.split() for term in ("세종", "대왕", "훈민정음", "창제"))
