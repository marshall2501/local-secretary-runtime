-- Promote the accepted PKB runtime schema to the operational database.
-- Final-schema DDL only. Data promotion is a separate operation.
SET search_path = secretary, pg_catalog;

ALTER TABLE claims
    ADD COLUMN semantic_kind text
        CHECK (semantic_kind IN ('attribute','event','state')),
    ADD COLUMN memory_metadata jsonb NOT NULL DEFAULT '{}'
        CHECK (jsonb_typeof(memory_metadata) = 'object');

CREATE OR REPLACE VIEW current_claims AS
SELECT c.* FROM claims c
WHERE c.valid_from <= now() AND (c.valid_to IS NULL OR c.valid_to > now())
  AND c.retracted_at IS NULL
  AND NOT EXISTS (
      SELECT 1 FROM claims newer
      WHERE newer.supersedes_id = c.id
        AND newer.valid_from <= now()
  );

CREATE TABLE pkb_input_receipts (
    input_id text PRIMARY KEY CHECK (btrim(input_id) <> ''),
    payload_sha256 char(64) NOT NULL CHECK (payload_sha256 ~ '^[0-9a-f]{64}$'),
    source_id uuid NOT NULL UNIQUE REFERENCES sources(id),
    claim_id uuid NOT NULL UNIQUE REFERENCES claims(id),
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE pkb_correction_receipts (
    input_id text PRIMARY KEY CHECK (btrim(input_id) <> ''),
    payload_sha256 char(64) NOT NULL CHECK (payload_sha256 ~ '^[0-9a-f]{64}$'),
    old_claim_id uuid NOT NULL UNIQUE REFERENCES claims(id),
    new_claim_id uuid NOT NULL UNIQUE REFERENCES claims(id),
    source_id uuid NOT NULL UNIQUE REFERENCES sources(id),
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK (old_claim_id <> new_claim_id)
);

CREATE TABLE pkb_pending_intake (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    input_id text NOT NULL UNIQUE CHECK (btrim(input_id) <> ''),
    raw_text text NOT NULL CHECK (btrim(raw_text) <> ''),
    reason text NOT NULL CHECK (btrim(reason) <> ''),
    entity_id uuid REFERENCES entities(id),
    predicate text,
    proposed_value text,
    review_status text NOT NULL DEFAULT 'pending'
        CHECK (review_status IN ('pending','accepted','rejected','needs_edit')),
    recorded_at timestamptz NOT NULL DEFAULT now(),
    reviewed_at timestamptz,
    accepted_source_id uuid REFERENCES sources(id),
    accepted_claim_id uuid REFERENCES claims(id),
    interpreter_kind text,
    interpreter_model text,
    memory_context jsonb NOT NULL DEFAULT '{}'
        CHECK (jsonb_typeof(memory_context) = 'object'),
    CONSTRAINT pkb_pending_reviewed_at_consistency
        CHECK (review_status <> 'pending' OR reviewed_at IS NULL),
    CONSTRAINT pkb_pending_acceptance_link_consistency
        CHECK (
            (review_status = 'accepted'
             AND accepted_source_id IS NOT NULL
             AND accepted_claim_id IS NOT NULL)
            OR
            (review_status <> 'accepted'
             AND accepted_source_id IS NULL
             AND accepted_claim_id IS NULL)
        ),
    CONSTRAINT pkb_pending_interpreter_pair
        CHECK (
            (interpreter_kind IS NULL AND interpreter_model IS NULL)
            OR
            (interpreter_kind = 'local_ollama'
             AND interpreter_model IS NOT NULL
             AND btrim(interpreter_model) <> '')
        )
);
CREATE INDEX pkb_pending_intake_status
    ON pkb_pending_intake(review_status, recorded_at DESC);

CREATE TABLE entity_relations (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    subject_entity_id uuid NOT NULL REFERENCES entities(id),
    predicate text NOT NULL CHECK (btrim(predicate) <> ''),
    object_entity_id uuid NOT NULL REFERENCES entities(id),
    source_id uuid NOT NULL REFERENCES sources(id),
    relation_role text CHECK (relation_role IS NULL OR btrim(relation_role) <> ''),
    valid_from timestamptz NOT NULL,
    valid_to timestamptz,
    recorded_at timestamptz NOT NULL DEFAULT now(),
    supersedes_id uuid REFERENCES entity_relations(id),
    retracted_at timestamptz,
    CHECK (subject_entity_id <> object_entity_id),
    CHECK (valid_to IS NULL OR valid_to > valid_from),
    CHECK (supersedes_id IS DISTINCT FROM id)
);
CREATE INDEX entity_relations_subject_time
    ON entity_relations(subject_entity_id, predicate, valid_from, valid_to);
CREATE INDEX entity_relations_object_time
    ON entity_relations(object_entity_id, predicate, valid_from, valid_to);
CREATE UNIQUE INDEX entity_relations_current_unique
    ON entity_relations(subject_entity_id, predicate, object_entity_id)
    WHERE valid_to IS NULL AND retracted_at IS NULL;
CREATE UNIQUE INDEX entity_relations_active_role_unique
    ON entity_relations(subject_entity_id, predicate, relation_role)
    WHERE relation_role IS NOT NULL
      AND valid_to IS NULL
      AND retracted_at IS NULL;

CREATE UNIQUE INDEX claims_one_active_state
    ON claims(entity_id, predicate)
    WHERE semantic_kind = 'state'
      AND valid_to IS NULL
      AND retracted_at IS NULL;

CREATE TABLE pkb_memory_intakes (
    input_id uuid PRIMARY KEY,
    payload_hash char(64) NOT NULL CHECK (payload_hash ~ '^[0-9a-f]{64}$'),
    source_id uuid NOT NULL UNIQUE REFERENCES sources(id),
    status text NOT NULL CHECK (status = 'committed'),
    result jsonb NOT NULL CHECK (jsonb_typeof(result) = 'object'),
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE pkb_memory_candidate_receipts (
    input_id uuid NOT NULL REFERENCES pkb_memory_intakes(input_id),
    candidate_id text NOT NULL,
    decision text NOT NULL
        CHECK (decision IN ('auto_commit','pending','task_context_only','ignore')),
    status text NOT NULL CHECK (status IN ('valid','invalid')),
    claim_id uuid REFERENCES claims(id),
    pending_id uuid REFERENCES pkb_pending_intake(id),
    derived_claim_ids uuid[] NOT NULL DEFAULT '{}',
    audit jsonb NOT NULL CHECK (jsonb_typeof(audit) = 'object'),
    PRIMARY KEY(input_id, candidate_id),
    CHECK ((decision = 'auto_commit') = (claim_id IS NOT NULL)),
    CHECK ((decision = 'pending') = (pending_id IS NOT NULL))
);
