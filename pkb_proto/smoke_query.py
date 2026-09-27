"""Read-only acceptance smoke against existing fictional PC/RC correction fixtures."""
from __future__ import annotations

import argparse
from pathlib import Path
from uuid import UUID

from pkb_proto.query_service import ClaimQuery, query_claims
from pkb_proto.smoke_correction import at
from pkb_proto.smoke_isolated import connection


def one_entity(db, domain: str, name: str) -> UUID:
    with db.cursor() as cur:
        cur.execute(
            """SELECT id FROM secretary.entities
               WHERE domain=%s AND name=%s AND retired_at IS NULL""",
            (domain, name),
        )
        ids = cur.fetchall()
    assert len(ids) == 1, f"Missing or ambiguous fictional Entity: {domain}/{name}"
    return ids[0][0]


def check():
    parser = argparse.ArgumentParser()
    parser.add_argument("--writer-secret", type=Path, required=True)
    parser.add_argument("--port", type=int, required=True)
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        raise ValueError("Invalid port")

    with connection("secretary_pkb_proto_writer_20260927",
                    args.writer_secret, args.port) as db:
        sub = one_entity(db, "pc", "サブPC")
        main = one_entity(db, "pc", "メインPC")
        rc_a = one_entity(db, "rc", "RCカーA")
        rc_b = one_entity(db, "rc", "RCカーB")
        early = at("2026-09-21T12:00:00+09:00")
        late = at("2026-09-24T12:00:00+09:00")

        sub_early = query_claims(
            db, ClaimQuery(entity_id=sub, predicate="driver_updated", known_at=early))
        assert sub_early.total == 1
        assert sub_early.items[0]["value"] == "DRV-A1"
        assert sub_early.items[0]["status_at_cutoff"] == "active"
        assert sub_early.items[0]["source_uri"].startswith("fixture://")

        main_early = query_claims(
            db, ClaimQuery(entity_id=main, predicate="driver_updated", known_at=early))
        assert main_early.total == 0 and main_early.status == "ok"

        sub_late = query_claims(
            db, ClaimQuery(entity_id=sub, predicate="driver_updated", known_at=late))
        assert sub_late.total == 0
        main_late = query_claims(
            db, ClaimQuery(entity_id=main, predicate="driver_updated",
                           effective_before=at("2026-09-20T00:00:00+09:00"),
                           known_at=late))
        assert main_late.total == 1
        assert main_late.items[0]["value"] == "DRV-A1"

        old_history = query_claims(
            db, ClaimQuery(entity_id=sub, predicate="driver_updated",
                           known_at=late, include_history=True))
        assert old_history.total == 1
        assert old_history.items[0]["status_at_cutoff"] == "superseded"
        assert old_history.items[0]["source_uri"] != main_late.items[0]["source_uri"]
        assert main_late.items[0]["supersedes_id"] == old_history.items[0]["id"]
        print("PASS: PC early/late knowledge, source, corrected target and history")

        rc_a_early = query_claims(
            db, ClaimQuery(entity_id=rc_a, predicate="servo_updated", known_at=early))
        rc_a_late = query_claims(
            db, ClaimQuery(entity_id=rc_a, predicate="servo_updated", known_at=late))
        assert rc_a_early.total == 1 and rc_a_late.total == 0

        rc_b_late = query_claims(
            db, ClaimQuery(entity_id=rc_b, predicate="servo_updated",
                           known_at=late, limit=1, offset=0))
        assert rc_b_late.total == 2 and len(rc_b_late.items) == 1
        next_page = query_claims(
            db, ClaimQuery(entity_id=rc_b, predicate="servo_updated",
                           known_at=late, limit=1, offset=1))
        assert next_page.total == 2 and len(next_page.items) == 1
        assert {rc_b_late.items[0]["value"], next_page.items[0]["value"]} == {
            "SERVO-X1", "SERVO-X2"
        }
        rc_early_event = query_claims(
            db, ClaimQuery(entity_id=rc_b, predicate="servo_updated",
                           effective_at=at("2026-09-19T14:00:00+09:00"),
                           known_at=late))
        assert rc_early_event.total == 1 and rc_early_event.items[0]["value"] == "SERVO-X1"
        print("PASS: RC correction and later distinct update, exact count and pagination")

        none = query_claims(
            db, ClaimQuery(domain="nonexistent_fixture_domain", known_at=late))
        assert none.status == "ok" and none.total == 0 and not none.items
        history_all = query_claims(
            db, ClaimQuery(domain="rc", predicate="servo_updated",
                           known_at=late, include_history=True))
        assert history_all.total >= 3
        assert all(row["source_uri"].startswith("fixture://") for row in history_all.items)
        print("PASS: scoped zero results, complete RC correction history with provenance")


if __name__ == "__main__":
    check()
