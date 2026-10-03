-- Add per-profile HTTP retry codes and per-member turn retry toggle.
BEGIN;

DO $guard$
BEGIN
  IF current_database() <> 'secretary_pkb_proto_20260927' THEN
    RAISE EXCEPTION 'Refusing unexpected database';
  END IF;
  IF NOT EXISTS (
    SELECT 1 FROM secretary.schema_migrations
    WHERE version='022_ollama_generation_budget.sql'
  ) THEN
    RAISE EXCEPTION 'Migration 022 must be applied first';
  END IF;
END $guard$;

ALTER TABLE secretary.llm_profiles
  ADD COLUMN retry_http_codes integer[] NOT NULL
  DEFAULT ARRAY[429,500,502,503,504]::integer[];

ALTER TABLE secretary.llm_profiles
  ADD CONSTRAINT llm_profiles_retry_http_codes_chk
  CHECK (cardinality(retry_http_codes) <= 20);

ALTER TABLE secretary.magi_member_assignments
  ADD COLUMN retry_within_turn boolean NOT NULL DEFAULT true;

INSERT INTO secretary.schema_migrations(version,sha256)
VALUES ('023_magi_retry_policy.sql','PENDING_SHA_REPLACED_BY_APPLIER');

COMMIT;
