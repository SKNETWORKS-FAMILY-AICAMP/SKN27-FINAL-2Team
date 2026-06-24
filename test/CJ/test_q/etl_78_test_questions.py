"""
Build test data from the 78th Korean History exam PDFs.

This script is intentionally isolated under test/CJ/test_q. It only modifies
the database when --import-db is passed.

Default output:
  test/CJ/test_q/output_78/
    answer_key_78.json
    explanations_78.json
    question_images/q_001.png ...
    db_seed_78.json

Optional Vision extraction:
  python test/CJ/test_q/etl_78_test_questions.py --vision

The --vision mode requires OPENAI_API_KEY and extracts passage/question/options
from each cropped question image. Without --vision, the script still produces
question images, answer key, explanations, and placeholder records.

Optional classification and DB import:
  python test/CJ/test_q/etl_78_test_questions.py --classify
  python test/CJ/test_q/etl_78_test_questions.py --import-db
"""

from __future__ import annotations

import argparse
import json
import os
import re
import time
from io import BytesIO
from pathlib import Path
from typing import Any

import pdfplumber
import pypdfium2 as pdfium


ROOT_DIR = Path(__file__).resolve().parents[3]
DOCS_DIR = ROOT_DIR / "test" / "CJ" / "test_docs"
OUT_DIR = ROOT_DIR / "test" / "CJ" / "test_q" / "output_78"
ALL_COMBINED_JSON = ROOT_DIR / "test" / "CJ" / "test_q" / "all_combined.json"

QUESTION_PDF = DOCS_DIR / "78???쒓뎅??臾몄젣吏(?ы솕).pdf"
ANSWER_PDF = DOCS_DIR / "78???쒓뎅???듭?(?ы솕).pdf"
EXPLANATION_PDF = DOCS_DIR / "?쒓뎅?щ뒫?κ??뺤떆??78???ы솕 ?댁꽕 ?쏱ro.pdf"
SCHEMA_ALTER_SQL = ROOT_DIR / "storage" / "postgresql" / "schema" / "alter_apply_latest.sql"

QUESTION_TYPES = [
    "역사 지식의 이해",
    "연대기의 파악",
    "역사 상황 및 쟁점의 인식",
    "역사 자료의 분석 및 해석",
    "역사 탐구의 설계 및 수행",
    "결론의 도출 및 평가",
]

QUESTION_SUBTYPES = [
    "개념",
    "인물",
    "사료",
    "연표",
    "지역",
]

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

CIRCLE_TO_INT = {
    "①": 1,
    "②": 2,
    "③": 3,
    "④": 4,
    "⑤": 5,
}


def load_dotenv_file(path: Path) -> None:
    """Load simple KEY=VALUE pairs without requiring python-dotenv."""
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


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def parse_answer_sheet() -> dict[int, dict[str, int]]:
    """Parse answer number and score from the answer PDF."""
    with pdfplumber.open(str(ANSWER_PDF)) as pdf:
        text = pdf.pages[0].extract_text() or ""

    pattern = re.compile(r"(\d+)\s+([?졻몼?™몿??)\s+(\d+)")
    answers: dict[int, dict[str, int]] = {}
    for q_no, circle, score in pattern.findall(text):
        answers[int(q_no)] = {
            "answer_no": CIRCLE_TO_INT[circle],
            "q_score": int(score),
        }
    return dict(sorted(answers.items()))


def parse_explanations() -> dict[int, dict[str, str]]:
    """Best-effort text extraction from the explanation PDF."""
    text_parts: list[str] = []
    with pdfplumber.open(str(EXPLANATION_PDF)) as pdf:
        for page in pdf.pages:
            text = page.extract_text() or ""
            if text.strip():
                text_parts.append(text)

    text = "\n".join(text_parts)
    text = re.sub(r"Cannot set [^\n]+", "", text)
    text = re.sub(r"\n{3,}", "\n\n", text)

    explanations: dict[int, dict[str, str]] = {}
    # The source usually marks sections like "<1踰??ㅻ떟 ?댁꽕>".
    section_pattern = re.compile(
        r"<\s*(\d+)\s*踰?s+?ㅻ떟\s+?댁꽕\s*>(.*?)(?=<\s*\d+\s*踰?s+?ㅻ떟\s+?댁꽕\s*>|\Z)",
        re.S,
    )
    for match in section_pattern.finditer(text):
        q_no = int(match.group(1))
        body = match.group(2).strip()
        body = re.sub(r"\s+", " ", body)
        explanations[q_no] = {
            "answer_explanation": body,
            "core_concept": infer_core_concept(body),
        }
    return dict(sorted(explanations.items()))


