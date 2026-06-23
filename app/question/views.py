import json
from collections import defaultdict

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.http import require_POST

from .models import Analytics, Questions, SolveRecords, SolveSessions


@login_required
def question_create(request):
    return render(request, "question/create.html")


@login_required
def question_exam(request):
    return render(request, "question/question_exam.html")


@login_required
def question_result(request):
    return render(request, "question/question_result.html")


@login_required
@require_POST
def submit_question_result(request):
    try:
        payload = json.loads(request.body.decode("utf-8"))
    except json.JSONDecodeError:
        return JsonResponse({"error": "요청 JSON 형식이 올바르지 않습니다."}, status=400)

    questions = payload.get("questions") if isinstance(payload.get("questions"), list) else []
    answers = payload.get("answers") if isinstance(payload.get("answers"), list) else []
    time_spent = payload.get("time_spent_sec") if isinstance(payload.get("time_spent_sec"), list) else []
    elapsed_sec = max(0, int(payload.get("elapsed_sec") or 0))
    total_count = len(questions)

    if not total_count:
        return JsonResponse({"error": "저장할 문제 결과가 없습니다."}, status=400)

    question_ids = []
    for index, question in enumerate(questions, start=1):
        question_id = question.get("question_id") or question.get("id") or index
        try:
            question_ids.append(int(question_id))
        except (TypeError, ValueError):
            continue

    question_map = {
        question.question_id: question
        for question in Questions.objects.filter(question_id__in=question_ids)
    }

    records = []
    correct_count = 0
    total_score = 0
    era_stats = defaultdict(lambda: {"total": 0, "correct": 0, "time_sum": 0})
    type_stats = defaultdict(lambda: {"total": 0, "correct": 0, "time_sum": 0})
    topic_stats = defaultdict(lambda: {"total": 0, "correct": 0, "time_sum": 0})

    session = SolveSessions.objects.create(
        user=request.user,
        session_type="practice",
        total_count=total_count,
        elapsed_sec=elapsed_sec,
        status="completed",
        answer_rate=0,
        total_score=0,
    )

    for index, question_payload in enumerate(questions):
        question_id = question_payload.get("question_id") or question_payload.get("id") or index + 1
        try:
            question_id = int(question_id)
        except (TypeError, ValueError):
            continue

        question = question_map.get(question_id)
        if not question:
            continue

        selected_no = answers[index] if index < len(answers) else None
        try:
            selected_no = int(selected_no) if selected_no is not None else None
        except (TypeError, ValueError):
            selected_no = None

        spent_sec = time_spent[index] if index < len(time_spent) else None
        try:
            spent_sec = max(0, int(spent_sec)) if spent_sec is not None else None
        except (TypeError, ValueError):
            spent_sec = None

        is_correct = selected_no == question.answer_no
        if is_correct:
            correct_count += 1
            total_score += question.q_score

        records.append(
            SolveRecords(
                session=session,
                question=question,
                selected_no=selected_no,
                is_correct=is_correct,
                time_spent_sec=spent_sec,
                q_type=question.question_type,
                topic=question.topic,
                era=question.era,
                q_score=question.q_score,
            )
        )

        for bucket, key in (
            (era_stats, question.era),
            (type_stats, question.question_type),
            (topic_stats, question.topic),
        ):
            bucket[key]["total"] += 1
            bucket[key]["correct"] += int(is_correct)
            bucket[key]["time_sum"] += spent_sec or 0

    if records:
        SolveRecords.objects.bulk_create(records)

    analytics_rows = []
    now = timezone.now()
    for classification, stats in (("시대", era_stats), ("유형", type_stats), ("주제", topic_stats)):
        for key, stat in stats.items():
            total = stat["total"]
            analytics_rows.append(
                Analytics(
                    session=session,
                    key_concept=key,
                    classification=classification,
                    avg_time_sec=stat["time_sum"] // total if total else None,
                    topic_rate=round(stat["correct"] / total, 4) if total else 0.0,
                    date=now,
                )
            )
    if analytics_rows:
        Analytics.objects.bulk_create(analytics_rows)

    answer_rate = round(correct_count / len(records), 4) if records else 0.0
    session.answer_rate = answer_rate
    session.total_score = total_score
    session.save(update_fields=["answer_rate", "total_score"])

    return JsonResponse(
        {
            "session_id": session.session_id,
            "saved_records": len(records),
            "elapsed_sec": elapsed_sec,
            "answer_rate": answer_rate,
        }
    )
