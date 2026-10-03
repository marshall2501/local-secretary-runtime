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


## Production promotion design (2026-10-03)

The sub-PC inventory confirmed that the operational `secretary` database is still at production migrations 001-004 while the isolated database has the prototype history through 026. Promotion therefore uses a **final-schema reconstruction**, not a replay of `db/isolated/pkb_proto/005-026`.

### Production migration set

The proposed production history starts from the already-applied 001-004 and collapses the accepted prototype outcome into concern-level migrations:

| Production migration | Purpose | Prototype material folded in | Explicitly not carried forward |
| --- | --- | --- | --- |
| `005_pkb_runtime_schema.sql` | Final PKB tables/columns/indexes used by the current daily PKB and Memory Intake | 005, 006, 008-015 schema portions, 019 | 007 fictional episode corpus table; 014 fixture INSERTs; 015 fixture-specific UPDATEs; prototype DB guards/grants |
| `006_finance_runtime_schema.sql` | MoneyForward structured finance tables and indexes | 016 schema | prototype writer grant |
| `007_runtime_settings_schema.sql` | Final Service Connection, MAGI profile/assignment, and Service Billing schema | accepted end state of 020-026 | intermediate Provider Usage table/name, legacy Connection backfill steps, legacy LLM provider/endpoint/credential columns |
| `008_runtime_privileges.sql` | Production group-role and DML grants for the new tables | accepted privilege intent from 009, 014, 016-020, 024-026 | `secretary_pkb_proto_writer_20260927` and any prototype-only grant |

These files are normal `db/migrations/NNN_*.sql` files. They do **not** write `secretary.schema_migrations` themselves because `scripts/db/migrate.sh` owns checksum/history recording and wraps the production migration set in one transaction.

### 005 final PKB schema

`005_pkb_runtime_schema.sql` keeps the structures still referenced by current runtime code:

- `pkb_input_receipts` and `pkb_correction_receipts` remain as compatibility tables while the existing daily write/correction paths still use them.
- `pkb_pending_intake` is created directly in its final accepted shape, including review timestamp, accepted Source/Claim links, interpreter provenance, and `memory_context`.
- `claims.semantic_kind` and `claims.memory_metadata` are added.
- `entity_relations` is created directly with `relation_role`; the current/historical indexes and one-active-State index are created without fixture data.
- `pkb_memory_intakes` and `pkb_memory_candidate_receipts` are created in their final Memory Intake v1 shape.
- `pkb_episode_receipts` is not a production table. It exists only to make the bundled ten-episode fictional acceptance corpus replayable.

The migration contains **no Entity, Source, Claim, Relation, or other fixture INSERT/UPDATE**. In particular the fixed UUIDs and fictional `メインPC` component rows from isolated migration 014 and the fixture renames/role updates from 015 remain isolated evidence only.

### 006 final Finance schema

`006_finance_runtime_schema.sql` creates the accepted final form of:

- `finance_import_batches`
- `finance_accounts`
- `finance_categories`
- `finance_transactions`
- `finance_transaction_versions`

with the existing unique keys, provenance/version references, and date/account/category indexes. It contains no imported transactions; the 2,270-row current data set is handled by the later data-promotion step, not by a schema migration.

### 007 final settings / connection schema

Because the operational DB has none of the 020-026 tables, production does not reproduce their intermediate evolution. It creates the accepted final model directly:

1. `service_connections` in the post-026 shape, including `connection_type`, `auth_data`, `connection_role`, capabilities, non-secret config, and `external_credentials`.
2. `llm_profiles` referencing `connection_id`, with model/runtime settings, context window, generation budget, retry codes, enable flag, and timestamps.
3. `magi_member_assignments` with the three logical member names, profile reference, weight, timeout, and within-turn retry flag.
4. `service_billing_profiles` with an explicit `connection_id`.

The production `llm_profiles` table does **not** recreate the obsolete `provider`, `endpoint`, or `credential_env` snapshot columns from migration 020. Current runtime reads provider/endpoint/credential information from `service_connections`; carrying the intermediate duplicate source of truth into a new production schema would preserve migration history rather than the accepted design. Provider-specific Ollama range validation remains in application validation; DB checks keep the numeric fields nullable with bounded ranges because a PostgreSQL CHECK cannot safely depend on another table's adapter row.

### Production role boundary

Existing roles continue to own their current responsibilities:

- `secretary_memory_writer` — PKB write boundary, extended only to the new PKB tables.
- `secretary_task_writer` / `secretary_audit_writer` — existing RITSUKO task/audit boundary.
- `secretary_candidate_writer` and `secretary_review_writer` — unchanged existing API/review boundaries.

