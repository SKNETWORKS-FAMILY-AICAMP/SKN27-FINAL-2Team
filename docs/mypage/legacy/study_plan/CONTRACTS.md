# 학습계획 v2 계약

- 상태: PROPOSED CANONICAL
- 구현 상태: 미구현
- 최종 검토일: 2026-07-14

이 문서는 SPEC.md의 정책을 Python·HTTP·DB 연결 계약으로 고정한다. 구현 내부 구조가 바뀌어도 이 계약은 acceptance 승인 없이 변경하지 않는다.

## 0. 외부 협조 상태

2026-07-14 기준 diagnosis·DB 담당에 다음 항목을 요청했다.

- study_plan_mypage 사용자별 active partial unique index
- SolveSessions.review_type 컬럼·CHECK·조회 index
- diagnosis_start의 weekly_review 저장과 일반 진단 NULL 유지
- diagnosis_submit의 멱등 WEEKLY_REPORT enqueue
- versioned blueprint 기반 고정 비율 종합평가

이미 구현된 주간평가 block 검증, SolveRecords 연결값 저장, 제출 시 block 완료는 재작업하지 않는다. 요청 반영 여부는 실제 SQL·model·test가 들어온 뒤 확인하며, 요청을 보냈다는 사실을 구현 완료로 보지 않는다.

## 1. 명명 규칙

- DB와 Python 내부: snake_case
- 기존 HTTP 응답과 template DTO: camelCase
- question·diagnosis 시작 요청의 기존 연결 필드: studyplan_id, study_plan_block_id
- 안정 식별자: plan_id와 block_id
- label은 표시용이며 식별에 사용하지 않는다.
- API가 받는 날짜는 YYYY-MM-DD이고 서버가 plan timezone으로 검증한다.

## 2. v1 공개 Python façade

전환 기간에는 analytics.service.studyplan 모듈을 삭제하지 않는다. 다음 import 경로와 호출 계약을 façade에서 유지한다.

예외:

- StudyPlanBlockDeleteLimitExceeded
- StudyPlanDateOutOfRange
- StudyPlanExtraBlockUnavailable
- StudyPlanExtraBlockCompletionRequired
- StudyPlanGenerationUnavailable

서비스:

- get_user_study_info
- get_study_plan_info
- ensure_today_study_plan
- create_study_plan
- delete_study_plan_block
- complete_study_plan_block
- complete_study_plan_block_by_id
- add_extra_study_plan_block
- calculate_record_based_plan_progress
- get_study_plan_config
- is_weekly_review_plan_block

v2 구현은 façade 뒤에서 선택한다. 기존 consumer를 한 번에 변경하지 않는다.

## 3. v2 application command

모든 command 매개변수에는 타입을 명시한다. dict 남용 대신 TypedDict, dataclass 또는 Pydantic schema 중 하나로 고정한다.

### GeneratePlanCommand

- user_id: int
- reason: initial, manual, weekly_report, recovery
- source_plan_id: int nullable
- source_plan_revision: int nullable
- source_report_id: int nullable
- idempotency_key: str
- requested_by: user, system, worker
- now: 테스트·관리자 전용 nullable

### SyncPlanCommand

- user_id: int
- plan_id: int
- expected_plan_revision: int
- local_date: 서버 계산
- idempotency_key: str

### BlockCommand 공통

- user_id: int
- plan_id: int
- block_id: UUID
- expected_plan_revision: int
- expected_block_revision: int
- idempotency_key: str

MoveBlockCommand는 target_date를, ReplaceBlockCommand는 replacement reason을 추가한다. 클라이언트가 replacement filter나 question count를 정하지 않는다.

## 4. 서비스 응답 DTO

화면과 API에는 정규화 테이블을 다시 중첩 DTO로 조립해 전달한다.

~~~json
{
  "studyPlanId": 101,
  "planVersion": 3,
  "revision": 7,
  "status": "active",
  "generationReason": "weekly_report",
  "timezone": "Asia/Seoul",
  "startDate": "2026-07-15",
  "endDate": "2026-07-21",
  "dailyAvailableMinutes": 60,
  "summary": "조선 정치·사료 해석 중심 계획",
  "progressRate": 0.35,
  "plans": [
    {
      "date": "2026-07-15",
      "dayOrdinal": 1,
      "capacityMinutes": 60,
      "blocks": [
        {
          "blockId": "00000000-0000-0000-0000-000000000001",
          "revision": 1,
          "blockType": "practice",
          "focusKind": "weakness",
          "status": "scheduled",
          "groupKeyId": "era=조선|topic=정치|q_type=사료",
          "era": "조선",
          "topic": "정치",
          "qType": "사료",
          "questionCount": 5,
          "estimatedMinutes": 30,
          "reason": "보정 취약 점수와 악화 추세",
          "achievedCount": 0,
          "progressRate": 0.0,
          "canStart": true
        }
      ]
    }
  ]
}
~~~

