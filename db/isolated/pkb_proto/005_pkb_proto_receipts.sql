-- PKB prototype only: apply to a newly created secretary_pkb_proto_* database.
-- Never apply this migration to the live secretary DB as part of this experiment.
SET search_path = secretary, pg_catalog;
CREATE TABLE pkb_input_receipts (
    input_id text PRIMARY KEY CHECK (btrim(input_id) <> ''),
    payload_sha256 char(64) NOT NULL CHECK (payload_sha256 ~ '^[0-9a-f]{64}$'),
    source_id uuid NOT NULL UNIQUE REFERENCES sources(id),
    claim_id uuid NOT NULL UNIQUE REFERENCES claims(id),
    created_at timestamptz NOT NULL DEFAULT now()
);
GRANT SELECT, INSERT ON pkb_input_receipts TO secretary_memory_writer;
