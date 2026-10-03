"""Provision the local daily runtime login with composed group roles.

The password remains in a git-ignored local secret file and is never printed.
"""
from __future__ import annotations

import argparse
import secrets
from pathlib import Path

import psycopg
from psycopg import sql

ROLE = "secretary_daily_runtime"
GROUPS = (
    "secretary_reader", "secretary_memory_writer", "secretary_task_writer",
    "secretary_audit_writer", "secretary_finance_writer",
    "secretary_magi_settings_writer", "secretary_connection_writer",
    "secretary_billing_writer",
)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--admin-secret-file", type=Path, required=True)
    parser.add_argument("--runtime-secret-file", type=Path, required=True)
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        raise RuntimeError("invalid PostgreSQL port")
    if not args.admin_secret_file.is_file():
        raise RuntimeError("admin secret is missing")
    admin_password = args.admin_secret_file.read_text(encoding="utf-8").strip()
    if len(admin_password) < 32:
        raise RuntimeError("admin secret is invalid")

    args.runtime_secret_file.parent.mkdir(parents=True, exist_ok=True)
    if args.runtime_secret_file.exists():
        runtime_password = args.runtime_secret_file.read_text(encoding="utf-8").strip()
        if len(runtime_password) < 32:
            raise RuntimeError("existing daily runtime secret is invalid")
    else:
        runtime_password = secrets.token_urlsafe(48)
        args.runtime_secret_file.write_text(runtime_password, encoding="utf-8")

    with psycopg.connect(
        host="127.0.0.1", port=args.port, dbname="postgres",
        user="secretary_admin", password=admin_password, connect_timeout=5, autocommit=True,
    ) as db:
        with db.cursor() as cur:
            cur.execute("SELECT rolname FROM pg_roles WHERE rolname = ANY(%s)", (list(GROUPS),))
            existing = {row[0] for row in cur.fetchall()}
            if set(GROUPS) - existing:
                raise RuntimeError("required production group roles are missing")
            cur.execute(
                """SELECT rolcanlogin, rolsuper, rolcreatedb, rolcreaterole,
                          rolreplication, rolbypassrls
                   FROM pg_roles WHERE rolname=%s""", (ROLE,)
            )
            row = cur.fetchone()
            if row is None:
                cur.execute(
                    sql.SQL(
                        "CREATE ROLE {} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE "
                        "NOREPLICATION NOBYPASSRLS PASSWORD %s"
                    ).format(sql.Identifier(ROLE)),
                    (runtime_password,),
                )
            else:
                if not row[0] or any(row[1:]):
                    raise RuntimeError("existing daily runtime role has unsafe attributes")
                cur.execute(
                    sql.SQL("ALTER ROLE {} PASSWORD %s").format(sql.Identifier(ROLE)),
                    (runtime_password,),
                )
            for group in GROUPS:
                cur.execute(sql.SQL("GRANT {} TO {}").format(
                    sql.Identifier(group), sql.Identifier(ROLE)
                ))
            cur.execute(sql.SQL("GRANT CONNECT ON DATABASE secretary TO {}").format(
                sql.Identifier(ROLE)
            ))
            cur.execute(
                """SELECT count(*) FROM unnest(%s::text[]) AS g(name)
                   WHERE NOT pg_has_role(%s, g.name, 'MEMBER')""",
                (list(GROUPS), ROLE),
            )
            if cur.fetchone()[0] != 0:
                raise RuntimeError("daily runtime role membership verification failed")

    print("PASS: daily runtime login provisioned with production group roles.")
    print("Secret value was not printed.")


if __name__ == "__main__":
    main()
