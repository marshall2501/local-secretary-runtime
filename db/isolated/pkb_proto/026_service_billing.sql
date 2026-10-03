-- Generalize Provider Usage into Service Billing and add the external credential type.
BEGIN;

DO $guard$
BEGIN
  IF current_database() <> 'secretary_pkb_proto_20260927' THEN
    RAISE EXCEPTION 'Refusing unexpected database';
  END IF;
  IF NOT EXISTS (
    SELECT 1 FROM secretary.schema_migrations
    WHERE version='025_connection_auth_and_consumer_binding.sql'
  ) THEN
    RAISE EXCEPTION 'Migration 025 must be applied first';
  END IF;
END $guard$;

ALTER TABLE secretary.provider_usage_profiles
  RENAME TO service_billing_profiles;

ALTER INDEX secretary.provider_usage_profiles_name_uq
  RENAME TO service_billing_profiles_name_uq;

COMMENT ON TABLE secretary.service_billing_profiles IS
  'Service Billing consumer definitions. Each profile explicitly selects one Service Connection.';

UPDATE secretary.service_billing_profiles
SET display_name='OpenAI Billing', updated_at=now()
WHERE lower(display_name)='openai usage';

UPDATE secretary.service_connections
SET capabilities = array_replace(capabilities, 'provider_usage_read', 'service_billing_read'),
    connection_role = CASE
      WHEN connection_role='provider_usage' THEN 'service_billing'
      ELSE connection_role
    END,
    updated_at=now()
WHERE 'provider_usage_read'=ANY(capabilities)
   OR connection_role='provider_usage';

ALTER TABLE secretary.service_connections
  DROP CONSTRAINT service_connections_type_chk;

ALTER TABLE secretary.service_connections
  ADD CONSTRAINT service_connections_type_chk
    CHECK (
      connection_type IN (
        'none','api_key','username_password','oauth2','external_credentials'
      )
    );

GRANT SELECT, INSERT, UPDATE, DELETE
  ON secretary.service_billing_profiles
  TO secretary_pkb_proto_writer_20260927;

INSERT INTO secretary.schema_migrations(version,sha256)
VALUES ('026_service_billing.sql','PENDING_SHA_REPLACED_BY_APPLIER');

COMMIT;
