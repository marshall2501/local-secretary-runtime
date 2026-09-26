# PostgreSQL foundation (M0 / M1 / persistent-task schema)

## Design reviewed

Implementation baseline: design repo commit `f9fe00f4fda56864f73e16f62b3d272658afffe8`:

- [SecretaryCore-vNext](https://github.com/marshall2501/local-secretary-ai/blob/f9fe00f4fda56864f73e16f62b3d272658afffe8/docs/01_Architecture/SecretaryCore-vNext.md)
- [Memory-vNext](https://github.com/marshall2501/local-secretary-ai/blob/f9fe00f4fda56864f73e16f62b3d272658afffe8/docs/02_Database/Memory-vNext.md)
- [PrototypeVerticalSlice](https://github.com/marshall2501/local-secretary-ai/blob/f9fe00f4fda56864f73e16f62b3d272658afffe8/docs/03_Workflows/PrototypeVerticalSlice.md)

This is storage/provisioning, not a completed autonomous secretary or Memory API.
The design leaves concrete types and state machines open; the migrations make initial,
domain-neutral choices rather than importing the old PC-specific schema.

| Design requirement | Initial implementation |
| --- | --- |
| Structured source of truth + original archive | `entities`, `sources`; URI, hash, citation, retrieval time, confidentiality; original files stay outside DB/Git |
| Facts/observations/attributes separate from guesses | typed `claims`, separate `pending_claims` and `hypotheses`; evidence and source required |
| Effective time versus record time | `valid_from/to`, `recorded_at`, `supersedes_id`, retraction; `current_claims` excludes expired and superseded revisions |
| AI candidates require review | restricted candidate INSERT columns; accepted-candidate provenance trigger; no direct AI claim-write grant |
| Issues, hypotheses, decisions | separate tables, source references and decision history |
| Persistent task core | UUID tasks/steps, dependencies, checkpoint, due/next-run/retry time, revision, approval-wait state |
| Memory and external approval separation | `approval_kind`, distinct subject constraints, scope, decider, expiry; no application approval-write grants yet |
| Operation and verification history | actions/results with evidence, errors, actor, parameters, idempotency key, reversibility |
| Audit | linked `audit_events`; audit role INSERT-only; read/proposal/execution events must be emitted by future services |

`current_claims` means currently effective records, **not all verified facts**: callers must filter
`verification_status` and preserve uncertainty. A superseding revision stops its predecessor from
being current from the successor's `valid_from` onward, even if the successor later expires.
Multiple values are allowed (attributes may be multi-valued); ambiguity resolution is not invented
by this schema. Point-in-time queries must constrain both effective time and `recorded_at`, and
only consider successors recorded by that time. These columns preserve revisions but are not a
complete automatic bitemporal history system: trusted writers must append corrections and close
effective periods transactionally instead of replacing old values.

`simple` full-text GIN supports basic token search, not Japanese morphological segmentation.
SQL exact filters/enumeration work independently; no vector database or cloud LLM required.

## Windows setup and start

Requirements: PowerShell 5.1+; Git; Docker Desktop running **Linux containers**; Compose with `up --wait`.
Run from the runtime checkout you intend to operate. The DB commands locate their own checkout;
the pre-existing general doctor still uses its original `D:\AI` paths.

```powershell
cd D:\AI\projects\local-secretary-runtime
.\scripts\db\postgres.ps1 -Action Setup
.\scripts\db\postgres.ps1 -Action Start
.\scripts\db\postgres.ps1 -Action Migrate
.\scripts\db\postgres.ps1 -Action Doctor
# Optional combined environment + database checks:
.\scripts\doctor\doctor.ps1 -Postgres
```

Setup creates only ignored `.env.postgres` (port) and `secrets/postgres-password.txt`
(cryptographically random password). Existing `.env`, config and secrets are preserved.
The password is mounted as a Compose secret, not in compose YAML or command arguments.
Protect the secrets directory with local user-only NTFS permissions; Compose secrets are local
file mounts, not encryption at rest. Back up the password separately in secure storage.
Never paste expanded environment files, secret contents or raw private DB output into an issue.

Defaults:

| Item | Value |
| --- | --- |
| Compose project | `local-secretary-runtime-db` |
| Service | `secretary-postgres` |
| Volume | `local-secretary-runtime-db_secretary_pgdata` |
| Network | `local-secretary-runtime-db_secretary_db` |
| Host binding | `127.0.0.1:55432` only |
| DB / provisioning user | `secretary` / `secretary_admin` |
| PostgreSQL image | `postgres:17-bookworm` (major fixed, minor/security updates available) |

No `container_name`, external/shared volume or network, existing-project dependency, or Docker prune.
Scripts always pass the project, compose file and dedicated env file explicitly. Existing container
project/checkout/service labels and named volume/network labels are checked before DB operations.
Orphaned volumes/networks cause a refusal, not silent adoption: inspect them manually and recover
from a trusted backup to a fresh instance if ownership is uncertain. Direct Compose commands bypass
script guards; prefer the script.

Start reserves/tests the loopback port before starting; if unavailable, it stops without choosing
another port or stopping the owner. On first setup, use `-Action Setup -Port 55433` if needed.
For existing setup, edit only `LSA_DB_PORT=55433` in ignored `.env.postgres` before initial start.
Start does not silently recreate an already running service with a changed port: plan that restart
explicitly. A port can still be taken between preflight and Docker binding; Docker then fails safely.

Readiness uses `pg_isready`; Doctor additionally checks the actual localhost binding, TCP password
authentication, and migration history. It fails before migration, intentionally. Use Start then Migrate.
Changing the secret file after initialization does **not** change an existing DB password. A mismatch
is caught by Doctor; plan password rotation through PostgreSQL before updating the local secret.
Do not delete the volume to fix credentials. Keep admin credentials away from LLMs and future apps.

## Migrations, permissions, remaining service work

Migrate explicitly applies ordered `db/migrations/NNN_*.sql`, with an advisory transaction lock,
one transaction, `ON_ERROR_STOP`, and SHA-256 history. Reapplying is a no-op. Editing an applied
migration fails; add a new migration instead. Failure rolls back all pending migrations and their
history. Migration SQL is not tied to first-time image initialization, so it works on an existing volume.
No down migrations or auto-rollback that drops user data. Back up before schema changes.

Five NOLOGIN group roles are created: reader, candidate writer, memory writer, task writer and audit
writer. There are no application passwords/logins yet. Candidate writers can only insert unreviewed
candidates; memory writers are trusted service roles; task writers cannot decide approvals; audit
writers cannot edit/delete history. The provisioning account is a superuser and is not a safe runtime
identity. There is no multi-user row-level isolation yet. Do not connect untrusted code with admin.

Before enabling autonomous actions, implement and test:

- Memory Write Service: transactionally validate schema, evidence, duplicate/correction rules,
  reviewer authority, approval scope/expiry, and append audit events. An accepted review is not
  automatically an authorized write; explicit user corrections have a separate policy path.
- Policy/Executor: bind approval to exact operation/parameters, identity, risk, expiry and revocation;
  recheck immediately before external execution. An approval FK/high-risk presence check is **not**
  authorization. No external operation is executed by this stage.
- Task Manager: legal transitions, dependency-cycle detection, atomic claims/leases, optimistic
  revision checks, retry policy and completion verification. Unique idempotency keys prevent duplicate
  ledger entries; they cannot guarantee exactly-once external effects after a crash.
- Audit read access, redact sensitive parameters, implement archive ingestion/hash verification,
  privacy deletion with explicit FK-aware handling, and immutable correction history at service level.

## Backup and restore drill

```powershell
.\scripts\db\postgres.ps1 -Action Backup
# Use the exact dump path printed above; choose a NEW restore database name each time:
.\scripts\db\postgres.ps1 -Action Restore -BackupPath 'D:\AI\data\backup\example.dump' -RestoreDatabase secretary_restore_drill1
```

Default backups go to ignored `backups/` in this checkout. To write directly to the external data
directory, pass `-BackupPath 'D:\AI\data\backup\secretary-YYYYMMDD.dump'` to Backup. Existing files
are never overwritten. `pg_dump --format=custom` makes a consistent DB snapshot. Binary data moves
with `docker cp`, avoiding Windows PowerShell 5.1 redirection corruption. Dumps contain private data:
keep them out of Git, encrypt/protect storage, and set a retention/off-device backup policy.

Restore accepts only a new `secretary_restore_*` DB in the dedicated service. It uses a single
transaction, no `--clean`, no overwrite, and no production DB target. A failed restore may leave an
empty diagnostic DB; it is not automatically dropped. Restore only trusted dumps because database
archives can contain executable SQL. Existing production `secretary` remains unchanged.

After restoring, compare migration versions, entity/source/claim/task counts, source hash/archive
availability, and selected task IDs/status/checkpoints/approvals with the source. Run a recovery drill
before relying on backups. Production cutover is intentionally a separate operator procedure.
Dumps omit owners/ACLs and cluster-global roles; restored drill DBs are admin-only. For recovery to a
new cluster, initialize this version in an empty `secretary` DB to create group roles, then restore to
a new drill DB and deliberately reapply reviewed grants before a planned cutover. Secrets, original
source archive files and local config require separate coordinated backup; pg_dump includes tasks
but not those files or Docker configuration. Pause application writes if archive/DB cross-consistency
is needed. Never copy a live PostgreSQL data directory as a logical backup.

## Validation

```powershell
.\tests\db\test-static.ps1
# Uses the configured port, which must be free. No production service should occupy it.
.\tests\db\test-integration.ps1
```

Integration tests use a random `local-secretary-test-*` project, synthetic data and dedicated resources.
They reject occupied ports and applied-migration checksum drift; apply/reapply SQL; reject unreviewed promotion, invalid periods, cross-task steps and missing
high-risk approval references; inspect role boundaries; verify waiting-task state across restart and
binary backup/restore; reject existing restore targets. Finally they remove only that random project's
containers/volume/network and compare all other container IDs, status, start times and restart counts.
Synthetic dump files are ignored and retained locally; the shared downloaded PostgreSQL image remains.
Do not run tests concurrently with manual lifecycle changes to other containers.

Local validation on 2026-09-26: static checks passed under Windows PowerShell 5.1 and PowerShell
7.6.5. Docker Desktop Linux-container integration passed on Windows; the final expanded suite
(including port collision and checksum drift) passed under Windows PowerShell 5.1. These runs used
an isolated Codex working checkout, not `D:\AI\projects\local-secretary-runtime`. Test containers,
volumes and networks were removed; existing `yt-topic-search` container IDs, running state, start
times and restart counts were unchanged. The downloaded image and ignored synthetic test dumps
remain in local test storage. No production secretary database was provisioned in `D:\AI`.
GitHub Actions defines a separate Linux integration job and Windows static job; local Windows
results do not imply those CI jobs have passed.

References: [Compose secrets](https://docs.docker.com/compose/how-tos/use-secrets/),
[PostgreSQL pg_restore](https://www.postgresql.org/docs/17/app-pgrestore.html).
