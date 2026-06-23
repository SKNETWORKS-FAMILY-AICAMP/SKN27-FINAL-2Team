# 78회 기출 테스트 ETL

이 폴더의 `etl_78_test_questions.py`는 `test/CJ/test_docs`에 있는 78회 한국사 문제지, 답지, 해설 PDF를 테스트용 DB 적재 데이터로 변환한다.

## 기본 실행

```powershell
python test/CJ/test_q/etl_78_test_questions.py
```

생성 위치:

```text
test/CJ/test_q/output_78/
|-- answer_key_78.json
|-- explanations_78.json
|-- question_images_78.json
|-- vision_extracted_78.json
|-- classification_78.json
|-- db_seed_78.json
`-- question_images/
    |-- q_001.png
    `-- ...
```

## 문제 이미지에서 지문/발문/선택지 추출

문항 이미지에서 실제 텍스트를 채우려면 OpenAI Vision을 사용한다. 프로젝트 루트의 `.env`에 `OPENAI_API_KEY`가 있으면 자동으로 읽는다.

```powershell
# 먼저 일부 문항만 테스트
python test/CJ/test_q/etl_78_test_questions.py --vision --limit 3

# 결과가 괜찮으면 전체 실행
python test/CJ/test_q/etl_78_test_questions.py --vision
```

결과는 `vision_extracted_78.json`에 저장되고, 이후 기본 실행에서도 재사용된다.

## 해설 추출

```powershell
# 먼저 일부 페이지만 테스트
python test/CJ/test_q/etl_78_test_questions.py --explanations --explanation-limit 3

# 결과가 괜찮으면 전체 실행
python test/CJ/test_q/etl_78_test_questions.py --explanations
```

기존 `explanations_78.json`이 있으면 기본 실행에서 보존해서 사용한다.

## 시대/주제/유형 분류

`era`, `topic`, `question_type`이 `미분류`로 남아 있으면 아래 명령으로 분류한다.

```powershell
# 먼저 3문항만 확인
python test/CJ/test_q/etl_78_test_questions.py --classify --classify-limit 3

# 전체 분류
python test/CJ/test_q/etl_78_test_questions.py --classify
```

사용하는 유형:

```text
역사 지식의 이해
연대기의 파악
역사 상황 및 쟁점의 인식
역사 자료의 분석 및 해석
역사 탐구의 설계 및 수행
결론의 도출 및 평가
```

분류 결과는 `classification_78.json`에 저장되고, `db_seed_78.json`에도 반영된다.

## DB 적재

PostgreSQL 컨테이너가 실행 중이고 `.env`의 DB 접속 정보가 맞는 상태에서 실행한다.

```powershell
python test/CJ/test_q/etl_78_test_questions.py --import-db
```

`--import-db`는 `storage/postgresql/schema/alter_questions_for_exam_assets.sql`을 먼저 적용한 뒤 `questions`, `question_options`에 데이터를 upsert한다.

## 권장 순서

```powershell
python test/CJ/test_q/etl_78_test_questions.py --vision --limit 3
python test/CJ/test_q/etl_78_test_questions.py --vision
python test/CJ/test_q/etl_78_test_questions.py --explanations
python test/CJ/test_q/etl_78_test_questions.py --classify --classify-limit 3
python test/CJ/test_q/etl_78_test_questions.py --classify
python test/CJ/test_q/etl_78_test_questions.py --import-db
```

## 자주 나는 오류

`app` 폴더에서 실행하면 경로를 못 찾을 수 있다. 항상 프로젝트 루트에서 실행한다.

```powershell
cd C:\dev\project\SKN27-FINAL-2Team
```

`OPENAI_API_KEY` 오류가 나면 `.env`에 키가 있는지 확인하거나 현재 터미널에 직접 지정한다.

```powershell
$env:OPENAI_API_KEY="..."
```
