"""Create ml_han_features_v2 with standard OpenAI API relabeling.

This script reads ai/ml/ML_han_v1.json, asks a GPT model to classify each
question, stores per-row raw results, and writes v2 feature JSON/CSV files.

Run:
  python ai/ml/gpt_relabel_ml_han_features_v2_standard.py --limit 10

Full run:
  python ai/ml/gpt_relabel_ml_han_features_v2_standard.py

Required environment:
  OPENAI_API_KEY
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import time
import urllib.error
import urllib.request
from collections import Counter
from pathlib import Path
from typing import Any


ROOT_DIR = Path(__file__).resolve().parents[2]
ML_DIR = Path(__file__).resolve().parent

INPUT_JSON = ML_DIR / "ML_han_v1.json"
ENV_FILE = ROOT_DIR / ".env"
OUTPUT_DIR = ML_DIR / "output" / "gpt_relabel_v2"
RAW_JSONL = OUTPUT_DIR / "ml_han_gpt_relabel_v2_raw.jsonl"
FEATURE_JSON = ML_DIR / "output" / "ml_han_features_v2.json"
FEATURE_CSV = ML_DIR / "output" / "ml_han_features_v2.csv"
REPORT_JSON = ML_DIR / "output" / "ml_han_features_v2_report.json"
REPORT_MD = OUTPUT_DIR / "ml_han_features_v2_report.md"

API_URL = "https://api.openai.com/v1/chat/completions"

ERA_VALUES = [
    "선사 시대",
    "고조선",
    "초기 국가",
    "삼국 시대",
    "남북국 시대",
    "고려",
    "조선",
    "개항기",
    "일제 강점기",
    "현대",
]

TOPIC_VALUES = [
    "정치",
    "경제",
    "사회",
    "문화",
    "인물",
    "군사",
    "외교",
    "사상·종교",
    "제도",
    "사건",
]

TOPIC_TRAIN_VALUES = ["문화", "사건", "인물", "정치", "제도"]

QUESTION_TYPES = [
    "역사 지식의 이해",
    "연대기의 파악",
    "역사 상황 및 쟁점의 인식",
    "역사 자료의 분석 및 해석",
    "역사 탐구의 설계 및 수행",
    "결론의 도출 및 평가",
]

QUESTION_SUBTYPES = [
    "기본 사실·개념 확인",
    "자료 기반 시대·대상 추론",
    "사건·자료 순서 배열",
    "연표·흐름 빈칸",
    "전후 시기 판단",
    "지도·지역 위치 판단",
    "시각 자료 해석",
    "제도·기관·정책 기능 이해",
    "탐구 주제·활동 선정",
    "자료 수집·검색 방법",
    "의의·영향·결과 평가",
    "비교·공통점 도출",
    "보기 조합 판단",
]

TOPIC_TRAIN_MAP_V1 = {
    "정치": "정치",
    "경제": "정치",
    "사회": "정치",
    "군사": "정치",
    "외교": "정치",
    "문화": "문화",
    "사상·종교": "문화",
    "인물": "인물",
    "사건": "사건",
    "제도": "제도",
}

FEATURE_COLUMNS = [
    "ml_sequence_index",
    "split",
    "round_no",
    "question_no",
    "problem_id",
    "data_source",
    "input_text",
    "keywords",
    "era",
    "topic",
    "topic_train",
    "topic_train_v1",
    "topic_train_v2",
    "question_type",
    "question_subtype",
    "core_concept",
    "label_confidence",
    "ambiguous_flag",
    "label_reason",
    "review_model",
]


SYSTEM_PROMPT = """당신은 한국사능력검정시험 기출 문항을 ML 학습용으로 라벨링하는 검수자입니다.
문항의 정답을 맞히는 것이 아니라, 문항이 요구하는 역사 지식의 중심 대상을 분류합니다.
항상 지정된 라벨 목록 중 하나만 고르고, reason은 1문장 80자 이내로 짧게 씁니다.
"""


LABEL_CRITERIA = """주제 라벨 기준:
- 문화: 사상, 종교, 학문, 예술, 문화재, 생활 문화가 정답 판단의 중심.
- 사건: 전쟁, 반란, 운동, 조약, 개혁, 선언 등 특정 사건의 원인/전개/결과/순서가 중심.
- 인물: 특정 인물의 업적, 활동, 정책, 발언, 저술 식별이 중심.
- 정치: 권력 구조, 왕권/정권 운영, 통치 체제, 대외 정책 방향이 중심.
- 제도: 법, 행정, 수취, 토지, 신분, 관직, 교육, 군역 등 구조화된 제도 자체가 중심.
- 경제/사회/군사/외교/사상·종교는 원본 세부 topic으로 필요할 때만 사용한다.

