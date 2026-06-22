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

# 6. Django 연결 확인
cd app
python manage.py check
```

- `inspectdb`는 이미 `models.py`에 반영되어 있으므로 팀원들은 다시 실행하지 않는다.
- `user` 앱 담당자가 `EmailVerificationCode`, `UserStudyProfile` 등을 migration으로 관리 중이면 그대로 `python manage.py migrate`를 먼저 실행한다.
- `init.sql`은 `CREATE TABLE IF NOT EXISTS` 방식이라 이미 존재하는 테이블은 건너뛴다.
- 진단평가 테스트 전에는 `user_accounts`에 `user_id=1` 사용자가 있고, `questions`가 20개 이상이며, 각 문제의 `question_options`가 있어야 한다.
