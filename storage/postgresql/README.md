# PostgreSQL 설정 및 데이터 적용 가이드

모든 명령은 프로젝트 루트에서 실행합니다.

```powershell
cd C:\dev\project\SKN27-FINAL-2Team
```

## 1. 최초 DB 구성

### 1.1 `.env` 파일 생성

`.env` 파일이 없다면 예시 파일을 복사합니다.

```powershell
Copy-Item .env.example .env
```

### 1.2 PostgreSQL 컨테이너 실행

```powershell
docker compose -p storage --env-file .env -f storage/postgresql/docker-compose.yml up -d
```

### 1.3 초기 테이블 생성

새 DB를 처음 구성할 때만 `init.sql`을 실행합니다.

```powershell
Get-Content storage/postgresql/schema/init.sql | docker exec -i skn27-postgres psql -U himate -d history_rag
```

### 1.4 Django DB 연결 확인

```powershell
cd app
python manage.py check
cd ..
```

## 2. 기존 DB에 최신 스키마 반영

이미 사용 중인 DB가 있다면 `init.sql`을 다시 실행하지 말고, 아래 SQL만 실행합니다.

```powershell
Get-Content storage/postgresql/schema/alter_apply_latest.sql | docker exec -i skn27-postgres psql -U himate -d history_rag
```

`alter_apply_latest.sql`은 여러 번 실행해도 안전하도록 작성합니다.

현재 반영 대상:

```text
exam_data 테이블
questions.question_no
questions.passage
questions.image_caption
questions.question_image_path
questions.question_subtype
questions.question_type VARCHAR(50)
questions.exam_round 제거
questions.exam_level 제거
questions.visual_note 제거
questions.parse_status 제거
solve_sessions.recorded_date
solve_sessions.review_type
solve_records.time_spent_ms
solve_records.studyplan_id
solve_records.study_plan_block_id
solve_records.time_spent_sec 제거
study_plan_mypage 상태/버전/기간/완료율/보관/삭제 컬럼
study_plan_mypage 사용자별 active 계획 유일성 인덱스
study_plan_mypage.weekly_report_data
ml_trend_top5 테이블
```

## 3. 과거 시험 원본 테이블

`exam_data`는 과거 기출 데이터를 서비스 테이블, ML feature, RAG chunk로 변환하기 전에 보관하는 원본성 테이블입니다.

초기 import 원본:

```text
C:\dev\project\SKN27-FINAL-2Team\ai\ml\ML_han_v1.json
C:\dev\project\SKN27-FINAL-2Team\ai\ml\output\ml_han_features_v1.csv
```

현재 초기 데이터는 이미 전처리된 `ML_han_v1.json`을 사용합니다.

사용 데이터:

```text
문제 본문
자료 본문
선택지
정답 선택지
정답 번호
```

분류 라벨은 `ml_han_features_v1.csv`에서 `round_no + question_no` 기준으로 매칭합니다.

분류 라벨:

```text
era
topic
question_type
question_subtype
```

이후 버전에서는 원본 기출 파일에서 다시 추출한 데이터를 사용하되, `exam_data` 테이블 구조는 유지합니다.

주요 컬럼:

```text
round_no, question_no
question_text, material_text
choices_json, distractor_choices_json
answer_choice, answer_no
era, topic, question_type, question_subtype, q_score
has_image, image_meta_json
answer_explanation, choice_explanations_json, explanation_source
```

### 3.1 테이블 스키마 생성/갱신

```powershell
Get-Content storage/postgresql/schema/alter_apply_latest.sql | docker exec -i skn27-postgres psql -U himate -d history_rag
```

### 3.2 DB 저장 없이 변환 확인

```powershell
python storage/postgresql/import_exam_data.py --dry-run
```

### 3.3 다른 feature CSV 사용

```powershell
python storage/postgresql/import_exam_data.py --features-csv ai/ml/output/ml_han_features_v1.csv --dry-run
```

### 3.4 전체 데이터 import

`ML_han_v1.json` 전체 행을 `exam_data`에 저장합니다.

```powershell
python storage/postgresql/import_exam_data.py --truncate
```

