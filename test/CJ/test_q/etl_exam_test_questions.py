"""
Build test question data from Korean History exam PDFs.

Default target rounds: 76 and 77.

Examples:
  python test/CJ/test_q/etl_exam_test_questions.py --vision
  python test/CJ/test_q/etl_exam_test_questions.py --answers
  python test/CJ/test_q/etl_exam_test_questions.py --explanations
  python test/CJ/test_q/etl_exam_test_questions.py --classify
  python test/CJ/test_q/etl_exam_test_questions.py --import-db
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import time
from io import BytesIO
from pathlib import Path
from typing import Any

import pypdfium2 as pdfium


ROOT_DIR = Path(__file__).resolve().parents[3]
DOCS_DIR = ROOT_DIR / "test" / "CJ" / "test_docs"
OUT_DIR = ROOT_DIR / "test" / "CJ" / "test_q" / "output_exam"
SCHEMA_ALTER_SQL = ROOT_DIR / "storage" / "postgresql" / "schema" / "alter_apply_latest.sql"

DEFAULT_ROUNDS = [76, 77]
QUESTION_COUNT = 50

QUESTION_TYPES = [
    "역사 지식의 이해",
    "연대기의 파악",
    "역사 상황 및 쟁점의 인식",
    "역사 자료의 분석 및 해석",
    "역사 탐구의 설계 및 수행",
    "결론의 도출 및 평가",
]

QUESTION_SUBTYPES = ["개념", "인물", "사료", "연표", "지역"]

ERA_VALUES = [
    "선사 시대",
    "고조선",
    "여러 나라",
    "삼국 시대",
    "남북국 시대",
    "고려",
    "조선 전기",
    "조선 후기",
    "개항기",
    "일제 강점기",
    "현대",
    "통합 주제",
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
    "지역",
    "통합",
]


def load_dotenv_file(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def find_pdf(round_no: int, keyword: str) -> Path:
    matches = sorted(
        path for path in DOCS_DIR.glob("*.pdf")
        if str(round_no) in path.name and keyword in path.name
    )
    if not matches:
        raise FileNotFoundError(f"{round_no}??{keyword} PDF??筌≪뼚??????곷뮸??덈뼄.")
    return matches[0]


def round_output_dir(round_no: int) -> Path:
    return OUT_DIR / f"round_{round_no}"


def page_question_numbers(page_no: int) -> dict[str, list[int]]:
    layout = {
        1: {"left": [1, 2], "right": [3, 4]},
        2: {"left": [5, 6], "right": [7, 8]},
        3: {"left": [9, 10], "right": [11, 12]},
        4: {"left": [13, 14], "right": [15, 16]},
        5: {"left": [17, 18], "right": [19, 20]},
        6: {"left": [21, 22, 23], "right": [24, 25]},
        7: {"left": [26, 27], "right": [28, 29]},
        8: {"left": [30, 31], "right": [32, 33, 34]},
        9: {"left": [35, 36], "right": [37, 38]},
        10: {"left": [39, 40], "right": [41, 42]},
        11: {"left": [43, 44], "right": [45, 46]},
        12: {"left": [47, 48], "right": [49, 50]},
    }
    if page_no not in layout:
        raise ValueError(f"unexpected page_no: {page_no}")
    return layout[page_no]


def render_pdf_page(pdf_path: Path, page_index: int, scale: float = 2.0) -> bytes:
    pdf = pdfium.PdfDocument(str(pdf_path))
    page = pdf[page_index]
    image = page.render(scale=scale).to_pil().convert("RGB")
    buf = BytesIO()
    image.save(buf, format="PNG")
    return buf.getvalue()


def crop_question_images(round_no: int, scale: float = 2.0) -> dict[int, str]:
    question_pdf = find_pdf(round_no, "문제지")
    image_dir = round_output_dir(round_no) / "question_images"
    image_dir.mkdir(parents=True, exist_ok=True)

    pdf = pdfium.PdfDocument(str(question_pdf))
    results: dict[int, str] = {}

    for page_index in range(len(pdf)):
        page_no = page_index + 1
        page = pdf[page_index]
        image = page.render(scale=scale).to_pil().convert("RGB")
        width, height = image.size

        top = int(height * (0.135 if page_no == 1 else 0.075))
        bottom = int(height * 0.94)
        gutter_left = int(width * 0.495)
        gutter_right = int(width * 0.505)
        margin_x = int(width * 0.055)
        left_box = (margin_x, top, gutter_left, bottom)
        right_box = (gutter_right, top, int(width * 0.945), bottom)

        for side, numbers in page_question_numbers(page_no).items():
            box = left_box if side == "left" else right_box
            column = image.crop(box)
            col_width, col_height = column.size
            block_height = col_height // len(numbers)

            for idx, q_no in enumerate(numbers):
                path = image_dir / f"q_{q_no:03d}.png"
                if not path.exists():
                    y1 = max(0, idx * block_height - int(col_height * 0.015))
                    y2 = col_height if idx == len(numbers) - 1 else (idx + 1) * block_height + int(col_height * 0.035)
                    column.crop((0, y1, col_width, y2)).save(path)
                results[q_no] = str(path.relative_to(ROOT_DIR)).replace("\\", "/")

    write_json(round_output_dir(round_no) / f"question_images_{round_no}.json", results)
    return dict(sorted(results.items()))


def openai_json_from_image(prompt: str, image_bytes: bytes) -> dict[str, Any]:
    try:
        from openai import OpenAI
    except ImportError as exc:
        raise RuntimeError("openai package is required for vision extraction") from exc
    if not os.getenv("OPENAI_API_KEY"):
        raise RuntimeError("OPENAI_API_KEY is required for vision extraction")

    client = OpenAI()
    b64 = base64.b64encode(image_bytes).decode("utf-8")
    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/png;base64,{b64}",
                            "detail": "high",
                        },
                    },
                ],
            }
        ],
        response_format={"type": "json_object"},
        temperature=0,
    )
    return json.loads(response.choices[0].message.content or "{}")


def normalize_choice(choice: dict[str, Any]) -> dict[str, Any] | None:
    raw_no = str(choice.get("choice_no", "")).strip()
    if not raw_no.isdigit():
        return None
    choice_no = int(raw_no)
    if choice_no < 1 or choice_no > 5:
        return None
    return {
        "choice_no": choice_no,
        "content": str(choice.get("content", "")).strip(),
    }


def normalize_question_item(item: dict[str, Any]) -> dict[str, Any]:
    choices = []
    for choice in item.get("choices") or []:
        normalized = normalize_choice(choice)
        if normalized:
            choices.append(normalized)
    choices = sorted(choices, key=lambda row: row["choice_no"])
    return {
        "content": str(item.get("content", "")).strip(),
        "passage": str(item.get("passage", "")).strip(),
        "image_caption": str(item.get("image_caption", "")).strip(),
        "choices": choices,
    }


def extract_questions_with_vision(round_no: int, image_paths: dict[int, str], limit: int | None = None) -> dict[int, dict[str, Any]]:
    output_path = round_output_dir(round_no) / f"vision_questions_{round_no}.json"
    errors_path = round_output_dir(round_no) / f"vision_question_errors_{round_no}.json"
    extracted = {int(k): v for k, v in read_json(output_path, {}).items()}
    errors = {int(k): str(v) for k, v in read_json(errors_path, {}).items()}

    prompt = """
