# Runtime database boundary and clean rebuild plan

The logical subsystem boundary is not the physical database boundary.

## Adopted target

Normal structured runtime data uses one physical PostgreSQL database, `secretary`, by default. The improved source layout remains responsibility-oriented; package layout is not mechanically mirrored as PostgreSQL databases or schemas.

Logical ownership remains useful for design and code responsibility:

| Owner | Tables / views |
| --- | --- |
| PKB | `entities`, `sources`, `claims`, `current_claims`, `pending_claims`, `issues`, `hypotheses`, `decisions`, `entity_relations`, `pkb_*` receipts/intake |
| RITSUKO | `tasks`, `task_steps`, task dependency tables, `approvals`, `actions`, `results`, `audit_events` |
| Finance | `finance_*` |
| MAGI runtime settings | `llm_profiles`, `magi_member_assignments` |
| Service Connections | `service_connections` |
| Service Billing | `service_billing_profiles` |

Intentional cross-owner references include RITSUKO Task/Result records pointing at PKB Entity/Source data and MAGI/Billing settings pointing at Service Connections. These references preserve consistency; they are not a reason to duplicate persistence or access code.

## Internal runtime access boundary

The local internal runtime does not use fine-grained writer-role segmentation as a product goal. Database administration/recovery credentials remain separate from normal runtime access, while the normal runtime may use one shared login that can read and write the tables required by PKB, RITSUKO, Finance, MAGI settings, Service Connections and Service Billing.

External exposure and consequential actions are controlled where they are actually invoked: Web/API/MCP route and tool surfaces, authentication, and RITSUKO Policy / Approval / Executor. Additional DB-role separation is introduced only when a concrete operational requirement justifies it.

## Current physical state

- Existing operational database: `secretary`, historically built from the old production migration series.
- Historical isolated database: `secretary_pkb_proto_20260927`, which contains the pre-refactor daily PKB/RITSUKO/Finance/settings data.
- Historical isolated migrations: `db/isolated/pkb_proto/`.
- Isolated verification scripts: `scripts/pkb/isolated/`.
- Improved runtime source layout: `ritsuko/`, `pkb/`, `capabilities/`, `integrations/`, `infrastructure/`, `interfaces/`, `bootstrap/`.

The source refactor is retained. Restoring pre-refactor functionality means making those capabilities work on this layout, not reverting the layout.

## Clean rebuild sequence

1. Preserve the current `secretary` and `secretary_pkb_proto_20260927` as source/rollback databases; do not overwrite them during rehearsal.
2. Create a separate replacement production database from the schema currently required by the improved runtime.
3. Keep old production/prototype migration series as history; do not reproduce obsolete role/prototype complexity merely to make the new database look historically identical.
4. Copy data needed for current daily use:
   - operational data that exists only in the old `secretary`;
   - personal PKB data plus its referential closure from the isolated database;
   - Finance data;
   - Service Connections, LLM Profiles, MAGI Assignments and Service Billing Profiles.
5. Exclude known development fixtures by positive identification. Do not discard rows only because older daily code used fixture-shaped URIs.
6. Verify collisions, foreign references, row counts, settings references and Secret non-disclosure.
7. Run the existing daily capabilities against the replacement database: PKB, RITSUKO, MAGI, Finance, Service Connections, Service Billing, Web/System Debug, Secretary API and MCP read paths.
8. Cut over only after the replacement database passes those checks. Keep the old databases available for rollback until the sub-PC result is accepted.

Physical separation can still be reconsidered later for measured load, retention, backup/restore, lifecycle, or a concrete security boundary. It is not introduced merely to mirror subsystem names.
