-- Shared Service Connection Registry for LLM and future external services.
BEGIN;

DO $guard$
BEGIN
  IF current_database() <> 'secretary_pkb_proto_20260927' THEN
    RAISE EXCEPTION 'Refusing unexpected database';
  END IF;
  IF NOT EXISTS (
    SELECT 1 FROM secretary.schema_migrations
    WHERE version='023_magi_retry_policy.sql'
  ) THEN
    RAISE EXCEPTION 'Migration 023 must be applied first';
  END IF;
END $guard$;

CREATE TABLE secretary.service_connections (
  id uuid PRIMARY KEY,
  display_name text NOT NULL CHECK (length(btrim(display_name)) BETWEEN 1 AND 200),
  adapter_key text NOT NULL CHECK (adapter_key ~ '^[a-z][a-z0-9_]{1,63}$'),
  endpoint text NOT NULL CHECK (length(btrim(endpoint)) BETWEEN 1 AND 500),
  credential_ref text,
  account_label text,
  capabilities text[] NOT NULL DEFAULT ARRAY[]::text[],
  nonsecret_config jsonb NOT NULL DEFAULT '{}'::jsonb,
  enabled boolean NOT NULL DEFAULT true,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  CHECK (
    credential_ref IS NULL
    OR credential_ref ~ '^env:[A-Z_][A-Z0-9_]*$'
  ),
  CHECK (account_label IS NULL OR length(btrim(account_label)) BETWEEN 1 AND 200),
  CHECK (cardinality(capabilities) <= 50),
  CHECK (jsonb_typeof(nonsecret_config)='object')
);

CREATE UNIQUE INDEX service_connections_identity_uq
  ON secretary.service_connections(
    adapter_key, endpoint, COALESCE(credential_ref,''), COALESCE(account_label,'')
  );

ALTER TABLE secretary.llm_profiles
  ADD COLUMN connection_id uuid;

INSERT INTO secretary.service_connections(
  id, display_name, adapter_key, endpoint, credential_ref,
  account_label, capabilities, nonsecret_config, enabled,
  created_at, updated_at
)
SELECT DISTINCT ON (provider, endpoint, COALESCE(credential_env,''))
  id,
  provider || ' / LLM',
  provider,
  endpoint,
  CASE
    WHEN credential_env IS NULL THEN NULL
    ELSE 'env:' || credential_env
  END,
  NULL,
  ARRAY['llm_inference']::text[],
  '{}'::jsonb,
  true,
  created_at,
  updated_at
FROM secretary.llm_profiles
ORDER BY provider, endpoint, COALESCE(credential_env,''), id::text;

UPDATE secretary.llm_profiles p
SET connection_id=c.id
FROM secretary.service_connections c
WHERE c.adapter_key=p.provider
  AND c.endpoint=p.endpoint
  AND c.credential_ref IS NOT DISTINCT FROM (
    CASE
      WHEN p.credential_env IS NULL THEN NULL
      ELSE 'env:' || p.credential_env
    END
  )
  AND c.account_label IS NULL;

ALTER TABLE secretary.llm_profiles
  ALTER COLUMN connection_id SET NOT NULL;

ALTER TABLE secretary.llm_profiles
  ADD CONSTRAINT llm_profiles_connection_fk
  FOREIGN KEY (connection_id)
  REFERENCES secretary.service_connections(id);

DROP INDEX secretary.llm_profiles_identity_uq;

CREATE UNIQUE INDEX llm_profiles_connection_model_uq
  ON secretary.llm_profiles(connection_id, model);

ALTER TABLE secretary.llm_profiles
  ALTER COLUMN provider DROP NOT NULL,
  ALTER COLUMN endpoint DROP NOT NULL;

COMMENT ON COLUMN secretary.llm_profiles.provider IS
  'Legacy pre-024 snapshot; Service Connection is the runtime source of truth.';
COMMENT ON COLUMN secretary.llm_profiles.endpoint IS
  'Legacy pre-024 snapshot; Service Connection is the runtime source of truth.';
COMMENT ON COLUMN secretary.llm_profiles.credential_env IS
  'Legacy pre-024 snapshot; Service Connection credential_ref is authoritative.';

GRANT SELECT, INSERT, UPDATE, DELETE
  ON secretary.service_connections
  TO secretary_pkb_proto_writer_20260927;

INSERT INTO secretary.schema_migrations(version,sha256)
VALUES ('024_service_connections.sql','PENDING_SHA_REPLACED_BY_APPLIER');

COMMIT;
