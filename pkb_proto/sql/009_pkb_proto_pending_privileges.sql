-- Prototype-only privilege correction for Pending Claims intake.
-- Apply only to secretary_pkb_proto_20260927. Never run on the production DB.
BEGIN;
DO $guard$
BEGIN
  IF current_database() <> 'secretary_pkb_proto_20260927' THEN
    RAISE EXCEPTION 'Refusing unexpected database';
  END IF;
  IF to_regclass('secretary.pkb_pending_intake') IS NULL THEN
    RAISE EXCEPTION '008 Pending Claims intake table is missing';
  END IF;
  IF NOT EXISTS (
    SELECT 1 FROM pg_roles
    WHERE rolname='secretary_pkb_proto_writer_20260927'
  ) THEN
    RAISE EXCEPTION 'Dedicated prototype writer role is missing';
  END IF;
END $guard$;

GRANT SELECT, INSERT, UPDATE ON secretary.pkb_pending_intake
  TO secretary_pkb_proto_writer_20260927;

INSERT INTO secretary.schema_migrations(version, sha256)
VALUES ('009_pkb_proto_pending_privileges.sql',
        'PENDING_SHA_REPLACED_BY_APPLIER');
COMMIT;
