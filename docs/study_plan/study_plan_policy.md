# 학습계획 정책 정리

> **ℹ️ 부분 갱신 안내 (2026-07 기준)**
> 이 문서의 대부분(블록 유형·진행률·이월·삭제·과거날짜 차단)은 현행 정책이다.
> 단, 아래 "주간평가 판단 기준"은 최신 설계에서 `SolveSessions.review_type='weekly_review'`를 1차 식별 기준으로 바꿨다. 자세한 흐름은 `docs/study_plan/weekly_review_ai_report_plan.md`를 따른다.

## 목적

학습계획은 사용자의 취약점, 출제 예상, 남은 기간, 하루 학습 가능 시간을 기준으로 7일 단위 학습 블록을 만들고, 실제 풀이 기록과 블록 완료 상태를 기준으로 진행률을 보여주는 기능이다.

이 문서는 현재 코드에 반영된 정책과 앞으로 반영해야 할 정책을 분리해서 정리한다.

## 관련 위치

- `app/analytics/service/studyplan.py`
  - 학습계획 생성, 수정, 삭제, 이동, 완료 처리, 진행률 계산
- `app/analytics/service/display.py`
  - 마이페이지 학습 플래너 표시용 데이터 생성
- `app/templates/analytics/mypage.html`
  - 학습계획 버튼, 달력, 학습일 변경 UI
- `app/question/views.py`
  - 일반 학습계획 문제풀이 시작/제출 흐름
- `app/diagnosis/views.py`
  - 주간평가를 진단평가 흐름으로 연결해야 하는 위치

## 현재 데이터 구조

학습계획은 `StudyPlanMypage.study_plan_items` JSON 안에 날짜별 계획과 블록을 저장한다.

```json
[
  {
    "date": "2026-07-06",
    "blocks": [
      {
        "blockId": "uuid",
        "blockType": "weaknessFocus",
        "classification": "복합",
        "label": "조선 · 정치 · 자료 분석",
        "era": "조선",
        "topic": "정치",
        "qType": "자료 분석",
        "questionCount": 10,
        "estimatedMinutes": 30,
        "isCompleted": false,
        "completedAt": null
      }
    ]
  }
]
```

블록 기본 필드:

- `blockId`: 블록 단위 추적 ID
- `blockType`: 일반 학습 블록, 복습 블록, 주간평가 블록 구분
- `classification`, `label`: 표시와 기존 분류 매핑용 값
- `era`, `topic`, `qType`: 실제 문제 필터링에 우선 사용할 상세 조건
- `questionCount`: 목표 문항 수
- `estimatedMinutes`: 예상 소요 시간
- `isCompleted`: 블록 완료 여부
- `completedAt`: 완료 시각

## 학습계획 생성 정책

현재 구현 기준:

- 새 학습계획을 만들면 기존 active 학습계획은 archived 처리한다.
- 새 active 학습계획 row를 생성한다.
- 학습계획 생성 직후 `study_plan_base` 분석 snapshot을 저장한다.
- 기본 계획 기간은 7일이다.
- 앞의 6일은 일반 학습 블록을 배치한다.
- 마지막 1일은 주간평가 블록을 배치한다.
- 하루 학습 가능 시간에 따라 하루 블록 수를 조정한다.
- 각 블록의 문제 수는 예상 풀이 시간과 해설/복습 시간을 기준으로 계산한다.
- 일반 블록 문제 수는 설정된 최소/최대 범위 안으로 제한한다.

현재 주요 설정:

- `weekly_plan_days`: 7
- `weekly_learning_days`: 6
- `weekly_review_block_type`: `weekly_review`
- `weekly_review_question_count`: 50
- `weekly_review_minutes`: 80
- `daily_delete_limit`: 2
- `min_question_count`: 3
- `max_question_count`: 20

## 블록 유형 정책

### 일반 학습 블록

일반 학습 블록은 `/question/api/start/`의 `generation_mode="study_plan"` 흐름으로 시작한다.

필수 연결값:

- `studyplan_id`
- `study_plan_block_id`

문제풀이 세션 생성 시 `SolveRecords`에 두 값을 저장해야 학습계획 진행률 계산에 반영된다.

### 복습 확인 블록

`blockType = "review"` 블록은 실제 문제풀이 기록으로 진행률을 계산하지 않고, `isCompleted` 상태를 기준으로 완료 여부를 본다.

