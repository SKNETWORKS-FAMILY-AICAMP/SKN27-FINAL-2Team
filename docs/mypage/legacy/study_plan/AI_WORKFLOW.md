# 주간 리포트 AI workflow v2

- 상태: PROPOSED CANONICAL
- 구현 상태: 미구현
- 최종 검토일: 2026-07-14

이 문서는 기존 weekly_review_ai_report_plan.md를 대체한다. v2는 다수의 자율 에이전트가 아니라 결정론 서비스와 제한된 LLM 노드를 조합한 단계형 AI workflow다.

## 1. 핵심 결정

1. PostgreSQL durable job queue와 Django management command worker를 사용한다.
2. LangGraph는 추천·리포트 작성과 검증 loop에만 사용한다.
3. DB claim, lease, heartbeat, 상태 전이, persist, Planner는 LangGraph 밖에서 처리한다.
4. Collector, Weakness Analyst, Renderer, Planner는 결정론 서비스다.
5. LLM이 필요한 역할은 Study Guide Recommender와 Report Writer다.
6. semantic critic은 feature flag가 켜진 경우에만 제한적으로 사용한다.
7. 다음 계획은 SPEC.md의 결정론 Planner가 생성한다.
8. 제출 transaction에서 report와 durable job을 함께 저장해 알림 유실을 제거한다.

## 2. 전체 구성

~~~mermaid
flowchart LR
    D["diagnosis submit"] --> TX["transaction<br/>session·block 완료<br/>report·job upsert"]
    TX --> Q[("ai_jobs")]
    Q --> W["run_ai_worker<br/>claim·heartbeat·retry"]
    W --> C["Collector<br/>deterministic"]
    C --> A["Weakness Analyst<br/>deterministic"]
    A --> G["LangGraph AI subgraph<br/>Recommender·Writer"]
    G --> R["Renderer<br/>deterministic"]
    R --> F["report finalize<br/>READY + NEXT_PLAN job"]
    F --> Q
    Q --> P["Deterministic Planner"]
    P --> V["PlanDraft validator"]
    V --> PF["plan finalize<br/>archive + active insert"]
~~~

## 3. 역할

### 3.1 결정론

- 제출 검증과 weekly block 완료
- Collector: 고정 session snapshot과 metric 생성
- Weakness Analyst: 공통 weakness 규칙 적용
- taxonomy 기반 retrieval query 생성
- metric·target reference guard
- deterministic renderer
- PlanDraft 생성과 domain validator
- DB repository, claim, finalize, repair

### 3.2 LLM

Study Guide Recommender:

- 검증된 취약 target을 한국사 콘텐츠 chunk와 연결
- 학습 포인트를 structured output으로 반환
- 허용된 chunk id만 citation으로 사용
- 점수·출제 확률을 생성하지 않음

Report Writer:

- source_metrics와 analysis_result를 참조한 학생용 문장 생성
- 숫자와 target 이름 대신 metric ref·target ref 사용
- structured section schema로 출력
- 추천 콘텐츠 사실은 직접 생성하지 않고 recommendation_result를 참조

Semantic critic:

- deterministic guard가 모두 통과한 경우에만 선택적으로 실행
- tone과 retrieved chunk faithfulness만 평가
- 정책·수치·분류를 새로 결정하지 않음

## 4. 최소 테이블

기존 모델이 managed=False이므로 init.sql, alter_apply_latest.sql, Django model을 함께 갱신한다.

### 4.1 weekly_ai_reports

도메인 결과만 저장하고 queue lease 필드는 저장하지 않는다.

필드:

- report_id BIGINT PK
- user_id BIGINT FK
- session_id BIGINT FK UNIQUE
- source_studyplan_id BIGINT nullable
- study_plan_block_id UUID nullable
- archive_target_studyplan_id BIGINT nullable
- status: PENDING, READY, FAILED
- source_metrics JSONB
- collected_facts JSONB
- analysis_result JSONB
- recommendation_result JSONB
- writer_draft JSONB
- eval_results JSONB
- rendered_report JSONB
- planner_input_snapshot JSONB nullable
- generated_studyplan_id BIGINT nullable
- schema_version
- prompt_version
- model_config_version
- taxonomy_version
- content_version
- created_at, modified_at, ready_at

