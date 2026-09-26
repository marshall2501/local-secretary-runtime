\set ON_ERROR_STOP on
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM secretary.tasks
        WHERE id='00000000-0000-4000-8000-000000000001' AND status='waiting_approval'
          AND checkpoint='{"next_step":"diagnose"}'::jsonb AND next_run_at='2030-01-01'::timestamptz) THEN
        RAISE EXCEPTION 'Persistent task checkpoint lost';
    END IF;
    IF (SELECT count(*) FROM secretary.schema_migrations) <> 2 THEN
        RAISE EXCEPTION 'Expected two migrations';
    END IF;
END $$;
