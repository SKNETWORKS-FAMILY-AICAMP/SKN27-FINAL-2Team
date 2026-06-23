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
