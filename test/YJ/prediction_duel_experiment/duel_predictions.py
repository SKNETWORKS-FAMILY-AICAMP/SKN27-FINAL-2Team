from __future__ import annotations

import argparse
import json
import os
import random
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_INPUT = Path(r"C:\Users\Playdata\Downloads\generated_predictions (8).jsonl")
DEFAULT_OUT_DIR = Path(__file__).resolve().parent / "results"


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
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


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


def call_openai(
    *,
    base_url: str,
    api_key: str,
    model: str,
    messages: list[dict[str, str]],
    temperature: float,
    timeout: int,
    json_mode: bool,
    max_retries: int,
) -> str:
    url = base_url.rstrip("/") + "/chat/completions"
    body: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
    }
    if json_mode:
        body["response_format"] = {"type": "json_object"}

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
            return payload["choices"][0]["message"]["content"] or ""
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            last_error = RuntimeError(f"OpenAI API error {exc.code}: {detail}")
        except urllib.error.URLError as exc:
            last_error = RuntimeError(f"OpenAI API request failed: {exc}")
        if attempt < max_retries:
            time.sleep(2 * (attempt + 1))
    raise RuntimeError(str(last_error) if last_error else "OpenAI API request failed")


def compact_json_text(text: str) -> str:
    try:
        return json.dumps(json.loads(text), ensure_ascii=False)
    except Exception:
        return str(text).strip()


