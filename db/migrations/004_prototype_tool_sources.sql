-- G1 prototype: permit the existing task-writer service to record its own
-- tool-result Source metadata. Do not grant memory-writer or reviewer privileges.
-- API code must only use this grant for generated tool://prototype sources.
SET search_path = secretary, pg_catalog;

GRANT INSERT (source_type, uri, citation, retrieved_at, confidentiality, metadata)
ON secretary.sources TO secretary_task_writer;
