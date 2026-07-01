"""
ML Feature Data Extractor
한능검 기출 PDF에서 ML 피처 생성에 필요한 데이터를 추출한다.

자동 감지:
  - 텍스트 PDF → pdfplumber 추출 + GPT 텍스트 분류 (회차당 1회 호출)
  - 이미지 PDF → pypdfium2로 렌더링 + GPT Vision 분류 (페이지당 1회 호출)

출력: test/CJ/test_ml/output/ml_raw_data.csv
컬럼: round_no, question_no, era, topic, question_type, question_subtype, core_concept

Usage:
  python test/CJ/test_ml/ML_feature_data.py
  python test/CJ/test_ml/ML_feature_data.py --rounds 47 48 49
  python test/CJ/test_ml/ML_feature_data.py --rounds 47 --force
  python test/CJ/test_ml/ML_feature_data.py --rounds 47 --source vision
  python test/CJ/test_ml/ML_feature_data.py --rounds 47 --repair-missing
"""

from __future__ import annotations

import argparse
import base64
import csv
import io
import json
import os
import time
from pathlib import Path

import pdfplumber
import pypdfium2 as pdfium
from openai import OpenAI

# ── 경로 설정 ──────────────────────────────────────────────────────────────
ROOT_DIR      = Path(__file__).resolve().parents[3]   # SKN27-FINAL-2Team/
DOCS_DIR      = ROOT_DIR / "test" / "CJ" / "test_docs"
QUESTION_DIR  = DOCS_DIR / "1. 문제지"
OUT_DIR       = Path(__file__).resolve().parent / "output"
OUTPUT_CSV    = OUT_DIR / "ml_raw_data.csv"
REFERENCE_PATH = Path(__file__).resolve().parent / "era_reference.json"

DEFAULT_ROUNDS = list(range(47, 79))   # 47 ~ 78회

# ── 허용 값 목록 ────────────────────────────────────────────────────────────
ERA_VALUES = [
    "선사 시대", "고조선", "초기 국가", "삼국 시대", "남북국 시대",
    "고려", "조선 전기", "조선 후기", "개항기", "일제 강점기", "현대",
]

TOPIC_VALUES = [
    "정치", "경제", "사회", "문화", "인물", "군사",
    "외교", "사상·종교", "제도", "사건",
]

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
    "사료",
    "연표",
    "인물",
    "지역",
    "지도",
    "유물",
    "제도",
    "사건",
]

CSV_FIELDNAMES = [
    "round_no",
    "question_no",
    "era",
    "topic",
    "question_type",
    "question_subtype",
    "core_concept",
]

TEXT_MIN_CHARS  = 1000   # 이 이하면 이미지 PDF로 판단
TEXT_MAX_CHARS  = 14000  # GPT에 보낼 텍스트 최대 길이
IMAGE_SCALE     = 2.0    # PDF 렌더링 배율
PAGES_PER_BATCH = 1      # 페이지 1장씩 처리 (속도보다 정확도 우선)
API_MAX_RETRIES = 4
OPENAI_MODEL_ENV = "OPENAI_CLASSIFY_MODEL"

MANUAL_ERA_OVERRIDES = {
    "성호사설": "조선 후기",
    "성호 이익": "조선 후기",
    "운요호": "개항기",
    "강화도조약": "개항기",
    "외규장각": "조선 후기",
    "규장각": "조선 후기",
    "병인양요": "조선 후기",
    "신미양요": "조선 후기",
    "대동법": "조선 후기",
    "공주명학소": "고려",
    "명학소": "고려",
    "망이": "고려",
    "망소이": "고려",
    "광평성": "고려",
    "태봉": "고려",
    "후고구려": "고려",
    "흥선대원군": "개항기",
    "대원군": "개항기",
}

ERA_ORDER_HINT = """
[한능검 심화 시대 배열 순서 — 문항 번호가 올라갈수록 아래 순서를 따름]
  선사 시대 → 고조선 → 초기 국가 → 삼국 시대 → 남북국 시대
  → 고려 → 조선 전기 → 조선 후기 → 개항기 → 일제 강점기 → 현대
  예) 1~5번 = 선사~초기국가, 14~20번 = 고려, 21~30번 = 조선 전기,
      31~37번 = 조선 후기, 38~40번 = 개항기, 41~46번 = 일제 강점기, 47~50번 = 현대
  (단, 회차마다 범위가 다르므로 실제 문제 내용을 우선 판단하세요)
"""