def infer_core_concept(explanation: str) -> str:
    """Small heuristic for test data only."""
    candidates = [
        "구석기",
        "신석기",
        "청동기",
        "고조선",
        "부여",
        "고구려",
        "諛깆젣",
        "신라",
        "가야",
        "諛쒗빐",
        "怨좊젮",
        "議곗꽑",
        "대한 제국",
        "일제 강점기",
        "愿묐났",
        "민주화",
        "통일",
    ]
    for word in candidates:
        if word in explanation:
            return word
    return ""


def page_question_numbers(page_no: int) -> dict[str, list[int]]:
    """Return question numbers per column for the 78th exam layout."""
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


def crop_question_images(scale: float = 2.0) -> dict[int, str]:
    """Render the question PDF and crop each question area by known layout."""
    image_dir = OUT_DIR / "question_images"
    image_dir.mkdir(parents=True, exist_ok=True)

    pdf = pdfium.PdfDocument(str(QUESTION_PDF))
    results: dict[int, str] = {}

    for page_index in range(len(pdf)):
        page_no = page_index + 1
        page = pdf[page_index]
        bitmap = page.render(scale=scale)
        image = bitmap.to_pil().convert("RGB")
        width, height = image.size

        # Coordinates tuned for the official two-column PDF.
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
                if path.exists():
                    results[q_no] = str(path.relative_to(ROOT_DIR)).replace("\\", "/")
                    continue

                y1 = max(0, idx * block_height - int(col_height * 0.015))
                y2 = col_height if idx == len(numbers) - 1 else (idx + 1) * block_height + int(col_height * 0.035)
                q_img = column.crop((0, y1, col_width, y2))
                q_img.save(path)
                results[q_no] = str(path.relative_to(ROOT_DIR)).replace("\\", "/")

    return dict(sorted(results.items()))


