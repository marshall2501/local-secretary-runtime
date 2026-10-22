# Provenance-Origin: original
# Provenance-AI: AI-generated
"""MAGI Goal schema and proposal-only decision checks; no old Goal runner."""
from __future__ import annotations

import unittest
from uuid import uuid4

from ritsuko.magi.goal_contract import validate_goal_turn
from ritsuko.magi.goal_decision import evaluate_goal


def judgment(next_step, *, goal="Python の公開情報について回答する", answer=None,
             fulfillment="unknown", basis="insufficient", refs=None, request=None,
             question=None, reason="判断理由"):
    return {
        "goal": goal, "next": next_step, "fulfillment": fulfillment,
        "answer": answer, "evidence": {
            "basis": basis, "result_ids": list(refs or []),
            "assessment": "今回のGoalについて根拠の確認範囲を示す",
        },
        "read_request": request, "question": question, "reason": reason,
    }


class GoalContractTests(unittest.TestCase):
    def test_source_free_and_web_read_are_distinct_valid_proposals(self):
        direct = judgment("respond", answer="辞書はkey/value構造です。",
                          fulfillment="met", basis="not_required")
        read = judgment("request_read", request={
            "source": "web", "query": "Python dict.get official docs",
            "reason": "本人が公式資料を要求",
        })
        self.assertEqual(validate_goal_turn(direct), [])
        self.assertEqual(validate_goal_turn(read), [])

    def test_nonrespond_answer_is_rejected_and_prompt_tells_model_null(self):
        """LLMの余分なanswerで未実行操作が完了扱いにならない。"""
        from ritsuko.magi.goal_contract import GOAL_SYSTEM
        self.assertIn("next=invoke", GOAL_SYSTEM)
        self.assertIn("answer=null", GOAL_SYSTEM)
        self.assertIn("Unicode文字インデックス", GOAL_SYSTEM)
        self.assertIn("raw[evidence_start:evidence_end]", GOAL_SYSTEM)
        self.assertIn("未確定なら", GOAL_SYSTEM)
        self.assertIn("かもしれない", GOAL_SYSTEM)
        self.assertIn("登録操作ではなく", GOAL_SYSTEM)
        proposal = judgment("invoke", answer="架空GPUの機種を登録しました")
        proposal["operation_request"] = {
            "operation_id": "pkb.search",
            "arguments": {"query": "架空GPU"},
            "reason": "登録対象を確認",
        }
        self.assertIn("answer:unexpected", validate_goal_turn(proposal))
        proposal["answer"] = None
        self.assertEqual(validate_goal_turn(proposal), [])

    def test_post_write_goal_instruction_cites_persisted_write_result(self):
        """PKB登録後のGoal達成評価には既存の書込みResultを明示させる。"""
        from ritsuko.magi.goal_contract import GOAL_SYSTEM
        from ritsuko.core.goal_evidence import check_goal_response_evidence

        self.assertIn("source=pkb_write", GOAL_SYSTEM)
        self.assertIn("verified=true", GOAL_SYSTEM)
        self.assertIn("evidence.basis=result_refs", GOAL_SYSTEM)
        self.assertIn("evidence.result_ids", GOAL_SYSTEM)
        self.assertIn("書込みを伴うGoalにnot_requiredを使ってはいけません", GOAL_SYSTEM)

        session = {
            "pkb_claim_write_requested": True,
            "observations": [{
                "source": "pkb_write", "verified": True,
                "action_id": "action-1", "result_id": "write-result-1",
                "verification_level": "pkb_repository_receipt",
            }],
        }
        absent = {"basis": "not_required", "result_ids": []}
        grounded = {"basis": "result_refs", "result_ids": ["write-result-1"]}
        invented = {"basis": "result_refs", "result_ids": ["invented"]}
        self.assertEqual(
            check_goal_response_evidence(session, absent)[0],
            "pkb_write_result_required",
        )
        self.assertEqual(
            check_goal_response_evidence(session, grounded)[0],
            "verified_results",
        )
        self.assertEqual(
            check_goal_response_evidence(session, invented)[0],
            "unrecorded_task_result_reference",
        )

    def test_invoke_registered_read_and_reject_unregistered_operation(self):
        # New MAGI judgments use one typed Operation request, not domain IFs.
        read = judgment("invoke")
        read["operation_request"] = {
            "operation_id": "pkb.search",
            "arguments": {"query": "架空PCのGPU"},
            "reason": "既存記憶を照会する",
        }
        self.assertEqual(validate_goal_turn(read), [])
        read["operation_request"]["operation_id"] = "pkb.register"
        self.assertIn("operation_request:invalid", validate_goal_turn(read))
        read["operation_request"]["operation_id"] = "pkb.search"
        read["operation_request"]["arguments"] = {"query": ""}
        self.assertIn("operation_request:invalid", validate_goal_turn(read))
        read["next"] = "respond"
        self.assertIn("operation_request:state_mismatch", validate_goal_turn(read))

    def test_attribute_write_predicates_are_only_existing_pkb_contract(self):
        """MAGIは正式に許された属性のみ提案できる。"""
        from pkb.write_service import KNOWN_PREDICATES
        from ritsuko.magi.goal_contract import GOAL_SCHEMA
        schema = GOAL_SCHEMA["properties"]["operation_request"]["anyOf"][1]
        labels = schema["properties"]["arguments"]["properties"]["predicate"]["enum"]
        self.assertEqual(labels, sorted(KNOWN_PREDICATES))
        for predicate in ("model", "manufacturer"):
            proposal = judgment("invoke")
            proposal["operation_request"] = {
                "operation_id": "pkb.register_claim",
                "arguments": {
                    "entity_id": "cccccccc-cccc-4ccc-8ccc-cccccccccccc",
                    "entity_mention": "架空GPU", "predicate": predicate,
                    "value": "架空値", "evidence_start": 0,
                    "evidence_end": 15,
                },
                "reason": "本人原文に明示された属性",
            }
            self.assertEqual(validate_goal_turn(proposal), [])
        proposal["operation_request"]["arguments"]["predicate"] = "account_balance"
        self.assertIn("operation_request:invalid", validate_goal_turn(proposal))

    def test_malformed_operation_id_fails_closed_without_exception(self):
        case = judgment("invoke")
        case["operation_request"] = {
            "operation_id": ["pkb.register_claim"],
            "arguments": {"query": "架空PC"},
            "reason": "不正な識別子",
        }
        self.assertIn("operation_request:invalid", validate_goal_turn(case))

    def test_new_goal_reply_does_not_allow_write_or_extra_arguments(self):
        reply = judgment("invoke")
        reply["operation_request"] = {
            "operation_id": "finance.read",
            "arguments": {"query": "架空の収支", "write": "YES"},
            "reason": "調べるため",
        }
        self.assertIn("operation_request:invalid", validate_goal_turn(reply))
        reply["operation_request"] = None
        self.assertIn("operation_request:state_mismatch", validate_goal_turn(reply))

    def test_rejects_model_claim_of_missing_evidence(self):
        x = judgment("respond", answer="確認済みです", fulfillment="met",
                     basis="result_refs", refs=[])
        self.assertIn("evidence:missing_refs", validate_goal_turn(x))
        x["read_request"] = {"source": "external_service",
                             "query": "do something", "reason": "X"}
        self.assertIn("read_request:invalid", validate_goal_turn(x))

    def test_insufficient_but_recorded_result_may_explain_missing_coverage(self):
        """A cited search Result can be insufficient for direct-page verification."""
        existing = str(uuid4())
        x = judgment("request_read", fulfillment="partial", basis="insufficient",
                     refs=[existing], request={
                         "source": "web", "query": "official page source",
                         "reason": "provider citation is not direct content verification",
                     })
        self.assertEqual(validate_goal_turn(x), [])
        # Refs are never allowed when the model claims NO evidence is needed.
        x["evidence"]["basis"] = "not_required"
        self.assertIn("evidence:unexpected_refs", validate_goal_turn(x))
        x["evidence"]["basis"] = "insufficient"
        x["next"] = "respond"
        x["read_request"] = None
        x["answer"] = "Unverified source is sufficient"
        x["fulfillment"] = "met"
        self.assertIn("respond:insufficient_evidence", validate_goal_turn(x))

    def test_malformed_and_dangerous_state_pairs(self):
        x = judgment("respond", answer="result", fulfillment="unknown",
                     basis="not_required")
        self.assertIn("respond:answer_or_goal_missing", validate_goal_turn(x))
        x = judgment("ask_user")
        self.assertIn("ask_user:question_required", validate_goal_turn(x))