topic_train_v2 추천 기준:
- 사건/인물/제도/문화가 명확하면 해당 라벨을 우선한다.
- 경제/사회/군사/외교가 세부 topic이면, 학습용 통합 라벨은 보통 정치로 둔다.
- 사상·종교가 세부 topic이면, 학습용 통합 라벨은 문화로 둔다.
- 한 문항에 여러 요소가 섞이면 정답 판단에 가장 직접 필요한 요소를 기준으로 한다.
"""


JSON_SCHEMA = {
    "name": "han_exam_label_v2",
    "strict": True,
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "era": {"type": "string", "enum": ERA_VALUES},
            "topic": {"type": "string", "enum": TOPIC_VALUES},
            "topic_train_v2": {"type": "string", "enum": TOPIC_TRAIN_VALUES},
            "question_type": {"type": "string", "enum": QUESTION_TYPES},
            "question_subtype": {"type": "string", "enum": QUESTION_SUBTYPES},
            "core_concept": {"type": "string"},
            "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
            "ambiguous": {"type": "boolean"},
            "reason": {"type": "string"},
        },
        "required": [
            "era",
            "topic",
            "topic_train_v2",
            "question_type",
            "question_subtype",
            "core_concept",
            "confidence",
            "ambiguous",
            "reason",
        ],
    },
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Relabel ML_han_v1.json with a GPT model and build v2 features.")
    parser.add_argument("--input-json", type=Path, default=INPUT_JSON)
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_DIR)
    parser.add_argument("--model", default=os.environ.get("OPENAI_MODEL") or "gpt-5.6-terra")
    parser.add_argument("--limit", type=int, default=0, help="Process only the first N rows. 0 means all rows.")
    parser.add_argument("--start-index", type=int, default=0, help="Skip rows before this zero-based index.")
    parser.add_argument("--sleep", type=float, default=0.15, help="Delay between successful requests.")
    parser.add_argument("--max-retries", type=int, default=5)
    parser.add_argument("--rebuild-only", action="store_true", help="Do not call API; rebuild features from raw JSONL.")
    parser.add_argument("--partial-build", action="store_true", help="Allow feature rebuild with only processed rows.")
    parser.add_argument("--force", action="store_true", help="Reprocess rows even if raw result already exists.")
    return parser.parse_args()


def load_dotenv(path: Path = ENV_FILE) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def normalize_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def short_text(value: Any, *, limit: int = 2400) -> str:
    text = normalize_text(value)
    return text[:limit]


def row_key(row: dict[str, Any]) -> str:
    problem_id = normalize_text(row.get("problem_id"))
    if problem_id:
        return problem_id
    return str(row.get("ml_sequence_index") or "")


def split_by_round(round_no: Any) -> str:
    return "train" if int(round_no or 0) <= 70 else "test"


def topic_train_v1(topic: str) -> str:
    return TOPIC_TRAIN_MAP_V1.get(topic, topic)


def load_existing_results(path: Path, *, model: str | None = None) -> dict[str, dict[str, Any]]:
    results: dict[str, dict[str, Any]] = {}
    if not path.exists():
        return results
    with path.open("r", encoding="utf-8") as file:
        for line in file:
            if not line.strip():
                continue
            record = json.loads(line)
            key = normalize_text(record.get("problem_id")) or str(record.get("ml_sequence_index") or "")
            if key and record.get("ok") and (model is None or record.get("model") == model):
                results[key] = record
    return results


def append_jsonl(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as file:
        file.write(json.dumps(record, ensure_ascii=False) + "\n")


def make_user_prompt(row: dict[str, Any]) -> str:
    choices = row.get("choices") or []
    visible_choices = []
    for idx, choice in enumerate(choices, start=1):
        content = normalize_text(choice.get("content") if isinstance(choice, dict) else choice)
        if content:
            visible_choices.append(f"{idx}. {content}")

    return "\n".join(
        [
            LABEL_CRITERIA,
            "",
            "허용 라벨:",
            f"- era: {', '.join(ERA_VALUES)}",
            f"- topic: {', '.join(TOPIC_VALUES)}",
            f"- topic_train_v2: {', '.join(TOPIC_TRAIN_VALUES)}",
            "",
            "문항 정보:",
            f"- problem_id: {normalize_text(row.get('problem_id'))}",
            f"- round_no: {row.get('round_no')}",
            f"- question_no: {row.get('question_no')}",
            f"- 기존 topic_type: {normalize_text(row.get('topic_type'))}",
            f"- 기존 topic 후보: {normalize_text(row.get('topic'))}",
            f"- 기존 major_type: {normalize_text(row.get('major_type'))}",
            f"- 기존 minor_type: {normalize_text(row.get('minor_type'))}",
            f"- question_task: {normalize_text(row.get('question_task'))}",
            "",
            "지문:",
            short_text(row.get("material")),
            "",
            "질문:",
            short_text(row.get("question"), limit=800),
            "",
            "선택지:",
            "\n".join(visible_choices) if visible_choices else "(없음)",
            "",
            "정답 선택지:",
            short_text(row.get("answer_choice"), limit=600),
            "",
            "출력은 스키마에 맞는 JSON만 반환한다.",
        ]
    )


def post_chat_completion(api_key: str, payload: dict[str, Any], *, max_retries: int) -> dict[str, Any]:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    for attempt in range(max_retries + 1):
        request = urllib.request.Request(API_URL, data=data, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            if exc.code in {408, 409, 429, 500, 502, 503, 504} and attempt < max_retries:
                time.sleep(min(30, 2**attempt))
                continue
            raise RuntimeError(f"OpenAI API HTTP {exc.code}: {body}") from exc
        except urllib.error.URLError as exc:
            if attempt < max_retries:
                time.sleep(min(30, 2**attempt))
                continue
            raise RuntimeError(f"OpenAI API network error: {exc}") from exc

    raise RuntimeError("OpenAI API request failed after retries")


def parse_model_json(response: dict[str, Any]) -> dict[str, Any]:
    content = response["choices"][0]["message"]["content"]
    parsed = json.loads(content)
    return parsed


def call_model(row: dict[str, Any], *, model: str, api_key: str, max_retries: int) -> dict[str, Any]:
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": make_user_prompt(row)},
        ],
        "response_format": {
            "type": "json_schema",
            "json_schema": JSON_SCHEMA,
        },
    }
    response = post_chat_completion(api_key, payload, max_retries=max_retries)
    parsed = parse_model_json(response)
    return {
        "ok": True,
        "problem_id": normalize_text(row.get("problem_id")),
        "ml_sequence_index": row.get("ml_sequence_index"),
        "round_no": row.get("round_no"),
        "question_no": row.get("question_no"),
        "model": model,
        "result": parsed,
        "usage": response.get("usage", {}),
        "response_id": response.get("id", ""),
    }


def validate_result(result: dict[str, Any]) -> dict[str, Any]:
    output = dict(result)
    if output.get("era") not in ERA_VALUES:
        raise ValueError(f"invalid era: {output.get('era')}")
    if output.get("topic") not in TOPIC_VALUES:
        raise ValueError(f"invalid topic: {output.get('topic')}")
    if output.get("topic_train_v2") not in TOPIC_TRAIN_VALUES:
        raise ValueError(f"invalid topic_train_v2: {output.get('topic_train_v2')}")
    if output.get("question_type") not in QUESTION_TYPES:
        raise ValueError(f"invalid question_type: {output.get('question_type')}")
    if output.get("question_subtype") not in QUESTION_SUBTYPES:
        raise ValueError(f"invalid question_subtype: {output.get('question_subtype')}")
    output["core_concept"] = normalize_text(output.get("core_concept")) or "미분류"
    output["reason"] = normalize_text(output.get("reason"))[:120]
    output["confidence"] = output.get("confidence") if output.get("confidence") in {"high", "medium", "low"} else "low"
    output["ambiguous"] = bool(output.get("ambiguous"))
    return output


def build_input_text(row: dict[str, Any]) -> str:
    return "\n".join(
        value
        for value in [normalize_text(row.get("material")), normalize_text(row.get("question"))]
        if value
    )


def build_feature_rows(
    source_rows: list[dict[str, Any]],
    raw_results: dict[str, dict[str, Any]],
    *,
    allow_partial: bool = False,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    missing: list[str] = []

    for source in source_rows:
        key = row_key(source)
        record = raw_results.get(key)
        if not record:
            missing.append(key)
            if allow_partial:
                continue
        result = validate_result(record["result"])
        topic = result["topic"]
        topic_train_1 = topic_train_v1(topic)
        topic_train_2 = result["topic_train_v2"]
        keywords = normalize_text(result.get("core_concept"))

        rows.append(
            {
                "ml_sequence_index": int(source.get("ml_sequence_index") or 0),
                "split": split_by_round(source.get("round_no")),
                "round_no": int(source.get("round_no") or 0),
                "question_no": int(source.get("question_no") or 0),
                "problem_id": normalize_text(source.get("problem_id")),
                "data_source": normalize_text(source.get("data_source")),
                "input_text": build_input_text(source),
                "keywords": keywords,
                "era": result["era"],
                "topic": topic,
                "topic_train": topic_train_2,
                "topic_train_v1": topic_train_1,
                "topic_train_v2": topic_train_2,
                "question_type": result["question_type"],
                "question_subtype": result["question_subtype"],
                "core_concept": keywords,
                "label_confidence": result["confidence"],
                "ambiguous_flag": str(result["ambiguous"]),
                "label_reason": result["reason"],
                "review_model": normalize_text(record.get("model")),
            }
        )

    if missing and not allow_partial:
        raise ValueError(f"missing raw relabel results: {len(missing)} rows; first keys: {missing[:10]}")
    return rows


def write_json(path: Path, rows: list[dict[str, Any]]) -> None:
    path.write_text(json.dumps(rows, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=FEATURE_COLUMNS)
        writer.writeheader()
        writer.writerows({field: row.get(field, "") for field in FEATURE_COLUMNS} for row in rows)


def count_by(rows: list[dict[str, Any]], field: str) -> dict[str, int]:
    return dict(Counter(str(row.get(field) or "").strip() for row in rows).most_common())


def write_reports(rows: list[dict[str, Any]], raw_results: dict[str, dict[str, Any]]) -> None:
    usage = Counter()
    for record in raw_results.values():
        for key, value in (record.get("usage") or {}).items():
            if isinstance(value, int):
                usage[key] += value

    report = {
        "input": INPUT_JSON.relative_to(ROOT_DIR).as_posix(),
        "raw_results": RAW_JSONL.relative_to(ROOT_DIR).as_posix(),
        "outputs": {
            "json": FEATURE_JSON.relative_to(ROOT_DIR).as_posix(),
            "csv": FEATURE_CSV.relative_to(ROOT_DIR).as_posix(),
        },
        "total_rows": len(rows),
        "split_counts": count_by(rows, "split"),
        "era_counts": count_by(rows, "era"),
        "topic_counts": count_by(rows, "topic"),
        "topic_train_v1_counts": count_by(rows, "topic_train_v1"),
        "topic_train_v2_counts": count_by(rows, "topic_train_v2"),
        "question_type_counts": count_by(rows, "question_type"),
        "confidence_counts": count_by(rows, "label_confidence"),
        "ambiguous_counts": count_by(rows, "ambiguous_flag"),
        "usage": dict(usage),
    }
    REPORT_JSON.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    lines = [
        "# ML Han Features v2 Report",
        "",
        f"- total rows: {len(rows)}",
        f"- raw results: `{RAW_JSONL.relative_to(ROOT_DIR).as_posix()}`",
        f"- feature json: `{FEATURE_JSON.relative_to(ROOT_DIR).as_posix()}`",
        f"- feature csv: `{FEATURE_CSV.relative_to(ROOT_DIR).as_posix()}`",
        "",
        "## Topic Train v2 Counts",
        "",
        "| label | count |",
        "|---|---:|",
    ]
    for label, count in report["topic_train_v2_counts"].items():
        lines.append(f"| {label} | {count} |")
    lines.extend(["", "## Confidence Counts", "", "| label | count |", "|---|---:|"])
    for label, count in report["confidence_counts"].items():
        lines.append(f"| {label} | {count} |")
    REPORT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")


def process_rows(args: argparse.Namespace) -> None:
    api_key = os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not set")

    source_rows = read_json(args.input_json)
    end_index = len(source_rows) if args.limit <= 0 else min(len(source_rows), args.start_index + args.limit)
    target_rows = source_rows[args.start_index:end_index]
    existing = load_existing_results(RAW_JSONL, model=args.model)

    for offset, row in enumerate(target_rows, start=args.start_index):
        key = row_key(row)
        if key in existing and not args.force:
            print(f"skip existing {offset + 1}/{len(source_rows)} {key}")
            continue
        print(f"relabel {offset + 1}/{len(source_rows)} {key}")
        try:
            record = call_model(row, model=args.model, api_key=api_key, max_retries=args.max_retries)
            validate_result(record["result"])
        except Exception as exc:  # noqa: BLE001 - keep batch progress even on one bad row.
            record = {
                "ok": False,
                "problem_id": normalize_text(row.get("problem_id")),
                "ml_sequence_index": row.get("ml_sequence_index"),
                "round_no": row.get("round_no"),
                "question_no": row.get("question_no"),
                "model": args.model,
                "error": str(exc),
            }
            append_jsonl(RAW_JSONL, record)
            raise
        append_jsonl(RAW_JSONL, record)
        time.sleep(args.sleep)


def rebuild_features(args: argparse.Namespace) -> None:
    source_rows = read_json(args.input_json)
    raw_results = load_existing_results(RAW_JSONL, model=args.model)
    if len(raw_results) < len(source_rows) and not args.partial_build:
        print(
            json.dumps(
                {
                    "status": "raw_results_incomplete",
                    "source_rows": len(source_rows),
                    "raw_results": len(raw_results),
                    "message": "Feature files were not rebuilt. Run full relabeling or pass --partial-build.",
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return

    rows = build_feature_rows(source_rows, raw_results, allow_partial=args.partial_build)
    FEATURE_JSON.parent.mkdir(parents=True, exist_ok=True)
    write_json(FEATURE_JSON, rows)
    write_csv(FEATURE_CSV, rows)
    write_reports(rows, raw_results)
    print(
        json.dumps(
            {
                "rows": len(rows),
                "feature_json": FEATURE_JSON.relative_to(ROOT_DIR).as_posix(),
                "feature_csv": FEATURE_CSV.relative_to(ROOT_DIR).as_posix(),
                "report": REPORT_JSON.relative_to(ROOT_DIR).as_posix(),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


def main() -> None:
    load_dotenv()
    args = parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    if not args.rebuild_only:
        process_rows(args)
    rebuild_features(args)


if __name__ == "__main__":
    main()