불변조건:

- user_id와 session 소유자가 같음
- weekly session당 report 하나
- READY이면 rendered_report가 유효함
- generated_studyplan_id가 있으면 READY
- JSONB artifact는 schema_version validator 통과

### 4.2 ai_jobs

durable queue와 outbox 역할을 함께 한다.

필드:

- job_id UUID PK
- job_type: WEEKLY_REPORT, NEXT_PLAN, PLAN_ROLLOVER
- report_id nullable FK
- dedupe_key UNIQUE
- parent_job_id nullable
- status: QUEUED, RUNNING, RETRY, BLOCKED, SUCCEEDED, DEAD, CANCELLED
- priority
- payload JSONB
- available_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
- attempt_count, max_attempts
- lease_token UUID nullable
- locked_by nullable
- locked_until nullable
- heartbeat_at nullable
- last_error_code nullable
- last_error_summary nullable
- created_at, modified_at, finished_at

payload에는 ID와 version만 저장한다. 원문 답안, 개인정보, prompt, secret은 넣지 않는다.

필수 index:

- QUEUED·RETRY의 job_type, priority, available_at
- RUNNING의 locked_until
- report_id와 job_type

상태 CHECK:

- RUNNING이면 lease_token, locked_by, locked_until 필수
- SUCCEEDED·DEAD·CANCELLED이면 finished_at 필수
- QUEUED·RETRY이면 available_at 필수
- RUNNING이 아니면 lease_token·locked_by·locked_until은 null

### 4.3 ai_job_runs

시도별 감사·비용·관측 정보를 보존한다.

필드:

- run_id UUID PK
- job_id UUID FK
- attempt_no
- lease_token UUID UNIQUE
- worker_id
- status
- current_stage
- started_at, heartbeat_at, finished_at
- duration_ms
- error_code, error_summary
- model_usage JSONB
- node_metrics JSONB
- trace_id nullable

UNIQUE(job_id, attempt_no)를 적용한다. 수동 재시도는 attempt를 0으로 되돌리지 않고 허용 attempt를 늘려 이력을 보존한다.

## 5. 제출과 enqueue

diagnosis submit의 한 transaction에서 처리한다. 이미 구현된 block 검증·SolveRecords 연결·block 완료는 재사용하고 analytics enqueue만 결합한다.

1. user → source plan → weekly block → session 순서로 잠금
2. active·revision·소유권·review_type·SolveRecords 연결 재검증
3. 전체 답안 저장과 session completed 처리
4. weekly block completed와 plan revision 증가
5. weekly_ai_reports upsert
6. WEEKLY_REPORT ai_job을 available_at=DB NOW()로 upsert
7. commit

dedupe key는 weekly-report:session:{session_id}다.

job row가 durable outbox이므로 transaction.on_commit 알림은 필수가 아니다. PostgreSQL NOTIFY를 추가하더라도 wake-up 최적화일 뿐이며 worker polling을 계속 유지한다.

## 6. Worker

기본 command:

    python manage.py run_ai_worker --job-types weekly_report,next_plan,plan_rollover

로컬은 별도 terminal에서 실행한다. 운영은 별도 supervisor 또는 app과 분리된 worker container가 재시작을 책임져야 한다.

초기값은 concurrency 1이다. 여러 worker process를 실행하면 SKIP LOCKED로 확장한다.

### 6.1 Claim

짧은 transaction에서:

1. attempt_count < max_attempts이고 available_at <= DB NOW()인 QUEUED·RETRY 또는 lease가 만료된 RUNNING 후보 조회
2. SELECT FOR UPDATE SKIP LOCKED
3. 만료 RUNNING이면 이전 ai_job_runs를 EXPIRED로 종료
4. 새 lease_token과 worker_id 발급
5. attempt_count 증가
6. RUNNING, locked_until, heartbeat_at 저장
7. ai_job_runs row 생성
8. commit 후 lock 해제

실행 중에는 job row lock을 유지하지 않는다.