# 실행 시 load_reference()로 채워짐
ERA_REFERENCE_TEXT: str = ""


# ── 레퍼런스 로딩 ────────────────────────────────────────────────────────────
def load_reference() -> str:
    if not REFERENCE_PATH.exists():
        return ""
    ref = json.loads(REFERENCE_PATH.read_text(encoding="utf-8"))
    lines = ["[시대별 핵심 키워드 — 분류 기준으로 반드시 참조]"]
    for era, keywords in ref.get("era_keywords", {}).items():
        lines.append(f"  {era}: {', '.join(keywords[:20])}")
    lines.append("")
    lines.append("[주제별 핵심 키워드]")
    for topic, keywords in ref.get("topic_keywords", {}).items():
        lines.append(f"  {topic}: {', '.join(keywords[:10])}")
    return "\n".join(lines)


def get_openai_model() -> str:
    return os.getenv(OPENAI_MODEL_ENV, "gpt-4o")


def parse_items_from_response(content: str | None) -> list[dict]:
    if not content:
        return []
    data = json.loads(content)
    items = data.get("items", [])
    return items if isinstance(items, list) else []


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
            print(f"      [Rate limit] {wait}초 대기 후 재시도 ({attempt + 1}/{API_MAX_RETRIES})...")
            time.sleep(wait)


# ── GPT 분류 공통 suffix ────────────────────────────────────────────────────
def build_classify_suffix() -> str:
    return f"""
{ERA_ORDER_HINT}

시대는 정답/오답 선택지보다 지문, 자료, 발문이 묻는 실제 시대 배경을 우선하세요.
'옳지 않은 것', '틀린 것' 문제도 틀린 선택지의 시대가 아니라 지문과 발문이 설명하는 시대를 기준으로 분류하세요.
선택지에 여러 시대가 섞이면 선택지 키워드보다 지문과 문제 대상 인물/사건/제도/작품을 우선하세요.
core_concept도 선택지 키워드가 아니라 지문과 발문이 묻는 핵심 인물/사건/제도/작품으로 작성하세요.

이미지에 보이는 모든 문항을 아래 기준으로 분류하세요.
지도·사료 이미지가 있는 페이지도 반드시 해당 문항을 분류하세요.
한 회차 전체를 처리하는 경우 1번부터 50번까지 빠짐없이 반환하세요.
확신이 낮아도 허용값 중 가장 가까운 하나를 선택하고, 임의의 새 라벨은 만들지 마세요.
core_concept에는 "문제", "자료", "시기", "상황", "인물", "정책" 같은 일반어를 쓰지 말고 가장 구체적인 역사 용어를 쓰세요.

분류 기준:
- era: 반드시 아래 11개 중 정확히 하나 선택 (시대별 키워드를 참고하세요)
  {ERA_VALUES}
- topic: 반드시 아래 10개 중 정확히 하나 선택
  {TOPIC_VALUES}
- question_type: 반드시 아래 6개 중 정확히 하나 선택
  {QUESTION_TYPES}
- question_subtype: 반드시 아래 9개 중 정확히 하나 선택
  {QUESTION_SUBTYPES}
  기준: 원문 사료를 읽는 문제는 사료, 연도·순서를 묻는 문제는 연표, 지도 위치를 묻는 문제는 지도,
  유물·유적·작품 이미지를 해석하는 문제는 유물, 특정 인물을 묻는 문제는 인물,
  지역·장소를 묻는 문제는 지역, 제도·법·기구를 묻는 문제는 제도, 사건 전개를 묻는 문제는 사건,
  나머지 기본 개념 확인 문제는 개념으로 분류하세요.
- core_concept: 문제에서 다루는 구체적인 역사 용어 1개
  예시: 광종, 세도정치, 3·1운동, 훈민정음, 과전법, 갑신정변, 청산리대첩, 강화도조약, 동학농민운동
  (절대 "사이", "시기", "내용", "상황", "인물", "제도" 같은 일반 단어 사용 금지)

era 분류 주의사항:
- 강화도조약, 위정척사, 갑신정변, 동학농민운동, 대한제국 → 개항기
- 3·1운동, 독립군, 신간회, 임시정부, 창씨개명, 민족말살정책 → 일제 강점기
- 목민심서, 실학, 탕평책, 세도정치, 홍경래의 난 → 조선 후기
- 훈민정음, 경국대전, 과전법, 집현전, 4군 6진 → 조선 전기
- 불교, 팔만대장경, 무신정권, 쌍성총관부, 공민왕 → 고려
- 발해, 통일신라, 장보고, 원효, 의상 → 남북국 시대
- 고구려·백제·신라·가야, 화랑도, 삼국통일 → 삼국 시대
- 8조법, 고조선, 단군왕검, 위만조선 → 고조선
- 부여·옥저·동예·삼한, 소도, 천군 → 초기 국가
- 구석기·신석기·청동기·철기, 빗살무늬토기, 고인돌 → 선사 시대

JSON만 반환 (설명 없이):
{{
  "items": [
    {{"question_no": 1, "era": "...", "topic": "...", "question_type": "...", "question_subtype": "...", "core_concept": "..."}},
    ...
  ]
}}
"""


