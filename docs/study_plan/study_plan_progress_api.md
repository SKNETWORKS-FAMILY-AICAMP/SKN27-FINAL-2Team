# 학습계획 달성률 계산 연동 가이드

## 목적

학습계획 달성률은 더 이상 날짜, 시대, 유형, 주제를 추정 매칭해서 계산하지 않습니다.

학습계획 블록에서 시작한 문제풀이 기록만 `studyplan_id`, `study_plan_block_id`로 직접 연결하고, 제출 완료된 기록만 달성률에 반영합니다.

## 핵심 흐름

1. 마이페이지 학습계획 블록에서 `문제 풀기` 클릭
2. 문제 생성 조건에 `studyPlanId`, `studyPlanBlockId` 저장
3. 문제풀이 페이지에서 `POST /question/api/start/` 호출
4. 생성된 `solve_records`에 `studyplan_id`, `study_plan_block_id` 저장
5. 문제 제출 시 `POST /question/api/session/{session_id}/submit/` 호출
6. `solve_sessions.status = completed`가 되면 학습계획 달성률에 반영

## 참고 API

### 1. 문제 생성 API

```http
POST /question/api/start/
```

학습계획 블록에서 시작할 때 추가로 보내는 값:

```json
{
  "generation_mode": "detail",
  "studyplan_id": 1,
  "study_plan_block_id": "block-uuid",
  "count": 10
}
```

실제 화면에서는 마이페이지의 `문제 풀기` 버튼이 이 값을 `sessionStorage.questionGenerationCondition`에 저장하고, 문제풀이 페이지가 `/question/api/start/`로 전달합니다.

### 2. 문제풀이 제출 API

```http
POST /question/api/session/{session_id}/submit/
```

제출 완료 시 해당 세션이 `completed`가 되고, 그 세션의 `solve_records`가 학습계획 달성률 계산 대상이 됩니다.

제출 API 상세는 [practice_submit_api.md](./practice_submit_api.md)를 참고하면 됩니다.

### 3. 마이페이지 조회

```http
GET /analytics/mypage/
```

마이페이지에서 학습계획 달성률을 표시합니다. 내부적으로 `analytics.service.studyplan.calculate_record_based_plan_progress()`가 호출됩니다.

## DB 변경 사항

`solve_records`에 아래 컬럼이 추가되었습니다.

```sql
studyplan_id BIGINT NULL,
study_plan_block_id VARCHAR(36) NULL
```

의미:

- `studyplan_id`: `study_plan_mypage.studyplan_id`
- `study_plan_block_id`: `study_plan_mypage.study_plan_items` JSON 안의 `blockId`

기존 DB에 반영:

```powershell
Get-Content storage/postgresql/schema/alter_apply_latest.sql | docker exec -i skn27-postgres psql -U himate -d history_rag
```

새 DB 생성 기준은 `storage/postgresql/schema/init.sql`에 반영되어 있습니다.

## 계산 기준

달성률 계산 대상:

- 로그인 사용자 본인의 기록
- `solve_sessions.status = completed`
- `solve_records.selected_no IS NOT NULL`
- `solve_records.studyplan_id = 현재 학습계획 ID`
- `solve_records.study_plan_block_id = 현재 블록 blockId`

계산에서 제외되는 기록:

- 일반 문제풀이에서 생성한 기록
- 이어 풀기 상태인 `in_progress` 세션
- 학습계획 블록 ID가 없는 기록
- 미응답 문항

## 확인 SQL

학습계획 문제풀이로 생성된 기록 확인:

```sql
SELECT
    record_id,
    session_id,
    question_id,
    studyplan_id,
    study_plan_block_id,
    selected_no,
    is_correct
FROM solve_records
WHERE studyplan_id IS NOT NULL
ORDER BY record_id DESC;
```

특정 세션이 제출 완료되었는지 확인:

```sql
SELECT
    session_id,
    status,
    total_count,
    answer_rate,
    total_score,
    elapsed_sec
FROM solve_sessions
WHERE session_id = 세션ID;
```

## 팀원이 참고할 파일

- `app/templates/analytics/mypage.html`
  - 학습계획 블록의 `문제 풀기` 버튼
  - `studyPlanId`, `studyPlanBlockId` 전달

- `app/analytics/service/display.py`
  - 마이페이지 planner 데이터에 `blockId`, `classification`, `label` 포함

- `app/question/serializers.py`
  - `StartQuestionsRequest.studyplan_id`
  - `StartQuestionsRequest.study_plan_block_id`

- `app/question/views.py`
  - 학습계획 블록 검증
  - 블록 조건에 맞는 문제 생성
  - `solve_records`에 학습계획 연결값 저장

- `app/analytics/service/studyplan.py`
  - 학습계획 달성률 계산
  - 직접 연결된 records만 집계

## 주의사항

일반 문제풀이 기록은 학습계획 달성률에 자동 반영되지 않습니다.

반드시 마이페이지 학습계획 블록의 `문제 풀기` 버튼으로 시작한 문제풀이만 해당 학습계획 달성률에 반영됩니다.

이 방식은 같은 날짜와 같은 분류의 풀이 기록이 여러 학습계획에 중복 반영되는 문제를 막기 위한 구조입니다.
