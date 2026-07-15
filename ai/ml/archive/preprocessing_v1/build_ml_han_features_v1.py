# ML_han_v1 원본 데이터를 모델 입력 피처로 변환하는 전처리 파일입니다.
# 문제 지문과 질문을 input_text로 만들고 시대/주제/문항 유형 라벨을 정리합니다.
# 결과는 ai/ml/output 아래 JSON, CSV, 검증 리포트로 저장됩니다.
"""
Build ML feature data from ML_han_v1.json.

Input:
  ai/ml/ML_han_v1.json

Outputs:
  ai/ml/output/ml_han_features_v1.json
  ai/ml/output/ml_han_features_v1.csv
  ai/ml/output/ml_han_features_v1_report.json

Run:
  python ai/ml/build_ml_han_features_v1.py
"""

from __future__ import annotations

import csv
import json
import re
from pathlib import Path
from typing import Any


# ai/ml 안에서 바로 작업할 수 있도록 현재 파일 위치를 ML 작업 폴더로 사용합니다.
ROOT_DIR = Path(__file__).resolve().parents[2]
ML_DIR = Path(__file__).resolve().parent
DOCS_DIR = ROOT_DIR / "test" / "CJ" / "test_docs"

INPUT_JSON = ML_DIR / "ML_han_v1.json"
REFERENCE_JSON = ML_DIR / "era_reference.json"
ERA_OVERRIDES_JSON = ML_DIR / "ml_keyword_era_overrides.json"
PERSON_JSON = DOCS_DIR / "3. 참고 자료" / "시대별_인물_정리_v2_1.json"

OUT_DIR = ML_DIR / "output"
OUTPUT_JSON = OUT_DIR / "ml_han_features_v1.json"
OUTPUT_CSV = OUT_DIR / "ml_han_features_v1.csv"
REPORT_JSON = OUT_DIR / "ml_han_features_v1_report.json"

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

TOPIC_TYPE_TO_TOPIC = {
    "인물": "인물",
    "제도": "제도",
    "사건": "사건",
    "문화": "문화",
    "문화유산": "문화",
    "집단": "정치",
    "매체": "문화",
}

QUESTION_TASK_TO_TYPE = {
    "order": "연대기의 파악",
    "timeline_position": "연대기의 파악",
    "period_between": "연대기의 파악",
    "map_location": "역사 자료의 분석 및 해석",
    "multi_select_combo": "역사 자료의 분석 및 해석",
    "negative_select": "역사 상황 및 쟁점의 인식",
    "standard_select": "역사 지식의 이해",
}

ERA_ALIAS = {
    "조선 전기": "조선",
    "조선 후기": "조선",
}

