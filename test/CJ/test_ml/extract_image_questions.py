"""
extract_image_questions.py

han_cj_v41.json에서 누락된 이미지 기반 문항을 GPT Vision으로 추출하여
동일한 형태의 JSON으로 저장한다.

출력: test/CJ/test_ml/output/han_cj_v41_image.json

Usage:
  python test/CJ/test_ml/extract_image_questions.py
  python test/CJ/test_ml/extract_image_questions.py --rounds 47 48 49
  python test/CJ/test_ml/extract_image_questions.py --force
"""

from __future__ import annotations

import argparse
import base64
import glob
import io
import json
import os
import re
import sys
import time
from pathlib import Path

import pdfplumber
import pypdfium2 as pdfium
from openai import OpenAI

# ── 경로 ────────────────────────────────────────────────────────────────────
ROOT_DIR       = Path(__file__).resolve().parents[3]
DOCS_DIR       = ROOT_DIR / "test" / "CJ" / "test_docs"
QUESTION_DIR   = DOCS_DIR / "1. 문제지"
ANSWER_DIR     = DOCS_DIR / "4. 정답지"
EXPLAIN_DIR    = DOCS_DIR / "2. 해설지"
OUT_DIR        = Path(__file__).resolve().parent / "output"
OUTPUT_JSON    = OUT_DIR / "han_cj_v41_image.json"
SOURCE_JSON    = Path(__file__).resolve().parent / "han_cj_v41.json"

# 해설지 우선순위: cbt(이미지 레이블 있음) > 한pro > 원유철 > 고담 > 공기출
EXPLAIN_PRIORITY = ["cbt", "한pro", "원유철", "고담한국사", "공기출"]

IMAGE_SCALE    = 2.0
API_MAX_RETRIES = 4
OPENAI_MODEL_ENV = "OPENAI_CLASSIFY_MODEL"

# ── 누락 문항 목록 (분석 결과) ────────────────────────────────────────────
MISSING_QUESTIONS: dict[int, list[int]] = {
    47: [5, 15, 27, 50],
    48: [3],
    49: [27, 39],
    50: [14, 50],
    51: [9, 27],
    52: [3, 6, 14],
    53: [9, 17, 21, 47],
    54: [5, 20, 24, 43, 45],
    55: [17],
    56: [16, 33, 35],
    57: [4, 15, 20, 50],
    58: [16],
    59: [6, 15],
    60: [10],
    61: [18, 20, 28, 50],
    62: [5, 50],
    63: [10, 15, 46, 50],
    64: [4, 16],
    65: [4, 17, 22],
    66: [3, 17, 24, 50],
    68: [49],
    69: [48],
    70: [27],
    71: [16],
    72: [47],
    73: [4, 6, 14, 26, 27],
    76: [4, 17, 27],
    77: [4, 13],
    78: [9, 14, 26],
}

# ── 허용값 ──────────────────────────────────────────────────────────────────
CIRCLE_MAP = {"①": 1, "②": 2, "③": 3, "④": 4, "⑤": 5}

TOPIC_TYPES    = ["인물", "사건", "제도", "문화", "문화유산", "집단", "매체", "기타"]
MATERIAL_TYPES = ["시각 자료 설명", "자료 제시문", "짧은 설명 자료", "탐구 자료", "연표 자료", "사건 배열 자료"]
MAJOR_TYPES    = [
    "역사 지식의 이해", "연대기의 파악",
    "역사 자료의 분석 및 해석", "역사 탐구의 설계 및 수행",
    "결론의 도출 및 평가",
]
MINOR_TYPES = [
    "기본 사실·개념 확인", "보기 조합 판단", "비교·공통점 도출",
    "사건·자료 순서 배열", "시각 자료 해석", "연표·흐름 빈칸",
    "의의·영향·결과 평가", "자료 기반 시대·대상 추론",
    "자료 수집·검색 방법", "전후 시기 판단",
    "제도·기관·정책 기능 이해", "지도·지역 위치 판단", "탐구 주제·활동 선정",
]
QUESTION_TASKS = [
    "standard_select", "map_location", "multi_select_combo",
    "negative_select", "order", "period_between", "timeline_position",
]
DIFFICULTY_LABELS = ["쉬움", "보통", "어려움"]


