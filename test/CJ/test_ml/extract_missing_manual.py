"""
extract_missing_manual.py

정답지 파싱 실패한 6개 문항을 Vision으로 추출하고
정답은 TODO로 남겨두어 수동 입력 가능하게 저장.

대상:
  58회 16번
  73회 4, 6, 14, 26, 27번

Usage:
  python test/CJ/test_ml/extract_missing_manual.py
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

# extract_image_questions 모듈에서 공용 함수/상수 임포트
sys.path.insert(0, str(Path(__file__).resolve().parent))
from extract_image_questions import (
    ROOT_DIR, OUT_DIR, OUTPUT_JSON,
    EXPLAIN_PRIORITY, TOPIC_TYPES, MATERIAL_TYPES, MAJOR_TYPES,
    MINOR_TYPES, QUESTION_TASKS, DIFFICULTY_LABELS,
    load_dotenv, get_model, create_chat_completion,
    find_pdf, find_explanation_pdf, render_page_b64, estimate_pages,
    extract_question_vision, load_existing, save_output,
    get_source_group_index, MISSING_QUESTIONS,
)
import pypdfium2 as pdfium
from openai import OpenAI

# ── 대상 문항 ────────────────────────────────────────────────────────────────
MANUAL_TARGETS = [
    (58, 16),
    (73, 4),
    (73, 6),
    (73, 14),
    (73, 26),
    (73, 27),
]


def to_manual_item(
    round_no: int,
    question_no: int,
    extracted: dict,
    problem_id: str,
    source_group_index: int,
) -> dict:
    """정답 없이 구조만 채운 항목. answer_choice = 'TODO'"""
    choices_text = list(extracted.get("choices", []))
    while len(choices_text) < 5:
        choices_text.append("")

    material   = extracted.get("material", "")
    question   = extracted.get("question", "")
    input_text = f"{material}\n{question}".strip()

    topic_type = extracted.get("topic_type", "기타")
    if topic_type not in TOPIC_TYPES:
        topic_type = "기타"
    material_type = extracted.get("material_type", MATERIAL_TYPES[0])
    if material_type not in MATERIAL_TYPES:
        material_type = MATERIAL_TYPES[0]
    major_type = extracted.get("major_type", MAJOR_TYPES[0])
    if major_type not in MAJOR_TYPES:
        major_type = MAJOR_TYPES[0]
    minor_type = extracted.get("minor_type", MINOR_TYPES[0])
    if minor_type not in MINOR_TYPES:
        minor_type = MINOR_TYPES[0]
    question_task = extracted.get("question_task", "standard_select")
    if question_task not in QUESTION_TASKS:
        question_task = "standard_select"
    difficulty_label = extracted.get("difficulty_label", "보통")
    if difficulty_label not in DIFFICULTY_LABELS:
        difficulty_label = "보통"

    # 정답 미입력 → TODO 표시
    choices_full = [{"is_answer": False, "content": c} for c in choices_text]

    return {
        "problem_id":         problem_id,
        "source_group_index": source_group_index,
        "material":           material,
        "question":           question,
        "input_text":         input_text,
        "answer_choice":      "TODO",          # ← 수동 입력 필요
        "distractor_choices": choices_text,    # ← 정답 제외 전이므로 전체
        "topic_type":         topic_type,
        "topic":              extracted.get("topic", ""),
        "material_type":      material_type,
        "major_type":         major_type,
        "minor_type":         minor_type,
        "question_task":      question_task,
        "difficulty_label":   difficulty_label,
        "choices":            choices_full,    # ← is_answer 전부 false
        "choice_count":       5,
        "distractor_count":   4,
        "round_no":           round_no,
        "question_no":        question_no,
        "_needs_answer":      True,            # ← 수동 완성 필요 표시
    }


def main() -> None:
    load_dotenv()

    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        print("[ERROR] OPENAI_API_KEY 없음.")
        sys.exit(1)

    client = OpenAI(api_key=api_key)
    print(f"[INFO] 모델: {get_model()}")

    existing = load_existing()
    print(f"[INFO] 기존 결과: {len(existing)}개\n")

    for rnd, qno in MANUAL_TARGETS:
        key = (rnd, qno)
        if key in existing:
            print(f"  [{rnd}-{qno:02d}] 이미 존재 — 스킵")
            continue

        q_pdf = find_pdf(rnd)
        if not q_pdf:
            print(f"  [{rnd}-{qno:02d}] 문제지 PDF 없음 — 스킵")
            continue

        ex_pdf = find_explanation_pdf(rnd)

        print(f"  [{rnd}-{qno:02d}] 추출 중...", end=" ", flush=True)

        try:
            q_total  = len(pdfium.PdfDocument(str(q_pdf)))
            ex_total = len(pdfium.PdfDocument(str(ex_pdf))) if ex_pdf else 0
        except Exception as e:
            print(f"PDF 열기 실패: {e}")
            continue

        q_pages  = estimate_pages(qno, q_total)
        ex_pages = estimate_pages(qno, ex_total) if ex_pdf else []

        extracted = None

        # Tier 1: Dual (문제지 + 해설지 추정 페이지)
        if ex_pdf and ex_pages:
            extracted = extract_question_vision(
                client, rnd, qno, q_pdf, q_pages, ex_pdf, ex_pages,
            )

        # Tier 2: 해설지 전체 스캔
        if extracted is None and ex_pdf:
            print("(T2)", end=" ", flush=True)
            extracted = extract_question_vision(
                client, rnd, qno, q_pdf, q_pages, ex_pdf, list(range(ex_total)),
            )

        # Tier 3: 문제지 단독
        if extracted is None:
            print("(T3)", end=" ", flush=True)
            extracted = extract_question_vision(client, rnd, qno, q_pdf, q_pages)

        if extracted is None:
            print("FAIL — Vision 추출 불가")
            continue

        problem_id         = f"cj_v41_img_{rnd:02d}_{qno:02d}"
        source_group_index = get_source_group_index(rnd, qno)

        item = to_manual_item(rnd, qno, extracted, problem_id, source_group_index)
        existing[key] = item
        save_output(existing)
        print("OK (정답 TODO)")

    print("\n[완료] han_cj_v41_image.json에 저장됨")
    print("→ '_needs_answer': true 항목을 찾아 정답을 직접 입력하세요")
    print("  - answer_choice: 정답 텍스트")
    print("  - distractor_choices: 오답만 남기기")
    print("  - choices: 정답 항목의 is_answer를 true로 변경")
    print("  - _needs_answer 필드 삭제")


if __name__ == "__main__":
    main()
