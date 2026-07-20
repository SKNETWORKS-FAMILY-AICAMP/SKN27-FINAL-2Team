import re
import sys
from argparse import ArgumentParser
from json import dumps, loads
from pathlib import Path

import pandas as pd

sys.path.append(str(Path(__file__).resolve().parent.parent))

from common import get_historyterm_llm
from prep_json import prep_json


def get_history_terms(problems: list[dict]) -> list[dict]:
    """
    여러 한국사 시험 문항을 한 번의 LLM 호출로 처리해 용어를 추출하는 함수
    problems: [{"problem_id": ..., "full_text": ...}] 목록
    반환: [{"problem_id": ..., "terms": [용어 dict, ...]}] 목록
    """
    problem_blocks = "\n\n".join(
        f"[problem_id: {problem['problem_id']}]\n{problem['full_text']}"
        for problem in problems
    )

    prompt = """다음 한국사 시험 문항들에서 역사적으로 고유한 의미를 가진 핵심 용어만 추출하라

[핵심 판정 기준]
- 역사 교과서의 색인이나 백과사전의 표제어로 실릴 수 있는 개념만 용어다.
- 그 용어의 의미를 알아야 문제를 풀 수 있는 출제 포인트급 개념만 추출한다.
- 시대를 대표하는 교과서 개념어(뗀석기, 의례 도구, 철제 농기구)는 용어다.
  그러나 역사적 의미가 없는 일반 사물(수달가죽, 노둣돌)은 용어가 아니다.
- 확신이 없으면 추출하지 않는다. 많이 뽑는 것보다 핵심만 뽑는 것이 중요하다.

[규칙]
- 입력 원문에 실제로 등장한 표현만 추출한다
- 새로운 용어를 추론하거나 생성하지 않는다.
- 인물, 사건, 국가, 왕조, 제도, 정책, 단체, 기관, 문헌, 문화재, 조약, 사상,
  역사적 지명, 유물, 유적 등을 추출한다.
- 조사와 수식어는 제거하고 표제어 형태의 명사만 남긴다
- 지문뿐 아니라 선지에 등장한 용어도 모두 추출한다
- 모든 문항을 빠짐없이 처리한다. 뒤쪽 문항도 앞쪽과 같은 기준으로 꼼꼼히 추출한다.

[제외 대상 — 다음은 절대 추출하지 않는다]
- 자리 표시 기호: (가), (나), ㉠, ㄱ 등. 단독으로도, "(가) 정부"처럼 붙여서도 안 된다.
- 일반 명사: '설명', '내용', '사회', '정부', '사건', '국민', '백성', '기록' 등
- 역사적 의미가 없는 일반 사물·생활 표현: '수달가죽', '노둣돌', '대검', '혼례식 행렬' 등
- 작물·음식·상품 같은 일반 사물: '고구마', '감자', '담배', '인삼' 등.
  전래·재배가 시험에 나와도 사물 자체는 용어가 아니다 ('구황 작물' 같은 개념어만 추출한다)
- 인물·사건에 서술이 붙은 파생 표현: '고종 즉위', '상경 천도'는 안 되고 '고종', '상경'만 된다
- 일반적 지칭: '일본인 재정 고문', '중국 역사서', '대황제 폐하', '전라 감영군' 등
  (단 '메가타'처럼 고유명이면 추출한다)
- 연도·숫자·조항 번호: '1392', '제67조' 등
- 수량 표현: '6만여 명', '수백 명' 등
- 간지·연도 지칭: '을미', '계사년', '경인년' 등. 단 '을미사변'처럼 사건명이면 추출한다.
- 성씨만 있는 표현: '장씨', '민씨', '부여씨' 등
- 종류를 가리키는 상위 일반어: '조약', 'FTA', '헌법', '불상', '정당', '비석' 등.
  고유 이름이 붙은 것('강화도 조약', '제헌 헌법', '이불 병좌상')만 추출한다.

[카테고리 분류 규칙 — category는 아래 정의에 따라 붙인다]
- 인물: 실존 역사 인물의 이름
- 국가: 나라·정치체의 이름 (고구려, 조선, 대한제국, 대한민국 임시 정부).
  당, 송, 원, 명, 청 같은 외국의 나라 이름도 '왕조'가 아니라 '국가'다.
- 왕조: 왕실 가문 자체를 가리킬 때만 쓴다. 확실하지 않으면 '국가'로 분류한다.
- 사건: 전쟁·전투·정변·운동·사화 등 일어난 일
- 제도: 통치·사회 운영 방식. 법령·법률(회사령, 치안 유지법), 관직·관등(도승지, 부제학),
  연호(광덕, 건양), 과거제·신분제, 제천 행사(영고, 무천, 동맹)는 모두 '제도'다.
- 정책: 정부·집권자가 추진한 시책·사업 (토지 조사 사업, 새마을 운동, 북벌)
- 기관: 국가의 공식 조직. 관청(통리기무아문), 군사 조직(별무반, 삼별초, 한국광복군),
  관립 학교는 '기관'이다.
- 단체: 민간의 자발적 조직 (신간회, 조선 형평사, 정당, 학회, 비밀 결사)
- 문헌: 책·문서·선언문·격문. 신문·잡지(대한매일신보, 독립신문)도 '문헌'이다.
- 문화재: 탑·비석·그림·불상·건축물 등 문화유산 (경천사지 십층석탑, 부석사 무량수전)
- 유물: 발굴·출토된 옛 물건과 기물 (토기, 석기, 청동기). 화폐(상평통보, 은병)도 '유물'이다.
  작물·음식(고구마, 감자)은 유물이 아니라 추출 제외 대상이다.
- 유적: 터·성곽·궁궐·무덤 등 장소로 남은 흔적 (경복궁, 남한산성, 수원 화성)
- 지명: 역사적 지명 (벽란도, 서경, 강화도)
- 조약: 국가 간 맺은 개별 조약·협정의 고유 이름 (강화도 조약, 을사늑약)
- 사상: 학문·종교·이념 (동학, 양명학, 삼균주의, 천태종)

[필드 규칙]
- raw_term: 원문에 등장한 표기 그대로 적는다. 원문에 없는 표현을 만들지 않는다.
  원문에 오탈자가 있으면 오탈자 그대로 적는다.
- canonical_term: 표준 표기로 정규화한 이름. 오탈자는 여기서 교정한다.
  같은 대상을 가리키는 이칭은 가장 널리 쓰이는 표기 하나로 통일한다 (예: 활구 → 은병).
- category: 인물/사건/국가/왕조/제도/정책/단체/기관/문헌/문화재/조약/사상/지명/유물/유적 중
  반드시 하나만 사용한다. 이 목록에 없는 값을 만들지 않는다.

[출력 규칙]
- 아래 예시와 같은 JSON 배열만 출력한다. 다른 텍스트는 붙이지 않는다.
- 문항마다 problem_id를 붙여 해당 문항의 용어 목록을 출력한다.
- 같은 문항 안에서 같은 용어는 한 번만 출력한다.

[추출 예시 1 — 선사시대 문항]
입력 문항:
단양 수양개 유적에서 출토된 이 슴베찌르개는 주먹도끼와 함께 (가) 시대의 대표적인
유물 중 하나이다. 이 유적에서는 슴베찌르개와 함께 돌날과 몸돌 등의 뗀석기 출토되었다.
(가) 시대의 사회 모습으로 옳은 것은?
주로 동굴이나 막집에 거주하였다.
가락바퀴를 이용하여 실을 뽑았다.
명도전을 이용하여 중국과 교역하였다.
철제 농기구를 사용하여 농사를 지었다.
의례 도구로 청동 방울 등을 제작하였다.

추출: 단양 수양개 유적, 슴베찌르개, 주먹도끼, 돌날, 몸돌, 뗀석기, 막집, 가락바퀴,
명도전, 철제 농기구, 의례 도구, 청동 방울
제외: '(가) 시대', '사회 모습', '유물', '유적', '동굴', '교역', '농사', '중국'

[추출 예시 2 — 근현대사 문항]
입력 문항:
파고다 공원에 모인 수백 명의 학생들이 독립 만세를 외치자 공원 근처에 살던 시민들도
합류하였다. 학생들은 숨겨온 선언서들을 길가에 뿌렸고, 덕수궁 문앞에서 붕어하신
고종에게 조의를 표하였다.
다음 자료에 나타난 민족 운동에 대한 설명으로 옳은 것은?
조선 형평사의 주도로 전개되었다.
신간회에서 진상 조사단을 파견하였다.
조선 혁명 선언을 활동 지침으로 삼았다.
전개 과정에서 일제가 제암리 학살 등을 자행하였다.

추출: 파고다 공원, 덕수궁, 고종, 조선 형평사, 신간회, 조선 혁명 선언, 제암리 학살
제외: '학생들', '시민들', '독립 만세', '선언서', '민족 운동', '진상 조사단', '조의'

[출력 예시]
[
    {
        "problem_id": "cj_v41_0001",
        "terms": [
            {"raw_term": "슴베찌르개", "canonical_term": "슴베찌르개", "category": "유물"},
            {"raw_term": "조선 형평사", "canonical_term": "조선 형평사", "category": "단체"}
        ]
    }
]

[문항 목록]
""" + problem_blocks

    llm = get_historyterm_llm()
    response = llm.invoke(prompt)

    content = response.content.strip()
    if content.startswith("```"):
        content = content.strip("`")
        content = content.removeprefix("json").strip()

    results = loads(content)
    return results