# ── 유틸 ────────────────────────────────────────────────────────────────────
def load_dotenv() -> None:
    env_path = ROOT_DIR / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def get_model() -> str:
    return os.getenv(OPENAI_MODEL_ENV, "gpt-4o")


def create_chat_completion(client: OpenAI, **kwargs):
    for attempt in range(API_MAX_RETRIES):
        try:
            return client.chat.completions.create(**kwargs)
        except Exception as e:
            is_last = attempt == API_MAX_RETRIES - 1
            msg = str(e)
            if is_last or ("429" not in msg and "rate" not in msg.lower()):
                raise
            wait = min(60, 8 * (attempt + 1))
            print(f"      [Rate limit] {wait}초 대기 후 재시도...")
            time.sleep(wait)


# ── 정답지 파싱 ──────────────────────────────────────────────────────────────
def parse_answer_sheets() -> dict[int, dict[int, int]]:
    """정답지 PDF 전체 파싱 → {round_no: {question_no: answer_idx(1~5)}}"""
    result: dict[int, dict[int, int]] = {}
    files = glob.glob(str(ANSWER_DIR / "*.pdf"))

    for path in sorted(files):
        m = re.search(r"(\d{2})회", path)
        if not m:
            continue
        rnd = int(m.group(1))
        try:
            with pdfplumber.open(path) as pdf:
                text = ""
                for page in pdf.pages:
                    t = page.extract_text()
                    if t:
                        text += t
            answers: dict[int, int] = {}
            for match in re.finditer(r"(\d{1,2})\s+([①②③④⑤])", text):
                qno = int(match.group(1))
                ans = CIRCLE_MAP[match.group(2)]
                if 1 <= qno <= 50:
                    answers[qno] = ans
            if answers:
                result[rnd] = answers
        except Exception as e:
            print(f"  [WARN] {rnd}회 정답지 파싱 실패: {e}")

    return result


# ── PDF 파일 찾기 ────────────────────────────────────────────────────────────
def find_pdf(round_no: int) -> Path | None:
    matches = [
        Path(p) for p in glob.glob(str(QUESTION_DIR / "*.pdf"))
        if f"{round_no}회" in p
        and "답지" not in p
        and "해설" not in p
    ]
    if not matches:
        return None
    return sorted(matches, key=lambda p: p.name)[0]


def find_explanation_pdf(round_no: int) -> Path | None:
    """
    해설지 PDF 찾기. 우선순위: cbt > 한pro > 원유철 > 고담한국사 > 공기출
    cbt 형식은 이미지 선택지 레이블이 텍스트로 병기되어 있어 Vision 정확도 높음.
    """
    candidates: list[tuple[int, Path]] = []

    # 해설지 폴더 하위 전체 탐색
    for p in glob.glob(str(EXPLAIN_DIR / "**" / "*.pdf"), recursive=True):
        if f"{round_no}회" in p or f"{round_no}회" in Path(p).name:
            path = Path(p)
            # 우선순위 인덱스 계산
            priority = len(EXPLAIN_PRIORITY)  # 낮을수록 우선
            for i, key in enumerate(EXPLAIN_PRIORITY):
                if key in str(path):
                    priority = i
                    break
            candidates.append((priority, path))

    # 문제지 폴더에 해설 있는 경우 (76, 77회 등)
    for p in glob.glob(str(QUESTION_DIR / "*해설*.pdf")):
        if f"{round_no}회" in p:
            candidates.append((len(EXPLAIN_PRIORITY), Path(p)))

    if not candidates:
        return None
    candidates.sort(key=lambda x: x[0])
    return candidates[0][1]


# ── 페이지 렌더링 → base64 ───────────────────────────────────────────────────
def render_page_b64(pdf_path: Path, page_index: int, scale: float = IMAGE_SCALE) -> str:
    pdf_doc = pdfium.PdfDocument(str(pdf_path))
    page    = pdf_doc[page_index]
    bitmap  = page.render(scale=scale)
    pil_img = bitmap.to_pil()
    buf     = io.BytesIO()
    pil_img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode()


# ── 페이지 추정 ──────────────────────────────────────────────────────────────
def estimate_pages(question_no: int, total_pages: int) -> list[int]:
    """
    한능검 심화 레이아웃 기반 페이지 추정.
    표지 1장 + 문제 페이지 (보통 4~5문항/페이지).
    """
    # 표지=0, 문제 시작=1
    est = 1 + (question_no - 1) // 5
    candidates = []
    for offset in [-1, 0, 1, 2]:
        p = est + offset
        if 0 <= p < total_pages:
            candidates.append(p)
    return candidates


