# 학습계획 v2 전환 계획

- 상태: PROPOSED CANONICAL
- 구현 상태: 미시작
- 최종 검토일: 2026-07-14

목표는 v1을 먼저 삭제하는 재작성 방식이 아니다. 계약을 고정하고 v2를 side-by-side로 검증한 뒤 traffic을 전환하고, 관찰 기간이 끝난 후 v1을 제거한다.

## 1. 절대 원칙

- app/analytics/service/studyplan.py는 전환이 끝날 때까지 공개 façade로 유지한다.
- 기존 endpoint, 응답 key, 예외 import를 먼저 깨지 않는다.
- 기존 studyplan_id와 blockId를 재발급하지 않는다.
- 기존 SolveRecords를 수정하거나 다른 plan·block에 다시 연결하지 않는다.
- canary 전 DB 변경은 additive만 허용한다.
- destructive migration, v1 table drop, legacy column drop은 최종 별도 승인 대상이다.
- 빈 v2 계획이나 validator 실패 때문에 기존 active를 archive하지 않는다.

## 2. 목표 모듈

권장 구조:

~~~text
app/analytics/service/
  studyplan.py                 # v1/v2 공개 façade
  study_plan_v2/
    contracts.py              # typed command·DTO
    config.py                 # versioned config loader
    planner.py                # 순수 PlanDraft compiler
    lifecycle.py              # rollover·replace·move·complete
    progress.py               # records 기반 projection
    repository.py             # ORM와 lock
    service.py                # transaction 경계
    validators.py             # domain invariant
  weekly_report/
    contracts.py
    collector.py
    analyzer.py
    ai_graph.py
    renderer.py
    jobs.py
    worker.py
    repair.py
~~~

파일은 역할 단위로 나누되 한두 줄 helper를 위해 파일이나 함수를 남발하지 않는다. 새 함수의 매개변수에는 타입을 명시한다.

## 3. Phase 0 — 계약과 현행 고정

작업:

- README, SPEC, CONTRACTS, AI_WORKFLOW 승인
- IMPLEMENTATION_STATUS의 현재 사실 재확인
- v1 공개 import·signature 목록 생성
- v1 endpoint와 serializer golden snapshot
- 현재 DB schema snapshot과 row count
- active 중복, invalid JSON, invalid blockId 사전 조사
- created_at·modified_at TIMESTAMP가 실제로 어떤 timezone 기준으로 저장됐는지 DB 설정·표본 조사
- question·diagnosis의 StudyPlanMypage·JSON 직접 조회 위치 목록
- 현재 query·latency·오류율 baseline
- 외부 협조 요청인 active unique index·review_type·diagnosis enqueue·고정 비율 출제의 실제 반영 여부 확인
- question·diagnosis의 plan 연결 in_progress session 자동 삭제 금지·이어풀기·명시적 취소 계약 협의

임시 안전 정책:

- 자동 NEXT_PLAN worker가 준비되기 전에는 weekly_review 완료 후 수동 재생성까지 막는 UI 정책을 배포하지 않는다.
- active partial unique index가 실제 DB에 적용되기 전 AI Planner를 활성화하지 않는다.

Gate:

- characterization test 통과
- 문서 미결정 항목 승인
- 사용자별 active 중복 정리 방안 승인
- 요청만 보낸 DB·diagnosis 항목의 적용 commit·test 확인

## 4. Phase 1 — DB 불변조건 보강

v1에도 필요한 선행 변경이며, 외부 요청으로 먼저 반영되면 이 Phase에서는 중복 실행하지 않고 검증만 한다.

- study_plan_mypage_user_active_uidx 추가
- 인덱스 전 중복 active 조회·수동 정리
- 가능하면 UNIQUE(user_id, plan_version)
- status CHECK
- SolveSessions.review_type 추가
- review_type CHECK와 weekly 조회 index
- SolveSessions cancelled 상태, cancelled_at, cancellation_reason과 보존형 취소 endpoint

v2 additive schema:

- study_plan_mypage v2 Plan 헤더 컬럼과 engine_version
- study_plan_days
- study_plan_blocks
- study_plan_command_receipts
- weekly_ai_reports
- ai_jobs
- ai_job_runs

