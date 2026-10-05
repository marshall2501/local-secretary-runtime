"""Integration smoke: corrected entity and historical as-known-at views (fictional)."""
from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path
from uuid import UUID

from infrastructure.postgres.pkb_correction_repository import correct_entity
from pkb.ingestion_gate import InputRecord, ProposedClaim
from scripts.pkb.smoke_isolated import connection
from infrastructure.postgres.pkb_write_repository import write_one

DB = "secretary_pkb_proto_20260927"
WRITER = "secretary_pkb_proto_writer_20260927"


def at(value: str) -> datetime:
    return datetime.fromisoformat(value)


def ensure_entity(admin, name: str, domain: str, kind: str) -> UUID:
    with admin.transaction(), admin.cursor() as cur:
        cur.execute(
            "SELECT id FROM secretary.entities WHERE name=%s AND domain=%s",
            (name, domain),
        )
        rows = cur.fetchall()
        if not rows:
            cur.execute(
                """INSERT INTO secretary.entities(name,domain,entity_type)
                   VALUES (%s,%s,%s) RETURNING id""",
                (name, domain, kind),
            )
            return cur.fetchone()[0]
        if len(rows) != 1:
            raise AssertionError("fixture entity is ambiguous")
        return rows[0][0]


def fixture(input_id: str, text: str, recorded: str, occurred: str,
            entity_id: UUID, mention: str, predicate: str, value: str,
            corrected_id: str | None = None):
    source = InputRecord(
        input_id, "user_statement", "fixture://" + input_id,
        text, at(recorded), at(occurred),
    )
    proposal = ProposedClaim(
        str(entity_id), mention, predicate, value,
        0, len(text), text,
        intent="correction" if corrected_id else "assertion",
        corrects_claim_id=corrected_id,
    )
    return source, proposal