class GoalDecisionTests(unittest.IsolatedAsyncioTestCase):
    """Goal judgment returns a proposal; only RITSUKO commits Task state."""

    async def test_valid_single_member_proposal_does_not_execute_tools(self):
        member = {
            "name": "CASPER", "provider": "openai", "model": "fake",
            "enabled": True, "weight": 1.0,
        }
        session = {
            "task_id": "synthetic-task", "user_raw": "辞書とは",
            "member_specs": [member], "turns": [],
            "observations": [], "runtime_context": None,
            "status": "running", "next_step": "evaluate_goal",
        }
        events = []

        async def fake_member(spec, envelope, **_kwargs):
            self.assertEqual(spec["name"], "CASPER")
            self.assertEqual(envelope["stage"], "goal")
            self.assertEqual(envelope["user_input"]["raw"], "辞書とは")
            return {
                "status": "ok", "response": judgment(
                    "respond", answer="キーと値の対応", fulfillment="met",
                    basis="not_required",
                ),
            }

        decided = await evaluate_goal(
            session, timeout=5, caller=fake_member,
            on_member_progress=lambda item: events.append(item["state"]),
        )
        self.assertEqual(decided["next"], "respond")
        self.assertEqual(session["status"], "running")
        self.assertEqual(session["next_step"], "evaluate_goal")
        self.assertNotIn("actions", session)
        self.assertNotIn("results", session)
        self.assertEqual(len(session["turns"]), 1)
        self.assertEqual(events, ["queued", "running", "completed"])

    async def test_invalid_member_proposal_does_not_complete_task(self):
        session = {
            "task_id": "invalid-task", "user_raw": "教えて",
            "member_specs": [{
                "name": "CASPER", "provider": "openai",
                "model": "fake", "enabled": True,
            }],
            "turns": [], "observations": [],
            "status": "running", "next_step": "evaluate_goal",
        }

        async def fake_member(_spec, _envelope, **_kwargs):
            return {"status": "ok", "response": {"next": "respond"}}

        decided = await evaluate_goal(session, timeout=5, caller=fake_member)
        self.assertIsNone(decided)
        self.assertEqual(session["status"], "stopped")
        self.assertEqual(session["next_step"], "goal_member_invalid")
        self.assertNotIn("completed", session.get("status", ""))


if __name__ == "__main__":
    unittest.main()
