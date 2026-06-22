"""
한국사능력검정시험 기출문제 PDF → PostgreSQL DB 적재 스크립트

[파이프라인]
STEP 1 : 답지 PDF        → 정답/배점 파싱 (pdfplumber)
STEP 2 : PDF 페이지      → base64 이미지 변환 (PyMuPDF)
STEP 3 : 문제지 이미지   → GPT-4o Vision → 문제내용/선택지 추출
STEP 4 : 해설지 이미지   → GPT-4o Vision → 해설/핵심개념 추출
STEP 5 : DB INSERT        → questions / question_options 적재
STEP 6 : GPT-4o-mini     → era / topic / question_type 분류 → DB UPDATE

실행 위치: SKN27-FINAL-2Team 루트
실행 명령: python test/CJ/test_q/etl_questions.py
"""

import os
import re
import json
import time
import base64
import pdfplumber
import fitz
import psycopg2
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()

# =============================================
# 설정
# =============================================
DOCS_DIR     = "test/CJ/test_docs"
QUESTION_PDF = os.path.join(DOCS_DIR, "78회+한국사_문제지(심화).pdf")
ANSWER_PDF   = os.path.join(DOCS_DIR, "78회+한국사_답지(심화).pdf")
EXPLAIN_PDF  = os.path.join(DOCS_DIR, "한국사능력검정시험 78회 심화 해설 한Pro.pdf")

SAVE_DIR = "test/CJ/test_q"

DB_CONFIG = {
    "dbname":   os.getenv("POSTGRES_DB", "history_rag"),
    "user":     os.getenv("POSTGRES_USER", "himate"),
    "password": os.getenv("POSTGRES_PASSWORD", "himate1234"),
    "host":     "localhost",
    "port":     os.getenv("POSTGRES_PORT", "5432"),
}

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# ── STEP 6 분류 기준 ─────────────────────────────────────────
QUESTION_TYPES = """
[question_type 6개 대유형]
1. 역사 지식의 이해      : 기본 사실, 개념, 인물, 제도, 문화 요소를 직접 알고 있는지 확인
2. 연대기의 파악         : 사건 순서, 전후 관계, 특정 시기 범위, 연표 흐름 판단
3. 역사 상황 및 쟁점의 인식 : 배경, 원인, 목적, 주장, 입장, 정세, 전개 양상 파악
4. 역사 자료의 분석 및 해석 : 사료, 지도, 사진, 도표, 대화, 기사 등의 단서를 해석
5. 역사 탐구의 설계 및 수행 : 탐구 주제, 답사, 조사, 검색, 보고서, 전시, 자료 수집 방법 선택
6. 결론의 도출 및 평가   : 의의, 영향, 결과, 공통점, 차이점, 종합 결론 판단
"""

ERA_LIST = """
[era] 아래 중 하나만 사용:
선사 시대, 고조선, 삼국 시대, 통일 신라, 발해, 남북국 시대,
고려, 조선 전기, 조선 후기, 개항기, 일제 강점기, 현대, 시대 통합
"""

TOPIC_LIST = """
[topic] 아래 중 하나만 사용:
정치, 경제, 사회, 문화, 외교, 군사, 인물, 유물·유적, 제도, 사건, 사상·종교, 근현대사
"""


# =============================================
# STEP 1: 답지 파싱 → {문제번호: {answer_no, q_score}}
# =============================================
def parse_answer_sheet():
    print("[STEP 1] 답지 파싱 중...")
    circle_map = {"①": 1, "②": 2, "③": 3, "④": 4, "⑤": 5}
    pattern = re.compile(r"(\d+)\s+([①②③④⑤])\s+(\d+)")
    answers = {}

    with pdfplumber.open(ANSWER_PDF) as pdf:
        text = pdf.pages[0].extract_text()

    for m in pattern.finditer(text):
        q_no  = int(m.group(1))
        ans   = circle_map[m.group(2)]
        score = int(m.group(3))
        answers[q_no] = {"answer_no": ans, "q_score": score}

    print(f"  → {len(answers)}개 문제 정답/배점 파싱 완료")
    return answers


# =============================================
# STEP 2: PDF 페이지 → base64 이미지 변환
# =============================================
def pdf_page_to_b64(pdf_path, page_index, scale=2):
    doc = fitz.open(pdf_path)
    pix = doc[page_index].get_pixmap(matrix=fitz.Matrix(scale, scale))
    b64 = base64.b64encode(pix.tobytes("png")).decode()
    doc.close()
    return b64


