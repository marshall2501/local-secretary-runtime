-- Prototype-only audit timestamp for Pending Claims review actions.
-- Apply only to secretary_pkb_proto_20260927. Never run on the production DB.
BEGIN;
DO $guard$
BEGIN
  IF current_database() <> 'secretary_pkb_proto_20260927' THEN
    RAISE EXCEPTION 'Refusing unexpected database';
  END IF;
  IF to_regclass('secretary.pkb_pending_intake') IS NULL THEN
    RAISE EXCEPTION 'Pending Claims intake table is missing';
  END IF;
END $guard$;

ALTER TABLE secretary.pkb_pending_intake
  ADD COLUMN reviewed_at timestamptz;

ALTER TABLE secretary.pkb_pending_intake
  ADD CONSTRAINT pkb_pending_reviewed_at_consistency
  CHECK (
    (review_status='pending' AND reviewed_at IS NULL)
    OR
    (review_status<>'pending' AND reviewed_at IS NOT NULL)
  );

INSERT INTO secretary.schema_migrations(version, sha256)
VALUES ('010_pkb_proto_pending_review_audit.sql',
        'PENDING_SHA_REPLACED_BY_APPLIER');
COMMIT;
