-- Add an explicit per-profile Ollama context window.
BEGIN;

DO $guard$
BEGIN
  IF current_database() <> 'secretary_pkb_proto_20260927' THEN
    RAISE EXCEPTION 'Refusing unexpected database';
  END IF;
  IF NOT EXISTS (
    SELECT 1 FROM secretary.schema_migrations
    WHERE version='020_magi_llm_settings.sql'
  ) THEN
    RAISE EXCEPTION 'Migration 020 must be applied first';
  END IF;
END $guard$;

ALTER TABLE secretary.llm_profiles
  ADD COLUMN context_window_tokens integer;

UPDATE secretary.llm_profiles
SET context_window_tokens = 65536
WHERE provider='ollama';

ALTER TABLE secretary.llm_profiles
  ADD CONSTRAINT llm_profiles_context_window_chk
  CHECK (
    (provider='ollama'
      AND context_window_tokens BETWEEN 1024 AND 1048576)
    OR
    (provider IN ('openai','gemini')
      AND context_window_tokens IS NULL)
  );

INSERT INTO secretary.schema_migrations(version,sha256)
VALUES ('021_ollama_context_window.sql','PENDING_SHA_REPLACED_BY_APPLIER');

COMMIT;
