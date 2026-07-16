# DB 스키마 변경 요청서 — 학습계획·주간평가 (study_plan_mypage / solve_sessions)

- 요청 근거: `docs/mypage/study_plan/SPEC.md`, `docs/mypage/study_plan/AI_WORKFLOW.md`,
  `docs/mypage/주간평가_협조_요청서.md`
- 변경 요약: **인덱스 2종 + 컬럼 3개 + CHECK 제약 2개** (테이블 신규 생성 없음, 데이터 마이그레이션 없음)
- 적용 파일:
  - `storage/postgresql/schema/init.sql` — 신규 환경용. CREATE TABLE 정의 안에 컬럼·제약을
    직접 반영하고, 인덱스는 테이블 정의 뒤에 추가
  - `storage/postgresql/schema/alter_apply_latest.sql` — 기존 DB용. 아래 구문 그대로 추가
- 전 구문 재실행 안전(멱등)하게 작성했다. 같은 파일을 두 번 실행해도 오류가 없다.

## 애플리케이션 의존성 (적용 순서)

DB 적용이 **선행**이고, 적용 완료 통보 후 앱 배포가 따라간다.

```text
이 요청서 적용 (DB)
  → question 앱: SolveSessions 모델에 컬럼 3개 선언 (managed=False라 모델 선언 별도 필요)
  → diagnosis/question 앱: review_type 저장, 세션 보존·취소 API (협조 요청서 1~4)
  → analytics 앱: 주간평가 식별·이월 분기 구현
```

DB만 먼저 적용돼도 기존 앱 동작에는 영향이 없다 — 추가 컬럼은 전부 NULL 허용이고
기존 쿼리는 새 컬럼을 참조하지 않는다.

---

## 1. `study_plan_mypage` — 사용자별 active 계획 유일성 인덱스

### 목적

문서 전체가 "사용자별 active 학습계획은 정확히 1개"를 전제한다. 마이페이지 진입 시
자동 생성·이월이 연결되면 동시 GET 요청 두 개가 각각 "active 없음 → 생성"을 실행해
active 계획이 2개 생길 수 있다. 앱 레벨 락으로 줄일 수는 있지만 최종 방어선은
DB 제약이어야 한다.

### 사전 확인 (필수)

이미 중복 active가 있으면 인덱스 생성이 실패한다. 먼저 조회:

```sql
SELECT user_id, COUNT(*) AS active_count
FROM study_plan_mypage
WHERE status = 'active'
GROUP BY user_id
HAVING COUNT(*) > 1;
```

중복이 있으면 사용자별로 `plan_version`이 가장 높은 1개만 남기고 archived 처리 후 진행:

```sql
-- 중복 발견 시에만 실행. 사용자별 최신(plan_version 최대, 동률 시 modified_at 최신)
-- 1개를 제외한 active를 archived로 정리한다.
WITH ranked AS (
    SELECT studyplan_id,
           ROW_NUMBER() OVER (
               PARTITION BY user_id
               ORDER BY plan_version DESC, modified_at DESC, studyplan_id DESC
           ) AS rn
    FROM study_plan_mypage
    WHERE status = 'active'
)
UPDATE study_plan_mypage sp
SET status = 'archived',
    archived_at = NOW(),
    modified_at = NOW()
FROM ranked r
WHERE sp.studyplan_id = r.studyplan_id
  AND r.rn > 1;
```

### 적용 구문

```sql
CREATE UNIQUE INDEX IF NOT EXISTS study_plan_mypage_user_active_uidx
    ON study_plan_mypage(user_id)
    WHERE status = 'active';
```

### 주의

- `CREATE INDEX`는 대상 테이블에 쓰기 락을 잡는다. `study_plan_mypage`는 소규모
  테이블이라 순간 적용이 예상되지만, **운영 중 무중단 적용이 필요하면**
  `CREATE UNIQUE INDEX CONCURRENTLY`로 실행한다 (단, CONCURRENTLY는 트랜잭션 블록
  안에서 실행 불가 — alter 스크립트를 트랜잭션으로 감싸는 경우 이 구문만 분리).
- 이 인덱스 적용 후, 앱의 계획 생성 경로는 unique 충돌(23505) 시 새로 생긴 active를
  재조회해 반환하도록 구현된다 (앱 측 책임, `study_plan/SPEC.md` 15장 계획 교체와 동시성).

### 롤백

```sql
DROP INDEX IF EXISTS study_plan_mypage_user_active_uidx;
```

---

## 2. `solve_sessions.review_type` — 주간평가 세션 식별 컬럼

### 목적

주간평가는 진단평가 API로 실행돼 `session_type='diagnostic'`으로 저장되므로,
세션 데이터만으로는 일반 진단평가와 구분할 수 없다. 현재는 `solve_records`의
학습계획 연결값으로 간접 추정하는데(record 조인 필요), 세션 단위 식별자를 둬서
1차 기준으로 쓴다.

### 적용 구문

```sql
ALTER TABLE solve_sessions
    ADD COLUMN IF NOT EXISTS review_type VARCHAR(20) NULL;

-- 허용값 제한. ADD CONSTRAINT는 IF NOT EXISTS 미지원이라 존재 확인 후 추가한다.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'solve_sessions_review_type_check'
    ) THEN
        ALTER TABLE solve_sessions
            ADD CONSTRAINT solve_sessions_review_type_check
            CHECK (
                review_type IS NULL
                OR review_type IN ('weekly_review')
            );
    END IF;
END $$;
```

- 값 정의: `NULL` = 일반 세션(진단·연습·오늘의 학습 모두), `'weekly_review'` = 주간평가.
  현재 다른 값은 없으며, 새 값이 필요해지면 CHECK 목록을 함께 변경한다.