한국사능력검정시험 심화 문항 이미지에서 문제 정보를 JSON으로 추출하세요.
이미지는 실제 경로로 저장하지 않고, 지문과 선택지를 모두 텍스트 캡션으로 대체합니다.
반드시 JSON 객체만 반환하세요.

형식:
{
  "content": "문제 발문",
  "passage": "문제에 포함된 지문, 사료, 그림, 사진, 지도, 도표, 대화문을 자연어로 설명한 내용. 이미지 자료도 여기에서 글로 설명",
  "image_caption": "이미지를 보고 문제 풀이에 필요한 핵심 시각 단서, 시대/주제/키워드 요약",
  "choices": [
    {"choice_no": 1, "content": "선택지 텍스트 또는 선택지 이미지의 자연어 설명"},
    {"choice_no": 2, "content": "선택지 텍스트 또는 선택지 이미지의 자연어 설명"},
    {"choice_no": 3, "content": "선택지 텍스트 또는 선택지 이미지의 자연어 설명"},
    {"choice_no": 4, "content": "선택지 텍스트 또는 선택지 이미지의 자연어 설명"},
    {"choice_no": 5, "content": "선택지 텍스트 또는 선택지 이미지의 자연어 설명"}
  ]
}

주의:
- 선택지 번호 기호는 content에 넣지 말고 choice_no로 분리하세요.
- 이미지 지문은 passage에 글로 풀어 쓰세요.
- 이미지 선택지는 choices.content에 유물명, 자료명, 특징을 글로 설명하세요.
- 좌표, 이미지 경로, image_path, bbox, crop 정보는 절대 반환하지 마세요.
- image_caption은 정답을 직접 말하지 말고 문제 풀이에 필요한 시각 단서만 설명하세요.
"""

    processed = 0
    for q_no, rel_path in image_paths.items():
        if q_no in extracted and extracted[q_no].get("content"):
            continue
        if limit is not None and processed >= limit:
            break

        try:
            item = openai_json_from_image(prompt, (ROOT_DIR / rel_path).read_bytes())
            extracted[q_no] = normalize_question_item(item)
            errors.pop(q_no, None)
        except Exception as exc:
            errors[q_no] = f"{type(exc).__name__}: {exc}"
            extracted[q_no] = {"content": "", "passage": "", "image_caption": "", "choices": []}

        write_json(output_path, dict(sorted(extracted.items())))
        write_json(errors_path, dict(sorted(errors.items())))
        processed += 1
        time.sleep(0.2)

    return extracted

def extract_answers_with_vision(round_no: int) -> dict[int, dict[str, int]]:
    output_path = round_output_dir(round_no) / f"answer_key_{round_no}.json"
    existing = read_json(output_path, None)
    if existing:
        return {int(k): v for k, v in existing.items()}

    answer_pdf = find_pdf(round_no, "답지")
    prompt = """
