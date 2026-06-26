# 진단평가 분석 저장 흐름 정리

## 목적

진단평가 제출 API 안에서 직접 analytics 데이터를 만들던 로직을 제거하고, 완료된 세션을 기준으로 공통 분석 스냅샷을 저장하도록 정리했습니다.

이제 진단평가와 문제풀이 모두 같은 서비스 함수인 `create_session_snapshot(session_id)`를 사용합니다.

## 변경 요약

### 기존 흐름

`diagnosis_submit`에서 다음 작업을 한 번에 처리했습니다.

- 답안 저장
- 정답 여부 계산
- 총점 계산
- 세션 완료 처리
- `era`, `type` 기준 analytics 직접 생성
- `Analytics.objects.bulk_create()` 직접 호출

### 변경 후 흐름

`diagnosis_submit`는 제출 처리만 담당합니다.

- 답안 저장
- 정답 여부 계산
- 총점 계산
- 세션 완료 처리
- 완료된 세션 ID로 공통 분석 스냅샷 저장 함수 호출

analytics 저장은 아래 공통 서비스에서만 처리합니다.

```python
from analytics.service.analysis_snapshot import create_session_snapshot

create_session_snapshot(session.session_id)
```

## 참고해야 할 API

### 1. 진단평가 제출 API

```http
POST /diagnosis/api/submit/
```

역할:

- 진단평가 답안 전체 제출
- `solve_records`에 문항별 답안, 정오답, 풀이 시간 저장
- `solve_sessions`를 `completed`로 변경
- 총점, 정답률, 풀이 시간 저장
- 세션 완료 직후 `create_session_snapshot(session_id)` 호출

주의:

- 이 API 안에서 `Analytics.objects.bulk_create()`를 직접 호출하지 않습니다.
- analytics 저장이 필요하면 반드시 `create_session_snapshot()` 경로를 사용합니다.

### 2. 문제풀이 제출 API

```http
POST /question/api/session/{session_id}/submit/
```

역할:

- 문제풀이 답안 전체 제출
- `solve_records`에 문항별 답안, 정오답, 풀이 시간 저장
- `solve_sessions`를 `completed`로 변경
- 총점, 정답률, 풀이 시간 저장
- 세션 완료 직후 `create_session_snapshot(session_id)` 호출

진단평가와 동일한 분석 저장 흐름을 사용합니다.

## 공통 분석 저장 서비스

### 함수

```python
create_session_snapshot(session_id)
```

위치:

```text
app/analytics/service/analysis_snapshot.py
```

역할:

- `completed` 상태인 세션만 분석 대상으로 사용
- 해당 세션의 `solve_records`를 기준으로 analytics row 생성
- 같은 세션의 기존 session analytics row는 삭제 후 재생성
- 하나의 제출 실행은 같은 `analysis_run_id`로 묶음

## analytics 저장 단위

세션 제출 완료 후 analytics 테이블에는 다음 단위가 저장됩니다.

| analysis_unit | 기준 컬럼 | 설명 |
| --- | --- | --- |
| `overall` | 전체 | 세션 전체 결과 |
| `era` | `solve_records.era` | 시대별 결과 |
| `type` | `solve_records.q_type` | 대유형별 결과 |
| `topic` | `solve_records.topic` | 주제별 결과 |

## analytics 주요 저장 컬럼

| 컬럼 | 저장 내용 |
| --- | --- |
| `user_id` | 제출한 사용자 ID |
| `session_id` | 완료된 세션 ID |
| `analysis_scope` | `session` |
| `analysis_run_id` | 같은 제출 분석 묶음 ID |
| `analysis_unit` | `overall`, `era`, `type`, `topic` |
| `classification` | 전체, 시대, 유형, 주제 |
| `key_concept` | 분석 대상 이름 |
| `total_count` | 전체 문항 수 |
| `correct_count` | 정답 수 |
| `wrong_count` | 오답 수 |
| `answer_rate` | 정답률 |
| `wrong_rate` | 오답률 |
| `avg_time_sec` | 평균 풀이 시간 |
| `period_start` | 세션 기록일 |
| `period_end` | 세션 기록일 |
| `created_at` | 분석 생성 시각 |

## 계산 기준

### 정답률

```text
correct_count / total_count
```

### 오답률

```text
wrong_count / total_count
```

### 평균 풀이 시간

```text
solve_records.time_spent_ms 평균값을 초 단위로 변환
```

## 팀원이 개발할 때 참고할 부분

### 진단평가 결과 화면

진단평가 결과 화면에서 취약 영역을 보여줄 때는 직접 계산하기보다 analytics 테이블의 session 분석 결과를 사용할 수 있습니다.

조회 기준:

```sql
SELECT *
FROM analytics
WHERE session_id = {session_id}
  AND analysis_scope = 'session'
ORDER BY analysis_unit, wrong_rate DESC;
```

취약 TOP 3 예시:

```sql
SELECT
    analysis_unit,
    classification,
    key_concept,
    total_count,
    wrong_count,
    wrong_rate
FROM analytics
WHERE session_id = {session_id}
  AND analysis_scope = 'session'
  AND analysis_unit IN ('era', 'type', 'topic')
ORDER BY wrong_rate DESC, wrong_count DESC, total_count DESC
LIMIT 3;
```

### 마이페이지/학습계획

마이페이지나 학습계획에서 사용자 누적 취약점을 볼 때는 세션 하나가 아니라 여러 completed 세션을 기준으로 별도 집계하거나, weekly/monthly/total snapshot을 사용할 수 있습니다.

세션 결과 화면:

- `analysis_scope = 'session'`
- `session_id` 있음

누적 분석:

- `analysis_scope = 'weekly'`, `monthly`, `total`
- `session_id` 없음

학습계획 분석:

- `analysis_scope = 'study_plan_base'`
- `analysis_scope = 'study_plan_result'`
- `studyplan_id` 있음

## 검증 방법

### 1. Django check

```powershell
cd C:\dev\project\SKN27-FINAL-2Team\app
python manage.py check
```

### 2. 진단평가 제출 후 세션 확인

```sql
SELECT
    session_id,
    session_type,
    status,
    answer_rate,
    total_score,
    elapsed_sec
FROM solve_sessions
WHERE session_id = {session_id};
```

기대값:

- `session_type = diagnostic`
- `status = completed`
- `answer_rate` 저장
- `total_score` 저장
- `elapsed_sec` 저장

### 3. 진단평가 제출 후 analytics 확인

```sql
SELECT
    analytics_id,
    session_id,
    analysis_scope,
    analysis_run_id,
    analysis_unit,
    classification,
    key_concept,
    total_count,
    correct_count,
    wrong_count,
    answer_rate,
    wrong_rate,
    avg_time_sec
FROM analytics
WHERE session_id = {session_id}
ORDER BY analysis_unit, wrong_rate DESC;
```

기대값:

- `analysis_scope = session`
- 같은 제출 결과는 동일한 `analysis_run_id`
- `overall`, `era`, `type`, `topic` 단위 row 존재
- `total_count`, `correct_count`, `wrong_count`, `answer_rate`, `wrong_rate`, `avg_time_sec` 저장

## 주의사항

- 진단평가 제출 로직의 점수 계산 방식은 변경하지 않았습니다.
- 문제풀이 제출 로직의 채점 방식도 변경하지 않았습니다.
- 변경된 부분은 analytics 저장 위치와 저장 단위입니다.
- 앞으로 analytics 저장 로직을 추가할 때 view에서 직접 `Analytics.objects.bulk_create()`를 호출하지 말고, `analytics.service.analysis_snapshot`의 공통 함수를 사용해야 합니다.
