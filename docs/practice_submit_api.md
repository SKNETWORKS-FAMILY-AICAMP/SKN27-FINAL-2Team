# 문제풀이 제출 저장 로직 개선

## 목적

문제풀이(practice) 제출 시 현재 문항만 저장되는 위험을 없애고, 사용자가 푼 전체 문항 답안을 서버에서 한 번에 확정 저장합니다.

진단평가(diagnostic) 제출 로직은 변경하지 않았습니다.

## 변경된 API

### 문제풀이 제출 API

```http
POST /question/api/session/{session_id}/submit/
```

사용처:
- 문제풀이 화면에서 `제출` 버튼 클릭 시 호출
- 제한 시간이 끝나 자동 제출될 때도 동일하게 호출

요청 예시:

```json
{
  "elapsed_sec": 1234,
  "answers": [
    {
      "question_id": 1,
      "choice_id": 5,
      "time_spent_ms": 15000
    },
    {
      "question_id": 2,
      "choice_id": null,
      "time_spent_ms": 8000
    }
  ]
}
```

응답 예시:

```json
{
  "session_id": 10,
  "status": "completed",
  "total_count": 20,
  "answered_count": 18,
  "correct_count": 12,
  "answer_rate": 60.0,
  "total_score": 24
}
```

## 저장 기준

- `choice_id`로 실제 선택지를 찾습니다.
- 정답 여부는 `question_options.is_answer` 기준으로 계산합니다.
- DB의 `questions.answer_no` 값은 변경하지 않습니다.
- 선택지 랜덤 표시와 관계없이 `choice_id` 기준으로 채점합니다.

## 저장되는 테이블

### solve_records

제출 시 세션에 포함된 모든 문항을 갱신합니다.

- 응답 문항
  - `selected_no`: 선택한 원본 선택지 번호
  - `is_correct`: 선택한 선택지의 `is_answer`
  - `time_spent_ms`: 문항별 풀이 시간

- 미응답 문항
  - `selected_no`: `NULL`
  - `is_correct`: `false`
  - `time_spent_ms`: 전달된 값 또는 `NULL`

### solve_sessions

전체 문항 저장이 끝난 뒤 세션을 완료 처리합니다.

- `status`: `completed`
- `elapsed_sec`: 전체 풀이 시간
- `answer_rate`: 정답률
- `total_score`: 획득 점수

## 기존 자동 저장 API와 차이

### 자동 저장 API

```http
PATCH /question/api/session/{session_id}/answer/
```

역할:
- 문제 풀이 중 선택, 이동, 나가기, 새로고침 대비 저장
- 한 문항의 답안과 시간만 임시 저장
- 세션을 `completed`로 바꾸지 않음

### 제출 API

```http
POST /question/api/session/{session_id}/submit/
```

역할:
- 제출 시 전체 문항 답안 목록을 서버에 전달
- transaction 안에서 모든 `solve_records` 갱신
- `solve_sessions`를 `completed`로 확정

## 검증 방법

### 1. Django 정적 검사

```powershell
python manage.py check
```

또는 로컬 Python 경로 문제가 있을 경우:

```powershell
$env:PYTHONPATH='C:\dev\project\SKN27-FINAL-2Team\.venv\Lib\site-packages'
C:\Users\Playdata\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe app\manage.py check
```

### 2. 화면 테스트

1. 서버 실행

```powershell
cd C:\dev\project\SKN27-FINAL-2Team\app
python manage.py runserver
```

2. 로그인 후 문제풀이 생성
3. 일부 문항만 선택하고 일부 문항은 미응답으로 둠
4. 제출 클릭
5. 결과 페이지가 정상 표시되는지 확인

### 3. DB 확인

제출한 세션 ID를 기준으로 확인합니다.

```sql
SELECT
    session_id,
    status,
    answer_rate,
    total_score,
    elapsed_sec
FROM solve_sessions
WHERE session_id = {session_id};
```

기대값:
- `status = completed`
- `answer_rate` 값 존재
- `total_score` 값 존재

문항별 저장 확인:

```sql
SELECT
    record_id,
    question_id,
    selected_no,
    is_correct,
    time_spent_ms
FROM solve_records
WHERE session_id = {session_id}
ORDER BY record_id;
```

기대값:
- 응답 문항은 `selected_no`가 있음
- 미응답 문항은 `selected_no IS NULL`
- 미응답 문항은 `is_correct = false`
- 문항별 `time_spent_ms`가 저장됨

### 4. 결과 API 확인

```http
GET /question/api/session/{session_id}/result/
```

기대값:
- completed 세션이면 결과 반환
- 선택 답안, 정답 여부, 점수, 해설이 표시됨

## 팀원이 참고할 파일

- `app/question/urls.py`
  - `api/session/<int:session_id>/submit/` URL 추가
- `app/question/serializers.py`
  - `SubmitAnswersRequest`
  - `SubmitAnswersResponse`
- `app/question/views.py`
  - `question_submit_session`
- `app/templates/question/question_exam.html`
  - `submitPracticeExam()`