MANUAL_ERA_OVERRIDES = {
    "구석기": "선사 시대",
    "신석기": "선사 시대",
    "청동기": "선사 시대",
    "고조선": "고조선",
    "우거왕": "고조선",
    "부여": "초기 국가",
    "옥저": "초기 국가",
    "동예": "초기 국가",
    "삼한": "초기 국가",
    "백제": "삼국 시대",
    "고구려": "삼국 시대",
    "신라": "삼국 시대",
    "가야": "삼국 시대",
    "진흥왕": "삼국 시대",
    "비유왕": "삼국 시대",
    "눌지왕": "삼국 시대",
    "근초고왕": "삼국 시대",
    "광개토대왕": "삼국 시대",
    "백제 금동대향로": "삼국 시대",
    "발해": "남북국 시대",
    "대무예": "남북국 시대",
    "정효 공주": "남북국 시대",
    "정효공주": "남북국 시대",
    "해동성국": "남북국 시대",
    "통일 신라": "남북국 시대",
    "통일신라": "남북국 시대",
    "신문왕": "남북국 시대",
    "장보고": "남북국 시대",
    "원효": "남북국 시대",
    "의상": "남북국 시대",
    "후백제": "남북국 시대",
    "견훤": "남북국 시대",
    "고려": "고려",
    "광종": "고려",
    "공민왕": "고려",
    "무신 정권": "고려",
    "무신정권": "고려",
    "몽골": "고려",
    "원 간섭기": "고려",
    "팔만대장경": "고려",
    "직지심체요절": "고려",
    "향교": "고려",
    "조선": "조선",
    "세종": "조선",
    "장영실": "조선",
    "자격루": "조선",
    "훈민정음": "조선",
    "경국대전": "조선",
    "사화": "조선",
    "조의제문": "조선",
    "임진왜란": "조선",
    "정조": "조선",
    "균역법": "조선",
    "대동법": "조선",
    "비변사": "조선",
    "홍경래": "조선",
    "세도 정치": "조선",
    "세도정치": "조선",
    "원납전": "조선",
    "경복궁 중건": "조선",
    "영건일감": "조선",
    "성호사설": "조선",
    "곤여만국전도": "조선",
    "박제가": "조선",
    "박지원": "조선",
    "김홍도": "조선",
    "신윤복": "조선",
    "몽유도원도": "조선",
    "개항": "개항기",
    "강화도 조약": "개항기",
    "강화도조약": "개항기",
    "운요호": "개항기",
    "조선책략": "개항기",
    "황준헌": "개항기",
    "황쭌쉔": "개항기",
    "임오군란": "개항기",
    "갑신정변": "개항기",
    "동학 농민 운동": "개항기",
    "동학농민운동": "개항기",
    "갑오개혁": "개항기",
    "삼국 간섭": "개항기",
    "독립협회": "개항기",
    "대한 제국": "개항기",
    "대한제국": "개항기",
    "환구단": "개항기",
    "한성 전기 회사": "개항기",
    "한성전기회사": "개항기",
    "전차": "개항기",
    "을사늑약": "개항기",
    "헤이그 특사": "개항기",
    "일제": "일제 강점기",
    "3·1 운동": "일제 강점기",
    "3·1운동": "일제 강점기",
    "6·10 만세 운동": "일제 강점기",
    "물산 장려": "일제 강점기",
    "물산장려": "일제 강점기",
    "소년 운동": "일제 강점기",
    "어린이날": "일제 강점기",
    "박은식": "일제 강점기",
    "한국독립운동지혈사": "일제 강점기",
    "미쓰야": "일제 강점기",
    "미쓰야 협정": "일제 강점기",
    "대한민국 임시 정부": "일제 강점기",
    "대한민국 임시정부": "일제 강점기",
    "신간회": "일제 강점기",
    "의열단": "일제 강점기",
    "한국광복군": "일제 강점기",
    "광복": "현대",
    "모스크바 3상 회의": "현대",
    "좌우 합작": "현대",
    "대한민국 정부": "현대",
    "6·25": "현대",
    "4·19": "현대",
    "5·16": "현대",
    "5.16": "현대",
    "5·18": "현대",
    "6월 민주 항쟁": "현대",
    "교복과 두발": "현대",
    "야간 통행 금지": "현대",
    "보도 지침": "현대",
    "전두환": "현대",
    "남북": "현대",
    "통일": "현대",
    "광주 대단지 사건": "현대",
    "행정 중심 복합 도시": "현대",
}

FIELDNAMES = [
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
    "question_type",
    "question_subtype",
    "core_concept",
]


# JSON 파일을 읽고, 파일이 없으면 지정한 기본값을 반환합니다.
# 참조 데이터가 없을 때 전처리가 바로 중단되지 않도록 안전장치 역할을 합니다.
# 모든 JSON 입력은 UTF-8 기준으로 읽습니다.
def read_json(path: Path, fallback: Any) -> Any:
    if not path.exists():
        return fallback
    return json.loads(path.read_text(encoding="utf-8"))


# None, 숫자, 빈 값 등 다양한 입력을 문자열로 정규화합니다.
# 앞뒤 공백을 제거해 라벨 비교와 키워드 비교가 안정적으로 되게 합니다.
# 전처리 전반에서 기본 텍스트 클리닝 함수로 사용됩니다.
def normalize_text(value: Any) -> str:
    return str(value or "").strip()


# 문자열 안의 모든 공백을 제거한 비교용 값을 만듭니다.
# 한국어 라벨이나 키워드가 띄어쓰기 차이로 매칭 실패하는 것을 줄입니다.
# 원문을 바꾸는 용도가 아니라 비교 정확도를 높이는 보조 함수입니다.
def no_space(value: Any) -> str:
    return re.sub(r"\s+", "", normalize_text(value))


# 입력 라벨을 허용된 라벨 목록 중 하나로 맞춥니다.
# 정확히 일치하지 않아도 공백 제거 비교나 포함 관계로 보정합니다.
# 그래도 맞지 않으면 지정한 fallback 라벨을 반환합니다.
def normalize_label(value: Any, allowed: list[str], fallback: str) -> str:
    text = ERA_ALIAS.get(normalize_text(value), normalize_text(value))
    if text in allowed:
        return text

    compact = no_space(text)
    for item in allowed:
        if no_space(item) == compact:
            return item
    for item in allowed:
        if item in text or text in item:
            return item
    return fallback