한국사능력검정시험 심화 정답표 이미지에서 1번부터 50번까지 정답 번호와 배점을 추출하세요.
반드시 JSON 객체만 반환하세요.

형식:
{
  "answers": [
    {"question_no": 1, "answer_no": 1, "q_score": 1},
    {"question_no": 2, "answer_no": 2, "q_score": 2}
  ]
}
"""
    item = openai_json_from_image(prompt, render_pdf_page(answer_pdf, 0, scale=3.0))
    answers: dict[int, dict[str, int]] = {}
    for row in item.get("answers") or []:
        try:
            q_no = int(row["question_no"])
            answer_no = int(row["answer_no"])
            q_score = int(row["q_score"])
        except (KeyError, TypeError, ValueError):
            continue
        if 1 <= q_no <= QUESTION_COUNT and 1 <= answer_no <= 5 and q_score in {1, 2, 3}:
            answers[q_no] = {"answer_no": answer_no, "q_score": q_score}
    write_json(output_path, dict(sorted(answers.items())))
    return answers


def extract_explanations_with_vision(round_no: int, limit: int | None = None) -> dict[int, dict[str, str]]:
    output_path = round_output_dir(round_no) / f"explanations_{round_no}.json"
    errors_path = round_output_dir(round_no) / f"explanation_errors_{round_no}.json"
    done_path = round_output_dir(round_no) / f"explanation_pages_done_{round_no}.json"
    explanations = {int(k): v for k, v in read_json(output_path, {}).items()}
    errors = {int(k): str(v) for k, v in read_json(errors_path, {}).items()}
    done_pages = set(int(v) for v in read_json(done_path, []))

    explanation_pdf = find_pdf(round_no, "해설")
    pdf = pdfium.PdfDocument(str(explanation_pdf))
    prompt = """
한국사능력검정시험 해설 이미지에서 문항별 해설을 추출하세요.
반드시 JSON 객체만 반환하세요.

