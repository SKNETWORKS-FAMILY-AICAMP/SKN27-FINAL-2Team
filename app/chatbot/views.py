from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request

from django.http import HttpResponse, JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_GET
from django.views.decorators.http import require_POST

from .rag_service import build_history_rag_answer


def chat_page(request):
    return render(request, "chatbot/chat.html")


@require_POST
def rag_chat_api(request):
    try:
        payload = json.loads(request.body.decode("utf-8"))
    except json.JSONDecodeError:
        return JsonResponse({"error": "요청 JSON 형식이 올바르지 않습니다."}, status=400)

    question = (payload.get("question") or "").strip()
    if not question:
        return JsonResponse({"error": "질문을 입력해 주세요."}, status=400)

    mode = payload.get("mode") or "history"
    answer_format = payload.get("answer_format") or "structured"
    follow_up = bool(payload.get("follow_up", False))
    top_k = int(payload.get("top_k") or 5)

    try:
        result = build_history_rag_answer(
            question=question,
            mode=mode,
            answer_format=answer_format,
            follow_up=follow_up,
            top_k=top_k,
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
