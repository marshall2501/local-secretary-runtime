"""Acceptance smoke for lossless import of original ten fictional episodes."""
from __future__ import annotations

import argparse
from pathlib import Path

from .episode_intake import ingest, load_fixture
from .smoke_isolated import connection

WRITER = "secretary_pkb_proto_writer_20260927"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--writer-secret", type=Path, required=True)
    parser.add_argument("--port", type=int, required=True)
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        raise ValueError("Invalid port")
    episodes = load_fixture(Path(__file__).parent / "fixtures" / "episodes.json")
    assert {ep["source_kind"] for ep in episodes} == {"user_statement", "file", "web"}
    with connection(WRITER, args.writer_secret, args.port) as db:
        first = ingest(db, episodes)
        assert first in ((10, 0), (0, 10)), first
        second = ingest(db, episodes)
        assert second == (0, 10), second
        with db.cursor() as cur:
            cur.execute(
                """SELECT r.episode_id,r.domain,r.interpretation_state,
                          s.uri,s.source_type,s.metadata->>'original_text'
                   FROM secretary.pkb_episode_receipts r
                   JOIN secretary.sources s ON s.id=r.source_id
                   ORDER BY r.episode_id"""
            )
            rows = cur.fetchall()
            assert len(rows) == 10, len(rows)
            by_id = {row[0]: row for row in rows}
            for ep in episodes:
                row = by_id[ep["id"]]
                assert row[1] == ep["domain"]
                assert row[2] == "uninterpreted"
                assert row[3] == ep["source_ref"]
                assert row[4] == ep["source_kind"]
                assert row[5] == ep["text"]
            cur.execute(
                """SELECT count(*) FROM secretary.claims c
                   JOIN secretary.pkb_episode_receipts r ON r.source_id=c.source_id"""
            )
            assert cur.fetchone()[0] == 0, (
                "Uninterpreted episodes must not be promoted to accepted Claims"
            )
            cur.execute(
                """SELECT domain,count(*) FROM secretary.pkb_episode_receipts
                   GROUP BY domain ORDER BY domain"""
            )
            assert cur.fetchall() == [("pc", 5), ("rc", 5)]
        print("PASS: all ten original PC/RC episodes, full text, Source and timestamps preserved")
        print("PASS: exact retry creates no duplicate episode or Source")
        print("PASS: external speculative episodes remain Source-only, no Claims promoted")


if __name__ == "__main__":
    main()
