\set ON_ERROR_STOP on
UPDATE secretary.schema_migrations SET sha256=repeat('0',64) WHERE version='001_memory.sql';
