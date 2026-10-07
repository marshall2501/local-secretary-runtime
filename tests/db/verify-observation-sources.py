"""Verify source-neutral Observation persistence on disposable PostgreSQL."""
from __future__ import annotations

import sys
from pathlib import Path
from uuid import uuid4

import psycopg

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from infrastructure.postgres.magi_task_repository import (
    create_task,
    record_source_read,
)


def _env_value(name: str) -> str:
    for line in (ROOT / ".env.postgres").read_text(encoding="utf-8").splitlines():
        if line.startswith(name + "="):
            return line.split("=", 1)[1].strip()
    raise RuntimeError(f"missing {name} in .env.postgres")


def main() -> None:
    port = int(_env_value("LSA_DB_PORT"))
    password = (ROOT / "secrets" / "postgres-password.txt").read_text(
        encoding="utf-8"
    ).strip()
    task_id = uuid4()

    with psycopg.connect(
        host="127.0.0.1",
        port=port,
        dbname="secretary",
        user="secretary_admin",
        password=password,
    ) as db:
        create_task(
            db,
            task_id=task_id,
            request="synthetic observation persistence verification",
            member_specs=[{
                "name": "MELCHIOR",
                "provider": "ollama",
                "model": "fixture",
                "enabled": True,
            }],
        )

        fixtures = (
            (
                {
                    "source": "web",
                    "request_ids": ["REQ-WEB"],
                    "what": "synthetic public fact",
                    "capability": "web_research",
                    "confidentiality": "public",
                },
                {
                    "status": "ok",
                    "source": "web",
                    "capability": "web_research",
                    "confidentiality": "public",
                    "tool": "web",
                    "operation": "research",
                    "total": 1,
                    "answer": "synthetic public result",
                    "citation": "synthetic web fixture",
                    "verified_by": "bounded_web_retrieval",
                    "result": {
                        "result_kind": "web_research",
                        "hits": [{"title": "synthetic official source"}],
                    },
                    "source_metadata": {"provider": "fixture"},
                },
            ),
            (
                {
                    "source": "finance",
                    "request_ids": ["REQ-FIN"],
                    "what": "synthetic monthly summary",
                    "capability": "finance_read",
                    "confidentiality": "private",
                },
                {
                    "status": "ok",
                    "source": "finance",
                    "capability": "finance_read",
                    "confidentiality": "private",
                    "tool": "finance",
                    "operation": "summary",
                    "total": 1,
                    "answer": "synthetic private finance result",
                    "citation": "synthetic finance fixture",
                    "verified_by": "deterministic_finance_query",
                    "result": {
                        "result_kind": "finance_summary",
                        "monthly": [{"month": "2099-01", "net": 1}],
                    },
                },
            ),
        )

        for pending, execution in fixtures:
            record_source_read(
                db,
                task_id=task_id,
                execution=execution,
                pending_request=pending,
            )

        with db.cursor() as cur:
            cur.execute(
                """SELECT s.confidentiality, a.tool, a.risk, a.status,
                          r.outcome, r.verified_by
                   FROM secretary.actions a
                   JOIN secretary.results r ON r.action_id=a.id
                   JOIN secretary.sources s ON s.id=r.source_id
                   WHERE a.task_id=%s
                   ORDER BY a.recorded_at, a.id""",
                (task_id,),
            )
            rows = cur.fetchall()
            if len(rows) != 2:
                raise AssertionError(f"expected 2 read records, got {len(rows)}")
            confidentialities = {row[0] for row in rows}
            if confidentialities != {"public", "private"}:
                raise AssertionError(
                    f"unexpected confidentialities: {confidentialities}"
                )
            if any(row[2] != "read_only" for row in rows):
                raise AssertionError("non-read-only action recorded")
            if any(row[3] != "succeeded" or row[4] != "success" for row in rows):
                raise AssertionError("successful read record not persisted")

            cur.execute(
                """SELECT details->>'source', details->>'confidentiality'
                   FROM secretary.audit_events
                   WHERE task_id=%s
                     AND event_type='core.magi.source_observed'
                   ORDER BY occurred_at, id""",
                (task_id,),
            )
            audit = cur.fetchall()
            if {row[0] for row in audit} != {"web", "finance"}:
                raise AssertionError(f"missing generic source audit: {audit}")
            if {row[1] for row in audit} != {"public", "private"}:
                raise AssertionError(f"audit confidentiality mismatch: {audit}")

    print(
        "PASS: disposable PostgreSQL source-neutral Observation persistence "
        "records Web(public) and Finance(private) as read-only Action/Result/Source/Audit."
    )


if __name__ == "__main__":
    main()
