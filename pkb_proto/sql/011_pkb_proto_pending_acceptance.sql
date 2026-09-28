-- Prototype-only linkage from accepted Pending intake rows to promoted Source/Claim.
-- Apply only to secretary_pkb_proto_20260927. Never run on the production DB.
BEGIN;
DO $guard$
BEGIN
  IF current_database() <> 'secretary_pkb_proto_20260927' THEN
    RAISE EXCEPTION 'Refusing unexpected database';
  END IF;
  IF to_regclass('secretary.pkb_pending_intake') IS NULL THEN
    RAISE EXCEPTION 'Pending Claims intake table is missing';
  END IF;
END $guard$;

ALTER TABLE secretary.pkb_pending_intake
  ADD COLUMN accepted_source_id uuid REFERENCES secretary.sources(id),
  ADD COLUMN accepted_claim_id uuid REFERENCES secretary.claims(id);

ALTER TABLE secretary.pkb_pending_intake
  ADD CONSTRAINT pkb_pending_acceptance_link_consistency
  CHECK (
    (review_status='accepted' AND accepted_source_id IS NOT NULL AND accepted_claim_id IS NOT NULL)
    OR
    (review_status<>'accepted' AND accepted_source_id IS NULL AND accepted_claim_id IS NULL)
  );

INSERT INTO secretary.schema_migrations(version, sha256)
VALUES ('011_pkb_proto_pending_acceptance.sql',
        'PENDING_SHA_REPLACED_BY_APPLIER');
COMMIT;
