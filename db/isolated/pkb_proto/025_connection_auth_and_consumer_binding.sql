-- D-24 revision: one Service Connection owns connection + authentication.
-- Consumer definitions explicitly reference connection_id.
BEGIN;

DO $guard$
BEGIN
  IF current_database() <> 'secretary_pkb_proto_20260927' THEN
    RAISE EXCEPTION 'Refusing unexpected database';
  END IF;
  IF NOT EXISTS (
    SELECT 1 FROM secretary.schema_migrations
    WHERE version='024_service_connections.sql'
  ) THEN
    RAISE EXCEPTION 'Migration 024 must be applied first';
  END IF;
END $guard$;

ALTER TABLE secretary.service_connections
  ADD COLUMN connection_type text,
  ADD COLUMN auth_data jsonb NOT NULL DEFAULT '{}'::jsonb,
  ADD COLUMN connection_role text;

UPDATE secretary.service_connections
SET connection_type = CASE
  WHEN adapter_key='ollama' THEN 'none'
  ELSE 'api_key'
END
WHERE connection_type IS NULL;

ALTER TABLE secretary.service_connections
  ALTER COLUMN connection_type SET NOT NULL,
  ADD CONSTRAINT service_connections_type_chk
    CHECK (connection_type IN ('none','api_key','username_password','oauth2')),
  ADD CONSTRAINT service_connections_auth_data_chk
    CHECK (jsonb_typeof(auth_data)='object'),
  ADD CONSTRAINT service_connections_role_chk
    CHECK (
      connection_role IS NULL
      OR connection_role ~ '^[a-z][a-z0-9_]{1,63}$'
    );

DROP INDEX secretary.service_connections_identity_uq;

CREATE UNIQUE INDEX service_connections_display_name_uq
  ON secretary.service_connections(lower(display_name));

-- 024 briefly allowed the Usage page to add provider_usage_read to an
-- inference Connection automatically. Remove that accidental coupling only
-- from the legacy auto-created OpenAI LLM Connection. Future shared
-- capabilities, when intentional, can still be configured explicitly.
UPDATE secretary.service_connections
SET capabilities = array_remove(capabilities, 'provider_usage_read'),
    updated_at = now()
WHERE lower(display_name)='openai / llm'
  AND 'llm_inference'=ANY(capabilities);

CREATE TABLE secretary.provider_usage_profiles (
  id uuid PRIMARY KEY,
  display_name text NOT NULL
    CHECK (length(btrim(display_name)) BETWEEN 1 AND 200),
  connection_id uuid NOT NULL
    REFERENCES secretary.service_connections(id),
  enabled boolean NOT NULL DEFAULT true,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX provider_usage_profiles_name_uq
  ON secretary.provider_usage_profiles(lower(display_name));

COMMENT ON TABLE secretary.provider_usage_profiles IS
  'Provider Usage consumer definitions. Each profile explicitly selects one Service Connection.';

COMMENT ON COLUMN secretary.service_connections.auth_data IS
  'Local-only authentication payload. Plain DB storage is an accepted current design; do not copy to Prompt/Task/Audit/Git.';
COMMENT ON COLUMN secretary.service_connections.connection_role IS
  'Optional classification/display hint only. Consumers must not auto-select a Connection by this field.';

GRANT SELECT, INSERT, UPDATE, DELETE
  ON secretary.provider_usage_profiles
  TO secretary_pkb_proto_writer_20260927;

INSERT INTO secretary.schema_migrations(version,sha256)
VALUES ('025_connection_auth_and_consumer_binding.sql','PENDING_SHA_REPLACED_BY_APPLIER');

COMMIT;