def extract_with_openai(image_paths: dict[int, str]) -> dict[int, dict[str, Any]]:
    """Use OpenAI Vision to structure each cropped question image."""
    try:
        from openai import OpenAI
    except ImportError as exc:
        raise RuntimeError("openai package is required for --vision") from exc

    if not os.getenv("OPENAI_API_KEY"):
        raise RuntimeError("OPENAI_API_KEY is required for --vision")

    import base64

    client = OpenAI()
    extracted_path = OUT_DIR / "vision_extracted_78.json"
    errors_path = OUT_DIR / "vision_errors_78.json"
    extracted: dict[int, dict[str, Any]] = {}
    errors: dict[int, str] = {}
    if extracted_path.exists():
        raw_existing = json.loads(extracted_path.read_text(encoding="utf-8"))
        extracted = {int(k): v for k, v in raw_existing.items()}
    if errors_path.exists():
        raw_errors = json.loads(errors_path.read_text(encoding="utf-8"))
        errors = {int(k): str(v) for k, v in raw_errors.items()}

    prompt = """
?쒓뎅?щ뒫?κ??뺤떆???ы솕 臾명빆 ?대?吏?먯꽌 吏臾? 諛쒕Ц, ?좏깮吏瑜?援ъ“?뷀빐以?
諛섎뱶??JSON 媛앹껜留?諛섑솚??

?뺤떇:
{
  "passage": "?먮즺/吏臾?留먰뭾?????대?吏 ?ㅻ챸 ?꾩껜. ?놁쑝硫?鍮?臾몄옄??,
  "content": "諛쒕Ц留?,
  "choices": [
    {"choice_no": 1, "content": "?좏깮吏 ?댁슜"},
    {"choice_no": 2, "content": "?좏깮吏 ?댁슜"},
    {"choice_no": 3, "content": "?좏깮吏 ?댁슜"},
    {"choice_no": 4, "content": "?좏깮吏 ?댁슜"},
    {"choice_no": 5, "content": "?좏깮吏 ?댁슜"}
  ],
  "visual_note": "洹몃┝/吏???ъ쭊/???ㅻ챸. ?놁쑝硫?鍮?臾몄옄??,
  "parse_status": "ok ?먮뒗 review_needed"
}

二쇱쓽:
- ?좏깮吏 踰덊샇 ?졻몼?™몿?ㅻ뒗 choice_no濡?蹂?섑븯怨?content?먮뒗 ?ｌ? 留?
- ?대?吏 ???띿뒪?멸? ?먮━硫?異붿륫?섏? 留먭퀬 parse_status瑜?review_needed濡???
- 洹몃┝ 臾몄젣??visual_note??臾댁뾿??蹂닿퀬 ??댁빞 ?섎뒗吏 ?ㅻ챸??
"""

    for q_no, rel_path in image_paths.items():
        if q_no in extracted and extracted[q_no].get("parse_status") != "error":
            continue

        abs_path = ROOT_DIR / rel_path
        b64 = base64.b64encode(abs_path.read_bytes()).decode("utf-8")
        try:
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
            content = response.choices[0].message.content or "{}"
            extracted[q_no] = normalize_vision_item(json.loads(content))
            errors.pop(q_no, None)
        except Exception as exc:
            errors[q_no] = f"{type(exc).__name__}: {exc}"
            extracted[q_no] = {
                "passage": "",
                "content": "",
                "choices": [],
                "visual_note": "",
                "parse_status": "error",
            }

        write_json(extracted_path, dict(sorted(extracted.items())))
        write_json(errors_path, dict(sorted(errors.items())))
        time.sleep(0.2)

    return extracted


def extract_explanations_with_openai(limit: int | None = None) -> dict[int, dict[str, str]]:
    """Use OpenAI Vision to extract explanations from the explanation PDF."""
    try:
        from openai import OpenAI
    except ImportError as exc:
        raise RuntimeError("openai package is required for --explanations") from exc

    if not os.getenv("OPENAI_API_KEY"):
        raise RuntimeError("OPENAI_API_KEY is required for --explanations")

    import base64

    output_path = OUT_DIR / "explanations_78.json"
    errors_path = OUT_DIR / "explanation_errors_78.json"
    done_path = OUT_DIR / "explanation_pages_done_78.json"

    explanations: dict[int, dict[str, str]] = {}
    errors: dict[int, str] = {}
    done_pages: set[int] = set()
    if output_path.exists():
        raw = json.loads(output_path.read_text(encoding="utf-8"))
        explanations = {int(k): v for k, v in raw.items()} if isinstance(raw, dict) else {}
    if errors_path.exists():
        raw_errors = json.loads(errors_path.read_text(encoding="utf-8"))
        errors = {int(k): str(v) for k, v in raw_errors.items()}
    if done_path.exists():
        done_pages = set(json.loads(done_path.read_text(encoding="utf-8")))
    if not explanations and done_pages:
        done_pages = set()

    client = OpenAI()
    prompt = """
?쒓뎅?щ뒫?κ??뺤떆???댁꽕 PDF ???섏씠吏 ?대?吏?먯꽌 臾명빆蹂??댁꽕??異붿텧?댁쨾.
諛섎뱶??JSON 媛앹껜留?諛섑솚??

?뺤떇:
{
  "results": [
    {
      "question_no": 1,
      "answer_explanation": "?뺣떟 洹쇨굅? ?ㅻ떟 ?댁꽕???ы븿???댁꽕 ?꾩껜瑜??먯뿰?ㅻ읇寃??뺣━",
      "core_concept": "?듭떖 媛쒕뀗 1媛?
    }
  ]
}

二쇱쓽:
- ?섏씠吏??蹂댁씠??臾명빆 踰덊샇留?異붿텧??
- ?뺣떟?쒕쭔 ?덇퀬 ?댁꽕???녿뒗 ?쒕뒗 results???ｌ? 留?
- ?대?吏???먮Ц ?댁꽕??湲몃㈃ ?듭떖??蹂댁〈?댁꽌 5~12臾몄옣?쇰줈 ?뺣━??
- 臾명빆 踰덊샇媛 遺덊솗?ㅽ븯硫??ｌ? 留?
"""

    pdf = pdfium.PdfDocument(str(EXPLANATION_PDF))
    page_indexes = range(len(pdf))
    if limit:
        page_indexes = range(min(limit, len(pdf)))

    for page_index in page_indexes:
        page_no = page_index + 1
        if page_no in done_pages:
            continue

        try:
            image = pdf[page_index].render(scale=1.8).to_pil().convert("RGB")
            buffer = BytesIO()
            image.save(buffer, format="PNG")
            b64 = base64.b64encode(buffer.getvalue()).decode("utf-8")

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
            data = json.loads(response.choices[0].message.content or "{}")
            for item in data.get("results", []):
                try:
                    q_no = int(item.get("question_no"))
                except Exception:
                    continue
                if 1 <= q_no <= 50:
                    explanations[q_no] = {
                        "answer_explanation": str(item.get("answer_explanation", "")).strip(),
                        "core_concept": str(item.get("core_concept", "")).strip(),
                    }
            errors.pop(page_no, None)
            done_pages.add(page_no)
        except Exception as exc:
            errors[page_no] = f"{type(exc).__name__}: {exc}"

        write_json(output_path, dict(sorted(explanations.items())))
        write_json(errors_path, dict(sorted(errors.items())))
        write_json(done_path, sorted(done_pages))
        time.sleep(0.2)

    return explanations


