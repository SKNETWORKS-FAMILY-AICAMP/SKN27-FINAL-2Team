# 주간평가 완료 후 AI 주간 리포트 및 학습계획 생성 설계

## 목적

주간평가는 단순히 점수를 보여 주는 종료 화면이 아니라, 한 주 동안의 학습 결과를 요약하고 다음 주 학습계획을 만드는 트리거가 되어야 한다.

현재 학습계획은 `analytics` 앱의 `study_plan_mypage.study_plan_items` JSON과 `analytics` 집계 결과를 중심으로 동작한다. 따라서 주간평가 완료 후 흐름도 `analytics` 앱이 오케스트레이션을 담당하고, 문제 풀이/진단평가 앱은 완료 이벤트와 연결 식별자를 정확히 넘기는 역할로 제한하는 것이 좋다.

## 목표 동작

1. 사용자가 학습플래너의 주간평가 블록을 시작한다.
2. 진단평가 API가 주간평가 세션을 생성한다.
3. 사용자가 주간평가를 제출한다.
4. 제출 완료 시 해당 세션의 `solve_records`에 `studyplan_id`, `study_plan_block_id`가 있으면 리포트 원천 계획과 블록 연결값으로 남는다.
5. `analytics` 앱이 주간평가 블록을 완료 처리한다.
6. `analytics` 앱이 주간평가 결과와 최근 학습 기록을 바탕으로 AI 주간 리포트 생성 작업을 백그라운드 큐에 등록한다.
7. 제출 응답은 리포트 생성 완료를 기다리지 않고 즉시 반환하며, 나의 학습실은 리포트 상태(`PENDING`, `RUNNING`, `READY`, `FAILED`)를 표시한다.
8. 리포트가 준비되면 사용자가 "새 계획 만들기"를 선택할 수 있다.
9. 사용자가 새 계획 생성을 확정한 시점에만 Planner를 실행하고, 확정된 archive target 계획을 archived 처리한다.

## 멀티에이전트 역할 분리

### 구성도

```mermaid
flowchart TB
    O["Orchestrator<br/>흐름 조율 · 상태 FSM · lease"]

    subgraph reportWorker["리포트 worker (제출 시 백그라운드 실행)"]
        direction LR
        C["1. Weekly Review Collector<br/>원천 사실 · metric 수집"]
        WA["2. Weakness Analyst<br/>취약/개선 판정"]
        RW["3. Report Writer<br/>metric 토큰 초안"]
        RN["deterministic renderer<br/>metric_id → 검증 수치 치환"]
        C --> WA --> RW --> RN
    end

    P["4. Study Plan Planner<br/>다음 학습계획 생성<br/>(사용자 확정 시 실행)"]

    subgraph stores["데이터"]
        DB1[("solve_sessions<br/>solve_records")]
        DB2[("analytics<br/>study_plan_mypage")]
        DB3[("weekly_ai_reports")]
        CFG["weekly_report_config"]
    end

    O -- "제출 이벤트" --> C
    O -- "새 계획 만들기 확정" --> P

    DB1 --> C
    DB2 --> C
    C -- "collected_facts · source_metrics" --> DB3
    WA -- "analysis_result" --> DB3
    RN -- "summary · READY" --> DB3

    DB3 -- "analysis_result 재사용" --> P
    P -- "generated_studyplan_id" --> DB3
    P -- "새 학습계획 저장" --> DB2

    CFG -.-> WA
    CFG -.-> RW
    CFG -.-> P
```

제출 시점에는 Orchestrator가 Collector → Weakness Analyst → Report Writer → deterministic renderer까지만 리포트 worker에서 실행하고, Planner는 사용자가 "새 계획 만들기"를 확정한 시점에만 별도로 실행한다. 모든 산출물은 `weekly_ai_reports`에 분리 저장되어 실패 단계 구분과 Planner의 `analysis_result` 재사용을 가능하게 한다.

### 1. Weekly Review Collector

주간평가 완료 세션을 수집한다.

- 입력: `user_id`, `session_id`, `review_type`, `source_studyplan_id`, `study_plan_block_id`
- 조회 대상:
  - `solve_sessions`
  - `solve_records`
  - `analytics`
  - `study_plan_mypage`
- 산출:
  - `metric_id`가 부여된 주간평가 총 문항 수
  - `metric_id`가 부여된 정답률/오답률
  - `metric_id`가 부여된 평균 풀이 시간
  - `metric_id`가 부여된 시대/주제/유형별 오답률
  - `metric_id`가 부여된 기존 진단평가 대비 변화량
  - 비교 기준 세션 존재 여부와 선택 근거
  - 리포트 작성에 사용할 원천 사실 묶음(`collected_facts`)

### 2. Weakness Analyst

취약점을 판단한다.

- 취약 기준: 공통 취약점 판정 결과를 사용한다 (`docs/study_plan/취약점_분석_개선_설계.md`의
  weakness_score/status. 자체 임계값을 두지 않는다)
- 단일 분류뿐 아니라 `era + topic + q_type` 복합 취약점을 우선 사용한다.
- 표본 수가 부족한 항목(status=INSUFFICIENT)은 리포트에서 "관찰 필요"로 낮은 확신도를 붙인다.
- 산출:
  - 핵심 취약 `weekly_report_config.top_weak_limit`개
  - 개선된 항목 `weekly_report_config.top_improved_limit`개
  - 다음 주 우선 학습 대상
  - 근거 수치
  - 비교 대상이 없을 때 `analysis_result.comparison.status='INSUFFICIENT_BASELINE'` 신호

### 3. Report Writer

학생에게 보여 줄 주간 리포트 문장을 생성한다.

- 입력: Collector/Weakness Analyst 산출물
- 출력 형식:
  - 이번 주 요약
  - 진단평가 대비 변화
  - 가장 취약한 영역
  - 가장 개선된 영역
  - 다음 주 학습 전략
- 주의:
  - 점수만 말하지 않고 원인과 행동 제안을 함께 적는다.
  - 데이터가 부족하면 `analysis_result.comparison.status`를 읽어 단정하지 않고 "추가 풀이 필요"로 표시한다.
  - 숫자를 자유 서술로 직접 생성하지 않는다.
  - Writer는 `{metric:weekly_wrong_rate}` 같은 metric 참조 토큰만 사용하고, 렌더링 단계에서 Collector의 검증된 수치로 치환한다.
  - 저장 전 검증은 "참조한 metric_id가 모두 존재하는지"와 "허용되지 않은 숫자 literal이 문장에 섞였는지"를 확인한다.

