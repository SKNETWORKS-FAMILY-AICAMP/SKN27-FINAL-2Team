# 학습계획 v2 정책·도메인 명세

- 상태: PROPOSED CANONICAL
- 구현 상태: 미구현
- 최종 검토일: 2026-07-14

이 문서는 학습계획 v2의 단일 정책 기준이다. 구 문서 study_plan_policy.md와 학습계획_설계.md는 유니크 정책(기간별 가중치 프리셋, 표시 계약 등)을 이 문서와 CONTRACTS.md로 이관한 뒤 삭제했다(2026-07-16, docs/drafts/학습계획_AI리포트_정의_재검토.md 참조).

## 1. 목표

- 사용자의 취약도, 출제 예상, 시험일까지 남은 기간, 하루 학습 시간을 근거로 재현 가능한 계획을 만든다.
- 날짜·블록·완료 상태를 DB 제약과 명시적 상태 전이로 보호한다.
- 일반 학습, 복습, 주간평가를 서로 다른 라우팅 규칙으로 처리한다.
- 주간 리포트와 수동 재생성이 같은 계획 생성·활성화 서비스를 사용한다.
- 기존 blockId와 SolveRecords 연결을 보존하면서 v1에서 단계적으로 전환한다.

## 2. 핵심 결정

1. Plan 헤더는 기존 study_plan_mypage를 확장해 단일 ID·active 권위로 사용하고, Day·Block은 정규화 테이블을 source of truth로 둔다.
2. 기존 study_plan_items TEXT JSON은 전환 기간에 정규화 데이터를 투영한 호환 출력으로만 유지한다.
3. Planner는 결정론적 정책 엔진이다. LLM은 날짜, 문항 수, 필터, 우선순위를 결정하지 않는다.
4. DB timestamp는 UTC TIMESTAMPTZ로 저장하고, 계획 날짜는 IANA 시간대 기준으로 계산한다.
5. 초기 업무 시간대는 Asia/Seoul이며 계획 생성 당시 값을 plan에 snapshot으로 남긴다.
6. GET 요청은 계획을 변경하지 않는다. 일자 rollover는 durable worker 또는 명시적 sync command가 수행한다.
7. 모든 mutation은 blockId와 expected revision을 사용한다. dayIndex·blockIndex는 v1 호환 adapter에서만 허용한다.
8. active 계획은 사용자당 최대 하나다. 생성 불가 상태에서는 0개일 수 있다.
9. 진행 중 세션이 연결된 active 계획은 자동으로 교체하지 않는다. 완료 또는 명시적 취소 후 재시도한다.
10. 생성 결과가 유효하지 않으면 기존 active 계획을 유지한다.

## 3. 용어

| 용어 | 의미 |
|---|---|
| Plan | 일정 기간의 학습계획 |
| Day | Plan 안의 특정 현지 날짜와 학습 가능 용량 |
| Block | 시작·완료·이동의 최소 단위 |
| Practice | question API에서 문제를 푸는 일반 블록 |
| Review | 학습 내용을 복습하고 명시적으로 완료하는 블록 |
| Weekly review | diagnosis API에서 수행하는 종합평가 블록 |
| Focus kind | weakness, prediction, mixed, carryover, extra 등 배치 이유 |
| Anchor date | 새 계획의 첫 현지 날짜 |
| Revision | 오래된 화면과 동시 요청을 막는 낙관적 잠금 값 |

## 4. 데이터 모델

### 4.1 study_plan_mypage (Plan 헤더)

별도 study_plans 테이블을 만들지 않는다. 기존 테이블과 BIGSERIAL을 재사용해야 v1/v2 병행 기간에도 plan ID와 active 상태의 권위가 하나로 유지된다.

주요 필드:

