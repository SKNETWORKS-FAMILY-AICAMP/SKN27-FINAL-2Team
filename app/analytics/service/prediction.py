from django.db.models import Count

from question.models import Questions


def get_predicted_targets(user_id=None):
    """
    문제은행의 시대+주제+유형 조합별 문항 수를 기준으로 출제 예상 대상을 만든다.

    가장 많이 존재하는 조합을 1.0으로 잡고, 나머지 조합은 상대 비율을
    predictionScore로 계산한다.
    현재 predictionScore는 임시 휴리스틱이며, 추후 출제 예측 ML/RAG가
    붙으면 이 함수의 반환값을 ML/RAG 결과로 대체한다.
    """
    rows = list(
        Questions.objects.values("era", "topic", "question_type")
        .annotate(question_count=Count("question_id"))
        .order_by("-question_count", "era", "topic", "question_type")
    )
    if not rows:
        return []

    max_count = max(row["question_count"] or 0 for row in rows)
    predicted_targets = []
    for row in rows:
        era = row["era"]
        topic = row["topic"]
        q_type = row["question_type"]
        question_count = row["question_count"] or 0
        if era and topic and q_type and max_count:
            label = " · ".join([era, topic, q_type])
            prediction_score = round(question_count / max_count, 4)
            predicted_targets.append(
                {
                    "classification": "복합",
                    "label": label,
                    "era": era,
                    "topic": topic,
                    "qType": q_type,
                    "predictionScore": prediction_score,
                    "reason": f"문제은행에서 {label} 조합의 출제 비중이 높습니다.",
                }
            )

    return predicted_targets