# ── Vision 프롬프트 ──────────────────────────────────────────────────────────
# 해설지용: 이미지 선택지에 이미 레이블 텍스트가 병기되어 있음
EXPLAIN_PROMPT = """이 이미지는 한국사능력검정시험 심화 {round_no}회 해설집 페이지입니다.
해설집에는 문제 원문과 선택지 이미지 아래에 텍스트 레이블(유물명, 지역명 등)이 적혀 있습니다.

이 페이지에서 {question_no}번 문항을 찾아 아래 JSON 형식으로 추출하세요.

반환 형식 (JSON만, 설명 없이):
{{
  "found": true 또는 false,
  "material": "자료 지문 텍스트 그대로. 이미지 자료면 해설집에 표시된 설명 또는 '(이미지: 설명)' 형태로 묘사",
  "question": "발문 텍스트 (예: '다음 설명에 해당하는 문화유산으로 옳은 것은?')",
  "choices": [
    "①번 선택지 — 이미지라면 해설집에 적힌 레이블 텍스트 (예: '백제 금동대향로')",
    "②번 선택지",
    "③번 선택지",
    "④번 선택지",
    "⑤번 선택지"
  ],
  "topic_type": "{topic_types} 중 하나",
  "topic": "구체적 역사 주제 자유 서술",
  "material_type": "{material_types} 중 하나",
  "major_type": "{major_types} 중 하나",
  "minor_type": "{minor_types} 중 하나",
  "question_task": "{question_tasks} 중 하나",
  "difficulty_label": "{difficulty_labels} 중 하나"
}}

choices 추출 규칙:
- ① ② ③ ④ ⑤ 기호 제외하고 텍스트만
- 이미지 선택지: 해설집에 "1. 백제 금동대향로" "2. 가야 기마인물형뿔잔" 식으로 레이블이 적혀 있음 → 그 텍스트를 그대로 사용
- 지도 위치 선택지: "(가)", "(나)" 등 위치 기호 사용
- 5개 없으면 빈 문자열 ""로 채울 것

분류 기준:
- material_type: 지도/유물/그림/사진 → '시각 자료 설명', 사료/문서 → '자료 제시문', 짧은 설명 → '짧은 설명 자료', 연표 → '연표 자료', 사건 순서 → '사건 배열 자료', 탐구활동 → '탐구 자료'
- question_task: 지도 위치 선택 → 'map_location', <보기> ㄱㄴ조합 → 'multi_select_combo', 틀린 것 → 'negative_select', 순서 배열 → 'order', 사이 시기 → 'period_between', 연표 위치 → 'timeline_position', 나머지 → 'standard_select'
- difficulty_label: 배점 1점 → '쉬움', 2점 → '보통', 3점 → '어려움'
- {question_no}번이 이 페이지에 없으면 found: false
"""

# 문제지 + 해설지 동시 사용 프롬프트
DUAL_PROMPT = """첫 번째 이미지는 한국사능력검정시험 심화 {round_no}회 문제지 페이지,
두 번째 이미지는 같은 회차의 해설집 페이지입니다.

{question_no}번 문항을 아래 규칙으로 추출하세요:
- material, question → 첫 번째 이미지(문제지)에서 읽기
- choices → 두 번째 이미지(해설집)에서 읽기
  * 해설집에는 이미지 선택지 아래에 "1. 백제 금동대향로" 같은 텍스트 레이블이 있음
  * 텍스트 선택지는 문제지에서 그대로 읽어도 됨

반환 형식 (JSON만, 설명 없이):
{{
  "found": true 또는 false,
  "material": "문제지의 자료 지문 텍스트. 이미지 자료면 그림 내용 간략 묘사",
  "question": "문제지의 발문 텍스트",
  "choices": [
    "①번 — 해설집 레이블 텍스트 (기호 제외, 예: '백제 금동대향로')",
    "②번",
    "③번",
    "④번",
    "⑤번"
  ],
  "topic_type": "{topic_types} 중 하나",
  "topic": "구체적 역사 주제 자유 서술",
  "material_type": "{material_types} 중 하나",
  "major_type": "{major_types} 중 하나",
  "minor_type": "{minor_types} 중 하나",
  "question_task": "{question_tasks} 중 하나",
  "difficulty_label": "{difficulty_labels} 중 하나"
}}

choices 규칙: ① ② ③ ④ ⑤ 기호 제외, 5개 없으면 "" 채움
분류: map_location(지도위치), multi_select_combo(ㄱㄴ조합), negative_select(틀린것), order(순서), period_between(사이시기), timeline_position(연표위치), standard_select(나머지)
{question_no}번 없으면 found: false
"""