- studyplan_id BIGINT PK: 도메인 이름은 plan_id
- user_id BIGINT FK
- plan_version INTEGER
- engine_version: v1, v2
- revision INTEGER
- status: active, archived, deleted
- generation_reason: initial, manual, weekly_report, recovery
- source_plan_id nullable
- source_report_id nullable
- idempotency_key
- anchor_date, start_date, end_date
- timezone_name
- exam_date_snapshot
- daily_available_minutes_snapshot
- policy_version
- config_snapshot JSONB
- input_digest
- summary
- legacy study_plans TEXT, study_plan_items TEXT, completion_rate: 전환 기간 derived projection
- created_at, activated_at, modified_at, archived_at, deleted_at

필수 제약:

- 사용자별 active partial unique index
- UNIQUE(user_id, plan_version)
- UNIQUE(source_report_id) WHERE source_report_id IS NOT NULL
- UNIQUE(user_id, idempotency_key)
- start_date <= end_date
- revision >= 1

expired와 awaiting_report는 저장 상태가 아니라 날짜·리포트 상태로 계산하는 표시 상태다.

### 4.2 study_plan_days

주요 필드:

- day_id BIGINT PK
- plan_id BIGINT FK → study_plan_mypage.studyplan_id
- plan_date DATE
- day_ordinal INTEGER
- learning_capacity_minutes INTEGER
- created_at, modified_at

필수 제약:

- UNIQUE(plan_id, plan_date)
- UNIQUE(plan_id, day_ordinal)
- UNIQUE(day_id, plan_id): block의 복합 FK 대상
- day_ordinal >= 1
- learning_capacity_minutes > 0

### 4.3 study_plan_blocks

주요 필드:

- block_id UUID PK
- plan_id BIGINT FK
- day_id BIGINT FK
- block_ordinal INTEGER
- revision INTEGER
- block_type: practice, review, weekly_review
- focus_kind: weakness, prediction, mixed, carryover, extra
- status: scheduled, in_progress, completed, missed, cancelled
- group_key_id nullable
- era, topic, q_type nullable
- immutable_selection_spec JSONB
- target_question_count nullable
- estimated_minutes
- priority_score
- reason
- source_block_id nullable
- review_stage nullable
- initial_plan_date
- cancellation_reason nullable
- started_at, completed_at, missed_at, cancelled_at
- created_at, modified_at

필수 제약:

- 한 day 안의 non-cancelled block_ordinal unique
- UNIQUE(plan_id, block_id): SolveRecords 연결 pair 검증 대상
- FOREIGN KEY(day_id, plan_id) REFERENCES study_plan_days(day_id, plan_id)
- practice·weekly_review의 target_question_count > 0
- review는 question API로 시작하지 않음
- plan당 non-cancelled weekly_review 최대 1개
- weekly_review는 7일 기본 계획의 마지막 day에만 존재
- review에는 UNIQUE(source_block_id, review_stage) WHERE status <> 'cancelled'
- source_block_id는 자기 자신을 가리킬 수 없으며 이전 plan의 block을 가리킬 수 있음

immutable_selection_spec에는 문제 선택에 필요한 filter와 blueprint version을 저장한다. 클라이언트가 보낸 filter·문항 수로 덮어쓰지 않는다.

### 4.4 기존 ID 호환

- 기존 study_plan_mypage.studyplan_id가 그대로 v2 plan_id다.
- 기존 JSON의 blockId는 v2 block_id로 그대로 backfill한다.
- 기존 SolveRecords의 studyplan_id와 study_plan_block_id는 수정하지 않는다.
- 새 plan ID는 기존 study_plan_mypage의 단일 sequence로, block ID는 UUID로 발급한다.
- v2 finalize는 같은 Plan 헤더에 정규화 Day·Block과 derived study_plan_items JSON을 한 트랜잭션으로 기록한다.
- v1 row를 읽을 때 선택 필드가 없더라도 adapter가 기본값을 보완한다.

## 5. 상태 전이

### 5.1 Plan

| 현재 | 명령 | 다음 | 조건 |
|---|---|---|---|
| 없음 | 최초 생성 finalize | active | 유효한 draft, active 없음 |
| active | 새 계획 finalize | archived | 같은 트랜잭션에서 새 active 생성 |
| archived | 사용자 삭제 | deleted | 기록 보존 정책 통과 |
| active | 직접 삭제 | 금지 | 먼저 대체 계획 또는 명시적 서비스 정책 필요 |

