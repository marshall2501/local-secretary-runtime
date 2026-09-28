-- Prototype-only persistence for PKB exception review.
-- Apply only to secretary_pkb_proto_20260927. Never run on the production DB.
BEGIN;
DO $guard$
BEGIN
  IF current_database() <> 'secretary_pkb_proto_20260927' THEN
    RAISE EXCEPTION 'Refusing unexpected database';
  END IF;
  IF to_regclass('secretary.pkb_pending_intake') IS NOT NULL THEN
    RAISE EXCEPTION 'pkb_pending_intake already exists';
  END IF;
END $guard$;

CREATE TABLE secretary.pkb_pending_intake (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  input_id text NOT NULL UNIQUE CHECK (btrim(input_id) <> ''),
  raw_text text NOT NULL CHECK (btrim(raw_text) <> ''),
  reason text NOT NULL CHECK (btrim(reason) <> ''),
  entity_id uuid REFERENCES secretary.entities(id),
  predicate text,
  proposed_value text,
  review_status text NOT NULL DEFAULT 'pending'
    CHECK (review_status IN ('pending','accepted','rejected','needs_edit')),
  recorded_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX pkb_pending_intake_status
  ON secretary.pkb_pending_intake(review_status, recorded_at DESC);

DO $grant$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='secretary_pkb_proto_writer_20260927') THEN
    GRANT SELECT, INSERT, UPDATE ON secretary.pkb_pending_intake
      TO secretary_pkb_proto_writer_20260927;
  END IF;
END $grant$;

INSERT INTO secretary.schema_migrations(version, sha256)
VALUES ('008_pkb_proto_pending_intake.sql',
        'PENDING_SHA_REPLACED_BY_APPLIER');
COMMIT;