def normalize_vision_item(item: dict[str, Any]) -> dict[str, Any]:
    choices = item.get("choices") or []
    normalized_choices = []
    for choice in choices:
        try:
            choice_no = int(choice.get("choice_no"))
        except Exception:
            continue
        if 1 <= choice_no <= 5:
            normalized_choices.append({
                "choice_no": choice_no,
                "content": str(choice.get("content", "")).strip(),
            })

    status = item.get("parse_status") or "review_needed"
    if len(normalized_choices) != 5:
        status = "review_needed"

    return {
        "passage": str(item.get("passage", "")).strip(),
        "content": str(item.get("content", "")).strip(),
        "choices": normalized_choices,
        "visual_note": str(item.get("visual_note", "")).strip(),
        "parse_status": status,
    }


def limit_items(items: dict[int, str], limit: int | None) -> dict[int, str]:
    if not limit:
        return items
    return dict(list(items.items())[:limit])


def load_existing_vision_data() -> dict[int, dict[str, Any]] | None:
    path = OUT_DIR / "vision_extracted_78.json"
    if not path.exists():
        return None
    raw = json.loads(path.read_text(encoding="utf-8"))
    return {int(k): v for k, v in raw.items()}


def load_existing_explanations() -> dict[int, dict[str, str]]:
    path = OUT_DIR / "explanations_78.json"
    if not path.exists():
        return {}
    raw = json.loads(path.read_text(encoding="utf-8"))
    return {int(k): v for k, v in raw.items()}


def load_existing_classification() -> dict[int, dict[str, str]]:
    path = OUT_DIR / "classification_78.json"
    if not path.exists():
        return {}
    raw = json.loads(path.read_text(encoding="utf-8"))
    return {int(k): v for k, v in raw.items()}


def load_reference_classification() -> dict[int, dict[str, Any]]:
    """Load existing hand-reviewed labels from all_combined.json when present."""
    if not ALL_COMBINED_JSON.exists():
        return {}
    raw = json.loads(ALL_COMBINED_JSON.read_text(encoding="utf-8"))
    result: dict[int, dict[str, Any]] = {}
    for row in raw:
        q_no = row.get("臾몄젣踰덊샇")
        if q_no is None:
            continue
        result[int(q_no)] = row
    return result


