# PostgreSQL 설정 가이드

## 팀원 적용 순서 요약

처음 세팅하거나 pull 받은 뒤 DB를 맞출 때는 아래 순서로 진행한다.

```powershell
# 1. 프로젝트 루트에서 .env 준비
Copy-Item .env.example .env

# 2. 패키지 설치
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt

# 3. PostgreSQL 컨테이너 실행
docker compose -f storage/postgresql/docker-compose.yml --env-file .env up -d

# 4. Django migration 실행
cd app
python manage.py migrate
cd ..

# 5. SQL 테이블 생성
Get-Content storage/postgresql/schema/init.sql | docker exec -i skn27-postgres psql -U himate -d history_rag

# 6. 기존 DB에 문제 지문/이미지 컬럼만 추가해야 하는 경우
Get-Content storage/postgresql/schema/alter_questions_for_exam_assets.sql | docker exec -i skn27-postgres psql -U himate -d history_rag

# 7. Django 연결 확인
cd app
python manage.py check
cd ..
```

## 진단평가 테스트 문제 적재

78회 심화 기출 테스트 데이터는 `test/CJ/test_q`의 ETL 스크립트로 만든다.

```powershell
# 프로젝트 루트에서 실행
python test/CJ/test_q/etl_78_test_questions.py --vision

# 시대, 주제, 유형 분류만 먼저 3문항 테스트
python test/CJ/test_q/etl_78_test_questions.py --classify --classify-limit 3

# 분류 결과가 괜찮으면 전체 분류
python test/CJ/test_q/etl_78_test_questions.py --classify

# DB 스키마 확장 후 questions/question_options에 적재
python test/CJ/test_q/etl_78_test_questions.py --import-db
```

`--import-db`는 내부에서 `alter_questions_for_exam_assets.sql`도 한 번 실행한다. 그래도 팀원이 수동으로 DB 상태를 맞춰야 할 때를 대비해 위 6번 명령을 따로 남겨둔다.

## 참고

- `inspectdb`는 이미 `app/question/models.py`에 반영되어 있으므로 팀원들이 다시 실행하지 않아도 된다.
- `user` 앱 담당자가 `EmailVerificationCode`, `UserStudyProfile` 등을 migration으로 관리 중이면 그대로 `python manage.py migrate`를 먼저 실행한다.
- `init.sql`은 `CREATE TABLE IF NOT EXISTS` 방식이라 이미 존재하는 테이블은 건너뛴다. 단, 기존 테이블에 새 컬럼을 추가하는 작업은 `alter_questions_for_exam_assets.sql`이 담당한다.
- 진단평가 테스트 전에는 `questions`와 `question_options`에 데이터가 있어야 한다.
