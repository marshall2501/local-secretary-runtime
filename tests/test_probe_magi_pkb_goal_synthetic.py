# Provenance-Origin: original
# Provenance-AI: AI-assisted
"""Offline contract tests for a local-only fictional MAGI proposal probe."""
from __future__ import annotations

import unittest

from scripts.db.probe_magi_pkb_goal_synthetic import (
    ENTITY_ID, ENTITY_NAME, TEXT, VALUE,
    check_endpoint, evaluate_proposal, synthetic_observation,
    synthetic_session, invalid_proposal_diagnostics,
)
from ritsuko.magi.goal_contract import validate_goal_turn


def proposal(*, entity_id=ENTITY_ID, value=VALUE, predicate="model",
             start=0, end=None):
    return {
        "goal": "架空GPUの機種をPKBに記録する",
        "next": "invoke", "fulfillment": "partial", "answer": None,
        "evidence": {
            "basis": "result_refs",
            "result_ids": ["synthetic-read-result"],
            "assessment": "既存の架空PKB Entityを確認",
        },
        "read_request": None,
        "operation_request": {
            "operation_id": "pkb.register_claim",
            "arguments": {
                "entity_id": entity_id,
                "entity_mention": ENTITY_NAME,
                "predicate": predicate,
                "value": value,
                "evidence_start": start,
                "evidence_end": len(TEXT) if end is None else end,
            },
            "reason": "本人原文の明示属性を登録する",
        },
        "question": None,
        "reason": "属性の記憶が必要",
    }