def normalize_for_match(text: str) -> str:
    """
    원문 대조용 정규화 함수: 한글/한자/영숫자만 남기고 공백·괄호·문장부호 제거
    """
    return re.sub(r"[^0-9A-Za-z가-힣一-龥]", "", text)


def fuzzy_in_text(term: str, text: str, allowed_errors: int) -> bool:
    """
    term이 text 안에 등장하는지 검사하는 함수
    OCR 오탈자를 감안해 같은 길이 구간에서 allowed_errors 글자까지 달라도 등장으로 인정
    """
    if term in text:
        return True
    if allowed_errors == 0:
        return False

    term_len = len(term)
    for start in range(len(text) - term_len + 1):
        window = text[start:start + term_len]
        errors = sum(1 for a, b in zip(term, window) if a != b)
        if errors <= allowed_errors:
            return True
    return False


def count_terms(
    json_path: str,
    batch_size: int = 20,
    limit: int = 0,
    checkpoint_path: str = "terms_checkpoint.jsonl",
    max_retries: int = 2,
    thesaurus_path: str = "",
    raw_output: str = "",
) -> pd.DataFrame:
    """
    기출문제 json을 전처리(prep_json)한 뒤 용어를 추출해 집계하는 함수
    - batch_size: LLM 호출 한 번에 넣을 문항 수
    - limit: 0이면 전체, 양수면 앞에서부터 해당 개수의 문항만 처리 (소량 테스트용)
    - checkpoint_path: 배치별 결과를 jsonl로 중간 저장.
      실패 후 재실행하면 저장된 문항은 건너뛰고 실패분만 다시 호출함
    - max_retries: 배치 실패(파싱 에러 등) 시 재시도 횟수
    - thesaurus_path: 시소러스 csv 경로. 지정하면 시소러스 등재 용어는
      오탈자 허용 폭을 넓혀 환각 필터에서 구제함
    - raw_output: 지정하면 환각 필터 통과한 문항별 용어 목록을 json으로 저장
    - 같은 단어(공백 차이 무시)는 하나로 합침
    - count: 해당 용어가 등장한 문항 수 / problem_ids: 등장 문항 id 목록
    """
    df = prep_json(json_path)
    if limit > 0:
        df = df.head(limit)

    thesaurus_keys: set[str] = set()
    if thesaurus_path:
        thesaurus = pd.read_csv(thesaurus_path, encoding="utf-8")
        thesaurus_keys = set(thesaurus["term_name"].map(normalize_for_match))

    # 체크포인트에서 이미 처리된 문항 결과 불러오기
    results_by_problem: dict[str, list[dict]] = {}
    checkpoint = Path(checkpoint_path)
    if checkpoint.exists():
        for line in checkpoint.read_text(encoding="utf-8").splitlines():
            for item in loads(line)["results"]:
                results_by_problem.setdefault(item["problem_id"], item["terms"])
        print(f"체크포인트 발견: {len(results_by_problem)}문항 결과 재사용")

    remaining = df[~df["problem_id"].isin(results_by_problem.keys())]
    failed_batches = 0

    with open(checkpoint, "a", encoding="utf-8") as checkpoint_file:
        for start in range(0, len(remaining), batch_size):
            batch_df = remaining.iloc[start:start + batch_size]
            problems = [
                {"problem_id": row.problem_id, "full_text": row.full_text}
                for row in batch_df.itertuples()
            ]

            results = None
            for attempt in range(1, max_retries + 2):
                try:
                    results = get_history_terms(problems)
                    break
                except Exception as error:
                    print(f"배치 실패 (시도 {attempt}/{max_retries + 1}): {error}")

            if results is None:
                failed_batches += 1
                print(f"배치 건너뜀: {batch_df['problem_id'].iloc[0]} 외 {len(batch_df) - 1}건")
                continue

            checkpoint_file.write(dumps({"results": results}, ensure_ascii=False) + "\n")
            checkpoint_file.flush()
            for item in results:
                results_by_problem.setdefault(item["problem_id"], item["terms"])

            omitted = len(batch_df) - len(results)
            if omitted > 0:
                print(f"경고: 배치에서 {omitted}문항 응답 누락 — 재실행하면 다시 시도함")
            print(f"진행: {len(results_by_problem)}/{len(df)} 문항 처리 완료")

    if failed_batches > 0:
        print(f"실패한 배치 {failed_batches}개 — 같은 명령을 다시 실행하면 실패분만 재시도함")

    # 전체 결과 집계 (환각 필터 포함)
    aggregated: dict[str, dict] = {}
    hallucinated: list[dict] = []
    filtered_by_problem: list[dict] = []

    # (가), ㉠ 같은 마커가 하나 이상 이어진 형태 ("(가) (이)", "(가)~(마)" 포함)
    marker_unit = r"[(\[{]?[가나다라마바사아자차카타파하이㉠-㉭ㄱ-ㅎ①-⑮][)\]}]?"
    marker_pattern = re.compile(rf"(?:{marker_unit}[\s,·와과및~\-]*)+")
    # 연도·숫자로만 이루어진 표현 (1392, 1997년, 8·15 등)
    number_pattern = re.compile(r"[\d\s·.~년대월일]+")

    for row in df.itertuples():
        problem_id = row.problem_id
        if problem_id not in results_by_problem:
            continue
        compact_text = normalize_for_match(row.full_text)
        seen_in_problem: set[str] = set()
        problem_terms: list[dict] = []

        for term in results_by_problem[problem_id]:
            raw_key = normalize_for_match(term["raw_term"])
            canonical_key = normalize_for_match(term["canonical_term"])

            raw_stripped = term["raw_term"].strip()

            # 자리 표시 기호((가), ㉠, "(가) (이)" 등)와 연도·숫자 표현, 빈 용어는 무효
            # 서술구(4단어 이상 + '의 ' 포함)도 용어가 아니므로 제외
            is_invalid = (
                marker_pattern.fullmatch(raw_stripped)
                or number_pattern.fullmatch(raw_stripped)
                or len(raw_key) == 0
                or len(canonical_key) == 0
                or (raw_stripped.count(" ") >= 3 and "의 " in raw_stripped)
            )
            if is_invalid:
                hallucinated.append({"problem_id": problem_id, "raw_term": term["raw_term"]})
                continue

            # 오탈자 허용 폭: 4글자 이상 1글자, 10글자 이상 또는 시소러스 등재어(5글자 이상) 2글자
            allowed_errors = 0
            if len(raw_key) >= 4:
                allowed_errors = 1
            if len(raw_key) >= 10:
                allowed_errors = 2
            if len(raw_key) >= 5 and canonical_key in thesaurus_keys:
                allowed_errors = 2

            found = fuzzy_in_text(raw_key, compact_text, allowed_errors)
            if not found:
                found = fuzzy_in_text(canonical_key, compact_text, allowed_errors)
            if not found:
                hallucinated.append({"problem_id": problem_id, "raw_term": term["raw_term"]})
                continue

            key = term["canonical_term"].replace(" ", "")
            if key in seen_in_problem:
                continue
            seen_in_problem.add(key)
            problem_terms.append(term)

            if key in aggregated:
                aggregated[key]["count"] += 1
                aggregated[key]["problem_ids"].append(problem_id)
            elif key not in aggregated:
                aggregated[key] = {
                    "canonical_term": term["canonical_term"],
                    "category": term["category"],
                    "count": 1,
                    "problem_ids": [problem_id],
                }

        filtered_by_problem.append({"problem_id": problem_id, "terms": problem_terms})

    if raw_output:
        with open(raw_output, "w", encoding="utf-8") as raw_file:
            raw_file.write(dumps(filtered_by_problem, ensure_ascii=False, indent=2))
        print(f"문항별 용어 json 저장 완료: {raw_output}")

    if hallucinated:
        print(f"원문에 없는 용어 {len(hallucinated)}건 제거:")
        for dropped in hallucinated:
            print(f"  {dropped['problem_id']}: {dropped['raw_term']}")

    result = pd.DataFrame(aggregated.values())
    result = result.sort_values("count", ascending=False)
    result = result.reset_index(drop=True)
    return result