def normalize_era(value: Any, text: str) -> str:
    source = f"{value or ''} {text}"
    era_aliases = [
        ("선사 시대", ["선사", "구석기", "신석기", "청동기", "철기"]),
        ("고조선", ["고조선", "위만 조선", "단군"]),
        ("여러 나라", ["부여", "옥저", "동예", "삼한"]),
        ("삼국 시대", ["삼국", "고구려", "백제", "신라", "가야"]),
        ("남북국 시대", ["남북국", "통일 신라", "발해"]),
        ("고려", ["고려"]),
        ("조선 전기", ["조선 전기", "태조", "세종", "세조", "성종", "훈구"]),
        ("조선 후기", ["조선 후기", "영조", "정조", "세도 정치", "흥선 대원군"]),
        ("개항기", ["개항", "대한 제국", "갑신정변", "동학", "갑오개혁", "광무개혁"]),
        ("일제 강점기", ["일제", "강점", "독립운동", "3·1", "광복군"]),
        ("현대", ["현대", "대한민국", "정부 수립", "6·25", "민주화", "통일"]),
    ]
    for era, keywords in era_aliases:
        if any(keyword in source for keyword in keywords):
            return era
    return normalize_allowed(value, ERA_VALUES, "통합 주제")


def normalize_topic(value: Any, text: str) -> str:
    source = f"{value or ''} {text}"
    topic_aliases = [
        ("정치", ["정치", "왕", "정부", "국왕", "통치", "권력", "선거"]),
        ("경제", ["경제", "토지", "상업", "무역", "화폐", "수취", "세금"]),
        ("사회", ["사회", "신분", "풍속", "여성", "향촌", "민중"]),
        ("문화", ["문화", "불교", "유교", "교육", "예술", "건축", "문학", "유산"]),
        ("인물", ["인물", "업적", "왕", "장군", "학자"]),
        ("군사", ["군사", "전쟁", "전투", "침입", "항쟁", "군대"]),
        ("외교", ["외교", "조약", "강화도", "청", "일본", "미국", "러시아"]),
        ("사상·종교", ["사상", "종교", "불교", "유학", "천도교", "동학"]),
        ("제도", ["제도", "관청", "법", "정책", "행정", "교육 제도"]),
        ("사건", ["사건", "운동", "정변", "개혁", "전쟁", "봉기"]),
        ("지역", ["지역", "지도", "위치", "읍", "도"]),
    ]
    for topic, keywords in topic_aliases:
        if any(keyword in source for keyword in keywords):
            return topic
    return normalize_allowed(value, TOPIC_VALUES, "통합")


def infer_question_subtype(record: dict[str, Any], reference: dict[str, Any] | None = None) -> str:
    """Classify subtype for filtering: concept, person, source, chronology, or region."""
    parts = [
        str(record.get("content", "")),
        str(record.get("passage", "")),
        str(record.get("answer_explanation", "")),
        str(record.get("core_concept", "")),
    ]
    if reference:
        parts.extend([
            str(reference.get("臾몄젣?댁슜", "")),
            " ".join(str(choice) for choice in reference.get("?좏깮吏", [])),
            str(reference.get("?댁꽕", "")),
            str(reference.get("?듭떖媛쒕뀗", "")),
        ])
    text = " ".join(parts)

    if any(keyword in text for keyword in ["순서", "나열", "전후", "시기", "연표", "흐름"]):
        return "연표"
    if any(keyword in text for keyword in ["지도", "위치", "지역", "읍", "도", "영토", "경계"]):
        return "지역"
    if any(keyword in text for keyword in ["왕", "인물", "업적", "장군", "학자", "대통령"]):
        return "인물"
    if any(keyword in text for keyword in ["자료", "사료", "밑줄", "그림", "사진", "대화", "기사", "보고서", "(가)", "(나)"]):
        return "사료"
    return "개념"