Plan 생성 계산 결과를 장기 draft row로 저장하지 않는다. AI job의 planner output으로 보관하고 finalize에서 archive와 새 active insert를 원자적으로 처리한다.

### 5.2 Block

| 현재 | 명령 | 다음 |
|---|---|---|
| scheduled | practice·weekly 시작 | in_progress |
| scheduled | review 완료 | completed |
| in_progress | 정상 제출 | completed |
| scheduled | 지난 weekly_review 확정 | missed |
| scheduled | 사용자 교체·재계획 | cancelled |
| in_progress | 명시적 세션 취소 후 재계획 | cancelled |

completed, missed, cancelled는 terminal 상태이며 일반 API로 되돌리지 않는다. 운영 복구는 감사 로그가 남는 별도 관리자 command로만 수행한다.

## 6. 날짜와 시간대

- DB의 현재 시각 비교는 DB NOW()와 UTC TIMESTAMPTZ를 사용한다.
- 기존 created_at·modified_at TIMESTAMP의 저장 기준 시간대는 배포 DB 조사 후 명시적으로 변환한다. 확인 없이 session timezone을 가정해 cast하지 않는다.
- local date는 plan.timezone_name의 ZoneInfo로 계산한다.
- 클라이언트가 보낸 today는 신뢰하지 않는다. 테스트와 관리자 도구만 기준 날짜를 주입할 수 있다.
- 사용자별 시간대가 도입되기 전 기본값은 Asia/Seoul이다.

Anchor date:

- 최초·수동·복구 생성: 명령 시작 시 서버 local date
- 주간 리포트 생성: max(주간평가 완료 local date + 1일, 실행 시 local date)
- 실행이 자정을 넘겨 anchor 또는 프로필 digest가 바뀌면 stale 결과로 버리고 다시 계산

시험일 규칙:

- exam_date > anchor: plan_days = min(7, exam_date - anchor의 날짜 수), 시험일은 제외
- exam_date == anchor: 당일 압축 계획 1일
- exam_date < anchor: 생성 불가
- exam_date 없음: 7일
- plan_days == 7: 6일 학습 + 7일차 weekly_review
- plan_days 1~6: 전일 압축 학습, weekly_review 없음

## 7. 계획 생성 입력

PlannerInput은 다음 snapshot을 포함한다.

- user_id, anchor_date, timezone_name
- exam_date, daily_available_minutes
- weak targets: groupKeyId, weaknessScore, status, trend, 근거 count
- prediction targets: groupKeyId, predictionScore, reason
- source active plan id와 revision
- 완료되지 않았지만 세션이 시작되지 않은 carryover targets
- 완료된 practice와 이미 수행한 review stage에서 다시 계산한 deferred review candidates
- policy_version과 config_snapshot
- source_report_id 또는 idempotency_key

입력 snapshot은 digest를 계산해 plan에 저장한다. 같은 input, config, anchor는 ID와 timestamp를 제외하면 같은 draft를 만들어야 한다.

## 8. 대상 선택과 점수

1. groupKeyId를 안정 식별자로 사용해 weakness와 prediction을 병합한다.
2. 이름이나 표시 label로 병합하지 않는다.
3. 기본 weakness 후보는 status가 INSUFFICIENT가 아니고 weaknessScore가 stable threshold보다 큰 항목이다.
4. 기본 후보가 없으면 오답이 존재하는 관찰 부족 항목을 낮은 확신도로 사용한다.
5. 그래도 없으면 prediction-only 대상을 사용한다.
6. 후보가 하나도 없거나 문제 pool이 부족하면 생성 실패로 처리하고 기존 active를 유지한다.

점수:

    priority = clamp(
        weaknessScore * Wweak
        + predictionScore * Wprediction
        + timeBurden * Wtime
        + trendBonus,
        0,
        1
    )

