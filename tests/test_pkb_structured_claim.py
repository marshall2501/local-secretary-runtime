# Provenance-Origin: original
# Provenance-AI: AI-assisted
"""Isolated, fictional PKB structured write checks; no operational database."""
from __future__ import annotations

import unittest
from dataclasses import replace
from datetime import datetime, timezone
from uuid import UUID

from pkb.application.structured_claim import register_structured_claim
from pkb.ingestion_gate import assess, Route
from pkb.write_service import WriteResult, literal_gate, payload_hash

TASK_ID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
OPERATION_ID = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
ENTITY_ID = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"
NOW = datetime(2026, 10, 20, 0, 0, tzinfo=timezone.utc)
ORIGINAL = "架空GPUをDRV-200へ更新した。"


class FakePkbRepository:
    """Mirrors the existing PKB gates without any real DB or LLM calls."""

    def __init__(self):
        self.payloads = {}
        self.calls = 0

    def source_ref(self, prefix, identifier):
        return f"local://{prefix}/{identifier}"

    def write_one(self, record, claim):
        self.calls += 1
        outcome = assess(
            record, claim,
            aliases={ENTITY_ID: {"架空GPU"}},
        )
        if outcome.route is not Route.AUTO_CANDIDATE:
            return WriteResult(outcome.route.value.replace("auto_candidate", "inserted"), outcome.reason)
        issue = literal_gate(record, claim)
        if issue:
            return WriteResult("review", issue)
        digest = payload_hash(record, claim)
        previous = self.payloads.get(record.input_id)
        if previous:
            if previous != digest:
                return WriteResult("rejected", "input_id_reused_with_different_payload")
            return WriteResult("replayed", "same_input_already_written", "claim-1", "source-1")
        self.payloads[record.input_id] = digest
        return WriteResult("inserted", "single_claim_committed", "claim-1", "source-1")


def write(repository, **overrides):
    arguments = {
        "task_id": TASK_ID,
        "operation_id": OPERATION_ID,
        "user_text": ORIGINAL,
        "entity_id": ENTITY_ID,
        "entity_mention": "架空GPU",
        "predicate": "driver_updated",
        "value": "DRV-200",
        "evidence_start": 0,
        "evidence_end": len(ORIGINAL),
        "recorded_at": NOW,
        "occurred_at": NOW,
    }
    arguments.update(overrides)
    return register_structured_claim(repository, **arguments)


class StructuredClaimTests(unittest.TestCase):
    def test_register_replay_and_payload_conflict_keep_one_claim(self):
        repo = FakePkbRepository()
        first = write(repo)
        again = write(repo)
        conflict = write(repo, value="DRV-201")
        self.assertEqual(first["status"], "inserted")
        self.assertEqual(again["status"], "replayed")
        self.assertEqual(conflict["status"], "rejected")
        self.assertEqual(len(repo.payloads), 1)
        self.assertEqual(first["claim_id"], again["claim_id"])

    def test_source_and_evidence_do_not_depend_on_model_assertions(self):
        repo = FakePkbRepository()
        self.assertEqual(write(repo)["status"], "inserted")
        self.assertEqual(len(repo.payloads), 1)
        for override, error in (
            ({"task_id": "model-provided-id"}, "invalid_structured_claim_identity"),
            ({"operation_id": "arbitrary"}, "invalid_structured_claim_identity"),
            ({"predicate": "has_component"}, "unsupported_structured_claim"),
            ({"entity_mention": "架空GPU以外"}, "structured_claim_not_grounded_in_user_text"),
            ({"evidence_start": -1}, "invalid_structured_claim_evidence"),
            ({"recorded_at": datetime(2026, 10, 20)}, "structured_claim_times_must_be_persisted_and_aware"),
        ):
            with self.subTest(override=override):
                with self.assertRaisesRegex(ValueError, error):
                    write(repo, **override)
        self.assertEqual(len(repo.payloads), 1)

    def test_uncertain_user_statement_needs_review(self):
        repo = FakePkbRepository()
        tentative = "架空GPUをDRV-200へ更新したかもしれない。"
        result = write(repo, user_text=tentative, evidence_end=len(tentative))
        self.assertEqual(result["status"], "review")
        self.assertEqual(repo.payloads, {})

    def test_new_task_operation_identity_is_independent(self):
        repo = FakePkbRepository()
        first = write(repo)
        second = write(repo, operation_id="dddddddd-dddd-4ddd-8ddd-dddddddddddd")
        self.assertEqual(first["status"], "inserted")
        self.assertEqual(second["status"], "inserted")
        self.assertEqual(len(repo.payloads), 2)


if __name__ == "__main__":
    unittest.main()