canStart와 progress는 서버가 계산한 표시 projection이다. 클라이언트가 다시 저장하지 않는다.
capacityMinutes는 v2의 learning_capacity_minutes를 기존 camelCase DTO로 투영한 값이며 weekly assessment 시간은 포함하지 않는다.

## 5. HTTP endpoint

기존 endpoint는 전환 기간 유지한다.

| Method | Path | v2 의미 |
|---|---|---|
| GET | /analytics/mypage/ | active plan read, mutation 없음 |
| POST | /analytics/study-plan/create/ | generate command |
| POST | /analytics/study-plan/block/delete/ | legacy replace adapter |
| POST | /analytics/study-plan/block/complete/ | review complete |
| POST | /analytics/study-plan/block/add/ | extra practice |
| POST | /question/api/start/ | practice 시작 |
| POST | /diagnosis/api/start/ | weekly_review 시작 |

v2에서 추가할 endpoint:

| Method | Path | 의미 |
|---|---|---|
| POST | /analytics/study-plan/sync/ | rollover 명시적 실행 또는 enqueue |
| POST | /analytics/study-plan/block/move/ | scheduled block 이동 |
| POST | /analytics/study-plan/block/replace/ | legacy delete의 명확한 이름 |
| GET | /analytics/study-plan/report/status/ | 주간 리포트·다음 계획 job 상태 |
| POST | /analytics/study-plan/report/retry/ | DEAD report job 수동 재시도 |
| POST | /question/api/session/{session_id}/cancel/ | plan 연결 practice session 명시적 취소 |
| POST | /diagnosis/api/session/{session_id}/cancel/ | plan 연결 weekly_review session 명시적 취소 |

모든 POST mutation은 Idempotency-Key header를 요구한다. 기존 UI가 준비될 때까지 legacy adapter가 안정적인 key를 생성할 수 있으나 canary 전에는 클라이언트 전달 방식으로 전환한다.

## 6. Block mutation 요청

신규 요청 기본 형태:

~~~json
{
  "studyPlanId": 101,
  "blockId": "00000000-0000-0000-0000-000000000001",
  "expectedPlanRevision": 7,
  "expectedBlockRevision": 1
}
~~~

move는 targetDate를 추가한다.

legacy dayIndex·blockIndex 요청:

- v1 façade에서 현재 DTO의 blockId로 즉시 해석한다.
- 해석 뒤에는 같은 v2 command를 호출한다.
- stale 화면으로 index가 다른 block을 가리킬 수 있으면 409를 반환한다.
- 신규 UI에서는 전송하지 않는다.

## 7. 세션·블록 연결

v2 1차 전환에서는 이미 구현된 SolveRecords.studyplan_id와 study_plan_block_id를 실행 연결의 canonical 값으로 유지한다. SolveSessions에는 협조 요청된 review_type만 추가해 일반 진단과 주간평가를 식별한다.

제약과 조회 규칙:

- 계획에서 시작한 session의 모든 record는 같은 non-null studyplan_id·study_plan_block_id를 가져야 한다.
- session에서 distinct 연결 pair가 0개면 미연결, 1개면 정상, 2개 이상이면 DATA_INTEGRITY_ERROR다.
- 연결 pair는 사용자 소유 Plan·Block과 일치해야 하며 기존 record를 다른 Plan·Block으로 다시 연결하지 않는다.
- 일반 diagnosis는 review_type null, weekly_review는 session_type diagnostic·review_type weekly_review다.
- block row를 먼저 잠근 뒤 연결 record·session을 조회해 block당 in_progress session을 하나만 허용한다.
- SolveSessions에 plan·block FK를 중복 추가하는 안은 현재 협조 범위에 넣지 않으며 필요 시 별도 승인한다.

v2 canary 전 question·diagnosis의 block 조회는 공통 analytics plan resolver를 사용해야 한다. 전환 중에는 derived study_plan_items JSON도 같은 트랜잭션에서 갱신하지만, 신규 검증 로직이 JSON을 직접 해석하게 만들지 않는다.

## 8. 시작 API 검증

question과 diagnosis 시작 API는 다음 순서로 검증한다.

1. 로그인 사용자 소유 plan·block과 route 일치
2. Plan → Block 순서로 잠금
3. SolveRecords 연결값으로 기존 session 확인
4. 기존 in_progress면 새 session을 만들지 않고 이어풀기 정보 반환
5. 기존 completed면 재시작 거부
6. 새 session일 때만 plan active·revision·block scheduled 검증
7. 새 session일 때만 서버 local date와 block scheduled date 일치 검증
8. immutable_selection_spec과 question pool 검증
9. session·records 생성과 block in_progress 전이를 한 트랜잭션으로 저장

