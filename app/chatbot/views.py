from __future__ import annotations

import json
import logging
import urllib.error
import urllib.parse
import urllib.request

from django.http import HttpResponse, JsonResponse, StreamingHttpResponse
from django.contrib.auth.decorators import login_required
from django.urls import reverse
from django.shortcuts import render
from django.utils import timezone
from django.db import transaction
from django.views.decorators.http import require_GET
from django.views.decorators.http import require_POST

from question.models import QuestionOptions, SolveRecords
from user.views import build_session_display_map

from .models import ChatMessages, ChatSessions
from .rag_service import build_history_rag_answer, stream_concept_rag_answer, stream_question_rag_answer


logger = logging.getLogger(__name__)


def proxied_image_path(value: object) -> str:
    url = str(value or "").strip()
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme == "https" and parsed.netloc == "contents.history.go.kr" and parsed.path.startswith("/data/img/"):
        return f"{reverse('chatbot:image_proxy')}?{urllib.parse.urlencode({'url': url})}"
    return url


def load_problem_choice_explanations(user, record_id: int | None) -> dict[int, str]:
    if record_id is None:
        return {}
    question_id = (
        SolveRecords.objects.filter(record_id=record_id, session__user=user)
        .values_list("question_id", flat=True)
        .first()
    )
    if question_id is None:
        return {}
    return {
        option.choice_no: option.choice_explanation.strip()
        for option in QuestionOptions.objects.filter(question_id=question_id).order_by("choice_no")
        if option.choice_explanation and option.choice_explanation.strip()
    }


@login_required
def chat_page(request):
    return render(request, "chatbot/chat.html")


def save_chat_turn(request, session_id: str, user_content: str, result: dict) -> None:
    if not session_id:
        return
    now = timezone.now()
    with transaction.atomic():
        session, created = ChatSessions.objects.select_for_update().get_or_create(
            session_id=session_id[:50],
            defaults={
                "chat_type": "history",
                "turn_count": 0,
                "status": "active",
                "created_at": now,
                "user": request.user,
            },
        )
        if session.user_id != request.user.user_id:
            return
        ChatMessages.objects.create(
            session=session,
            sender_type="user",
            content=user_content,
            created_at=now,
        )
        ChatMessages.objects.create(
            session=session,
            sender_type="assistant",
            content=json.dumps(result, ensure_ascii=False),
            created_at=now,
        )
        keep_ids = list(
            ChatMessages.objects.filter(session=session)
            .order_by("-created_at", "-message_id")
            .values_list("message_id", flat=True)[:10]
        )
        ChatMessages.objects.filter(session=session).exclude(message_id__in=keep_ids).delete()
        session.turn_count = min((session.turn_count or 0) + 1, 5)
        session.status = "active"
        session.save(update_fields=["turn_count", "status"])


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
    try:
        top_k = min(max(int(payload.get("top_k") or 20), 1), 20)
    except (TypeError, ValueError):
        return JsonResponse({"error": "top_k 값이 올바르지 않습니다."}, status=400)
    session_id = str(payload.get("session_id") or "").strip()
    display_question = str(payload.get("display_question") or question).strip()
    conversation_history = payload.get("conversation_history")
    if not isinstance(conversation_history, list):
        conversation_history = []
    try:
        problem_session_id = int(payload.get("problem_session_id"))
    except (TypeError, ValueError):
        problem_session_id = None
    try:
        problem_record_id = int(payload.get("problem_record_id"))
    except (TypeError, ValueError):
        problem_record_id = None
    choice_explanations = load_problem_choice_explanations(request.user, problem_record_id)
    explanation_level = "foundation" if intent == "question" and payload.get("foundation_explanation") is True else "core" if intent == "question" else ""

    try:
        result = build_history_rag_answer(
            question=question,
            mode=mode,
            intent=intent,
            answer_format=answer_format,
            follow_up=follow_up,
            top_k=top_k,
            history=conversation_history,
            explanation_level=explanation_level,
            choice_explanations=choice_explanations,
        )
    except Exception:
        logger.exception("RAG 답변 생성 실패")
        return JsonResponse({"error": "RAG 답변 생성 중 오류가 발생했습니다."}, status=500)

    try:
        save_chat_turn(request, session_id, display_question, result)
    except Exception:
        logger.exception("채팅 기록 저장 실패 session_id=%s", session_id)
    return JsonResponse(result, json_dumps_params={"ensure_ascii": False})