### 3.5 특정 회차만 import

```powershell
python storage/postgresql/import_exam_data.py --rounds 74 75 76 77 --truncate
```

주의사항:

```text
--truncate는 exam_data만 비웁니다.
questions, question_options, solve_records는 변경하지 않습니다.
데이터는 round_no + question_no 기준으로 upsert됩니다.
```

## 4. 78회 테스트 문제 import

자세한 내용은 아래 문서를 확인합니다.

```text
test/CJ/test_q/README.md
```

실행 흐름:

```powershell
python test/CJ/test_q/etl_exam_test_questions.py --answers
python test/CJ/test_q/etl_exam_test_questions.py --vision
python test/CJ/test_q/etl_exam_test_questions.py --explanations
python test/CJ/test_q/etl_exam_test_questions.py --classify
python test/CJ/test_q/etl_exam_test_questions.py --import-db
```

주의:

```text
--import-db는 solve_records, question_options, questions를 비웁니다.
실행 전 기존 데이터 삭제가 의도한 동작인지 반드시 확인합니다.
```

## 5. ML 최신 트렌드 TOP5 데이터 import

import 대상 CSV:

```text
ai/ml/reports/trend_top5_for_db_2026-07-18.csv
```

### 5.1 테이블 생성/갱신

```powershell
Get-Content storage/postgresql/schema/alter_apply_latest.sql | docker exec -i skn27-postgres psql -U himate -d history_rag
```

### 5.2 CSV 파일을 PostgreSQL 컨테이너로 복사

```powershell
docker cp "C:\dev\project\SKN27-FINAL-2Team\ai\ml\reports\trend_top5_for_db_2026-07-18.csv" skn27-postgres:/tmp/trend_top5_for_db_2026-07-18.csv
```

### 5.3 PostgreSQL 접속

```powershell
docker exec -it skn27-postgres psql -U himate -d history_rag
```

### 5.4 psql 안에서 import 실행

```sql
-- CSV를 먼저 임시 테이블로 적재합니다.
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

-- 컨테이너에 복사한 CSV를 임시 테이블로 읽어옵니다.
\copy ml_trend_top5_import FROM '/tmp/trend_top5_for_db_2026-07-18.csv' WITH (FORMAT csv, HEADER true, ENCODING 'UTF8');

-- 임시 테이블 데이터를 실제 서비스 테이블에 upsert합니다.
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

### 5.5 import 건수 확인

```sql
SELECT COUNT(*)
FROM ml_trend_top5;
```

예상 결과:

```text
480
```

### 5.6 최근 5회차 실제 트렌드 확인

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

### 5.7 값 의미

```text
source = recent5_actual  : 최근 5회차 실제 라벨 기반 트렌드
source = predicted       : 모델 예측 라벨 기반 검증 데이터
source = actual          : 예측 대상 회차의 실제 라벨 비교 데이터

trend_type = era_topic_train : 시대 + 통합 주제 TOP5
trend_type = era             : 시대 TOP5
trend_type = topic_train     : 통합 주제 TOP5
trend_type = topic           : 세부 주제 TOP5
```

## 6. 자주 쓰는 명령

### 6.1 컨테이너 상태 확인

```powershell
docker ps -a --filter "name=skn27-postgres"
```

### 6.2 DB 접속

```powershell
docker exec -it skn27-postgres psql -U himate -d history_rag
```

### 6.3 컨테이너 이름 충돌 시 재생성

```powershell
docker rm skn27-postgres
docker compose -p storage --env-file .env -f storage/postgresql/docker-compose.yml up -d
```

주의:

```text
DB 데이터를 삭제하려는 의도가 아니라면 docker compose down -v를 사용하지 않습니다.
```

## 7. inspectdb 사용 기준

`inspectdb`는 기존 DB 테이블에서 Django 모델을 처음 만들 때만 사용합니다.

이후 컬럼 변경은 관련 파일을 함께 직접 수정합니다.

```text
storage/postgresql/schema/init.sql
storage/postgresql/schema/alter_apply_latest.sql
app/question/models.py
app/analytics/models.py
```

