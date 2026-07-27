from __future__ import annotations

import argparse
import json
import os
import re
import time
from pathlib import Path
from typing import Any


DEFAULT_MODEL = "gpt-5-mini"
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DOWNLOADS_DIR = Path.home() / "Downloads"

DEFAULT_INPUT_FILES = [
    DOWNLOADS_DIR / "01_LLM_게이트_통과_518.json",
    DOWNLOADS_DIR / "02_로컬검사_통과_LLM평가대기_586.json",
    DOWNLOADS_DIR / "05_검증정보_없음_5.json",
]

DEFAULT_OUTPUT = Path(__file__).resolve().parent / "gpt_error_labels.jsonl"
DEFAULT_SUMMARY = Path(__file__).resolve().parent / "gpt_error_label_summary.json"

ERROR_CODES = {
    "ANSWER_IN_PASSAGE": "정답 선지가 지문/질문에 직접 포함되어 정답이 노출됨",
    "ANSWER_LENGTH_BIAS": "정답 선지만 다른 선지보다 유독 길거나 짧음",
    "NO_OR_MULTI_ANSWER": "표시 정답이 0개이거나 2개 이상임",
    "WEIRD_CHOICE": "선지가 문항 맥락에 비해 너무 어색하거나 오답 선지로 부적절함",
    "CHOICE_FORMAT_ERROR": "선지 문장, 표기, 외국 문자, 메타어, 제목형 표현 등 형식 문제",
    "DUPLICATE_CHOICE": "서로 같은 선지 또는 거의 같은 선지가 있음",
    "QUESTION_FORMAT_ERROR": "발문이 지문/표시 형식과 맞지 않음",
    "NO_CLEAR_ERROR": "명확한 오류를 찾기 어려움",
}


def load_dotenv(path: Path) -> None:
    if not path.exists():
        return

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def append_jsonl(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def load_processed_keys(path: Path) -> set[str]:
    if not path.exists():
        return set()

    keys: set[str] = set()
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            key = str(row.get("question_key") or "")
            if key:
                keys.add(key)
    return keys


def extract_questions(data: Any) -> list[dict[str, Any]]:
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    if isinstance(data, dict) and isinstance(data.get("questions"), list):
        return [item for item in data["questions"] if isinstance(item, dict)]
    if isinstance(data, dict):
        return [data]
    return []


def question_key(question: dict[str, Any], source_path: Path) -> str:
    seed = question.get("seed_id") or question.get("id") or question.get("problem_id") or "unknown"
    source_file = question.get("_source_file") or source_path.name
    return f"{source_file}::{seed}"


def choice_text(choice: Any) -> str:
    if isinstance(choice, dict):
        return str(choice.get("text") or "")
    return str(choice or "")


def choice_number(choice: Any, fallback: int) -> int:
    if isinstance(choice, dict):
        try:
            return int(choice.get("number"))
        except (TypeError, ValueError):
            return fallback
    return fallback


def choice_is_answer(choice: Any, answer_number: int | None, fallback: int) -> bool:
    if isinstance(choice, dict) and "is_answer" in choice:
        return bool(choice.get("is_answer"))
    return answer_number == fallback


def answer_number(question: dict[str, Any]) -> int | None:
    for key in ("answer_number", "answer", "answer_index"):
        value = question.get(key)
        if value is None:
            continue
        try:
            number = int(value)
        except (TypeError, ValueError):
            continue
        if key == "answer_index" and number in range(0, 5):
            return number + 1
        return number
    return None


def compact_question(question: dict[str, Any]) -> dict[str, Any]:
    answer_no = answer_number(question)
    choices = []
    for idx, choice in enumerate(question.get("choices") or [], start=1):
        number = choice_number(choice, idx)
        choices.append(
            {
                "number": number,
                "text": choice_text(choice),
                "is_answer": choice_is_answer(choice, answer_no, number),
            }
        )

    return {
        "seed_id": question.get("seed_id") or question.get("id") or question.get("problem_id"),
        "topic": question.get("topic"),
        "material": question.get("material") or question.get("passage") or "",
        "question": question.get("question") or "",
        "choices": choices,
        "answer_number": answer_no,
        "existing_validation_errors": (question.get("validation") or {}).get("errors", []),
    }


def local_rule_labels(question: dict[str, Any]) -> tuple[list[str], list[dict[str, Any]]]:
    compact = compact_question(question)
    choices = compact["choices"]
    answer_choices = [choice for choice in choices if choice["is_answer"]]
    problem_codes: list[str] = []
    choice_labels: list[dict[str, Any]] = [
        {"number": choice["number"], "label": 1, "error_codes": [], "reason_ko": ""}
        for choice in choices
    ]

    if len(answer_choices) != 1:
        problem_codes.append("NO_OR_MULTI_ANSWER")

    normalized_choices = [normalize(choice["text"]) for choice in choices]
    seen: dict[str, int] = {}
    duplicate_numbers: set[int] = set()
    for choice, norm in zip(choices, normalized_choices):
        if not norm:
            continue
        if norm in seen:
            duplicate_numbers.add(seen[norm])
            duplicate_numbers.add(choice["number"])
        else:
            seen[norm] = choice["number"]
    if duplicate_numbers:
        problem_codes.append("DUPLICATE_CHOICE")
        for label in choice_labels:
            if label["number"] in duplicate_numbers:
                label["label"] = 0
                label["error_codes"].append("DUPLICATE_CHOICE")
                label["reason_ko"] = "동일하거나 거의 동일한 선지가 반복됩니다."

    return problem_codes, choice_labels


def normalize(text: str) -> str:
    return re.sub(r"\s+", "", str(text or "")).lower()


def build_prompt(question: dict[str, Any]) -> str:
    compact = compact_question(question)
    return f"""
다음 한국사 객관식 문항이 2차 검수에서 왜 이상한지 분류하세요.

중요한 원칙:
- 역사적 사실 자체의 참/거짓은 판단하지 않습니다.
- 입력으로 주어진 지문, 질문, 선지, 정답 여부만 보고 판단합니다.
- 오류가 명확하지 않으면 억지로 만들지 말고 NO_CLEAR_ERROR를 사용합니다.
- 선지 단위 학습 데이터로 쓸 예정이므로, 문제가 있는 선지 번호를 최대한 지정합니다.

사용 가능한 오류 코드:
{json.dumps(ERROR_CODES, ensure_ascii=False, indent=2)}

판단해야 할 주요 기준:
- 정답 선지가 지문/질문 표현을 거의 그대로 반복해 정답이 노출되는가?
- 정답 선지만 다른 선지에 비해 유독 길거나 짧은가?
- 정답 표시가 0개 또는 2개 이상인가?
- 선지에 외국 문자, 메타어, 깨진 문장, 제목형 표현 등 형식 문제가 있는가?
- 선지들이 중복되거나 거의 같은가?
- 발문이 밑줄/보기/자료 형식과 맞지 않는가?
- 오답 선지가 문항 맥락에서 지나치게 이상하거나 시험 선지로 부적절한가?

문항:
{json.dumps(compact, ensure_ascii=False, indent=2)}
""".strip()


JSON_SCHEMA = {
    "name": "question_error_label",
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "problem_label": {"type": "integer", "enum": [0, 1]},
            "problem_error_codes": {
                "type": "array",
                "items": {"type": "string", "enum": list(ERROR_CODES.keys())},
            },
            "choice_labels": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "number": {"type": "integer"},
                        "label": {"type": "integer", "enum": [0, 1]},
                        "error_codes": {
                            "type": "array",
                            "items": {"type": "string", "enum": list(ERROR_CODES.keys())},
                        },
                        "reason_ko": {"type": "string"},
                    },
                    "required": ["number", "label", "error_codes", "reason_ko"],
                },
            },
            "reason_ko": {"type": "string"},
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            "needs_human_review": {"type": "boolean"},
        },
        "required": [
            "problem_label",
            "problem_error_codes",
            "choice_labels",
            "reason_ko",
            "confidence",
            "needs_human_review",
        ],
    },
    "strict": True,
}