중복 start:

- 같은 idempotency key면 기존 session을 반환한다.
- 이미 in_progress session이 있으면 날짜가 지났어도 소유권·연결·route를 재검증한 뒤 이어풀기 정보를 반환한다.
- completed session이 있으면 재시작을 거부한다.
- question·diagnosis의 자동 정리는 plan에 연결된 in_progress session을 삭제하지 않는다. 미연결 session만 같은 session_type·review_type 범위에서 별도 정책으로 정리한다.

클라이언트가 보낸 era, topic, qType, count, difficulty는 block 선택 규칙을 대체하지 못한다.

## 9. 제출·완료 트랜잭션

Practice·weekly_review 제출:

1. user, plan, block, session을 공통 잠금 순서로 SELECT FOR UPDATE
2. 사용자·연결·active·revision·현재 상태 재검증
3. 지정된 전체 question에 finalized answer가 있는지 검증
4. 불완전하면 422 SESSION_INCOMPLETE를 반환하고 session·block은 in_progress 유지
5. 완전하면 answers, session completed, block completed를 함께 저장
6. weekly_review면 report와 WEEKLY_REPORT job upsert
7. plan revision 증가와 BLOCKED Planner wake-up event를 함께 저장
8. 한 트랜잭션으로 commit

중복 submit은 기존 완료 결과를 반환한다. 같은 answers를 다시 기록하거나 job을 추가하지 않는다.

답안 임시저장은 submit과 분리하며 terminal 상태를 만들지 않는다.

Session 취소:

1. Idempotency-Key와 expected plan·block revision 필수
2. user → plan → block → session 순서로 잠금
3. 사용자 소유, plan active, session·block in_progress, SolveRecords 연결 pair 일치 검증
4. SolveSessions.status=cancelled, cancelled_at, cancellation_reason 저장
5. 답안·SolveRecords는 삭제하거나 다른 block에 다시 연결하지 않음
6. block cancelled와 plan·block revision 증가
7. 취소 target을 다음 Planner carryover 입력 대상으로 표시
8. BLOCKED NEXT_PLAN job wake-up event 저장 후 commit

같은 취소 요청은 기존 결과를 반환한다. completed session은 취소할 수 없다. 이 계약을 위해 SolveSessions cancelled 상태·필드와 두 앱 endpoint를 추가 승인해야 하며, 반영 전에는 v2 자동 계획 교체를 배포하지 않는다.

Review 완료:

- review block만 허용
- scheduled → completed
- 이미 completed면 기존 결과 반환
- 다른 block_type이면 409

## 10. 서버 검증표

| 검증 | 실패 |
|---|---|
| 리소스 없음 또는 타 사용자 | 404 |
| 잘못된 입력 schema | 400 |
| stale revision | 409 STALE_REVISION |
| 과거·미래 block 시작 | 409 BLOCK_NOT_DUE |
| route mismatch | 409 BLOCK_ROUTE_MISMATCH |
| terminal block mutation | 409 BLOCK_TERMINAL |
| in-progress session 때문에 재계획 불가 | 409 PLAN_REPLACEMENT_BLOCKED |
| daily replace 제한 | 409 REPLACE_LIMIT_REACHED |
| pool 부족 | 422 QUESTION_POOL_INSUFFICIENT |
| 생성 후보 없음 | 422 PLAN_GENERATION_UNAVAILABLE |
| assessment blueprint 미충족 | 422 ASSESSMENT_BLUEPRINT_UNAVAILABLE |
| 제출 답안 미완료 | 422 SESSION_INCOMPLETE |
| 완료 session 취소 | 409 SESSION_TERMINAL |
| 동일 command 처리 중 | 409 COMMAND_IN_PROGRESS |
| idempotency key payload 충돌 | 409 IDEMPOTENCY_CONFLICT |
| 한 session에 plan 연결 pair가 여러 개 | 500 DATA_INTEGRITY_ERROR |

오류 응답은 code, message, details, traceId를 갖는다. details에는 개인정보, 정답, prompt, secret을 넣지 않는다.

## 11. Revision과 잠금

- 읽기 DTO에 plan revision과 block revision을 포함한다.
- mutation은 expected revision을 필수로 받는다.
- application service는 transaction 안에서 대상 row를 SELECT FOR UPDATE한다.
- revision 불일치는 409이며 자동 덮어쓰지 않는다.
- 성공한 mutation만 revision을 증가시킨다.
- 계획 finalize는 사용자 row와 source active plan을 잠근다.
- DB partial unique index가 active 중복을 최종 차단한다.
- 여러 도메인이 만나는 공통 잠금 순서는 user → plan → block → session → report → job → run이다.