### 4. Study Plan Planner

리포트 결과를 다음 학습계획으로 변환한다.

- 입력:
  - 원천 학습계획 ID(`sourceStudyPlanId`, fallback 리포트에서는 `null` 가능)
  - archive 대상 학습계획 ID(`archiveTargetStudyPlanId`)
  - 공통 취약점 판정 행 목록(`groupKeyId`, `weaknessScore`, `status`, `trend` 포함)
  - 개선 항목 목록(`trend == "IMPROVING"`)
  - 출제 예상 목록
  - 사용자 남은 시험일
  - 하루 학습 가능 시간
  - 최근 미완료 학습 블록
- 출력:
  - 6일 학습 + 1일 주간평가 구조
  - 각 일자별 학습 블록
  - 블록별 `era`, `topic`, `qType`, `questionCount`, `reason`
- 반영 규칙:
  - 취약 항목은 다음 계획의 우선 학습 블록으로 배치한다.
  - 개선 항목은 완전히 제외하지 않고 유지 복습 후보로 낮은 가중치를 둔다.
  - 개선 항목의 복습 비중은 `weekly_report_config.improved_review_ratio`로 조절한다.
  - 같은 `groupKeyId`가 취약 항목과 개선 항목에 동시에 있으면 최신 `trend/status`를 기준으로 한 번만 배치한다.

### 5. Orchestrator

전체 흐름을 조율한다.

- 주간평가 완료 이벤트를 받는다.
- 제출 시점에는 Collector, Weakness Analyst, Report Writer까지만 백그라운드로 실행한다.
- Planner는 사용자의 "새 계획 만들기" 행동 시점에만 실행한다.
- 실패 시 `collected_facts`, `analysis_result`, `writer_draft`를 분리 저장하고 재시도 가능하게 한다.
- 백그라운드 작업은 상태 FSM과 lease로 제어한다.
  - 상태: `PENDING`, `RUNNING`, `READY`, `FAILED`
  - lease 필드: `locked_until`, `lease_token`, `attempt_count`, `last_error`
  - Planner lease 필드: `planner_locked_until`, `planner_lease_token`, `planner_attempt_count`, `planner_last_error`
- 새 학습계획 생성 전 기존 active 계획을 archived 처리하는 작업은 사용자 확정 이후에만 수행한다.
- 새 계획 생성 요청도 멱등하게 처리하되, 락을 잡은 채 Planner를 실행하지 않는다. Planner가 LLM/계산을 포함하면 락 대기가 길어지므로 claim → 실행 → finalize 세 단계로 나눈다.
  - claim 트랜잭션: `weekly_ai_reports` row를 `SELECT ... FOR UPDATE`로 잠그고 재실행 여부를 판정한 뒤 lease만 확보하고 즉시 커밋해 락을 푼다.
  - Planner 실행: 락 없이 수행한다. 계산이 길어져도 다른 요청을 막지 않는다.
  - finalize 트랜잭션: 같은 row를 다시 잠그고, archive target 계획 archive와 새 계획 insert를 한 트랜잭션에서 수행한다.
  - 이미 `generated_studyplan_id`가 있으면 claim 단계에서 기존 계획 ID를 반환한다.
  - active 계획 archive는 `planner_archive_target_studyplan_id`와 `status='active'` 조건을 함께 걸어 한 번만 적용한다.

## LangGraph 그래프 설계

에이전트는 두 개의 독립된 LangGraph 그래프로 나눈다. 리포트 생성 그래프는 제출 시점에 worker가 실행하고, Planner 그래프는 사용자의 "새 계획 만들기" 확정 시점에 실행한다. 두 그래프를 분리하는 이유는 실행 트리거와 lease 필드(`locked_until` vs `planner_locked_until`)가 다르고, 리포트가 `READY`인 상태에서 Planner만 여러 번 재시도될 수 있기 때문이다.

lease 획득·`attempt_count` 증가·`RUNNING`/`FAILED` 전이 같은 FSM 제어는 그래프 바깥의 worker가 담당한다. 그래프 노드는 한 번의 실행 안에서 단계별 산출물을 만들고, 실패 시 어느 단계에서 멈췄는지만 상태에 남긴다.

### 공유 상태

두 그래프가 공유하지 않는 별도 상태를 쓰되, 리포트 그래프 상태는 다음 필드를 갖는다.

```python
class WeeklyReviewState(TypedDict):
    # 식별자
    user_id: int
    session_id: int
    review_type: str
    source_studyplan_id: int | None
    study_plan_block_id: str | None
    # 단계별 산출물 (weekly_ai_reports 컬럼과 1:1 대응)
    collected_facts: dict
    source_metrics: dict
    analysis_result: dict
    writer_draft: dict
    rendered_report: dict
    # 제어
    write_retry: int
    failed_stage: str | None   # "collect" | "analyze" | "write" | "render"
    last_error: str | None
```

각 노드는 자기 산출물 필드만 채운다. `failed_stage`가 채워지면 그 값으로 "수집 오류/분석 오류/서술 오류/렌더 오류"를 구분해 `weekly_ai_reports`에 저장한다.

### 리포트 생성 그래프

```mermaid
flowchart TD
    START([START]) --> collect["collect<br/>Weekly Review Collector"]
    collect -->|"수집 오류"| pf["persist_failed"]
    collect -->|"정상 (비교 대상 없어도 진행)"| analyze["analyze<br/>Weakness Analyst"]
    analyze -->|"분석 오류"| pf
    analyze -->|"정상"| write["write<br/>Report Writer"]
    write -->|"검증 실패 & write_retry < max_write_retry"| write
    write -->|"검증 실패 & 재시도 소진"| pf
    write -->|"검증 통과"| render["render<br/>metric_id → 검증 수치 치환"]
    render -->|"렌더 오류"| pf
    render -->|"정상"| pr["persist_ready<br/>report_status=READY · modified_at 갱신"]
    pr --> ENDR([END])
    pf --> ENDF([END])
```

조건부 엣지 라우팅 규칙:

- `collect` 이후: 예외가 있으면 `persist_failed`. 비교 대상 세션이 없는 것은 오류가 아니라 `collected_facts.comparison.baseline_session_id=NULL`로 남긴 뒤 정상 진행한다.
- `analyze` 이후: 예외가 있으면 `persist_failed`, 아니면 `write`.
- `write` 이후: Writer 초안을 저장 전 검증한다. 검증은 "참조한 `metric_id`가 `source_metrics`에 모두 존재하는지"와 "허용되지 않은 숫자 literal이 문장에 섞였는지"를 본다. 검증 실패면 `write_retry`를 올려 재시도하고, 허용된 추가 재작성 횟수를 소진하면 `persist_failed`로 간다.
- `render` 이후: 예외가 있으면 `persist_failed`, 아니면 `persist_ready`.

`write_retry`는 최초 Writer 호출 이후의 추가 재작성 횟수다. `weekly_report_config.max_write_retry=2`이면 최초 1회 + 재작성 2회까지 허용한다. `write`의 재시도 자기 루프는 그래프 안에서 발생하고, `persist_failed`로 끝난 뒤의 전체 재실행은 worker가 lease와 `attempt_count`로 제어한다. 두 재시도 계층을 섞지 않는다.

라우팅 함수는 `else` 없이 가드 방식으로 작성한다.

```python
def route_after_write(state: WeeklyReviewState, config: WeeklyReportConfig) -> str:
    if state["failed_stage"] == "write":
        return "persist_failed"
    if is_report_valid(state["writer_draft"], state["source_metrics"]):
        return "render"
    if state["write_retry"] < config.max_write_retry:
        return "write"
    return "persist_failed"
```

### Planner 그래프

Planner는 락을 잡은 채 실행하지 않는다. claim(짧은 트랜잭션) → run_planner(락 없음) → finalize(짧은 트랜잭션) 순서다.

```mermaid
flowchart TD
    START([START]) --> claim["claim (트랜잭션 A)<br/>SELECT ... FOR UPDATE → lease/token 확보 후 커밋(락 해제)"]
    claim -->|"report_status != READY"| reject["reject<br/>리포트 미완료 반환"]
    claim -->|"generated_studyplan_id 존재"| existing["return_existing<br/>기존 계획 ID 반환"]
    claim -->|"planner RUNNING & lease 유효"| running["return_running<br/>현재 상태 반환"]
    claim -->|"archive target 없음"| needsTarget["persist_planner_needs_target<br/>planner_status=NEEDS_ARCHIVE_TARGET"]
    claim -->|"attempt 상한 도달"| capped["persist_planner_attempt_capped<br/>planner_status=FAILED"]
    claim -->|"NOT_REQUESTED / FAILED / lease 만료 + 상한 미도달"| claimed["claim_planner_attempt<br/>attempt+1 · lease/token 저장 · 커밋"]
    claimed --> run["run_planner (락 없음)<br/>Study Plan Planner"]
    run -->|"생성 오류"| ppf["persist_planner_error (트랜잭션 C)"]
    run -->|"정상"| fin["finalize (트랜잭션 C)<br/>재잠금 + archive target 처리 + 새 계획 insert"]
    fin --> ppr["persist_planner_ready<br/>generated_studyplan_id 저장"]
    reject --> ENDX([END])
    existing --> ENDX
    running --> ENDX
    needsTarget --> ENDX
    capped --> ENDX
    ppr --> ENDX
    ppf --> ENDF2([END])
```

claim 트랜잭션의 라우팅이 새 계획 생성 멱등성의 핵심이다. `SELECT ... FOR UPDATE`로 row를 잠근 뒤 우선순위대로 분기하고, `claim_planner_attempt`로 갈 때만 lease를 잡고 커밋한다.

```python
def route_after_claim(state: PlannerState, config: WeeklyReportConfig, now) -> str:
    if state["report_status"] != "READY":
        return "reject"
    if state["generated_studyplan_id"] is not None:
        return "return_existing"
    if (
        state["planner_status"] == "RUNNING"
        and state["planner_locked_until"] is not None
        and state["planner_locked_until"] > now
    ):
        return "return_running"
    if state["resolved_archive_target_studyplan_id"] is None:
        return "persist_planner_needs_target"
    if state["planner_attempt_count"] >= config.planner_max_attempts:
        return "persist_planner_attempt_capped"
    return "claim_planner_attempt"
```

`resolved_archive_target_studyplan_id`는 claim 트랜잭션 안에서 기존 `planner_archive_target_studyplan_id`, active 상태의 `source_studyplan_id`, 사용자 요청의 `archiveTargetStudyPlanId` 순서로 해석한 값이다. 값이 없으면 `planner_status='NEEDS_ARCHIVE_TARGET'`, `planner_last_error=NULL`, `planner_locked_until=NULL`, `planner_lease_token=NULL`, `modified_at=NOW()`를 저장하고 Planner를 실행하지 않는다. 이 상태는 실패 시도 횟수에 포함하지 않는다. `claim_planner_attempt`에서만 `planner_attempt_count`를 1 증가시키고 `planner_archive_target_studyplan_id=:resolved_archive_target_studyplan_id`, `planner_locked_until=NOW()+weekly_report_config.planner_lease_minutes`, `planner_lease_token=:planner_lease_token`, `planner_status='RUNNING'`을 저장한다. `run_planner`는 트랜잭션 밖에서 실행되므로 계산이 길어도 락을 잡지 않는다. finalize 트랜잭션은 같은 row를 다시 잠그고, 이 요청의 `planner_lease_token`이 아직 유효한지 확인한 뒤(만료·교체됐으면 결과를 버린다) archive target 처리와 새 계획 insert, `generated_studyplan_id`·`planner_status='READY'` 저장을 한 트랜잭션에서 커밋한다. 실패 처리(`persist_planner_error`)는 `WHERE planner_status='RUNNING' AND planner_lease_token=:planner_lease_token AND planner_locked_until > NOW()` 조건이 맞을 때만 수행하고, 조건이 맞지 않으면 stale 실행 결과로 보고 버린다. 조건이 맞으면 `planner_attempt_count`를 다시 증가시키지 않고 `planner_last_error`, `planner_locked_until=NULL`, `planner_lease_token=NULL`, `modified_at`만 갱신한다.

## analytics 앱 내 구현 범위

analytics 앱에서 직접 구현 가능한 일:

- 주간평가 완료 후 비교 데이터 조회
- 주간평가 블록 완료 처리
- AI 리포트 저장 모델 또는 JSON 저장 정책 설계
- 사용자가 확정한 뒤 다음 학습계획 생성
- 나의 학습실 표시
- 공통 취약점 판정 결과 적용
- 오늘 학습계획 자동 보정
- 전날 미완료 블록 오늘 이월
- 백그라운드 리포트 작업 상태 관리
- 리포트 생성 재시도와 lease 관리

analytics 앱에서 직접 하기 어려운 일:

- 진단평가 시작 API가 `review_type`, `studyplan_id`, `study_plan_block_id`를 세션/레코드에 저장하는 것
- 진단평가 제출 API가 주간평가 완료 이벤트를 직접 호출하는 것
- 문제풀이 앱의 세션 생성/제출 payload 변경

이 부분은 아래 "외부 앱 변경 요청서"로 분리한다.

## 저장 구조 제안

### 주간 리포트 저장

새 테이블을 추가할 수 있다면 다음 구조를 권장한다.

```sql
weekly_ai_reports (
    report_id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL,
    source_studyplan_id BIGINT NULL,
    session_id BIGINT NOT NULL,
    report_status VARCHAR(20) NOT NULL DEFAULT 'PENDING',
    summary TEXT NOT NULL DEFAULT '',
    strengths JSONB NOT NULL DEFAULT '[]',
    weaknesses JSONB NOT NULL DEFAULT '[]',
    recommendations JSONB NOT NULL DEFAULT '[]',
    source_metrics JSONB NOT NULL DEFAULT '{}',
    collected_facts JSONB NOT NULL DEFAULT '{}',
    analysis_result JSONB NOT NULL DEFAULT '{}',
    writer_draft JSONB NOT NULL DEFAULT '{}',
    rendered_report JSONB NOT NULL DEFAULT '{}',
    failed_stage VARCHAR(20) NULL,
    planner_status VARCHAR(20) NOT NULL DEFAULT 'NOT_REQUESTED',
    generated_studyplan_id BIGINT NULL,
    planner_archive_target_studyplan_id BIGINT NULL,
    planner_locked_until TIMESTAMPTZ NULL,
    planner_lease_token UUID NULL,
    planner_attempt_count INT NOT NULL DEFAULT 0,
    planner_last_error TEXT NULL,
    locked_until TIMESTAMPTZ NULL,
    lease_token UUID NULL,
    attempt_count INT NOT NULL DEFAULT 0,
    last_error TEXT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    modified_at TIMESTAMPTZ NOT NULL,
    CONSTRAINT weekly_ai_reports_user_session_uidx UNIQUE (user_id, session_id),
    CONSTRAINT weekly_ai_reports_status_check CHECK (
        report_status IN ('PENDING', 'RUNNING', 'READY', 'FAILED')
    ),
    CONSTRAINT weekly_ai_reports_planner_status_check CHECK (
        planner_status IN (
            'NOT_REQUESTED',
            'NEEDS_ARCHIVE_TARGET',
            'RUNNING',
            'READY',
            'FAILED'
        )
    ),
    CONSTRAINT weekly_ai_reports_failed_stage_check CHECK (
        failed_stage IS NULL
        OR failed_stage IN ('collect', 'analyze', 'write', 'render')
    )
);

CREATE INDEX weekly_ai_reports_worker_claim_idx
    ON weekly_ai_reports(report_status, locked_until, report_id)
    WHERE report_status IN ('PENDING', 'RUNNING');
```

리포트 worker는 위 인덱스로 claim 대상을 폴링한다. Planner 경로는 worker 폴링이 아니라 사용자 요청이 아는 `report_id`/`(user_id, session_id)`로 단건 조회하므로 PK·unique 제약으로 충분하고, 별도 claim 인덱스를 두지 않는다.

백그라운드 worker(lease, `ON CONFLICT`, `SELECT ... FOR UPDATE`)를 쓰는 이상 DB 테이블은 필수다. JSON 파일 임시 저장은 이 동시성 제어와 맞지 않으므로, worker·lease 없이 단일 프로세스에서 동기 실행하는 로컬 데모용으로만 허용한다. worker를 붙이는 순간 unique 제약이 있는 별도 DB 테이블로 전환한다.

`source_metrics`, `collected_facts`, `analysis_result`는 의도를 분리한다.

- `source_metrics`: 렌더링 치환용 metric_id와 검증된 숫자 값
- `collected_facts`: Collector가 DB에서 수집한 원천 사실
- `analysis_result`: Weakness Analyst의 판정 결과와 Planner 재사용 입력
- `writer_draft`: Report Writer가 생성한 metric 참조 토큰 기반 초안
- `rendered_report`: deterministic renderer가 만든 최종 구조화 리포트. 화면용 `summary`, `strengths`, `weaknesses`, `recommendations`는 이 값에서 분리 저장한다.
- `failed_stage`: 실패가 발생한 리포트 그래프 단계
- `source_studyplan_id`: 이 리포트의 원천이 된 기존(주간평가 대상) 학습계획 ID
- `planner_status`: 사용자의 새 계획 생성 요청 처리 상태. `NEEDS_ARCHIVE_TARGET`은 새 계획을 만들기 전 사용자가 교체할 active 계획을 확정해야 하는 상태다.
- `generated_studyplan_id`: Planner가 새로 만든 계획 ID. 버튼 중복 클릭 시 이 값을 반환한다.
- `planner_archive_target_studyplan_id`: 새 계획 저장 시 archived 처리할 active 계획 ID. 기본값은 `source_studyplan_id`이지만, `source_studyplan_id`가 없으면 사용자가 확정한 현재 active 계획 ID를 요청에서 받아 저장한다.
- `lease_token`, `planner_lease_token`: lease 만료 후 다른 worker/request가 같은 row를 다시 잡았을 때 이전 실행 결과가 덮어쓰지 못하게 하는 실행별 UUID

참조 무결성은 다음 정책으로 둔다.