형식:
{
  "items": [
    {
      "question_no": 1,
      "answer_explanation": "정답과 오답 근거를 포함한 해설",
      "core_concept": "핵심 개념 1개"
    }
  ]
}
"""
    processed = 0
    for page_index in range(len(pdf)):
        page_no = page_index + 1
        if page_no in done_pages:
            continue
        if limit is not None and processed >= limit:
            break
        try:
            item = openai_json_from_image(prompt, render_pdf_page(explanation_pdf, page_index, scale=2.0))
            for row in item.get("items") or []:
                try:
                    q_no = int(row["question_no"])
                except (KeyError, TypeError, ValueError):
                    continue
                if 1 <= q_no <= QUESTION_COUNT:
                    explanations[q_no] = {
                        "answer_explanation": str(row.get("answer_explanation", "")).strip(),
                        "core_concept": str(row.get("core_concept", "")).strip(),
                    }
            done_pages.add(page_no)
            errors.pop(page_no, None)
        except Exception as exc:
            errors[page_no] = f"{type(exc).__name__}: {exc}"

        write_json(output_path, dict(sorted(explanations.items())))
        write_json(errors_path, dict(sorted(errors.items())))
        write_json(done_path, sorted(done_pages))
        processed += 1
        time.sleep(0.2)

    return explanations


def normalize_allowed(value: Any, allowed: list[str], fallback: str) -> str:
    value = str(value or "").strip()
    return value if value in allowed else fallback


def normalize_era(value: Any, text: str) -> str:
    source = f"{value or ''} {text}"
    aliases = [
        ("선사 시대", ["선사", "구석기", "신석기", "청동기", "철기"]),
        ("고조선", ["고조선", "위만 조선", "단군", "8조법"]),
        ("여러 나라", ["부여", "옥저", "동예", "삼한"]),
        ("삼국 시대", ["삼국", "고구려", "백제", "신라", "가야"]),
        ("남북국 시대", ["남북국", "통일 신라", "발해"]),
        ("고려", ["고려"]),
        ("조선 전기", ["조선 전기", "태조", "세종", "세조", "성종"]),
        ("조선 후기", ["조선 후기", "영조", "정조", "세도 정치", "홍경래"]),
        ("개항기", ["개항", "대한 제국", "갑신정변", "동학", "갑오개혁", "광무개혁"]),
        ("일제 강점기", ["일제", "강점", "의병", "3·1", "광복군", "독립운동"]),
        ("현대", ["현대", "대한민국", "정부 수립", "6·25", "민주화", "통일"]),
    ]
    for era, keywords in aliases:
        if any(keyword in source for keyword in keywords):
            return era
    return normalize_allowed(value, ERA_VALUES, "통합 주제")


def normalize_topic(value: Any, text: str) -> str:
    source = f"{value or ''} {text}"
    aliases = [
        ("정치", ["정치", "왕", "정부", "국왕", "통치", "권력", "선거"]),
        ("경제", ["경제", "토지", "상업", "무역", "화폐", "수취", "세금"]),
        ("사회", ["사회", "신분", "풍속", "여성", "민중"]),
        ("문화", ["문화", "불교", "유교", "교육", "예술", "건축", "문학", "유산"]),
        ("인물", ["인물", "업적", "왕건", "이성계", "세종", "정약용", "안중근", "김구"]),
        ("군사", ["군사", "전쟁", "전투", "침입", "항쟁", "군대"]),
        ("외교", ["외교", "조약", "강화도", "청", "일본", "미국", "사신"]),
        ("사상·종교", ["사상", "종교", "불교", "유학", "천도교", "동학"]),
        ("제도", ["제도", "관청", "법", "정책", "행정", "교육 제도"]),
        ("사건", ["사건", "운동", "정변", "개혁", "전쟁", "봉기"]),
        ("지역", ["지역", "지도", "위치", "강", "산", "영토", "경계"]),
    ]
    for topic, keywords in aliases:
        if any(keyword in source for keyword in keywords):
            return topic
    return normalize_allowed(value, TOPIC_VALUES, "통합")


def infer_question_type(text: str) -> str:
    if any(keyword in text for keyword in ["순서", "나열", "전후", "연표", "흐름", "시기"]):
        return "연대기의 파악"
    if any(keyword in text for keyword in ["자료", "사료", "지도", "사진", "도표", "대화", "기사", "그림", "이미지"]):
        return "역사 자료의 분석 및 해석"
    if any(keyword in text for keyword in ["탐구", "조사", "검색", "보고서", "답사", "전시", "수집"]):
        return "역사 탐구의 설계 및 수행"
    if any(keyword in text for keyword in ["의의", "영향", "결과", "공통점", "차이점", "결론"]):
        return "결론의 도출 및 평가"
    if any(keyword in text for keyword in ["배경", "원인", "목적", "주장", "입장", "정세", "전개"]):
        return "역사 상황 및 쟁점의 인식"
    return "역사 지식의 이해"


def infer_question_subtype(text: str) -> str:
    if any(keyword in text for keyword in ["순서", "나열", "전후", "시기", "연표", "흐름"]):
        return "연표"
    if any(keyword in text for keyword in ["지도", "위치", "지역", "강", "산", "영토", "경계"]):
        return "지역"
    if any(keyword in text for keyword in ["왕", "인물", "업적", "항일", "독립운동가", "대통령"]):
        return "인물"
    if any(keyword in text for keyword in ["자료", "사료", "밑줄", "그림", "사진", "대화", "기사", "보고서", "(가)", "(나)"]):
        return "사료"
    return "개념"

def classify_record(record: dict[str, Any]) -> None:
    text = " ".join(
        str(record.get(key, ""))
        for key in ["content", "passage", "image_caption", "answer_explanation", "core_concept"]
    )
    record["era"] = normalize_era(record.get("era"), text)
    record["topic"] = normalize_topic(record.get("topic"), text)
    record["question_type"] = normalize_allowed(record.get("question_type"), QUESTION_TYPES, infer_question_type(text))
    record["question_subtype"] = normalize_allowed(record.get("question_subtype"), QUESTION_SUBTYPES, infer_question_subtype(text))


def build_seed_records(round_no: int, image_paths: dict[int, str], questions: dict[int, dict[str, Any]], answers: dict[int, dict[str, int]], explanations: dict[int, dict[str, str]]) -> list[dict[str, Any]]:
    records = []
    for q_no in range(1, QUESTION_COUNT + 1):
        question = questions.get(q_no, {})
        answer = answers.get(q_no, {})
        explanation = explanations.get(q_no, {})
        choices = question.get("choices") or [
            {"choice_no": n, "content": f"{n}번 선택지"}
            for n in range(1, 6)
        ]
        record = {
            "source_exam": f"{round_no}회 심화",
            "question_no": q_no,
            "q_score": int(answer.get("q_score") or 2),
            "era": "미분류",
            "topic": "미분류",
            "question_type": "미분류",
            "question_subtype": "미분류",
            "content": question.get("content") or "문항 이미지를 글로 설명한 지문을 보고 정답을 선택하세요.",
            "passage": question.get("passage") or "",
            "image_caption": question.get("image_caption") or "",
            "question_image_path": "",
            "answer_no": int(answer.get("answer_no") or 1),
            "answer_explanation": explanation.get("answer_explanation", ""),
            "core_concept": explanation.get("core_concept", ""),
            "choices": [
                {
                    "choice_no": int(choice["choice_no"]),
                    "content": str(choice.get("content", "")).strip(),
                    "choice_image_path": "",
                    "is_answer": int(choice["choice_no"]) == int(answer.get("answer_no") or 1),
                }
                for choice in choices
                if str(choice.get("choice_no", "")).isdigit()
            ],
        }
        classify_record(record)
        records.append(record)
    return records


def import_records_to_db(records: list[dict[str, Any]]) -> None:
    try:
        import psycopg2
    except ImportError as exc:
        raise RuntimeError("psycopg2-binary is required for --import-db") from exc

    config = {
        "dbname": os.getenv("POSTGRES_DB", "history_rag"),
        "user": os.getenv("POSTGRES_USER", "himate"),
        "password": os.getenv("POSTGRES_PASSWORD", "himate1234"),
        "host": os.getenv("POSTGRES_HOST", "localhost"),
        "port": os.getenv("POSTGRES_PORT", "5432"),
    }

    conn = psycopg2.connect(**config)
    try:
        with conn, conn.cursor() as cur:
            if SCHEMA_ALTER_SQL.exists():
                cur.execute(SCHEMA_ALTER_SQL.read_text(encoding="utf-8"))
            cur.execute("TRUNCATE TABLE solve_records, question_options, questions RESTART IDENTITY CASCADE")
            for record in records:
                cur.execute(
                    """
                    INSERT INTO questions (
                        question_no,
                        q_score, era, topic, question_type, question_subtype,
                        content, passage, image_caption, question_image_path,
                        answer_no, answer_explanation, core_concept
                    )
                    VALUES (
                        %(question_no)s,
                        %(q_score)s, %(era)s, %(topic)s, %(question_type)s, %(question_subtype)s,
                        %(content)s, %(passage)s, %(image_caption)s, %(question_image_path)s,
                        %(answer_no)s, %(answer_explanation)s, %(core_concept)s
                    )
                    RETURNING question_id
                    """,
                    record,
                )
                question_id = cur.fetchone()[0]
                for choice in record.get("choices", []):
                    cur.execute(
                        """
                        INSERT INTO question_options (
                            question_id, choice_no, content, choice_image_path, is_answer, choice_explanation
                        )
                        VALUES (%s, %s, %s, %s, %s, %s)
                        """,
                        (
                            question_id,
                            choice["choice_no"],
                            choice["content"],
                            choice.get("choice_image_path") or None,
                            choice["is_answer"],
                            None,
                        ),
                    )
    finally:
        conn.close()


def process_round(round_no: int, args: argparse.Namespace) -> list[dict[str, Any]]:
    image_paths = crop_question_images(round_no, scale=args.scale)

    answers = {int(k): v for k, v in read_json(round_output_dir(round_no) / f"answer_key_{round_no}.json", {}).items()}
    if args.answers:
        answers = extract_answers_with_vision(round_no)

    questions = {int(k): v for k, v in read_json(round_output_dir(round_no) / f"vision_questions_{round_no}.json", {}).items()}
    if args.vision:
        questions = extract_questions_with_vision(round_no, image_paths, limit=args.limit)

    explanations = {int(k): v for k, v in read_json(round_output_dir(round_no) / f"explanations_{round_no}.json", {}).items()}
    if args.explanations:
        explanations = extract_explanations_with_vision(round_no, limit=args.explanation_limit)

    records = build_seed_records(round_no, image_paths, questions, answers, explanations)
    write_json(round_output_dir(round_no) / f"db_seed_{round_no}.json", records)
    return records


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rounds", nargs="+", type=int, default=DEFAULT_ROUNDS)
    parser.add_argument("--vision", action="store_true", help="extract question text and image captions")
    parser.add_argument("--answers", action="store_true", help="extract answer key from answer PDFs")
    parser.add_argument("--explanations", action="store_true", help="extract explanations from explanation PDFs")
    parser.add_argument("--classify", action="store_true", help="rebuild db_seed files with local classification")
    parser.add_argument("--import-db", action="store_true", help="import combined seed records into PostgreSQL")
    parser.add_argument("--limit", type=int, default=None, help="limit vision question extraction per round")
    parser.add_argument("--explanation-limit", type=int, default=None, help="limit explanation pages per round")
    parser.add_argument("--scale", type=float, default=2.0)
    args = parser.parse_args()

    load_dotenv_file(ROOT_DIR / ".env")
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    all_records = []
    for round_no in args.rounds:
        all_records.extend(process_round(round_no, args))

    write_json(OUT_DIR / "db_seed_all.json", all_records)

    if args.import_db:
        import_records_to_db(all_records)

    summary = {
        "rounds": args.rounds,
        "records": len(all_records),
        "vision": bool(args.vision),
        "answers": bool(args.answers),
        "explanations": bool(args.explanations),
        "classify": bool(args.classify),
        "import_db": bool(args.import_db),
        "output_dir": str(OUT_DIR.relative_to(ROOT_DIR)).replace("\\", "/"),
    }
    write_json(OUT_DIR / "summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

