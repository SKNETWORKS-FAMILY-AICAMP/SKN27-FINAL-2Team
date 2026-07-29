from __future__ import annotations

import json
import logging
import urllib.error
import urllib.parse
import urllib.request

from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.db import connection, transaction
from django.http import (
    HttpRequest,
    HttpResponse,
    JsonResponse,
    StreamingHttpResponse,
)
from django.shortcuts import render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from question.models import QuestionOptions, SolveRecords
from user.views import build_session_display_map

from .models import ChatMessages, ChatSessions
from .rag_service import build_history_rag_answer, stream_concept_rag_answer, stream_question_rag_answer


logger = logging.getLogger(__name__)


def _chat_request_blocked(
    request: HttpRequest,
    question: str,
    history: list[object],
) -> JsonResponse | None:
    """챗봇 입력 크기와 사용자별 호출 빈도를 제한한다.

    반환이 JsonResponse 면 그대로 응답하고 None 이면 통과. 공유 캐시
    (DatabaseCache)라 gunicorn 워커 전체에 같은 한도가 적용된다.
    """
    maximum_question_chars = 2000
    maximum_history_items = 20
    maximum_history_chars = 12000
    maximum_calls_per_window = 20
    window_seconds = 60

    if len(question) > maximum_question_chars:
        return JsonResponse(
            {"error": "질문이 너무 깁니다. 2000자 이내로 입력해 주세요."},
            status=400,
        )
    if isinstance(history, list):
        if len(history) > maximum_history_items:
            return JsonResponse({"error": "대화 기록이 너무 깁니다."}, status=400)
        if sum(len(str(item)) for item in history) > maximum_history_chars:
            return JsonResponse({"error": "대화 기록이 너무 깁니다."}, status=400)

    cache_key = f"chat-rate:{request.user.user_id}"
    with transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                [cache_key],
            )
        call_count = cache.get(cache_key, 0)
        if call_count >= maximum_calls_per_window:
            return JsonResponse(
                {"error": "요청이 너무 잦습니다. 잠시 후 다시 시도해 주세요."},
                status=429,
            )
        cache.set(cache_key, call_count + 1, window_seconds)
    return None


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
    chat_block = _chat_request_blocked(request, question, conversation_history)
    if chat_block is not None:
        return chat_block
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
    chat_block = _chat_request_blocked(request, question, history)
    if chat_block is not None:
        return chat_block
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


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """리다이렉트를 따라가지 않는다. 허용 호스트에 open redirect 가 있어도
    내부 주소로 우회(SSRF)하지 못하게 막는다."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _is_allowed_image_url(url: str) -> bool:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https" or parsed.netloc != "contents.history.go.kr":
        return False

    return parsed.path.startswith("/data/img/")


@login_required
@require_GET
def image_proxy(request):
    maximum_bytes = 10 * 1024 * 1024
    timeout_seconds = 15
    allowed_content_types = {
        "image/gif",
        "image/jpeg",
        "image/png",
        "image/webp",
    }

    url = (request.GET.get("url") or "").strip()
    if not _is_allowed_image_url(url):
        return JsonResponse({"error": "허용되지 않은 이미지 URL입니다."}, status=400)

    opener = urllib.request.build_opener(_NoRedirectHandler)
    try:
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0",
                "Referer": "https://contents.history.go.kr/",
            },
        )
        with opener.open(req, timeout=timeout_seconds) as response:
            content_type = response.headers.get("Content-Type", "")
            normalized_content_type = content_type.partition(";")[0].strip().lower()
            if normalized_content_type not in allowed_content_types:
                return JsonResponse({"error": "이미지가 아닌 응답입니다."}, status=502)
            # 크기 상한을 두고 한도를 넘으면 중단해 메모리 고갈을 막는다.
            content = response.read(maximum_bytes + 1)
            if len(content) > maximum_bytes:
                return JsonResponse({"error": "이미지 용량이 너무 큽니다."}, status=502)
    except urllib.error.HTTPError as error:
        # 리다이렉트(3xx)는 위 핸들러가 막아 여기서 오류로 떨어진다.
        logger.warning("이미지 프록시 거부 status=%s", error.code)
        return JsonResponse({"error": "이미지를 불러오지 못했습니다."}, status=502)
    except (urllib.error.URLError, TimeoutError):
        logger.exception("이미지 프록시 요청 실패")
        return JsonResponse({"error": "이미지를 불러오지 못했습니다."}, status=502)

    response = HttpResponse(content, content_type=normalized_content_type)
    response["X-Content-Type-Options"] = "nosniff"
    return response