모든 변경은 init.sql과 alter_apply_latest.sql에 같이 반영한다. Django model은 managed=False로 실제 SQL과 동일하게 선언한다.
기존 TIMESTAMP → TIMESTAMPTZ 변환은 Phase 0에서 승인한 legacy timezone을 USING 절에 명시하며, DB session timezone에 맡기지 않는다.

Gate:

- 신규 환경 init 성공
- 기존 환경 alter 재실행 가능
- constraint·index 존재 검증
- schema와 model field 비교 테스트

## 5. Phase 2 — Legacy backfill

절차:

1. study_plan_mypage를 read-only snapshot으로 읽음
2. study_plan_items JSON schema 분류
3. 기존 Plan 헤더에 timezone_name·revision·engine_version='v1' 등 v2 기본값 backfill
4. 기존 studyplan_id를 FK로 날짜를 study_plan_days에 삽입
5. blockId를 그대로 study_plan_blocks에 삽입
6. v1 status와 timestamp 의미 보존
7. 기존 plan BIGSERIAL sequence는 변경하거나 복제하지 않음
8. derived JSON과 정규화 row의 count·hash 검증

invalid data:

- JSON 파싱 실패, 중복 blockId, UUID가 아닌 blockId, 날짜 범위 오류를 자동 보정하지 않는다.
- quarantine report에 plan_id와 오류 code만 기록한다.
- 해당 사용자는 v1 read를 유지하고 수동 결정 전 v2 canary 대상에서 제외한다.

SolveRecords:

- 기존 값을 갱신하지 않는다.
- plan_id·blockId가 backfill 대상과 연결되는지만 검증한다.
- orphan은 원인을 보고하고 임의 연결하지 않는다.

Gate:

- backfill 대상 count 일치
- 기존 ID 변화 0건
- 새로운 orphan 0건
- active plan 상태 일치
- timezone_name은 업무 날짜 기준 Asia/Seoul, legacy timestamp 변환은 승인된 저장 기준과 일치

## 6. Phase 3 — v2 순수 로직

먼저 DB write 없는 구성요소를 구현한다.

- typed config와 schema
- target canonicalization
- deterministic priority
- PlanDraft compiler
- date·capacity scheduler
- review scheduler
- assessment blueprint validator
- PlanDraft domain validator
- DTO adapter

Characterization 입력으로 v1과 v2 결과를 비교한다. 의도된 차이는 승인 목록에만 기록한다.

Gate:

- SPEC acceptance 단위 테스트
- 같은 input/config/anchor deterministic
- random seed와 ID를 제외한 결과 안정
- daily capacity와 날짜 invariant 위반 0건

## 7. Phase 4 — Repository·transaction

구현:

- user·plan·block row lock
- 공통 user → plan → block → session → report → job → run 잠금 순서
- revision check
- study_plan_command_receipts idempotency repository
- active archive + insert finalize
- rollover
- review complete
- move·replace·extra
- progress projection
- v1 façade adapter
- 정규화 Plan·Block을 읽는 공통 plan resolver
- question·diagnosis의 JSON 직접 검증을 공통 resolver 호출로 전환
- v2 write와 derived study_plan_items JSON projection의 원자 저장
- v2 plan의 GET에서 기존 ensure_today_study_plan mutation 차단

v1 endpoint는 동일 응답 DTO를 유지한다. 신규 UI가 blockId·revision 계약으로 바뀌기 전 legacy dayIndex·blockIndex adapter를 유지한다.

Gate:

- 동시 생성 active 최대 1개
- stale mutation 409
- archived mutation 거부
- 중복 request effect 1회
- submit과 plan finalize 경합 안전
- plan 연결 in_progress session은 삭제되지 않고 이어풀기
- 명시적 취소는 records 보존·session/block cancelled·Planner wake-up을 한 번만 수행
- 외부 diagnosis enqueue 중복 제출 effect 1회

## 8. Phase 5 — AI job infrastructure

순서:

1. ai_jobs와 ai_job_runs repository
2. management command worker
3. claim·SKIP LOCKED
4. heartbeat·fencing
5. retry·backoff
6. report domain 저장
7. deterministic Collector·Analyst·Renderer
8. deterministic NEXT_PLAN job
9. repair command
10. LLM Recommender·Writer graph

LLM graph를 붙이기 전에 fake node로 enqueue부터 next plan finalize까지 end-to-end를 검증한다.

Gate:

- worker crash 회수
- stale token 폐기
- report READY + NEXT_PLAN enqueue 원자성
- worker 없는 환경 feature flag off
- active plan은 report 실패 시 불변