def build_judge_messages(
    *,
    item_index: int,
    request_obj: dict[str, Any],
    label_text: str,
    candidate_a: str,
    candidate_b: str,
) -> list[dict[str, str]]:
    task_type = request_obj.get("task_type", "")
    required_key = "answer_choice" if task_type == "correct_choice_generation" else "distractor_choice"
    if task_type == "correct_choice_generation":
        required_key = "question, answer_choice"

    system = (
        "너는 한국사능력검정시험 심화 문항 생성 결과를 블라인드 비교하는 엄격한 평가자이다. "
        "후보 A/B가 어떤 모델의 출력인지 추측하지 말고, 입력 조건과 출력 품질만 비교한다. "
        "반드시 JSON 객체만 출력한다."
    )
    user = f"""
아래 두 후보를 비교하라. reference_label은 가능한 정답 예시일 뿐이며, 완전한 문자열 일치만으로 판단하지 않는다.

[공통 입력 조건]
{json.dumps(request_obj, ensure_ascii=False, indent=2)}

[요구 출력 키]
{required_key}

[reference_label]
{compact_json_text(label_text)}

[candidate A]
{compact_json_text(candidate_a)}

[candidate B]
{compact_json_text(candidate_b)}

평가 기준:
1. 필수 JSON 키만 정확히 출력했는가.
2. 출력이 material, question, answer_choice 등 입력 맥락과 충돌하지 않는가.
3. 정답 선지라면 answer_fact_basis에 근거하고, 오답 선지라면 distractor_fact_basis에 근거하는가.
4. 역사적으로 성립하는 문장인가. 없는 사실이나 서로 다른 사실의 허위 결합이 있으면 크게 감점한다.
5. 한능검 선지로 충분히 구체적이고 자연스러운가. 너무 뭉뚱그리거나 핵심 장소·주체·결과를 빼면 감점한다.
6. 오답 생성에서는 answer_choice와 의미가 같아지면 안 된다.

출력 JSON 형식:
{{
  "item_index": {item_index},
  "task_type": "{task_type}",
  "winner": "A|B|tie",
  "confidence": 0.0,
  "score_A": 0,
  "score_B": 0,
  "reason": "승패 이유를 2~4문장으로 설명",
  "candidate_A_issue": "없으면 없음",
  "candidate_B_issue": "없으면 없음"
}}
""".strip()
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def summarize(results: list[dict[str, Any]], out_path: Path, gen_model: str, judge_model: str) -> None:
    yj_wins = sum(1 for item in results if item["winner_owner"] == "YJ")
    gpt_wins = sum(1 for item in results if item["winner_owner"] == "GPT")
    ties = sum(1 for item in results if item["winner_owner"] == "tie")

    lines = [
        f"# Prediction Duel First {len(results)}",
        "",
        f"- GPT generation model: `{gen_model}`",
        f"- Judge model: `{judge_model}`",
        f"- Result: YJ {yj_wins} / GPT {gpt_wins} / tie {ties}",
        "",
        "| # | task | topic | YJ output | GPT output | winner | reason |",
        "|---:|---|---|---|---|---|---|",
    ]
    for item in results:
        request_obj = item["request"]
        yj_out = compact_json_text(item["yj_output"]).replace("|", "\\|")
        gpt_out = compact_json_text(item["gpt_output"]).replace("|", "\\|")
        reason = str(item["judge"].get("reason", "")).replace("\n", " ").replace("|", "\\|")
        lines.append(
            "| {idx} | {task} | {topic} | `{yj}` | `{gpt}` | **{winner}** | {reason} |".format(
                idx=item["item_index"],
                task=request_obj.get("task_type", ""),
                topic=request_obj.get("topic", ""),
                yj=yj_out,
                gpt=gpt_out,
                winner=item["winner_owner"],
                reason=reason,
            )
        )
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Blind duel between local predictions and GPT outputs.")
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument("--gen-model", default="gpt-4.1-mini")
    parser.add_argument("--judge-model", default="gpt-4.1-mini")
    parser.add_argument("--temperature", type=float, default=0.2)
    parser.add_argument("--timeout", type=int, default=90)
    parser.add_argument("--max-retries", type=int, default=2)
    parser.add_argument("--base-url", default=os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1"))
    args = parser.parse_args()

    load_dotenv(PROJECT_ROOT / ".env")
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not set in environment or .env")

    args.out_dir.mkdir(parents=True, exist_ok=True)
    rows = read_jsonl(args.input)[: args.limit]
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    raw_path = args.out_dir / f"duel_first{len(rows)}_{stamp}.json"
    summary_path = args.out_dir / f"duel_first{len(rows)}_{stamp}_summary.md"

    rng = random.Random(20260630)
    results: list[dict[str, Any]] = []
    for idx, row in enumerate(rows, 1):
        request_obj = extract_request(row["prompt"])
        messages = parse_chat_template(row["prompt"])
        gpt_output = call_openai(
            base_url=args.base_url,
            api_key=api_key,
            model=args.gen_model,
            messages=messages,
            temperature=args.temperature,
            timeout=args.timeout,
            json_mode=True,
            max_retries=args.max_retries,
        )

        swapped = bool(rng.getrandbits(1))
        if swapped:
            candidate_a, candidate_b = gpt_output, row["predict"]
            owner_a, owner_b = "GPT", "YJ"
        else:
            candidate_a, candidate_b = row["predict"], gpt_output
            owner_a, owner_b = "YJ", "GPT"

        judge_text = call_openai(
            base_url=args.base_url,
            api_key=api_key,
            model=args.judge_model,
            messages=build_judge_messages(
                item_index=idx,
                request_obj=request_obj,
                label_text=row.get("label", ""),
                candidate_a=candidate_a,
                candidate_b=candidate_b,
            ),
            temperature=0,
            timeout=args.timeout,
            json_mode=True,
            max_retries=args.max_retries,
        )
        judge = json.loads(judge_text)
        winner = str(judge.get("winner", "tie")).strip()
        if winner == "A":
            winner_owner = owner_a
        elif winner == "B":
            winner_owner = owner_b
        else:
            winner_owner = "tie"

        result = {
            "item_index": idx,
            "request": request_obj,
            "yj_output": row["predict"],
            "gpt_output": gpt_output,
            "label": row.get("label", ""),
            "candidate_mapping": {"A": owner_a, "B": owner_b},
            "judge": judge,
            "winner_owner": winner_owner,
        }
        results.append(result)
        print(f"[{idx}/{len(rows)}] {request_obj.get('task_type')} -> {winner_owner}")

    raw_path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    summarize(results, summary_path, args.gen_model, args.judge_model)
    print(f"raw: {raw_path}")
    print(f"summary: {summary_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