attempt 상한에 도달한 QUEUED·RETRY 또는 lease 만료 RUNNING은 일반 claim에서 제외한다. reaper가 후보 ID만 수집한 뒤 별도 finalize_exhausted_job을 호출한다. 이 함수는 report가 있으면 report → job → current run 순서로 다시 잠그고 상태·attempt 상한·lease 만료를 재검증한다. 만료 run은 EXPIRED, job은 DEAD로 닫고 WEEKLY_REPORT이면 report FAILED도 함께 저장한다. 새 run은 만들지 않으며 NEXT_PLAN의 READY report는 바꾸지 않는다.

### 6.2 Heartbeat와 fencing

- 별도 DB connection에서 lease interval의 1/3마다 heartbeat
- job_id, RUNNING, lease_token 조건이 일치할 때만 갱신
- 갱신 row가 0이면 소유권을 잃은 것으로 보고 결과 반영 금지
- finalize·retry·fail도 같은 lease_token fencing 조건을 사용
- 시간 비교는 DB NOW()만 사용

### 6.3 Timeout

- job 전체 timeout
- retrieval timeout
- LLM node별 timeout
- heartbeat grace
- graceful shutdown 시 신규 claim 중단

timeout 값은 versioned worker config에서 관리한다.

## 7. Retry

Transient:

- LLM 429·timeout·5xx
- RAG 연결 실패
- DB 일시 장애

Permanent:

- 입력 schema 오류
- 소유권 불일치
- metric·target 참조 위조
- assessment blueprint 불충족
- plan domain validator 실패

User action:

- 진행 중 session 때문에 plan 활성화 불가

Superseded:

- source plan_id가 사용자가 변경한 최신 active plan과 다름

처리:

- transient는 RETRY, last_error_code에는 원인 code, available_at에는 full-jitter exponential backoff 시각을 저장한다.
- permanent는 DEAD와 원인 code를 저장한다.
- user action은 BLOCKED와 IN_PROGRESS_SESSION 같은 원인 code를 저장한다.
- source plan_id가 바뀐 NEXT_PLAN은 CANCELLED와 SOURCE_PLAN_SUPERSEDED code를 저장한다.
- 상태는 QUEUED·RUNNING·RETRY·BLOCKED·SUCCEEDED·DEAD·CANCELLED 중 하나만 쓰고 세부 원인을 status 문자열에 합치지 않는다.
- RUNNING을 벗어날 때 current run을 종료하고 lease·worker lock을 지운다. terminal 상태는 finished_at을, RETRY·QUEUED는 available_at을 설정한다.
- BLOCKED 원인이 해소되면 session terminal event 또는 repair command가 원인을 다시 검증한 뒤 QUEUED·available_at=DB NOW()로 되돌린다.
- NEXT_PLAN이 BLOCKED에서 깨어날 때 이전 PlanDraft와 planner_input_snapshot을 폐기하고 같은 active plan의 최신 revision·입력으로 다시 계산한다.
- stale worker 결과는 저장하지 않는다.

마지막 WEEKLY_REPORT 시도가 DEAD가 되면 fenced transaction에서 job·run DEAD와 report FAILED를 함께 저장한다. NEXT_PLAN 실패는 이미 READY인 report 상태를 바꾸지 않는다.

수동 재시도는 인증·소유권·rate limit·cooldown을 통과해야 한다. 같은 transaction에서 report FAILED→PENDING, job DEAD→QUEUED, max_attempts 증가, available_at=DB NOW(), lease·오류 정리를 수행하며 attempt_count와 과거 run은 초기화하지 않는다.

graph 내부 rewrite 횟수와 job attempt_count는 분리한다. 이전 실행 피드백은 previous_run_feedback에 저장하고 현재 실행 generation_count와 섞지 않는다.

## 8. Report 입력 snapshot

첫 유효 claim에서 다음을 수집해 collected_facts에 고정한다.

- user·session·block 식별과 소유권
- session question·answer snapshot
- source plan id·revision
- 비교 baseline session 선택 근거
- era·topic·q_type별 raw metric
- weakness 계산 입력
- taxonomy·content version

