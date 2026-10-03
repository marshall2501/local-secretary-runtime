-- Prototype-only compositional Entity model for the daily PKB.
-- Apply only to secretary_pkb_proto_20260927. Never run on the production DB.
BEGIN;
DO $guard$
BEGIN
  IF current_database() <> 'secretary_pkb_proto_20260927' THEN
    RAISE EXCEPTION 'Refusing unexpected database';
  END IF;
  IF to_regclass('secretary.claims') IS NULL
     OR to_regclass('secretary.entities') IS NULL
     OR to_regclass('secretary.sources') IS NULL THEN
    RAISE EXCEPTION 'Required PKB base tables are missing';
  END IF;
END $guard$;

ALTER TABLE secretary.claims
  ADD COLUMN semantic_kind text
  CHECK (semantic_kind IN ('attribute','event','state'));

CREATE TABLE secretary.entity_relations (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  subject_entity_id uuid NOT NULL REFERENCES secretary.entities(id),
  predicate text NOT NULL CHECK (btrim(predicate) <> ''),
  object_entity_id uuid NOT NULL REFERENCES secretary.entities(id),
  source_id uuid NOT NULL REFERENCES secretary.sources(id),
  valid_from timestamptz NOT NULL,
  valid_to timestamptz,
  recorded_at timestamptz NOT NULL DEFAULT now(),
  supersedes_id uuid REFERENCES secretary.entity_relations(id),
  retracted_at timestamptz,
  CHECK (subject_entity_id <> object_entity_id),
  CHECK (valid_to IS NULL OR valid_to > valid_from),
  CHECK (supersedes_id IS DISTINCT FROM id)
);

CREATE INDEX entity_relations_subject_time
  ON secretary.entity_relations(subject_entity_id, predicate, valid_from, valid_to);
CREATE INDEX entity_relations_object_time
  ON secretary.entity_relations(object_entity_id, predicate, valid_from, valid_to);
CREATE UNIQUE INDEX entity_relations_current_unique
  ON secretary.entity_relations(subject_entity_id, predicate, object_entity_id)
  WHERE valid_to IS NULL AND retracted_at IS NULL;

DO $grant$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='secretary_pkb_proto_writer_20260927') THEN
    GRANT SELECT, INSERT, UPDATE ON secretary.entity_relations
      TO secretary_pkb_proto_writer_20260927;
  END IF;
END $grant$;

INSERT INTO secretary.schema_migrations(version, sha256)
VALUES ('013_pkb_proto_entity_model.sql',
        'PENDING_SHA_REPLACED_BY_APPLIER');
COMMIT;
