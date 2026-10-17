# Runtime database boundary — current single-master architecture

PostgreSQL stores current structured PKB, RITSUKO Task/Action/Result, Finance,
MAGI/Connection/Billing and runtime Policy settings in one operational database:
`secretary`. Logical subsystem ownership does not require separate databases.

## Current access boundary

- Normal localhost Runtime: `secretary_daily_runtime` using the configured local port
  and a protected secret file. Its database name is `secretary` except for an
  explicitly selected `secretary_rebuild_*` disposable/recovery target.
- Recovery/admin: `secretary_admin`, kept separate from normal application access.
- External API/MCP and high-impact Action: application route, identity, Policy,
  Approval, Source/Result and audit boundaries — not a second feature database.
- MAGI semantic judgment: replaceable `standard` / `llm` strategies.
  Task persistence and state transitions remain owned by shared RITSUKO.

## Migrations and test isolation

`db/migrations/` is the only tracked operational schema history. Disposable PostgreSQL
tests use the same DDL series on their own freshly provisioned service/database.
They must never target the user's `secretary` or personal files. Fictional Workbench
fixtures may still exist, but are neither a second runtime DB nor a second migration
series.

The historical `db/isolated/pkb_proto/`, `scripts/pkb/isolated/`, prototype
promotion/transfer tools and old Task import facade were removed from tracked
current source on 2026-10-17. Their history remains available in Git.
The user's historical database itself, if present on their sub-PC, has **not**
been dropped or altered by these GitHub changes.

## Operational recovery and safety

`scripts/db/postgres.ps1` retains Setup/Start/Migrate/Doctor/Backup/Restore.
A restore drill uses a distinct target and must not overwrite the running DB.
`scripts/db/initialize-fresh-production.ps1` is reserved for explicitly named
fresh schema targets and is not a daily launcher.

This source migration is **not** a production DB migration, automatic deployment,
service stop or daily Vue cutover. Those changes require their own acceptance and
operation-specific authorization. See the design repository STATUS and the
2026-10-17 Level C retirement plan for verification evidence.