@require_POST
def rag_chat_stream_api(request):
    if not request.user.is_authenticated:
        return JsonResponse({"error": "로그인이 필요합니다."}, status=401)
    try:
        payload = json.loads(request.body.decode("utf-8"))
    except json.JSONDecodeError:
        return JsonResponse({"error": "요청 JSON 형식이 올바르지 않습니다."}, status=400)
    question = str(payload.get("question") or "").strip()
    if not question:
        return JsonResponse({"error": "질문을 입력해 주세요."}, status=400)
    try:
        top_k = min(max(int(payload.get("top_k") or 20), 1), 20)
    except (TypeError, ValueError):
        return JsonResponse({"error": "top_k 값이 올바르지 않습니다."}, status=400)
    session_id = str(payload.get("session_id") or "").strip()
    display_question = str(payload.get("display_question") or question).strip()
    history = payload.get("conversation_history") if isinstance(payload.get("conversation_history"), list) else []
    try:
        problem_session_id = int(payload.get("problem_session_id"))
    except (TypeError, ValueError):
        problem_session_id = None
    try:
        problem_record_id = int(payload.get("problem_record_id"))
    except (TypeError, ValueError):
        problem_record_id = None
    choice_explanations = load_problem_choice_explanations(request.user, problem_record_id)
    intent = payload.get("intent") or "concept"
    explanation_level = "foundation" if intent == "question" and payload.get("foundation_explanation") is True else "core" if intent == "question" else ""

    def stream():
        try:
            answer_stream = (
                stream_question_rag_answer(
                    question,
                    mode=payload.get("mode") or "history",
                    top_k=top_k,
                    history=history,
                    explanation_level=explanation_level,
                    choice_explanations=choice_explanations,
                )
                if intent == "question"
                else stream_concept_rag_answer(question, mode=payload.get("mode") or "history", top_k=top_k, history=history)
            )
            for event in answer_stream:
                if event["type"] == "done":
                    if intent == "question":
                        event["data"]["problem_session_id"] = problem_session_id
                    try:
                        save_chat_turn(request, session_id, display_question, event["data"])
                    except Exception:
                        logger.exception("채팅 기록 저장 실패 session_id=%s", session_id)
                yield f"event: {event['type']}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"
        except Exception:
            logger.exception("RAG 스트리밍 답변 생성 실패")
            yield 'event: error\ndata: {"type":"error","error":"RAG 답변 생성 중 오류가 발생했습니다."}\n\n'

    response = StreamingHttpResponse(stream(), content_type="text/event-stream; charset=utf-8")
    response["Cache-Control"] = "no-cache"
    response["X-Accel-Buffering"] = "no"
    return response


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

    sessions = list({record.session_id: record.session for record in records}.values())
    session_display_map = build_session_display_map(request.user, sessions)
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
                "passage": getattr(question, "passage", ""),
                "image_caption": getattr(question, "image_caption", ""),
                "question_image_path": proxied_image_path(getattr(question, "question_image_path", "")),
                "question_type": question.question_type,
                "era": record.era,
                "topic": record.topic,
                "selected_no": record.selected_no,
                "answer_no": question.answer_no,
                "is_correct": record.is_correct,
                "answer_explanation": question.answer_explanation,
                "core_concept": question.core_concept,
                "session_label": session_display_map.get(session_id, {}).get("full", "풀이 기록"),
                "solved_url": f"/user/solved-problems/?session_id={session_id}",
                "options": [
                    {
                        "choice_no": option.choice_no,
                        "content": option.content,
                        "choice_image_path": proxied_image_path(option.choice_image_path),
                        "is_answer": option.is_answer,
                        "choice_explanation": option.choice_explanation,
                    }
                    for option in options
                ],
            }
        )

    return JsonResponse({"problems": problems}, json_dumps_params={"ensure_ascii": False})


@login_required
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
    except (urllib.error.URLError, TimeoutError):
        logger.exception("이미지 프록시 요청 실패")
        return JsonResponse({"error": "이미지를 불러오지 못했습니다."}, status=502)

    return HttpResponse(content, content_type=content_type)
