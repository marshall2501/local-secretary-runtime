"""Copy runtime settings from the historical isolated DB into production.

No row values are printed. Service Connection auth_data is transferred in-memory
through parameterized psycopg statements and is never serialized to a file.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import sys

import psycopg

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.db.transfer_common import copy_query_rows, one


TABLES = (
    (
        "service_connections",
        (
            "id", "display_name", "adapter_key", "endpoint", "credential_ref",
            "account_label", "capabilities", "nonsecret_config", "auth_data",
            "connection_type", "connection_role", "enabled", "created_at",
            "updated_at",
        ),
        ("id",),
        "id",
    ),
    (
        "llm_profiles",
        (
            "id", "display_name", "connection_id", "model",
            "context_window_tokens", "ollama_num_predict", "retry_http_codes",
            "enabled", "created_at", "updated_at",
        ),
        ("id",),
        "id",
    ),
    (
        "magi_member_assignments",
        (
            "member", "profile_id", "enabled", "weight", "timeout_seconds",
            "retry_within_turn", "updated_at",
        ),
        ("member",),
        "member",
    ),
    (
        "service_billing_profiles",
        (
            "id", "display_name", "connection_id", "enabled", "created_at",
            "updated_at",
        ),
        ("id",),
        "id",
    ),
)


def connect_admin(port: int, database: str, password: str):
    return psycopg.connect(
        host="127.0.0.1",
        port=port,
        dbname=database,
        user="secretary_admin",
        password=password,
        connect_timeout=5,
        autocommit=False,
    )


def validate_source(cur) -> None:
    if one(cur, "SELECT current_database()") != "secretary_pkb_proto_20260927":
        raise RuntimeError("unexpected settings source database")
    if one(
        cur,
        """SELECT count(*) FROM secretary.schema_migrations
           WHERE version='026_service_billing.sql'""",
    ) != 1:
        raise RuntimeError("isolated settings source is missing migration 026")


def validate_target(cur, expected_database: str = "secretary") -> None:
    if one(cur, "SELECT current_database()") != expected_database:
        raise RuntimeError("unexpected settings target database")
    if one(
        cur,
        """SELECT count(*) FROM secretary.schema_migrations
           WHERE version='008_runtime_privileges.sql'""",
    ) != 1:
        raise RuntimeError("production settings target is missing migration 008")


def copy_settings(
    source,
    target,
    *,
    require_empty: bool = True,
    expected_target_database: str = "secretary",
) -> dict:
    with source.cursor() as source_cur, target.cursor() as target_cur:
        validate_source(source_cur)
        validate_target(target_cur, expected_target_database)

        if require_empty:
            for table, _, _, _ in TABLES:
                if one(target_cur, f"SELECT count(*) FROM secretary.{table}") != 0:
                    raise RuntimeError(f"target settings table is not empty: {table}")

        counts = {}
        for table, columns, key_columns, order_by in TABLES:
            result = copy_query_rows(
                source_cur,
                target_cur,
                table=table,
                key_columns=key_columns,
                columns=columns,
                source_query_template=(
                    f"SELECT {{columns}} FROM secretary.{table} t "
                    f"ORDER BY {order_by}"
                ),
            )
            counts[table] = result["source"]

        source_auth = one(
            source_cur,
            """SELECT count(*) FROM secretary.service_connections
               WHERE auth_data <> '{}'::jsonb""",
        )
        target_auth = one(
            target_cur,
            """SELECT count(*) FROM secretary.service_connections
               WHERE auth_data <> '{}'::jsonb""",
        )
        if source_auth != target_auth:
            raise RuntimeError("configured authentication count changed during transfer")

        orphan_counts = (
            one(
                target_cur,
                """SELECT count(*) FROM secretary.llm_profiles p
                   LEFT JOIN secretary.service_connections c ON c.id=p.connection_id
                   WHERE c.id IS NULL""",
            ),
            one(
                target_cur,
                """SELECT count(*) FROM secretary.magi_member_assignments a
                   LEFT JOIN secretary.llm_profiles p ON p.id=a.profile_id
                   WHERE p.id IS NULL""",
            ),
            one(
                target_cur,
                """SELECT count(*) FROM secretary.service_billing_profiles b
                   LEFT JOIN secretary.service_connections c ON c.id=b.connection_id
                   WHERE c.id IS NULL""",
            ),
        )
        if any(orphan_counts):
            raise RuntimeError("settings transfer created orphan references")

        for table, _, _, _ in TABLES:
            if one(target_cur, f"SELECT count(*) FROM secretary.{table}") != counts[table]:
                raise RuntimeError(f"settings count mismatch after transfer: {table}")

    return {**counts, "auth_configured": int(source_auth)}


def transfer(
    source,
    target,
    *,
    commit: bool,
    expected_target_database: str = "secretary",
) -> dict:
    try:
        result = copy_settings(
            source,
            target,
            require_empty=True,
            expected_target_database=expected_target_database,
        )
        if commit:
            target.commit()
        else:
            target.rollback()
    except Exception:
        target.rollback()
        raise
    return {**result, "committed": bool(commit)}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-port", type=int, required=True)
    parser.add_argument("--target-port", type=int, required=True)
    parser.add_argument("--source-database", default="secretary_pkb_proto_20260927")
    parser.add_argument("--target-database", default="secretary")
    parser.add_argument("--secret-file", type=Path, required=True)
    parser.add_argument("--commit", action="store_true")
    args = parser.parse_args()

    if not 1024 <= args.source_port <= 65535 or not 1024 <= args.target_port <= 65535:
        raise RuntimeError("invalid PostgreSQL port")
    if not args.secret_file.is_file():
        raise RuntimeError("local PostgreSQL secret is missing")
    password = args.secret_file.read_text(encoding="utf-8").strip()
    if len(password) < 32:
        raise RuntimeError("local PostgreSQL secret is invalid")

    with connect_admin(args.source_port, args.source_database, password) as source:
        with connect_admin(args.target_port, args.target_database, password) as target:
            result = transfer(
                source,
                target,
                commit=args.commit,
                expected_target_database=args.target_database,
            )

    print("Settings transfer: " + ("COMMIT" if result["committed"] else "ROLLBACK rehearsal"))
    print(
        "Connections={service_connections} Profiles={llm_profiles} "
        "Assignments={magi_member_assignments} BillingProfiles={service_billing_profiles} "
        "AuthConfigured={auth_configured}".format(**result)
    )
    print("PASS: runtime settings transferred without printing credential values.")


if __name__ == "__main__":
    main()
