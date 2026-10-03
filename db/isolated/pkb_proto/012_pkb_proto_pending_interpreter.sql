-- Prototype-only interpreter provenance for daily PKB Pending intake.
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
  ADD COLUMN interpreter_kind text,
  ADD COLUMN interpreter_model text;

ALTER TABLE secretary.pkb_pending_intake
  ADD CONSTRAINT pkb_pending_interpreter_pair
  CHECK (
    (interpreter_kind IS NULL AND interpreter_model IS NULL)
    OR
    (interpreter_kind='local_ollama'
     AND interpreter_model IS NOT NULL
     AND btrim(interpreter_model) <> '')
  );

INSERT INTO secretary.schema_migrations(version, sha256)
VALUES ('012_pkb_proto_pending_interpreter.sql',
        'PENDING_SHA_REPLACED_BY_APPLIER');
COMMIT;
