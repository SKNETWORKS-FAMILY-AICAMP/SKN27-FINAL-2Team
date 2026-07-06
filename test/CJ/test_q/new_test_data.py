"""
ML_han_v1.json에서 74~77회차 문항만 골라 DB 적재용 seed 데이터를 만든다.
공기출 해설 PDF의 [짧은해설] 텍스트를 보조 정보로 붙여 시대/주제 분류 품질을 높인다.
기본 실행은 API 비용이 없고, --classify-openai 옵션을 줄 때만 OpenAI API로 era/topic을 재분류한다.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import time
from pathlib import Path
from typing import Any

def load_dotenv_file(path: Path) -> None:
    if not path.exists():
        return

    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        os.environ.setdefault(
            key.strip(),
            value.strip().strip('"').strip("'"),
        )
        
ROOT_DIR = Path(__file__).resolve().parents[3]
load_dotenv_file(ROOT_DIR / ".env")
ML_JSON = ROOT_DIR / "ai" / "ml" / "ML_han_v1.json"
EXPLANATION_DIR = ROOT_DIR / "test" / "CJ" / "test_docs" / "2. 해설지" / "공기출"
ANSWER_DIR = ROOT_DIR / "test" / "CJ" / "test_docs" / "4. 정답지"
LEGACY_OUTPUT_DIR = ROOT_DIR / "test" / "CJ" / "test_q" / "output_exam"
OUT_DIR = ROOT_DIR / "test" / "CJ" / "test_q" / "output_new_test_data"

DEFAULT_ROUNDS = [74, 75, 76, 77]
QUESTION_COUNT = 50

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
    "사건",
    "인물",
    "정치",
    "제도",
    "문화",
    "사회",
    "군사",
    "경제",
    "사상·종교",
    "외교",
]

ERA_ALIASES = {
    "조선 전기": "조선",
    "조선 후기": "조선",
    "조선전기": "조선",
    "조선후기": "조선",
}

TOPIC_ALIASES = {
    "사상 종교": "사상·종교",
    "사상, 종교": "사상·종교",
    "사상ㆍ종교": "사상·종교",
    "종교": "사상·종교",
    "지역": "사건",
    "통합": "사건",
    "통합 주제": "사건",
}

CHOICE_MARKERS = {
    "①": 1,
    "②": 2,
    "③": 3,
    "④": 4,
    "⑤": 5,
    "➀": 1,
    "➁": 2,
    "➂": 3,
    "➃": 4,
    "➄": 5,
    "❶": 1,
    "❷": 2,
    "❸": 3,
    "❹": 4,
    "❺": 5,
}

ANSWER_MARKERS = {
    "①": 1,
    "②": 2,
    "③": 3,
    "④": 4,
    "⑤": 5,
    "➀": 1,
    "➁": 2,
    "➂": 3,
    "➃": 4,
    "➄": 5,
}


# JSON 파일을 UTF-8로 읽어 파이썬 객체로 반환한다.
# 파일이 없거나 JSON 형식이 깨져 있으면 즉시 예외가 나도록 두어
# 전처리 결과를 조용히 잘못 만들지 않게 한다.
def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


# 전처리 결과를 UTF-8 JSON 파일로 저장한다.
# 한글 라벨과 해설이 깨지지 않도록 ensure_ascii=False를 사용하고,
# 상위 폴더가 없으면 자동으로 생성한다.
def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


# 값의 앞뒤 공백을 제거하고 빈 값은 빈 문자열로 통일한다.
# PDF나 기존 JSON에서 None, 숫자, 리스트가 섞여 들어올 수 있어
# 모든 입력을 안전하게 문자열로 정규화한다.
def clean_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        return " ".join(clean_text(item) for item in value if clean_text(item))
    text = str(value).replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


# 라벨 문자열을 허용 목록에 맞게 보정한다.
# 조선 전기/후기처럼 팀 기준에서 사라진 라벨은 조선으로 합치고,
# 허용 목록에 없는 값은 fallback으로 처리한다.
def normalize_label(value: Any, allowed: list[str], aliases: dict[str, str], fallback: str) -> str:
    text = clean_text(value)
    text = aliases.get(text, text)
    return text if text in allowed else fallback


# ML_han_v1.json에서 원하는 회차만 필터링한다.
# 회차와 문항 번호 기준으로 정렬해서 DB seed 결과가 실제 시험 순서와
# 최대한 같은 순서를 유지하도록 한다.
def load_ml_rows(rounds: list[int]) -> list[dict[str, Any]]:
    rows = read_json(ML_JSON)
    selected = [row for row in rows if int(row.get("round_no") or 0) in rounds]
    return sorted(selected, key=lambda row: (int(row.get("round_no") or 0), int(row.get("question_no") or 0)))


# 특정 회차의 공기출 해설 PDF 경로를 찾는다.
# 파일명에 회차 번호와 '해설'이 같이 들어간 PDF를 우선 사용하며,
# 없으면 명확한 오류를 내서 잘못된 해설 없이 진행하지 않게 한다.
def find_explanation_pdf(round_no: int) -> Path:
    matches = sorted(
        path for path in EXPLANATION_DIR.glob("*.pdf")
        if str(round_no) in path.name and "해설" in path.name
    )
    if not matches:
        raise FileNotFoundError(f"{round_no}회 공기출 해설 PDF를 찾지 못했습니다: {EXPLANATION_DIR}")
    return matches[0]


# PDF에서 텍스트를 추출한다.
# pdfplumber가 설치되어 있어야 하며, 공기출 PDF의 글꼴 구조상 일부 문자는
# 깨질 수 있으므로 원본 추출 텍스트와 후처리 결과를 함께 활용한다.
def extract_pdf_text(pdf_path: Path) -> str:
    try:
        import pdfplumber
    except ImportError as exc:
        raise RuntimeError("pdfplumber가 필요합니다. 설치 후 다시 실행하세요.") from exc

    chunks = []
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            chunks.append(page.extract_text() or "")
    return clean_text("\n".join(chunks))


# 공기출 PDF 텍스트에서 [짧은해설] 영역을 문항별로 분리한다.
# PDF 텍스트 추출이 완벽하지 않을 수 있어 여러 패턴을 시도하고,
# 실패한 문항은 빈 해설로 두어 전체 전처리가 중단되지 않게 한다.
def parse_short_explanations(text: str) -> dict[int, str]:
    normalized = re.sub(r"\(cid:\d+\)", " ", text)
    normalized = normalized.replace("[짧은 해설]", "[짧은해설]")
    normalized = normalized.replace("【짧은 해설】", "[짧은해설]")
    normalized = normalized.replace("【짧은해설】", "[짧은해설]")
    normalized = re.sub(r"[\[\(【]\s*짧은\s*해설\s*[\]\)】]", "[짧은해설]", normalized)
    normalized = re.sub(r"(?<!\[)짧은\s*해설(?!\])", "[짧은해설]", normalized)
    normalized = re.sub(r"\s+", " ", normalized)
    normalized = re.sub(r"(한국사능력검정시험\s*\d+회\s*심화\s*해설\s*공기출\s*)?(\d{1,2})번", r"\n\2번\n", normalized)

    by_question: dict[int, str] = {}
    page_pattern = re.compile(r"(?:^|\n)\s*(\d{1,2})\s*번\s*[\s\S]*?\[짧은해설\]\s*([\s\S]*?)(?=\n\s*\d{1,2}\s*번\s*|\Z)")
    for match in page_pattern.finditer(normalized):
        q_no = int(match.group(1))
        if 1 <= q_no <= QUESTION_COUNT:
            short_text = re.split(r"\s*긴\s*해설\b|\s*상세\s*해설\b|\s*정답\s*해설\b", match.group(2), maxsplit=1)[0]
            by_question[q_no] = clean_text(short_text)

    if by_question:
        return by_question

    blocks = re.split(r"\[짧은해설\]", normalized)
    for q_no, block in enumerate(blocks[1:], start=1):
        if q_no > QUESTION_COUNT:
            break
        short = re.split(r"\[(?:상세|정답|오답|해설|핵심)", block, maxsplit=1)[0]
        by_question[q_no] = clean_text(short)
    return by_question


# 74~77회차 해설 PDF에서 짧은해설을 추출해 캐시한다.
# 한 번 추출한 결과는 output_new_test_data/short_explanations_회차.json에 저장하여
# 다음 실행부터 PDF를 다시 읽지 않게 한다.
def load_short_explanations(rounds: list[int], refresh: bool = False) -> dict[tuple[int, int], str]:
    explanations: dict[tuple[int, int], str] = {}
    for round_no in rounds:
        cache_path = OUT_DIR / f"short_explanations_{round_no}.json"
        if cache_path.exists() and not refresh:
            parsed = {int(k): v for k, v in read_json(cache_path).items()}
        else:
            pdf_path = find_explanation_pdf(round_no)
            parsed = parse_short_explanations(extract_pdf_text(pdf_path))
            write_json(cache_path, dict(sorted(parsed.items())))
        for q_no, text in parsed.items():
            explanations[(round_no, q_no)] = text
    return explanations


# 특정 회차의 정답표 PDF 경로를 찾는다.
# 기존 answer_key JSON이 없는 74/75회차도 정답 번호와 배점을
# 정답표 PDF에서 읽어 seed 생성에 사용할 수 있게 한다.
def find_answer_pdf(round_no: int) -> Path:
    matches = sorted(path for path in ANSWER_DIR.glob("*.pdf") if str(round_no) in path.name)
    if not matches:
        raise FileNotFoundError(f"{round_no}회 정답표 PDF를 찾지 못했습니다: {ANSWER_DIR}")
    return matches[0]


# 정답표 PDF 텍스트에서 문항별 정답 번호와 배점을 추출한다.
# 정답표는 '문항번호 정답기호 배점'이 5개 묶음으로 반복되므로,
# ①~⑤ 기호를 숫자로 바꾸어 {문항번호: {answer_no, q_score}} 형태로 만든다.
def parse_answer_key_pdf(pdf_path: Path) -> dict[int, dict[str, int]]:
    try:
        import pdfplumber
    except ImportError as exc:
        raise RuntimeError("pdfplumber가 필요합니다. 설치 후 다시 실행하세요.") from exc

    text = "\n".join(page.extract_text() or "" for page in pdfplumber.open(pdf_path).pages)
    pattern = re.compile(r"(\d{1,2})\s*([①②③④⑤➀➁➂➃➄])\s*(\d)")
    answer_key: dict[int, dict[str, int]] = {}
    for q_no, marker, score in pattern.findall(text):
        question_no = int(q_no)
        if 1 <= question_no <= QUESTION_COUNT:
            answer_key[question_no] = {
                "answer_no": ANSWER_MARKERS[marker],
                "q_score": int(score),
            }
    return answer_key


# 회차별 정답표를 로드해 캐시한다.
# output_exam의 기존 answer_key JSON을 우선 사용하고, 없으면 정답표 PDF에서
# 추출한 뒤 output_new_test_data에 저장해 다음 실행부터 재사용한다.
def load_answer_keys(rounds: list[int], refresh: bool = False) -> dict[tuple[int, int], dict[str, int]]:
    answers: dict[tuple[int, int], dict[str, int]] = {}
    for round_no in rounds:
        cache_path = OUT_DIR / f"answer_key_{round_no}.json"
        legacy_path = LEGACY_OUTPUT_DIR / f"round_{round_no}" / f"answer_key_{round_no}.json"

        if cache_path.exists() and not refresh:
            parsed = {int(k): v for k, v in read_json(cache_path).items()}
        elif legacy_path.exists() and not refresh:
            parsed = {int(k): v for k, v in read_json(legacy_path).items()}
            write_json(cache_path, dict(sorted(parsed.items())))
        else:
            parsed = parse_answer_key_pdf(find_answer_pdf(round_no))
            write_json(cache_path, dict(sorted(parsed.items())))

        for q_no, answer in parsed.items():
            answers[(round_no, q_no)] = {
                "answer_no": int(answer.get("answer_no") or 1),
                "q_score": int(answer.get("q_score") or 2),
            }
    return answers


# 문항 선택지를 실제 ①~⑤ 순서로 재구성한다.
# ML_han_v1.json은 정답 선택지를 맨 앞에 둔 구조라 그대로 쓰면 모두 1번 정답이 되므로,
# 정답표의 answer_no 위치에 정답 선택지를 끼워 넣고 나머지 선택지는 기존 순서대로 채운다.
def build_choices(item: dict[str, Any], answer_no: int) -> list[dict[str, Any]]:
    raw_choices = item.get("choices") or []
    answer_choice = clean_text(item.get("answer_choice"))
    answer_content = ""
    distractor_contents: list[str] = []

    for choice in raw_choices:
        content = clean_text(choice.get("content"))
        if not content:
            continue
        if bool(choice.get("is_answer")) or (answer_choice and content == answer_choice):
            answer_content = content
        else:
            distractor_contents.append(content)

    if not answer_content:
        answer_content = answer_choice or "정답 선택지"

    ordered_contents: list[str] = []
    distractor_index = 0
    for choice_no in range(1, max(5, len(distractor_contents) + 1) + 1):
        if choice_no == answer_no:
            ordered_contents.append(answer_content)
        elif distractor_index < len(distractor_contents):
            ordered_contents.append(distractor_contents[distractor_index])
            distractor_index += 1

    return [
        {
            "choice_no": index,
            "content": content,
            "choice_image_path": "",
            "is_answer": index == answer_no,
            "choice_explanation": "",
        }
        for index, content in enumerate(ordered_contents, start=1)
    ]


# 짧은해설에서 ①~⑤ 또는 ➀~➄로 시작하는 선지별 해설을 분리한다.
# 공기출 PDF는 기호와 문장 사이 공백이 깨지는 경우가 있어,
# 다음 선지 기호가 나오기 전까지를 해당 선택지 해설로 잡는다.
def split_choice_explanations(short_explanation: str) -> dict[int, str]:
    text = clean_text(short_explanation)
    if not text:
        return {}

    marker_pattern = "|".join(re.escape(marker) for marker in sorted(CHOICE_MARKERS, key=len, reverse=True))
    pattern = re.compile(rf"({marker_pattern})")
    matches = list(pattern.finditer(text))
    explanations: dict[int, str] = {}

    for index, match in enumerate(matches):
        choice_no = CHOICE_MARKERS.get(match.group(1))
        if not choice_no:
            continue
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        explanation = clean_text(text[start:end])
        explanation = re.sub(r"^[은는이가을를\s]+", "", explanation).strip()
        explanation = re.sub(r"^(?:옳다|틀리다)[\s\.,，。:：-]*", "", explanation).strip()
        explanation = re.sub(r"[\s\-‑–—]+$", "", explanation).strip()
        if explanation:
            explanations[choice_no] = explanation
    return explanations


# 선택지 목록에 선지별 해설을 붙인다.
# 해설이 없는 선택지는 빈 문자열을 유지해서 DB 적재 시 None 또는 빈 값으로
# 처리할 수 있게 하고, 선택지 번호와 해설 번호가 어긋나지 않게 choice_no로 매칭한다.
def attach_choice_explanations(choices: list[dict[str, Any]], short_explanation: str) -> list[dict[str, Any]]:
    explanations = split_choice_explanations(short_explanation)
    for choice in choices:
        choice_no = int(choice.get("choice_no") or 0)
        choice["choice_explanation"] = explanations.get(choice_no, "")
    return choices


# GPT 분류에 넣을 최소 문항 텍스트를 만든다.
# 비용을 줄이기 위해 긴 필드는 앞부분만 사용하고,
# 문제 본문/선지/짧은해설 중심으로 시대와 주제 판단에 필요한 정보만 남긴다.
def build_classification_text(item: dict[str, Any], short_explanation: str) -> str:
    choices = item.get("choices") or []
    choice_text = "\n".join(
        f"{index}. {clean_text(choice.get('content'))}"
        for index, choice in enumerate(choices, start=1)
        if clean_text(choice.get("content"))
    )
    parts = [
        f"문제: {clean_text(item.get('question'))}",
        f"자료: {clean_text(item.get('material'))}",
        f"입력문: {clean_text(item.get('input_text'))}",
        f"선택지:\n{choice_text}" if choice_text else "",
        f"짧은해설: {short_explanation}",
    ]
    text = "\n".join(part for part in parts if part)
    return text[:4500]


# 기존 데이터에서 시대/주제 라벨을 우선 정규화한다.
# GPT 재분류를 사용하지 않을 때도 DB seed가 허용 라벨만 갖도록
# 팀 기준 라벨 목록에 맞춰 기본값을 만든다.
def infer_labels_without_api(item: dict[str, Any]) -> tuple[str, str]:
    era = normalize_label(item.get("era"), ERA_VALUES, ERA_ALIASES, "")
    topic = normalize_label(item.get("topic"), TOPIC_VALUES, TOPIC_ALIASES, "")

    source = " ".join([
        clean_text(item.get("topic")),
        clean_text(item.get("topic_type")),
        clean_text(item.get("input_text")),
    ])

    if not era:
        if any(word in source for word in ["조선", "태조", "세종", "영조", "정조", "세도"]):
            era = "조선"
        elif "고려" in source:
            era = "고려"
        elif any(word in source for word in ["일제", "독립", "광복군"]):
            era = "일제 강점기"
        elif any(word in source for word in ["개항", "대한 제국", "동학", "갑오"]):
            era = "개항기"
        elif any(word in source for word in ["삼국", "고구려", "백제", "신라", "가야"]):
            era = "삼국 시대"
        elif any(word in source for word in ["발해", "통일 신라", "남북국"]):
            era = "남북국 시대"
        elif "고조선" in source:
            era = "고조선"
        elif any(word in source for word in ["선사", "구석기", "신석기", "청동기"]):
            era = "선사 시대"
        elif any(word in source for word in ["대한민국", "민주화", "6·25"]):
            era = "현대"
        else:
            era = "조선"

    if not topic:
        if any(word in source for word in ["인물", "왕", "업적"]):
            topic = "인물"
        elif any(word in source for word in ["제도", "법", "정책", "관청"]):
            topic = "제도"
        elif any(word in source for word in ["문화", "불교", "유교", "문학", "건축"]):
            topic = "문화"
        elif any(word in source for word in ["전쟁", "군사", "전투", "항쟁"]):
            topic = "군사"
        elif any(word in source for word in ["외교", "조약", "사신"]):
            topic = "외교"
        elif any(word in source for word in ["경제", "토지", "무역", "화폐"]):
            topic = "경제"
        elif any(word in source for word in ["사회", "신분", "민중"]):
            topic = "사회"
        elif any(word in source for word in ["사상", "종교", "천도교", "동학"]):
            topic = "사상·종교"
        elif any(word in source for word in ["정치", "왕권", "정부"]):
            topic = "정치"
        else:
            topic = "사건"

    return era, topic


# OpenAI API로 여러 문항의 시대와 주제를 한 번에 재분류한다.
# 배치 크기를 작게 유지해 JSON 파싱 실패와 비용 낭비를 줄이고,
# temperature=0으로 같은 입력에 대해 일관된 라벨을 받는다.
def classify_batch_with_openai(batch: list[dict[str, Any]], model: str) -> dict[str, dict[str, str]]:
    try:
        from openai import OpenAI
    except ImportError as exc:
        raise RuntimeError("openai 패키지가 필요합니다. 설치 후 다시 실행하세요.") from exc

    client = OpenAI()
    items = [
        {
            "id": row["id"],
            "round_no": row["round_no"],
            "question_no": row["question_no"],
            "text": row["classification_text"],
        }
        for row in batch
    ]
    prompt = f"""
