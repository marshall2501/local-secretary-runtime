-- Prototype-only read privilege for Core Task audit trace.
-- Apply only to secretary_pkb_proto_20260927. This permits the daily GUI to
-- display audit events it already writes; it does not grant approval writes.
BEGIN;

DO $guard$
BEGIN
  IF current_database() <> 'secretary_pkb_proto_20260927' THEN
    RAISE EXCEPTION 'Refusing unexpected database';
  END IF;
  IF to_regclass('secretary.audit_events') IS NULL THEN
    RAISE EXCEPTION 'Core audit table is missing';
  END IF;
  IF NOT EXISTS (
    SELECT 1 FROM pg_roles
    WHERE rolname='secretary_pkb_proto_writer_20260927'
  ) THEN
    RAISE EXCEPTION 'Dedicated prototype writer role is missing';
  END IF;
END $guard$;

GRANT SELECT ON secretary.audit_events
  TO secretary_pkb_proto_writer_20260927;

-- Deliberately no additional write grant on approvals or external actions.

INSERT INTO secretary.schema_migrations(version, sha256)
VALUES ('018_pkb_proto_core_audit_read.sql',
        'PENDING_SHA_REPLACED_BY_APPLIER');

COMMIT;