def classify_records_locally(records: list[dict[str, Any]]) -> dict[int, dict[str, str]]:
    """Apply the user's classification criteria without calling external APIs."""
    references = load_reference_classification()
    classification: dict[int, dict[str, str]] = {}
    for record in records:
        q_no = int(record["question_no"])
        reference = references.get(q_no, {})
        reference_text = " ".join(
            [
                str(reference.get("臾몄젣?댁슜", "")),
                " ".join(str(choice) for choice in reference.get("?좏깮吏", [])),
                str(reference.get("?댁꽕", "")),
                str(reference.get("?듭떖媛쒕뀗", "")),
            ]
        )
        record_text = " ".join(
            [
                str(record.get("content", "")),
                str(record.get("passage", "")),
                str(record.get("answer_explanation", "")),
                str(record.get("core_concept", "")),
            ]
        )
        source_text = f"{reference_text} {record_text}"

        question_type = normalize_allowed(
            reference.get("question_type") or record.get("question_type"),
            QUESTION_TYPES,
            infer_question_type(source_text),
        )
        classification[q_no] = {
            "era": normalize_era(reference.get("?쒕?") or record.get("era"), source_text),
            "topic": normalize_topic(reference.get("二쇱젣") or record.get("topic"), source_text),
            "question_type": question_type,
            "question_subtype": infer_question_subtype(record, reference),
        }
    return classification


def infer_question_type(text: str) -> str:
    """Fallback major-type classifier based on the user's six behavior criteria."""
    if any(keyword in text for keyword in ["순서", "나열", "전후", "연표", "흐름", "시기"]):
        return "연대기의 파악"
    if any(keyword in text for keyword in ["자료", "사료", "지도", "사진", "도표", "대화", "기사", "그림"]):
        return "역사 자료의 분석 및 해석"
    if any(keyword in text for keyword in ["탐구", "조사", "검색", "보고서", "답사", "전시", "수집"]):
        return "역사 탐구의 설계 및 수행"
    if any(keyword in text for keyword in ["의의", "영향", "결과", "공통점", "차이점", "결론"]):
        return "결론의 도출 및 평가"
    if any(keyword in text for keyword in ["배경", "원인", "목적", "주장", "입장", "정세", "전개"]):
        return "역사 상황 및 쟁점의 인식"
    return "역사 지식의 이해"


def apply_classification(records: list[dict[str, Any]], classification: dict[int, dict[str, str]]) -> None:
    for record in records:
        item = classification.get(int(record["question_no"]))
        if not item:
            continue
        record["era"] = item.get("era") or record["era"]
        record["topic"] = item.get("topic") or record["topic"]
        record["question_type"] = item.get("question_type") or record["question_type"]
        record["question_subtype"] = item.get("question_subtype") or record["question_subtype"]