# ── 환경 변수 로드 ──────────────────────────────────────────────────────────
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


# ── PDF 탐색 ────────────────────────────────────────────────────────────────
def find_pdf(round_no: int) -> Path | None:
    if not QUESTION_DIR.exists():
        return None
    matches = [
        p for p in QUESTION_DIR.glob("*.pdf")
        if (
            f"{round_no}회" in p.name
            and "문제지" in p.name
            and "답지" not in p.name
            and "해설" not in p.name
        )
    ]
    if not matches:
        return None
    matches.sort(key=lambda p: p.name)
    return matches[0]


# ── 텍스트 추출 ─────────────────────────────────────────────────────────────
def extract_text(pdf_path: Path) -> str:
    pages: list[str] = []
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            text = page.extract_text()
            if text:
                pages.append(text)
    return "\n".join(pages)


# ── 이미지 PDF: 페이지 렌더링 → base64 ──────────────────────────────────────
def render_page_b64(pdf_path: Path, page_index: int, scale: float = IMAGE_SCALE) -> str:
    pdf_doc = pdfium.PdfDocument(str(pdf_path))
    page    = pdf_doc[page_index]
    bitmap  = page.render(scale=scale)
    pil_img = bitmap.to_pil()
    buf     = io.BytesIO()
    pil_img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode()


# ── GPT 분류: 텍스트 기반 ───────────────────────────────────────────────────
def classify_text(round_no: int, text: str) -> list[dict]:
    client = OpenAI()
    prompt = (
        f"다음은 한국사능력검정시험 {round_no}회 심화 문제지 텍스트입니다.\n\n"
        + ERA_REFERENCE_TEXT + "\n\n"
        + build_classify_suffix()
        + f"\n문제지 텍스트:\n{text[:TEXT_MAX_CHARS]}"
    )
    resp = create_chat_completion(
        client,
        model=get_openai_model(),
        messages=[{"role": "user", "content": prompt}],
        response_format={"type": "json_object"},
        temperature=0,
    )
    return parse_items_from_response(resp.choices[0].message.content)


def classify_missing_text(round_no: int, text: str, question_numbers: list[int]) -> list[dict]:
    client = OpenAI()
    target_numbers = ", ".join(str(n) for n in question_numbers)
    prompt = (
        f"다음은 한국사능력검정시험 {round_no}회 심화 문제지 텍스트입니다.\n"
        f"기존 분류 결과에서 {target_numbers}번 문항이 누락되었습니다.\n"
        "아래 문제지 텍스트에서 누락된 문항만 찾아 분류하세요.\n"
        "반드시 요청한 문항 번호만 반환하고, 찾기 어려워도 주변 문항 흐름과 문제 내용을 근거로 가장 가까운 값을 선택하세요.\n\n"
        + ERA_REFERENCE_TEXT + "\n\n"
        + build_classify_suffix()
        + f"\n분류 대상 문항 번호: {target_numbers}\n"
        + f"\n문제지 텍스트:\n{text[:TEXT_MAX_CHARS]}"
    )
    resp = create_chat_completion(
        client,
        model=get_openai_model(),
        messages=[{"role": "user", "content": prompt}],
        response_format={"type": "json_object"},
        temperature=0,
    )
    items = parse_items_from_response(resp.choices[0].message.content)
    target_set = set(question_numbers)
    return [item for item in items if int(item.get("question_no", 0) or 0) in target_set]


