# 74~77회차 문제 데이터 전처리 및 DB 적재 안내

이 문서는 `ML_han_v1.json`과 공기출 해설 PDF를 이용해 74~77회차 문제 데이터를 새로 만들고 DB에 적재하는 방법을 정리합니다.

## 목적

- 74~77회차 총 200문항을 DB 적재용 데이터로 변환합니다.
- 시대(`era`)와 주제(`topic`)는 팀에서 정한 라벨 기준으로 통일합니다.
- 정답 번호는 정답표 기준으로 보정합니다.
- 선택지는 실제 1~5번 순서로 재구성합니다.
- 정답 해설은 정답 선지 해설만 저장합니다.
- 선택지별 해설은 `question_options.choice_explanation`에 저장합니다.

## 사용 파일

입력 데이터:

```text
ai/ml/ML_han_v1.json
test/CJ/test_docs/2. 해설지/공기출/
test/CJ/test_docs/4. 정답지/
```

실행 스크립트:

```text
test/CJ/test_q/new_test_data.py
```

결과 파일:

```text
test/CJ/test_q/output_new_test_data/prepared_ml_han_74_75_76_77.json
test/CJ/test_q/output_new_test_data/db_seed_ml_han_74_75_76_77.json
test/CJ/test_q/output_new_test_data/summary_ml_han_74_75_76_77.json
```

## 라벨 기준

시대(`era`)는 아래 값만 사용합니다.

```text
선사 시대
고조선
초기 국가
삼국 시대
남북국 시대
고려
조선
개항기
일제 강점기
현대
```

주제(`topic`)는 아래 값만 사용합니다.

```text
사건
인물
정치
제도
문화
사회
군사
경제
사상·종교
외교
```

## 실행 전 확인

가상환경을 활성화합니다.

```powershell
.venv\Scripts\activate
```

`.env`에 DB 접속 정보와 OpenAI API 키가 있어야 합니다.

```env
OPENAI_API_KEY=...
POSTGRES_DB=...
POSTGRES_USER=...
POSTGRES_PASSWORD=...
POSTGRES_HOST=...
POSTGRES_PORT=...
```

필요 패키지가 없으면 설치합니다.

```bash
pip install openai pdfplumber psycopg2-binary
```

## 1. 비용 없이 전처리만 실행

아래 명령어는 OpenAI API를 호출하지 않습니다.

```bash
python test/CJ/test_q/new_test_data.py
```

실행 결과로 DB 적재용 seed 파일이 생성됩니다.

```text
test/CJ/test_q/output_new_test_data/db_seed_ml_han_74_75_76_77.json
```

## 2. GPT로 시대/주제 재분류

아래 명령어는 OpenAI API 비용이 발생합니다.

```bash
python test/CJ/test_q/new_test_data.py --classify-openai --model gpt-4o --batch-size 10
```

이미 분류된 문항은 캐시를 사용하므로 다시 호출하지 않습니다.

캐시 파일:

```text
test/CJ/test_q/output_new_test_data/openai_labels_gpt-4o.json
```

## 3. 해설/정답표 캐시 새로 만들기

해설 PDF 또는 정답표 PDF를 다시 읽어야 할 때만 사용합니다.

```bash
python test/CJ/test_q/new_test_data.py --refresh-explanations --refresh-answers
```

## 4. DB 적재

기존 문제 데이터를 삭제하고 74~77회차 새 데이터를 적재합니다.

```bash
python test/CJ/test_q/new_test_data.py --rounds 74 75 76 77 --import-db
```

주의:

```text
이 명령어는 solve_records, question_options, questions 테이블을 비웁니다.
기존 풀이 기록과 문제 데이터가 삭제됩니다.
테스트 DB 또는 기존 데이터 삭제가 가능한 DB에서만 실행하세요.
```

## 5. 정상 적재 확인

실행 후 아래 파일을 확인합니다.

```text
test/CJ/test_q/output_new_test_data/summary_ml_han_74_75_76_77.json
```

정상이라면 아래 값이 포함됩니다.

```json
{
  "count": 200,
  "cached_openai_label_count": 200,
  "answer_key_count": 200,
  "missing_short_explanation_count": 0,
  "import_db": {
    "question_count": 200,
    "option_count": 1000
  }
}
```

DB에서 직접 확인하려면 아래 SQL을 실행합니다.

```sql
SELECT COUNT(*) FROM questions;
SELECT COUNT(*) FROM question_options;
SELECT COUNT(*) FROM solve_records;
```

예상 결과:

```text
questions: 200
question_options: 1000
solve_records: 0
```

라벨 확인:

```sql
SELECT era, COUNT(*) FROM questions GROUP BY era ORDER BY COUNT(*) DESC;
SELECT topic, COUNT(*) FROM questions GROUP BY topic ORDER BY COUNT(*) DESC;
```

`조선 전기`, `조선 후기`, `통합`, `지역` 같은 라벨이 나오면 안 됩니다.

## 데이터 구조

`questions.answer_explanation`:

```text
정답 번호에 해당하는 선지 해설만 저장합니다.
```

`question_options.choice_explanation`:

```text
각 선택지별 해설을 저장합니다.
```

예시:

```json
{
  "answer_no": 4,
  "answer_explanation": "도의가 당에서 선종을 배우고 귀국한 뒤 ...",
  "choices": [
    {
      "choice_no": 4,
      "is_answer": true,
      "choice_explanation": "도의가 당에서 선종을 배우고 귀국한 뒤 ..."
    }
  ]
}
```

## 권장 실행 순서

처음 실행하는 경우:

```bash
python test/CJ/test_q/new_test_data.py --refresh-explanations --refresh-answers
python test/CJ/test_q/new_test_data.py --classify-openai --model gpt-4o --batch-size 10
python test/CJ/test_q/new_test_data.py --rounds 74 75 76 77 --import-db
```

이미 캐시가 있고 DB만 다시 적재하는 경우:

```bash
python test/CJ/test_q/new_test_data.py --rounds 74 75 76 77 --import-db
```
