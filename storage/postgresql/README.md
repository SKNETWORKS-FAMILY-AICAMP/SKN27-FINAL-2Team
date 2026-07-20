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

## Import ML trend TOP5 data

Use this when importing:

```text
ai/ml/reports/trend_top5_for_db_2026-07-18.csv
```

Create or update the table first:

```powershell
Get-Content storage/postgresql/schema/alter_apply_latest.sql | docker exec -i skn27-postgres psql -U himate -d history_rag
```

Copy the CSV file into the PostgreSQL container:

```powershell
docker cp "C:\dev\project\SKN27-FINAL-2Team\ai\ml\reports\trend_top5_for_db_2026-07-18.csv" skn27-postgres:/tmp/trend_top5_for_db_2026-07-18.csv
```

Connect to PostgreSQL:

```powershell
docker exec -it skn27-postgres psql -U himate -d history_rag
```

Run this inside `psql`:

```sql
CREATE TEMP TABLE ml_trend_top5_import (
    target_round INT,
    recent5_rounds TEXT,
    source TEXT,
    source_name TEXT,
    usage TEXT,
    trend_type TEXT,
    rank INT,
    era TEXT,
    topic_train TEXT,
    topic TEXT,
    topic_summary TEXT,
    label TEXT,
    combo_label TEXT,
    combo_label_with_topic TEXT,
    count INT,
    ratio DOUBLE PRECISION,
    ratio_percent DOUBLE PRECISION
);

\copy ml_trend_top5_import FROM '/tmp/trend_top5_for_db_2026-07-18.csv' WITH (FORMAT csv, HEADER true, ENCODING 'UTF8');

INSERT INTO ml_trend_top5 (
    target_round,
    recent5_rounds,
    source,
    source_name,
    usage_text,
    trend_type,
    rank_no,
    era,
    topic_train,
    topic,
    topic_summary,
    label,
    combo_label,
    combo_label_with_topic,
    count_value,
    ratio,
    ratio_percent
)
SELECT
    target_round,
    recent5_rounds,
    source,
    source_name,
    usage,
    trend_type,
    rank,
    era,
    topic_train,
    topic,
    topic_summary,
    label,
    combo_label,
    combo_label_with_topic,
    count,
    ratio,
    ratio_percent
FROM ml_trend_top5_import
ON CONFLICT (target_round, source, trend_type, rank_no)
DO UPDATE SET
    recent5_rounds = EXCLUDED.recent5_rounds,
    source_name = EXCLUDED.source_name,
    usage_text = EXCLUDED.usage_text,
    era = EXCLUDED.era,
    topic_train = EXCLUDED.topic_train,
    topic = EXCLUDED.topic,
    topic_summary = EXCLUDED.topic_summary,
    label = EXCLUDED.label,
    combo_label = EXCLUDED.combo_label,
    combo_label_with_topic = EXCLUDED.combo_label_with_topic,
    count_value = EXCLUDED.count_value,
    ratio = EXCLUDED.ratio,
    ratio_percent = EXCLUDED.ratio_percent;
```

Check import count:

```sql
SELECT COUNT(*)
FROM ml_trend_top5;
```

Expected result:

```text
480
```

Check recent trend rows:

```sql
SELECT
    target_round,
    trend_type,
    source,
    rank_no,
    era,
    topic_train,
    topic,
    label,
    combo_label_with_topic,
    count_value,
    ratio_percent
FROM ml_trend_top5
WHERE source = 'recent5_actual'
ORDER BY target_round, trend_type, rank_no;
```

Usage notes:

```text
source = recent5_actual  : recent 5-round actual-label trend rows
source = predicted       : model-classified rows for validation
source = actual          : target-round actual-label rows for comparison

trend_type = era_topic_train : era + integrated topic TOP5
trend_type = era             : era TOP5
trend_type = topic_train     : integrated topic TOP5
trend_type = topic           : detailed topic TOP5
```