- 가중치와 threshold는 versioned config에서 읽는다.
- 가중치 세트는 남은 기간 프리셋으로 선택한다. 생성 시점의 `exam_date - anchor`
  구간(예: 7일 이하 / 8~21일 / 22일 이상 / exam_date 없음)에 따라 config의
  구간→가중치 매핑에서 하나를 고르고, 선택된 프리셋은 config_snapshot에 남긴다.
  짧은 구간일수록 Wprediction 비중을, 긴 구간일수록 Wweak 비중을 높이는 것이
  기본 방향이다. 계획 기간이 최대 7일인 것과 무관하게 남은 기간 전체로 판단한다.
- timeBurden는 평균 풀이 시간을 기본 시간으로 나눈 뒤 1로 제한한다.
- 정렬은 priority 내림차순, weakness 내림차순, prediction 내림차순, groupKeyId 오름차순이다.
- focus_kind는 점수 구성과 출처를 나타내며 API 라우팅에 사용하지 않는다.

## 9. 날짜·용량 배치

- 하루 learning capacity는 사용자의 daily_available_minutes snapshot이다.
- 설정이 없으면 versioned config의 fallback을 사용하고 화면에 fallback 사용 사실을 표시한다.
- 같은 target은 같은 날 한 번만 배치한다.
- 후보는 결정론적 정렬 후 round-robin으로 배치한다.
- 하루 block 수, 최소 block 시간, 문제 수 min/max는 config 한 곳에서 관리한다.
- 분 단위 나눗셈의 나머지는 앞 block부터 1분씩 배정한다.
- Planner가 배치한 필수 practice·review의 합계 estimated_minutes는 day learning capacity를 넘을 수 없다.
- weekly_review는 별도 assessment capacity이고, 완료 후 사용자가 선택하는 extra block은 필수 learning capacity 계산에서 제외한다.

Practice 문항 수:

    unit_seconds = clamped_average_solve_seconds + review_seconds
    question_count = clamp(
        floor(estimated_minutes * 60 / unit_seconds),
        min_questions,
        max_questions
    )

Question pool 검증이 실패하면 filter를 조용히 완화하거나 임의 문제를 섞지 않는다.

## 10. 복습 배치

- review는 실제 화면에 표시되는 독립 block이다.
- practice의 source_block_id와 review_stage를 사용해 대상을 추적한다.
- 기본 review offset은 config에 version으로 저장한다.
- 7일 계획의 기본 제안은 +1일, +3일이며 +7일 후보는 다음 계획 입력에 넘긴다.
- 압축 계획에서는 기간 안에 들어오는 offset만 사용한다.
- offset이 평가 전용 day 또는 plan 밖에 도달하면 그 plan에는 배치하지 않고 다음 계획의 deferred review candidate로 다시 계산한다.
- review는 사용자가 복습을 수행한 뒤 명시적 완료 command로 완료한다.
- review 완료 버튼을 제공하지 않는 UI에서는 review block을 생성하면 안 된다.
- rollover 시 더 최신 review stage가 이미 도래했다면 오래된 미완료 review를 cancelled 처리하고 중복 이월하지 않는다.

## 11. 주간평가

- 7일 계획의 마지막 날에 정확히 1개 배치한다.
- 마지막 날은 평가 전용이며 practice·review를 함께 배치하지 않는다.
- 규격은 50문항·100점·80분, 배점은 1점×10 + 2점×30 + 3점×10이다
  (기출 75·76·77회와 동일 규격, `docs/drafts/기출_분포_조사_75_76_77.md` 3장).
- 하루 일반 학습 시간과 별개인 평가 capacity로 취급한다.
- 일반 diagnosis와 weekly_review는 같은 blueprint version을 사용해야 비교가 가능하다.
- blueprint는 상호배타적인 stratum별 filter, 문항 수, 배점 quota를 정의한다.
- 각 축의 독립 비율만 맞추는 방식은 교차 분포 충돌을 만들 수 있으므로 사용하지 않는다.
- stratum은 서비스 era 10개 단일 축이다. quota 초안은 기출 3회 분포 근거로
  선사 1 / 고조선 1 / 초기국가 1 / 삼국 4 / 남북국 3 / 고려 9 / 조선 9 /
  개항기 7 / 일제강점기 10 / 현대 5 = 50
  (근거와 재배분 규칙은 `docs/drafts/기출_분포_조사_75_76_77.md` 7장).