- `user_id`, `session_id`는 원천 사용자가 사라지거나 세션이 사라지면 리포트도 의미가 없으므로 DB 구현 시 FK를 걸고 삭제 정책은 서비스 정책에 맞춰 `CASCADE` 또는 사용자 삭제 배치에서 함께 제거한다.
- `source_studyplan_id`, `generated_studyplan_id`, `planner_archive_target_studyplan_id`는 과거 리포트 조회를 위해 물리 삭제보다 soft delete/상태 변경을 우선한다. 물리 삭제를 허용해야 한다면 FK는 `SET NULL`로 두고 리포트에는 당시의 `collected_facts`와 `analysis_result`를 보존한다.

이 분리가 있어야 실패 시 "수집 오류", "분석 오류", "서술 오류"를 구분할 수 있고, 사용자가 나중에 새 계획을 만들 때 분석 결과를 재사용할 수 있다.

### enqueue 멱등성

`diagnosis_submit`에서 주간 리포트 작업을 enqueue할 때는 `(user_id, session_id)` unique 제약을 전제로 upsert한다.

```sql
INSERT INTO weekly_ai_reports (
    user_id,
    source_studyplan_id,
    session_id,
    report_status,
    created_at,
    modified_at
)
VALUES (
    :user_id,
    :source_studyplan_id,
    :session_id,
    'PENDING',
    NOW(),
    NOW()
)
ON CONFLICT (user_id, session_id) DO NOTHING;
```

중복 제출 또는 네트워크 재시도로 같은 세션이 다시 들어오면 새 row를 만들지 않고 기존 row를 그대로 사용한다. API 응답에서 리포트 상태가 필요하면 upsert 이후 `(user_id, session_id)`로 다시 조회한다.

이미 `READY` 또는 `RUNNING`인 row를 `PENDING`으로 되돌리지 않는다.

worker는 반드시 제출 트랜잭션이 **커밋된 뒤에** 깨운다. enqueue INSERT 자체는 제출 트랜잭션 안에서 수행하되, worker 알림/스케줄링은 Django `transaction.on_commit()` 콜백에 등록한다. 그렇지 않으면 worker가 아직 커밋되지 않은 row나 session/records를 못 보거나, 롤백된 작업을 잡으려 할 수 있다.

```python
def enqueue_weekly_report(user_id, source_studyplan_id, session_id):
    upsert_pending_report(user_id, source_studyplan_id, session_id)  # ON CONFLICT DO NOTHING
    transaction.on_commit(lambda: notify_report_worker(user_id, session_id))
```

### 리포트 worker claim 절차

리포트 생성도 Planner와 같은 시도 횟수 규칙을 쓴다. `attempt_count`는 실행 직전 claim에서만 증가하고, 실패 저장 단계에서는 다시 증가시키지 않는다. `weekly_report_config.max_attempts=3`이면 실제 실행은 최대 3회다.

1. worker가 트랜잭션을 시작한다.
2. `report_status='PENDING'` 또는 lease가 만료된 `RUNNING` row를 `SELECT ... FOR UPDATE SKIP LOCKED`로 잡는다.
3. `attempt_count >= weekly_report_config.max_attempts`이면 실행하지 않고 `report_status='FAILED'`, `last_error='attempt_limit_exceeded'`, `locked_until=NULL`, `lease_token=NULL`, `modified_at=NOW()`로 저장한다.
4. 상한 미도달이면 실행별 `lease_token`을 생성하고 `report_status='RUNNING'`, `locked_until=NOW()+weekly_report_config.lease_minutes`, `lease_token=:lease_token`, `attempt_count=attempt_count+1`, `last_error=NULL`, `modified_at=NOW()`를 저장하고 커밋한다.
5. 커밋 후 Collector → Weakness Analyst → Report Writer → renderer를 실행한다. 실행 중에는 DB row 락을 잡지 않는다.
6. 성공하면 `WHERE report_status='RUNNING' AND lease_token=:lease_token AND locked_until > NOW()` 조건으로 다시 잠그고, 토큰과 lease가 유효할 때만 `rendered_report`, `summary`, `strengths`, `weaknesses`, `recommendations`, `source_metrics`, `collected_facts`, `analysis_result`, `writer_draft`, `report_status='READY'`, `failed_stage=NULL`, `locked_until=NULL`, `lease_token=NULL`, `last_error=NULL`, `modified_at=NOW()`를 저장한다.
7. 실패하면 같은 `lease_token`과 `locked_until > NOW()` 조건으로 마지막 중간 산출물과 `failed_stage`, `last_error`, `locked_until=NULL`, `lease_token=NULL`, `modified_at=NOW()`를 저장한다. 이 실행으로 `attempt_count >= weekly_report_config.max_attempts`가 됐으면 `FAILED`, 아직 남았으면 `PENDING`으로 되돌려 다음 worker가 재시도할 수 있게 한다. 토큰이나 lease가 일치하지 않으면 이미 다른 실행이 이어받은 것으로 보고 결과를 버린다.

### 새 계획 생성 멱등성

사용자가 "새 계획 만들기"를 누르는 경로는 리포트 생성 worker와 별도이므로 별도 동시성 제어가 필요하다. Planner 실행이 길어질 수 있으므로 락을 잡은 채 실행하지 않고 세 단계로 나눈다.

**claim 트랜잭션 (짧게, 락 확보용)**

1. 트랜잭션을 시작한다.
2. 대상 `weekly_ai_reports` row를 `SELECT ... FOR UPDATE`로 잠근다.
3. `report_status='READY'`가 아니면 아무것도 하지 않고 현재 상태를 반환한다.
4. `generated_studyplan_id`가 이미 있으면 archive/create 없이 해당 ID를 반환한다.
5. `planner_status='RUNNING'`이고 `planner_locked_until > NOW()`이면 중복 요청으로 보고 현재 상태를 반환한다.
6. `resolved_archive_target_studyplan_id`를 정한다.
   - 기존 `planner_archive_target_studyplan_id`가 있고 아직 active이면 그 값을 유지한다.
   - `source_studyplan_id`가 있고 아직 active이면 그 값을 사용한다.
   - `source_studyplan_id`가 없거나 이미 active가 아니면 사용자가 확정한 `archiveTargetStudyPlanId` 요청값을 사용한다.
   - 둘 다 없으면 `planner_status='NEEDS_ARCHIVE_TARGET'`으로 저장하고 Planner를 실행하지 않는다.
