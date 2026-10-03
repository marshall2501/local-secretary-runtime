# Isolated PKB prototype migrations

This directory preserves the historical migrations used by the isolated database `secretary_pkb_proto_20260927`.

These migrations are evidence and regression assets for the isolated prototype. They are **not** production migrations for the operational `secretary` database and must not be applied there directly.

Accepted capabilities are promoted separately into `db/migrations/` with production roles, guards, and migration semantics. The current database-boundary decision is documented in the design repository as D-27.
