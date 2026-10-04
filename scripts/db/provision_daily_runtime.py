"""Provision the local daily runtime login with shared internal database access.

The daily runtime is a trusted localhost-internal process boundary. Database
administration/recovery still uses secretary_admin, while normal runtime access
is intentionally shared across PKB, RITSUKO, Finance, MAGI settings,
Connections and Billing. External/public/high-impact operations are controlled
at the Web/API/MCP/RITSUKO policy boundaries.

The password remains in a git-ignored local secret file and is never printed.
"""
from __future__ import annotations

import argparse
import secrets
from pathlib import Path

import psycopg
from psycopg import sql

ROLE = "secretary_daily_runtime"
LEGACY_GROUPS = (
    "secretary_reader",
    "secretary_candidate_writer",
    "secretary_memory_writer",
    "secretary_task_writer",
    "secretary_audit_writer",
    "secretary_review_writer",
    "secretary_finance_writer",
    "secretary_magi_settings_writer",
    "secretary_connection_writer",
    "secretary_billing_writer",
)


def _read_secret(path: Path, label: str) -> str:
    if not path.is_file():
        raise RuntimeError(f"{label} secret is missing")
    value = path.read_text(encoding="utf-8").strip()
    if len(value) < 32:
        raise RuntimeError(f"{label} secret is invalid")
    return value


def _ensure_login(cur, runtime_password: str) -> None:
    cur.execute(
        """SELECT rolcanlogin, rolsuper, rolcreatedb, rolcreaterole,
                  rolreplication, rolbypassrls
           FROM pg_roles WHERE rolname=%s""",
        (ROLE,),
    )
    row = cur.fetchone()
    if row is None:
        cur.execute(
            sql.SQL(
                "CREATE ROLE {} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE "
                "NOREPLICATION NOBYPASSRLS PASSWORD {}"
            ).format(sql.Identifier(ROLE), sql.Literal(runtime_password))
        )
        return
    if not row[0] or any(row[1:]):
        raise RuntimeError("existing daily runtime role has unsafe attributes")
    cur.execute(
        sql.SQL("ALTER ROLE {} PASSWORD {}").format(
            sql.Identifier(ROLE),
            sql.Literal(runtime_password),
        )
    )


def _remove_legacy_memberships(cur) -> None:
    cur.execute(
        "SELECT rolname FROM pg_roles WHERE rolname = ANY(%s)",
        (list(LEGACY_GROUPS),),
    )
    existing = {row[0] for row in cur.fetchall()}
    for group in sorted(existing):
        cur.execute(
            sql.SQL("REVOKE {} FROM {}").format(
                sql.Identifier(group),
                sql.Identifier(ROLE),
            )
        )


def _grant_runtime_access(cur, database: str) -> None:
    cur.execute(
        sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(
            sql.Identifier(database),
            sql.Identifier(ROLE),
        )
    )


def _grant_schema_access(cur) -> None:
    cur.execute(
        sql.SQL("GRANT USAGE ON SCHEMA secretary TO {}").format(
            sql.Identifier(ROLE)
        )
    )
    cur.execute(
        sql.SQL(
            "GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES "
            "IN SCHEMA secretary TO {}"
        ).format(sql.Identifier(ROLE))
    )
    cur.execute(
        sql.SQL(
            "GRANT USAGE, SELECT, UPDATE ON ALL SEQUENCES "
            "IN SCHEMA secretary TO {}"
        ).format(sql.Identifier(ROLE))
    )
    cur.execute(
        sql.SQL("GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA secretary TO {}").format(
            sql.Identifier(ROLE)
        )
    )
    # Future objects created by secretary_admin inherit the same internal
    # runtime access without adding feature-specific database roles.
    cur.execute(
        sql.SQL(
            "ALTER DEFAULT PRIVILEGES FOR ROLE secretary_admin IN SCHEMA secretary "
            "GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {}"
        ).format(sql.Identifier(ROLE))
    )
    cur.execute(
        sql.SQL(
            "ALTER DEFAULT PRIVILEGES FOR ROLE secretary_admin IN SCHEMA secretary "
            "GRANT USAGE, SELECT, UPDATE ON SEQUENCES TO {}"
        ).format(sql.Identifier(ROLE))
    )
    cur.execute(
        sql.SQL(
            "ALTER DEFAULT PRIVILEGES FOR ROLE secretary_admin IN SCHEMA secretary "
            "GRANT EXECUTE ON FUNCTIONS TO {}"
        ).format(sql.Identifier(ROLE))
    )


def _verify_runtime_access(cur) -> None:
    checks = (
        ("secretary.schema_migrations", "SELECT"),
        ("secretary.entities", "SELECT"),
        ("secretary.claims", "INSERT"),
        ("secretary.tasks", "UPDATE"),
        ("secretary.audit_events", "INSERT"),
        ("secretary.finance_transactions", "SELECT"),
        ("secretary.llm_profiles", "UPDATE"),
        ("secretary.service_connections", "UPDATE"),
        ("secretary.service_billing_profiles", "SELECT"),
    )
    for relation, privilege in checks:
        cur.execute(
            "SELECT has_table_privilege(%s, %s, %s)",
            (ROLE, relation, privilege),
        )
        if cur.fetchone()[0] is not True:
            raise RuntimeError(
                f"daily runtime is missing {privilege} on {relation}"
            )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--database", default="secretary")
    parser.add_argument("--admin-secret-file", type=Path, required=True)
    parser.add_argument("--runtime-secret-file", type=Path, required=True)
    args = parser.parse_args()

    if not 1024 <= args.port <= 65535:
        raise RuntimeError("invalid PostgreSQL port")
    if not args.database or len(args.database) > 63:
        raise RuntimeError("invalid database name")
    admin_password = _read_secret(args.admin_secret_file, "admin")

    args.runtime_secret_file.parent.mkdir(parents=True, exist_ok=True)
    if args.runtime_secret_file.exists():
        runtime_password = _read_secret(args.runtime_secret_file, "existing daily runtime")
    else:
        runtime_password = secrets.token_urlsafe(48)
        args.runtime_secret_file.write_text(runtime_password, encoding="utf-8")

    common = dict(
        host="127.0.0.1",
        port=args.port,
        user="secretary_admin",
        password=admin_password,
        connect_timeout=5,
        autocommit=True,
    )
    with psycopg.connect(dbname="postgres", **common) as db:
        with db.cursor() as cur:
            _ensure_login(cur, runtime_password)
            _remove_legacy_memberships(cur)
            _grant_runtime_access(cur, args.database)

    with psycopg.connect(dbname=args.database, **common) as db:
        with db.cursor() as cur:
            _grant_schema_access(cur)
            _verify_runtime_access(cur)

    print("PASS: daily runtime login provisioned with shared internal database access.")
    print("Secret value was not printed.")


if __name__ == "__main__":
    main()