7. 그 외(`NOT_REQUESTED`, `NEEDS_ARCHIVE_TARGET`, `FAILED`, lease 만료 `RUNNING`)이면 재실행 대상이다.
   - `planner_attempt_count >= weekly_report_config.planner_max_attempts`이면 실행하지 않고 `planner_status='FAILED'`, `planner_last_error='attempt_limit_exceeded'`, `planner_locked_until=NULL`, `planner_lease_token=NULL`, `modified_at=NOW()`로 저장한다.
   - 상한 미도달이면 실행별 `planner_lease_token`을 생성하고 이 claim에서만 `planner_attempt_count=planner_attempt_count+1`, `planner_archive_target_studyplan_id=:resolved_archive_target_studyplan_id`, `planner_status='RUNNING'`, `planner_locked_until=NOW()+weekly_report_config.planner_lease_minutes`, `planner_lease_token=:planner_lease_token`, `modified_at=NOW()`를 저장한다.
8. 트랜잭션을 커밋해 락을 즉시 푼다. claim이 성공한 요청은 이후 Planner 실행 단계로 진행한다.

**Planner 실행 (락 없음)**

- claim에 성공한 요청만 Planner를 실행한다. 이 단계에서는 어떤 row도 잠그지 않는다.
- LLM/계산이 오래 걸려도 다른 요청이나 worker를 막지 않는다.

**finalize 트랜잭션 (짧게, 반영용)**

1. 트랜잭션을 시작한다.
2. 같은 row를 다시 `SELECT ... FOR UPDATE`로 잠근다.
3. `planner_status='RUNNING'`이고 `planner_lease_token`이 이 요청의 토큰과 일치하며 `planner_locked_until > NOW()`인지 확인한다. 만료·교체됐으면 이 요청 결과를 버린다(다른 실행이 이어받았다고 본다).
4. `planner_archive_target_studyplan_id`의 계획만 `WHERE id=:planner_archive_target_studyplan_id AND user_id=:user_id AND status='active'` 조건으로 archive한다. 대상이 더 이상 active가 아니면 stale 요청으로 보고 새 계획 insert를 중단한 뒤 사용자에게 재시도를 요구한다.
5. 새 학습계획을 insert한다.
6. `generated_studyplan_id`, `planner_status='READY'`, `planner_locked_until=NULL`, `planner_lease_token=NULL`, `modified_at`을 저장한다.
7. 커밋한다.

archive와 새 계획 insert는 반드시 finalize 트랜잭션 안에서 함께 처리한다. Planner 계산 자체는 트랜잭션 밖이다.

### 학습계획 생성 입력

AI 리포트가 Planner에 넘기는 최소 입력:

```json
{
  "userId": 1,
  "sourceStudyPlanId": 10,
  "archiveTargetStudyPlanId": 10,
  "weeklyReviewSessionId": 100,
  "weakTargets": [
    {
      "groupKeyId": "era:조선|topic:정치|qType:사료 해석",
      "era": "조선",
      "topic": "정치",
      "qType": "사료 해석",
      "weaknessScore": 0.72,
      "status": "WEAK",
      "trend": "WORSENING",
      "wrongRate": 0.42,
      "wrongCount": 5,
      "totalCount": 12,
      "reason": "최근 주간평가에서 오답률이 높음"
    }
  ],
  "improvedTargets": [
    {
      "groupKeyId": "era:고려|topic:문화|qType:개념",
      "era": "고려",
      "topic": "문화",
      "qType": "개념",
      "weaknessScore": 0.24,
      "status": "STABLE",
      "trend": "IMPROVING",
      "wrongRate": 0.18,
      "wrongCount": 2,
      "totalCount": 11,
      "reason": "이전 진단 대비 오답률이 낮아짐"
    }
  ],
  "predictionTargets": [
    {
      "era": "조선",
      "topic": "정치",
      "qType": "사료 해석",
      "expectedWeight": 0.35,
      "reason": "최근 오답률과 출제 빈도가 모두 높음"
    }
  ],
  "incompleteBlocks": [
    {
      "studyPlanBlockId": "block-uuid",
      "scheduledDate": "2026-07-07",
      "era": "고려",
      "topic": "문화",
      "qType": "개념",
      "remainingQuestionCount": 8
    }
  ],
  "availableMinutesPerDay": 60,
  "remainingDays": 14
}
```

`improvedTargets`는 별도 로직으로 다시 계산하지 않고, 공통 취약점 판정 행 중 `trend == "IMPROVING"`인 항목으로 정의한다.
`weakTargets`·`improvedTargets`는 리포트의 `analysis_result`를 재사용한다. 반면 `predictionTargets`(출제 예상)와 `incompleteBlocks`(최근 미완료 블록)는 Weakness Analyst 산출이 아니므로 `analysis_result`에 없다. 이 둘은 "새 계획 만들기" 확정 시점에 각각 analytics의 출제 예상 집계와 `study_plan_mypage` 현재 상태에서 새로 수집해 채운다.
`sourceStudyPlanId`는 리포트의 원천 계획 ID라서 fallback 리포트에서는 `null`일 수 있다. `archiveTargetStudyPlanId`는 새 계획으로 교체할 active 계획 ID이며, `sourceStudyPlanId`가 `null`이면 사용자 확정 요청에서 별도로 받아야 한다. 이 값이 없으면 Planner를 실행하지 않고 `NEEDS_ARCHIVE_TARGET`을 반환한다.

## 주간평가 완료 이벤트 처리 순서

```mermaid
flowchart TD
    start["주간평가 제출 완료"] --> link["session/block 연결 확인"]
    link -->|"block 연결 있음"| complete["학습계획 블록 완료 처리"]
    link -->|"block 연결 없음"| enqueue["weekly_ai_reports PENDING 저장"]
    complete --> enqueue
    enqueue --> response["제출 응답 반환"]
    enqueue --> worker["백그라운드 worker lease 획득"]
    worker --> running["RUNNING"]
    running --> snapshot["세션 분석 스냅샷 수집<br/>collected_facts.session_snapshot"]
    snapshot --> collect["Weekly Review Collector"]
    collect --> analyze["Weakness Analyst"]
    analyze --> report["Report Writer"]
    report --> validate["metric 참조 검증 및 렌더링"]
    validate --> ready["READY 저장"]
    ready --> mypage["나의 학습실 리포트 표시"]
    mypage --> userPlan["사용자가 새 계획 만들기 선택"]
    userPlan --> planner["Study Plan Planner"]
    planner --> archive["archive target 계획 archived"]
    archive --> savePlan["새 학습계획 저장"]
```

