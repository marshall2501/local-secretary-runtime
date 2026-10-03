-- Prototype-only least-privilege bridge from the daily GUI to existing Core task tables.
-- Apply only to secretary_pkb_proto_20260927. It does not grant approval decisions
-- or external-action privileges.
BEGIN;

DO $guard$
BEGIN
  IF current_database() <> 'secretary_pkb_proto_20260927' THEN
    RAISE EXCEPTION 'Refusing unexpected database';
  END IF;
  IF to_regclass('secretary.tasks') IS NULL
     OR to_regclass('secretary.actions') IS NULL
     OR to_regclass('secretary.results') IS NULL
     OR to_regclass('secretary.audit_events') IS NULL THEN
    RAISE EXCEPTION 'Core task schema 002 is missing';
  END IF;
  IF NOT EXISTS (
    SELECT 1 FROM pg_roles
    WHERE rolname='secretary_pkb_proto_writer_20260927'
  ) THEN
    RAISE EXCEPTION 'Dedicated prototype writer role is missing';
  END IF;
END $guard$;

GRANT SELECT, INSERT, UPDATE ON secretary.tasks
  TO secretary_pkb_proto_writer_20260927;
GRANT SELECT, INSERT ON secretary.actions, secretary.results
  TO secretary_pkb_proto_writer_20260927;
GRANT INSERT ON secretary.audit_events
  TO secretary_pkb_proto_writer_20260927;

-- Deliberately no write grant on secretary.approvals. External actions remain
-- outside this first read-only Secretary Core vertical slice.

INSERT INTO secretary.schema_migrations(version, sha256)
VALUES ('017_pkb_proto_core_task_privileges.sql',
        'PENDING_SHA_REPLACED_BY_APPLIER');

COMMIT;
