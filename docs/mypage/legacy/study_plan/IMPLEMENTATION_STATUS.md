# 학습계획 구현 상태

- 상태: CURRENT FACTS ONLY
- 확인일: 2026-07-14
- 확인 브랜치: feature-mypage
- 코드·DB 확인 커밋: cd627d5

이 문서는 현재 저장소에 실제 존재하는 상태만 기록한다. 목표 설계는 SPEC.md와 AI_WORKFLOW.md를 따른다.

## 구현됨

- StudyPlanMypage와 study_plan_mypage 테이블
- study_plan_items TEXT 안의 날짜·블록 JSON 저장
- active 계획 조회·생성·archive·soft delete
- 기간 내 과거 미완료 일반 블록 이월
- blockId를 이용한 SolveRecords 직접 연결과 진행률 계산
- 일반 학습 블록의 question API 연결
- 주간평가 블록의 diagnosis API 연결
- diagnosis 시작 시 records에 studyplan_id와 study_plan_block_id 저장
- diagnosis 제출 시 연결된 주간평가 블록 완료 처리
- 삭제·완료 시 active 계획 행 잠금

## 부분 구현

- 사용자별 active 계획 하나를 전제로 코드가 작성됐지만, 문서에서 요구한 partial unique index가 init.sql과 alter_apply_latest.sql에 없다.
- 주간평가는 SolveRecords의 계획 연결값으로 간접 식별한다.
- review 블록 계산 함수는 있으나 생성·표시·완료 UX가 하나의 계약으로 완성되지 않았다.
- 주간평가 이후 다음 계획 자동 생성 흐름이 화면 문서에 반영됐지만 실제 worker는 없다.

## 미구현

- SolveSessions.review_type
- SolveSessions cancelled 상태·감사 필드와 plan 연결 session 취소 API
- 고정 비율 종합 주간평가와 문항 부족 fallback
- weekly_ai_reports
- PostgreSQL durable AI job queue
- AI worker management command
- ai_job_runs 실행 이력
- 주간 리포트 LangGraph
- 자동 다음 계획 job과 안전한 finalize
- heartbeat, backoff, stale fencing, repair command
- study_plan_mypage v2 Plan 헤더 컬럼과 normalized Day·Block 테이블
- v2 feature flag, shadow, canary, rollback

## 외부 협조 요청됨 — 적용 확인 전까지 미구현

- study_plan_mypage_user_active_uidx와 중복 active 사전 점검
- SolveSessions.review_type 컬럼·CHECK·조회 index
- diagnosis_start의 review_type 저장과 삭제 범위 분리
- diagnosis_submit의 analytics enqueue 호출
- versioned config 기반 고정 비율 주간평가

## 현재 알려진 위험

- 주간평가 완료 후 화면은 수동 재생성을 제한하지만 자동 Planner가 없어 사용자가 다음 계획으로 넘어가지 못할 수 있다.
- create_study_plan은 active unique index가 있다고 가정해 특정 constraint 이름을 검사하지만 실제 SQL에는 그 인덱스가 없다.
- app/analytics/views.py, app/diagnosis/views.py, app/question/views.py, display.py, mypage.py가 analytics.service.studyplan을 직접 import한다. 파일을 먼저 삭제하면 Django 앱 로딩이 깨진다.
- question·diagnosis 시작 로직이 StudyPlanMypage JSON을 직접 읽으므로 정규화 Block 전환 전 공통 resolver가 필요하다.
- question과 diagnosis 모두 새 세션 시작 시 기존 in_progress session을 DELETE한다. plan 연결 세션까지 삭제되면 이어풀기·진행률·BLOCKED Planner 해제가 깨질 수 있다.
- Django 설정의 TIME_ZONE은 UTC이고 계획 날짜는 timezone.localdate를 사용한다. 한국 서비스 날짜 기준을 별도로 확정하지 않으면 자정 경계가 9시간 어긋날 수 있다.

## 구현 상태 갱신 규칙

- 파일·테스트·SQL 근거가 확인된 항목만 구현됨으로 이동한다.
- 설계 문서에 있다는 이유만으로 구현됨으로 표시하지 않는다.
- 각 갱신에 확인일과 커밋을 기록한다.
- 부분 구현은 남은 조건을 함께 적는다.