제출 API 안에서 LLM 호출을 직접 수행하지 않는다. Collector, Analyst, Writer만 리포트 worker에서 실행하고, Planner는 사용자 확정 이벤트에서 별도 실행한다.

여기서 세션 분석 스냅샷은 별도 테이블이 아니라 worker가 claim에 성공한 뒤 읽은 `solve_sessions`, `solve_records`, `analytics` 집계 결과의 고정 복사본이다. Collector가 이를 `collected_facts.session_snapshot`에 저장하고, 비교 대상 없음/표본 부족 같은 신호는 Analyst가 `analysis_result.comparison`에 저장한다. Report Writer는 이 필드만 읽어 "추가 풀이 필요" 표시 여부를 결정한다.

## 실패 처리

- 주간평가 세션은 완료됐지만 `study_plan_block_id`가 없으면:
  - 리포트 원천은 현재 제출된 `session_id`로 고정한다.
  - `source_studyplan_id=NULL`, `study_plan_block_id=NULL`로 저장한다.
  - 단, 학습계획 블록 자동 완료는 하지 않는다.
  - 이후 사용자가 새 계획을 만들려면 `archiveTargetStudyPlanId`를 별도로 확정해야 한다.
- AI 리포트 생성 실패:
  - `attempt_count`는 claim 단계에서 이미 증가했으므로 실패 처리에서는 다시 증가시키지 않는다.
  - `attempt_count >= weekly_report_config.max_attempts`이면 `report_status='FAILED'`로 고정한다.
  - 상한 미도달이면 `report_status='PENDING'`으로 되돌려 다음 worker가 다시 claim할 수 있게 한다.
  - `last_error`, 마지막 중간 산출물, `failed_stage`를 보존한다.
  - lease가 만료된 `RUNNING` 작업은 재시도 대상으로 본다.
  - 제출 응답 자체는 실패시키지 않는다.
  - 수동 재시도 버튼은 `report_status`를 `FAILED → PENDING`으로 되돌리고 `attempt_count=0`, `last_error=NULL`, `locked_until=NULL`, `lease_token=NULL`로 초기화한다. 이후 worker가 다시 집는다. 보존된 중간 산출물은 새 실행이 덮어쓴다.
- 새 계획 생성 대상이 없으면:
  - `planner_status='NEEDS_ARCHIVE_TARGET'`로 저장한다.
  - Planner를 실행하지 않으므로 `planner_attempt_count`는 증가시키지 않는다.
  - 마이페이지는 사용자가 교체할 active 계획을 고를 수 있는 상태를 표시한다.
- 다음 학습계획 생성 실패:
  - 기존 계획을 archived 처리하지 않는다.
  - `planner_attempt_count`는 claim 단계에서 이미 증가했으므로 실패 처리에서는 다시 증가시키지 않는다.
  - `planner_last_error`, `planner_locked_until=NULL`, `planner_lease_token=NULL`, `modified_at`을 갱신한다.
  - 생성 오류 저장은 `planner_status='RUNNING' AND planner_lease_token=:planner_lease_token AND planner_locked_until > NOW()` 조건이 맞을 때만 수행하고, 조건이 맞지 않으면 stale 실행 결과로 버린다.
  - finalize에서 archive target이 더 이상 active가 아니면 새 계획을 insert하지 않고 `planner_last_error='stale_archive_target'`로 저장한 뒤 사용자에게 리포트/계획 상태 새로고침을 요구한다.
  - `planner_attempt_count >= weekly_report_config.planner_max_attempts`이면 `planner_status='FAILED'`로 고정한다.
  - 상한 이내이면 `planner_status='NOT_REQUESTED'`로 되돌려 사용자가 다시 "새 계획 만들기"를 누를 수 있게 한다.
  - `FAILED`로 고정된 뒤 수동 재시도 버튼은 `planner_status`를 `FAILED → NOT_REQUESTED`로 되돌리고 `planner_attempt_count=0`, `planner_last_error=NULL`, `planner_locked_until=NULL`, `planner_lease_token=NULL`로 초기화한다.
  - 사용자에게 "리포트는 생성됐지만 새 계획 생성이 필요합니다" 상태를 보여 준다.

## 구현 스택과 실행 정책

- Orchestrator는 LangGraph 기반 상태 그래프로 구현할 수 있다. `requirements.txt`에 이미 `langgraph`가 있으므로 별도 런타임 도입 없이 시작할 수 있다.
- worker는 DB lease와 실행별 token을 잡은 작업만 실행한다.
- `locked_until < now()`인 `RUNNING` 작업은 worker 장애로 간주해 재시도 가능하다.
- 저장 단계는 항상 실행별 token 일치와 lease 만료 시각 조건을 함께 건다. 리포트는 `lease_token` + `locked_until > NOW()`, Planner는 `planner_lease_token` + `planner_locked_until > NOW()` 조건으로 만료된 이전 실행 결과가 새 실행 결과를 덮어쓰지 못하게 한다.
- `attempt_count >= weekly_report_config.max_attempts`이면 새 실행을 시작하지 않고 `FAILED`로 고정한다. 수동 재시도는 `FAILED → PENDING` 전이와 `attempt_count=0` 초기화로 정의한다(Planner는 `FAILED → NOT_REQUESTED`, `planner_attempt_count=0`).
- Planner 경로는 리포트 worker와 별도이므로 `planner_attempt_count`와 `weekly_report_config.planner_max_attempts`를 사용한다.
- Report Writer는 metric 참조 토큰만 생성하고, 숫자 렌더링은 deterministic renderer가 수행한다.

## 설정값

하드코딩을 피하기 위해 다음 값은 설정으로 둔다.

```json
{
  "weekly_report_config": {
    "top_weak_limit": 3,
    "top_improved_limit": 3,
    "max_attempts": 3,
    "planner_max_attempts": 2,
    "max_write_retry": 2,
    "lease_minutes": 10,
    "planner_lease_minutes": 10,
    "improved_review_ratio": 0.2
  }
}
```

## 확정 결정 사항