# 시대별 인물 참고 자료를 읽어 인물명 -> 시대 인덱스를 만듭니다.
# 문제의 핵심 키워드가 인물명일 때 시대 라벨을 보정하는 데 사용합니다.
# 여러 시대에 걸친 인물은 모호하므로 인덱스에서 제외합니다.
def build_person_index() -> dict[str, str]:
    data = read_json(PERSON_JSON, {})
    index: dict[str, str] = {}
    for entry in data.get("_entity_index", {}).values():
        name = normalize_text(entry.get("name"))
        appearances = entry.get("appearances") or []
        if not name or entry.get("multi_era") or not appearances:
            continue

        era = ERA_ALIAS.get(normalize_text(appearances[0].get("era")), normalize_text(appearances[0].get("era")))
        if era not in ERA_VALUES:
            continue
        index[name] = era
        index[no_space(name)] = era
    return index


# 시대/주제/오버라이드/인물 키워드를 하나의 검색 목록으로 합칩니다.
# 중복 키워드는 제거하고 긴 키워드가 먼저 매칭되도록 정렬합니다.
# 이후 input_text에서 핵심 키워드를 추출할 때 사용합니다.
def flatten_keyword_groups(reference: dict, era_overrides: dict, person_index: dict[str, str]) -> list[str]:
    keywords: list[str] = []

    keywords.extend(MANUAL_ERA_OVERRIDES)
    for values in reference.get("era_keywords", {}).values():
        keywords.extend(normalize_text(keyword) for keyword in values or [])
    for values in reference.get("topic_keywords", {}).values():
        keywords.extend(normalize_text(keyword) for keyword in values or [])
    for values in era_overrides.values():
        keywords.extend(normalize_text(keyword) for keyword in values or [])
    for name in person_index:
        if re.search(r"\s", name):
            keywords.append(normalize_text(name))

    deduped: dict[str, str] = {}
    for keyword in keywords:
        compact = no_space(keyword)
        if keyword and len(keyword) >= 2:
            deduped[compact] = keyword
    return sorted(deduped.values(), key=len, reverse=True)


# 문제 텍스트에서 모델 입력 보조 키워드를 추출합니다.
# 핵심 개념과 참조 키워드 목록을 기준으로 최대 12개까지 찾습니다.
# 키워드가 없으면 core_concept를 fallback으로 사용합니다.
def extract_keywords(input_text: str, core_concept: str, keyword_list: list[str]) -> str:
    source = normalize_text(input_text)
    compact_source = no_space(source)
    found: list[str] = []
    seen: set[str] = set()

    def add_keyword(keyword: str) -> None:
        compact = no_space(keyword)
        if not compact or compact in seen:
            return
        seen.add(compact)
        found.append(normalize_text(keyword))

    if core_concept and core_concept in source:
        add_keyword(core_concept)

    for keyword in keyword_list:
        compact = no_space(keyword)
        if compact and compact in compact_source:
            add_keyword(keyword)
        if len(found) >= 12:
            break

    if not found and core_concept and core_concept != "미분류":
        add_keyword(core_concept)
    return " ".join(found)


# 핵심 개념, 지문, 정답 텍스트를 바탕으로 시대 라벨을 추론합니다.
# 수동 오버라이드, 인물 인덱스, 시대 키워드 순서로 보정합니다.
# 어떤 근거도 찾지 못하면 미분류 값을 반환해 이후 fallback 처리를 받습니다.
def infer_era(
    core_concept: str,
    input_text: str,
    label_text: str,
    person_index: dict[str, str],
    reference: dict,
    era_overrides: dict,
) -> str:
    core_compact = no_space(core_concept)
    text_compact = no_space(f"{core_concept}\n{label_text}\n{input_text}")

    for keyword, era in MANUAL_ERA_OVERRIDES.items():
        compact = no_space(keyword)
        if compact and compact in text_compact:
            return normalize_label(era, ERA_VALUES, "미분류")

    for era, values in era_overrides.items():
        normalized_era = ERA_ALIAS.get(era, era)
        for keyword in values or []:
            compact = no_space(keyword)
            if compact and compact in text_compact:
                return normalize_label(normalized_era, ERA_VALUES, "미분류")

    if core_compact in person_index:
        return person_index[core_compact]
    for name, era in person_index.items():
        compact = no_space(name)
        if compact and (compact in core_compact or compact in text_compact):
            return era

    candidates: list[tuple[int, str]] = []
    for era, values in reference.get("era_keywords", {}).items():
        normalized_era = normalize_label(ERA_ALIAS.get(era, era), ERA_VALUES, "")
        if not normalized_era:
            continue
        for keyword in values or []:
            compact = no_space(keyword)
            if compact and compact in text_compact:
                candidates.append((len(compact), normalized_era))

    candidates.sort(reverse=True)
    return candidates[0][1] if candidates else "미분류"