## 9. Phase 6 — Shadow

Shadow에서는 v1만 사용자 결과로 저장·노출한다.

- 동일 입력으로 v1과 v2 PlanDraft 생성
- UUID와 timestamp를 정규화한 뒤 diff
- 정책 invariant 비교
- v2 결과·diff는 제한된 개발 로그 또는 전용 검증 테이블에 저장
- 개인정보와 답안 원문 저장 금지

필수 metric:

- 생성 성공·실패율
- 대상·날짜·문항 수 차이
- capacity 위반
- empty plan
- pool shortage
- latency

Shadow 통과 조건:

- 불변조건 위반 0건
- ID churn 0건
- 승인되지 않은 구조 차이 0건
- 성능 기준 합의

## 10. Phase 7 — Canary

- 안정적인 사용자 allowlist 또는 deterministic percentage bucket
- feature flag는 read engine과 write engine을 분리
- 신규 v2 plan만 v2 writer 사용
- 같은 study_plan_mypage의 engine_version으로 v1 legacy plan과 v2 plan을 구분해 조회
- 사용자별 engine 선택을 request 중간에 바꾸지 않음

즉시 rollback 기준:

- 사용자당 active 0개 또는 2개
- blockId 변경
- SolveRecords 연결 누락·교차 사용자 연결
- finalize 전 기존 plan archive
- 공개 payload 파손
- stale AI 결과가 최신 plan을 archive

운영 threshold 기준:

- v1 baseline 대비 오류율
- p95 latency
- worker DEAD·BLOCKED 비율
- report ready 지연
- plan 활성화 지연

## 11. Phase 8 — 전체 전환

조건:

- canary 관찰 기간 통과
- open P0·P1 없음
- repair command 검증
- rollback rehearsal 완료
- 운영 worker supervisor 확인
- 사용자 지원 문구 준비

전체 traffic을 v2로 바꿔도 v1 façade와 legacy read adapter는 관찰 기간 동안 유지한다.

## 12. Rollback

Engine rollback:

- feature flag로 신규 명령을 v1 façade로 전환
- worker 신규 claim 중단
- RUNNING job은 heartbeat를 유지하며 종료하거나 안전하게 lease 만료
- v2 table과 기록은 삭제하지 않음

Data rollback:

- v2 plan에 SolveRecords가 없고 사용자 lock을 획득한 경우에만 v2 plan archive와 직전 plan 재활성화를 검토
- v2 plan에 기록이 있으면 과거 plan을 되살리지 않음
- 기록을 보존하고 v1 engine으로 교정 plan을 새로 생성

DB rollback:

- additive table과 nullable column은 즉시 drop하지 않음
- destructive rollback은 별도 migration과 승인 필요

## 13. v1 제거 조건

다음을 모두 만족한 뒤 별도 작업으로 수행한다.

- 전체 전환 후 관찰 기간 통과
- legacy plan 조회 비율 0 또는 승인된 임계 이하
- v1 façade 호출 metric 0
- v1 전용 test를 v2 contract test로 대체
- rollback에 v1 engine이 더 이상 필요하지 않음
- 데이터 보존·감사 승인

제거 순서:

1. v1 write 차단
2. v1 read adapter 제거
3. façade 내부 v1 branch 제거
4. legacy docs archive
5. legacy table·column drop proposal
6. 사용자 승인 후 destructive migration

## 14. 필수 테스트 묶음

Characterization:

- public import·signature·exception
- endpoint status·payload
- legacy JSON readback

Domain:

- 날짜·시간대·exam 경계
- priority·review·capacity
- progress positive·negative

Concurrency:

- double create
- double start·submit
- move·replace·complete 경합
- submit·replan 경합

AI jobs:

- duplicate enqueue
- lease reclaim
- heartbeat
- stale fencing
- backoff
- report·plan atomic finalize

Security:

- 타 사용자 ID
- 조작 filter·count
- prompt injection chunk
- retry rate limit
- error masking

Migration:

- count·hash
- blockId 보존
- orphan
- init·alter 재실행

## 15. 구현 보고 형식

각 phase마다 다음을 보고한 뒤 다음 단계 허락을 받는다.

1. 현재 문제
2. 목표 계약
3. 수정 파일
4. 실제 변경
5. test 결과
6. DB 영향
7. 남은 위험
8. rollback 방법