def call_openai(client: Any, model: str, question: dict[str, Any]) -> dict[str, Any]:
    prompt = build_prompt(question)
    system = (
        "당신은 한국사 객관식 문항 2차 검수 라벨러입니다. "
        "역사 사실성 판단은 제외하고, 형식/노출/선지 품질 오류만 엄격하게 JSON으로 분류합니다."
    )

    try:
        response = client.responses.create(
            model=model,
            input=[
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
            text={"format": {"type": "json_schema", **JSON_SCHEMA}},
        )
        return json.loads(response.output_text)
    except Exception as first_error:
        # 일부 구버전 SDK/환경은 Responses API의 structured output 인자가 다를 수 있어
        # Chat Completions JSON 모드로 한 번 더 시도한다.
        try:
            response = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": system + " 반드시 JSON 객체만 출력하세요."},
                    {"role": "user", "content": prompt},
                ],
                response_format={"type": "json_object"},
            )
            content = response.choices[0].message.content or "{}"
            return json.loads(content)
        except Exception as second_error:
            raise RuntimeError(f"OpenAI call failed: {first_error} / fallback failed: {second_error}") from second_error


def merge_local_and_gpt(local_codes: list[str], local_choices: list[dict[str, Any]], gpt_result: dict[str, Any]) -> dict[str, Any]:
    problem_codes = list(dict.fromkeys(local_codes + list(gpt_result.get("problem_error_codes") or [])))
    gpt_choice_map = {int(item.get("number")): item for item in gpt_result.get("choice_labels") or []}
    merged_choices: list[dict[str, Any]] = []

    for local_choice in local_choices:
        number = int(local_choice["number"])
        gpt_choice = gpt_choice_map.get(number, {})
        codes = list(dict.fromkeys(list(local_choice.get("error_codes") or []) + list(gpt_choice.get("error_codes") or [])))
        label = 0 if codes else int(gpt_choice.get("label", 1))
        reason_parts = [local_choice.get("reason_ko", ""), gpt_choice.get("reason_ko", "")]
        merged_choices.append(
            {
                "number": number,
                "label": label,
                "error_codes": codes,
                "reason_ko": " ".join(part for part in reason_parts if part).strip(),
            }
        )

    if any(code != "NO_CLEAR_ERROR" for code in problem_codes):
        problem_label = 0
    else:
        problem_label = int(gpt_result.get("problem_label", 1))

    return {
        "problem_label": problem_label,
        "problem_error_codes": problem_codes,
        "choice_labels": merged_choices,
        "reason_ko": str(gpt_result.get("reason_ko") or ""),
        "confidence": float(gpt_result.get("confidence") or 0),
        "needs_human_review": bool(gpt_result.get("needs_human_review", True)),
    }


