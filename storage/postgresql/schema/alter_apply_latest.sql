-- Apply this file once after pulling the latest question/solve schema changes.
-- It is safe to run multiple times.

-- exam_data: source table for preprocessed past exam question data.
-- Initial import source is ML_han_v1.json; later imports should use data extracted
-- again from original past exam files in the same table shape.
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
    created_at                 TIMESTAMP       NOT NULL DEFAULT NOW(),
    updated_at                 TIMESTAMP       NOT NULL DEFAULT NOW(),
    answer_explanation         TEXT            NULL,
    choice_explanations_json   JSONB           NOT NULL DEFAULT '{}'::jsonb,
    explanation_source         VARCHAR(50)     NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS exam_data_round_question_uidx
    ON exam_data(round_no, question_no);

CREATE INDEX IF NOT EXISTS exam_data_classification_idx
    ON exam_data(era, topic, question_type, question_subtype);

-- questions: columns used by question filtering and exam rendering.
ALTER TABLE questions
    ADD COLUMN IF NOT EXISTS question_no INT NULL;

ALTER TABLE questions
    ADD COLUMN IF NOT EXISTS passage TEXT NULL;

ALTER TABLE questions
    ADD COLUMN IF NOT EXISTS image_caption TEXT NULL;

ALTER TABLE questions
    ADD COLUMN IF NOT EXISTS question_image_path TEXT NULL;

ALTER TABLE questions
    ADD COLUMN IF NOT EXISTS question_subtype VARCHAR(50) NOT NULL DEFAULT U&'\BBF8\BD84\B958';

ALTER TABLE questions
    ALTER COLUMN question_type TYPE VARCHAR(50);

-- question_options: store optional image choices for visual multiple-choice questions.
ALTER TABLE question_options
    ADD COLUMN IF NOT EXISTS choice_image_path TEXT NULL;

-- questions: remove unused legacy parsing/exam metadata columns.
DROP INDEX IF EXISTS questions_exam_round_exam_level_question_no_uidx;

ALTER TABLE questions
    DROP COLUMN IF EXISTS exam_round,
    DROP COLUMN IF EXISTS exam_level,
    DROP COLUMN IF EXISTS visual_note,
    DROP COLUMN IF EXISTS parse_status;

-- solve_sessions: date used for saved sessions and daily learning features.
ALTER TABLE solve_sessions
    ADD COLUMN IF NOT EXISTS recorded_date DATE NOT NULL DEFAULT CURRENT_DATE;

-- solve_sessions: mark weekly review diagnostic sessions.
ALTER TABLE solve_sessions
    ADD COLUMN IF NOT EXISTS review_type VARCHAR(20) NULL;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
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

CREATE INDEX IF NOT EXISTS solve_sessions_weekly_review_idx
    ON solve_sessions(user_id, recorded_date, session_id)
    WHERE session_type = 'diagnostic'
      AND review_type = 'weekly_review'
      AND status = 'completed';

-- solve_records: store per-question time in milliseconds only.
ALTER TABLE solve_records
    ADD COLUMN IF NOT EXISTS time_spent_ms INT NULL;

-- solve_records: mark questions that the user saved into the note.
ALTER TABLE solve_records
    ADD COLUMN IF NOT EXISTS is_saved BOOLEAN NOT NULL DEFAULT FALSE;

ALTER TABLE solve_records
    ADD COLUMN IF NOT EXISTS saved_at TIMESTAMP NULL;

-- solve_records: 학습계획 블록에서 시작한 풀이 기록을 계획/블록에 직접 연결한다.
ALTER TABLE solve_records
    ADD COLUMN IF NOT EXISTS studyplan_id BIGINT NULL,
    ADD COLUMN IF NOT EXISTS study_plan_block_id VARCHAR(36) NULL;

DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_name = 'solve_records'
          AND column_name = 'time_spent_sec'
    ) THEN
        UPDATE solve_records
        SET time_spent_ms = time_spent_sec * 1000
        WHERE time_spent_ms IS NULL
          AND time_spent_sec IS NOT NULL;

        ALTER TABLE solve_records
            DROP COLUMN time_spent_sec;
    END IF;
END $$;

-- analytics: add analysis metadata columns used by mypage/session/study-plan analytics.
ALTER TABLE analytics
    ALTER COLUMN session_id DROP NOT NULL;

ALTER TABLE analytics
    ADD COLUMN IF NOT EXISTS user_id BIGINT NULL;

UPDATE analytics a
SET user_id = s.user_id
FROM solve_sessions s
WHERE a.session_id = s.session_id
  AND a.user_id IS NULL;

DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'analytics'
          AND column_name = 'date'
    ) AND NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'analytics'
          AND column_name = 'created_at'
    ) THEN
        ALTER TABLE analytics
            RENAME COLUMN date TO created_at;
    END IF;
END $$;

ALTER TABLE analytics
    ADD COLUMN IF NOT EXISTS analysis_scope VARCHAR(30) NOT NULL DEFAULT 'session',
    ADD COLUMN IF NOT EXISTS analysis_run_id VARCHAR(36) NOT NULL DEFAULT 'legacy',
    ADD COLUMN IF NOT EXISTS analysis_unit VARCHAR(30) NOT NULL DEFAULT 'overall',
    ADD COLUMN IF NOT EXISTS studyplan_id BIGINT NULL,
    ADD COLUMN IF NOT EXISTS total_count INT NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS correct_count INT NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS wrong_count INT NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS answer_rate DOUBLE PRECISION NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS wrong_rate DOUBLE PRECISION NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS period_start DATE NULL,
    ADD COLUMN IF NOT EXISTS period_end DATE NULL,
    ADD COLUMN IF NOT EXISTS created_at TIMESTAMP NOT NULL DEFAULT NOW();

UPDATE analytics
SET analysis_unit = CASE classification
    WHEN '시대' THEN 'era'
    WHEN '유형' THEN 'type'
    WHEN '주제' THEN 'topic'
    ELSE analysis_unit
END
WHERE analysis_unit = 'overall';

UPDATE analytics
SET answer_rate = topic_rate
WHERE answer_rate = 0
  AND topic_rate IS NOT NULL;

UPDATE analytics
SET wrong_rate = 1 - answer_rate
WHERE wrong_rate = 0
  AND answer_rate IS NOT NULL;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM analytics WHERE user_id IS NULL
    ) THEN
        ALTER TABLE analytics
            ALTER COLUMN user_id SET NOT NULL;
    END IF;
END $$;

-- study_plan_mypage: 학습계획 상태, 버전, 기간, 완료율, 보관/삭제 시각을 추가한다.
ALTER TABLE study_plan_mypage
    ADD COLUMN IF NOT EXISTS status VARCHAR(20) NOT NULL DEFAULT 'active',
    ADD COLUMN IF NOT EXISTS plan_version INT NOT NULL DEFAULT 1,
    ADD COLUMN IF NOT EXISTS start_date DATE NULL,
    ADD COLUMN IF NOT EXISTS end_date DATE NULL,
    ADD COLUMN IF NOT EXISTS completion_rate DOUBLE PRECISION NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS archived_at TIMESTAMPTZ NULL,
    ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMPTZ NULL,
    ADD COLUMN IF NOT EXISTS weekly_report_data JSONB NULL;

DO $$
BEGIN
    ALTER TABLE study_plan_mypage
        DROP CONSTRAINT IF EXISTS study_plan_mypage_weekly_report_data_object_check;

    ALTER TABLE study_plan_mypage
        ADD CONSTRAINT study_plan_mypage_weekly_report_data_object_check
        CHECK (
            weekly_report_data IS NULL
            OR COALESCE(
                jsonb_typeof(weekly_report_data) = 'object',
                FALSE
            )
        );
END
$$;

CREATE UNIQUE INDEX IF NOT EXISTS study_plan_mypage_user_active_uidx
    ON study_plan_mypage(user_id)
    WHERE status = 'active';

-- user_accounts: 학습 가능 시간과 시험일을 사용자 계정 테이블에 직접 저장한다.
ALTER TABLE user_accounts
    ADD COLUMN IF NOT EXISTS exam_date DATE NULL,
    ADD COLUMN IF NOT EXISTS daily_available_hours DECIMAL(3,1) NOT NULL DEFAULT 1.0;

ALTER TABLE user_accounts
    ALTER COLUMN daily_available_hours TYPE DECIMAL(3,1)
    USING daily_available_hours::DECIMAL(3,1),
    ALTER COLUMN daily_available_hours SET DEFAULT 1.0;

DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM information_schema.tables
        WHERE table_name = 'user_study_profiles'
    ) THEN
        UPDATE user_accounts u
        SET daily_available_hours = COALESCE(p.daily_available_hours, u.daily_available_hours),
            exam_date = COALESCE(p.exam_date, u.exam_date)
        FROM user_study_profiles p
        WHERE u.user_id = p.user_id;

        DROP TABLE user_study_profiles;
    END IF;
END $$;

-- ml_trend_top5: recent trend TOP5 rows for study-plan and question-generation flows.
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
    created_at             TIMESTAMP       NOT NULL DEFAULT NOW(),
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