최초 snapshot 저장은 report → job 순서로 잠그는 짧은 transaction에서 RUNNING·lease_token을 다시 확인한 뒤 collected_facts가 null일 때만 수행한다. 이미 값이 있으면 digest를 검증하고 재사용한다. 재시도는 같은 report snapshot을 사용하며 stale lease는 이를 덮어쓸 수 없다. 원천 data가 사후 변경되면 새 report version을 명시적으로 생성하지 않는 한 기존 결과를 조용히 바꾸지 않는다.

## 9. AI subgraph

LangGraph state는 typed schema를 사용한다.

Immutable 입력:

- source_metrics
- analysis_result
- retrieved_chunks
- config·schema·prompt version
- previous_run_feedback

출력:

- recommendation_result
- writer_draft
- eval_results
- failed_stage

그래프:

~~~mermaid
flowchart TD
    S([START]) --> REC["recommend"]
    REC --> RG["validate recommend<br/>deterministic"]
    RG -->|"재작성 1회 이내"| REC
    RG --> W["write"]
    W --> WG["validate write<br/>deterministic"]
    WG -->|"재작성 1회 이내"| W
    WG --> C{"semantic critic enabled?"}
    C -->|"예"| SC["batch semantic critic"]
    C -->|"아니오"| E([END])
    SC --> E
~~~

금지 node:

- DB claim·persist
- lease·attempt 변경
- Planner
- plan archive·insert
- 최종 metric renderer

Recommender는 soft-fail이다. 검색 품질 gate를 통과하지 못하면 recommendation을 비우고 안전한 챗봇 질문 링크 metadata만 남긴다. Writer와 renderer 실패는 report 실패다.

## 10. Guard와 Renderer

Recommend guard:

- citation chunk가 이번 retrieval allowlist에 존재
- 실제 weak target에만 매핑
- 중복 개념 금지
- 숫자·출제 예측 금지
- 길이 제한

Writer guard:

- 모든 metric ref가 source_metrics에 존재
- 모든 target ref가 analysis_result에 존재
- 허용되지 않은 숫자 literal 금지
- 필수 section 존재
- 데이터 부족 시 단정 금지
- 한국어·길이 schema

Renderer:

- metric ref를 검증 수치로 치환
- target ref를 taxonomy 표시명으로 치환
- recommendation_result를 별도 section으로 조립
- URL encoding과 HTML escaping
- rendered_report schema 검증

## 11. Report 성공 finalize

하나의 짧은 transaction에서:

1. report → job → current run 순서로 잠금
2. RUNNING·lease_token·lease 유효성 확인
3. artifact와 rendered_report 저장
4. report READY
5. current run과 job SUCCEEDED
6. NEXT_PLAN job upsert
7. commit

dedupe key는 next-plan:report:{report_id}다. 이 transaction 덕분에 READY report만 남고 Planner job이 유실되는 상태를 방지한다.

최종 실패도 같은 fencing·잠금 순서를 사용한다. attempt 상한 또는 permanent 오류이면 current run과 job을 DEAD로 닫고, WEEKLY_REPORT에 한해 report를 FAILED로 바꾸며 finished_at과 안전한 error code를 한 transaction으로 저장한다.

## 12. 다음 계획 job

NEXT_PLAN job은 LLM을 호출하지 않는다.

1. report와 source active plan snapshot 확인
2. analysis_result 재사용
3. prediction·carryover·deferred review·프로필을 수집해 시도별 Planner input 계산
4. SPEC.md의 deterministic Planner로 PlanDraft 계산
5. PlanDraft domain validator
6. finalize에서 source plan id·revision 재확인
7. 같은 source plan_id에서 revision·anchor·profile digest만 바뀌었으면 RETRY로 돌려 최신 입력을 다시 계산
8. 진행 중 session이 있으면 status BLOCKED, last_error_code IN_PROGRESS_SESSION
9. source plan_id가 바뀌었으면 최신 active를 archive하지 않고 status CANCELLED, last_error_code SOURCE_PLAN_SUPERSEDED
10. 기존 active archive와 같은 study_plan_mypage에 새 active insert
11. accepted planner_input_snapshot, report.generated_studyplan_id, job SUCCEEDED 저장

