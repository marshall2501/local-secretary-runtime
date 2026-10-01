-- Prototype MAGI LLM configuration. Member labels are independent from providers/models.
BEGIN;

DO $guard$
BEGIN
  IF current_database() <> 'secretary_pkb_proto_20260927' THEN
    RAISE EXCEPTION 'Refusing unexpected database';
  END IF;
  IF NOT EXISTS (
    SELECT 1 FROM pg_roles
    WHERE rolname='secretary_pkb_proto_writer_20260927'
  ) THEN
    RAISE EXCEPTION 'Dedicated prototype writer role is missing';
  END IF;
END $guard$;

CREATE TABLE secretary.llm_profiles (
  id uuid PRIMARY KEY,
  display_name text NOT NULL CHECK (length(btrim(display_name)) BETWEEN 1 AND 200),
  provider text NOT NULL CHECK (provider IN ('ollama','openai','gemini')),
  model text NOT NULL CHECK (length(btrim(model)) BETWEEN 1 AND 200),
  endpoint text NOT NULL CHECK (length(btrim(endpoint)) BETWEEN 1 AND 500),
  credential_env text,
  enabled boolean NOT NULL DEFAULT true,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  CHECK (
    (provider='ollama' AND credential_env IS NULL)
    OR
    (provider IN ('openai','gemini') AND credential_env IS NOT NULL
      AND credential_env ~ '^[A-Z_][A-Z0-9_]*$')
  )
);

CREATE UNIQUE INDEX llm_profiles_identity_uq
  ON secretary.llm_profiles(
    provider, model, endpoint, COALESCE(credential_env,'')
  );

CREATE TABLE secretary.magi_member_assignments (
  member text PRIMARY KEY CHECK (member IN ('MELCHIOR','CASPER','BALTHASAR')),
  profile_id uuid NOT NULL REFERENCES secretary.llm_profiles(id),
  enabled boolean NOT NULL DEFAULT true,
  weight double precision NOT NULL DEFAULT 1.0 CHECK (weight > 0 AND weight <= 100),
  timeout_seconds integer NOT NULL DEFAULT 900 CHECK (timeout_seconds BETWEEN 1 AND 3600),
  updated_at timestamptz NOT NULL DEFAULT now()
);

GRANT SELECT, INSERT, UPDATE, DELETE
  ON secretary.llm_profiles, secretary.magi_member_assignments
  TO secretary_pkb_proto_writer_20260927;

INSERT INTO secretary.schema_migrations(version,sha256)
VALUES ('020_magi_llm_settings.sql','PENDING_SHA_REPLACED_BY_APPLIER');

COMMIT;
