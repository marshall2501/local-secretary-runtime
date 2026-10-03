# Runtime database boundary and promotion plan

The logical subsystem boundary is not the physical database boundary.

## Adopted target

Normal structured runtime data uses one physical PostgreSQL database, `secretary`, by default. The current `secretary` schema is retained during migration; source package layout is not mechanically mirrored as PostgreSQL schemas.

Logical ownership is:

| Owner | Tables / views |
| --- | --- |
| PKB | `entities`, `sources`, `claims`, `current_claims`, `pending_claims`, `issues`, `hypotheses`, `decisions`, `entity_relations`, `pkb_*` receipts/intake |
| RITSUKO | `tasks`, `task_steps`, task dependency tables, `approvals`, `actions`, `results`, `audit_events` |
| Finance | `finance_*` |
| MAGI runtime settings | `llm_profiles`, `magi_member_assignments` |
| Service Connections | `service_connections` |
| Service Billing | `service_billing_profiles` |

Intentional cross-owner references include RITSUKO Task/Result records pointing at PKB Entity/Source data and MAGI/Billing settings pointing at Service Connections. These are consistency boundaries, not evidence that the subsystems are the same application component.

## Current physical state

- Operational database: `secretary` with production migrations in `db/migrations/`.
- Historical isolated database: `secretary_pkb_proto_20260927`.
- Historical isolated migrations: `db/isolated/pkb_proto/`.
- Isolated verification scripts: `scripts/pkb/isolated/`.

Isolated migrations are regression/evidence assets. Never apply them directly to `secretary`.

## Promotion sequence

1. Run `scripts/db/compare-runtime-databases.ps1` read-only on the sub-PC.
2. Record table presence/counts and migration history differences.
3. Build production migrations in `db/migrations/` from the accepted final schema, using production group roles rather than the prototype writer.
4. Back up `secretary` and verify restore before any live schema/data migration.
5. Apply the production migrations to a restored/isolated target first.
6. Promote data with explicit collision checks and row-count verification.
7. Verify standalone PKB, RITSUKO, Finance, MAGI settings, Service Connections, Service Billing, API and MCP read paths.
8. Only then change the daily launcher from the isolated DB to operational `secretary`.

Physical separation can be reconsidered later for measured load, retention, backup/restore, privilege, or lifecycle reasons. Finance/time-series data is the most likely future candidate, but it is not split pre-emptively.