### 주간평가 블록

`blockType = "weekly_review"` 블록은 일반 문제풀이가 아니라 진단평가 흐름으로 진행한다.

정책:

- 시작 API: `/diagnosis/api/start/`
- 제출 API: `/diagnosis/api/submit/`
- 일반 문제풀이 API인 `/question/api/start/`에서 직접 생성하면 안 된다.
- 주간평가로 시작된 진단평가의 `SolveRecords`에는 `studyplan_id`, `study_plan_block_id`가 저장되어야 한다.
- 제출 완료 시 연결된 주간평가 블록의 `isCompleted`를 `true`로 변경해야 한다.

주의:

일반 진단평가도 `session_type = "diagnostic"`, `status = "completed"`가 되므로, 단순히 completed diagnostic만 보고 주간평가 완료로 처리하면 안 된다.

주간평가 판단 기준:

> 갱신: 최신 설계는 `SolveSessions.review_type = "weekly_review"`를 1차 식별 기준으로 쓴다. 아래 record 연결값(`studyplan_id`, `study_plan_block_id`)은 블록 연결·진행률 계산용 보조 기준으로 유지한다. (`weekly_review_ai_report_plan.md` 참조)

- `SolveSessions.session_type = "diagnostic"`
- `SolveSessions.review_type = "weekly_review"` (1차 기준)
- `SolveSessions.status = "completed"`
- `SolveRecords.studyplan_id` 존재 (보조)
- `SolveRecords.study_plan_block_id` 존재 (보조)
- 연결된 block의 `blockType = "weekly_review"`

## 진행률 계산 정책

학습계획 진행률은 단순 날짜/분류 추정으로 계산하지 않고, 학습계획 블록에서 시작된 기록만 반영한다.

반영 조건:

- 현재 사용자 기록
- `SolveSessions.status = "completed"`
- `SolveRecords.selected_no IS NOT NULL`
- `SolveRecords.studyplan_id = 현재 학습계획 ID`
- `SolveRecords.study_plan_block_id = 현재 blockId`

일반 학습 블록:

- 해당 blockId와 직접 연결된 풀이 기록 수를 센다.
- 목표 문항 수(`questionCount`)만큼 달성하면 블록을 달성한 것으로 본다.

복습 확인 블록:

- `isCompleted` 값을 기준으로 진행률을 계산한다.

주간평가 블록:

- 현재는 `isCompleted` 값을 기준으로 진행률을 계산한다.
- 주간평가 제출 흐름에서 `isCompleted`가 갱신되어야 진행률이 오른다.

## 문제풀이 시작 가능 정책

현재 표시 정책:

- 미래 날짜 블록은 시작할 수 없다.
- 오늘 또는 과거 날짜 블록은 시작할 수 있다.
- 완료된 주간평가 블록은 다시 시작할 수 없다.
- 주간평가 블록은 버튼 클릭 시 진단평가 API로 시작한다.
- 일반 학습 블록은 버튼 클릭 시 문제풀이 API로 시작한다.

변경 필요 정책:

- 과거 날짜의 일반 학습 블록에서는 문제풀이를 시작할 수 없어야 한다.
- 사용자가 해당 날짜에 문제를 풀지 않고 지나갔다면, 미완료 일반 학습 블록은 다음날로 이월되어야 한다.
- 과거 날짜 화면은 기록 확인용으로만 사용하고, 실제 문제풀기는 오늘 날짜 또는 오늘로 이월된 블록에서만 가능해야 한다.

이 정책이 필요한 이유:

- 과거 날짜에서 그대로 문제풀기를 허용하면 사용자가 여러 날짜의 계획을 뒤늦게 한 번에 처리할 수 있다.
- 그러면 "오늘 해야 할 학습량"과 "지나간 미완료 학습량"이 섞인다.
- 같은 미완료 문제가 과거 날짜에도 남고 오늘 날짜에도 보이면 중복 풀이와 중복 진행률 반영 위험이 생긴다.
- 따라서 미완료분은 하나의 blockId를 유지한 채 다음날로 이동시키고, 시작 가능 위치를 오늘 날짜로 단일화하는 편이 안전하다.

## 미완료 블록 이월 정책

신규로 반영해야 할 정책:

- 대상은 `weekly_review`가 아닌 일반 학습 블록이다.
- 복습 확인 블록은 별도 정책으로 둔다.
- 이미 완료된 블록은 이월하지 않는다.
- 진행 중 세션이 있는 블록은 자동 이월 전에 별도 처리 정책이 필요하다.
- 미완료 블록은 다음날 날짜의 blocks로 이동한다.
- blockId는 유지한다.

blockId를 유지해야 하는 이유:

- 기존에 생성된 `SolveRecords.study_plan_block_id`와 연결이 끊기지 않는다.
- 진행률 계산이 같은 블록을 계속 추적할 수 있다.
- 새 blockId로 복사하면 기존 미완료 기록과 새 계획이 분리되어 중복 집계나 누락이 생길 수 있다.

이월 시점 후보:

1. 마이페이지 조회 시점
   - 장점: 사용자가 들어오면 즉시 최신 상태로 정리된다.
   - 단점: GET 요청에서 DB 상태가 바뀌므로 주의가 필요하다.

2. 학습계획 생성/조회 전 별도 서비스 호출
   - 장점: view에서 호출 순서를 명확히 관리할 수 있다.
   - 단점: 호출 누락 시 정책이 적용되지 않을 수 있다.

3. 배치 작업
   - 장점: 날짜 변경 직후 자동 정리 가능
   - 단점: 현재 프로젝트에 배치 실행 환경이 없으면 도입 비용이 크다.

권장:

- 우선은 마이페이지 진입 시 active 학습계획을 정리하는 서비스 함수를 명시적으로 호출한다.
- 함수 역할은 "오늘 이전의 미완료 일반 학습 블록을 오늘 날짜로 이동"으로 제한한다.
- view는 서비스 호출 순서만 담당한다.

권장 함수 역할:

- active 학습계획 조회
- 오늘 이전 날짜의 블록 검사
- 완료/주간평가/복습 확인 블록 제외
- 미완료 일반 학습 블록을 오늘 날짜로 이동
- 빈 날짜 정리
- `update_study_plan`으로 저장

## 과거 날짜 문제풀이 차단 정책

신규로 반영해야 할 정책:

- `display.py`에서 `canStart` 계산 시 과거 날짜 일반 학습 블록은 `false`로 내려야 한다.
- 오늘 날짜 블록만 `canStart = true`가 될 수 있다.
- 미래 날짜 블록은 기존처럼 `canStart = false`다.
- 주간평가도 기본적으로 해당 날짜에만 시작 가능하게 맞추는 것이 자연스럽다.

권장 기준:

```text
canStart = plan_date == today and not done
```

다만 주간평가 완료 후 재시작은 계속 막아야 한다.

버튼 표시 정책:

- 오늘 시작 가능: `문제 풀기`
- 과거 미완료: `오늘로 이월됨` 또는 비활성화
- 과거 완료: `완료`
- 미래 예정: `예정`
- 완료된 주간평가: `주간 평가 완료`

## 학습일 변경 정책

현재 구현 기준:

- 학습일 변경 모달에서 선택한 블록을 target date로 이동한다.
- 이동 가능한 날짜는 해당 학습계획의 `start_date`와 `end_date` 범위 안이다.
- 이동 시 blockId는 유지된다.
- 대상 날짜가 없으면 새 day plan을 만든다.
- 이동 후 날짜순으로 정렬한다.
- 시작일/종료일 경계 날짜는 빈 날짜라도 보존한다.

정책 보강 필요:

- 완료된 블록 이동 허용 여부를 정해야 한다.
- 주간평가 블록 이동 허용 여부를 정해야 한다.
- 자동 이월과 수동 학습일 변경이 충돌하지 않도록 우선순위를 정해야 한다.

권장:

- 완료된 블록은 이동하지 않는다.
- 주간평가 블록은 자동/수동 이동 대상에서 제외한다.
- 자동 이월은 오늘 이전 미완료 일반 학습 블록에만 적용한다.

## 삭제 정책

현재 구현 기준:

- 일반 학습 블록은 삭제할 수 있다.
- 주간평가 블록은 삭제할 수 없다.
- 하루 삭제 가능 횟수는 2회다.
- 삭제 후 우선순위가 높은 미배치 학습 블록을 같은 위치에 보충한다.
- 삭제 횟수는 day plan의 `deletedBlockCount`, `deletedBlockCountDate`로 관리한다.

주의:

