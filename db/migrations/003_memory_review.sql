-- Trusted human-only reviewer login receives this group separately.
-- The API's secretary_api login must NOT receive this role.
SET search_path = secretary, pg_catalog;
CREATE ROLE secretary_review_writer NOLOGIN;
GRANT USAGE ON SCHEMA secretary TO secretary_review_writer;
GRANT SELECT ON pending_claims, entities, sources, approvals TO secretary_review_writer;
-- Only memory_write approvals can be created using these columns;
-- external_action approvals require task_id, which is deliberately excluded.
GRANT INSERT (approval_kind, pending_claim_id, status, scope, requested_by,
              decided_by, reason, decided_at, expires_at)
ON approvals TO secretary_review_writer;
-- Candidate producers and task writers cannot inherit this group.
