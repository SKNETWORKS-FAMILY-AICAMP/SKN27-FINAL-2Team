ALTER TABLE questions
ADD COLUMN IF NOT EXISTS exam_round INT NULL;

ALTER TABLE questions
ADD COLUMN IF NOT EXISTS exam_level VARCHAR(20) NULL;

ALTER TABLE questions
ADD COLUMN IF NOT EXISTS question_no INT NULL;

ALTER TABLE questions
ADD COLUMN IF NOT EXISTS passage TEXT NULL;

ALTER TABLE questions
ADD COLUMN IF NOT EXISTS visual_note TEXT NULL;

ALTER TABLE questions
ADD COLUMN IF NOT EXISTS question_image_path TEXT NULL;

ALTER TABLE questions
ADD COLUMN IF NOT EXISTS parse_status VARCHAR(20) NULL;

ALTER TABLE questions
ADD COLUMN IF NOT EXISTS question_subtype VARCHAR(50) NOT NULL DEFAULT U&'\BBF8\BD84\B958';

ALTER TABLE questions
ALTER COLUMN question_type TYPE VARCHAR(50);

CREATE UNIQUE INDEX IF NOT EXISTS questions_exam_round_exam_level_question_no_uidx
ON questions (exam_round, exam_level, question_no);