- 주간평가 세션 식별 방식:
  - `solve_sessions`에 `review_type` 필드를 추가한다.
  - 기존 `session_type='diagnostic'`은 유지한다.
  - 주간평가 세션은 `session_type='diagnostic'`, `review_type='weekly_review'`로 식별한다.
  - 일반 진단평가는 `session_type='diagnostic'`, `review_type IS NULL`로 둔다.
  - record의 `studyplan_id`, `study_plan_block_id`는 블록 연결과 진행률 계산에 사용하고, 세션 식별의 1차 기준으로 쓰지 않는다.
  - 이유: 기존 진단평가 집계 호환성을 유지하면서도 "이 세션이 주간평가인가"를 record 조인 없이 판단할 수 있다.
- 블록 자동 완료와 수동 완료 버튼 경합:
  - `complete_study_plan_block_by_id()`는 이미 완료된 블록에 다시 호출돼도 같은 완료 상태를 유지해야 한다.
  - 완료 시각 갱신 여부는 정책으로 고정한다. 권장값은 최초 완료 시각 보존이다.
- 스키마 세부 사항:
  - 리포트 원천은 주간평가 세션이므로 `session_id`는 `NOT NULL`을 권장한다.
  - 시간 필드는 worker lease와 장애 복구 기준이 되므로 `TIMESTAMPTZ`를 사용한다.
  - JSON 임시 저장은 worker·lease 없이 동기 실행하는 단일 프로세스에서만 쓰고, worker를 붙이면 unique 제약이 있는 별도 DB 테이블로 전환한다.

### solve_sessions 변경

```sql
ALTER TABLE solve_sessions
    ADD COLUMN review_type VARCHAR(30) NULL;

ALTER TABLE solve_sessions
    ADD CONSTRAINT solve_sessions_review_type_check
    CHECK (review_type IS NULL OR review_type IN ('weekly_review'));

CREATE INDEX solve_sessions_weekly_review_idx
    ON solve_sessions(user_id, recorded_date, session_id)
    WHERE session_type = 'diagnostic'
      AND review_type = 'weekly_review'
      AND status = 'completed';
```

## 외부 앱 변경 요청서

### diagnosis 앱 요청

주간평가 시작 요청에서 `studyplan_id`, `study_plan_block_id`를 받아야 한다.

현재 나의 학습실은 주간평가 시작 시 `/diagnosis/api/start/`로 다음 값을 보낸다.

```json
{
  "studyplan_id": 1,
  "study_plan_block_id": "block-uuid"
}
```

요청사항:

- `DiagnosisStartRequestSerializer`에 두 필드를 추가한다.
- `diagnosis_start`에서 `studyplan_id`, `study_plan_block_id`가 들어온 요청은 주간평가로 판단한다.
- 주간평가 세션은 `SolveSessions.session_type='diagnostic'`, `SolveSessions.review_type='weekly_review'`로 저장한다.
- 일반 진단평가 세션은 `SolveSessions.session_type='diagnostic'`, `SolveSessions.review_type=NULL`로 저장한다.
- 세션 생성 후 생성되는 `SolveRecords`에는 `studyplan_id`, `study_plan_block_id`를 저장한다.
- 주간평가 여부 판단은 세션의 `review_type`을 1차 기준으로 사용하고, record 연결값은 블록 연결/진행률 계산에 사용한다.

### diagnosis 제출 API 요청

요청사항:

- `diagnosis_submit` 완료 후 같은 트랜잭션에서 주간 리포트 작업을 `PENDING`으로 enqueue한다.
- enqueue 조건:
  - 해당 세션의 `review_type='weekly_review'`
  - 세션 상태가 `completed`
- 해당 세션의 record 중 `studyplan_id`, `study_plan_block_id`가 둘 다 있으면 `analytics.service.studyplan.complete_study_plan_block_by_id()`를 호출한다.
- record 연결값이 없으면 enqueue는 유지하되 `source_studyplan_id=NULL`로 저장하고 블록 완료 처리는 건너뛴다.
- 이후 AI 리포트 생성은 analytics 백그라운드 오케스트레이터가 담당한다.
- 제출 API는 Collector, Analyst, Writer, Planner를 직접 실행하지 않는다.
- 제출 API는 기존 active 계획을 archived 처리하지 않는다.
- enqueue는 `(user_id, session_id)` 기준 `ON CONFLICT DO NOTHING`으로 처리한다.
- worker 알림은 제출 트랜잭션 커밋 후에만 발생하도록 `transaction.on_commit()`에 등록한다. 커밋 전 알림은 worker가 미커밋/롤백 데이터를 잡게 만든다.

### question 앱 요청

일반 학습계획 문제풀이 쪽은 이미 `studyplan_id`, `study_plan_block_id`를 저장하는 흐름이 있으므로 유지한다.

추가 요청:

- 학습계획 문제풀이 완료 후 analytics 이벤트 훅을 하나로 정리한다.
- 주간평가와 일반 학습 블록 완료 처리 로직이 같은 서비스 함수를 타도록 맞춘다.
- 완료 처리 함수는 멱등성을 보장한다. 이미 완료된 블록을 다시 완료 처리해도 중복 기록이나 잘못된 진행률 변화가 없어야 한다.

## 현재 analytics 내부 보완 정책

외부 앱 변경 전까지는 analytics에서 다음 fallback을 유지한다.

- 주간평가 비교 대상은 완료된 `review_type='weekly_review'` 세션 중 최신 1건을 사용한다.
- 최신 기준은 `recorded_date DESC, session_id DESC`이다.
- `review_type` 도입 전 데이터는 완료된 diagnostic 세션 중 `studyplan_id`, `study_plan_block_id`가 연결된 record가 있는 최신 1건을 임시 fallback으로 사용한다.
- 비교 기준 진단평가는 해당 주간평가보다 이전에 완료된 일반 진단평가(`session_type='diagnostic'`, `review_type IS NULL`) 중 가장 최근 1건을 사용한다.
- 이전 일반 진단평가가 없으면 기준 세션을 임의로 고르지 않고 `analysis_result.comparison.status='INSUFFICIENT_BASELINE'`으로 남긴다.
- 기준 진단평가와 주간평가 후보가 여러 개일 때는 항상 `recorded_date`, `session_id` 정렬로 하나만 선택한다.
- 과거 미완료 학습계획 블록은 나의 학습실 진입 시 오늘 날짜로 이월한다.
- 오늘 학습계획이 없고 같은 날 이미 생성/수정된 빈 계획이 아니라면 새 학습계획을 자동 생성한다.
