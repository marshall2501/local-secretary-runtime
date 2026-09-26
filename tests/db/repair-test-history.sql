\set ON_ERROR_STOP on
-- Synthetic integration-test ledger only. Never repair real migration drift this way.
UPDATE secretary.schema_migrations SET sha256=:'checksum' WHERE version='001_memory.sql';