PlanDraft 계산 중에는 DB lock을 잡지 않는다. planner_input_snapshot은 시도 중 report에 덮어쓰지 않고, finalize의 fenced transaction에서 성공에 사용된 snapshot만 저장한다. archive와 insert도 이 transaction에서 수행한다.

## 13. Repair

repair_ai_jobs command는 다음을 idempotent하게 보정한다.

- 완료 weekly session인데 report 또는 WEEKLY_REPORT job 없음
- READY report인데 NEXT_PLAN job 없음
- lease가 만료된 RUNNING
- BLOCKED 원인이 해소됐지만 queue로 돌아오지 않은 job
- report.generated_studyplan_id와 plan source_report_id 불일치

repair는 정상 경로를 대신하지 않는 운영 복구 수단이다.

## 14. 비용

versioned config에서 관리:

- 모델
- 입력·출력 token 상한
- retrieval top-k와 chunk 길이
- report당 LLM call 상한
- report당 token·비용 상한
- graph rewrite 횟수
- job attempt 상한
- 수동 재시도 cooldown

원칙:

- deterministic guard를 먼저 실행
- critic은 feature flag
- 추천과 Writer를 target별 개별 call로 무제한 분리하지 않음
- budget 초과는 status DEAD, last_error_code BUDGET_EXCEEDED
- model usage는 ai_job_runs에 기록

## 15. 보안

- worker가 user·session·plan·block 소유권 재검증
- LLM 전달 필드 allowlist
- 개인정보, 정답 원문, secret 최소화
- retrieved chunk는 untrusted data로 표시
- RAG content가 tool 실행·URL 호출을 지시해도 무시
- structured output schema 강제
- citation allowlist 검증
- prompt와 답안을 last_error·일반 log에 저장하지 않음
- error summary 길이 제한과 민감정보 masking
- retry API 인증, 소유권, rate limit
- 링크 URL encoding과 XSS escaping

## 16. 관측성

구조화 log key:

- job_id, run_id, report_id
- job_type, stage, attempt
- worker_id, trace_id
- duration, result, error_code

user_id는 log에서 제거하거나 비가역 hash를 사용한다.

metric:

- queue depth와 oldest age
- running·retry·blocked·dead 수
- expired lease reclaim
- stale result discard
- stage별 성공률과 p95 latency
- validation fail·fallback 비율
- LLM call·token·cost
- report READY 후 plan 활성화까지 시간

## 17. 필수 acceptance

- 중복 submit에도 report·report job 각 1개
- commit 직후 worker 알림이 없어도 polling으로 실행
- worker crash 후 expired lease 회수
- 오래된 lease token 결과 폐기
- heartbeat 중복 실행 방지
- backoff available_at 준수
- 신규 QUEUED job은 available_at non-null이고 즉시 claim 가능
- expired RUNNING 회수 시 이전 run EXPIRED 종료
- report finalize와 NEXT_PLAN enqueue 원자성
- 마지막 WEEKLY_REPORT 실패의 job DEAD·report FAILED 원자성
- READY report에 plan job 누락 시 repair
- citation·metric·target 위조 차단
- Recommender soft-fail 후 report 생성
- Writer schema 실패 시 active plan 불변
- 같은 report의 plan 최대 1개
- source plan 변경 시 최신 plan 임의 archive 금지
- 진행 중 session이 있으면 plan 활성화 보류
- BLOCKED 해제 후 최신 source revision으로 Planner 재계산
- stale lease의 report·Planner snapshot 덮어쓰기 차단
- budget 초과와 수동 retry audit 보존

## 18. 배포 전제

- requirements에 worker가 필요한 직접 의존성을 명시한다.
- PostgreSQL compose만으로 worker가 자동 실행되지 않는다.
- 로컬 실행 명령과 운영 supervisor/container를 문서화한다.
- schema SQL과 managed=False Django model을 같은 변경에서 반영한다.
- worker가 없는 환경에서는 주간 리포트 기능 flag를 켜지 않는다.
