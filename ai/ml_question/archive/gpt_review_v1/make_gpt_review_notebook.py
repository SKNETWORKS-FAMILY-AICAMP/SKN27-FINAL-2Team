from __future__ import annotations

import json
from pathlib import Path


OUT = Path(__file__).resolve().parent / "gpt_review_questions.ipynb"


def md(source: str) -> dict:
    return {
        "cell_type": "markdown",
        "metadata": {},
        "source": source.strip("\n").splitlines(keepends=True),
    }


def code(source: str) -> dict:
    return {
        "cell_type": "code",
        "execution_count": None,
        "metadata": {},
        "outputs": [],
        "source": source.strip("\n").splitlines(keepends=True),
    }


cells = [
    md(
        """
# GPT 기반 문제 2차 검수

이 노트북은 문제 1개를 GPT API에 한 번 보내고, 내부에서 5개 선지를 모두 평가한다.

- 입력: 지문 + 질문 + 선지 5개 + 표시 정답
- 출력: 정상/이상 label, 오류 코드, 선지별 검수 결과
- 기본 모델: `gpt-5-mini`
"""
    ),
    md("## 1. 라이브러리 설치"),
    code("!pip install -q openai python-dotenv pandas tqdm"),
    md("## 2. 기본 설정"),
    code(
        """
from __future__ import annotations

import csv
import json
import os
import re
import time
from pathlib import Path
from statistics import mean
from typing import Any

import pandas as pd
from dotenv import load_dotenv
from openai import OpenAI
from tqdm.auto import tqdm


WORKSPACE_DIR = Path.cwd()
DATA_DIR = WORKSPACE_DIR / "common"
INPUT_JSON = DATA_DIR / "generated_questions.json"
OUTPUT_DIR = WORKSPACE_DIR / "gpt_review_output"

MODEL_NAME = "gpt-5-mini"

# 처음에는 비용 확인을 위해 20개만 돌린다. 전체 실행하려면 None으로 바꾼다.
REVIEW_LIMIT: int | None = 20

# API 호출 전 입력/출력 예상 비용만 보고 싶으면 True로 둔다.
DRY_RUN = False

# 너무 긴 지문은 비용과 응답 안정성을 위해 잘라낸다.
MAX_PASSAGE_CHARS = 2500
MAX_QUESTION_CHARS = 700
MAX_CHOICE_CHARS = 500

# GPT가 사용할 최대 출력 토큰이다. 오류 이유까지 받으므로 너무 낮게 잡지 않는다.
MAX_OUTPUT_TOKENS = 1800

# 현재 공식 가격 기준: gpt-5-mini, text token, standard API.
PRICE_PER_1M_INPUT = 0.25
PRICE_PER_1M_OUTPUT = 2.00

OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# .env 위치를 여러 곳에서 찾는다.
for env_path in [
    WORKSPACE_DIR / ".env",
    DATA_DIR / ".env",
    WORKSPACE_DIR.parent / ".env",
]:
    if env_path.exists():
        load_dotenv(env_path)

load_dotenv()

if not os.getenv("OPENAI_API_KEY"):
    raise ValueError(
        "OPENAI_API_KEY가 없습니다. /workspace/.env 또는 /workspace/common/.env에 OPENAI_API_KEY=... 형태로 넣어주세요."
    )

client = OpenAI()

print("WORKSPACE_DIR:", WORKSPACE_DIR)
print("INPUT_JSON:", INPUT_JSON)
print("INPUT_JSON exists:", INPUT_JSON.exists())
print("OUTPUT_DIR:", OUTPUT_DIR)
print("MODEL_NAME:", MODEL_NAME)
print("REVIEW_LIMIT:", REVIEW_LIMIT)
print("DRY_RUN:", DRY_RUN)
"""
    ),
    md("## 3. 데이터 로드 및 기본 검사"),
    code(
        """
def read_json(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        raise FileNotFoundError(f"파일이 없습니다: {path}")
    rows = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(rows, list):
        raise ValueError("입력 JSON은 list 형태여야 합니다.")
    return rows


def normalize_answer(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        answer = int(value)
    except Exception:
        return None
    if 1 <= answer <= 5:
        return answer
    return None


def validate_question(row: dict[str, Any], idx: int) -> list[dict[str, str]]:
    errors: list[dict[str, str]] = []
    choices = row.get("choices")
    if not isinstance(choices, list) or len(choices) != 5:
        errors.append({"type": "CHOICE_COUNT_ERROR", "message": "선지가 정확히 5개가 아닙니다."})

    answer = normalize_answer(row.get("answer"))
    if answer is None:
        errors.append({"type": "ANSWER_FORMAT_ERROR", "message": "표시 정답이 1~5 범위의 숫자가 아닙니다."})

    if not str(row.get("question", "")).strip():
        errors.append({"type": "QUESTION_MISSING", "message": "질문이 비어 있습니다."})

    if not str(row.get("passage", "")).strip():
        errors.append({"type": "PASSAGE_MISSING", "message": "지문이 비어 있습니다."})

    return errors


rows = read_json(INPUT_JSON)
if REVIEW_LIMIT is not None:
    rows = rows[:REVIEW_LIMIT]

print("review rows:", len(rows))
print("sample keys:", rows[0].keys() if rows else None)
print("sample:", rows[0] if rows else None)

local_errors = []
for idx, row in enumerate(rows, start=1):
    errors = validate_question(row, idx)
    if errors:
        local_errors.append({"idx": idx, "id": row.get("id") or row.get("question_id"), "errors": errors})

print("local validation errors:", len(local_errors))
if local_errors[:3]:
    print(json.dumps(local_errors[:3], ensure_ascii=False, indent=2))
"""
    ),
    md("## 4. GPT 출력 스키마"),
    code(
        """
REVIEW_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "final_label": {
            "type": "integer",
            "enum": [0, 1],
            "description": "0=이상 있음, 1=이상 없음",
        },
        "final_error_codes": {
            "type": "array",
            "items": {
                "type": "string",
                "enum": [
                    "NO_ANSWER_CANDIDATE",
                    "MULTIPLE_ANSWER_CANDIDATES",
                    "ANSWER_KEY_MISMATCH",
                    "ANSWER_IN_PASSAGE",
                    "ANSWER_LENGTH_BIAS",
                    "CHOICE_COUNT_ERROR",
                    "ANSWER_FORMAT_ERROR",
                    "QUESTION_OR_PASSAGE_ERROR",
                    "UNCERTAIN_REVIEW",
                ],
            },
        },
        "answer_candidates": {
            "type": "array",
            "items": {"type": "integer", "enum": [1, 2, 3, 4, 5]},
        },
        "predicted_answer": {
            "type": ["integer", "null"],
            "enum": [1, 2, 3, 4, 5, None],
        },
        "confidence": {
            "type": "number",
            "minimum": 0,
            "maximum": 1,
        },
        "summary": {
            "type": "string",
            "description": "최종 판단 이유를 짧게 작성",
        },
        "choice_reviews": {
            "type": "array",
            "minItems": 5,
            "maxItems": 5,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "choice_no": {"type": "integer", "enum": [1, 2, 3, 4, 5]},
                    "is_answer_candidate": {"type": "boolean"},
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                    "reason": {"type": "string"},
                    "error_codes": {
                        "type": "array",
                        "items": {
                            "type": "string",
                            "enum": [
                                "ANSWER_IN_PASSAGE",
                                "ANSWER_LENGTH_BIAS",
                                "FACTUAL_CONFLICT_IN_CONTEXT",
                                "OFF_TOPIC",
                                "TOO_WEIRD_DISTRACTOR",
                                "UNCERTAIN_REVIEW",
                            ],
                        },
                    },
                },
                "required": ["choice_no", "is_answer_candidate", "confidence", "reason", "error_codes"],
            },
        },
    },
    "required": [
        "final_label",
        "final_error_codes",
        "answer_candidates",
        "predicted_answer",
        "confidence",
        "summary",
        "choice_reviews",
    ],
}


ERROR_TYPE_KO = {
    "NO_ANSWER_CANDIDATE": "정답 후보 없음",
    "MULTIPLE_ANSWER_CANDIDATES": "복수 정답 후보",
    "ANSWER_KEY_MISMATCH": "표시 정답 불일치",
    "ANSWER_IN_PASSAGE": "정답 지문/질문 포함",
    "ANSWER_LENGTH_BIAS": "정답 길이 편향",
    "CHOICE_COUNT_ERROR": "선지 개수 오류",
    "ANSWER_FORMAT_ERROR": "정답 형식 오류",
    "QUESTION_OR_PASSAGE_ERROR": "질문/지문 오류",
    "UNCERTAIN_REVIEW": "판단 불확실",
}
"""
    ),
    md("## 5. 프롬프트 및 규칙"),
    code(
        """
DEVELOPER_PROMPT = \"\"\"당신은 한국사 객관식 문제 2차 검수자입니다.
목표는 생성된 문제가 정상적으로 출제되었는지 보조 검수하는 것입니다.

판단 기준:
1. 지문과 질문 조건에서 정답 후보가 정확히 1개면 정상 가능성이 높습니다.
2. 정답 후보가 0개면 이상 문제입니다.
3. 정답 후보가 2개 이상이면 이상 문제입니다.
4. 표시 정답(answer)과 실제 정답 후보가 다르면 이상 문제입니다.
5. 정답 선지가 지문 또는 질문에 그대로 포함되어 정답이 노출되면 이상 문제입니다.
6. 정답 선지가 다른 선지에 비해 유독 길거나 짧아 힌트가 되면 이상 문제입니다.
7. 선지가 역사적으로 사실이어도, 현재 지문/질문 조건을 만족하지 않으면 정답 후보가 아닙니다.
8. 세부 역사 사실 확인이 불확실하면 단정하지 말고 UNCERTAIN_REVIEW를 포함합니다.

출력은 반드시 제공된 JSON schema를 따르세요.
reason은 짧고 실무자가 확인하기 쉽게 작성하세요.
\"\"\"


def truncate_text(text: Any, max_chars: int) -> str:
    value = str(text or "").strip()
    if len(value) <= max_chars:
        return value
    return value[:max_chars] + "...[TRUNCATED]"


def normalize_text(text: Any) -> str:
    return re.sub(r"\\s+", "", str(text or "")).lower()


def check_answer_in_passage_rule(row: dict[str, Any]) -> bool:
    choices = row.get("choices") or []
    answer = normalize_answer(row.get("answer"))
    if answer is None or len(choices) != 5:
        return False
    answer_text = normalize_text(choices[answer - 1])
    body = normalize_text(str(row.get("passage", "")) + " " + str(row.get("question", "")))
    return bool(answer_text and answer_text in body)


def check_answer_length_bias_rule(row: dict[str, Any], ratio_threshold: float = 1.5, diff_threshold: int = 12) -> bool:
    choices = row.get("choices") or []
    answer = normalize_answer(row.get("answer"))
    if answer is None or len(choices) != 5:
        return False
    lengths = [len(str(choice)) for choice in choices]
    answer_len = lengths[answer - 1]
    other_lengths = [length for i, length in enumerate(lengths, start=1) if i != answer]
    avg_other = mean(other_lengths)
    return (
        answer_len >= avg_other * ratio_threshold and answer_len - avg_other >= diff_threshold
    ) or (
        answer_len * ratio_threshold <= avg_other and avg_other - answer_len >= diff_threshold
    )


def build_user_prompt(row: dict[str, Any]) -> str:
    choices = row.get("choices") or []
    choice_lines = []
    for idx in range(5):
        text = choices[idx] if idx < len(choices) else ""
        choice_lines.append(f"{idx + 1}. {truncate_text(text, MAX_CHOICE_CHARS)}")

    answer = row.get("answer", "")
    rule_hints = {
        "answer_in_passage_rule": check_answer_in_passage_rule(row),
        "answer_length_bias_rule": check_answer_length_bias_rule(row),
    }

    return f\"\"\"다음 한국사 객관식 문제를 검수하세요.

[문항 ID]
{row.get("id") or row.get("question_id") or ""}

[지문]
{truncate_text(row.get("passage", ""), MAX_PASSAGE_CHARS)}

[질문]
{truncate_text(row.get("question", ""), MAX_QUESTION_CHARS)}

[선지]
{chr(10).join(choice_lines)}

[표시 정답]
{answer}

[규칙 기반 참고값]
{json.dumps(rule_hints, ensure_ascii=False)}

[해야 할 일]
1. 5개 선지를 각각 검토하여 현재 지문/질문 조건에서 정답 후보인지 판단하세요.
2. 정답 후보 개수를 기준으로 final_label과 final_error_codes를 정하세요.
3. 표시 정답과 실제 정답 후보가 다르면 ANSWER_KEY_MISMATCH를 포함하세요.
4. 정답 노출/길이 편향 참고값이 true이면 해당 오류 코드를 포함할지 검토하세요.
\"\"\"
"""
    ),
    md("## 6. API 호출 함수"),
    code(
        """
def call_gpt_review(row: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    response = client.responses.create(
        model=MODEL_NAME,
        input=[
            {"role": "developer", "content": DEVELOPER_PROMPT},
            {"role": "user", "content": build_user_prompt(row)},
        ],
        text={
            "format": {
                "type": "json_schema",
                "name": "question_review",
                "schema": REVIEW_SCHEMA,
                "strict": True,
            },
            "verbosity": "low",
        },
        reasoning={"effort": "low"},
        max_output_tokens=MAX_OUTPUT_TOKENS,
    )
    parsed = json.loads(response.output_text)
    usage = response.usage.model_dump() if response.usage else {}
    return parsed, usage


def estimate_cost_from_usage(usage: dict[str, Any]) -> float:
    input_tokens = int(usage.get("input_tokens") or 0)
    output_tokens = int(usage.get("output_tokens") or 0)
    return (input_tokens / 1_000_000) * PRICE_PER_1M_INPUT + (output_tokens / 1_000_000) * PRICE_PER_1M_OUTPUT


def rough_token_estimate(text: str) -> int:
    # 한국어는 정확한 토큰 계산이 어렵기 때문에 비용 사전 확인용 대략치로만 사용한다.
    return max(1, int(len(text) / 1.7))


def estimate_before_run(rows: list[dict[str, Any]]) -> dict[str, float]:
    input_tokens = 0
    for row in rows:
        input_tokens += rough_token_estimate(DEVELOPER_PROMPT)
        input_tokens += rough_token_estimate(build_user_prompt(row))

    # 구조화 출력이므로 문제당 500~900 output token 정도로 보수적으로 잡는다.
    low_output_tokens = len(rows) * 500
    high_output_tokens = len(rows) * 900

    low_cost = (input_tokens / 1_000_000) * PRICE_PER_1M_INPUT + (low_output_tokens / 1_000_000) * PRICE_PER_1M_OUTPUT
    high_cost = (input_tokens / 1_000_000) * PRICE_PER_1M_INPUT + (high_output_tokens / 1_000_000) * PRICE_PER_1M_OUTPUT

    return {
        "question_count": len(rows),
        "rough_input_tokens": input_tokens,
        "rough_output_tokens_low": low_output_tokens,
        "rough_output_tokens_high": high_output_tokens,
        "estimated_cost_usd_low": round(low_cost, 4),
        "estimated_cost_usd_high": round(high_cost, 4),
        "estimated_cost_krw_low_rough": round(low_cost * 1400),
        "estimated_cost_krw_high_rough": round(high_cost * 1400),
    }


estimate = estimate_before_run(rows)
print(json.dumps(estimate, ensure_ascii=False, indent=2))
"""
    ),
    md("## 7. 검수 실행"),
    code(
        """
def merge_local_validation_result(row: dict[str, Any], errors: list[dict[str, str]]) -> dict[str, Any]:
    error_codes = [error["type"] for error in errors]
    return {
        "id": row.get("id") or row.get("question_id"),
        "final_label": 0,
        "final_error_codes": error_codes,
        "final_error_labels": [ERROR_TYPE_KO.get(code, code) for code in error_codes],
        "answer_candidates": [],
        "predicted_answer": None,
        "confidence": 1.0,
        "summary": "기본 형식 검사에서 오류가 발견되어 GPT 검수를 생략했습니다.",
        "choice_reviews": [],
        "usage": {},
        "cost_usd": 0.0,
        "source": "local_validation",
        "original": row,
    }


def review_one(row: dict[str, Any], idx: int) -> dict[str, Any]:
    errors = validate_question(row, idx)
    if errors:
        return merge_local_validation_result(row, errors)

    parsed, usage = call_gpt_review(row)
    cost_usd = estimate_cost_from_usage(usage)
    error_codes = parsed.get("final_error_codes", [])

    return {
        "id": row.get("id") or row.get("question_id"),
        "final_label": parsed["final_label"],
        "final_error_codes": error_codes,
        "final_error_labels": [ERROR_TYPE_KO.get(code, code) for code in error_codes],
        "answer_candidates": parsed["answer_candidates"],
        "predicted_answer": parsed["predicted_answer"],
        "confidence": parsed["confidence"],
        "summary": parsed["summary"],
        "choice_reviews": parsed["choice_reviews"],
        "usage": usage,
        "cost_usd": round(cost_usd, 6),
        "source": "gpt",
        "original": row,
    }


results: list[dict[str, Any]] = []
total_cost = 0.0

if DRY_RUN:
    print("DRY_RUN=True 이므로 API 호출을 하지 않습니다.")
else:
    for idx, row in enumerate(tqdm(rows, desc="GPT review"), start=1):
        try:
            result = review_one(row, idx)
        except Exception as exc:
            result = {
                "id": row.get("id") or row.get("question_id"),
                "final_label": 0,
                "final_error_codes": ["UNCERTAIN_REVIEW"],
                "final_error_labels": [ERROR_TYPE_KO["UNCERTAIN_REVIEW"]],
                "answer_candidates": [],
                "predicted_answer": None,
                "confidence": 0.0,
                "summary": f"API 호출 또는 파싱 오류: {type(exc).__name__}: {exc}",
                "choice_reviews": [],
                "usage": {},
                "cost_usd": 0.0,
                "source": "error",
                "original": row,
            }
        total_cost += float(result.get("cost_usd") or 0.0)
        results.append(result)
        time.sleep(0.05)

    print("done:", len(results))
    print("actual cost usd:", round(total_cost, 6))
    print("actual cost krw rough:", round(total_cost * 1400))
    print("label counts:", pd.Series([r["final_label"] for r in results]).value_counts().to_dict())
"""
    ),
    md("## 8. 결과 저장"),
    code(
        """
def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\\n", encoding="utf-8")


def write_summary_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "id",
                "final_label",
                "final_error_codes",
                "final_error_labels",
                "answer",
                "predicted_answer",
                "answer_candidates",
                "confidence",
                "cost_usd",
                "summary",
                "question",
            ],
        )
        writer.writeheader()
        for result in rows:
            original = result.get("original", {})
            writer.writerow(
                {
                    "id": result.get("id"),
                    "final_label": result.get("final_label"),
                    "final_error_codes": "|".join(result.get("final_error_codes", [])),
                    "final_error_labels": "|".join(result.get("final_error_labels", [])),
                    "answer": original.get("answer"),
                    "predicted_answer": result.get("predicted_answer"),
                    "answer_candidates": "|".join(map(str, result.get("answer_candidates", []))),
                    "confidence": result.get("confidence"),
                    "cost_usd": result.get("cost_usd"),
                    "summary": result.get("summary"),
                    "question": original.get("question", ""),
                }
            )


if DRY_RUN:
    write_json(OUTPUT_DIR / "cost_estimate.json", estimate)
    print("비용 예상 저장:", OUTPUT_DIR / "cost_estimate.json")
else:
    summary = {
        "model_name": MODEL_NAME,
        "count": len(results),
        "review_limit": REVIEW_LIMIT,
        "estimated": estimate,
        "actual_cost_usd": round(sum(float(r.get("cost_usd") or 0.0) for r in results), 6),
        "actual_cost_krw_rough": round(sum(float(r.get("cost_usd") or 0.0) for r in results) * 1400),
        "label_counts": pd.Series([r["final_label"] for r in results]).value_counts().to_dict() if results else {},
        "error_code_counts": pd.Series(
            [code for r in results for code in r.get("final_error_codes", [])]
        ).value_counts().to_dict() if results else {},
    }
    write_json(OUTPUT_DIR / "gpt_review_results.json", results)
    write_json(OUTPUT_DIR / "gpt_review_summary.json", summary)
    write_summary_csv(OUTPUT_DIR / "gpt_review_summary.csv", results)

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print("결과 JSON:", OUTPUT_DIR / "gpt_review_results.json")
    print("요약 JSON:", OUTPUT_DIR / "gpt_review_summary.json")
    print("요약 CSV:", OUTPUT_DIR / "gpt_review_summary.csv")
"""
    ),
    md("## 9. 결과 미리보기"),
    code(
        """
if not DRY_RUN and results:
    preview_rows = []
    for result in results[:10]:
        original = result.get("original", {})
        preview_rows.append(
            {
                "id": result.get("id"),
                "label": result.get("final_label"),
                "errors": "|".join(result.get("final_error_labels", [])),
                "answer": original.get("answer"),
                "predicted": result.get("predicted_answer"),
                "candidates": result.get("answer_candidates"),
                "confidence": result.get("confidence"),
                "summary": result.get("summary"),
            }
        )
    display(pd.DataFrame(preview_rows))
else:
    print("표시할 결과가 없습니다.")
"""
    ),
]


nb = {
    "cells": cells,
    "metadata": {
        "kernelspec": {
            "display_name": "Python 3 (ipykernel)",
            "language": "python",
            "name": "python3",
        },
        "language_info": {
            "name": "python",
            "version": "3.12.0",
        },
    },
    "nbformat": 4,
    "nbformat_minor": 5,
}

OUT.write_text(json.dumps(nb, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(f"created: {OUT}")
