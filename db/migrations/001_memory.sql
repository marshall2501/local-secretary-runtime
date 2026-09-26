-- Domain-neutral structured source of truth. UUIDs do not depend on an LLM/framework.
SET search_path = secretary, pg_catalog;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
REVOKE ALL ON SCHEMA secretary FROM PUBLIC;

CREATE TABLE entities (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name text NOT NULL CHECK (btrim(name) <> ''),
    entity_type text NOT NULL,
    domain text NOT NULL,
    identification_evidence jsonb NOT NULL DEFAULT '{}' CHECK (jsonb_typeof(identification_evidence) = 'object'),
    created_at timestamptz NOT NULL DEFAULT now(),
    retired_at timestamptz
);
CREATE INDEX entities_lookup ON entities(domain, entity_type, name);

CREATE TABLE sources (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    source_type text NOT NULL CHECK (source_type IN ('user_statement','file','web','tool','service')),
    uri text NOT NULL CHECK (btrim(uri) <> ''),
    archive_uri text,
    sha256 text CHECK (sha256 ~ '^[0-9a-f]{64}$'),
    citation text NOT NULL CHECK (btrim(citation) <> ''),
    retrieved_at timestamptz NOT NULL,
    recorded_at timestamptz NOT NULL DEFAULT now(),
    confidentiality text NOT NULL DEFAULT 'private' CHECK (confidentiality IN ('public','private','restricted')),
    metadata jsonb NOT NULL DEFAULT '{}' CHECK (jsonb_typeof(metadata) = 'object')
);
CREATE INDEX sources_hash ON sources(sha256) WHERE sha256 IS NOT NULL;

CREATE TABLE pending_claims (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    entity_id uuid NOT NULL REFERENCES entities(id),
    source_id uuid NOT NULL REFERENCES sources(id),
    claim_type text NOT NULL CHECK (claim_type IN ('fact','observation','attribute','unconfirmed')),
    predicate text NOT NULL,
    proposed_value jsonb NOT NULL,
    confidence numeric(4,3) CHECK (confidence BETWEEN 0 AND 1),
    evidence text NOT NULL CHECK (btrim(evidence) <> ''),
    extraction_model text NOT NULL,
    prompt_version text NOT NULL,
    review_status text NOT NULL DEFAULT 'pending' CHECK (review_status IN ('pending','accepted','rejected','needs_edit')),
    reviewer text,
    reviewer_notes text,
    reviewed_at timestamptz,
    recorded_at timestamptz NOT NULL DEFAULT now(),
    CHECK ((review_status = 'pending' AND reviewer IS NULL AND reviewed_at IS NULL)
        OR (review_status <> 'pending' AND reviewer IS NOT NULL AND reviewed_at IS NOT NULL))
);
CREATE INDEX pending_claims_review ON pending_claims(review_status, recorded_at);

CREATE TABLE claims (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    entity_id uuid NOT NULL REFERENCES entities(id),
    source_id uuid NOT NULL REFERENCES sources(id),
    pending_claim_id uuid UNIQUE REFERENCES pending_claims(id),
    claim_type text NOT NULL CHECK (claim_type IN ('fact','observation','attribute','unconfirmed')),
    predicate text NOT NULL,
    value jsonb NOT NULL,
    evidence text NOT NULL CHECK (btrim(evidence) <> ''),
    origin text NOT NULL CHECK (origin IN ('user_explicit','reviewed_extraction','verified_import')),
    verification_status text NOT NULL CHECK (verification_status IN ('unverified','verified','disputed','retracted')),
    valid_from timestamptz NOT NULL,
    valid_to timestamptz,
    recorded_at timestamptz NOT NULL DEFAULT now(),
    supersedes_id uuid REFERENCES claims(id),
    retracted_at timestamptz,
    CHECK (valid_to IS NULL OR valid_to > valid_from),
    CHECK (supersedes_id IS DISTINCT FROM id),
    CHECK (origin <> 'reviewed_extraction' OR pending_claim_id IS NOT NULL),
    CHECK (claim_type <> 'unconfirmed' OR verification_status <> 'verified'),
    CHECK ((verification_status = 'retracted') = (retracted_at IS NOT NULL))
);
CREATE INDEX claims_entity_time ON claims(entity_id, predicate, valid_from, valid_to);
CREATE INDEX claims_source ON claims(source_id);
CREATE INDEX claims_text ON claims USING gin (to_tsvector('simple', predicate || ' ' || value::text));
-- Current effective AND current recorded revision; no inferred single-value overwrite.
CREATE VIEW current_claims AS
SELECT c.* FROM claims c
WHERE c.valid_from <= now() AND (c.valid_to IS NULL OR c.valid_to > now())
  AND c.retracted_at IS NULL
  AND NOT EXISTS (SELECT 1 FROM claims newer WHERE newer.supersedes_id = c.id
                    AND newer.valid_from <= now());

CREATE TABLE issues (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    entity_id uuid REFERENCES entities(id),
    source_id uuid NOT NULL REFERENCES sources(id),
    description text NOT NULL,
    status text NOT NULL DEFAULT 'open' CHECK (status IN ('open','investigating','resolved','closed')),
    recorded_at timestamptz NOT NULL DEFAULT now(),
    resolved_at timestamptz
);
CREATE TABLE hypotheses (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    issue_id uuid NOT NULL REFERENCES issues(id),
    source_id uuid NOT NULL REFERENCES sources(id),
    statement text NOT NULL,
    evidence text NOT NULL,
    status text NOT NULL DEFAULT 'unverified' CHECK (status IN ('unverified','supported','refuted')),
    recorded_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE decisions (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    entity_id uuid REFERENCES entities(id),
    issue_id uuid REFERENCES issues(id),
    source_id uuid NOT NULL REFERENCES sources(id),
    decision text NOT NULL,
    rationale text NOT NULL,
    decided_by text NOT NULL,
    valid_from timestamptz NOT NULL,
    valid_to timestamptz,
    recorded_at timestamptz NOT NULL DEFAULT now(),
    supersedes_id uuid REFERENCES decisions(id),
    CHECK (valid_to IS NULL OR valid_to > valid_from),
    CHECK (supersedes_id IS DISTINCT FROM id)
);

-- A provenance guard is only part of Memory Write Service validation (see docs).
CREATE FUNCTION validate_reviewed_claim() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.origin = 'reviewed_extraction' AND NOT EXISTS (
        SELECT 1 FROM secretary.pending_claims p
        WHERE p.id = NEW.pending_claim_id AND p.review_status = 'accepted'
          AND p.entity_id = NEW.entity_id AND p.source_id = NEW.source_id
          AND p.claim_type = NEW.claim_type AND p.predicate = NEW.predicate
          AND p.proposed_value = NEW.value
    ) THEN
        RAISE EXCEPTION 'Reviewed extraction must match an accepted candidate';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER claims_review BEFORE INSERT OR UPDATE ON claims
FOR EACH ROW EXECUTE FUNCTION validate_reviewed_claim();
