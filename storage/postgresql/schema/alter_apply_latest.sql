-- Apply this file once after pulling the latest question/solve schema changes.
-- It is safe to run multiple times.

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

-- solve_records: store per-question time in milliseconds only.
ALTER TABLE solve_records
    ADD COLUMN IF NOT EXISTS time_spent_ms INT NULL;

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
