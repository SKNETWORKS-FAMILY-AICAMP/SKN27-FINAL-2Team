# 기출 테스트 문제 ETL

76회, 77회 한국사능력검정시험 심화 PDF를 테스트 문제 데이터로 전처리합니다.

78회 데이터는 사용하지 않습니다.

모든 명령어는 프로젝트 루트에서 실행합니다.

```powershell
cd C:\dev\project\SKN27-FINAL-2Team
```

## 1. PostgreSQL 실행

```powershell
docker compose -p storage --env-file .env -f storage/postgresql/docker-compose.yml up -d
```

## 2. 최신 DB 컬럼 반영

기존 PostgreSQL DB의 `questions`, `question_options`, `solve_sessions`, `solve_records` 컬럼 구조를 최신 상태로 맞춥니다.

```powershell
Get-Content storage/postgresql/schema/alter_apply_latest.sql | docker exec -i skn27-postgres psql -U himate -d history_rag
```

## 3. 답안 추출

답지 PDF에서 1~50번 정답 번호와 배점을 추출합니다.

```powershell
python test/CJ/test_q/etl_exam_test_questions.py --answers
```

## 4. 문제/지문/선택지 추출

문제지 PDF를 문항 단위 이미지로 자른 뒤, Vision으로 발문, 지문, 선택지, 이미지 설명을 추출합니다.

현재 테스트 데이터는 실제 이미지를 화면에 사용하지 않습니다. 이미지 지문과 이미지 선택지는 모두 텍스트 캡션으로 대체합니다.

```text
questions.passage                 = 이미지 지문을 글로 설명한 내용
questions.image_caption           = 이미지 핵심 단서 요약
question_options.content          = 이미지 선택지를 글로 설명한 내용
questions.question_image_path     = 빈 값
question_options.choice_image_path = 빈 값
```

`.env`에 `OPENAI_API_KEY`가 있어야 합니다.

소량 테스트:

```powershell
python test/CJ/test_q/etl_exam_test_questions.py --vision --limit 3
```

전체 추출:

```powershell
python test/CJ/test_q/etl_exam_test_questions.py --vision
```

## 5. 해설 추출

해설 PDF에서 문항별 해설과 핵심 개념을 추출합니다.

소량 테스트:

```powershell
python test/CJ/test_q/etl_exam_test_questions.py --explanations --explanation-limit 3
```

전체 추출:

```powershell
python test/CJ/test_q/etl_exam_test_questions.py --explanations
```

## 6. 분류 파일 생성

추출 결과를 현재 `questions` 테이블 컬럼 형식에 맞춰 `db_seed` 파일로 다시 생성합니다.

```powershell
python test/CJ/test_q/etl_exam_test_questions.py --classify
```

## 7. DB 적재

```powershell
python test/CJ/test_q/etl_exam_test_questions.py --import-db
```

주의: `--import-db`는 `solve_records`, `question_options`, `questions` 데이터를 비우고 76회/77회 테스트 문제를 다시 넣습니다. 운영 데이터가 있는 DB에서는 실행하지 마세요.

## 전체 실행 순서

```powershell
cd C:\dev\project\SKN27-FINAL-2Team

docker compose -p storage --env-file .env -f storage/postgresql/docker-compose.yml up -d

Get-Content storage/postgresql/schema/alter_apply_latest.sql | docker exec -i skn27-postgres psql -U himate -d history_rag

python test/CJ/test_q/etl_exam_test_questions.py --answers
python test/CJ/test_q/etl_exam_test_questions.py --vision
python test/CJ/test_q/etl_exam_test_questions.py --explanations
python test/CJ/test_q/etl_exam_test_questions.py --classify
python test/CJ/test_q/etl_exam_test_questions.py --import-db
```

## 생성 파일

```text
test/CJ/test_q/output_exam/
|-- db_seed_all.json
|-- summary.json
|-- round_76/
|   |-- answer_key_76.json
|   |-- vision_questions_76.json
|   |-- explanations_76.json
|   |-- db_seed_76.json
|   `-- question_images/
|-- round_77/
|   |-- answer_key_77.json
|   |-- vision_questions_77.json
|   |-- explanations_77.json
|   |-- db_seed_77.json
|   `-- question_images/
```

## DB 컬럼 기준

`questions`:

```text
question_no
q_score
era
topic
question_type
question_subtype
content
passage
image_caption
question_image_path
answer_no
answer_explanation
core_concept
```

`question_options`:

```text
choice_no
content
choice_image_path
is_answer
choice_explanation
```

## image_caption 용도

`image_caption`은 이미지 기반 문제를 풀기 위해 필요한 시각 정보를 자연어로 설명하는 컬럼입니다.

예시:

```text
이미지 설명
핵심 시각 단서
역사 키워드
이미지에서 추론해야 하는 내용
```

`source_exam`은 DB에 저장하지 않습니다. 데이터 검수와 SLLM 학습 추적을 위해 seed/output 파일에만 유지합니다.