- topic·난이도는 stratum 내 2차 soft 조건으로만 사용하고 quota로 강제하지 않는다.
- question pool이 quota를 충족하지 못하면 명시적으로 실패한다.
- 적용 선행조건: Questions 적재 후 era별 pool 조사에서 각 stratum이
  quota × 여유배수(versioned config)를 충족해야 한다.
- 저장된 seed와 question_id hash를 사용해 동일 blueprint 실행을 재현할 수 있어야 한다.

## 12. 진행률

Block status가 완료 여부의 source of truth다. JSON isCompleted와 plan.completion_rate를 별도 진실 원천으로 저장하지 않는다.

부분 진행:

- practice: 해당 사용자·plan·block에 직접 연결되고 completed session에 속하며 답변이 있는 distinct record 수
- review: scheduled면 0, completed면 1
- weekly_review: completed 전 0, completed 후 target_question_count
- cancelled: 계획 진행률 분모에서 제외
- missed: 분모에는 포함하고 달성값은 0

전체 진행률:

    sum(min(achieved_units, target_units))
    / sum(target_units)

session 제출 트랜잭션에서 session completed와 block completed를 함께 반영한다. 중복 제출은 같은 결과를 반환하며 진행률을 두 번 증가시키지 않는다. reconciliation command는 records와 block 상태 불일치를 탐지하고 자동 수정 여부를 별도 정책으로 결정한다.

## 13. Rollover

- rollover는 GET에서 수행하지 않는다.
- durable worker 또는 POST active/sync command가 plan_id + local_date dedupe key로 실행한다.
- 같은 날짜에 여러 번 실행해도 결과가 같아야 한다.
- superseded review를 먼저 cancelled 처리한 뒤, 남은 과거 scheduled practice·review와 in_progress practice를 block_id 그대로 오늘 day로 이동한다.
- in_progress practice는 session 연결과 답안을 유지하며 오늘 화면에서 이어 푼다.
- completed, cancelled, missed block은 이동하지 않는다.
- scheduled weekly_review는 이동하지 않고 날짜가 지나면 missed 처리한다. 이미 in_progress인 weekly_review는 원래 날짜에 두고 완료 또는 명시적 취소를 기다린다.
- end_date 이후에는 이동하지 않는다.
- 이동 전후에는 plan과 block revision을 증가시킨다.
- rollover는 필수 learning capacity를 넘길 수 있는 유일한 자동 이동 예외다.
- 이동 후 오늘 예상 시간이 learning capacity × overload multiplier를 넘으면 overload 표시와 재생성을 안내한다.

## 14. 블록 조작

### 완료

- practice·weekly_review는 연결 session 제출로만 완료한다.
- review는 review 완료 API로만 완료한다.
- 이미 terminal이면 동일 결과를 반환한다.

### 이동

- scheduled practice·review만 이동 가능하다.
- completed, weekly_review, in_progress는 이동 불가다.
- plan 기간과 대상 day learning capacity를 서버에서 검증한다.
- block_id는 유지한다.

### 교체

v1의 삭제는 실제로 workload를 줄이지 않고 대체 block을 넣으므로 v2에서는 replace로 명명한다.

- scheduled practice만 교체 가능하다.
- 기존 block은 cancelled, replacement는 새 UUID다.
- 사용자별 local date 교체 횟수는 config 제한을 적용한다.
- replacement는 아직 배치되지 않은 다음 우선 대상에서 선택한다.
- daily capacity를 초과하면 실패한다.

### 추가 학습

- 오늘 예정된 필수 practice가 완료된 경우에만 허용한다.
- extra focus_kind의 practice block을 만든다.
- weekly_review 당일과 기간 밖에서는 허용하지 않는다.

## 15. 계획 교체와 동시성

