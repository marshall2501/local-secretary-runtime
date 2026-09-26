"""Offline checks for the separate local human Memory Review CLI."""
import os
import unittest
from argparse import Namespace
from unittest.mock import MagicMock, patch
from uuid import UUID

from scripts.memory.review import checked_dsn, require_text, review_candidate


class MemoryReviewStaticTests(unittest.TestCase):
    def test_admin_api_and_remote_credentials_rejected(self):
        for dsn in (
            "host=127.0.0.1 user=secretary_admin dbname=secretary",
            "host=127.0.0.1 user=secretary_api dbname=secretary",
            "host=203.0.113.10 user=secretary_reviewer dbname=secretary",
            "host=127.0.0.1 user=secretary_reviewer dbname=other",
        ):
            with self.subTest(dsn=dsn):
                with patch.dict(os.environ, {"LSA_REVIEW_DSN": dsn}):
                    with self.assertRaises(ValueError):
                        checked_dsn()

    def test_reviewer_local_configuration(self):
        with patch.dict(
            os.environ,
            {"LSA_REVIEW_DSN": "host=127.0.0.1 port=55432 "
                               "dbname=secretary user=secretary_reviewer"}
        ):
            self.assertIn("secretary_reviewer", checked_dsn())

    def test_blank_notes_rejected(self):
        with self.assertRaises(ValueError):
            require_text("  ", "Notes")

    def test_cancelled_review_never_updates(self):
        candidate_id = UUID("00000000-0000-4000-8000-000000000010")
        candidate = {
            "id": candidate_id, "review_status": "pending",
            "entity_name": "Synthetic PC", "domain": "pc",
            "source_uri": "test:fictional", "source_type": "user_statement",
            "citation": "Synthetic source", "claim_type": "observation",
            "predicate": "ram_gb", "proposed_value": 16,
            "confidence": None, "evidence": "Synthetic source",
            "extraction_model": "test", "prompt_version": "fixture",
            "recorded_at": "2026-01-01",
        }
        cur = MagicMock()
        cur.fetchone.return_value = candidate
        db = MagicMock()
        db.__enter__.return_value = db
        db.cursor.return_value.__enter__.return_value = cur
        args = Namespace(
            id=str(candidate_id), decision="accepted",
            notes="Test review", reviewer="test-human"
        )
        with patch("scripts.memory.review.connect", return_value=db):
            review_candidate(args, confirm=lambda _: "not confirmed")
        self.assertEqual(cur.execute.call_count, 1)
        self.assertEqual(
            cur.execute.call_args.args[0].splitlines()[0].strip(),
            "SELECT p.*, e.name AS entity_name, e.domain, s.citation,"
        )


    def test_acceptance_records_only_unverified_memory_claim(self):
        candidate_id = UUID("00000000-0000-4000-8000-000000000020")
        entity_id = UUID("00000000-0000-4000-8000-000000000021")
        source_id = UUID("00000000-0000-4000-8000-000000000022")
        candidate = {
            "id": candidate_id, "review_status": "pending",
            "entity_name": "Synthetic PC", "domain": "pc",
            "entity_id": entity_id, "source_id": source_id,
            "source_uri": "test:fictional", "source_type": "user_statement",
            "citation": "Synthetic source", "claim_type": "observation",
            "predicate": "ram_gb", "proposed_value": 16,
            "confidence": None, "evidence": "Synthetic source",
            "extraction_model": "test", "prompt_version": "fixture",
            "recorded_at": "2026-01-01",
        }
        cur = MagicMock()
        cur.fetchone.side_effect = [
            candidate,
            {"id": UUID("00000000-0000-4000-8000-000000000023")},
            {"id": UUID("00000000-0000-4000-8000-000000000024")},
        ]
        cur.rowcount = 1
        db = MagicMock()
        db.__enter__.return_value = db
        db.cursor.return_value.__enter__.return_value = cur
        args = Namespace(
            id=str(candidate_id), decision="accepted",
            notes="Checked fictional statement", reviewer="test-human"
        )
        with patch("scripts.memory.review.connect", return_value=db):
            review_candidate(
                args, confirm=lambda _: f"CONFIRM {candidate_id}"
            )
        sql_calls = [call.args[0] for call in cur.execute.call_args_list]
        self.assertEqual(len(sql_calls), 5)
        self.assertIn("UPDATE secretary.pending_claims", sql_calls[1])
        self.assertIn("INSERT INTO secretary.approvals", sql_calls[2])
        self.assertIn("memory_write", sql_calls[2])
        self.assertIn("INSERT INTO secretary.claims", sql_calls[3])
        self.assertIn("'unverified'", sql_calls[3])
        self.assertIn("INSERT INTO secretary.audit_events", sql_calls[4])



if __name__ == "__main__":
    unittest.main()