한국사능력검정시험 문항을 정해진 라벨 중 하나로만 분류하세요.

허용 era 라벨: {", ".join(ERA_VALUES)}
허용 topic 라벨: {", ".join(TOPIC_VALUES)}

규칙:
- 조선 전기, 조선 후기, 조선전기, 조선후기는 모두 era='조선'으로 분류합니다.
- topic은 문제에서 가장 중심이 되는 학습 포인트 하나만 고릅니다.
- 애매하면 해설의 핵심 사건/인물/제도를 기준으로 고릅니다.
- 출력은 JSON 객체만 반환합니다.
- 형식: {{"items":[{{"id":"...","era":"...","topic":"..."}}]}}

문항:
{json.dumps(items, ensure_ascii=False)}
""".strip()

    response = client.chat.completions.create(
        model=model,
        temperature=0,
        messages=[
            {"role": "system", "content": "You are a strict Korean History exam label classifier."},
            {"role": "user", "content": prompt},
        ],
    )
    content = response.choices[0].message.content or "{}"
    content = re.sub(r"^```(?:json)?|```$", "", content.strip(), flags=re.MULTILINE).strip()
    parsed = json.loads(content)

    results = {}
    for row in parsed.get("items", []):
        row_id = clean_text(row.get("id"))
        era = normalize_label(row.get("era"), ERA_VALUES, ERA_ALIASES, "")
        topic = normalize_label(row.get("topic"), TOPIC_VALUES, TOPIC_ALIASES, "")
        if row_id and era and topic:
            results[row_id] = {"era": era, "topic": topic}
    return results


# OpenAI 분류 결과를 캐시 파일에 저장하면서 누락된 문항만 호출한다.
# 중간에 실패하거나 중단되어도 이미 분류한 문항은 재사용되므로
# 비용이 중복 발생하는 일을 줄인다.
def classify_rows_with_openai(rows: list[dict[str, Any]], model: str, batch_size: int, sleep_sec: float) -> dict[str, dict[str, str]]:
    cache_path = OUT_DIR / f"openai_labels_{model.replace('/', '_')}.json"
    cache = read_json(cache_path) if cache_path.exists() else {}

    pending = [row for row in rows if row["id"] not in cache]
    for start in range(0, len(pending), batch_size):
        batch = pending[start:start + batch_size]
        labels = classify_batch_with_openai(batch, model)
        cache.update(labels)
        write_json(cache_path, cache)
        print(f"classified {min(start + batch_size, len(pending))}/{len(pending)} pending rows")
        if sleep_sec:
            time.sleep(sleep_sec)
    return cache


# 기존 OpenAI 분류 캐시를 읽어 반환한다.
# API를 다시 호출하지 않고도 이미 분류한 era/topic을 seed 생성에 재사용해
# 비용이 추가로 발생하지 않게 한다.
def load_openai_label_cache(model: str) -> dict[str, dict[str, str]]:
    cache_path = OUT_DIR / f"openai_labels_{model.replace('/', '_')}.json"
    if not cache_path.exists():
        return {}
    cache = read_json(cache_path)
    return {
        clean_text(row_id): {
            "era": normalize_label(labels.get("era"), ERA_VALUES, ERA_ALIASES, ""),
            "topic": normalize_label(labels.get("topic"), TOPIC_VALUES, TOPIC_ALIASES, ""),
        }
        for row_id, labels in cache.items()
        if clean_text(row_id)
    }


# ML_han_v1 문항과 짧은해설을 합쳐 DB seed 한 건을 만든다.
# questions/question_options 테이블에 넣기 쉬운 형태로 필드를 맞추고,
# answer_explanation에는 공기출 [짧은해설]을 저장한다.
def build_seed_record(
    item: dict[str, Any],
    short_explanation: str,
    answer_meta: dict[str, int],
    labels: dict[str, str] | None = None,
) -> dict[str, Any]:
    round_no = int(item.get("round_no") or 0)
    question_no = int(item.get("question_no") or 0)
    answer_no = int(answer_meta.get("answer_no") or 1)
    q_score = int(answer_meta.get("q_score") or item.get("q_score") or 2)
    choices = build_choices(item, answer_no)
    choices = attach_choice_explanations(choices, short_explanation)
    answer_explanation = next(
        (
            clean_text(choice.get("choice_explanation"))
            for choice in choices
            if int(choice.get("choice_no") or 0) == answer_no
        ),
        "",
    ) or clean_text(short_explanation)
    fallback_era, fallback_topic = infer_labels_without_api(item)
    labels = labels or {}

    era = normalize_label(labels.get("era") or fallback_era, ERA_VALUES, ERA_ALIASES, "조선")
    topic = normalize_label(labels.get("topic") or fallback_topic, TOPIC_VALUES, TOPIC_ALIASES, "사건")
    content = clean_text(item.get("question")) or clean_text(item.get("input_text")) or "문제를 읽고 정답을 선택하세요."
    passage = clean_text(item.get("material"))
    question_type = clean_text(item.get("major_type")) or "미분류"
    question_subtype = clean_text(item.get("minor_type")) or "미분류"

    return {
        "source_exam": f"{round_no}회 심화",
        "question_no": question_no,
        "q_score": q_score,
        "era": era,
        "topic": topic,
        "question_type": question_type,
        "question_subtype": question_subtype,
        "content": content,
        "passage": passage,
        "image_caption": "",
        "question_image_path": "",
        "answer_no": answer_no,
        "answer_explanation": answer_explanation,
        "core_concept": clean_text(item.get("topic")) or topic,
        "choices": choices,
        "problem_id": clean_text(item.get("problem_id")),
        "data_source": clean_text(item.get("data_source")),
    }


# 전체 전처리 파이프라인을 실행한다.
# 회차 필터링, 짧은해설 추출, 선택적 OpenAI 재분류, DB seed 저장을
# 한 번에 처리하고 결과 파일 경로를 반환한다.
def run(args: argparse.Namespace) -> dict[str, Path]:
    if args.seed_file:
        seed_path = Path(args.seed_file)
        seed_records = read_json(seed_path)
        summary_path = OUT_DIR / f"summary_{seed_path.stem}.json"
        summary = {
            "seed_file": str(seed_path),
            "count": len(seed_records),
            "era_counts": count_by(seed_records, "era"),
            "topic_counts": count_by(seed_records, "topic"),
            "question_type_counts": count_by(seed_records, "question_type"),
            "question_subtype_counts": count_by(seed_records, "question_subtype"),
        }
        if args.import_db:
            summary["import_db"] = import_seed_records_to_db(seed_records)
        write_json(summary_path, summary)
        return {
            "seed": seed_path,
            "summary": summary_path,
        }

    rounds = args.rounds
    rows = load_ml_rows(rounds)
    explanations = load_short_explanations(rounds, refresh=args.refresh_explanations)
    answer_keys = load_answer_keys(rounds, refresh=args.refresh_answers)

    prepared_rows = []
    for item in rows:
        round_no = int(item.get("round_no") or 0)
        question_no = int(item.get("question_no") or 0)
        short_explanation = explanations.get((round_no, question_no), "")
        row_id = f"{round_no}_{question_no:02d}"
        prepared_rows.append({
            "id": row_id,
            "round_no": round_no,
            "question_no": question_no,
            "short_explanation": short_explanation,
            "classification_text": build_classification_text(item, short_explanation),
            "raw": item,
        })

    openai_labels = {}
    if args.classify_openai:
        openai_labels = classify_rows_with_openai(
            prepared_rows,
            model=args.model,
            batch_size=args.batch_size,
            sleep_sec=args.sleep_sec,
        )
    else:
        openai_labels = load_openai_label_cache(args.model)

    seed_records = [
        build_seed_record(
            row["raw"],
            row["short_explanation"],
            answer_keys.get((row["round_no"], row["question_no"]), {}),
            labels=openai_labels.get(row["id"]),
        )
        for row in prepared_rows
    ]

    suffix = "_".join(str(round_no) for round_no in rounds)
    prepared_path = OUT_DIR / f"prepared_ml_han_{suffix}.json"
    seed_path = OUT_DIR / f"db_seed_ml_han_{suffix}.json"
    summary_path = OUT_DIR / f"summary_ml_han_{suffix}.json"

    write_json(prepared_path, prepared_rows)
    write_json(seed_path, seed_records)
    summary = {
        "rounds": rounds,
        "count": len(seed_records),
        "classify_openai": bool(args.classify_openai),
        "model": args.model if args.classify_openai else None,
        "cached_openai_label_count": len(openai_labels),
        "answer_key_count": len(answer_keys),
        "era_counts": count_by(seed_records, "era"),
        "topic_counts": count_by(seed_records, "topic"),
        "missing_short_explanation_count": sum(1 for row in prepared_rows if not row["short_explanation"]),
    }

    if args.import_db:
        summary["import_db"] = import_seed_records_to_db(seed_records)

    write_json(summary_path, summary)

    return {
        "prepared": prepared_path,
        "seed": seed_path,
        "summary": summary_path,
    }


# 지정한 필드 기준으로 건수를 집계한다.
# 전처리 후 라벨 분포를 빠르게 확인하기 위한 요약 파일 생성에 사용한다.
def count_by(rows: list[dict[str, Any]], field: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        label = clean_text(row.get(field)) or "미분류"
        counts[label] = counts.get(label, 0) + 1
    return dict(sorted(counts.items(), key=lambda item: (-item[1], item[0])))


# DB 접속 정보를 .env의 POSTGRES_* 값에서 읽어 seed 데이터를 적재한다.
# 기존 테스트 문제 데이터는 모두 삭제한 뒤 새로 만든 74~77회차 200문항과
# 선택지 1000개를 questions/question_options 테이블에 넣는다.
def import_seed_records_to_db(records: list[dict[str, Any]]) -> dict[str, int]:
    try:
        import psycopg2
    except ImportError as exc:
        raise RuntimeError("psycopg2-binary가 필요합니다. 설치 후 다시 실행하세요.") from exc

    config = {
        "dbname": os.getenv("POSTGRES_DB", "history_rag"),
        "user": os.getenv("POSTGRES_USER", "himate"),
        "password": os.getenv("POSTGRES_PASSWORD", "himate1234"),
        "host": os.getenv("POSTGRES_HOST", "localhost"),
        "port": os.getenv("POSTGRES_PORT", "5432"),
    }

    option_count = 0
    conn = psycopg2.connect(**config)
    try:
        with conn, conn.cursor() as cur:
            cur.execute("ALTER TABLE question_options ADD COLUMN IF NOT EXISTS choice_explanation TEXT NULL")
            cur.execute("ALTER TABLE question_options ADD COLUMN IF NOT EXISTS choice_image_path TEXT NULL")
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
                            int(choice["choice_no"]),
                            clean_text(choice.get("content")),
                            clean_text(choice.get("choice_image_path")) or None,
                            bool(choice.get("is_answer")),
                            clean_text(choice.get("choice_explanation")) or None,
                        ),
                    )
                    option_count += 1
    finally:
        conn.close()

    return {
        "question_count": len(records),
        "option_count": option_count,
    }


# CLI 인자를 정의한다.
# 기본값은 74~77회차를 비용 없이 전처리하는 설정이며,
# --classify-openai를 붙이면 지정 모델로 era/topic 재분류를 수행한다.
def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build DB seed data from ML_han_v1.json for rounds 74~77.")
    parser.add_argument("--rounds", nargs="+", type=int, default=DEFAULT_ROUNDS, help="전처리할 회차 목록")
    parser.add_argument("--classify-openai", action="store_true", help="OpenAI API로 era/topic을 재분류")
    parser.add_argument("--model", default="gpt-4o", help="OpenAI 분류 모델")
    parser.add_argument("--batch-size", type=int, default=10, help="OpenAI 한 번 호출에 넣을 문항 수")
    parser.add_argument("--sleep-sec", type=float, default=0.2, help="OpenAI 배치 호출 사이 대기 시간")
    parser.add_argument("--refresh-explanations", action="store_true", help="짧은해설 PDF 캐시를 새로 추출")
    parser.add_argument("--refresh-answers", action="store_true", help="정답표 PDF/JSON 캐시를 새로 추출")
    parser.add_argument("--seed-file", type=Path, help="전처리 대신 지정한 seed JSON 파일을 사용")
    parser.add_argument("--import-db", action="store_true", help="기존 문제 데이터를 삭제하고 생성한 seed를 DB에 적재")
    return parser.parse_args()


if __name__ == "__main__":
    output_paths = run(parse_args())
    for name, path in output_paths.items():
        print(f"{name}: {path}")
