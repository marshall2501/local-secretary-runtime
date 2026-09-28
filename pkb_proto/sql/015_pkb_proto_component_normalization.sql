-- Prototype-only normalization of component identity vs parent-specific role.
-- 014 was already applied on the isolated sub-PC DB, so this migration does not rewrite 014.
BEGIN;

DO $guard$
BEGIN
  IF current_database() <> 'secretary_pkb_proto_20260927' THEN
    RAISE EXCEPTION 'Refusing unexpected database';
  END IF;
  IF to_regclass('secretary.entity_relations') IS NULL THEN
    RAISE EXCEPTION 'Entity relation schema is missing';
  END IF;
END $guard$;

ALTER TABLE secretary.entity_relations
  ADD COLUMN relation_role text
  CHECK (relation_role IS NULL OR btrim(relation_role) <> '');

-- Canonical Entity names no longer embed the current parent.
UPDATE secretary.entities
SET name='GPU1'
WHERE id='14000000-0000-0000-0000-000000000001'
  AND name='メインPCのGPU';

UPDATE secretary.entities
SET name='NIC1'
WHERE id='14000000-0000-0000-0000-000000000002'
  AND name='メインPCのNIC';

-- Parent-specific semantics live on the Relation, not the child Entity.
UPDATE secretary.entity_relations
SET relation_role='primary_gpu'
WHERE id='14000000-0000-0000-0000-000000000101'
  AND predicate='has_component';

UPDATE secretary.entity_relations
SET relation_role='wired_nic'
WHERE id='14000000-0000-0000-0000-000000000102'
  AND predicate='has_component';

CREATE UNIQUE INDEX entity_relations_active_role_unique
  ON secretary.entity_relations(subject_entity_id, predicate, relation_role)
  WHERE relation_role IS NOT NULL
    AND valid_to IS NULL
    AND retracted_at IS NULL;

INSERT INTO secretary.schema_migrations(version, sha256)
VALUES ('015_pkb_proto_component_normalization.sql',
        'PENDING_SHA_REPLACED_BY_APPLIER');
COMMIT;
