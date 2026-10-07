"""Verify ER schema introspection against the disposable PostgreSQL test database."""
from __future__ import annotations

import sys
from pathlib import Path

import psycopg

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from infrastructure.postgres.schema_introspection import load_schema_snapshot
from infrastructure.schema_diagram.providers import render_mermaid


def _env_value(name: str) -> str:
    path = ROOT / ".env.postgres"
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith(name + "="):
            return line.split("=", 1)[1].strip()
    raise RuntimeError(f"missing {name} in .env.postgres")


def main() -> None:
    port = int(_env_value("LSA_DB_PORT"))
    password = (ROOT / "secrets" / "postgres-password.txt").read_text(
        encoding="utf-8"
    ).strip()

    def connect():
        return psycopg.connect(
            host="127.0.0.1",
            port=port,
            dbname="secretary",
            user="secretary_admin",
            password=password,
        )

    snapshot = load_schema_snapshot(connect, schema="secretary")
    base_tables = {
        table.name
        for table in snapshot.tables
        if table.kind == "BASE TABLE"
    }

    expected = {
        "entities",
        "claims",
        "tasks",
        "actions",
        "results",
        "finance_transactions",
        "service_connections",
    }
    missing = sorted(expected - base_tables)
    if missing:
        raise AssertionError("missing expected tables: " + ", ".join(missing))
    if "current_claims" not in snapshot.views:
        raise AssertionError("current_claims view was not detected")
    if snapshot.relation_count < 1:
        raise AssertionError("foreign keys were not detected")

    diagram = render_mermaid(snapshot)
    if "erDiagram" not in diagram:
        raise AssertionError("Mermaid header missing")
    if "entities {" not in diagram or "claims {" not in diagram:
        raise AssertionError("expected tables missing from Mermaid output")

    print(
        "PASS: disposable PostgreSQL schema introspection "
        f"tables={snapshot.table_count} fks={snapshot.relation_count} "
        f"views={len(snapshot.views)}"
    )


if __name__ == "__main__":
    main()