# =============================================
# STEP 3: GPT-4o Vision으로 문제지 추출
# =============================================
def extract_questions_from_page(b64_image, page_num):
    prompt = """이 한국사능력검정시험 문제지 이미지에서 문제들을 추출해줘.
반드시 아래 JSON 배열 형식으로만 출력해줘 (다른 텍스트 없이):
[
  {
    "문제번호": 1,
    "문제내용": "문제 지문 전체 텍스트",
    "선택지": ["① 선택지1", "② 선택지2", "③ 선택지3", "④ 선택지4", "⑤ 선택지5"]
  }
]
- 이미지(사진, 지도, 유물 등)는 [이미지: 간략한 설명] 으로 표시
- 선택지 번호(①②③④⑤) 포함해서 출력
- 이 페이지에 있는 모든 문제 추출"""

    response = client.chat.completions.create(
        model="gpt-4o",
        messages=[{
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {
                    "url": f"data:image/png;base64,{b64_image}",
                    "detail": "high"
                }}
            ]
        }],
        max_tokens=4000
    )

    content = response.choices[0].message.content.strip()
    content = re.sub(r"```json\n?|\n?```", "", content).strip()
    return json.loads(content)


# =============================================
# STEP 4: GPT-4o Vision으로 해설지 추출
# =============================================
def extract_explanations_from_page(b64_image):
    prompt = """이 한국사능력검정시험 해설지 이미지에서 각 문제의 해설을 추출해줘.
반드시 아래 JSON 형식으로만 응답해 (다른 텍스트 없이):
{
  "results": [
    {
      "문제번호": 1,
      "해설": "정답 해설 전체 텍스트",
      "핵심개념": "이 문제의 핵심 개념 (한 줄)"
    }
  ]
}
- 이 페이지에 있는 모든 문제 해설 추출
- 핵심개념은 시대, 주제, 인물, 사건 등 핵심 키워드로 짧게
- JSON 외 텍스트 절대 출력 금지"""

    response = client.chat.completions.create(
        model="gpt-4o",
        messages=[{
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {
                    "url": f"data:image/png;base64,{b64_image}",
                    "detail": "high"
                }}
            ]
        }],
        max_tokens=4000,
        response_format={"type": "json_object"},
    )

    content = response.choices[0].message.content.strip()
    data = json.loads(content)
    return data.get("results", [])


# =============================================
# STEP 5: DB INSERT
# =============================================
def insert_to_db(questions_data, answers_data, explanations_data):
    print("[STEP 5] DB에 데이터 적재 중...")
    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor()

    inserted = 0
    qid_map = {}   # 문제번호 → question_id (STEP 6에서 사용)

    for q in questions_data:
        q_no = q["문제번호"]
        ans  = answers_data.get(q_no, {})
        exp  = explanations_data.get(q_no, {})

        if not ans:
            print(f"  ⚠ {q_no}번 정답 없음, 스킵")
            continue

        cur.execute("""
            INSERT INTO questions
              (q_score, era, topic, question_type, content, answer_no, answer_explanation, core_concept)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING question_id
        """, (
            ans["q_score"],
            "미분류",           # STEP 6에서 UPDATE
            "미분류",           # STEP 6에서 UPDATE
            "미분류",           # STEP 6에서 UPDATE
            q["문제내용"],
            ans["answer_no"],
            exp.get("해설", ""),
            exp.get("핵심개념", ""),
        ))
        question_id = cur.fetchone()[0]
        qid_map[q_no] = question_id

        choices = q.get("선택지", [])
        for i, choice_text in enumerate(choices):
            choice_no = i + 1
            clean = re.sub(r"^[①②③④⑤]\s*", "", choice_text).strip()
            is_answer = (choice_no == ans["answer_no"])
            cur.execute("""
                INSERT INTO question_options
                  (question_id, choice_no, content, is_answer, choice_explanation)
                VALUES (%s, %s, %s, %s, %s)
            """, (question_id, choice_no, clean, is_answer, None))

        inserted += 1
        print(f"  ✓ {q_no}번 문제 적재 완료 (question_id={question_id})")

    conn.commit()
    cur.close()
    conn.close()
    print(f"\n총 {inserted}개 문제 DB 적재 완료")
    return qid_map


