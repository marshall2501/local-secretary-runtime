WITH RECURSIVE
candidate_sources AS (
    SELECT id
    FROM secretary.sources
    WHERE uri LIKE 'fixture://daily-pkb/%'
       OR uri LIKE 'fixture://memory-intake/%'
),
candidate_memory_intakes AS (
    SELECT input_id, source_id
    FROM secretary.pkb_memory_intakes
    WHERE source_id IN (SELECT id FROM candidate_sources)
),
candidate_memory_receipts AS (
    SELECT r.*
    FROM secretary.pkb_memory_candidate_receipts r
    JOIN candidate_memory_intakes i ON i.input_id=r.input_id
),
candidate_pending AS (
    SELECT p.*
    FROM secretary.pkb_pending_intake p
    WHERE p.input_id LIKE 'daily-pkb-%'
       OR p.accepted_source_id IN (SELECT id FROM candidate_sources)
       OR p.memory_context->>'source_id' IN (
           SELECT id::text FROM candidate_sources
       )
),
seed_claim_ids AS (
    SELECT c.id
    FROM secretary.claims c
    WHERE c.source_id IN (SELECT id FROM candidate_sources)
    UNION
    SELECT p.accepted_claim_id
    FROM candidate_pending p
    WHERE p.accepted_claim_id IS NOT NULL
    UNION
    SELECT r.claim_id
    FROM candidate_memory_receipts r
    WHERE r.claim_id IS NOT NULL
    UNION
    SELECT unnest(r.derived_claim_ids)
    FROM candidate_memory_receipts r
    WHERE cardinality(r.derived_claim_ids) > 0
    UNION
    SELECT r.claim_id
    FROM secretary.pkb_input_receipts r
    WHERE r.source_id IN (SELECT id FROM candidate_sources)
    UNION
    SELECT r.old_claim_id
    FROM secretary.pkb_correction_receipts r
    WHERE r.source_id IN (SELECT id FROM candidate_sources)
    UNION
    SELECT r.new_claim_id
    FROM secretary.pkb_correction_receipts r
    WHERE r.source_id IN (SELECT id FROM candidate_sources)
),
claim_closure(id) AS (
    SELECT id FROM seed_claim_ids WHERE id IS NOT NULL
    UNION
    SELECT c.supersedes_id
    FROM secretary.claims c
    JOIN claim_closure cc ON cc.id=c.id
    WHERE c.supersedes_id IS NOT NULL
),
seed_entities AS (
    SELECT c.entity_id
    FROM secretary.claims c
    JOIN claim_closure cc ON cc.id=c.id
    UNION
    SELECT p.entity_id
    FROM candidate_pending p
    WHERE p.entity_id IS NOT NULL
),
entity_edges AS (
    SELECT subject_entity_id AS a, object_entity_id AS b
    FROM secretary.entity_relations
    WHERE valid_to IS NULL AND retracted_at IS NULL
    UNION ALL
    SELECT object_entity_id AS a, subject_entity_id AS b
    FROM secretary.entity_relations
    WHERE valid_to IS NULL AND retracted_at IS NULL
),
entity_closure(id) AS (
    SELECT entity_id FROM seed_entities
    UNION
    SELECT e.b
    FROM entity_edges e
    JOIN entity_closure ec ON ec.id=e.a
),
relation_closure AS (
    SELECT r.id, r.subject_entity_id, r.object_entity_id, r.source_id
    FROM secretary.entity_relations r
    WHERE r.subject_entity_id IN (SELECT id FROM entity_closure)
       OR r.object_entity_id IN (SELECT id FROM entity_closure)
),
input_receipt_closure AS (
    SELECT r.*
    FROM secretary.pkb_input_receipts r
    WHERE r.source_id IN (SELECT id FROM candidate_sources)
       OR r.claim_id IN (SELECT id FROM claim_closure)
),
correction_receipt_closure AS (
    SELECT r.*
    FROM secretary.pkb_correction_receipts r
    WHERE r.source_id IN (SELECT id FROM candidate_sources)
       OR r.old_claim_id IN (SELECT id FROM claim_closure)
       OR r.new_claim_id IN (SELECT id FROM claim_closure)
),
source_closure(id) AS (
    SELECT id FROM candidate_sources
    UNION
    SELECT c.source_id
    FROM secretary.claims c
    JOIN claim_closure cc ON cc.id=c.id
    UNION
    SELECT r.source_id FROM relation_closure r
    UNION
    SELECT p.accepted_source_id
    FROM candidate_pending p
    WHERE p.accepted_source_id IS NOT NULL
    UNION
    SELECT r.source_id FROM input_receipt_closure r
    UNION
    SELECT r.source_id FROM correction_receipt_closure r
),
known_dev_sources AS (
    SELECT id
    FROM secretary.sources
    WHERE metadata->>'dataset_id'='pkb-p0-fictional-20260927'
       OR id='14000000-0000-0000-0000-0000000000f0'::uuid
       OR uri LIKE 'fixture://pkb-proto-%'
),
known_dev_claims AS (
    SELECT id, entity_id
    FROM secretary.claims
    WHERE source_id IN (SELECT id FROM known_dev_sources)
),
known_dev_entities AS (
    SELECT entity_id AS id FROM known_dev_claims
    UNION
    SELECT id
    FROM secretary.entities
    WHERE id IN (
        '14000000-0000-0000-0000-000000000001'::uuid,
        '14000000-0000-0000-0000-000000000002'::uuid
    )
),
known_dev_relations AS (
    SELECT id
    FROM secretary.entity_relations
    WHERE source_id IN (SELECT id FROM known_dev_sources)
       OR id IN (
          '14000000-0000-0000-0000-000000000101'::uuid,
          '14000000-0000-0000-0000-000000000102'::uuid
       )
)
