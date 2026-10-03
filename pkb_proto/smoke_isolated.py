"""Isolated, fictional-only integration smoke test for a dedicated PKB test DB."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

import psycopg

from pkb.ingestion_gate import InputRecord, ProposedClaim
from pkb.write_service import write_one

WRITER = "secretary_pkb_proto_writer_20260927"
DBNAME = "secretary_pkb_proto_20260927"
AT = datetime(2026, 9, 23, 9, 0, tzinfo=timezone.utc)


def connection(user: str, secret: Path, port: int):
    if not secret.is_file():
        raise RuntimeError("Local secret file missing")
    password = secret.read_text(encoding="utf-8").strip()
    if len(password) < 32:
        raise RuntimeError("Invalid secret file")
    return psycopg.connect(
        dbname=DBNAME, host="127.0.0.1", port=port,
        user=user, password=password, connect_timeout=5,
        autocommit=True,
    )


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--admin-secret", type=Path, required=True)
    p.add_argument("--writer-secret", type=Path, required=True)
    p.add_argument("--port", type=int, required=True)
    args = p.parse_args()
    if not 1024 <= args.port <= 65535:
        raise RuntimeError("Invalid localhost port")

    # Admin use is strictly isolated DB and strictly fixture setup.
    with connection("secretary_admin", args.admin_secret, args.port) as admin:
        with admin.transaction(), admin.cursor() as cur:
            cur.execute("SELECT current_database()")
            assert cur.fetchone()[0] == DBNAME
            cur.execute(
                "SELECT id FROM secretary.entities WHERE name=%s AND domain='pc'",
                ("メインPC",),
            )
            rows = cur.fetchall()
            if len(rows) == 0:
                cur.execute(
                    """INSERT INTO secretary.entities (name, entity_type, domain)
                       VALUES (%s,'computer','pc') RETURNING id""",
                    ("メインPC",),
                )
                entity_id = cur.fetchone()[0]
            elif len(rows) == 1:
                entity_id = rows[0][0]
            else:
                raise RuntimeError("Ambiguous fixture entity")
    assert isinstance(entity_id, UUID)

    original = "メインPCをDRV-A2へ更新した。"
    record = InputRecord(
        "pkb-proto-smoke-01", "user_statement",
        "fixture://pkb-proto-smoke-01", original, AT, AT,
    )
    claim = ProposedClaim(
        str(entity_id), "メインPC", "driver_updated", "DRV-A2",
        0, len(original), original,
    )
    with connection(WRITER, args.writer_secret, args.port) as db:
        with db.cursor() as cur:
            cur.execute("SELECT current_user")
            assert cur.fetchone()[0] == WRITER
        first = write_one(db, record, claim)
        assert first.status in ("inserted", "replayed"), first
        second = write_one(db, record, claim)
        assert second.status == "replayed", second
        assert first.claim_id == second.claim_id
        assert first.source_id == second.source_id

        modified_record = InputRecord(
            record.input_id, record.source_kind, record.source_ref,
            "メインPCをDRV-A3へ更新した。", AT, AT,
        )
        altered_claim = ProposedClaim(
            str(entity_id), "メインPC", "driver_updated", "DRV-A3",
            0, len(modified_record.text), modified_record.text,
        )
        collision = write_one(db, modified_record, altered_claim)
        assert collision.status == "rejected"
        assert collision.reason == "input_id_reused_with_different_payload"

        with db.cursor() as cur:
            cur.execute(
                """SELECT count(*) FROM secretary.pkb_input_receipts
                   WHERE input_id=%s""", (record.input_id,),
            )
            assert cur.fetchone()[0] == 1
            cur.execute(
                """SELECT c.value::text, c.verification_status, s.uri
                   FROM secretary.claims c JOIN secretary.sources s ON s.id=c.source_id
                   WHERE c.id=%s""",
                (first.claim_id,),
            )
            value, status, uri = cur.fetchone()
            assert value == '"DRV-A2"'
            assert status == "unverified"
            assert uri == record.source_ref
        print("PASS: insert / exact replay / changed-payload rejection / source and status")

        # A transaction in the same restricted writer connection must roll back
        # intermediate Source inserts if a later stage fails.
        marker = "fixture://pkb-proto-rollback-check"
        try:
            with db.transaction():
                with db.cursor() as cur:
                    cur.execute(
                        """INSERT INTO secretary.sources
                           (source_type,uri,citation,retrieved_at)
                           VALUES ('user_statement',%s,'rollback-smoke',%s)""",
                        (marker, AT),
                    )
                    raise RuntimeError("simulate later-stage write failure")
        except RuntimeError:
            pass
        with db.cursor() as cur:
            cur.execute("SELECT count(*) FROM secretary.sources WHERE uri=%s", (marker,))
            assert cur.fetchone()[0] == 0
        print("PASS: simulated intermediate failure rolls back the transaction")

    # Check the new writer has no inherited production schema access. It may
    # connect to the DB through PostgreSQL's default PUBLIC CONNECT privilege,
    # but reading protected user data must fail.
    denied = False
    password = args.writer_secret.read_text(encoding="utf-8").strip()
    try:
        with psycopg.connect(
            dbname="secretary", host="127.0.0.1", port=args.port,
            user=WRITER, password=password, connect_timeout=5,
        ) as prod, prod.cursor() as cur:
            try:
                cur.execute("SELECT count(*) FROM secretary.claims")
            except psycopg.errors.InsufficientPrivilege:
                denied = True
    except psycopg.OperationalError:
        denied = True  # even stronger: connection rejected
    assert denied, "Prototype writer unexpectedly read the production schema"
    print("PASS: dedicated writer denied production Claim reads")


if __name__ == "__main__":
    main()
