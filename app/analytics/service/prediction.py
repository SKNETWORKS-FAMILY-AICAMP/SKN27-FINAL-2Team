from django.db.models import Count

from question.models import Questions


def get_predicted_targets(user_id=None):
    predicted_targets = []
    for classification, field_name in get_prediction_fields():
        rows = list(
            Questions.objects.values(field_name)
            .annotate(question_count=Count("question_id"))
            .order_by("-question_count", field_name)
        )
        if rows:
            max_count = max(row["question_count"] or 0 for row in rows)
            for row in rows:
                label = row[field_name]
                question_count = row["question_count"] or 0
                if label and max_count:
                    prediction_score = round(question_count / max_count, 4)
                    predicted_targets.append(
                        {
                            "classification": classification,
                            "label": label,
                            "predictionScore": prediction_score,
                            "reason": f"문제은행에서 {label} 항목의 출제 비중이 높습니다.",
                        }
                    )

    return predicted_targets


def get_prediction_fields():
    return [
        ("시대", "era"),
        ("유형", "question_type"),
        ("주제", "topic"),
    ]
