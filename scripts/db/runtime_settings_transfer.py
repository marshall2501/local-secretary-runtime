"""Copy only runtime settings from the historical isolated DB into production schema.

No row values are printed. Service Connection auth_data is transferred in-memory
through parameterized psycopg statements and is never serialized to a file.
"""
from __future__ import annotations

import argparse
from pathlib import Path

import psycopg

TABLES = (
    ("service_connections",
     ("id","display_name","adapter_key","endpoint","credential_ref","account_label",
      "capabilities","nonsecret_config","auth_data","connection_type","connection_role",
      "enabled","created_at","updated_at"), "id"),
    ("llm_profiles",
     ("id","display_name","connection_id","model","context_window_tokens",
      "ollama_num_predict","retry_http_codes","enabled","created_at","updated_at"), "id"),
    ("magi_member_assignments",
     ("member","profile_id","enabled","weight","timeout_seconds","retry_within_turn",
      "updated_at"), "member"),
    ("service_billing_profiles",
     ("id","display_name","connection_id","enabled","created_at","updated_at"), "id"),
)


def _connect(port: int, database: str, password: str):
    return psycopg.connect(
        host="127.0.0.1", port=port, dbname=database,
        user="secretary_admin", password=password, connect_timeout=5, autocommit=False,
    )


def _one(cur, statement: str, args=()):
    cur.execute(statement, args)
    row = cur.fetchone()
    if row is None or len(row) != 1:
        raise RuntimeError("unexpected scalar result")
    return row[0]


def _validate_source(cur):
    if _one(cur, "SELECT current_database()") != "secretary_pkb_proto_20260927":
        raise RuntimeError("unexpected settings source database")
    if _one(cur, """SELECT count(*) FROM secretary.schema_migrations
                    WHERE version='026_service_billing.sql'""") != 1:
        raise RuntimeError("isolated settings source is missing migration 026")


def _validate_target(cur):
    if _one(cur, "SELECT current_database()") != "secretary":
        raise RuntimeError("unexpected settings target database")
    if _one(cur, """SELECT count(*) FROM secretary.schema_migrations
                    WHERE version='008_runtime_privileges.sql'""") != 1:
        raise RuntimeError("production settings target is missing migration 008")
    for table, _, _ in TABLES:
        if _one(cur, f"SELECT count(*) FROM secretary.{table}") != 0:
            raise RuntimeError(f"target settings table is not empty: {table}")


def transfer(source, target, *, commit: bool) -> dict:
    with source.cursor() as source_cur, target.cursor() as target_cur:
        _validate_source(source_cur)
        _validate_target(target_cur)
        counts = {}
        for table, columns, order_by in TABLES:
            column_sql = ",".join(columns)
            source_cur.execute(f"SELECT {column_sql} FROM secretary.{table} ORDER BY {order_by}")
            rows = source_cur.fetchall()
            counts[table] = len(rows)
            if rows:
                placeholders = ",".join(["%s"] * len(columns))
                target_cur.executemany(
                    f"INSERT INTO secretary.{table} ({column_sql}) VALUES ({placeholders})",
                    rows,
                )

        for table, _, _ in TABLES:
            if _one(target_cur, f"SELECT count(*) FROM secretary.{table}") != counts[table]:
                raise RuntimeError(f"settings count mismatch after transfer: {table}")

        source_auth = _one(
            source_cur, """SELECT count(*) FROM secretary.service_connections
                           WHERE auth_data <> '{}'::jsonb"""
        )
        target_auth = _one(
            target_cur, """SELECT count(*) FROM secretary.service_connections
                           WHERE auth_data <> '{}'::jsonb"""
        )
        if source_auth != target_auth:
            raise RuntimeError("configured authentication count changed during transfer")

        orphan_counts = (
            _one(target_cur, """SELECT count(*) FROM secretary.llm_profiles p
                                LEFT JOIN secretary.service_connections c ON c.id=p.connection_id
                                WHERE c.id IS NULL"""),
            _one(target_cur, """SELECT count(*) FROM secretary.magi_member_assignments a
                                LEFT JOIN secretary.llm_profiles p ON p.id=a.profile_id
                                WHERE p.id IS NULL"""),
            _one(target_cur, """SELECT count(*) FROM secretary.service_billing_profiles b
                                LEFT JOIN secretary.service_connections c ON c.id=b.connection_id
                                WHERE c.id IS NULL"""),
        )
        if any(orphan_counts):
            raise RuntimeError("settings transfer created orphan references")

    if commit:
        target.commit()
    else:
        target.rollback()
    return {**counts, "auth_configured": int(source_auth), "committed": bool(commit)}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-port", type=int, required=True)
    parser.add_argument("--target-port", type=int, required=True)
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

    with _connect(args.source_port, "secretary_pkb_proto_20260927", password) as source:
        with _connect(args.target_port, "secretary", password) as target:
            result = transfer(source, target, commit=args.commit)

    print("Settings transfer: " + ("COMMIT" if result["committed"] else "ROLLBACK rehearsal"))
    print(
        "Connections={service_connections} Profiles={llm_profiles} "
        "Assignments={magi_member_assignments} BillingProfiles={service_billing_profiles} "
        "AuthConfigured={auth_configured}".format(**result)
    )
    print("PASS: runtime settings transferred without printing credential values.")


if __name__ == "__main__":
    main()
