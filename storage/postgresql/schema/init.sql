CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS unaccent;

CREATE SCHEMA IF NOT EXISTS rag;

-- =============================================
-- 전체 테이블 생성 (public 스키마)
-- FK 의존성 순서: user_accounts, questions 먼저
-- =============================================

-- 1. 사용자 계정
CREATE TABLE IF NOT EXISTS user_accounts (
    user_id         BIGSERIAL       PRIMARY KEY,
    email           VARCHAR(255)    NOT NULL UNIQUE,
    password_hash   VARCHAR(255)    NOT NULL,
    nickname        VARCHAR(30)     NOT NULL,
    login_fail_count INT            NOT NULL DEFAULT 0,
    is_locked       BOOLEAN         NOT NULL DEFAULT FALSE,
    locked_at       TIMESTAMP       NULL,
    status          VARCHAR(20)     NOT NULL DEFAULT 'active',
    created_at      TIMESTAMP       NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMP       NOT NULL DEFAULT NOW(),
    deleted_at      TIMESTAMP       NULL,
    last_login      TIMESTAMP       NULL
);

-- 2. 문제
CREATE TABLE IF NOT EXISTS questions (
    question_id         BIGSERIAL       PRIMARY KEY,
    exam_round          INT             NULL,               -- 시험 회차
    exam_level          VARCHAR(20)     NULL,               -- 시험 등급 (심화/기본)
    question_no         INT             NULL,               -- 회차 내 문항 번호
    q_score             INT             NOT NULL,           -- 배점
    era                 VARCHAR(50)     NOT NULL,           -- 시대
    topic               VARCHAR(50)     NOT NULL,           -- 주제
    question_type       VARCHAR(50)     NOT NULL,           -- 대유형
    question_subtype    VARCHAR(50)     NOT NULL DEFAULT U&'\BBF8\BD84\B958', -- 소유형
    content             TEXT            NOT NULL,           -- 발문
    passage             TEXT            NULL,               -- 자료/지문
    visual_note         TEXT            NULL,               -- 이미지/도표 설명
    question_image_path TEXT            NULL,               -- 문항 이미지 경로
    parse_status        VARCHAR(20)     NULL,               -- 파싱 상태
    answer_no           INT             NOT NULL,           -- 정답 번호
    answer_explanation  TEXT            NOT NULL,           -- 정답 해설
    core_concept        VARCHAR(255)    NOT NULL,           -- 핵심 개념
    UNIQUE (exam_round, exam_level, question_no)
);

-- 3. 문제 선택지
CREATE TABLE IF NOT EXISTS question_options (
    choice_id           BIGSERIAL       PRIMARY KEY,
    question_id         BIGINT          NOT NULL REFERENCES questions(question_id) ON DELETE CASCADE,
    choice_no           INT             NOT NULL,           -- 보기 번호 (1~5)
    content             TEXT            NOT NULL,           -- 보기 내용
    is_answer           BOOLEAN         NOT NULL DEFAULT FALSE,  -- 정답 여부
    choice_explanation  TEXT            NULL                -- 선지별 오답 해설
);

-- 4. 챗봇 세션
CREATE TABLE IF NOT EXISTS chat_sessions (
    session_id  VARCHAR(50)     PRIMARY KEY,
    chat_type   VARCHAR(20)     NOT NULL,
    turn_count  INT             NOT NULL DEFAULT 0,
    status      VARCHAR(20)     NOT NULL DEFAULT 'active',
    created_at  TIMESTAMP       NOT NULL DEFAULT NOW(),
    user_id     BIGINT          NOT NULL REFERENCES user_accounts(user_id) ON DELETE CASCADE
);

-- 5. 챗봇 메시지
CREATE TABLE IF NOT EXISTS chat_messages (
    message_id  BIGSERIAL       PRIMARY KEY,
    session_id  VARCHAR(50)     NOT NULL REFERENCES chat_sessions(session_id) ON DELETE CASCADE,
    sender_type VARCHAR(10)     NOT NULL,                  -- 'user' | 'ai'
    content     TEXT            NOT NULL,
    used_tokens INT             NULL,
    created_at  TIMESTAMP       NOT NULL DEFAULT NOW()
);

-- 6. 풀이 세션 (시험 전체 단위)
CREATE TABLE IF NOT EXISTS solve_sessions (
    session_id      BIGSERIAL       PRIMARY KEY,
    user_id         BIGINT          NOT NULL REFERENCES user_accounts(user_id) ON DELETE CASCADE,
    session_type    VARCHAR(20)     NOT NULL,               -- 'diagnostic' | 'practice' | 'today'
    total_count     INT             NOT NULL,               -- 총 문제 수
    elapsed_sec     INT             NULL,                   -- 총 소요 시간(초)
    status          VARCHAR(20)     NOT NULL DEFAULT 'in_progress',  -- 'in_progress' | 'completed'
    answer_rate     FLOAT           NULL,                   -- 정답률
    total_score     INT             NULL,                   -- 총 점수
    recorded_date   DATE            NOT NULL DEFAULT CURRENT_DATE  -- 기록 일시
);

