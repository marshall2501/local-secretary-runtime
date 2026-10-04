"""Transfer the daily data needed by the rebuilt production runtime.

Source: historical daily isolated database (read-only use).
Target: disposable/rebuilt production database at migration 008.

The transfer reuses the same PKB referential-closure definition as the promotion
manifest, copies complete Finance and runtime settings, validates references, and
prints counts only. No personal values or credential payloads are printed.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import sys

import psycopg

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.db.runtime_settings_transfer import copy_settings, connect_admin
from scripts.db.transfer_common import copy_all_rows, copy_query_rows, one


CLOSURE_SQL = ROOT / "scripts" / "db" / "pkb_promotion_closure.sql"

FINANCE_TABLES = (
    ("finance_import_batches", ("id",), "imported_at,id"),
    ("finance_accounts", ("id",), "created_at,id"),
    ("finance_categories", ("id",), "created_at,id"),
    ("finance_transactions", ("id",), "transaction_date,id"),
    ("finance_transaction_versions", ("id",), "observed_at,id"),
)


def _read_closure_sql() -> str:
    if not CLOSURE_SQL.is_file():
        raise RuntimeError("shared PKB promotion closure SQL is missing")
    value = CLOSURE_SQL.read_text(encoding="utf-8").strip()
    if not value.startswith("WITH RECURSIVE"):
        raise RuntimeError("invalid shared PKB promotion closure SQL")
    if any(token in value.upper() for token in (
        " INSERT ", " UPDATE ", " DELETE ", " ALTER ", " CREATE ", " DROP ", " TRUNCATE "
    )):
        raise RuntimeError("PKB promotion closure must remain read-only")
    return value


def _validate_endpoints(source_cur, target_cur) -> None:
    if one(source_cur, "SELECT current_database()") != "secretary_pkb_proto_20260927":
        raise RuntimeError("unexpected data source database")
    if one(
        source_cur,
        """SELECT count(*) FROM secretary.schema_migrations
           WHERE version='026_service_billing.sql'""",
    ) != 1:
        raise RuntimeError("isolated source is not at migration 026")
    if one(target_cur, "SELECT current_database()") != "secretary":
        raise RuntimeError("unexpected data target database")
    if one(
        target_cur,
        """SELECT count(*) FROM secretary.schema_migrations
           WHERE version='008_runtime_privileges.sql'""",
    ) != 1:
        raise RuntimeError("production target is not at migration 008")


def _copy_pkb(source_cur, target_cur, closure: str) -> dict[str, dict[str, int]]:
    results: dict[str, dict[str, int]] = {}

    results["entities"] = copy_query_rows(
        source_cur,
        target_cur,
        table="entities",
        key_columns=("id",),
        source_query_template=closure + """
SELECT {columns}
FROM secretary.entities t
JOIN entity_closure c ON c.id=t.id
ORDER BY t.created_at,t.id
""",
        update_on_collision=True,
    )
    results["sources"] = copy_query_rows(
        source_cur,
        target_cur,
        table="sources",
        key_columns=("id",),
        source_query_template=closure + """
SELECT {columns}
FROM secretary.sources t
JOIN source_closure c ON c.id=t.id
ORDER BY t.recorded_at,t.id
""",
        update_on_collision=True,
    )
    results["pending_claims"] = copy_query_rows(
        source_cur,
        target_cur,
        table="pending_claims",
        key_columns=("id",),
        source_query_template=closure + """
SELECT {columns}
FROM secretary.pending_claims t
WHERE t.id IN (
    SELECT c.pending_claim_id
    FROM secretary.claims c
    JOIN claim_closure cc ON cc.id=c.id
    WHERE c.pending_claim_id IS NOT NULL
)
ORDER BY t.recorded_at,t.id
""",
        update_on_collision=True,
    )

    claim_query = closure + """,
claim_depth(id, depth) AS (
    SELECT c.id, 0
    FROM secretary.claims c
    JOIN claim_closure cc ON cc.id=c.id
    WHERE c.supersedes_id IS NULL
       OR NOT EXISTS (
           SELECT 1 FROM claim_closure parent
           WHERE parent.id=c.supersedes_id
       )
    UNION ALL
    SELECT c.id, d.depth + 1
    FROM secretary.claims c
    JOIN claim_closure cc ON cc.id=c.id
    JOIN claim_depth d ON d.id=c.supersedes_id
)
SELECT {columns}
FROM secretary.claims t
JOIN claim_depth d ON d.id=t.id
ORDER BY d.depth,t.recorded_at,t.id
"""
    results["claims"] = copy_query_rows(
        source_cur,
        target_cur,
        table="claims",
        key_columns=("id",),
        source_query_template=claim_query,
        update_on_collision=True,
    )

    relation_query = closure + """,
