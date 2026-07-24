from __future__ import annotations

import argparse
import json
import os
import re
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_INPUT = Path(r"C:\Users\Playdata\Downloads\generated_predictions (8).jsonl")
DEFAULT_OUT_DIR = Path(__file__).resolve().parent / "hint_variant_results"


CHECKLIST_HINT = """
[추가 자기검수 체크리스트]
출력 직전 아래를 반드시 확인하라.
1. 요구된 JSON 키만 출력한다. 설명, 해설, 정답 번호, 선택지 번호를 추가하지 않는다.
2. 정답 선지는 answer_fact_basis에 있는 사실만 사용한다.
3. 오답 선지는 distractor_fact_basis의 사실에 근거하되, answer_choice와 의미가 같아지면 안 된다.
4. 오답이어도 문장 자체는 한국사 사실로 성립해야 한다. 가짜 용어, 가짜 사건, 서로 다른 시대·주체·사건의 허위 결합을 만들지 않는다.
5. 발문·자료의 핵심 표현을 정답 선지에 그대로 반복하여 단어 매칭만으로 정답이 드러나게 하지 않는다.
6. 한능검 선지처럼 간결하지만, 핵심 주체·사건·시기·결과 중 필요한 정보는 빠뜨리지 않는다.
""".strip()


EXAMPLES_HINT = """
[좋은/나쁜 예시]
좋은 오답 예시:
- 정답 대상이 이순신일 때 `행주대첩을 지휘하였다`는 권율의 실제 활동이므로 정상 오답이다.
- 정답 대상이 후백제일 때 `국호를 마진으로 바꾸고 철원으로 천도하였다`는 궁예 계열 사실이므로 정상 오답이다.

나쁜 오답 예시:
- `척화비를 세운 뒤 갑신정변을 일으켰다`: 흥선 대원군 계열 사실과 갑신정변을 허위 결합했다.
- `훈민정음을 반포하기 위해 태학을 설립하였다`: 서로 다른 시대·제도를 목적 관계로 허위 결합했다.
- `김헌창이 왕위에 올라 반란을 진압하였다`: 입력 근거와 반대로 사실을 왜곡했다.

좋은 정답 선지 예시:
- `공산 전투에서 견훤이 이끄는 후백제군이 고려군을 크게 격파하였다.`

주의할 정답 선지 예시:
- 너무 길게 부연하여 근거에 없는 목적·평가를 덧붙이지 않는다.
- 발문 자료의 핵심 표현을 그대로 복사하지 않는다.
""".strip()


GATE_HINT = """
[우리 평가 Gate 요약]
생성 결과는 아래 Gate를 통과해야 한다.
G1 입력·형식 성립: 요구 키와 JSON 형식이 정확해야 한다.
G2 판독 가능성: 문장이 깨지거나 비문이 심하면 안 된다.
G3 정답 성립성·유일성: 발문 조건을 만족하는 정답 후보가 정확히 하나여야 한다.
G4 발문·자료 사실성: 자료와 발문이 한국사 사실과 명백히 충돌하면 안 된다.
G5 오답 역사 사실성: 오답은 정답 대상에는 틀려도, 다른 실제 역사 사실로 성립해야 한다. 가짜 사실·허위 결합은 금지한다.
G6 정답 노출 금지: 발문·자료와 정답 선지가 같은 핵심 명제를 반복해 단어 매칭으로 정답이 드러나면 안 된다.

Gate FAIL 가능성이 보이면, 더 안전한 표현으로 바꾼 뒤 최종 JSON만 출력하라.
""".strip()


PROFILE_HINTS = {
    "p1_checklist": CHECKLIST_HINT,
    "p2_checklist_examples": CHECKLIST_HINT + "\n\n" + EXAMPLES_HINT,
    "p3_checklist_examples_gate": CHECKLIST_HINT + "\n\n" + EXAMPLES_HINT + "\n\n" + GATE_HINT,
}


def load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def extract_request(prompt: str) -> dict[str, Any]:
    decoder = json.JSONDecoder()
    start = prompt.find("{")
    if start < 0:
        raise ValueError("prompt JSON object not found")
    request, _ = decoder.raw_decode(prompt[start:])
    return request