## 12. 멱등성

- idempotency key는 사용자·명령 범위에서 unique다.
- 같은 key와 같은 payload는 저장된 결과를 반환한다.
- 같은 key와 다른 payload는 409다.
- weekly report dedupe key: weekly-report:session:{session_id}
- next plan dedupe key: next-plan:report:{report_id}
- rollover dedupe key: plan-rollover:{plan_id}:{local_date}
- source_report_id로 생성된 plan은 하나뿐이다.

일반 mutation receipt를 study_plan_command_receipts에 저장한다.

- receipt_id UUID PK
- user_id, command_type, idempotency_key
- request_hash, status: RUNNING·SUCCEEDED·FAILED
- response_status, response_body JSONB, resource_type, resource_id
- created_at, finished_at, expires_at
- UNIQUE(user_id, command_type, idempotency_key)

outer transaction에서 receipt row를 잠그고 domain mutation은 savepoint 안에서 실행한다. 성공하면 SUCCEEDED 응답을, 예상 가능한 domain 오류면 savepoint만 rollback한 뒤 FAILED 응답을 receipt에 저장하고 outer transaction을 commit한다. 같은 hash의 SUCCEEDED·FAILED는 저장 응답을 반환하고 RUNNING은 COMMAND_IN_PROGRESS, 다른 hash는 IDEMPOTENCY_CONFLICT다. crash로 남은 RUNNING receipt는 versioned timeout과 repair command로만 정리한다. response_body에는 개인정보·정답·prompt를 넣지 않으며 보존 기간은 versioned config로 관리한다.

## 13. 설정 계약

get_study_plan_config의 v1 dict 반환은 façade에서 유지한다. v2 내부는 typed config를 사용한다.

필수 특성:

- version 필수
- 알 수 없는 key 거부
- 범위와 합계 검증
- 실행 중 mutable global 사용 금지
- plan과 report에 사용한 version snapshot
- 하드코딩된 fallback 금지

## 14. Characterization 계약

v2 전환 전에 다음을 golden test로 고정한다.

- 공개 import와 inspect.signature
- 예외 class
- endpoint status와 payload key
- serializer의 camelCase 구조
- 구형 study_plan_items readback
- 기존 blockId 유지
- progress의 user·plan·block 음성 사례

의도적으로 바꾸는 동작은 CUTOVER.md의 승인 목록에만 기록한다.

## 15. 표시 계약

서버가 계산해 내려주는 표시 projection의 기준이다. 클라이언트는 이 값을 저장하지
않으며, 상태 계산은 서비스 함수가 담당하고 view는 호출 순서만 담당한다.

### 블록 버튼

`canStart = (plan_date == 오늘 local date) AND (block status가 terminal이 아님)`.
미래·과거 날짜 블록은 시작할 수 없고, 과거 날짜 화면은 기록 확인 전용이다.

| 블록 상태 | 표시 |
|---|---|
| 오늘 scheduled practice·weekly_review | 문제 풀기 |
| 오늘 scheduled review | 복습 완료 버튼 |
| 과거 미완료 (rollover 대상) | "오늘로 이월됨" 또는 비활성화 |
| completed practice·review | 완료 |
| completed weekly_review | 주간 평가 완료 (재시작 불가) |
| 미래 scheduled | 예정 |
| weekly_review missed | 미응시 (시작 차단) |

### 학습 플래너 패널 헤딩 (리포트·다음 계획 상태)

상태 원천은 `GET /analytics/study-plan/report/status/`다. 주간평가 미응시 표시는
리포트 상태 표시와 혼용하지 않는다.

| 상태 | 표시 |
|---|---|
| 주간평가 미제출 | 표시 없음 |
| WEEKLY_REPORT job 진행 중 (QUEUED/RUNNING/RETRY) | "AI 리포트 생성 중" 배지 |
| report READY + NEXT_PLAN 진행 중 | "리포트 보기" + "다음 주 계획 생성 중" 배지 |
| report READY + 새 계획 active | "리포트 보기" + "다음 주 계획이 준비됐어요" |
| NEXT_PLAN BLOCKED (IN_PROGRESS_SESSION) | "리포트 보기" + 진행 중 학습 완료·취소 안내 |
| NEXT_PLAN CANCELLED (SOURCE_PLAN_SUPERSEDED) | "리포트 보기"만 유지 (계획은 사용자가 이미 교체) |
| report FAILED 또는 NEXT_PLAN DEAD | 실패 배지 + [다시 생성] 버튼 (report/retry) |
| 주간평가 미응시 | "평가 미응시" 배지 (버튼 없음, 새 계획 생성 유도) |