# =============================================
# STEP 6: GPT-4o-mini로 era/topic/question_type 분류 → DB UPDATE
# =============================================
def classify_and_update(questions_data, explanations_data, qid_map):
    print("\n[STEP 6] era / topic / question_type 분류 중 (gpt-4o-mini)...")

    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor()
    updated_total = 0
    BATCH_SIZE = 10

    for i in range(0, len(questions_data), BATCH_SIZE):
        batch = questions_data[i:i + BATCH_SIZE]
        nums  = [q["문제번호"] for q in batch]
        print(f"  배치 {i // BATCH_SIZE + 1}: {nums[0]}~{nums[-1]}번 분류 중...")

        # 배치 텍스트 구성
        items_text = ""
        for q in batch:
            n   = q["문제번호"]
            exp = explanations_data.get(n, {})
            items_text += f"""
문제번호: {n}
문제내용: {q['문제내용']}
핵심개념: {exp.get('핵심개념', '')}
해설 요약: {exp.get('해설', '')[:150]}
---"""

        prompt = f"""아래 한국사능력검정시험 문제들을 분류해줘.
{QUESTION_TYPES}
{ERA_LIST}
{TOPIC_LIST}

[분류할 문제들]
{items_text}

반드시 아래 JSON 형식으로만 응답해:
{{
  "results": [
    {{"문제번호": 1, "question_type": "역사 자료의 분석 및 해석", "era": "고려", "topic": "정치"}}
  ]
}}
- question_type은 반드시 위 6개 중 하나
- era는 위 목록 중 하나
- topic은 위 목록 중 하나
- 모든 문제번호에 대해 결과 포함"""

        try:
            response = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": prompt}],
                max_tokens=2000,
                response_format={"type": "json_object"},
                temperature=0.1,
            )
            results = json.loads(response.choices[0].message.content).get("results", [])

            for r in results:
                q_no = r.get("문제번호")
                qid  = qid_map.get(q_no)
                if not qid:
                    continue

                era   = r.get("era", "미분류")
                topic = r.get("topic", "미분류")
                qt    = r.get("question_type", "역사 지식의 이해")

                # 허용되지 않는 era 값 보정
                valid_eras = {
                    "선사 시대", "고조선", "삼국 시대", "통일 신라", "발해",
                    "남북국 시대", "고려", "조선 전기", "조선 후기",
                    "개항기", "일제 강점기", "현대", "시대 통합",
                    "고구려", "백제", "신라",
                }
                if era not in valid_eras:
                    print(f"    ⚠ {q_no}번 era '{era}' → '미분류' 처리")
                    era = "미분류"

                cur.execute("""
                    UPDATE questions
                    SET question_type = %s, era = %s, topic = %s
                    WHERE question_id = %s
                """, (qt, era, topic, qid))
                updated_total += 1
                print(f"    ✓ {q_no}번: {qt} / {era} / {topic}")

        except Exception as e:
            print(f"  ✗ 배치 실패: {e}")

        time.sleep(0.5)

    conn.commit()
    cur.close()
    conn.close()
    print(f"\n총 {updated_total}개 분류 UPDATE 완료")


# =============================================
# MAIN
# =============================================
def main():
    # STEP 1: 답지
    answers = parse_answer_sheet()

    # STEP 2 & 3: 문제지 (전체 페이지)
    print("\n[STEP 2-3] 문제지 GPT-4o 추출 중...")
    all_questions = []
    doc = fitz.open(QUESTION_PDF)
    total_pages = len(doc)
    doc.close()

    for page_idx in range(0, total_pages):
        print(f"  문제지 {page_idx + 1}/{total_pages} 페이지 처리 중...")
        b64 = pdf_page_to_b64(QUESTION_PDF, page_idx)
        try:
            questions = extract_questions_from_page(b64, page_idx)
            all_questions.extend(questions)
            print(f"    → {len(questions)}개 문제 추출")
        except Exception as e:
            print(f"    ⚠ 파싱 실패: {e}")

    # STEP 4: 해설지 (첫 페이지는 정답표이므로 1번 인덱스부터)
    print("\n[STEP 4] 해설지 GPT-4o 추출 중...")
    all_explanations = {}
    doc = fitz.open(EXPLAIN_PDF)
    total_pages = len(doc)
    doc.close()

    for page_idx in range(1, total_pages):
        print(f"  해설지 {page_idx + 1}/{total_pages} 페이지 처리 중...")
        b64 = pdf_page_to_b64(EXPLAIN_PDF, page_idx)
        try:
            explanations = extract_explanations_from_page(b64)
            for exp in explanations:
                all_explanations[exp["문제번호"]] = exp
            print(f"    → {len(explanations)}개 해설 추출")
        except Exception as e:
            print(f"    ⚠ 파싱 실패: {e}")

    # 중간 저장 (실패 대비)
    os.makedirs(SAVE_DIR, exist_ok=True)
    with open(f"{SAVE_DIR}/all_questions.json", "w", encoding="utf-8") as f:
        json.dump(all_questions, f, ensure_ascii=False, indent=2)
    with open(f"{SAVE_DIR}/all_explanations.json", "w", encoding="utf-8") as f:
        json.dump(all_explanations, f, ensure_ascii=False, indent=2)
    print("\n중간 결과 JSON 저장 완료")

    # STEP 5: DB INSERT → question_id 매핑 반환
    qid_map = insert_to_db(all_questions, answers, all_explanations)

    # STEP 6: 분류 → DB UPDATE
    classify_and_update(all_questions, all_explanations, qid_map)

    # 최종 통계
    conn = psycopg2.connect(**DB_CONFIG)
    cur  = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM questions")
    total = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM questions WHERE era='미분류'")
    unclassified = cur.fetchone()[0]
    cur.close()
    conn.close()

    print(f"\n{'='*40}")
    print(f"ETL 완료: questions {total}개, 미분류 {unclassified}개")
    print(f"{'='*40}")


if __name__ == "__main__":
    main()
