# 78th Exam Test Question ETL

This guide explains how to generate and import the 78th Korean History test questions.

Run every command from the project root:

```powershell
cd C:\dev\project\SKN27-FINAL-2Team
```

## 1. Start PostgreSQL

```powershell
docker compose --env-file .env -f storage/postgresql/docker-compose.yml up -d
```

테스트 문제를 적재하기 전에 기존 DB의 최신 컬럼 변경사항을 반영합니다:

```powershell
Get-Content storage/postgresql/schema/alter_apply_latest.sql | docker exec -i skn27-postgres psql -U himate -d history_rag
```

## 2. Extract questions from images

`OPENAI_API_KEY` must exist in `.env`.

```powershell
python test/CJ/test_q/etl_78_test_questions.py --vision
```

Small test:

```powershell
python test/CJ/test_q/etl_78_test_questions.py --vision --limit 3
```

## 3. Extract explanations

```powershell
python test/CJ/test_q/etl_78_test_questions.py --explanations
```

## 4. Classify questions

Small test:

```powershell
python test/CJ/test_q/etl_78_test_questions.py --classify --classify-limit 3
```

Full classification:

```powershell
python test/CJ/test_q/etl_78_test_questions.py --classify
```

## 5. Import into DB

```powershell
python test/CJ/test_q/etl_78_test_questions.py --import-db
```

Important: `--import-db` truncates `solve_records`, `question_options`, and `questions`, then inserts the 78th test questions again. Do not run it on a DB that contains production data.

## Recommended Full Flow

```powershell
cd C:\dev\project\SKN27-FINAL-2Team

docker compose --env-file .env -f storage/postgresql/docker-compose.yml up -d

# 기존 PostgreSQL DB의 question/solve 테이블 컬럼 구조를 최신 상태로 맞춥니다.
Get-Content storage/postgresql/schema/alter_apply_latest.sql | docker exec -i skn27-postgres psql -U himate -d history_rag

python test/CJ/test_q/etl_78_test_questions.py --vision
python test/CJ/test_q/etl_78_test_questions.py --explanations
python test/CJ/test_q/etl_78_test_questions.py --classify
python test/CJ/test_q/etl_78_test_questions.py --import-db
```

## Output Files

```text
test/CJ/test_q/output_78/
|-- answer_key_78.json
|-- explanations_78.json
|-- vision_extracted_78.json
|-- classification_78.json
|-- db_seed_78.json
`-- question_images/
    |-- q_001.png
    `-- ...
```

## Current questions table columns

```text
question_no
q_score
era
topic
question_type
question_subtype
content
passage
question_image_path
answer_no
answer_explanation
core_concept
```

Removed columns:

```text
exam_round
exam_level
visual_note
parse_status
```

`question_image_path` remains because image-based exam questions still need a source image path.