# 문제지 단독 fallback용 (해설지 없을 때)
QUESTION_PROMPT = """이 이미지는 한국사능력검정시험 심화 {round_no}회 문제지 페이지입니다.
이 페이지에서 {question_no}번 문항을 찾아 아래 JSON 형식으로 추출하세요.

반환 형식 (JSON만, 설명 없이):
{{
  "found": true 또는 false,
  "material": "자료 지문 텍스트 또는 이미지 설명 (지도면 한반도 지도 묘사, 유물이면 유물 외형 묘사, 사료면 원문)",
  "question": "발문 텍스트",
  "choices": ["①번 텍스트 (기호 제외)", "②번", "③번", "④번", "⑤번"],
  "topic_type": "{topic_types} 중 하나",
  "topic": "구체적 역사 주제 자유 서술",
  "material_type": "{material_types} 중 하나",
  "major_type": "{major_types} 중 하나",
  "minor_type": "{minor_types} 중 하나",
  "question_task": "{question_tasks} 중 하나",
  "difficulty_label": "{difficulty_labels} 중 하나"
}}

choices 주의: ① ② ③ ④ ⑤ 기호 제외, 이미지 선택지는 보이는 유물/지역 이름으로 묘사, 5개 없으면 "" 채움
분류: material_type — 지도/유물/그림 → '시각 자료 설명', question_task — 지도위치 → 'map_location', 보기조합 → 'multi_select_combo', 틀린것 → 'negative_select', 순서 → 'order', 사이시기 → 'period_between', 연표위치 → 'timeline_position', 나머지 → 'standard_select'
{question_no}번 없으면 found: false
"""


def _build_prompt(template: str, round_no: int, question_no: int) -> str:
    return template.format(
        round_no=round_no,
        question_no=question_no,
        topic_types="/".join(TOPIC_TYPES),
        material_types="/".join(MATERIAL_TYPES),
        major_types="/".join(MAJOR_TYPES),
        minor_types="/".join(MINOR_TYPES),
        question_tasks="/".join(QUESTION_TASKS),
        difficulty_labels="/".join(DIFFICULTY_LABELS),
    )


def extract_question_vision(
    client: OpenAI,
    round_no: int,
    question_no: int,
    q_pdf: Path,
    q_page_indices: list[int],
    ex_pdf: Path | None = None,
    ex_page_indices: list[int] | None = None,
) -> dict | None:
    """
    문제지 페이지 + 해설지 페이지를 함께 Vision에 전송.
    - 문제지: material(지문/자료), question(발문) 추출
    - 해설지: choices 이미지 레이블 추출
    해설지 없으면 문제지 단독으로 처리.
    """
    use_dual = ex_pdf is not None and ex_page_indices

    # 페이지 조합 순회: (문제지 페이지, 해설지 페이지 or None)
    pairs: list[tuple[int, int | None]] = []
    if use_dual:
        for qp in q_page_indices:
            for ep in (ex_page_indices or []):
                pairs.append((qp, ep))
    else:
        for qp in q_page_indices:
            pairs.append((qp, None))

    prompt_text = _build_prompt(
        DUAL_PROMPT if use_dual else QUESTION_PROMPT,
        round_no, question_no,
    )

    for q_page, ex_page in pairs:
        try:
            q_b64 = render_page_b64(q_pdf, q_page)
        except Exception as e:
            print(f"      [WARN] 문제지 p{q_page+1} 렌더 실패: {e}")
            continue

        content: list[dict] = [{"type": "text", "text": prompt_text}]
        content.append({"type": "image_url", "image_url": {
            "url": f"data:image/png;base64,{q_b64}", "detail": "high",
        }})

        if ex_page is not None:
            try:
                ex_b64 = render_page_b64(ex_pdf, ex_page)
                content.append({"type": "image_url", "image_url": {
                    "url": f"data:image/png;base64,{ex_b64}", "detail": "high",
                }})
            except Exception as e:
                print(f"      [WARN] 해설지 p{ex_page+1} 렌더 실패: {e}")

        try:
            resp = create_chat_completion(
                client,
                model=get_model(),
                messages=[{"role": "user", "content": content}],
                response_format={"type": "json_object"},
                temperature=0,
                max_tokens=2000,
            )
            data = json.loads(resp.choices[0].message.content)
            if data.get("found"):
                return data
        except Exception as e:
            print(f"      [WARN] Vision 실패: {e}")
            continue

    return None


