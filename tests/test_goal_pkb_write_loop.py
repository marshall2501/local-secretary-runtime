# Provenance-Origin: original
# Provenance-AI: AI-assisted
"""One existing RITSUKO Task loop: MAGI propose → PKB write → Result → MAGI."""
from __future__ import annotations

from functools import partial
import unittest
from uuid import UUID

from ritsuko.core.task_execution import run_observation_loop
from ritsuko.core.task_projection import task_projection
from ritsuko.application.pkb_observation import execute_magi_pkb_request
from ritsuko.core.judgment_application import apply_judgment
from ritsuko.magi.session_workflow import (
    start_dialogue_async, continue_with_verified_observations_async,
)
from ritsuko.magi.goal_contract import validate_goal_turn

TASK = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
ATTEMPT = UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")
TEXT = "架空GPUをDRV-200へ更新した。"
SPEC = [{
    "name": "MELCHIOR", "provider": "ollama", "model": "fake",
    "endpoint": "http://127.0.0.1:11434", "enabled": True,
    "weight": 1.0, "timeout_seconds": 5,
}]
POLICY = {"profile_id": "fake", "profile_name": "test", "revision": 1,
          "grants": [{"label": "llm.send_context.private",
                      "destination_scope": "local", "decision": "allow",
                      "mask_rules": {"tokens": []}}]}


def turn(next_step, *, refs=(), answer=None):
    return {
        "goal": "架空GPUの更新をPKBに登録する",
        "next": next_step,
        "fulfillment": "met" if next_step == "respond" else "partial",
        "answer": answer,
        "evidence": {
            "basis": "result_refs" if refs else "not_required",
            "result_ids": list(refs), "assessment": "保存済みResultを参照する",
        },
        "read_request": None,
        "operation_request": {
            "operation_id": "pkb.register_claim",
            "arguments": {
                "entity_id": "cccccccc-cccc-4ccc-8ccc-cccccccccccc",
                "entity_mention": "架空GPU",
                "predicate": "driver_updated",
                "value": "DRV-200",
                "evidence_start": 0,
                "evidence_end": len(TEXT),
            },
            "reason": "本人が明示した更新を登録する",
        } if next_step == "invoke" else None,
        "question": None, "reason": "本人が要求した記録",
    }