New NOLOGIN group roles are proposed for tables that did not exist in 001-004:

- `secretary_finance_writer`
- `secretary_magi_settings_writer`
- `secretary_connection_writer`
- `secretary_billing_writer`

Runtime roles receive DML only; PostgreSQL object ownership remains with the migration/admin boundary rather than being transferred to application logins. The current `secretary_reader` role is **not automatically granted the new Finance, Connection/auth, MAGI, or Billing tables**, so the existing Secretary API/MCP login does not gain those data surfaces merely because the schema was promoted.

The eventual localhost daily-runtime login is provisioned separately from migrations using a Git-ignored local secret and only the group roles required by that process. The prototype login `secretary_pkb_proto_writer_20260927` is never promoted or granted access to `secretary`.

### Data promotion is separate from schema migration

Schema promotion and data promotion are separate operations. No production migration contains a copy of isolated personal/configuration data.

Default promotion candidates are:

| Data | Default treatment |
| --- | --- |
| Finance import/account/category/transaction/version data | promote after collision/FK checks |
| Service Connections, LLM Profiles, MAGI assignments, Service Billing Profiles | promote as current runtime configuration after schema creation |
| PKB Relations / Pending / Memory Intake / compatibility receipts and their referenced base PKB rows | build an explicit referential-closure manifest first; promote only selected user data |
| existing operational `secretary` PKB/RITSUKO rows | preserve in place; never replace wholesale from isolated DB |
| RITSUKO development Task/Action/Result/Audit history from the isolated DB | do not promote by default |
| `pkb_episode_receipts` and bundled acceptance corpus | never promote |
| fixed 014 component fixture UUIDs/source/relations and 015 fixture normalization DML | never promote |

A row is **not** discarded merely because it has `fictional_only=true` or a `fixture://` URI. The current daily prototype deliberately writes user-entered daily PKB data through `fixture://daily-pkb/...` and marks it fictional as a safety guard. Fixture filtering must therefore identify known bundled/dev fixtures positively (dataset ID, fixed IDs, known test provenance), not use a broad `fictional_only` predicate that could silently drop user-entered records.

### Collision and referential-closure rules

The data-promotion tool must be fail-closed:

1. Compute the selected source rows and their full FK closure before any target write.
2. Same primary key + equivalent canonical content: reuse/skip.
3. Same primary key + different content: hard fail and report table/key only.
4. Natural/unique key collision with different IDs: hard fail for explicit reconciliation; no last-writer-wins UPSERT.
5. Insert in dependency order and validate FK closure before commit.
6. Compare source-selected / inserted / reused / rejected counts and run orphan checks after the transaction.
7. Do not print `service_connections.auth_data`, API keys, passwords, tokens, personal source text, or Finance row contents in normal logs. Secret-bearing Connection rows are copied locally DB-to-DB and validated by ID/count/presence metadata only.

### Safety / rollback gate before live `secretary`

No live DB migration is allowed directly from GitHub completion.

1. Create a fresh pre-change custom-format backup of `secretary`.
2. Validate that backup by restoring it and checking migration history plus representative counts/content.
3. Run 005-008 and data promotion first against an isolated disposable PostgreSQL target. A separate test cluster/project is preferred because PostgreSQL roles are cluster-global.
4. Verify schema, constraints, FK closure, row counts, group-role privileges, Secret non-disclosure, and current application queries against that target.
5. Run the daily PKB/RITSUKO/Finance/MAGI/Connections/Billing/API/MCP regression against the promoted test target with a production-style restricted login.
6. Before live apply, record an exact recovery procedure. A failed unapplied migration is transaction-rolled-back by the migration runner; a bad migration/data promotion discovered after commit is recovered from the validated pre-change backup, not by ad-hoc DELETE/DROP repair.
7. Only after these checks may `secretary` be migrated and the daily launcher switched from `secretary_pkb_proto_20260927` to `secretary`.

The existing restore command intentionally restores to a new `secretary_restore_*` database and does not overwrite the live DB. Before the live gate, migration/testing tooling must therefore support a non-live restored/disposable target and the recovery/cutover procedure must be executable, not only described.

### Code cutover boundary

Creating the production schema does not by itself change the daily runtime. Current PKB services and `infrastructure/postgres/pkb_runtime.py` intentionally reject anything other than the isolated DB/prototype writer. Those guards remain until the promoted schema/data has passed isolated regression. The later cutover changes the connection/provisioning boundary and then removes prototype-only `fixture://`/fictional-write restrictions from the actual production Memory Intake path deliberately; it does not weaken the guards early merely to make the migration test pass.
