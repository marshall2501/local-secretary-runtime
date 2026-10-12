-- Owner-configured policy labels, grants and environment/exposure profiles.
-- Initial activation preserves legacy private-context local restriction;
-- the owner can switch to verification through the Web settings UI.
SET search_path = secretary, pg_catalog;

CREATE TABLE permission_labels (
    label text PRIMARY KEY
        CHECK (label ~ '^[a-z][a-z0-9_.-]{1,95}$'),
    category text NOT NULL CHECK (length(btrim(category)) BETWEEN 1 AND 64),
    description text NOT NULL DEFAULT ''
        CHECK (length(description) <= 500)
);

CREATE TABLE policy_profiles (
    id uuid PRIMARY KEY,
    name text NOT NULL UNIQUE
        CHECK (name ~ '^[a-z][a-z0-9_-]{1,95}$'),
    purpose text NOT NULL CHECK (purpose IN ('verification','production')),
    exposure text NOT NULL CHECK (exposure IN ('local','lan','public')),
    revision bigint NOT NULL DEFAULT 1 CHECK (revision > 0),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE policy_grants (
    profile_id uuid NOT NULL REFERENCES policy_profiles(id) ON DELETE CASCADE,
    label text NOT NULL REFERENCES permission_labels(label),
    destination_scope text NOT NULL
        CHECK (destination_scope IN ('any','local','remote')),
    decision text NOT NULL
        CHECK (decision IN ('allow','deny','require_confirmation','redact')),
    mask_rules jsonb NOT NULL DEFAULT '{}'::jsonb
        CHECK (jsonb_typeof(mask_rules) = 'object'),
    PRIMARY KEY (profile_id, label, destination_scope)
);

CREATE TABLE policy_activations (
    exposure text PRIMARY KEY
        CHECK (exposure IN ('local','lan','public')),
    profile_id uuid NOT NULL REFERENCES policy_profiles(id),
    revision bigint NOT NULL DEFAULT 1 CHECK (revision > 0),
    updated_at timestamptz NOT NULL DEFAULT now()
);

INSERT INTO permission_labels(label, category, description) VALUES
    ('llm.send_context.private', 'llm_context', 'Send verified private observations'),
    ('llm.send_context.sensitive', 'llm_context', 'Send verified sensitive observations');

INSERT INTO policy_profiles (id, name, purpose, exposure) VALUES
    ('10000000-0000-0000-0000-000000000001', 'verification-lan', 'verification','lan'),
    ('10000000-0000-0000-0000-000000000002', 'production-lan', 'production','lan'),
    ('10000000-0000-0000-0000-000000000003', 'verification-local', 'verification','local'),
    ('10000000-0000-0000-0000-000000000004', 'production-local', 'production','local'),
    ('10000000-0000-0000-0000-000000000005', 'production-public', 'production','public');

-- Local/remote here describes where a *model endpoint* actually is,
-- never just the provider adapter name (a remote Ollama is remote).
INSERT INTO policy_grants(profile_id, label, destination_scope, decision)
SELECT p.id, l.label, 'any', 'allow'
FROM policy_profiles p CROSS JOIN permission_labels l
WHERE p.purpose='verification';

INSERT INTO policy_grants(profile_id, label, destination_scope, decision)
SELECT p.id, l.label, 'local', 'allow'
FROM policy_profiles p CROSS JOIN permission_labels l
WHERE p.purpose='production';

INSERT INTO policy_grants(profile_id, label, destination_scope, decision)
SELECT p.id, l.label, 'remote', 'deny'
FROM policy_profiles p CROSS JOIN permission_labels l
WHERE p.purpose='production';

-- No silently expanded cloud permission during a migration.
INSERT INTO policy_activations(exposure, profile_id) VALUES
    ('local', '10000000-0000-0000-0000-000000000004'),
    ('lan', '10000000-0000-0000-0000-000000000002'),
    ('public', '10000000-0000-0000-0000-000000000005');

REVOKE ALL ON
    permission_labels, policy_profiles, policy_grants, policy_activations
FROM PUBLIC;