relation_depth(id, depth) AS (
    SELECT r.id, 0
    FROM secretary.entity_relations r
    JOIN relation_closure rc ON rc.id=r.id
    WHERE r.supersedes_id IS NULL
       OR NOT EXISTS (
           SELECT 1 FROM relation_closure parent
           WHERE parent.id=r.supersedes_id
       )
    UNION ALL
    SELECT r.id, d.depth + 1
    FROM secretary.entity_relations r
    JOIN relation_closure rc ON rc.id=r.id
    JOIN relation_depth d ON d.id=r.supersedes_id
)
SELECT {columns}
FROM secretary.entity_relations t
JOIN relation_depth d ON d.id=t.id
ORDER BY d.depth,t.recorded_at,t.id
"""
    results["entity_relations"] = copy_query_rows(
        source_cur,
        target_cur,
        table="entity_relations",
        key_columns=("id",),
        source_query_template=relation_query,
        update_on_collision=True,
    )
    results["pkb_pending_intake"] = copy_query_rows(
        source_cur,
        target_cur,
        table="pkb_pending_intake",
        key_columns=("id",),
        source_query_template=closure + """
SELECT {columns}
FROM secretary.pkb_pending_intake t
JOIN candidate_pending c ON c.id=t.id
ORDER BY t.recorded_at,t.id
""",
        update_on_collision=True,
    )
    results["pkb_input_receipts"] = copy_query_rows(
        source_cur,
        target_cur,
        table="pkb_input_receipts",
        key_columns=("input_id",),
        source_query_template=closure + """
SELECT {columns}
FROM secretary.pkb_input_receipts t
JOIN input_receipt_closure c ON c.input_id=t.input_id
ORDER BY t.created_at,t.input_id
""",
        update_on_collision=True,
    )
    results["pkb_correction_receipts"] = copy_query_rows(
        source_cur,
        target_cur,
        table="pkb_correction_receipts",
        key_columns=("input_id",),
        source_query_template=closure + """
SELECT {columns}
FROM secretary.pkb_correction_receipts t
JOIN correction_receipt_closure c ON c.input_id=t.input_id
ORDER BY t.created_at,t.input_id
""",
        update_on_collision=True,
    )
    results["pkb_memory_intakes"] = copy_query_rows(
        source_cur,
        target_cur,
        table="pkb_memory_intakes",
        key_columns=("input_id",),
        source_query_template=closure + """
SELECT {columns}
FROM secretary.pkb_memory_intakes t
JOIN candidate_memory_intakes c ON c.input_id=t.input_id
ORDER BY t.created_at,t.input_id
""",
        update_on_collision=True,
    )
    results["pkb_memory_candidate_receipts"] = copy_query_rows(
        source_cur,
        target_cur,
        table="pkb_memory_candidate_receipts",
        key_columns=("input_id", "candidate_id"),
        source_query_template=closure + """
SELECT {columns}
FROM secretary.pkb_memory_candidate_receipts t
JOIN candidate_memory_receipts c
  ON c.input_id=t.input_id AND c.candidate_id=t.candidate_id
