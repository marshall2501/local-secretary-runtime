-- Fictional prototype only. Existing receipts, claims and Pending remain valid.
BEGIN;
DO $guard$
BEGIN
  IF current_database() <> 'secretary_pkb_proto_20260927' THEN
    RAISE EXCEPTION 'Refusing unexpected database';
  END IF;
END $guard$;
ALTER TABLE secretary.claims ADD COLUMN memory_metadata jsonb NOT NULL DEFAULT '{}'
  CHECK (jsonb_typeof(memory_metadata)='object');
ALTER TABLE secretary.pkb_pending_intake ADD COLUMN memory_context jsonb NOT NULL DEFAULT '{}'
  CHECK (jsonb_typeof(memory_context)='object');
CREATE TABLE secretary.pkb_memory_intakes (
  input_id uuid PRIMARY KEY,
  payload_hash char(64) NOT NULL CHECK (payload_hash ~ '^[0-9a-f]{64}$'),
  source_id uuid NOT NULL UNIQUE REFERENCES secretary.sources(id),
  status text NOT NULL CHECK (status='committed'),
  result jsonb NOT NULL CHECK (jsonb_typeof(result)='object'),
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE secretary.pkb_memory_candidate_receipts (
  input_id uuid NOT NULL REFERENCES secretary.pkb_memory_intakes(input_id),
  candidate_id text NOT NULL,
  decision text NOT NULL CHECK (decision IN ('auto_commit','pending','task_context_only','ignore')),
  status text NOT NULL CHECK (status IN ('valid','invalid')),
  claim_id uuid REFERENCES secretary.claims(id),
  pending_id uuid REFERENCES secretary.pkb_pending_intake(id),
  derived_claim_ids uuid[] NOT NULL DEFAULT '{}',
  audit jsonb NOT NULL CHECK (jsonb_typeof(audit)='object'),
  PRIMARY KEY(input_id,candidate_id),
  CHECK ((decision='auto_commit') = (claim_id IS NOT NULL)),
  CHECK ((decision='pending') = (pending_id IS NOT NULL))
);
GRANT SELECT, INSERT ON secretary.pkb_memory_intakes, secretary.pkb_memory_candidate_receipts
 TO secretary_pkb_proto_writer_20260927;
GRANT UPDATE(memory_metadata) ON secretary.claims TO secretary_pkb_proto_writer_20260927;
INSERT INTO secretary.schema_migrations(version,sha256)
VALUES ('019_pkb_proto_memory_intake.sql','PENDING_SHA_REPLACED_BY_APPLIER');
COMMIT;