def classify_records_with_openai(records: list[dict[str, Any]], limit: int | None = None) -> dict[int, dict[str, str]]:
    output_path = OUT_DIR / "classification_78.json"
    errors_path = OUT_DIR / "classification_errors_78.json"
    classification = load_existing_classification()
    classification.update(classify_records_locally(records))
    errors: dict[int, str] = {}
    if errors_path.exists():
        raw_errors = json.loads(errors_path.read_text(encoding="utf-8"))
        errors = {int(k): str(v) for k, v in raw_errors.items()}

    targets = records[:limit] if limit else records
    missing_targets = [
        record for record in targets
        if int(record["question_no"]) not in classification
    ]
    if not missing_targets:
        write_json(output_path, dict(sorted(classification.items())))
        write_json(errors_path, dict(sorted(errors.items())))
        return classification

    try:
        from openai import OpenAI
    except ImportError as exc:
        raise RuntimeError("openai package is required for unresolved --classify records") from exc

    if not os.getenv("OPENAI_API_KEY"):
        raise RuntimeError("OPENAI_API_KEY is required for unresolved --classify records")

    client = OpenAI()
    question_types = "\n".join(f"- {value}" for value in QUESTION_TYPES)
    question_subtypes = "\n".join(f"- {value}" for value in QUESTION_SUBTYPES)
    eras = "\n".join(f"- {value}" for value in ERA_VALUES)
    topics = "\n".join(f"- {value}" for value in TOPIC_VALUES)

    for record in missing_targets:
        q_no = int(record["question_no"])

        choices_text = "\n".join(
            f"{choice['choice_no']}. {choice['content']}"
            for choice in record.get("choices", [])
        )
        prompt = f"""
?ㅼ쓬 ?쒓뎅?щ뒫?κ??뺤떆??臾명빆??遺꾨쪟?댁쨾.
諛섎뱶??JSON 媛앹껜留?諛섑솚??

?덉슜 question_type:
{question_types}

?덉슜 question_subtype:
{question_subtypes}

?덉슜 era:
{eras}

?덉슜 topic:
{topics}

臾명빆:
- 踰덊샇: {q_no}
- 諛쒕Ц: {record.get("content", "")}
- ?먮즺/吏臾? {record.get("passage", "")}
- ?좏깮吏:
{choices_text}
- ?뺣떟 ?댁꽕: {record.get("answer_explanation", "")}

諛섑솚 ?뺤떇:
{{
  "era": "?덉슜 era 以??섎굹",
  "topic": "?덉슜 topic 以??섎굹",
  "question_type": "?덉슜 question_type 以??섎굹",
  "question_subtype": "?덉슜 question_subtype 以??섎굹"
}}
"""
        try:
            response = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": prompt}],
                response_format={"type": "json_object"},
                temperature=0,
            )
            data = json.loads(response.choices[0].message.content or "{}")
            item = {
                "era": normalize_allowed(data.get("era"), ERA_VALUES, "?듯빀 二쇱젣"),
                "topic": normalize_allowed(data.get("topic"), TOPIC_VALUES, "?듯빀"),
                "question_type": normalize_allowed(
                    data.get("question_type"),
                    QUESTION_TYPES,
                    "??궗 ?먮즺??遺꾩꽍 諛??댁꽍",
                ),
                "question_subtype": normalize_allowed(
                    data.get("question_subtype"),
                    QUESTION_SUBTYPES,
                    "媛쒕뀗",
                ),
            }
            classification[q_no] = item
            errors.pop(q_no, None)
        except Exception as exc:
            errors[q_no] = f"{type(exc).__name__}: {exc}"

        write_json(output_path, dict(sorted(classification.items())))
        write_json(errors_path, dict(sorted(errors.items())))
        time.sleep(0.2)

    write_json(output_path, dict(sorted(classification.items())))
    write_json(errors_path, dict(sorted(errors.items())))
    return classification


def normalize_allowed(value: Any, allowed: list[str], fallback: str) -> str:
    text = str(value or "").strip()
    if text in allowed:
        return text
    for item in allowed:
        if item in text:
            return item
    return fallback


