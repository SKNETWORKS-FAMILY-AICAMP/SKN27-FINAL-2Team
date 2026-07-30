CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS unaccent;

CREATE SCHEMA IF NOT EXISTS rag;

-- =============================================
-- 초기 PostgreSQL 스키마 생성 파일
-- 새 DB를 처음 만들 때 실행되는 기준 스키마입니다.
--
-- 이미 만들어진 기존 DB에 변경된 컬럼만 반영할 때는
-- 이 파일이 아니라 storage/postgresql/schema/alter_apply_latest.sql 을 실행하세요.
--
-- 외래키 참조 순서 때문에 user_accounts, questions 계열 테이블을 먼저 생성합니다.
-- =============================================

-- 1. 사용자 계정
CREATE TABLE IF NOT EXISTS user_accounts (
    user_id          BIGSERIAL       PRIMARY KEY,
    email            VARCHAR(255)    NOT NULL UNIQUE,
    password_hash    VARCHAR(255)    NULL,
    nickname         VARCHAR(30)     NOT NULL,
    provider         VARCHAR(20)     NULL,
    provider_id      VARCHAR(255)    NULL,
    login_fail_count INT             NOT NULL DEFAULT 0,
    is_locked        BOOLEAN         NOT NULL DEFAULT FALSE,
    locked_at        TIMESTAMPTZ     NULL,
    status           VARCHAR(20)     NOT NULL DEFAULT 'active',
    created_at       TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    updated_at       TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    deleted_at       TIMESTAMPTZ     NULL,
    last_login       TIMESTAMPTZ     NULL,
    daily_available_hours   DECIMAL(3,1)    NOT NULL DEFAULT 1.0,
    exam_date               DATE            NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS user_accounts_provider_uidx
    ON user_accounts(provider, provider_id)
    WHERE provider IS NOT NULL;

-- 2. 문제 본문
-- 문제 생성/풀이 화면에서 사용하는 문항 단위 데이터입니다.
-- 현재 테스트 데이터는 이미지 파일을 직접 보여주지 않고,
-- 이미지 지문과 이미지 선택지를 텍스트 캡션으로 바꿔 저장합니다.
CREATE TABLE IF NOT EXISTS questions (
    question_id         BIGSERIAL       PRIMARY KEY,
    source_key          TEXT            NULL,       -- 생성 문항 variant_key
    question_no         INT             NULL,       -- 원본 시험지 문항 번호 또는 표시용 번호
    q_score             INT             NOT NULL,   -- 배점: 하 1점, 중 2점, 상 3점
    era                 VARCHAR(50)     NOT NULL,   -- 시대: 고려, 조선 전기, 일제 강점기 등
    topic               VARCHAR(50)     NOT NULL,   -- 주제: 정치, 경제, 문화, 인물 등
    question_type       VARCHAR(50)     NOT NULL,   -- 대유형
    question_subtype    VARCHAR(50)     NOT NULL DEFAULT U&'\BBF8\BD84\B958', -- 소유형 기본값: 미분류
    content             TEXT            NOT NULL,   -- 문제 발문
    passage             TEXT            NULL,       -- 텍스트 지문 또는 이미지 지문을 캡션으로 변환한 내용
    image_caption       TEXT            NULL,       -- 이미지 핵심 단서, 키워드, 시대 추론에 필요한 시각 정보
    question_image_path TEXT            NULL,       -- 실제 지문 이미지를 사용할 때의 경로. 현재 테스트 데이터는 비워 둠
    answer_no           INT             NOT NULL,   -- 정답 선택지 번호
    answer_explanation  TEXT            NOT NULL,   -- 정답 및 오답 해설
    core_concept        VARCHAR(255)    NOT NULL    -- 핵심 개념
);

CREATE UNIQUE INDEX IF NOT EXISTS questions_source_key_uidx
    ON questions(source_key);

-- 3. 문제 선택지
-- 한 문제의 1~5번 선택지를 저장합니다.
-- 이미지 선택지도 현재 테스트 데이터에서는 content에 텍스트 설명으로 저장하고,
-- choice_image_path는 나중에 실제 이미지 선택지를 쓸 경우를 위해 남겨 둡니다.
CREATE TABLE IF NOT EXISTS question_options (
    choice_id           BIGSERIAL       PRIMARY KEY,
    question_id         BIGINT          NOT NULL REFERENCES questions(question_id) ON DELETE CASCADE,
    choice_no           INT             NOT NULL,               -- 선택지 번호 (1~5)
    content             TEXT            NOT NULL,               -- 선택지 내용 또는 이미지 선택지의 텍스트 설명
    choice_image_path   TEXT            NULL,                   -- 선택지 이미지 경로. 현재 테스트 데이터는 비워 둠
    is_answer           BOOLEAN         NOT NULL DEFAULT FALSE, -- 정답 여부
    choice_explanation  TEXT            NULL                    -- 선택지별 해설 또는 오답 해설
);

-- 4. 챗봇 세션
-- 3-1. 기출 전처리 원장
-- 초기 적재는 ML_han_v1.json 전처리 데이터를 사용하고,
-- 이후에는 기출 원본에서 다시 추출한 데이터를 같은 구조로 적재합니다.
CREATE TABLE IF NOT EXISTS exam_data (
    id                         BIGSERIAL       PRIMARY KEY,
    round_no                   INT             NOT NULL,
    question_no                INT             NOT NULL,
    question_text              TEXT            NOT NULL,
    material_text              TEXT            NULL,
    choices_json               JSONB           NOT NULL DEFAULT '[]'::jsonb,
    distractor_choices_json    JSONB           NOT NULL DEFAULT '[]'::jsonb,
    answer_choice              TEXT            NULL,
    answer_no                  INT             NULL,
    era                        VARCHAR(50)     NULL,
    topic                      VARCHAR(50)     NULL,
    question_type              VARCHAR(50)     NULL,
    question_subtype           VARCHAR(50)     NULL,
    q_score                    INT             NULL,
    has_image                  BOOLEAN         NOT NULL DEFAULT FALSE,
    image_meta_json            JSONB           NOT NULL DEFAULT '{}'::jsonb,
    created_at                 TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    updated_at                 TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    answer_explanation         TEXT            NULL,
    choice_explanations_json   JSONB           NOT NULL DEFAULT '{}'::jsonb,
    explanation_source         VARCHAR(50)     NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS exam_data_round_question_uidx
    ON exam_data(round_no, question_no);

CREATE INDEX IF NOT EXISTS exam_data_classification_idx
    ON exam_data(era, topic, question_type, question_subtype);

CREATE TABLE IF NOT EXISTS chat_sessions (
    session_id  VARCHAR(50)     PRIMARY KEY,
    chat_type   VARCHAR(20)     NOT NULL,
    turn_count  INT             NOT NULL DEFAULT 0,
    status      VARCHAR(20)     NOT NULL DEFAULT 'active',
    created_at  TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    user_id     BIGINT          NOT NULL REFERENCES user_accounts(user_id) ON DELETE CASCADE
);

-- 5. 챗봇 메시지
CREATE TABLE IF NOT EXISTS chat_messages (
    message_id  BIGSERIAL       PRIMARY KEY,
    session_id  VARCHAR(50)     NOT NULL REFERENCES chat_sessions(session_id) ON DELETE CASCADE,
    sender_type VARCHAR(10)     NOT NULL,                  -- 'user' | 'ai'
    content     TEXT            NOT NULL,
    used_tokens INT             NULL,
    created_at  TIMESTAMPTZ     NOT NULL DEFAULT NOW()
);

-- 6. 풀이 세션
-- 사용자가 한 번 생성한 시험/문제풀이 묶음입니다.
-- solve_records가 이 세션 아래에서 문항별 풀이 기록으로 연결됩니다.
CREATE TABLE IF NOT EXISTS solve_sessions (
    session_id      BIGSERIAL       PRIMARY KEY,
    user_id         BIGINT          NOT NULL REFERENCES user_accounts(user_id) ON DELETE CASCADE,
    session_type    VARCHAR(20)     NOT NULL,              -- 'diagnostic' | 'practice' | 'today'
    total_count     INT             NOT NULL,              -- 세션 전체 문항 수
    elapsed_sec     INT             NULL,                  -- 세션 전체 경과 시간(초)
    status          VARCHAR(20)     NOT NULL DEFAULT 'in_progress', -- 'in_progress' | 'completed'
    answer_rate     FLOAT           NULL,                  -- 정답률
    total_score     INT             NULL,                  -- 총점
    recorded_date   DATE            NOT NULL DEFAULT CURRENT_DATE, -- 저장/풀이 기록 날짜
    review_type     VARCHAR(20)     NULL,
    CONSTRAINT solve_sessions_review_type_check
        CHECK (
            review_type IS NULL
            OR review_type IN ('weekly_review')
        )
);

CREATE INDEX IF NOT EXISTS solve_sessions_weekly_review_idx
    ON solve_sessions(user_id, recorded_date, session_id)
    WHERE session_type = 'diagnostic'
      AND review_type = 'weekly_review'
      AND status = 'completed';

-- 7. 문항별 풀이 기록
-- solve_sessions에 포함된 각 문제의 선택 답안과 풀이 시간을 저장합니다.
-- 이어 풀기, 오답노트, 마이페이지 통계에서 사용할 수 있습니다.
CREATE TABLE IF NOT EXISTS solve_records (
    record_id       BIGSERIAL       PRIMARY KEY,
    session_id      BIGINT          NOT NULL REFERENCES solve_sessions(session_id) ON DELETE CASCADE,
    question_id     BIGINT          NOT NULL REFERENCES questions(question_id),
    selected_no     INT             NULL,                  -- 사용자가 선택한 번호. 미응답이면 NULL
    is_correct      BOOLEAN         NOT NULL DEFAULT FALSE, -- 정답 여부
    time_spent_ms   INT             NULL,                  -- 해당 문제 풀이 시간(ms)
    is_saved        BOOLEAN         NOT NULL DEFAULT FALSE, -- 사용자가 노트에 별도로 저장한 문제인지 여부
    saved_at        TIMESTAMPTZ     NULL,                  -- 노트 저장 시각. 저장하지 않은 문제는 NULL
    studyplan_id    BIGINT          NULL,                  -- 학습계획에서 시작한 풀이일 때 연결되는 study_plan_mypage ID
    study_plan_block_id VARCHAR(36) NULL,                  -- 학습계획 JSON 블록(blockId)과 연결되는 값
    q_type          VARCHAR(20)     NOT NULL,              -- 문제 대유형 스냅샷
    topic           VARCHAR(50)     NOT NULL,              -- 주제 스냅샷
    era             VARCHAR(20)     NOT NULL,              -- 시대 스냅샷
    q_score         INT             NOT NULL               -- 배점 스냅샷
);

-- 8. 풀이 통계
CREATE TABLE IF NOT EXISTS analytics (
    analytics_id        BIGSERIAL       PRIMARY KEY,
    session_id          BIGINT          NULL REFERENCES solve_sessions(session_id) ON DELETE CASCADE,
    user_id             BIGINT          NOT NULL REFERENCES user_accounts(user_id) ON DELETE CASCADE,
    analysis_scope      VARCHAR(30)     NOT NULL,          -- session | study_plan_base | study_plan_result
    analysis_run_id     VARCHAR(36)     NOT NULL,          -- 같은 분석 실행 묶음
    analysis_unit       VARCHAR(30)     NOT NULL,          -- overall | era | type | topic
    studyplan_id        BIGINT          NULL,
    key_concept         VARCHAR(50)     NOT NULL,          -- 예: 조선, 정치, 문화
    classification      VARCHAR(20)     NOT NULL,          -- 시대 | 주제 | 유형
    avg_time_sec        INT             NULL,              -- 분류별 평균 풀이 시간(초)
    topic_rate          FLOAT           NOT NULL,          -- 해당 분류 정답률
    total_count         INT             NOT NULL DEFAULT 0,
    correct_count       INT             NOT NULL DEFAULT 0,
    wrong_count         INT             NOT NULL DEFAULT 0,
    answer_rate         DOUBLE PRECISION NOT NULL DEFAULT 0,
    wrong_rate          DOUBLE PRECISION NOT NULL DEFAULT 0,
    period_start        DATE            NULL,
    period_end          DATE            NULL,
    created_at          TIMESTAMPTZ     NOT NULL DEFAULT NOW()
);

-- 9. 오답노트
CREATE TABLE IF NOT EXISTS note_mypage (
    note_id             BIGSERIAL       PRIMARY KEY,
    user_id             BIGINT          NOT NULL REFERENCES user_accounts(user_id) ON DELETE CASCADE,
    created_at          TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    modified_at         TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    title               VARCHAR(50)     NOT NULL,
    era                 VARCHAR(50)     NULL,
    topic               VARCHAR(50)     NULL,
    difficulty          VARCHAR(50)     NULL,
    question_type       VARCHAR(20)     NULL,
    content             TEXT            NOT NULL,          -- 문제 내용
    answer_no           INT             NULL,              -- 정답 번호
    answer_explanation  TEXT            NULL               -- 정답 해설
);

-- 10. 학습 계획
CREATE TABLE IF NOT EXISTS study_plan_mypage (
    studyplan_id        BIGSERIAL       PRIMARY KEY,
    user_id             BIGINT          NOT NULL REFERENCES user_accounts(user_id) ON DELETE CASCADE,
    study_plans         TEXT            NULL,              -- 학습/통계 목표
    study_plan_items    TEXT            NULL,              -- 날짜별 학습 목록. JSON 형태 권장
    created_at          TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    modified_at         TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    status              VARCHAR(20)     NOT NULL DEFAULT 'active', -- 계획 상태: active | archived | deleted
    plan_version        INT             NOT NULL DEFAULT 1,        -- 사용자별 계획 버전
    start_date          DATE            NULL,                      -- 계획 시작일
    end_date            DATE            NULL,                      -- 계획 종료일
    completion_rate     DOUBLE PRECISION NOT NULL DEFAULT 0,       -- 완료율
    archived_at         TIMESTAMPTZ     NULL,                      -- 과거 계획 처리 시각
    deleted_at          TIMESTAMPTZ     NULL,                      -- 삭제 처리 시각
    weekly_report_data  JSONB           NULL,
    CONSTRAINT study_plan_mypage_weekly_report_data_object_check
        CHECK (
            weekly_report_data IS NULL
            OR COALESCE(
                jsonb_typeof(weekly_report_data) = 'object',
                FALSE
            )
        )
);

CREATE UNIQUE INDEX IF NOT EXISTS study_plan_mypage_user_active_uidx
    ON study_plan_mypage(user_id)
    WHERE status = 'active';

-- 11. 이메일 인증 코드
CREATE TABLE IF NOT EXISTS email_verification_codes (
    id          BIGSERIAL       PRIMARY KEY,
    email       VARCHAR(255)    NOT NULL,
    code        VARCHAR(128)    NOT NULL,
    purpose     VARCHAR(20)     NOT NULL DEFAULT 'register',
    is_used     BOOLEAN         NOT NULL DEFAULT FALSE,
    attempt_count SMALLINT      NOT NULL DEFAULT 0,
    expires_at  TIMESTAMPTZ     NOT NULL,
    created_at  TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    used_at     TIMESTAMPTZ     NULL
);

-- 12. ML trend TOP5 data
-- Stores recent trend TOP5 rows used by study-plan and question-generation flows.
-- Import source: ai/ml/reports/trend_top5_for_db_2026-07-18.csv
CREATE TABLE IF NOT EXISTS ml_trend_top5 (
    trend_id               BIGSERIAL       PRIMARY KEY,
    target_round           INT             NOT NULL,
    recent5_rounds         VARCHAR(20)     NOT NULL,
    source                 VARCHAR(50)     NOT NULL,
    source_name            VARCHAR(100)    NULL,
    usage_text             VARCHAR(255)    NULL,
    trend_type             VARCHAR(50)     NOT NULL,
    rank_no                INT             NOT NULL,
    era                    VARCHAR(50)     NULL,
    topic_train            VARCHAR(50)     NULL,
    topic                  VARCHAR(50)     NULL,
    topic_summary          VARCHAR(255)    NULL,
    label                  VARCHAR(100)    NULL,
    combo_label            VARCHAR(100)    NULL,
    combo_label_with_topic VARCHAR(255)    NULL,
    count_value            INT             NOT NULL,
    ratio                  DOUBLE PRECISION NULL,
    ratio_percent          DOUBLE PRECISION NULL,
    created_at             TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    CONSTRAINT ml_trend_top5_source_check
        CHECK (source IN ('recent5_actual', 'predicted', 'actual')),
    CONSTRAINT ml_trend_top5_trend_type_check
        CHECK (trend_type IN ('era_topic_train', 'era', 'topic_train', 'topic')),
    CONSTRAINT ml_trend_top5_rank_check
        CHECK (rank_no BETWEEN 1 AND 5)
);

CREATE UNIQUE INDEX IF NOT EXISTS ml_trend_top5_round_source_type_rank_uidx
    ON ml_trend_top5(target_round, source, trend_type, rank_no);

CREATE INDEX IF NOT EXISTS ml_trend_top5_lookup_idx
    ON ml_trend_top5(target_round, source, trend_type);