- 기존 데이터는 전부 NULL 유지 — **UPDATE 백필 없음**. 과거 주간평가 세션은 앱이
  `solve_records`의 `studyplan_id`/`study_plan_block_id` 연결값으로 fallback 식별한다.
- NULL 허용 컬럼 추가 + 기존 행 전부 NULL이므로 CHECK 검증은 즉시 통과하고
  테이블 재작성(rewrite)도 발생하지 않는다.

### 조회용 partial index

리포트 파이프라인이 "완료된 주간평가 중 최신 1건"을 반복 조회한다
(`recorded_date DESC, session_id DESC` 정렬). 조건이 전부 고정이라 partial index가 맞다.

```sql
CREATE INDEX IF NOT EXISTS solve_sessions_weekly_review_idx
    ON solve_sessions(user_id, recorded_date, session_id)
    WHERE session_type = 'diagnostic'
      AND review_type = 'weekly_review'
      AND status = 'completed';
```

주간평가 세션은 사용자당 주 1건이라 인덱스 크기는 무시할 수준이다.

### 롤백

```sql
DROP INDEX IF EXISTS solve_sessions_weekly_review_idx;
ALTER TABLE solve_sessions DROP CONSTRAINT IF EXISTS solve_sessions_review_type_check;
ALTER TABLE solve_sessions DROP COLUMN IF EXISTS review_type;
```

---

## 3. `solve_sessions` — 세션 명시적 취소 필드

### 목적

현재 새 세션 시작 시 기존 `in_progress` 세션을 DELETE하는데, 학습계획에 연결된
세션이 지워지면 진행 중 답안이 유실된다. 자동 삭제를 보존 정책으로 바꾸는 대신
사용자가 진행 세션을 버릴 수 있는 명시적 취소 경로를 만들며, DELETE가 아니라
상태 전환으로 처리해 기록을 보존한다.

### status 값 추가에 대해

`solve_sessions.status`에는 CHECK 제약이 없으므로 (init.sql 주석으로만
`'in_progress' | 'completed'` 표기) `'cancelled'` 값 사용 자체는 스키마 변경이
필요 없다. **init.sql의 status 컬럼 주석에 `'cancelled'`를 추가**해 문서화만 맞춰달라.

파급 확인 완료: 통계·취약점 집계 쿼리는 전부 `status='completed'` 필터를 쓰므로
cancelled 세션은 자동 제외된다. 별도 데이터 처리 불필요.

### 적용 구문

```sql
ALTER TABLE solve_sessions
    ADD COLUMN IF NOT EXISTS cancelled_at TIMESTAMP NULL;

ALTER TABLE solve_sessions
    ADD COLUMN IF NOT EXISTS cancellation_reason VARCHAR(20) NULL;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'solve_sessions_cancellation_reason_check'
    ) THEN
        ALTER TABLE solve_sessions
            ADD CONSTRAINT solve_sessions_cancellation_reason_check
            CHECK (
                cancellation_reason IS NULL
                OR cancellation_reason IN ('user_cancel')
            );
    END IF;
END $$;
```

- `cancelled_at` 타입은 기존 solve_* 계열 관례(TIMESTAMP, 예: `solve_records.saved_at`)에
  맞췄다. 스키마 전반을 TIMESTAMPTZ로 통일 중이면 그쪽에 맞춰 변경해도 된다 —
  앱은 Django `DateTimeField`라 어느 쪽이든 동작한다. **결정만 회신해달라**
  (모델 선언은 동일, 값 해석만 다름).
- `cancellation_reason` 허용값은 초기 `'user_cancel'` 1종. 시스템 취소 등 새 사유가
  생기면 CHECK 목록을 함께 변경한다.
- 기존 행 전부 NULL → CHECK 즉시 통과, rewrite 없음.

### 롤백

```sql
ALTER TABLE solve_sessions DROP CONSTRAINT IF EXISTS solve_sessions_cancellation_reason_check;
ALTER TABLE solve_sessions DROP COLUMN IF EXISTS cancellation_reason;
ALTER TABLE solve_sessions DROP COLUMN IF EXISTS cancelled_at;
```

---

## 적용 후 검증 쿼리

```sql
-- 1) 인덱스 2종 존재 확인
SELECT indexname FROM pg_indexes
WHERE indexname IN ('study_plan_mypage_user_active_uidx', 'solve_sessions_weekly_review_idx');

-- 2) 컬럼 3개 존재·타입 확인
SELECT column_name, data_type, is_nullable
FROM information_schema.columns
WHERE table_name = 'solve_sessions'
  AND column_name IN ('review_type', 'cancelled_at', 'cancellation_reason');

-- 3) CHECK 제약 2종 존재 확인
SELECT conname FROM pg_constraint
WHERE conname IN ('solve_sessions_review_type_check', 'solve_sessions_cancellation_reason_check');

-- 4) unique 인덱스 동작 확인 (실패해야 정상 — 테스트 환경에서만)
--    같은 user_id로 status='active' row 2개 INSERT 시도 → 23505 오류 발생 확인

-- 5) 기존 데이터 무영향 확인
SELECT COUNT(*) FROM solve_sessions WHERE review_type IS NOT NULL;   -- 0이어야 정상
SELECT COUNT(*) FROM solve_sessions WHERE cancelled_at IS NOT NULL;  -- 0이어야 정상
```

## 회신 요청 사항

1. 적용 완료 시점 (앱 배포 순서가 이 적용에 걸려 있음)
2. 사전 확인에서 중복 active가 있었는지, 있었다면 정리 건수
3. `cancelled_at` 타입 결정 (TIMESTAMP 유지 vs TIMESTAMPTZ 통일)
4. 운영 DB 무중단 적용 필요 여부 (필요 시 인덱스만 CONCURRENTLY 분리 실행)
