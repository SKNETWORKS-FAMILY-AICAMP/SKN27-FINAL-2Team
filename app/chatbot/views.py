from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request

from django.http import HttpResponse, JsonResponse
from django.contrib.auth.decorators import login_required
from django.shortcuts import render
from django.views.decorators.http import require_GET
from django.views.decorators.http import require_POST

from question.models import QuestionOptions, SolveRecords

from .rag_service import build_history_rag_answer


@login_required
def chat_page(request):
    return render(request, "chatbot/chat.html")


@require_POST
def rag_chat_api(request):
    if not request.user.is_authenticated:
        return JsonResponse({"error": "로그인이 필요합니다."}, status=401)

    try:
        payload = json.loads(request.body.decode("utf-8"))
    except json.JSONDecodeError:
        return JsonResponse({"error": "요청 JSON 형식이 올바르지 않습니다."}, status=400)

    question = (payload.get("question") or "").strip()
    if not question:
        return JsonResponse({"error": "질문을 입력해 주세요."}, status=400)

    mode = payload.get("mode") or "history"
    intent = payload.get("intent") or "concept"
    answer_format = payload.get("answer_format") or "structured"
    follow_up = bool(payload.get("follow_up", False))
    top_k = int(payload.get("top_k") or 5)
    conversation_history = payload.get("conversation_history")
    if not isinstance(conversation_history, list):
        conversation_history = []

    try:
        result = build_history_rag_answer(
            question=question,
            mode=mode,
            intent=intent,
            answer_format=answer_format,
            follow_up=follow_up,
            top_k=top_k,
            history=conversation_history,
        )
    except Exception as exc:
        return JsonResponse(
            {
                "error": "RAG 답변 생성 중 오류가 발생했습니다.",
                "detail": str(exc),
            },
            status=500,
        )

    return JsonResponse(result, json_dumps_params={"ensure_ascii": False})


@login_required
@require_GET
def solved_problem_options_api(request):
    records = list(
        SolveRecords.objects.filter(session__user=request.user)
        .select_related("session", "question")
        .order_by("-session__session_id", "record_id")[:120]
    )
    question_ids = [record.question_id for record in records]
    option_map = {}
    for option in QuestionOptions.objects.filter(question_id__in=question_ids).order_by(
        "question_id",
        "choice_no",
    ):
        option_map.setdefault(option.question_id, []).append(option)

    session_numbers = {}
    problems = []
    for record in records:
        session_id = record.session_id
        session_numbers[session_id] = session_numbers.get(session_id, 0) + 1
        question = record.question
        options = option_map.get(question.question_id, [])
        problems.append(
            {
                "record_id": record.record_id,
                "session_id": session_id,
                "number": session_numbers[session_id],
                "content": question.content,
                "question_type": question.question_type,
                "era": record.era,
                "topic": record.topic,
                "selected_no": record.selected_no,
                "answer_no": question.answer_no,
                "is_correct": record.is_correct,
                "answer_explanation": question.answer_explanation,
                "core_concept": question.core_concept,
                "solved_url": f"/user/solved-problems/?session_id={session_id}",
                "options": [
                    {
                        "choice_no": option.choice_no,
                        "content": option.content,
                        "is_answer": option.is_answer,
                        "choice_explanation": option.choice_explanation,
                    }
                    for option in options
                ],
            }
        )

    return JsonResponse({"problems": problems}, json_dumps_params={"ensure_ascii": False})


@require_GET
def image_proxy(request):
    url = (request.GET.get("url") or "").strip()
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https" or parsed.netloc != "contents.history.go.kr":
        return JsonResponse({"error": "허용되지 않은 이미지 URL입니다."}, status=400)
    if not parsed.path.startswith("/data/img/"):
        return JsonResponse({"error": "허용되지 않은 이미지 경로입니다."}, status=400)

    try:
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0",
                "Referer": "https://contents.history.go.kr/",
            },
        )
        with urllib.request.urlopen(req, timeout=45) as response:
            content = response.read()
            content_type = response.headers.get("Content-Type", "image/jpeg")
    except (urllib.error.URLError, TimeoutError) as exc:
        return JsonResponse(
            {"error": "이미지를 불러오지 못했습니다.", "detail": str(exc)},
            status=502,
        )

    return HttpResponse(content, content_type=content_type)
