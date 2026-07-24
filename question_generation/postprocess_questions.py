"""완료된 문제의 선지 해설 생성과 서비스 DB 적재."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from question_generation.generation.material import chat_json
from storage.postgresql.connection import connect_db


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def load_questions(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    questions = data.get("questions") if isinstance(data, dict) else None
    if not isinstance(questions, list) or data.get("count") != len(questions):
        raise ValueError("입력은 count와 questions가 일치하는 최종 문제 JSON이어야 합니다.")

    keys: set[str] = set()
    for question in questions:
        key = str(question.get("variant_key") or "")
        choices = question.get("choices")
        numbers = sorted(choice.get("number") for choice in choices) if isinstance(choices, list) else []
        if not key or key in keys or numbers != [1, 2, 3, 4, 5]:
            raise ValueError(f"문항 식별자 또는 5개 선지 계약이 잘못되었습니다: {key!r}")
        keys.add(key)
    return questions


def load_explanations(path: Path) -> dict[str, dict[str, str]]:
    records: dict[str, dict[str, str]] = {}
    if not path.exists():
        return records
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        record = json.loads(line)
        key = str(record.get("variant_key") or "")
        if not key or key in records:
            raise ValueError(f"{path}:{line_no}의 variant_key가 없거나 중복됩니다.")
        records[key] = validate_choice_explanations(record.get("choice_explanations"))
    return records


def load_classifications(path: Path) -> dict[str, dict[str, str]]:
    required = {
        "service_era",
        "service_topic",
        "service_question_type",
        "service_question_subtype",
    }
    records: dict[str, dict[str, str]] = {}
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        record = json.loads(line)
        key = str(record.get("variant_key") or "")
        values = {name: str(record.get(name) or "").strip() for name in required}
        if not key or key in records or not all(values.values()):
            raise ValueError(f"{path}:{line_no}의 variant_key 또는 서비스 분류가 없거나 중복됩니다.")
        records[key] = values
    return records


def validate_choice_explanations(value: Any) -> dict[str, str]:
    if not isinstance(value, dict) or set(value) != {"1", "2", "3", "4", "5"}:
        raise ValueError("choice_explanations는 1~5번 키를 정확히 가져야 합니다.")
    explanations = {key: str(text).strip() for key, text in value.items()}
    if not all(explanations.values()):
        raise ValueError("빈 선지 해설은 허용되지 않습니다.")
    return explanations


def explanation_messages(question: dict[str, Any]) -> list[dict[str, str]]:
    choices = []
    for choice in sorted(question["choices"], key=lambda item: item["number"]):
        source = choice.get("source") or {}
        owner = str(source.get("owner_label") or "").strip()
        basis = str(source.get("fact_basis") or "").strip()
        if not owner or not basis:
            raise ValueError(f"{question['variant_key']}의 {choice['number']}번 선지 근거가 없습니다.")
        choices.append(
            {
                "number": choice["number"],
                "text": choice.get("text") or "",
                "is_answer": bool(choice.get("is_answer")),
                "fact_owner": owner,
                "fact_basis": basis,
            }
        )

    payload = {
        "material": question.get("material") or "",
        "question": question.get("question") or "",
        "answer_number": question.get("answer_number"),
        "choices": choices,
    }
    return [
        {
            "role": "system",
            "content": (
                "검수된 근거만 사용해 한국사 5지선다의 선지별 짧은 해설을 작성한다. "
                "각 해설은 한 문장으로 쓰고, 정답은 옳은 이유를, 오답은 실제 어느 대상의 사실인지 설명한다. "
                "근거에 없는 사실을 추가하거나 문제를 다시 평가하지 않는다. "
                'JSON 객체 {"choice_explanations":{"1":"...","2":"...","3":"...","4":"...","5":"..."}}만 출력한다.'
            ),
        },
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
    ]


def generate_explanations(args: argparse.Namespace) -> int:
    questions = load_questions(args.input)
    completed = load_explanations(args.output)
    api_key = os.getenv("OPENAI_API_KEY", "")
    if not api_key:
        raise ValueError("OPENAI_API_KEY가 필요합니다.")
    if args.max_attempts < 1:
        raise ValueError("--max-attempts는 1 이상이어야 합니다.")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("a", encoding="utf-8") as output:
        for index, question in enumerate(questions, 1):
            key = question["variant_key"]
            if key in completed:
                continue
            last_error: Exception | None = None
            for _ in range(args.max_attempts):
                try:
                    response = chat_json(
                        base_url=args.base_url,
                        api_key=api_key,
                        model=args.model,
                        messages=explanation_messages(question),
                        temperature=0,
                        timeout=args.timeout,
                        max_retries=0,
                    )
                    explanations = validate_choice_explanations(response.get("choice_explanations"))
                    output.write(
                        json.dumps(
                            {"variant_key": key, "choice_explanations": explanations},
                            ensure_ascii=False,
                        )
                        + "\n"
                    )
                    output.flush()
                    print(json.dumps({"completed": index, "total": len(questions), "variant_key": key}, ensure_ascii=False))
                    break
                except Exception as exc:
                    last_error = exc
            else:
                raise RuntimeError(f"{key} 해설 생성 실패") from last_error
    return 0


def load_major_types(items_dir: Path) -> dict[str, str]:
    major_types: dict[str, str] = {}
    for path in items_dir.glob("*.json"):
        item = json.loads(path.read_text(encoding="utf-8"))
        source = item.get("input") or {}
        key = str(source.get("variant_key") or "")
        major_type = str(source.get("major_type") or "").strip()
        if not key or not major_type or key in major_types:
            raise ValueError(f"{path}의 variant_key 또는 major_type이 없거나 중복됩니다.")
        major_types[key] = major_type
    return major_types


def db_rows(
    question: dict[str, Any],
    question_no: int,
    classification: dict[str, str],
    explanations: dict[str, str],
) -> tuple[tuple[Any, ...], list[tuple[Any, ...]]]:
    key = question["variant_key"]
    answer_no = question.get("answer_number")
    image = question.get("image") or {}
    choices = sorted(question["choices"], key=lambda item: item["number"])
    marked_answers = [choice["number"] for choice in choices if choice.get("is_answer")]
    if marked_answers != [answer_no]:
        raise ValueError(f"{key}의 answer_number와 선지 정답 표시가 다릅니다.")

    required = {
        "question": question.get("question"),
        "topic": question.get("topic"),
    }
    if any(not str(value or "").strip() for value in required.values()):
        raise ValueError(f"{key}의 DB 필수 필드가 비어 있습니다.")

    question_row = (
        key,
        question_no,
        int(question["target_score"]),
        classification["service_era"],
        classification["service_topic"],
        classification["service_question_type"],
        classification["service_question_subtype"],
        question["question"],
        question.get("material") or None,
        image.get("title"),
        image.get("original_image_url"),
        answer_no,
        explanations[str(answer_no)],
        question["topic"],
    )
    option_rows = []
    for choice in choices:
        number = choice["number"]
        text = choice.get("text") or ""
        image_path = choice.get("choice_image_path")
        if not text and not image_path:
            raise ValueError(f"{key}의 {number}번 선지에는 텍스트나 이미지가 필요합니다.")
        option_rows.append((number, text, image_path, number == answer_no, explanations[str(number)]))
    return question_row, option_rows


def import_db(args: argparse.Namespace) -> int:
    questions = load_questions(args.input)
    explanations = load_explanations(args.explanations)
    major_types = load_major_types(args.items_dir)
    classifications = load_classifications(args.classifications)
    keys = {question["variant_key"] for question in questions}
    if keys != set(explanations) or keys != set(major_types) or keys != set(classifications):
        raise ValueError("문항·해설·item·서비스 분류의 variant_key 집합이 정확히 일치해야 합니다.")

    prepared = []
    for number, question in enumerate(questions, 1):
        key = question["variant_key"]
        prepared.append(db_rows(question, number, classifications[key], explanations[key]))

    question_sql = """
        INSERT INTO questions (
            source_key, question_no, q_score, era, topic, question_type, question_subtype,
            content, passage, image_caption, question_image_path, answer_no,
            answer_explanation, core_concept
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING question_id
    """
    classification_sql = """
        UPDATE questions
        SET era = %s, topic = %s, question_type = %s, question_subtype = %s
        WHERE source_key = %s
    """
    option_sql = """
        INSERT INTO question_options (
            question_id, choice_no, content, choice_image_path, is_answer, choice_explanation
        ) VALUES (%s, %s, %s, %s, %s, %s)
    """
    with connect_db() as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT source_key FROM questions WHERE source_key = ANY(%s)", (list(keys),))
            existing = {row[0] for row in cursor.fetchall()}
            inserted = 0
            updated = 0
            if args.dry_run:
                print(
                    json.dumps(
                        {
                            "status": "validated",
                            "count": len(prepared),
                            "existing_updates": len(existing),
                            "new_inserts": len(keys - existing),
                        },
                        ensure_ascii=False,
                    )
                )
                return 0

            for question, (question_row, option_rows) in zip(questions, prepared):
                key = question["variant_key"]
                if key in existing:
                    classification = classifications[key]
                    cursor.execute(
                        classification_sql,
                        (
                            classification["service_era"],
                            classification["service_topic"],
                            classification["service_question_type"],
                            classification["service_question_subtype"],
                            key,
                        ),
                    )
                    if cursor.rowcount != 1:
                        raise RuntimeError(f"{key} 분류 UPDATE 대상이 정확히 1개가 아닙니다.")
                    updated += 1
                    continue

                cursor.execute(question_sql, question_row)
                question_id = cursor.fetchone()[0]
                cursor.executemany(option_sql, [(question_id, *row) for row in option_rows])
                inserted += 1
    print(
        json.dumps(
            {"status": "imported", "count": len(prepared), "updated": updated, "inserted": inserted},
            ensure_ascii=False,
        )
    )
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    explain = subparsers.add_parser("explain", help="선지별 짧은 해설을 JSONL로 생성")
    explain.add_argument("--input", type=Path, required=True)
    explain.add_argument("--output", type=Path, required=True)
    explain.add_argument("--model", default=os.getenv("OPENAI_EXPLANATION_MODEL", ""), required=not os.getenv("OPENAI_EXPLANATION_MODEL"))
    explain.add_argument("--base-url", default=os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1"))
    explain.add_argument("--timeout", type=int, default=60)
    explain.add_argument("--max-attempts", type=int, default=2)
    explain.set_defaults(func=generate_explanations)

    importer = subparsers.add_parser("import-db", help="해설이 완료된 문제를 서비스 DB에 적재")
    importer.add_argument("--input", type=Path, required=True)
    importer.add_argument("--items-dir", type=Path, required=True)
    importer.add_argument("--explanations", type=Path, required=True)
    importer.add_argument("--classifications", type=Path, required=True)
    importer.add_argument("--dry-run", action="store_true")
    importer.set_defaults(func=import_db)

    return parser.parse_args()


def main() -> int:
    load_dotenv(PROJECT_ROOT / ".env")
    args = parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