# 시대 추론이 실패했을 때 문제 번호 흐름으로 시대를 보정합니다.
# 한능검 문항은 대체로 시대 순서로 배열되는 특성을 이용합니다.
# 키워드 기반 추론보다 약한 fallback 규칙이므로 마지막에만 사용합니다.
def fallback_era_by_question_no(question_no: int) -> str:
    if question_no <= 1:
        return "선사 시대"
    if question_no <= 2:
        return "초기 국가"
    if question_no <= 5:
        return "삼국 시대"
    if question_no <= 9:
        return "남북국 시대"
    if question_no <= 16:
        return "고려"
    if question_no <= 29:
        return "조선"
    if question_no <= 36:
        return "개항기"
    if question_no <= 45:
        return "일제 강점기"
    return "현대"


# 원본 topic_type과 주제 키워드를 이용해 주제 라벨을 추론합니다.
# 명시적인 topic_type 매핑이 있으면 먼저 사용합니다.
# 없으면 문제 텍스트와 topic 필드에서 주제 키워드를 찾아 결정합니다.
def infer_topic(item: dict, reference: dict) -> str:
    topic_type = normalize_text(item.get("topic_type"))
    if topic_type in TOPIC_TYPE_TO_TOPIC:
        return TOPIC_TYPE_TO_TOPIC[topic_type]

    source = f"{normalize_text(item.get('topic'))}\n{normalize_text(item.get('input_text'))}"
    candidates: list[tuple[int, str]] = []
    for topic, values in reference.get("topic_keywords", {}).items():
        if topic not in TOPIC_VALUES:
            continue
        for keyword in values or []:
            text = normalize_text(keyword)
            if text and text in source:
                candidates.append((len(text), topic))

    candidates.sort(reverse=True)
    return candidates[0][1] if candidates else "정치"


# 원본 major_type과 question_task를 이용해 문항 유형을 정합니다.
# major_type이 이미 허용 라벨이면 그대로 사용합니다.
# 없거나 불안정하면 question_task 기반 매핑과 fallback 라벨을 사용합니다.
def infer_question_type(item: dict) -> str:
    major_type = normalize_text(item.get("major_type"))
    if major_type in QUESTION_TYPES:
        return major_type
    task_type = normalize_text(item.get("question_task"))
    return QUESTION_TASK_TO_TYPE.get(task_type) or normalize_label(major_type, QUESTION_TYPES, "역사 지식의 이해")


# 원본 minor_type을 세부 유형 허용 목록에 맞춰 정규화합니다.
# 띄어쓰기나 일부 표현 차이가 있어도 normalize_label로 보정합니다.
# 세부 유형은 현재 모델 타깃이 아니라 보관/분석용 컬럼입니다.
def infer_question_subtype(item: dict) -> str:
    return normalize_label(normalize_text(item.get("minor_type")), QUESTION_SUBTYPES, "기본 사실·개념 확인")


# 원본 topic 또는 텍스트 키워드에서 핵심 개념을 뽑습니다.
# topic이 짧고 명확하면 core_concept로 우선 사용합니다.
# 그렇지 않으면 지문에 등장하는 참조 키워드를 찾아 대체합니다.
def extract_core_concept(item: dict, input_text: str, keyword_list: list[str]) -> str:
    topic = normalize_text(item.get("topic"))
    if topic and len(topic) <= 40:
        return topic

    compact_source = no_space(input_text)
    for keyword in keyword_list:
        if no_space(keyword) in compact_source:
            return keyword
    return "미분류"