-- 7. 문제별 풀이 기록
CREATE TABLE IF NOT EXISTS solve_records (
    record_id       BIGSERIAL       PRIMARY KEY,
    session_id      BIGINT          NOT NULL REFERENCES solve_sessions(session_id) ON DELETE CASCADE,
    question_id     BIGINT          NOT NULL REFERENCES questions(question_id),
    selected_no     INT             NULL,                   -- 사용자 선택 번호 (미응답 시 NULL)
    is_correct      BOOLEAN         NOT NULL DEFAULT FALSE, -- 정답 여부
    time_spent_ms   INT             NULL,                   -- 해당 문제 소요 시간(ms)
    q_type          VARCHAR(20)     NOT NULL,               -- 문제 유형 (통계용 복사)
    topic           VARCHAR(50)     NOT NULL,               -- 주제 (통계용 복사)
    era             VARCHAR(20)     NOT NULL,               -- 시대 (통계용 복사)
    q_score         INT             NOT NULL                -- 배점 (통계용 복사)
);

-- 8. 통계
CREATE TABLE IF NOT EXISTS analytics (
    analytics_id        BIGSERIAL       PRIMARY KEY,
    session_id          BIGINT          NOT NULL REFERENCES solve_sessions(session_id) ON DELETE CASCADE,
    key_concept         VARCHAR(50)     NOT NULL,           -- 예: '조선', '정치', '문화'
    classification      VARCHAR(20)     NOT NULL,           -- '시대' | '주제' | '유형'
    avg_time_sec        INT             NULL,               -- 분류별 평균 풀이 시간(초)
    topic_rate          FLOAT           NOT NULL,           -- 해당 분류 정답률
    date                TIMESTAMP       NOT NULL DEFAULT NOW()  -- 집계 일시
);

-- 9. 오답노트
CREATE TABLE IF NOT EXISTS note_mypage (
    note_id             BIGSERIAL       PRIMARY KEY,
    user_id             BIGINT          NOT NULL REFERENCES user_accounts(user_id) ON DELETE CASCADE,
    created_at          TIMESTAMP       NOT NULL DEFAULT NOW(),
    modified_at         TIMESTAMP       NOT NULL DEFAULT NOW(),
    title               VARCHAR(50)     NOT NULL,
    era                 VARCHAR(50)     NULL,
    topic               VARCHAR(50)     NULL,
    difficulty          VARCHAR(50)     NULL,
    question_type       VARCHAR(20)     NULL,
    content             TEXT            NOT NULL,           -- 문제 내용
    answer_no           INT             NULL,               -- 정답 번호
    answer_explanation  TEXT            NULL                -- 정답 해설
);

-- 10. 학습 계획
CREATE TABLE IF NOT EXISTS study_plan_mypage (
    studyplan_id        BIGSERIAL       PRIMARY KEY,
    user_id             BIGINT          NOT NULL REFERENCES user_accounts(user_id) ON DELETE CASCADE,
    study_plans         TEXT            NULL,               -- 학습/습관 목표
    study_plan_items    TEXT            NULL,               -- 날짜별 학습 목록 (JSON 형태 권장)
    created_at          TIMESTAMP       NOT NULL DEFAULT NOW(),
    modified_at         TIMESTAMP       NOT NULL DEFAULT NOW()
);

-- 11. 이메일 인증 코드 테이블
CREATE TABLE IF NOT EXISTS email_verification_codes (
    id          BIGSERIAL       PRIMARY KEY,
    email       VARCHAR(255)    NOT NULL,
    code        VARCHAR(6)      NOT NULL,
    purpose     VARCHAR(20)     NOT NULL DEFAULT 'register',
    is_used     BOOLEAN         NOT NULL DEFAULT FALSE,
    expires_at  TIMESTAMP       NOT NULL,
    created_at  TIMESTAMP       NOT NULL DEFAULT NOW(),
    used_at     TIMESTAMP       NULL
);

-- 12. 사용자 학습 프로필 테이블
CREATE TABLE IF NOT EXISTS user_study_profiles (
    user_id                 BIGINT          PRIMARY KEY REFERENCES user_accounts(user_id) ON DELETE CASCADE,
    daily_available_hours   DECIMAL(3,1)    NOT NULL DEFAULT 1.0,
    exam_date               DATE            NULL,
    created_at              TIMESTAMP       NOT NULL DEFAULT NOW(),
    updated_at              TIMESTAMP       NOT NULL DEFAULT NOW()
);