def estimate_cost(questions: list[dict[str, Any]], input_per_1m: float, output_per_1m: float) -> dict[str, Any]:
    total_chars = sum(len(build_prompt(question)) for question in questions)
    estimated_input_tokens = int(total_chars / 2.5)
    estimated_output_tokens = len(questions) * 350
    estimated_cost = (estimated_input_tokens / 1_000_000 * input_per_1m) + (
        estimated_output_tokens / 1_000_000 * output_per_1m
    )
    return {
        "question_count": len(questions),
        "estimated_input_tokens": estimated_input_tokens,
        "estimated_output_tokens": estimated_output_tokens,
        "estimated_cost_usd": round(estimated_cost, 4),
        "note": "간단 추정치입니다. 실제 비용은 토큰화, 출력 길이, 캐시/배치 사용 여부에 따라 달라집니다.",
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="GPT로 팀원 생성 문항의 오류 사유를 자동 라벨링합니다.")
    parser.add_argument("--input", type=Path, action="append", help="입력 JSON 파일. 여러 번 지정 가능")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--summary", type=Path, default=DEFAULT_SUMMARY)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--limit", type=int, default=None, help="테스트용 처리 개수")
    parser.add_argument("--sleep", type=float, default=0.2, help="요청 사이 대기 시간")
    parser.add_argument("--overwrite", action="store_true", help="기존 출력 파일을 지우고 처음부터 실행")
    parser.add_argument("--dry-run", action="store_true", help="API 호출 없이 예상 비용과 대상 개수만 출력")
    parser.add_argument("--input-price", type=float, default=0.25, help="1M input token 가격. 기본값은 gpt-5-mini 기준")
    parser.add_argument("--output-price", type=float, default=2.0, help="1M output token 가격. 기본값은 gpt-5-mini 기준")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    load_dotenv(PROJECT_ROOT / ".env")

    input_files = args.input or DEFAULT_INPUT_FILES
    questions: list[tuple[Path, dict[str, Any]]] = []
    for input_file in input_files:
        data = read_json(input_file)
        questions.extend((input_file, question) for question in extract_questions(data))

    if args.limit is not None:
        questions = questions[: args.limit]

    if args.dry_run:
        print(json.dumps(estimate_cost([question for _, question in questions], args.input_price, args.output_price), ensure_ascii=False, indent=2))
        return

    if args.overwrite and args.output.exists():
        args.output.unlink()

    processed_keys = load_processed_keys(args.output)

    try:
        from openai import OpenAI
    except ImportError as error:
        raise SystemExit("openai 패키지가 필요합니다. 먼저 `pip install openai`를 실행하세요.") from error

    if not os.environ.get("OPENAI_API_KEY"):
        raise SystemExit("OPENAI_API_KEY를 찾지 못했습니다. 프로젝트 루트의 .env 파일을 확인하세요.")

    client = OpenAI()
    started_at = time.time()
    processed = 0
    skipped = 0
    failed = 0

    for source_path, question in questions:
        key = question_key(question, source_path)
        if key in processed_keys:
            skipped += 1
            continue

        local_codes, local_choice_labels = local_rule_labels(question)
        try:
            gpt_result = call_openai(client, args.model, question)
            label_result = merge_local_and_gpt(local_codes, local_choice_labels, gpt_result)
            row = {
                "question_key": key,
                "source_path": str(source_path),
                "model": args.model,
                "seed_id": question.get("seed_id") or question.get("id") or question.get("problem_id"),
                "topic": question.get("topic"),
                "label_result": label_result,
                "input": compact_question(question),
            }
            append_jsonl(args.output, row)
            processed += 1
            print(f"[OK] {processed} processed / skipped {skipped} / failed {failed} :: {key}")
            time.sleep(args.sleep)
        except Exception as error:
            failed += 1
            append_jsonl(
                args.output.with_suffix(".failed.jsonl"),
                {
                    "question_key": key,
                    "source_path": str(source_path),
                    "error": str(error),
                    "input": compact_question(question),
                },
            )
            print(f"[FAIL] {key} :: {error}")

    summary = {
        "model": args.model,
        "input_files": [str(path) for path in input_files],
        "output": str(args.output),
        "total_targets": len(questions),
        "processed": processed,
        "skipped": skipped,
        "failed": failed,
        "elapsed_seconds": round(time.time() - started_at, 2),
    }
    write_json(args.summary, summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