def as_known_at(db, ids: tuple[str, str], when: datetime) -> list[str]:
    """Prototype two-time view: effective-event date is separate from known-at."""
    with db.cursor() as cur:
        cur.execute(
            """SELECT c.id FROM secretary.claims c
               WHERE c.id=ANY(%s::uuid[])
                 AND c.recorded_at<=%s
                 AND (c.retracted_at IS NULL OR c.retracted_at>%s)
                 AND NOT EXISTS (
                   SELECT 1 FROM secretary.claims n
                   WHERE n.supersedes_id=c.id AND n.recorded_at<=%s
                 )
               ORDER BY c.recorded_at, c.id""",
            (list(ids), when, when, when),
        )
        return [str(x[0]) for x in cur.fetchall()]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--admin-secret", type=Path, required=True)
    parser.add_argument("--writer-secret", type=Path, required=True)
    parser.add_argument("--port", type=int, required=True)
    args = parser.parse_args()

    with connection("secretary_admin", args.admin_secret, args.port) as admin:
        sub = ensure_entity(admin, "サブPC", "pc", "computer")
        main_pc = ensure_entity(admin, "メインPC", "pc", "computer")
        rc_a = ensure_entity(admin, "RCカーA", "rc", "car")
        rc_b = ensure_entity(admin, "RCカーB", "rc", "car")

    pc_original = fixture(
        "pkb-proto-pc-01", "サブPCを架空GPUドライバーDRV-A1へ更新した。直後はゲームが軽くなった。",
        "2026-09-20T09:00:00+09:00", "2026-09-19T16:00:00+09:00",
        sub, "サブPC", "driver_updated", "DRV-A1",
    )
    rc_original = fixture(
        "pkb-proto-rc-01", "RCカーAのサーボを架空型式SERVO-X1に交換した。",
        "2026-09-20T10:00:00+09:00", "2026-09-19T14:00:00+09:00",
        rc_a, "RCカーA", "servo_updated", "SERVO-X1",
    )
    # The correction must coexist with subsequent, distinct historical updates.
    # The initial write slice flags uncertainty as review. Use an independent
    # unambiguous assertion of the same fictional later replacement here.
    rc_later = fixture(
        "pkb-proto-rc-04-clean", "RCカーBのサーボをSERVO-X2に交換した。",
        "2026-09-23T10:00:00+09:00", "2026-09-23T08:00:00+09:00",
        rc_b, "RCカーB", "servo_updated", "SERVO-X2",
    )
    with connection(WRITER, args.writer_secret, args.port) as db:
        pc_first = write_one(db, *pc_original)
        rc_first = write_one(db, *rc_original)
        rc_newer = write_one(db, *rc_later)
        assert pc_first.status in ("inserted", "replayed"), pc_first
        assert rc_first.status in ("inserted", "replayed"), rc_first
        assert rc_newer.status in ("inserted", "replayed"), rc_newer

        pc_fix = fixture(
            "pkb-proto-pc-03",
            "訂正：9月20日に話したDRV-A1への更新はサブPCではなくメインPCのこと。"
            "9月21日のサブPCの重さは訂正しない。",
            "2026-09-22T09:00:00+09:00", "2026-09-19T16:00:00+09:00",
            main_pc, "メインPC", "driver_updated", "DRV-A1", pc_first.claim_id,
        )
        rc_fix = fixture(
            "pkb-proto-rc-03",
            "訂正：9月20日に交換したSERVO-X1はRCカーAではなくRCカーB。"
            "RCカーAの右旋回の問題はそのまま正しい。",
            "2026-09-22T10:00:00+09:00", "2026-09-19T14:00:00+09:00",
            rc_b, "RCカーB", "servo_updated", "SERVO-X1", rc_first.claim_id,
        )

        for label, original, correction, early, late in (
            ("PC", pc_first, pc_fix, "2026-09-21T09:00:00+09:00",
             "2026-09-23T09:00:00+09:00"),
            ("RC", rc_first, rc_fix, "2026-09-21T10:00:00+09:00",
             "2026-09-23T10:00:00+09:00"),
        ):
            first = correct_entity(db, *correction)
            assert first.status in ("corrected", "replayed"), first
            again = correct_entity(db, *correction)
            assert again.status == "replayed", again
            assert first.new_claim_id == again.new_claim_id
            with db.cursor() as cur:
                cur.execute(
                    """SELECT c.verification_status,c.retracted_at,n.supersedes_id,
                              c.source_id,n.source_id
                       FROM secretary.claims c JOIN secretary.claims n
                       ON n.id=%s WHERE c.id=%s""",
                    (first.new_claim_id, original.claim_id),
                )
                status, retracted_at, supersedes_id, original_source, new_source = cur.fetchone()
                assert status == "retracted"
                assert retracted_at == correction[0].recorded_at
                assert supersedes_id == UUID(original.claim_id)
                assert original_source != new_source
                cur.execute(
                    """SELECT count(*) FROM secretary.pkb_correction_receipts
                       WHERE input_id=%s""", (correction[0].input_id,),
                )
                assert cur.fetchone()[0] == 1
                cur.execute(
                    """SELECT id, entity_id FROM secretary.current_claims
                       WHERE id IN (%s,%s)""",
                    (original.claim_id, first.new_claim_id),
                )
                current = cur.fetchall()
                assert current == [
                    (UUID(first.new_claim_id), UUID(correction[1].entity_key))
                ], current
            ids = (original.claim_id, first.new_claim_id)
            assert as_known_at(db, ids, at(early)) == [original.claim_id]
            assert as_known_at(db, ids, at(late)) == [first.new_claim_id]
            print(f"PASS: {label} correction preserves source, old history, new target and retry")

        # Correcting old RC car A's event must not erase later RC car B X2 event.
        with db.cursor() as cur:
            cur.execute(
                "SELECT value::text FROM secretary.current_claims WHERE id=%s",
                (rc_newer.claim_id,),
            )
            assert cur.fetchone()[0] == '"SERVO-X2"'
        print("PASS: later unrelated RC update remains current")
        print("PASS: both corrections distinguish as-known-at from effective-event time")


if __name__ == "__main__":
    main()