- 자동 이월은 사용자의 삭제 행동이 아니므로 삭제 횟수에 포함하지 않는 것이 맞다.
- 이월된 블록을 사용자가 삭제하면 삭제 횟수는 삭제한 당일 기준으로 증가해야 한다.

## 일반 학습계획 문제 생성 정책

`/question/api/start/`에서 `generation_mode = "study_plan"` 요청이 오면 `studyplan_id`, `study_plan_block_id`로 block을 찾는다.

문제 필터 우선순위:

1. `block.era`가 있으면 `Questions.era` 필터 적용
2. `block.topic`이 있으면 `Questions.topic` 필터 적용
3. `block.qType`, `block.q_type`, `block.questionType` 중 값이 있으면 `Questions.question_type` 필터 적용
4. `block.qSubtype`, `block.questionSubtype` 중 값이 있으면 `Questions.question_subtype` 필터 적용
5. 위 값이 없을 때만 기존 `classification/label` 매핑 사용

문제 수:

- `block.questionCount`를 우선 사용한다.
- 없으면 요청의 `count`를 사용한다.

난이도:

- `block.difficulty`가 있으면 우선 사용한다.
- 없으면 기존 기본 비율을 사용한다.

문항 수 부족:

- 현재 코드 흐름과 맞춰 에러 반환을 기본 정책으로 둔다.
- 단계적 조건 완화는 학습계획 의도와 다른 문제가 나올 수 있어 별도 정책으로 분리한다.

## 신규 구현 체크리스트

### 1. 과거 날짜 문제풀이 차단

- `app/analytics/service/display.py`
- `build_planner_summary`
- `canStart` 계산 변경
- 과거 날짜 일반 블록은 시작 불가
- 오늘 날짜 미완료 블록만 시작 가능

### 2. 미완료 블록 자동 이월

- `app/analytics/service/studyplan.py`
- active 학습계획의 과거 미완료 일반 블록을 오늘 날짜로 옮기는 서비스 함수 추가
- `weekly_review` 제외
- 완료 블록 제외
- blockId 유지
- 저장은 `update_study_plan` 사용

### 3. 마이페이지 진입 시 이월 적용

- `app/analytics/views.py`
- `mypage` 조회 전에 자동 이월 서비스 호출
- view는 호출 순서만 담당

### 4. 주간평가-진단평가 연결

- `app/diagnosis/serializers.py`
- `DiagnosisStartRequestSerializer`에 `studyplan_id`, `study_plan_block_id` 추가
- `app/diagnosis/views.py`
- 시작 시 weekly_review block 검증
- `SolveRecords`에 학습계획 참조 저장
- 제출 완료 시 해당 block 완료 처리

### 5. 일반 문제풀이 API 방어

- `app/question/views.py`
- `generation_mode = "study_plan"`인데 blockType이 `weekly_review`면 차단
- 일반 학습 블록만 question API에서 시작

## 검증 시나리오

### 과거 계획 차단

1. 어제 날짜의 미완료 일반 학습 블록을 만든다.
2. 마이페이지에 진입한다.
3. 어제 날짜에서 문제풀기 버튼이 비활성화되는지 확인한다.
4. 오늘 날짜에 같은 blockId의 블록이 표시되는지 확인한다.
5. 오늘 날짜에서만 문제풀이를 시작할 수 있는지 확인한다.

### 이월 후 진행률

1. 이월된 블록에서 문제풀이를 시작한다.
2. 생성된 `SolveRecords.study_plan_block_id`가 기존 blockId와 같은지 확인한다.
3. 문제를 제출한다.
4. 해당 블록 진행률이 정상 반영되는지 확인한다.
5. 과거 날짜와 오늘 날짜에 같은 블록이 중복 표시되지 않는지 확인한다.

### 주간평가

1. 주간평가 블록은 자동 이월 대상에서 제외되는지 확인한다.
2. 주간평가는 `/diagnosis/api/start/`로 시작되는지 확인한다.
3. 제출 완료 시 `weekly_review` 블록이 완료 처리되는지 확인한다.

### 삭제/이동

1. 자동 이월은 삭제 횟수를 증가시키지 않는지 확인한다.
2. 사용자가 직접 삭제할 때만 삭제 횟수가 증가하는지 확인한다.
3. 수동 학습일 변경 시 blockId가 유지되는지 확인한다.
