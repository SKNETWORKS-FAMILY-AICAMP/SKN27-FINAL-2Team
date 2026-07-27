"""V41 record를 RunPod OpenAI 호환 endpoint로 그대로 전송한다."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from argparse import Namespace
from typing import Any


DEFAULT_MODEL = "cubixsamju/hanneung-qwen25-7b-v41-merged-47-78"


def clean_model_text(text: str) -> str:
    """검증 전 모델 원문을 바꾸지 않고 연속 공백만 정리한다."""
    return " ".join((text or "").split())


def parse_model_json(content: str) -> dict[str, Any]:
    """응답에서 첫 JSON 객체를 읽는다."""
    start, end = content.find("{"), content.rfind("}")
    if start < 0 or end < start:
        return {}
    try:
        value = json.loads(content[start : end + 1])
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def call_chat(args: Namespace, record: dict[str, Any]) -> dict[str, Any]:
    """추가 지시 없이 V41 system, instruction, input만 전송한다."""
    payload = {
        "model": args.model,
        "messages": [
            {"role": "system", "content": record["system"]},
            {
                "role": "user",
                "content": record["instruction"] + "\n\n" + json.dumps(record["input"], ensure_ascii=False, indent=2),
            },
        ],
        "temperature": args.temperature,
        "max_tokens": args.max_tokens,
    }
    request = urllib.request.Request(
        f"https://api.runpod.ai/v2/{args.endpoint_id}/openai/v1/chat/completions",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Authorization": f"Bearer {args.api_key}", "Content-Type": "application/json"},
        method="POST",
    )
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(request, timeout=args.timeout) as response:
            body = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"RunPod request failed: HTTP {exc.code} {detail}") from exc
    content = body["choices"][0]["message"]["content"]
    return {
        "seed_id": record.get("seed_id"),
        "choice_role": record.get("choice_role"),
        "distractor_index": record.get("distractor_index"),
        "elapsed_seconds": round(time.perf_counter() - started, 3),
        "request_input": record.get("input"),
        "raw_content": content,
        "json": parse_model_json(content),
    }