class GoalPkbWriteLoopTests(unittest.IsolatedAsyncioTestCase):
    async def test_claim_write_result_reenters_magi_and_completes_task(self):
        self.assertEqual(validate_goal_turn(turn("invoke")), [])
        events = []

        async def member(_spec, envelope, **_kwargs):
            self.assertIn("pkb.register_claim", envelope["operation_catalog"])
            if envelope["turn"] == 1:
                events.append("magi_proposal")
                return {"status": "ok", "response": turn("invoke")}
            self.assertEqual(envelope["observations"][0]["result_id"], "result-write")
            self.assertEqual(envelope["observations"][0]["source"], "pkb_write")
            events.append("magi_re_evaluation")
            return {"status": "ok", "response": turn(
                "respond", refs=["result-write"], answer="架空GPUの更新を記録しました",
            )}

        def write(user_text, task_id, attempt_id, pending, _session):
            events.append("write")
            self.assertEqual((user_text, task_id, attempt_id), (TEXT, TASK, ATTEMPT))
            self.assertEqual(pending["operation_id"], "pkb.register_claim")
            self.assertEqual(pending["arguments"]["predicate"], "driver_updated")
            return {
                "source": "pkb_write", "operation_id": "pkb.register_claim",
                "capability": "pkb_structured_claim", "verified": True,
                "verification_level": "pkb_repository_receipt",
                "text": "PKB claim inserted",
                "responds_to": [pending["request_id"]],
                "action_id": "action-write", "result_id": "result-write",
            }

        def checkpoint(*_args):
            events.append("checkpoint")

        def persist(task_id, session, _capability, attempt_id):
            self.assertEqual((task_id, attempt_id), (TASK, ATTEMPT))
            self.assertEqual(task_projection(session)["task_status"], "completed")
            events.append("persist")

        session = await run_observation_loop(
            TEXT, task_id=TASK, member_specs=SPEC, timeout=5,
            create_task_record=lambda *_args: ATTEMPT,
            execute_source_request=lambda *_args: self.fail("read should not run"),
            record_source_read_record=lambda *_args: self.fail("write is not a read"),
            execute_write_operation=write,
            checkpoint_source_progress=checkpoint,
            persist_session_record=persist,
            fail_task_record=lambda *_args: self.fail("unexpected error"),
            dialogue_starter=partial(
                start_dialogue_async, decision_strategy="llm",
                goal_caller=member, policy_snapshot=POLICY,
            ),
            observation_continuation=partial(
                continue_with_verified_observations_async, goal_caller=member,
            ),
        )
        self.assertEqual(task_projection(session)["task_status"], "completed")
        self.assertEqual(events, [
            "magi_proposal", "write", "checkpoint", "magi_re_evaluation", "persist",
        ])

    async def test_existing_entity_read_then_write_uses_one_task_and_result(self):
        """本人原文の一意Entityを読んでから同じTask内で記録・再判断する。"""
        request = "架空機材AをDRV-200へ更新した。"
        entity_id = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"
        events = []

        async def member(_spec, envelope, **_kwargs):
            if envelope["turn"] == 1:
                events.append("magi_read")
                decision = turn("invoke")
                decision["operation_request"] = {
                    "operation_id": "pkb.search",
                    "arguments": {"query": "架空機材A"},
                    "reason": "既存EntityのIDを確認する",
                }
                return {"status": "ok", "response": decision}
            if envelope["turn"] == 2:
                observed = envelope["observations"][0]
                self.assertEqual(observed["source"], "pkb")
                self.assertEqual(observed["result_id"], "result-read")
                self.assertEqual(observed["resolved_entity"]["id"], entity_id)
                events.append("magi_write")
                decision = turn("invoke")
                decision["operation_request"]["arguments"].update({
                    "entity_id": observed["resolved_entity"]["id"],
                    "entity_mention": "架空機材A",
                    "evidence_end": len(request),
                })
                self.assertEqual(validate_goal_turn(decision), [])
                return {"status": "ok", "response": decision}
            self.assertEqual(envelope["turn"], 3)
            self.assertEqual(envelope["observations"][1]["result_id"], "result-write")
            events.append("magi_re_evaluation")
            return {"status": "ok", "response": turn(
                "respond", refs=["result-write"],
                answer="架空機材Aの更新を登録しました",
            )}

        def read(user_raw, pending, _session):
            events.append("pkb_read")
            return execute_magi_pkb_request(
                user_raw, pending, pkb_repository=None,
                entity_map=lambda: {
                    "架空機材A": {
                        "id": entity_id,
                        "name": "架空機材A", "entity_type": "equipment",
                    },
                },
                execute_read=lambda capability, query: {
                    "status": "ok", "operation": "search",
                    "verified_by": "deterministic_pkb_query",
                    "answer": "架空機材AにはClaimがまだありません。",
                    "result": {
                        "result_kind": "claims", "total": 0, "items": [],
                    },
                },
            )

        def record(task_id, execution, pending, attempt_id):
            self.assertEqual((task_id, attempt_id), (TASK, ATTEMPT))
            events.append("read_result")
            return ("action-read", "result-read")

        def write(user_raw, task_id, attempt_id, pending, session):
            self.assertEqual((user_raw, task_id, attempt_id), (request, TASK, ATTEMPT))
            self.assertEqual(session["observations"][0]["resolved_entity"]["id"], entity_id)
            self.assertEqual(pending["arguments"]["entity_id"], entity_id)
            events.append("pkb_write")
            return {
                "source": "pkb_write", "operation_id": "pkb.register_claim",
                "capability": "pkb_structured_claim", "verified": True,
                "verification_level": "pkb_repository_receipt",
                "text": "PKB claim inserted", "responds_to": [pending["request_id"]],
                "action_id": "action-write", "result_id": "result-write",
            }

        def checkpoint(*_args):
            events.append("checkpoint")

        def persist(task_id, session, _capability, attempt_id):
            self.assertEqual((task_id, attempt_id), (TASK, ATTEMPT))
            self.assertEqual(task_projection(session)["task_status"], "completed")
            events.append("completed")

        session = await run_observation_loop(
            request, task_id=TASK, member_specs=SPEC, timeout=5,
            create_task_record=lambda *_args: ATTEMPT,
            execute_source_request=read,
            record_source_read_record=record,
            execute_write_operation=write,
            checkpoint_source_progress=checkpoint,
            persist_session_record=persist,
            fail_task_record=lambda *_args: self.fail("unexpected failure"),
            dialogue_starter=partial(
                start_dialogue_async, decision_strategy="llm",
                goal_caller=member, policy_snapshot=POLICY,
            ),
            observation_continuation=partial(
                continue_with_verified_observations_async, goal_caller=member,
            ),
        )
        self.assertEqual(task_projection(session)["task_status"], "completed")
        self.assertEqual(events, [
            "magi_read", "pkb_read", "read_result", "checkpoint",
            "magi_write", "pkb_write", "checkpoint",
            "magi_re_evaluation", "completed",
        ])

    async def test_write_goal_cannot_complete_without_actual_write_receipt(self):
        async def member(_spec, envelope, **_kwargs):
            return {"status": "ok", "response": turn("invoke")}
        session = await start_dialogue_async(
            TEXT, member_specs=SPEC, timeout=5, pkb_claim_write_enabled=True,
            decision_strategy="llm", policy_snapshot=POLICY, goal_caller=member,
        )
        self.assertEqual(session["status"], "waiting_information")
        # MAGI cannot override a requested side effect with a source-free claim.
        await apply_judgment(
            session, turn("respond", answer="登録済みと主張"),
            strategy="llm", purpose="evaluate_goal",
        )
        self.assertEqual(session["next_step"], "pkb_write_result_required")
        self.assertNotEqual(task_projection(session)["task_status"], "completed")

        # A genuine but unrelated read result also cannot substitute for a
        # confirmed PKB write Result.
        session["status"] = "running"
        session["observations"] = [{
            "source": "pkb", "verified": True,
            "result_id": "read-result", "action_id": "read-action",
            "text": "架空GPUを読んだだけ",
        }]
        await apply_judgment(
            session, turn("respond", refs=["read-result"], answer="登録済みと主張"),
            strategy="llm", purpose="evaluate_goal",
        )
        self.assertEqual(session["next_step"], "pkb_write_result_required")
        self.assertNotEqual(task_projection(session)["task_status"], "completed")

    async def test_without_write_executor_magi_cannot_register(self):
        async def member(_spec, envelope, **_kwargs):
            self.assertNotIn("pkb.register_claim", envelope["operation_catalog"])
            return {"status": "ok", "response": turn("invoke")}
        session = await start_dialogue_async(
            TEXT, member_specs=SPEC, timeout=5,
            decision_strategy="llm", policy_snapshot=POLICY,
            goal_caller=member,
        )
        self.assertEqual(session["status"], "stopped")
        self.assertEqual(session["next_step"], "operation_not_registered")
        self.assertNotEqual(task_projection(session)["task_status"], "completed")


if __name__ == "__main__":
    unittest.main()
