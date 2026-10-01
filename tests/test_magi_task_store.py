from __future__ import annotations

import unittest
from uuid import UUID

from pkb_proto.magi_task_store import (
    claim_user_resume,
    complete_answer_only,
    complete_memory_review,
    create_task,
    persist_session,
    prepare_memory_intake,
    record_pkb_read,
)


TASK_ID = UUID("11111111-1111-4111-8111-111111111111")
ACTION_ID = UUID("22222222-2222-4222-8222-222222222222")
RESULT_ID = UUID("33333333-3333-4333-8333-333333333333")
SOURCE_ID = UUID("44444444-4444-4444-8444-444444444444")


class _Context:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class _Cursor(_Context):
    def __init__(self, fetches=()):
        self.fetches = list(fetches)
        self.calls = []

    def execute(self, sql, params=()):
        self.calls.append((sql, params))

    def fetchone(self):
        if not self.fetches:
            return None
        return self.fetches.pop(0)


class _DB:
    def __init__(self, fetches=()):
        self.cur = _Cursor(fetches)

    def transaction(self):
        return _Context()

    def cursor(self):
        return self.cur


class MagiTaskStoreTests(unittest.TestCase):
    def test_create_task_uses_state_driven_core_slice_and_read_only_scope(self):
        db = _DB()
        create_task(
            db,
            task_id=TASK_ID,
            request="メインPCのGPUの種類は？",
            member_specs=[{"name": "MELCHIOR", "enabled": True}],
        )
        task_call = next(
            call for call in db.cur.calls
            if "INSERT INTO secretary.tasks" in call[0]
        )
        self.assertEqual(task_call[1][0], TASK_ID)
        checkpoint = task_call[1][-1].obj
        scope = task_call[1][-2].obj
        self.assertEqual(checkpoint["core_slice"], "ritsuko_magi_observation_v1")
        self.assertTrue(scope["pkb_read"])
        self.assertFalse(scope["web_research"])
        self.assertFalse(scope["cloud_private_pkb_context"])

    def test_claim_user_resume_requires_waiting_magi_task_and_preserves_snapshot(self):
        saved_session = {
            "task_id": str(TASK_ID),
            "status": "waiting_user",
            "pending_requests": [{"request_id": "REQ-1", "source": "user"}],
        }
        db = _DB(fetches=[(
            "waiting_external",
            {
                "core_slice": "ritsuko_magi_observation_v1",
                "selected_capability": "pkb_search",
                "magi_session": saved_session,
            },
        )])
        session, capability = claim_user_resume(
            db,
            task_id=TASK_ID,
            reply_length=18,
        )
        self.assertEqual(session, saved_session)
        self.assertEqual(capability, "pkb_search")
        sql = "\n".join(call[0] for call in db.cur.calls)
        self.assertIn("SET status='running'", sql)
        self.assertIn("core.magi.user_reply_received", sql)
        audit = next(
            call for call in db.cur.calls
            if "core.magi.user_reply_received" in call[0]
        )
        self.assertEqual(audit[1][-1].obj["reply_length"], 18)
        self.assertEqual(audit[1][-1].obj["pending_request_ids"], ["REQ-1"])

    def test_persist_verified_candidate_completes_task(self):
        db = _DB(fetches=[({"core_slice": "ritsuko_magi_observation_v1"},)])
        projection = persist_session(
            db,
            task_id=TASK_ID,
            selected_capability="pkb_search",
            session={
                "status": "candidate_ready",
                "next_step": "review_answer_candidate",
                "tool_read_executed": True,
                "observations": [
                    {"source": "pkb", "verified": True, "text": "verified"}
                ],
                "detail": {
                    "answer_candidate": "メインPCのGPUは Radeon RX 9070 XT です。"
                },
                "turns": [{}, {}, {}],
            },
        )
        self.assertEqual(projection["task_status"], "completed")
        update_call = next(
            call for call in db.cur.calls
            if "UPDATE secretary.tasks" in call[0]
        )
        self.assertEqual(update_call[1][0], "completed")
        self.assertEqual(update_call[1][1].obj["selected_capability"], "pkb_search")

    @staticmethod
    def review_session():
        return {
            "status": "proposal_ready",
            "next_step": "review_proposal",
            "tool_read_executed": True,
            "detail": {
                "state": "KNOWLEDGE_CANDIDATE",
                "answer_candidate": "メインPCのGPUはRadeon RX 9070 XTです。",
                "knowledge_candidate": "メインPCのGPUモデル名: Radeon RX 9070 XT",
            },
            "observations": [
                {"source": "pkb", "verified": True, "text": "model unavailable"},
                {
                    "source": "user_clarification",
                    "text": "Radeon RX 9070 XT",
                    "responds_to": ["REQ-1"],
                },
            ],
        }

    def test_answer_only_completes_user_grounded_proposal_without_memory(self):
        db = _DB(fetches=[(
            "waiting_external",
            {
                "core_slice": "ritsuko_magi_observation_v1",
                "magi_session": self.review_session(),
            },
        )])
        result = complete_answer_only(db, task_id=TASK_ID)
        self.assertEqual(result["decision"], "answer_only")
        self.assertIsNone(result["memory_intake"])
        update = next(
            call for call in db.cur.calls
            if "SET status='completed'" in call[0]
        )
        checkpoint = update[1][0].obj
        self.assertEqual(checkpoint["message"], "メインPCのGPUはRadeon RX 9070 XTです。")
        self.assertEqual(checkpoint["proposal_review"]["decision"], "answer_only")

    def test_memory_review_uses_persisted_retry_safe_envelope_then_completes(self):
        initial = {
            "core_slice": "ritsuko_magi_observation_v1",
            "magi_session": self.review_session(),
        }
        db = _DB(fetches=[("waiting_external", initial)])
        intake = prepare_memory_intake(db, task_id=TASK_ID)
        self.assertEqual(
            intake.raw_text,
            "メインPCのGPUモデル名: Radeon RX 9070 XT",
        )
        prepared_update = next(
            call for call in db.cur.calls
            if "UPDATE secretary.tasks SET checkpoint" in call[0]
        )
        prepared_checkpoint = prepared_update[1][0].obj
        self.assertEqual(
            prepared_checkpoint["proposal_memory_intake"]["input_id"],
            intake.input_id,
        )

        completion_db = _DB(fetches=[(
            "waiting_external",
            prepared_checkpoint,
        )])
        review = complete_memory_review(
            completion_db,
            task_id=TASK_ID,
            memory_result={
                "status": "committed",
                "input_id": intake.input_id,
                "source_id": str(SOURCE_ID),
                "candidates": [{
                    "candidate_id": "1",
                    "decision": "pending",
                    "reason": "unresolved",
                    "claim_id": None,
                    "pending_id": "pending-1",
                    "derived_claim_ids": [],
                }],
            },
        )
        self.assertEqual(review["decision"], "remember")
        self.assertEqual(
            review["memory_intake"]["receipts"][0]["decision"],
            "pending",
        )
        update = next(
            call for call in completion_db.cur.calls
            if "SET status='completed'" in call[0]
        )
        self.assertEqual(update[1][0].obj["proposal_review"]["decision"], "remember")

    def test_record_pkb_read_writes_source_action_result_and_checkpoint(self):
        db = _DB(fetches=[
            None,
            (SOURCE_ID,),
            (ACTION_ID,),
            (RESULT_ID,),
            ({"core_slice": "ritsuko_magi_observation_v1"},),
        ])
        action_id, result_id = record_pkb_read(
            db,
            task_id=TASK_ID,
            pending_request={
                "request_ids": ["REQ-1"],
                "what": "メインPCのGPUモデル",
            },
            execution={
                "tool": "pkb",
                "operation": "entity_detail",
                "citation": "fixture",
                "verified_by": "deterministic_pkb_query",
                "total": 1,
                "answer": "Radeon RX 9070 XT",
                "result": {
                    "result_kind": "entity_detail",
                    "current": [{"predicate": "model", "value": "Radeon RX 9070 XT"}],
                },
            },
        )
        self.assertEqual(action_id, str(ACTION_ID))
        self.assertEqual(result_id, str(RESULT_ID))
        sql = "\n".join(call[0] for call in db.cur.calls)
        self.assertIn("INSERT INTO secretary.sources", sql)
        self.assertIn("INSERT INTO secretary.actions", sql)
        self.assertIn("INSERT INTO secretary.results", sql)
        self.assertIn("core.magi.pkb_observed", sql)


if __name__ == "__main__":
    unittest.main()