class PkbGoalSyntheticProbeTests(unittest.TestCase):
    def test_invalid_ollama_response_reports_only_bounded_codes(self):
        """契約失敗を診断しても、LLM本文や例外の自由文を出力しない。"""
        session = synthetic_session(
            "fake", "http://127.0.0.1:11434", 20, "with-entity",
        )
        session["turns"].append({
            "member_results": [{
                "status": "invalid",
                "response": {
                    "next": "invoke",
                    "answer": "FICTIONAL-PRIVATE-DO-NOT-PRINT",
                    "operation_request": {
                        "operation_id": "pkb.register_claim",
                        "reason": "FICTIONAL-PRIVATE-DO-NOT-PRINT",
                    },
                },
                "errors": ["answer:unexpected", "dangerous raw output / path"],
                "diagnostic": {
                    "done_reason": "length", "content_length": 932,
                    "thinking_length": 0, "num_predict": 2048,
                    "untrusted_text": "FICTIONAL-PRIVATE-DO-NOT-PRINT",
                },
            }],
        })
        lines = invalid_proposal_diagnostics(session)
        self.assertIn("diagnostic: next=invoke", lines)
        self.assertIn("diagnostic: operation_id=pkb.register_claim", lines)
        self.assertIn("diagnostic: answer_present=true", lines)
        self.assertIn("diagnostic: error_codes=answer:unexpected,unclassified_error", lines)
        self.assertIn("diagnostic: done_reason=length", lines)
        self.assertIn("diagnostic: content_length=932", lines)
        self.assertNotIn("FICTIONAL-PRIVATE-DO-NOT-PRINT", "\n".join(lines))

    def test_untrusted_operation_or_nonstring_next_does_not_leak(self):
        """未知のモデル出力はIDや本文をそのまま表示しない。"""
        session = synthetic_session(
            "fake", "http://127.0.0.1:11434", 20, "with-entity",
        )
        session["turns"].append({
            "member_results": [{
                "status": "invalid",
                "response": {
                    "next": ["PRIVATE"],
                    "answer": None,
                    "operation_request": {"operation_id": "private://SECRET"},
                },
                "errors": ["goal:field_mismatch"],
                "diagnostic": {},
            }],
        })
        lines = "\n".join(invalid_proposal_diagnostics(session))
        self.assertIn("diagnostic: next=unrecognized", lines)
        self.assertIn("diagnostic: operation_id=none_or_unrecognized", lines)
        self.assertNotIn("private://SECRET", lines)
        self.assertNotIn("PRIVATE", lines)

    def test_probe_rejects_nonlocal_endpoints(self):
        for endpoint in (
            "https://127.0.0.1:11434",
            "http://example.com:11434",
            "http://127.0.0.1:3333",
            "http://127.0.0.1:11434/api/chat",
            "http://user:password@localhost:11434",
            "http://127.0.0.1:11434?secret=x",
        ):
            with self.subTest(endpoint=endpoint):
                with self.assertRaises(ValueError):
                    check_endpoint(endpoint)
        check_endpoint("http://127.0.0.1:11434")

    def test_fake_pkb_result_supplies_only_fictional_existing_entity(self):
        obs = synthetic_observation()
        self.assertEqual(obs["resolved_entity"]["id"], ENTITY_ID)
        self.assertEqual(obs["result_id"], "synthetic-read-result")
        self.assertTrue(obs["verified"])

    def test_readonly_proposal_passes_existing_pkb_write_gates_without_writing(self):
        state = synthetic_session("fake", "http://127.0.0.1:11434", 20, "with-entity")
        decision = proposal()
        self.assertEqual(validate_goal_turn(decision), [])
        self.assertEqual(evaluate_proposal("with-entity", state, decision),
                         (True, "PKB_schema_and_literal_gates_passed"))

    def test_forged_id_value_and_bad_span_do_not_pass(self):
        state = synthetic_session("fake", "http://127.0.0.1:11434", 20, "with-entity")
        for changes in (
            {"entity_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"},
            {"value": "MADE-UP"},
            {"start": 5, "end": 9},
        ):
            with self.subTest(changes=changes):
                passed, _ = evaluate_proposal(
                    "with-entity", state, proposal(**changes),
                )
                self.assertFalse(passed)

    def test_without_entity_requires_read_before_write(self):
        state = synthetic_session("fake", "http://127.0.0.1:11434", 20, "without-entity")
        self.assertEqual(state["observations"], [])
        self.assertFalse(evaluate_proposal("without-entity", state, proposal())[0])
        read = proposal()
        read["operation_request"] = {
            "operation_id": "pkb.search",
            "arguments": {"query": ENTITY_NAME},
            "reason": "既存対象の照合",
        }
        self.assertTrue(evaluate_proposal("without-entity", state, read)[0])

    def test_uncertain_case_must_not_propose_write(self):
        state = synthetic_session("fake", "http://127.0.0.1:11434", 20, "uncertain")
        self.assertFalse(evaluate_proposal("uncertain", state, proposal())[0])
        ask = proposal()
        ask.update(next="ask_user", question="機種は確定していますか？",
                   operation_request=None)
        self.assertTrue(evaluate_proposal("uncertain", state, ask)[0])


class PkbGoalSyntheticEnvelopeTests(unittest.IsolatedAsyncioTestCase):
    async def test_actual_magi_goal_envelope_receives_entity_and_write_catalog(self):
        """本番のMAGI Goal envelopeは実在IDとOperation契約を保持する。"""
        from ritsuko.magi.goal_decision import evaluate_goal
        session = synthetic_session("fake", "http://127.0.0.1:11434", 20, "with-entity")

        async def fake_ollama(spec, envelope, **_kwargs):
            self.assertEqual(spec["provider"], "ollama")
            self.assertEqual(envelope["user_input"]["raw"], TEXT)
            self.assertEqual(envelope["observations"][0]["resolved_entity"]["id"], ENTITY_ID)
            self.assertIn("pkb.register_claim", envelope["operation_catalog"])
            self.assertIn("model", envelope["operation_catalog"]["pkb.register_claim"]["predicates"])
            return {"status": "ok", "response": proposal()}

        result = await evaluate_goal(session, timeout=3, caller=fake_ollama)
        self.assertTrue(evaluate_proposal("with-entity", session, result)[0])
        self.assertEqual(session["turns"][0]["status"], "ok")



if __name__ == "__main__":
    unittest.main()