if __name__ == "__main__":
    parser = ArgumentParser(description="기출문제 json에서 역사 용어 추출·집계")
    parser.add_argument("json_path", help="기출문제 json 파일 경로")
    parser.add_argument("--output", default="", help="집계 결과를 저장할 csv 경로")
    parser.add_argument("--batch-size", type=int, default=20, help="LLM 호출당 문항 수")
    parser.add_argument("--limit", type=int, default=0, help="처리할 문항 수 (0이면 전체)")
    parser.add_argument("--checkpoint", default="terms_checkpoint.jsonl", help="중간 저장 jsonl 경로")
    parser.add_argument("--retries", type=int, default=2, help="배치 실패 시 재시도 횟수")
    parser.add_argument("--thesaurus", default="", help="시소러스 csv 경로 (오탈자 구제용)")
    parser.add_argument("--raw-output", default="", help="문항별 용어 json 저장 경로")
    cli_args = parser.parse_args()

    term_df = count_terms(
        cli_args.json_path,
        batch_size=cli_args.batch_size,
        limit=cli_args.limit,
        checkpoint_path=cli_args.checkpoint,
        max_retries=cli_args.retries,
        thesaurus_path=cli_args.thesaurus,
        raw_output=cli_args.raw_output,
    )
    print(term_df.head(20))
    print(f"고유 용어 수: {len(term_df)}")

    if cli_args.output:
        term_df.to_csv(cli_args.output, index=False, encoding="utf-8-sig")
        print(f"저장 완료: {cli_args.output}")