# ── GPT 분류: Vision 기반 (페이지 1장) ──────────────────────────────────────
def classify_vision_page(
    round_no: int,
    pdf_path: Path,
    page_index: int,
    q_hint: tuple[int, int] | None = None,
) -> list[dict]:
    client = OpenAI()
    hint_text = (
        f"이 이미지에는 약 {q_hint[0]}번 ~ {q_hint[1]}번 문항이 포함되어 있습니다. "
        if q_hint else ""
    )
    content: list[dict] = [
        {
            "type": "text",
            "text": (
                f"다음은 한국사능력검정시험 {round_no}회 심화 문제지 이미지입니다. {hint_text}\n\n"
                + ERA_REFERENCE_TEXT + "\n\n"
                + build_classify_suffix()
            ),
        },
        {
            "type": "image_url",
            "image_url": {
                "url": f"data:image/png;base64,{render_page_b64(pdf_path, page_index)}",
                "detail": "high",
            },
        },
    ]
    resp = create_chat_completion(
        client,
        model=get_openai_model(),
        messages=[{"role": "user", "content": content}],
        response_format={"type": "json_object"},
        temperature=0,
        max_tokens=2000,
    )
    return parse_items_from_response(resp.choices[0].message.content)


# ── GPT 분류: Vision 기반 (이미지 PDF 전체) ─────────────────────────────────
def classify_vision(round_no: int, pdf_path: Path) -> list[dict]:
    pdf_doc    = pdfium.PdfDocument(str(pdf_path))
    total      = len(pdf_doc)
    all_pages  = list(range(0, total))
    q_per_page = 50 / max(1, len(all_pages))

    all_items: list[dict] = []
    for i, page_idx in enumerate(all_pages):
        est_start = max(1, round(i * q_per_page) + 1)
        est_end   = min(50, round((i + 1) * q_per_page))
        q_hint    = (est_start, est_end)

        print(f"    페이지 {page_idx} → 예상 {est_start}~{est_end}번")

        for attempt in range(3):
            try:
                items = classify_vision_page(round_no, pdf_path, page_idx, q_hint)
                all_items.extend(items)
                print(f"      → {len(items)}문항 분류됨")
                break
            except Exception as e:
                msg = str(e)
                if "429" in msg and attempt < 2:
                    wait = (attempt + 1) * 10
                    print(f"      [Rate limit] {wait}초 대기 후 재시도 ({attempt + 1}/3)...")
                    time.sleep(wait)
                else:
                    print(f"      [WARN] 실패: {e}")
                    break

        time.sleep(2.0)

    return all_items


# ── 분류값 정규화 ────────────────────────────────────────────────────────────
def normalize(value: str, allowed: list[str], fallback: str) -> str:
    v = str(value or "").strip()
    if v in allowed:
        return v
    v_nospace = v.replace(" ", "")
    for a in allowed:
        if a.replace(" ", "") == v_nospace:
            return a
    for a in allowed:
        if a in v or v in a:
            return a
    return fallback


def reference_era_for_core(core_concept: str) -> str | None:
    core = str(core_concept or "").replace(" ", "")
    if not core:
        return None

    for keyword, era in MANUAL_ERA_OVERRIDES.items():
        if keyword.replace(" ", "") in core:
            return era

    if not REFERENCE_PATH.exists():
        return None

    reference = json.loads(REFERENCE_PATH.read_text(encoding="utf-8"))
    candidates: list[tuple[int, str]] = []
    for era, keywords in reference.get("era_keywords", {}).items():
        for keyword in keywords:
            normalized_keyword = str(keyword).replace(" ", "")
            if normalized_keyword and normalized_keyword in core:
                candidates.append((len(normalized_keyword), era))

    if not candidates:
        return None
    candidates.sort(reverse=True)
    return candidates[0][1]