1. Planner는 DB lock 없이 PlanDraft를 계산하고 검증한다.
2. finalize에서 사용자 row와 현재 active plan을 잠근다.
3. source plan id·revision·프로필 digest·anchor를 다시 확인한다.
4. active plan에 in_progress session이 있으면 교체하지 않고 BLOCKED 상태·IN_PROGRESS_SESSION code로 보류한다.
5. source가 바뀌었으면 최신 active를 임의 archive하지 않고 stale 결과를 버린다.
6. 기존 active archive와 새 active insert를 한 트랜잭션에서 수행한다.
7. partial unique index가 최종적으로 active 중복을 차단한다.

수동 생성과 주간 리포트 Planner도 같은 finalize 서비스를 사용한다. 진행 중 session의 block 또는 SolveRecords를 새 plan으로 다시 연결하지 않는다. 사용자는 이어서 완료하거나, 답안·record를 보존하는 명시적 session 취소로 종료할 수 있다. terminal 전이는 보류된 Planner job을 재검증 후 깨운다.

## 16. API 방어 불변조건

- 사용자 소유 plan·block인지 확인
- active plan인지 확인
- expected plan revision과 block revision 확인
- practice는 question API, weekly_review는 diagnosis API로만 시작
- 새 session은 local date가 scheduled date와 같은지 확인
- 기존 in_progress session resume은 날짜 검사를 반복하지 않고 연결·소유권·route를 다시 검증
- terminal block 재시작 금지
- 동일 block의 in-progress session 중복 생성 금지
- 클라이언트의 filter, question count, difficulty, block type, completion 값 무시
- archived 화면의 mutation 거부
- mutation마다 idempotency key 적용
- stale revision은 409

## 17. 설정

다음 값은 코드에 분산 하드코딩하지 않고 versioned config 한 곳에서 관리한다.

- 업무 시간대
- 계획 기간과 learning day 수
- daily fallback minutes
- block 수·시간·문항 min/max
- priority 가중치와 threshold (남은 기간 구간별 프리셋 매핑 포함)
- review offset
- rollover overload multiplier
- replace 제한
- weekly assessment blueprint
- policy/schema version

Plan에는 사용한 config version과 snapshot을 저장해 재현성을 보장한다.

## 18. 필수 acceptance

- KST 자정 경계, exam 내일·오늘·과거·없음
- 동시 생성 2건에도 active 최대 1개
- 같은 idempotency key와 source_report는 같은 plan
- pool 부족 시 기존 active 유지
- block start·submit 중복 요청의 effect 1회
- 다른 사용자·과거·미래·route mismatch·조작된 filter 차단
- rollover 재실행 결과 동일, block UUID 유지
- end_date 이후 무이동, weekly_review missed
- 재계획과 submit 경합 시 active 중복·session orphan·기록 재연결 없음
- partial submit은 session·block을 terminal로 만들지 않음
- in_progress rollover 후 resume, 완료·취소 후 BLOCKED Planner 재평가
- session 취소 시 records 보존, session·block cancelled, 중복 효과 1회
- 7일 plan의 day 7 weekly_review 정확히 1개
- 1~6일 plan의 weekly_review 0개
- Planner draft와 수동 이동·교체의 필수 learning capacity 위반 0개
- rollover overload threshold와 표시 결과 일치
- 같은 input/config/anchor의 결정론적 결과
- cancelled 제외, missed 포함 진행률
- weekly blueprint의 문항 수·점수·stratum quota 검증

## 19. 구현 전 승인 항목

- 기존 study_plan_mypage Plan 헤더 + 정규화 Day·Block 모델
- Asia/Seoul 업무 시간대
- review를 명시적 완료 block으로 유지
- 진행 중 session이 있으면 계획 활성화 보류
- plan 연결 session 이어풀기·취소 상태와 diagnosis 자동 삭제 범위
- 7일 plan의 6일 학습 + 1일 평가
- weekly assessment stratum·quota — 초안 확정(11장), Questions 적재 후 pool 검증으로 최종 승인
- config 저장 위치와 변경 승인 절차