ORDER BY t.input_id,t.candidate_id
""",
        update_on_collision=True,
    )
    return results


def _copy_finance(source_cur, target_cur) -> dict[str, dict[str, int]]:
    results = {}
    for table, keys, order_by in FINANCE_TABLES:
        if one(target_cur, f"SELECT count(*) FROM secretary.{table}") != 0:
            raise RuntimeError(f"target finance table is not empty: {table}")
        results[table] = copy_all_rows(
            source_cur,
            target_cur,
            table=table,
            key_columns=keys,
            order_by=order_by,
        )
    return results


def _validate_references(cur) -> None:
    checks = {
        "claim_entity_or_source": """
            SELECT count(*) FROM secretary.claims c
            LEFT JOIN secretary.entities e ON e.id=c.entity_id
            LEFT JOIN secretary.sources s ON s.id=c.source_id
            WHERE e.id IS NULL OR s.id IS NULL
        """,
        "relation_endpoint_or_source": """
            SELECT count(*) FROM secretary.entity_relations r
            LEFT JOIN secretary.entities a ON a.id=r.subject_entity_id
            LEFT JOIN secretary.entities b ON b.id=r.object_entity_id
            LEFT JOIN secretary.sources s ON s.id=r.source_id
            WHERE a.id IS NULL OR b.id IS NULL OR s.id IS NULL
        """,
        "finance_account": """
            SELECT count(*) FROM secretary.finance_transactions t
            LEFT JOIN secretary.finance_accounts a ON a.id=t.account_id
            WHERE t.account_id IS NOT NULL AND a.id IS NULL
        """,
        "finance_category": """
            SELECT count(*) FROM secretary.finance_transactions t
            LEFT JOIN secretary.finance_categories c ON c.id=t.category_id
            WHERE t.category_id IS NOT NULL AND c.id IS NULL
        """,
        "llm_connection": """
            SELECT count(*) FROM secretary.llm_profiles p
            LEFT JOIN secretary.service_connections c ON c.id=p.connection_id
            WHERE c.id IS NULL
        """,
        "magi_profile": """
            SELECT count(*) FROM secretary.magi_member_assignments a
            LEFT JOIN secretary.llm_profiles p ON p.id=a.profile_id
            WHERE p.id IS NULL
        """,
        "billing_connection": """
            SELECT count(*) FROM secretary.service_billing_profiles b
            LEFT JOIN secretary.service_connections c ON c.id=b.connection_id
            WHERE c.id IS NULL
        """,
    }
    failed = [name for name, query in checks.items() if one(cur, query) != 0]
    if failed:
        raise RuntimeError("referential validation failed: " + ",".join(failed))


def transfer(source, target, *, commit: bool) -> dict:
    closure = _read_closure_sql()
    try:
        with source.cursor() as source_cur, target.cursor() as target_cur:
            _validate_endpoints(source_cur, target_cur)
            pkb = _copy_pkb(source_cur, target_cur, closure)
            finance = _copy_finance(source_cur, target_cur)

        settings = copy_settings(source, target, require_empty=True)

        with target.cursor() as target_cur:
            _validate_references(target_cur)

        if commit:
            target.commit()
        else:
            target.rollback()
    except Exception:
        target.rollback()
        raise

    return {
        "pkb": pkb,
        "finance": finance,
        "settings": settings,
        "committed": bool(commit),
    }


def _count_summary(group: dict[str, dict[str, int]]) -> tuple[int, int, int]:
    source = sum(item["source"] for item in group.values())
    inserted = sum(item["inserted"] for item in group.values())
    updated = sum(item["updated"] for item in group.values())
    return source, inserted, updated


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-port", type=int, required=True)
    parser.add_argument("--target-port", type=int, required=True)
    parser.add_argument("--secret-file", type=Path, required=True)
    parser.add_argument("--commit", action="store_true")
    args = parser.parse_args()

    if not 1024 <= args.source_port <= 65535 or not 1024 <= args.target_port <= 65535:
        raise RuntimeError("invalid PostgreSQL port")
    if args.source_port == args.target_port:
        raise RuntimeError("source and rebuild target ports must differ")
    if not args.secret_file.is_file():
        raise RuntimeError("local PostgreSQL secret is missing")
    password = args.secret_file.read_text(encoding="utf-8").strip()
    if len(password) < 32:
        raise RuntimeError("local PostgreSQL secret is invalid")

    with connect_admin(
        args.source_port, "secretary_pkb_proto_20260927", password
    ) as source:
        with connect_admin(args.target_port, "secretary", password) as target:
            result = transfer(source, target, commit=args.commit)

    pkb_source, pkb_inserted, pkb_updated = _count_summary(result["pkb"])
    finance_source, finance_inserted, finance_updated = _count_summary(result["finance"])
    settings = result["settings"]
    print("Production data transfer: " + ("COMMIT" if result["committed"] else "ROLLBACK rehearsal"))
    print(
        f"PKBRows={pkb_source} PKBInserted={pkb_inserted} PKBUpdated={pkb_updated} "
        f"FinanceRows={finance_source} FinanceInserted={finance_inserted} "
        f"FinanceUpdated={finance_updated}"
    )
    print(
        "Connections={service_connections} Profiles={llm_profiles} "
        "Assignments={magi_member_assignments} BillingProfiles={service_billing_profiles} "
        "AuthConfigured={auth_configured}".format(**settings)
    )
    print("PASS: selected daily data transferred without printing personal or credential values.")


if __name__ == "__main__":
    main()