# ── 결과를 han_cj 형식으로 변환 ──────────────────────────────────────────────
def to_han_cj_item(
    round_no: int,
    question_no: int,
    extracted: dict,
    answer_idx: int,
    problem_id: str,
    source_group_index: int,
) -> dict:
    choices_text = extracted.get("choices", [])
    # 5개 보장
    while len(choices_text) < 5:
        choices_text.append("")

    answer_choice    = choices_text[answer_idx - 1] if answer_idx >= 1 else ""
    distractor_list  = [c for i, c in enumerate(choices_text, 1) if i != answer_idx]

    choices_full = [
        {"is_answer": (i + 1 == answer_idx), "content": c}
        for i, c in enumerate(choices_text)
    ]

    material = extracted.get("material", "")
    question = extracted.get("question", "")
    input_text = f"{material}\n{question}".strip()

    return {
        "problem_id":          problem_id,
        "source_group_index":  source_group_index,
        "material":     material,
        "question":           question,
        "input_text":         input_text,
        "answer_choice":      answer_choice,
        "distractor_choices": distractor_list,
        "topic_type":         extracted.get("topic_type", "기타") if extracted.get("topic_type", "기타") in TOPIC_TYPES else "기타",
        "topic":              extracted.get("topic", ""),
        "material_type":      extracted.get("material_type", MATERIAL_TYPES[0]) if extracted.get("material_type", "") in MATERIAL_TYPES else MATERIAL_TYPES[0],
        "major_type":         extracted.get("major_type", MAJOR_TYPES[0]) if extracted.get("major_type", "") in MAJOR_TYPES else MAJOR_TYPES[0],
        "minor_type":         extracted.get("minor_type", MINOR_TYPES[0]) if extracted.get("minor_type", "") in MINOR_TYPES else MINOR_TYPES[0],
        "question_task":      extracted.get("question_task", "standard_select") if extracted.get("question_task", "") in QUESTION_TASKS else "standard_select",
        "difficulty_label":   extracted.get("difficulty_label", "보통") if extracted.get("difficulty_label", "") in DIFFICULTY_LABELS else "보통",
        "choices":            choices_full,
        "choice_count":       5,
        "distractor_count":   4,
        # 추적용 extra 필드
        "round_no":           round_no,
        "question_no":        question_no,
    }


# ── 저장/로드 ────────────────────────────────────────────────────────────────
def load_existing() -> dict[tuple[int, int], dict]:
    """기존 output JSON 로드 → {(round_no, question_no): item}"""
    if not OUTPUT_JSON.exists():
        return {}
    try:
        items = json.loads(OUTPUT_JSON.read_text(encoding="utf-8"))
        return {(it["round_no"], it["question_no"]): it for it in items}
    except Exception as e:
        print(f"[WARN] 기존 출력 로드 실패: {e}")
        return {}


