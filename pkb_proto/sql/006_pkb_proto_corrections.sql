-- Prototype ONLY: apply to secretary_pkb_proto_20260927, never to live secretary.
-- The existing dedicated writer can correct only columns needed by this slice.
SET search_path = secretary, pg_catalog;
CREATE TABLE pkb_correction_receipts (
    input_id text PRIMARY KEY CHECK (btrim(input_id) <> ''),
    payload_sha256 char(64) NOT NULL CHECK (payload_sha256 ~ '^[0-9a-f]{64}$'),
    old_claim_id uuid NOT NULL UNIQUE REFERENCES claims(id),
    new_claim_id uuid NOT NULL UNIQUE REFERENCES claims(id),
    source_id uuid NOT NULL UNIQUE REFERENCES sources(id),
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK (old_claim_id <> new_claim_id)
);
GRANT SELECT, INSERT ON pkb_correction_receipts TO secretary_pkb_proto_writer_20260927;
GRANT SELECT ON current_claims TO secretary_pkb_proto_writer_20260927;
GRANT UPDATE (verification_status, retracted_at) ON claims
    TO secretary_pkb_proto_writer_20260927;
