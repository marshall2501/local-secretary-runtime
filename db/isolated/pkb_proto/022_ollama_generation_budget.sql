-- Add explicit per-profile Ollama generation budget (num_predict).
BEGIN;

DO $guard$
BEGIN
  IF current_database() <> 'secretary_pkb_proto_20260927' THEN
    RAISE EXCEPTION 'Refusing unexpected database';
  END IF;
  IF NOT EXISTS (
    SELECT 1 FROM secretary.schema_migrations
    WHERE version='021_ollama_context_window.sql'
  ) THEN
    RAISE EXCEPTION 'Migration 021 must be applied first';
  END IF;
END $guard$;

ALTER TABLE secretary.llm_profiles
  ADD COLUMN ollama_num_predict integer;

UPDATE secretary.llm_profiles
SET ollama_num_predict = 4096
WHERE provider='ollama';

ALTER TABLE secretary.llm_profiles
  ADD CONSTRAINT llm_profiles_ollama_num_predict_chk
  CHECK (
    (provider='ollama'
      AND ollama_num_predict BETWEEN 256 AND 32768)
    OR
    (provider IN ('openai','gemini')
      AND ollama_num_predict IS NULL)
  );

INSERT INTO secretary.schema_migrations(version,sha256)
VALUES ('022_ollama_generation_budget.sql','PENDING_SHA_REPLACED_BY_APPLIER');

COMMIT;
