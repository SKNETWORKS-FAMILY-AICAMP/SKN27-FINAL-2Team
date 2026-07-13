# PostgreSQL Setup Guide

Run every command from the project root:

```powershell
cd C:\dev\project\SKN27-FINAL-2Team
```

## First setup

Create `.env` if it does not exist:

```powershell
Copy-Item .env.example .env
```

Start PostgreSQL:

```powershell
docker compose -p storage --env-file .env -f storage/postgresql/docker-compose.yml up -d
```

Create initial tables:

```powershell
Get-Content storage/postgresql/schema/init.sql | docker exec -i skn27-postgres psql -U himate -d history_rag
```

Check Django DB connection:

```powershell
cd app
python manage.py check
cd ..
```

## Existing DB after pull

기존 DB를 사용하는 경우 아래 SQL 파일 하나만 실행해서 최신 컬럼 변경사항을 반영합니다:

```powershell
Get-Content storage/postgresql/schema/alter_apply_latest.sql | docker exec -i skn27-postgres psql -U himate -d history_rag
```

`alter_apply_latest.sql` applies:

```text
exam_data table for preprocessed past exam source data
questions.question_no
questions.passage
questions.image_caption
questions.question_image_path
questions.question_subtype
questions.question_type VARCHAR(50)
drop questions.exam_round
drop questions.exam_level
drop questions.visual_note
drop questions.parse_status
solve_sessions.recorded_date
solve_records.time_spent_ms
drop solve_records.time_spent_sec
```

## Past Exam Source Table

`exam_data` stores preprocessed past exam question data before it is converted
for service tables, ML features, or RAG chunks.

Initial import source:

```text
C:\dev\project\SKN27-FINAL-2Team\ai\ml\ML_han_v1.json
C:\dev\project\SKN27-FINAL-2Team\ai\ml\output\ml_han_features_v1.csv
```

Current initial data uses the already-preprocessed `ML_han_v1.json` for the
question text, material text, choices, answer choice, and answer number.

The classification labels below are matched by `round_no + question_no` from
`ml_han_features_v1.csv`, so `exam_data` follows the same labels used by the
current ML feature dataset:

```text
era, topic, question_type, question_subtype
```

Later versions should replace this with data extracted again from the original
past exam files, while keeping the same `exam_data` table shape.

Main columns:

```text
round_no, question_no
question_text, material_text
choices_json, distractor_choices_json
answer_choice, answer_no
era, topic, question_type, question_subtype, q_score
has_image, image_meta_json
answer_explanation, choice_explanations_json, explanation_source
```

Create/update the table schema first:

```powershell
Get-Content storage/postgresql/schema/alter_apply_latest.sql | docker exec -i skn27-postgres psql -U himate -d history_rag
```

Check conversion without writing to DB:

```powershell
python storage/postgresql/import_exam_data.py --dry-run
```

Use a different feature CSV if needed:

```powershell
python storage/postgresql/import_exam_data.py --features-csv ai/ml/output/ml_han_features_v1.csv --dry-run
```

Import all `ML_han_v1.json` rows into `exam_data`:

```powershell
python storage/postgresql/import_exam_data.py --truncate
```

Import only specific rounds:

```powershell
python storage/postgresql/import_exam_data.py --rounds 74 75 76 77 --truncate
```

Notes:

```text
--truncate clears exam_data only.
questions, question_options, solve_records are not touched.
Rows are upserted by round_no + question_no.
```

## Import 78th test questions

See:

```text
test/CJ/test_q/README.md
```

Short command flow:

```powershell
python test/CJ/test_q/etl_exam_test_questions.py --answers
python test/CJ/test_q/etl_exam_test_questions.py --vision
python test/CJ/test_q/etl_exam_test_questions.py --explanations
python test/CJ/test_q/etl_exam_test_questions.py --classify
python test/CJ/test_q/etl_exam_test_questions.py --import-db
```

Important: `--import-db` truncates `solve_records`, `question_options`, and `questions`.

## Useful commands

Check container:

```powershell
docker ps -a --filter "name=skn27-postgres"
```

Connect to DB:

```powershell
docker exec -it skn27-postgres psql -U himate -d history_rag
```

If container name conflicts:

```powershell
docker rm skn27-postgres
docker compose -p storage --env-file .env -f storage/postgresql/docker-compose.yml up -d
```

Do not use `docker compose down -v` unless you intentionally want to remove DB data.

## inspectdb

Use `inspectdb` only when creating Django models from existing DB tables for the first time.

For later column changes, update these files together:

```text
storage/postgresql/schema/init.sql
storage/postgresql/schema/alter_apply_latest.sql
app/question/models.py
```