# 지정한 컬럼의 값별 건수를 계산합니다.
# 리포트에서 split, 시대, 주제, 유형 분포를 확인할 때 사용합니다.
# 결과는 건수 내림차순, 라벨명 오름차순으로 정렬합니다.
def count_by(rows: list[dict], field: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        key = normalize_text(row.get(field)) or "(blank)"
        counts[key] = counts.get(key, 0) + 1
    return dict(sorted(counts.items(), key=lambda item: (-item[1], item[0])))


# 전처리된 row 목록을 CSV 파일로 저장합니다.
# FIELDNAMES 순서를 고정해 모델 학습 전 확인하기 쉽게 만듭니다.
# JSON과 함께 사람이 표 형태로 검토할 수 있는 산출물입니다.
def write_csv(path: Path, rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows({field: row.get(field, "") for field in FIELDNAMES} for row in rows)


# ML_han_v1 원본 전체를 모델 학습용 row 목록으로 변환합니다.
# input_text, keywords, era, topic, question_type 등을 한 번에 생성합니다.
# split은 70회 이하 train, 71회 이상 test 기준으로 부여합니다.
def build_rows() -> list[dict]:
    source_rows = read_json(INPUT_JSON, [])
    reference = read_json(REFERENCE_JSON, {})
    era_overrides = read_json(ERA_OVERRIDES_JSON, {})
    person_index = build_person_index()
    keyword_list = flatten_keyword_groups(reference, era_overrides, person_index)

    rows: list[dict] = []
    for item in source_rows:
        input_text = "\n".join(
            value
            for value in [normalize_text(item.get("material")), normalize_text(item.get("question"))]
            if value
        )
        core_concept = extract_core_concept(item, input_text, keyword_list)
        answer_choices = [
            normalize_text(choice.get("content"))
            for choice in item.get("choices", [])
            if choice and choice.get("is_answer")
        ]
        label_text = "\n".join(
            value
            for value in [normalize_text(item.get("topic")), normalize_text(item.get("answer_choice")), *answer_choices]
            if value
        )

        round_no = int(item.get("round_no") or 0)
        question_no = int(item.get("question_no") or 0)
        inferred_era = infer_era(core_concept, input_text, label_text, person_index, reference, era_overrides)
        final_era = fallback_era_by_question_no(question_no) if inferred_era == "미분류" else inferred_era
        keywords = extract_keywords(input_text, core_concept, keyword_list) or final_era

        rows.append(
            {
                "ml_sequence_index": int(item.get("ml_sequence_index") or 0),
                "split": "train" if round_no <= 70 else "test",
                "round_no": round_no,
                "question_no": question_no,
                "problem_id": normalize_text(item.get("problem_id")),
                "data_source": normalize_text(item.get("data_source")),
                "input_text": input_text,
                "keywords": keywords,
                "era": final_era,
                "topic": infer_topic(item, reference),
                "question_type": infer_question_type(item),
                "question_subtype": infer_question_subtype(item),
                "core_concept": keywords if core_concept == "미분류" else core_concept,
            }
        )
    return rows


# 전처리 전체 실행 함수입니다.
# JSON/CSV 피처 파일과 분포 확인용 report JSON을 output 폴더에 저장합니다.
# 실행 결과 요약을 콘솔에 출력해 row 수와 누락 라벨 여부를 확인합니다.
def main() -> None:
    rows = build_rows()
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    OUTPUT_JSON.write_text(json.dumps(rows, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    write_csv(OUTPUT_CSV, rows)

    report = {
        "input": INPUT_JSON.relative_to(ROOT_DIR).as_posix(),
        "outputs": {
            "json": OUTPUT_JSON.relative_to(ROOT_DIR).as_posix(),
            "csv": OUTPUT_CSV.relative_to(ROOT_DIR).as_posix(),
        },
        "total_rows": len(rows),
        "split_counts": count_by(rows, "split"),
        "era_counts": count_by(rows, "era"),
        "topic_counts": count_by(rows, "topic"),
        "question_type_counts": count_by(rows, "question_type"),
        "question_subtype_counts": count_by(rows, "question_subtype"),
        "missing_label_rows": [
            {
                "round_no": row["round_no"],
                "question_no": row["question_no"],
                "era": row["era"],
                "keywords": row["keywords"],
                "core_concept": row["core_concept"],
            }
            for row in rows
            if row["era"] == "미분류" or not row["keywords"] or row["core_concept"] == "미분류"
        ],
    }
    REPORT_JSON.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(
        json.dumps(
            {
                "rows": len(rows),
                "split_counts": report["split_counts"],
                "output_json": report["outputs"]["json"],
                "output_csv": report["outputs"]["csv"],
                "report_json": REPORT_JSON.relative_to(ROOT_DIR).as_posix(),
                "missing_label_rows": len(report["missing_label_rows"]),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