def build_seed_records(
    answers: dict[int, dict[str, int]],
    explanations: dict[int, dict[str, str]],
    image_paths: dict[int, str],
    vision_data: dict[int, dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for q_no in range(1, 51):
        answer = answers.get(q_no, {})
        explanation = explanations.get(q_no, {})
        extracted = (vision_data or {}).get(q_no, {})
        choices = extracted.get("choices") or [
            {"choice_no": n, "content": f"{n}踰??좏깮吏"}
            for n in range(1, 6)
        ]
        content = extracted.get("content") or "臾명빆 ?대?吏瑜?蹂닿퀬 ?뺣떟???좏깮?섏꽭??"
        passage = extracted.get("passage") or ""
        if not extracted.get("content"):
            passage = f"臾명빆 ?대?吏: {image_paths.get(q_no, '')}"

        record = {
            "question_no": q_no,
            "q_score": answer.get("q_score"),
            "era": "미분류",
            "topic": "미분류",
            "question_type": "미분류",
            "question_subtype": "미분류",
            "content": content,
            "passage": passage,
            "question_image_path": image_paths.get(q_no, ""),
            "answer_no": answer.get("answer_no"),
            "answer_explanation": explanation.get("answer_explanation", ""),
            "core_concept": explanation.get("core_concept", ""),
            "choices": [
                {
                    "choice_no": int(choice.get("choice_no")),
                    "content": str(choice.get("content", "")).strip(),
                    "is_answer": int(choice.get("choice_no")) == answer.get("answer_no"),
                }
                for choice in choices
                if str(choice.get("choice_no", "")).isdigit()
            ],
        }
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
                        content, passage, question_image_path,
                        answer_no, answer_explanation, core_concept
                    )
                    VALUES (
                        %(question_no)s,
                        %(q_score)s, %(era)s, %(topic)s, %(question_type)s, %(question_subtype)s,
                        %(content)s, %(passage)s, %(question_image_path)s,
                        %(answer_no)s, %(answer_explanation)s, %(core_concept)s
                    )
                    RETURNING question_id
                    """,
                    record,
                )
                question_id = cur.fetchone()[0]
                cur.execute("DELETE FROM question_options WHERE question_id = %s", (question_id,))
                for choice in record.get("choices", []):
                    cur.execute(
                        """
                        INSERT INTO question_options (
                            question_id, choice_no, content, is_answer, choice_explanation
                        )
                        VALUES (%s, %s, %s, %s, %s)
                        """,
                        (
                            question_id,
                            choice["choice_no"],
                            choice["content"],
                            choice["is_answer"],
                            None,
                        ),
                    )
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--vision", action="store_true", help="extract text/options with OpenAI Vision")
    parser.add_argument("--explanations", action="store_true", help="extract explanations with OpenAI Vision")
    parser.add_argument("--classify", action="store_true", help="classify era/topic/question_type with OpenAI")
    parser.add_argument("--import-db", action="store_true", help="import db_seed_78.json into PostgreSQL")
    parser.add_argument("--scale", type=float, default=2.0, help="PDF render scale for cropped images")
    parser.add_argument("--limit", type=int, default=None, help="limit Vision extraction to the first N questions")
    parser.add_argument("--explanation-limit", type=int, default=None, help="limit explanation extraction to the first N PDF pages")
    parser.add_argument("--classify-limit", type=int, default=None, help="limit classification to the first N questions")
    args = parser.parse_args()
    load_dotenv_file(ROOT_DIR / ".env")

    missing = [path for path in [QUESTION_PDF, ANSWER_PDF, EXPLANATION_PDF] if not path.exists()]
    if missing:
        raise FileNotFoundError("Missing PDF files: " + ", ".join(str(path) for path in missing))

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    answers = parse_answer_sheet()
    image_paths = crop_question_images(scale=args.scale)

    explanations = load_existing_explanations() or parse_explanations()
    if args.explanations:
        explanations.update(extract_explanations_with_openai(args.explanation_limit))

    vision_data: dict[int, dict[str, Any]] | None = None
    if args.vision:
        vision_data = extract_with_openai(limit_items(image_paths, args.limit))
        write_json(OUT_DIR / "vision_extracted_78.json", vision_data)
    else:
        vision_data = load_existing_vision_data()

    records = build_seed_records(answers, explanations, image_paths, vision_data)
    classification = load_existing_classification()
    if args.classify:
        classification.update(classify_records_with_openai(records, args.classify_limit))
    apply_classification(records, classification)

    if args.import_db:
        import_records_to_db(records)

    write_json(OUT_DIR / "answer_key_78.json", answers)
    write_json(OUT_DIR / "explanations_78.json", explanations)
    write_json(OUT_DIR / "question_images_78.json", image_paths)
    write_json(OUT_DIR / "db_seed_78.json", records)

    summary = {
        "answers": len(answers),
        "explanations": len(explanations),
        "question_images": len(image_paths),
        "records": len(records),
        "vision": bool(args.vision),
        "explanations_vision": bool(args.explanations),
        "classified": sum(1 for row in records if row["question_type"] != "미분류"),
        "import_db": bool(args.import_db),
        "vision_limit": args.limit,
        "explanation_limit": args.explanation_limit,
        "classify_limit": args.classify_limit,
        "output_dir": str(OUT_DIR.relative_to(ROOT_DIR)).replace("\\", "/"),
    }
    write_json(OUT_DIR / "summary_78.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
