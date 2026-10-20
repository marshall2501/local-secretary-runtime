# Provenance-Origin: original
# Provenance-AI: AI-assisted
"""PKB write Operation through a durable Action; fake isolated persistence."""
from __future__ import annotations

import unittest
from datetime import datetime, timezone
from unittest.mock import patch
from uuid import UUID

from ritsuko.application.pkb_claim_operation import invoke_pkb_claim

TASK = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
ATTEMPT = UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")
ACTION = UUID("cccccccc-cccc-4ccc-8ccc-cccccccccccc")
AT = datetime(2026, 10, 20, tzinfo=timezone.utc)
ORIGINAL = "架空GPUをDRV-200へ更新した。"
ARGS = {
    "entity_id": "dddddddd-dddd-4ddd-8ddd-dddddddddddd",
    "entity_mention": "架空GPU",
    "predicate": "driver_updated",
    "value": "DRV-200",
    "evidence_start": 0,
    "evidence_end": len(ORIGINAL),
}


class FakeStore:
    def __init__(self, *, permitted=True):
        self.permitted = permitted
        self.actions = {}
        self.events = []

    def reserve(self, **kw):
        self.events.append("reserve")
        if not self.permitted:
            raise ValueError("pkb_write_not_permitted")
        key = (kw["task_id"], kw["request_id"])
        if key in self.actions:
            raise ValueError("pkb_write_already_reserved")
        self.actions[key] = "running"
        return {"action_id": str(ACTION), "recorded_at": AT, "occurred_at": AT}

    def finish(self, **kw):
        self.events.append("finish")
        assert kw["action_id"] == ACTION
        self.actions[(kw["task_id"], "REQ-1")] = "succeeded"
        return str(ACTION), "result-1"

    def mark_unknown(self, **kw):
        self.events.append("unknown")
        assert kw["action_id"] == ACTION
        self.actions[(kw["task_id"], "REQ-1")] = "unknown"


def invoke(store):
    return invoke_pkb_claim(
        task_store=store, pkb_repository=object(),
        task_id=TASK, attempt_id=ATTEMPT,
        request_id="REQ-1", user_text=ORIGINAL,
        arguments=ARGS.copy(),
    )


class PkbClaimOperationTests(unittest.TestCase):
    @patch("ritsuko.application.pkb_claim_operation.register_structured_claim")
    def test_committed_result_is_returned_to_magi_only_after_receipt(self, write):
        write.return_value = {
            "status": "inserted", "reason": "single_claim_committed",
            "claim_id": "claim-1", "source_id": "source-1",
        }
        store = FakeStore()
        result = invoke(store)
        self.assertEqual(store.events, ["reserve", "finish"])
        self.assertTrue(result["verified"])
        self.assertEqual(result["result_id"], "result-1")
        self.assertEqual(result["responds_to"], ["REQ-1"])
        self.assertEqual(result["operation_id"], "pkb.register_claim")
        self.assertEqual(write.call_args.kwargs["invocation_id"], str(ACTION))
        self.assertEqual(write.call_args.kwargs["recorded_at"], AT)
        with self.assertRaisesRegex(ValueError, "pkb_write_already_reserved"):
            invoke(store)
        self.assertEqual(write.call_count, 1)

    @patch("ritsuko.application.pkb_claim_operation.register_structured_claim")
    def test_pending_review_cannot_become_verified_claim(self, write):
        write.return_value = {"status": "review", "reason": "entity_ambiguous"}
        result = invoke(FakeStore())
        self.assertFalse(result["verified"])
        self.assertEqual(result["verification_level"], "not_committed")

    @patch("ritsuko.application.pkb_claim_operation.register_structured_claim")
    def test_unpermitted_task_is_never_dispatched(self, write):
        with self.assertRaisesRegex(ValueError, "pkb_write_not_permitted"):
            invoke(FakeStore(permitted=False))
        write.assert_not_called()

    @patch("ritsuko.application.pkb_claim_operation.register_structured_claim")
    def test_uncertain_dispatch_marks_unknown_not_retry(self, write):
        write.side_effect = RuntimeError("response_lost_after_commit")
        store = FakeStore()
        with self.assertRaisesRegex(RuntimeError, "response_lost_after_commit"):
            invoke(store)
        self.assertEqual(store.events, ["reserve", "unknown"])
        self.assertEqual(store.actions[(TASK, "REQ-1")], "unknown")
        with self.assertRaisesRegex(ValueError, "pkb_write_already_reserved"):
            invoke(store)
        self.assertEqual(write.call_count, 1)


if __name__ == "__main__":
    unittest.main()