def parse_chat_template(prompt: str) -> list[dict[str, str]]:
    pattern = re.compile(r"<\|im_start\|>(system|user|assistant)\n(.*?)(?:<\|im_end\|>|$)", re.DOTALL)
    messages: list[dict[str, str]] = []
    for role, content in pattern.findall(prompt):
        content = content.strip()
        if role == "assistant" and not content:
            continue
        messages.append({"role": role, "content": content})
    if not messages:
        messages.append({"role": "user", "content": prompt})
    return messages


def add_hint(messages: list[dict[str, str]], hint: str) -> list[dict[str, str]]:
    copied = [dict(message) for message in messages]
    for message in reversed(copied):
        if message["role"] == "user":
            message["content"] = message["content"].rstrip() + "\n\n" + hint
            return copied
    copied.append({"role": "user", "content": hint})
    return copied


def call_openai(
    *,
    base_url: str,
    api_key: str,
    model: str,
    messages: list[dict[str, str]],
    temperature: float,
    timeout: int,
    max_retries: int,
) -> tuple[str, dict[str, Any]]:
    url = base_url.rstrip("/") + "/chat/completions"
    body = {
        "model": model,
        "messages": messages,
        "response_format": {"type": "json_object"},
    }
    if not model.startswith("gpt-5"):
        body["temperature"] = temperature
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    last_error: Exception | None = None
    for attempt in range(max_retries + 1):
        request = urllib.request.Request(url, data=data, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
            return payload["choices"][0]["message"]["content"] or "", payload.get("usage") or {}
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            last_error = RuntimeError(f"OpenAI API error {exc.code}: {detail}")
        except urllib.error.URLError as exc:
            last_error = RuntimeError(f"OpenAI API request failed: {exc}")
        if attempt < max_retries:
            time.sleep(2 * (attempt + 1))
    raise RuntimeError(str(last_error) if last_error else "OpenAI API request failed")


def generate_profile(
    *,
    rows: list[dict[str, Any]],
    profile: str,
    hint: str,
    api_key: str,
    base_url: str,
    model: str,
    temperature: float,
    timeout: int,
    max_retries: int,
    out_dir: Path,
) -> Path:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = out_dir / f"{profile}_first{len(rows)}_{stamp}.json"
    results: list[dict[str, Any]] = []
    for index, row in enumerate(rows, start=1):
        request_obj = extract_request(row["prompt"])
        messages = add_hint(parse_chat_template(row["prompt"]), hint)
        output, usage = call_openai(
            base_url=base_url,
            api_key=api_key,
            model=model,
            messages=messages,
            temperature=temperature,
            timeout=timeout,
            max_retries=max_retries,
        )
        results.append(
            {
                "item_index": index,
                "profile": profile,
                "request": request_obj,
                "yj_output": row["predict"],
                "gpt_output": output,
                "label": row.get("label", ""),
                "usage": usage,
            }
        )
        print(f"[{profile} {index}/{len(rows)}] {request_obj.get('task_type')} ok")
    out_path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"saved: {out_path}")
    return out_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate GPT outputs with incremental hints.")
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--profile", choices=sorted(PROFILE_HINTS), default=None)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument("--model", default="gpt-4.1-mini")
    parser.add_argument("--temperature", type=float, default=0.2)
    parser.add_argument("--timeout", type=int, default=120)
    parser.add_argument("--max-retries", type=int, default=2)
    parser.add_argument("--base-url", default=os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1"))
    args = parser.parse_args()

    load_dotenv(PROJECT_ROOT / ".env")
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not set in environment or .env")

    rows = read_jsonl(args.input)[: args.limit]
    args.out_dir.mkdir(parents=True, exist_ok=True)
    profiles = [args.profile] if args.profile else list(PROFILE_HINTS)
    for profile in profiles:
        generate_profile(
            rows=rows,
            profile=profile,
            hint=PROFILE_HINTS[profile],
            api_key=api_key,
            base_url=args.base_url,
            model=args.model,
            temperature=args.temperature,
            timeout=args.timeout,
            max_retries=args.max_retries,
            out_dir=args.out_dir,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
