\set ON_ERROR_STOP on
DO $persistence$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM secretary.tasks
        WHERE id='00000000-0000-4000-8000-000000000001' AND status='waiting_approval'
          AND checkpoint='{"next_step":"diagnose"}'::jsonb AND next_run_at='2030-01-01'::timestamptz) THEN
        RAISE EXCEPTION 'Persistent task checkpoint lost';
    END IF;
    IF EXISTS (
        SELECT 1 FROM (
            VALUES ('001_memory.sql'), ('002_task_core.sql'), ('003_memory_review.sql')
        ) AS expected(version)
        WHERE NOT EXISTS (
            SELECT 1 FROM secretary.schema_migrations m
            WHERE m.version=expected.version
        )
    ) THEN
        RAISE EXCEPTION 'Expected memory, task and review migrations';
    END IF;
END $persistence$;
