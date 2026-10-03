-- Final production schema for Service Connections, MAGI settings and Service Billing.
-- Intermediate provider/endpoint snapshots are intentionally not recreated.
SET search_path = secretary, pg_catalog;

CREATE TABLE service_connections (
    id uuid PRIMARY KEY,
    display_name text NOT NULL CHECK (length(btrim(display_name)) BETWEEN 1 AND 200),
    adapter_key text NOT NULL CHECK (adapter_key ~ '^[a-z][a-z0-9_]{1,63}$'),
    endpoint text NOT NULL CHECK (length(btrim(endpoint)) BETWEEN 1 AND 500),
    credential_ref text,
    account_label text,
    capabilities text[] NOT NULL DEFAULT ARRAY[]::text[],
    nonsecret_config jsonb NOT NULL DEFAULT '{}'::jsonb,
    auth_data jsonb NOT NULL DEFAULT '{}'::jsonb,
    connection_type text NOT NULL
        CHECK (connection_type IN (
            'none','api_key','username_password','oauth2','external_credentials'
        )),
    connection_role text,
    enabled boolean NOT NULL DEFAULT true,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CHECK (
        credential_ref IS NULL
        OR credential_ref ~ '^env:[A-Z_][A-Z0-9_]*$'
    ),
    CHECK (account_label IS NULL OR length(btrim(account_label)) BETWEEN 1 AND 200),
    CHECK (cardinality(capabilities) <= 50),
    CHECK (jsonb_typeof(nonsecret_config) = 'object'),
    CHECK (jsonb_typeof(auth_data) = 'object'),
    CHECK (
        connection_role IS NULL
        OR connection_role ~ '^[a-z][a-z0-9_]{1,63}$'
    )
);
CREATE UNIQUE INDEX service_connections_display_name_uq
    ON service_connections(lower(display_name));

COMMENT ON COLUMN service_connections.auth_data IS
    'Local-only authentication payload. Never copy into Prompt, Task, Audit, Git, or normal logs.';
COMMENT ON COLUMN service_connections.connection_role IS
    'Classification/display hint only. Consumers explicitly select connection_id.';

CREATE TABLE llm_profiles (
    id uuid PRIMARY KEY,
    display_name text NOT NULL CHECK (length(btrim(display_name)) BETWEEN 1 AND 200),
    connection_id uuid NOT NULL REFERENCES service_connections(id),
    model text NOT NULL CHECK (length(btrim(model)) BETWEEN 1 AND 200),
    context_window_tokens integer
        CHECK (context_window_tokens IS NULL
               OR context_window_tokens BETWEEN 1024 AND 1048576),
    ollama_num_predict integer
        CHECK (ollama_num_predict IS NULL
               OR ollama_num_predict BETWEEN 256 AND 32768),
    retry_http_codes integer[] NOT NULL
        DEFAULT ARRAY[429,500,502,503,504]::integer[],
    enabled boolean NOT NULL DEFAULT true,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT llm_profiles_retry_http_codes_chk
        CHECK (cardinality(retry_http_codes) <= 20)
);
CREATE UNIQUE INDEX llm_profiles_connection_model_uq
    ON llm_profiles(connection_id, model);

CREATE TABLE magi_member_assignments (
    member text PRIMARY KEY CHECK (member IN ('MELCHIOR','CASPER','BALTHASAR')),
    profile_id uuid NOT NULL REFERENCES llm_profiles(id),
    enabled boolean NOT NULL DEFAULT true,
    weight double precision NOT NULL DEFAULT 1.0 CHECK (weight > 0 AND weight <= 100),
    timeout_seconds integer NOT NULL DEFAULT 900 CHECK (timeout_seconds BETWEEN 1 AND 3600),
    retry_within_turn boolean NOT NULL DEFAULT true,
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE service_billing_profiles (
    id uuid PRIMARY KEY,
    display_name text NOT NULL CHECK (length(btrim(display_name)) BETWEEN 1 AND 200),
    connection_id uuid NOT NULL REFERENCES service_connections(id),
    enabled boolean NOT NULL DEFAULT true,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX service_billing_profiles_name_uq
    ON service_billing_profiles(lower(display_name));

COMMENT ON TABLE service_billing_profiles IS
    'Service Billing consumer definitions. Each profile explicitly selects one Service Connection.';