def normalize_era(value: str, core_concept: str) -> str:
    reference_era = reference_era_for_core(core_concept)
    if reference_era in ERA_VALUES:
        return reference_era
    return normalize(value, ERA_VALUES, "미분류")


# ── 기존 처리 회차 조회 ─────────────────────────────────────────────────────
def load_done_rounds(csv_path: Path) -> set[int]:
    if not csv_path.exists():
        return set()
    done: set[int] = set()
    with open(csv_path, encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            try:
                done.add(int(row["round_no"]))
            except (KeyError, ValueError):
                pass
    return done


# ── CSV 저장 ────────────────────────────────────────────────────────────────
def delete_round_from_csv(csv_path: Path, round_no: int) -> None:
    if not csv_path.exists():
        return
    rows: list[dict] = []
    with open(csv_path, encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        rows = [
            {field: r.get(field, "") for field in CSV_FIELDNAMES}
            for r in reader
            if int(r.get("round_no", 0)) != round_no
        ]
    with open(csv_path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)


def append_rows(csv_path: Path, rows: list[dict]) -> None:
    is_new = not csv_path.exists()
    with open(csv_path, "a", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDNAMES)
        if is_new:
            writer.writeheader()
        writer.writerows({field: row.get(field, "") for field in CSV_FIELDNAMES} for row in rows)


# ── 결과 정제 ───────────────────────────────────────────────────────────────
def build_rows(round_no: int, items: list[dict]) -> list[dict]:
    seen: set[int] = set()
    rows: list[dict] = []
    for item in items:
        try:
            q_no = int(item.get("question_no", 0))
        except (TypeError, ValueError):
            continue
        if not (1 <= q_no <= 50):
            continue
        if q_no in seen:
            continue
        seen.add(q_no)
        core_concept = str(item.get("core_concept", "")).strip()
        row = {
            "round_no":       round_no,
            "question_no":    q_no,
            "era":            normalize_era(item.get("era"), core_concept),
            "topic":          normalize(item.get("topic"),         TOPIC_VALUES,   "정치"),
            "question_type":  normalize(item.get("question_type"), QUESTION_TYPES, "역사 지식의 이해"),
            "question_subtype": normalize(item.get("question_subtype"), QUESTION_SUBTYPES, "개념"),
            "core_concept":   core_concept,
        }
        rows.append(row)
    rows.sort(key=lambda r: r["question_no"])
    return rows


def missing_question_numbers(rows: list[dict]) -> list[int]:
    existing = {int(row["question_no"]) for row in rows}
    return [q for q in range(1, 51) if q not in existing]


def merge_rows(primary_rows: list[dict], supplement_rows: list[dict]) -> list[dict]:
    merged = {int(row["question_no"]): row for row in primary_rows}
    for row in supplement_rows:
        merged.setdefault(int(row["question_no"]), row)
    return [merged[q] for q in sorted(merged)]


def print_quality_report(round_no: int, rows: list[dict]) -> None:
    missing = missing_question_numbers(rows)
    if missing:
        print(f"  [CHECK] {round_no}회 누락 문항: {missing}")
    else:
        print(f"  [CHECK] {round_no}회 50문항 완료")

    era_counts: dict[str, int] = {}
    topic_counts: dict[str, int] = {}
    for row in rows:
        era_counts[row["era"]] = era_counts.get(row["era"], 0) + 1
        topic_counts[row["topic"]] = topic_counts.get(row["topic"], 0) + 1
    print(f"  [ERA] {era_counts}")
    print(f"  [TOPIC] {topic_counts}")


# ── 단일 회차 처리 ──────────────────────────────────────────────────────────
def process_round(round_no: int, source: str, repair_missing: bool) -> list[dict] | None:
    pdf_path = find_pdf(round_no)
    if pdf_path is None:
        print(f"  [SKIP] PDF 없음")
        return None

    text = extract_text(pdf_path)

    if source == "vision":
        pdf_doc = pdfium.PdfDocument(str(pdf_path))
        n_pages = len(pdf_doc)
        print(f"  [VISION] 강제 Vision 분류 ({n_pages}페이지)")
        items = classify_vision(round_no, pdf_path)
    elif source == "text" or len(text.strip()) >= TEXT_MIN_CHARS:
        print(f"  [TEXT] {len(text)}chars → GPT 텍스트 분류")
        items = classify_text(round_no, text)
    else:
        pdf_doc = pdfium.PdfDocument(str(pdf_path))
        n_pages = len(pdf_doc)
        print(f"  [IMAGE] 텍스트 없음 → Vision 분류 ({n_pages}페이지)")
        items = classify_vision(round_no, pdf_path)

    rows = build_rows(round_no, items)

    if repair_missing and missing_question_numbers(rows):
        missing = missing_question_numbers(rows)
        print(f"  [REPAIR] 누락 문항 텍스트 보강: {missing}")
        missing_rows = build_rows(round_no, classify_missing_text(round_no, text, missing))
        rows = merge_rows(rows, missing_rows)

    if repair_missing and missing_question_numbers(rows):
        print("  [REPAIR] 남은 누락 문항 보강을 위해 Vision 재분류를 실행합니다.")
        vision_rows = build_rows(round_no, classify_vision(round_no, pdf_path))
        rows = merge_rows(rows, vision_rows)

    print_quality_report(round_no, rows)
    return rows if rows else None


# ── 메인 ────────────────────────────────────────────────────────────────────
def main() -> None:
    parser = argparse.ArgumentParser(description="ML 피처용 한능검 기출 데이터 추출")
    parser.add_argument("--rounds", nargs="+", type=int, default=DEFAULT_ROUNDS,
                        help="처리할 회차 번호 (기본: 47~78)")
    parser.add_argument("--force", action="store_true",
                        help="이미 처리된 회차도 재처리 (기존 데이터 삭제 후 덮어쓰기)")
    parser.add_argument("--source", choices=["auto", "text", "vision"], default="auto",
                        help="분류 입력 방식 선택 (기본: auto, 정확도 우선: vision)")
    parser.add_argument("--repair-missing", action="store_true",
                        help="텍스트 분류 후 누락 문항이 있으면 Vision으로 보강")
    args = parser.parse_args()

    load_dotenv()
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    global ERA_REFERENCE_TEXT
    ERA_REFERENCE_TEXT = load_reference()
    if ERA_REFERENCE_TEXT:
        print(f"레퍼런스 로딩 완료 ({len(ERA_REFERENCE_TEXT)} chars)\n")
    else:
        print("[WARN] era_reference.json 없음 — 레퍼런스 없이 실행\n")
    print(f"OpenAI model: {get_openai_model()}")

    done_rounds = load_done_rounds(OUTPUT_CSV)
    targets = args.rounds if args.force else [r for r in args.rounds if r not in done_rounds]

    if not targets:
        print("처리할 회차가 없습니다. (--force 옵션으로 재처리 가능)")
        return

    skipped = [r for r in args.rounds if r in done_rounds and not args.force]
    if skipped:
        print(f"이미 처리된 회차 (스킵): {skipped}")
    print(f"처리 대상: {targets}")
    print(f"출력: {OUTPUT_CSV}\n")

    for round_no in targets:
        print(f"[{round_no}회] 시작")
        try:
            if args.force:
                delete_round_from_csv(OUTPUT_CSV, round_no)
            rows = process_round(round_no, args.source, args.repair_missing)
            if rows:
                append_rows(OUTPUT_CSV, rows)
                print(f"  완료: {len(rows)}문항 저장\n")
            else:
                print(f"  [WARN] 결과 없음\n")
        except Exception as e:
            print(f"  [ERROR] {type(e).__name__}: {e}\n")
        time.sleep(1.0)

    done = load_done_rounds(OUTPUT_CSV)
    print(f"전체 완료. 처리 회차: {sorted(done)} ({len(done)}회차)")


if __name__ == "__main__":
    main()