def save_output(items: dict[tuple[int, int], dict]) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    sorted_items = [v for k, v in sorted(items.items())]
    OUTPUT_JSON.write_text(
        json.dumps(sorted_items, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"  → 저장: {OUTPUT_JSON}  ({len(sorted_items)}개)")


# ── source_group_index 계산 ──────────────────────────────────────────────────
def get_source_group_index(round_no: int, question_no: int) -> int:
    """같은 회차 기존 문항 수 + 이미지 문항 내 순서."""
    try:
        existing = json.loads(SOURCE_JSON.read_text(encoding="utf-8"))
        # problem_id에서 회차 추출 (예: "cj_v41_47_01" → 47)
        same_round = [
            it for it in existing
            if str(round_no) in str(it.get("problem_id", ""))
        ]
        base = len(same_round)
    except Exception:
        base = 0
    missing_in_round = sorted(MISSING_QUESTIONS.get(round_no, []))
    try:
        order = missing_in_round.index(question_no)
    except ValueError:
        order = 0
    return base + order


# ── 메인 ────────────────────────────────────────────────────────────────────
def main() -> None:
    load_dotenv()

    parser = argparse.ArgumentParser(description="이미지 문항 GPT Vision 추출")
    parser.add_argument("--rounds", type=int, nargs="+",
                        help="처리할 회차 지정 (기본: 전체)")
    parser.add_argument("--force", action="store_true",
                        help="이미 추출된 항목도 재처리")
    args = parser.parse_args()

    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        print("[ERROR] OPENAI_API_KEY 없음. .env 파일을 확인하세요.")
        sys.exit(1)

    client = OpenAI(api_key=api_key)
    model  = get_model()
    print(f"[INFO] 모델: {model}")

    # 정답지 로드
    print("[INFO] 정답지 파싱 중...")
    answers = parse_answer_sheets()
    print(f"  → {len(answers)}개 회차 파싱 완료")

    # 기존 결과 로드
    existing = load_existing()
    print(f"[INFO] 기존 결과: {len(existing)}개")

    # 처리 대상 회차 결정
    target_rounds = args.rounds if args.rounds else sorted(MISSING_QUESTIONS.keys())

    total_ok   = 0
    total_fail = 0

    for rnd in target_rounds:
        if rnd not in MISSING_QUESTIONS:
            print(f"[SKIP] {rnd}회: 누락 문항 없음")
            continue

        q_pdf = find_pdf(rnd)
        if not q_pdf:
            print(f"[WARN] {rnd}회 문제지 PDF 없음 — 스킵")
            continue

        ex_pdf    = find_explanation_pdf(rnd)
        ex_label  = ex_pdf.parent.name if ex_pdf else "없음"
        print(f"\n[{rnd}회] 문제지: {q_pdf.name}  해설지: {ex_label}")

        try:
            q_doc    = pdfium.PdfDocument(str(q_pdf))
            q_total  = len(q_doc)
            ex_total = len(pdfium.PdfDocument(str(ex_pdf))) if ex_pdf else 0
        except Exception as e:
            print(f"  [WARN] PDF 열기 실패: {e}")
            continue

        for qno in sorted(MISSING_QUESTIONS[rnd]):
            key = (rnd, qno)
            if key in existing and not args.force:
                print(f"  [{rnd}-{qno:02d}] 이미 처리됨 — 스킵")
                total_ok += 1
                continue

            answer_idx = answers.get(rnd, {}).get(qno)
            if answer_idx is None:
                print(f"  [{rnd}-{qno:02d}] 정답 없음 — 스킵")
                total_fail += 1
                continue

            print(f"  [{rnd}-{qno:02d}] 추출 중... (정답: {answer_idx}번)", end=" ", flush=True)

            q_pages  = estimate_pages(qno, q_total)
            ex_pages = estimate_pages(qno, ex_total) if ex_pdf else []

            extracted = None

            # ─ Tier 1: 문제지 추정 페이지 + 해설지 추정 페이지 (Dual)
            if ex_pdf and ex_pages:
                extracted = extract_question_vision(
                    client, rnd, qno, q_pdf, q_pages, ex_pdf, ex_pages,
                )

            # ─ Tier 2: 문제지 추정 페이지 + 해설지 전 페이지 스캔
            if extracted is None and ex_pdf:
                print("(T2)", end=" ", flush=True)
                all_ex_pages = list(range(ex_total))
                extracted = extract_question_vision(
                    client, rnd, qno, q_pdf, q_pages, ex_pdf, all_ex_pages,
                )

            # ─ Tier 3: 문제지 단독 fallback
            if extracted is None:
                print("(T3)", end=" ", flush=True)
                extracted = extract_question_vision(
                    client, rnd, qno, q_pdf, q_pages,
                )

            if extracted is None:
                print("FAIL")
                total_fail += 1
                continue

            problem_id         = f"cj_v41_img_{rnd:02d}_{qno:02d}"
            source_group_index = get_source_group_index(rnd, qno)

            item = to_han_cj_item(
                rnd, qno, extracted, answer_idx,
                problem_id, source_group_index,
            )
            existing[key] = item
            save_output(existing)

            print("OK")
            total_ok += 1

    print(f"\n[완료] 성공: {total_ok}개 / 실패: {total_fail}개")
    print(f"[출력] {OUTPUT_JSON}")


if __name__ == "__main__":
    main()
