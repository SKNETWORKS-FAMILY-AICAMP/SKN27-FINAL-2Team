from __future__ import annotations

import re

from .retriever import HybridRagRetriever, SearchResult, compact_text, extract_keywords, source_payload


FIRST_TURN_RECOMMENDATIONS = [
    "이 개념에서 자주 나오는 오답 선지를 알려주세요.",
    "관련 왕이나 사건을 시대순으로 비교해 주세요.",
    "한능검 문제에서는 어떤 식으로 출제되나요?",
]

FOLLOW_UP_RECOMMENDATIONS = [
    "조금 더 쉽게 풀어서 설명해 주세요.",
    "비슷해서 헷갈리는 개념과 비교해 주세요.",
    "이 내용으로 예상 선지를 만들어 주세요.",
]


def infer_topic(question: str, results: list[SearchResult]) -> str:
    if results:
        title = results[0].document.title
        title = re.sub(r"\s+", " ", title).strip()
        return title.split(">")[-1].strip() or title
    return question.strip()


def make_study_note_answer(question: str, results: list[SearchResult]) -> str:
    topic = infer_topic(question, results)
    keywords = extract_keywords(results)
    primary = results[0].document if results else None
    secondary = results[1].document if len(results) > 1 else None

    if not primary:
        return (
            f"# {question.strip()}\n\n"
            "제공된 역사 자료에서 충분한 근거를 찾지 못했습니다.\n\n"
            "질문을 시대, 인물, 사건, 제도명 중 하나로 조금 더 구체화해 주시면 다시 찾아보겠습니다."
        )

    lines = [
        f"# {topic}",
        "",
        "## 1. 핵심 개념",
        f"- {compact_text(primary.chunk_text, 260)}",
        "",
        "## 2. 흐름 정리",
    ]

    if secondary:
        lines.append(f"- {compact_text(secondary.chunk_text, 220)}")
    else:
        lines.append("- 검색된 근거를 기준으로 핵심 개념을 먼저 정리하는 단계입니다.")

    lines.extend(["", "## 3. 한능검 포인트"])
    if keywords:
        for keyword in keywords[:7]:
            lines.append(f"- {keyword}")
    else:
        lines.append("- 시대, 인물, 제도, 사건의 연결 관계를 중심으로 암기하세요.")

    lines.extend(
        [
            "",
            "## 4. 오답 주의",
            "- 비슷한 시대의 제도나 왕 이름이 바뀐 선지를 주의하세요.",
            "- 정책은 반드시 시행한 왕, 목적, 결과를 함께 연결해서 보셔야 합니다.",
            "",
            "## 5. 이어서 물어보기",
        ]
    )
    for recommendation in FIRST_TURN_RECOMMENDATIONS:
        lines.append(f"- {recommendation}")
    return "\n".join(lines)


def make_explanation_answer(question: str, results: list[SearchResult]) -> str:
    if not results:
        return (
            "관련 근거를 충분히 찾지 못했습니다. 시대나 인물명을 함께 넣어서 다시 질문해 주시면 더 정확히 설명드리겠습니다."
        )

    primary = results[0].document
    lines = [
        f"질문하신 내용은 **{infer_topic(question, results)}**와 관련이 있습니다.",
        "",
        compact_text(primary.chunk_text, 520),
    ]

    if len(results) > 1:
        lines.extend(["", "덧붙이면,"])
        lines.append(compact_text(results[1].document.chunk_text, 360))

    lines.extend(["", "한능검에서는 이 내용을 키워드와 시대 흐름으로 같이 묶어서 보시는 게 좋습니다."])
    return "\n".join(lines)


class ConceptChatbotService:
    def __init__(self, retriever: HybridRagRetriever | None = None) -> None:
        self.retriever = retriever or HybridRagRetriever(top_k=5)

    def answer(self, question: str, is_first_turn: bool = True) -> dict:
        question = (question or "").strip()
        if not question:
            return {
                "answer": "질문을 입력해 주세요.",
                "answer_style": "validation_error",
                "sources": [],
                "recommendations": [],
            }

        results = self.retriever.search(question)
        answer_style = "study_note" if is_first_turn else "explanation"
        answer = make_study_note_answer(question, results) if is_first_turn else make_explanation_answer(question, results)

        return {
            "answer": answer,
            "answer_style": answer_style,
            "sources": source_payload(results),
            "recommendations": FIRST_TURN_RECOMMENDATIONS if is_first_turn else FOLLOW_UP_RECOMMENDATIONS,
        }
