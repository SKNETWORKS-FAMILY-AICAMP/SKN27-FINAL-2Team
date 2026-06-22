# PostgreSQL 설정 가이드

## 사전 준비

- Docker Desktop 설치 및 실행
- 프로젝트 루트(`SKN27-FINAL-2Team`)에 `.env` 파일 존재 확인

---

## 1. 컨테이너 실행

프로젝트 루트에서 실행한다.

```powershell
cd C:\dev\project\SKN27-FINAL-2Team
docker compose -f storage/postgresql/docker-compose.yml --env-file .env up -d
```

실행 후 Docker Desktop에서 `skn27-postgres` 컨테이너가 초록불(running) 상태인지 확인한다.

---

## 2. 테이블 생성

컨테이너가 실행된 상태에서 아래 명령어로 전체 테이블을 생성한다.

```powershell
Get-Content storage/postgresql/schema/init.sql | docker exec -i skn27-postgres psql -U himate -d history_rag
```

이미 존재하는 테이블은 `skipping` 메시지와 함께 자동으로 건너뛰므로 여러 번 실행해도 안전하다.

---

## 3. 테이블 생성 확인

```powershell
docker exec -it skn27-postgres psql -U himate -d history_rag
```

접속 후 아래 명령어로 테이블 목록을 확인한다.

```
\dt
```

아래 12개 테이블이 모두 존재하면 정상이다.

| 테이블 | 설명 |
|--|--|
| `user_accounts` | 사용자 계정 |
| `email_verification_codes` | 이메일 인증 코드 |
| `user_study_profiles` | 사용자 학습 프로필 |
| `questions` | 문제 |
| `question_options` | 문제 선택지 |
| `solve_sessions` | 풀이 세션 (시험 전체) |
| `solve_records` | 문제별 풀이 기록 |
| `analytics` | 통계 |
| `note_mypage` | 오답노트 |
| `study_plan_mypage` | 학습 계획 |
| `chat_sessions` | 챗봇 세션 |
| `chat_messages` | 챗봇 메시지 |

psql 종료는 `\q`를 입력한다.

---

## 4. Django 연결 확인

`app/` 폴더에서 아래 명령어로 DB 연결 상태를 확인한다.

```powershell
cd app
python manage.py check
```

에러 없이 `System check identified no issues` 가 출력되면 정상이다.

---

## 주의사항

- `init.sql`은 테이블 구조 변경 시 업데이트된다. 팀원이 테이블을 추가/수정했다면 반드시 **2번 명령어를 다시 실행**한다.
- `.env` 파일은 git에 포함되지 않으므로 팀원에게 별도로 공유받는다.
- PowerShell에서 `>` 연산자로 파일을 저장할 경우 UTF-16 인코딩 문제가 발생할 수 있다. 파일 저장 시 반드시 UTF-8로 저장한다.
