-- Prototype-only PC component fixture and current-state guard.
-- Apply only to secretary_pkb_proto_20260927. Never run on the production DB.
BEGIN;

DO $guard$
DECLARE
  main_pc_count integer;
BEGIN
  IF current_database() <> 'secretary_pkb_proto_20260927' THEN
    RAISE EXCEPTION 'Refusing unexpected database';
  END IF;
  SELECT count(*) INTO main_pc_count
  FROM secretary.entities
  WHERE name='メインPC' AND retired_at IS NULL;
  IF main_pc_count <> 1 THEN
    RAISE EXCEPTION 'Expected exactly one active fictional メインPC entity';
  END IF;
  IF to_regclass('secretary.entity_relations') IS NULL THEN
    RAISE EXCEPTION 'Entity relation schema 013 is missing';
  END IF;
END $guard$;

INSERT INTO secretary.entities
  (id, name, entity_type, domain, identification_evidence)
VALUES
  ('14000000-0000-0000-0000-000000000001',
   'メインPCのGPU', 'gpu', 'pc',
   '{"fictional_only":true,"component_role":"gpu","parent_hint":"メインPC"}'::jsonb),
  ('14000000-0000-0000-0000-000000000002',
   'メインPCのNIC', 'network_adapter', 'pc',
   '{"fictional_only":true,"component_role":"nic","parent_hint":"メインPC"}'::jsonb);

INSERT INTO secretary.sources
  (id, source_type, uri, citation, retrieved_at, recorded_at, confidentiality, metadata)
VALUES
  ('14000000-0000-0000-0000-0000000000f0',
   'user_statement',
   'fixture://pkb-proto/component-model-014',
   'fictional component fixture 014',
   now(), now(), 'private',
   '{"fictional_only":true,"purpose":"component-relation-fixture"}'::jsonb);

INSERT INTO secretary.entity_relations
  (id, subject_entity_id, predicate, object_entity_id, source_id, valid_from)
SELECT
  '14000000-0000-0000-0000-000000000101',
  p.id, 'has_component',
  '14000000-0000-0000-0000-000000000001',
  '14000000-0000-0000-0000-0000000000f0',
  now()
FROM secretary.entities p
WHERE p.name='メインPC' AND p.retired_at IS NULL;

INSERT INTO secretary.entity_relations
  (id, subject_entity_id, predicate, object_entity_id, source_id, valid_from)
SELECT
  '14000000-0000-0000-0000-000000000102',
  p.id, 'has_component',
  '14000000-0000-0000-0000-000000000002',
  '14000000-0000-0000-0000-0000000000f0',
  now()
FROM secretary.entities p
WHERE p.name='メインPC' AND p.retired_at IS NULL;

-- State succession closes the previous validity interval; it is not a correction.
GRANT UPDATE (valid_to) ON secretary.claims
  TO secretary_pkb_proto_writer_20260927;

-- One active State per Entity/predicate. Historical states remain queryable.
CREATE UNIQUE INDEX claims_one_active_state
  ON secretary.claims(entity_id, predicate)
  WHERE semantic_kind='state' AND valid_to IS NULL AND retracted_at IS NULL;

INSERT INTO secretary.schema_migrations(version, sha256)
VALUES ('014_pkb_proto_component_state.sql',
        'PENDING_SHA_REPLACED_BY_APPLIER');
COMMIT;
