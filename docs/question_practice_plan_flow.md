# 문제 풀이/학습 계획 연동 정리

## 화면 변경

- 문제 생성 화면에서 `기본`, `어려움` 모드를 제거하고 `학습 계획 문제 풀기`, `상세 설정` 모드만 남겼다.
- `학습 계획 문제 풀기`는 아직 학습 플래너 기능이 완성되지 않았기 때문에, 플래너가 전달한 조건이 있을 때만 문제 생성을 시작한다.
- `문제 불러오기` 버튼은 모달을 열고 진행 중인 `진단 평가`, `문제 풀이`, `학습 계획 문제 풀이` 세션을 나눠 보여준다.

## 학습 플래너에서 넘겨야 하는 조건

학습 플래너는 문제 풀이 화면으로 연결할 때 아래 값을 `sessionStorage`에 저장하면 된다.

```js
sessionStorage.setItem("studyPlanQuestionCondition", JSON.stringify({
  generationMode: "study_plan",
  studyPlanId: 1,
  studyPlanBlockId: "block-uuid-or-key"
}));
window.location.href = "/question/";
```

문제 생성 화면에서 `학습 계획 문제 풀기`를 누르면 위 조건을 읽어 `/question/exam/?new=1`로 이동하고, 실제 생성 요청은 공통 문제 풀이 화면에서 `/question/api/start/`로 전달된다.

## 문제 생성 API

### `POST /question/api/start/`

문제 풀이 세션을 생성한다.

일반 상세 설정 요청:

```json
{
  "generation_mode": "detail",
  "eras": ["고려"],
  "topics": ["문화"],
  "difficulties": ["중"],
  "question_types": ["역사 자료의 분석 및 해석"],
  "question_subtypes": ["사료"],
  "count": 20
}
```

학습 계획 요청:

```json
{
  "generation_mode": "study_plan",
  "studyplan_id": 1,
  "study_plan_block_id": "block-uuid-or-key"
}
```

학습 계획 요청은 로그인 사용자가 필요하며, `studyplan_id`, `study_plan_block_id`가 모두 있어야 한다.

## 이어풀기/저장 API

### 문제 풀이

- `GET /question/api/sessions/in-progress/`
  - 로그인 사용자의 진행 중 문제 풀이 세션 목록을 반환한다.
  - 응답의 `session_source`가 `practice`면 일반 문제풀이, `study_plan`이면 학습 계획 문제풀이이다.
- `GET /question/api/session/<session_id>/`
  - 저장된 문제 풀이 세션의 문항, 선택 답안, 남은 시간을 반환한다.
- `PATCH /question/api/session/<session_id>/answer/`
  - 현재 문항 선택 답안, 문항별 풀이 시간, 세션 경과 시간을 임시 저장한다.

### 진단 평가

- `GET /diagnosis/api/sessions/in-progress/`
  - 로그인 사용자의 진행 중 진단 평가 세션 목록을 반환한다.
- `GET /diagnosis/api/session/<session_id>/`
  - 저장된 진단 평가 세션의 문항, 선택 답안, 남은 시간을 반환한다.
- `PATCH /diagnosis/api/session/<session_id>/answer/`
  - 진단 평가 풀이 중 선택 답안과 풀이 시간을 임시 저장한다.

## 저장 정책

- 문제 풀이는 기존처럼 새 문제지를 생성할 때 진행 중 문제 풀이 세션을 정리한다.
- 진단 평가는 새 진단평가를 시작할 때 이전 진행 중 진단 세션을 삭제해 저장 진단지는 1개만 유지한다.
- 제출이 완료되면 진행 중 로컬 저장 상태는 삭제되고, DB 세션 상태는 `completed`가 된다.

## 확인 방법

1. `python app/manage.py check` 또는 프로젝트 환경에 맞는 `python manage.py check`로 Django 설정 오류를 확인한다.
2. 문제 생성 화면에서 `상세 설정`으로 문제지를 생성한다.
3. 풀이 중 선택지를 고르고 새로고침했을 때 동일 문제와 답안이 유지되는지 확인한다.
4. `문제 불러오기` 모달에서 저장 세션이 진단/문제풀이/학습계획 영역으로 분리되는지 확인한다.
5. 학습 플래너가 완성되면 `studyPlanQuestionCondition` 저장 후 `학습 계획 문제 풀기` 버튼으로 세션 생성이 되는지 확인한다.
