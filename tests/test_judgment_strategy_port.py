# Provenance-Origin: original
# Provenance-AI: AI-generated
"""Acceptance of real proposal-only strategies and one RITSUKO state applier."""
from __future__ import annotations

import unittest

from ritsuko.magi.session_workflow import (
    continue_with_user_clarification_async,
    start_dialogue_async,
)
from ritsuko.magi.judgment_strategies import (
    strategy_for_name, strategy_for_session,
)
from ritsuko.core.task_projection import task_projection


SPECS = [
    {"name": name, "provider": "openai", "model": "fake",
     "enabled": True, "weight": 1.0, "timeout_seconds": 10}
    for name in ("MELCHIOR", "CASPER")
]


def _classification():
    """Valid schema-conforming initial stage classification."""
    return {
        "category": "INFORMATION", "understood_request": "辞書の説明",
        "reason": "説明を要求", "confidence": "high",
        "multiple_requests": False,
    }


def _stage_detail(state: str, *, answer: str | None = None):
    """Valid schema-conforming staged MAGI response."""
    return {
        "understood_request": "辞書の説明", "state": state,
        "reason": "判断した", "information_requests": [],
        "question_for_user": "もう少し教えて" if state == "NEED_CLARIFICATION" else None,
        "answer_candidate": answer, "knowledge_candidate": None,
        "action_candidate": None,
    }


def _goal_decision(next_step: str, *, answer=None, question=None):
    """One valid fake Goal proposal; it cannot assert execution receipts."""
    return {
        "goal": "辞書を説明する", "next": next_step,
        "fulfillment": "met" if next_step == "respond" else "unknown",
        "answer": answer,
        "evidence": {
            "basis": "not_required", "result_ids": [],
            "assessment": "一般説明にSourceは不要",
        },
        "read_request": None, "question": question, "reason": "判断した",
    }


class JudgmentStrategyContractTests(unittest.IsolatedAsyncioTestCase):
    """Both strategies choose only a MAGI proposal, not a Task executor."""

    def test_strategy_is_restored_from_persisted_task(self):
        standard = strategy_for_name("standard")
        goal = strategy_for_name("llm")
        self.assertIs(strategy_for_session({"decision_strategy": "standard"}), standard)
        self.assertIs(strategy_for_session({"decision_strategy": "llm"}), goal)
        self.assertIs(strategy_for_session({"control_mode": "goal_loop_v1"}), goal)
        self.assertFalse(standard.allows_recorded_read_failure)
        self.assertTrue(goal.allows_recorded_read_failure)
        with self.assertRaisesRegex(ValueError, "unknown_flow_decision_strategy"):
            strategy_for_name("unconfigured")

    async def test_new_task_selects_one_judgment_and_shared_state_application(self):
        stages = []
        goals = []

        async def stage_caller(envelope, **_kwargs):
            stages.append(envelope["stage"])
            if envelope["stage"] == "classify":
                return {"status": "ok", "response": _classification()}
            return {"status": "ok", "response": _stage_detail(
                "READY", answer="辞書はキーと値を結び付けます",
            )}

        async def goal_caller(spec, envelope, **_kwargs):
            goals.append((spec["name"], envelope["stage"]))
            return {"status": "ok", "response": _goal_decision(
                "respond", answer="辞書はキーと値の組です",
            )}

        staged = await start_dialogue_async(
            "辞書を説明して", decision_strategy="standard",
            member_specs=SPECS, timeout=3, task_id="task-stage",
            caller=stage_caller,
        )
        goal = await start_dialogue_async(
            "辞書を説明して", decision_strategy="llm",
            member_specs=SPECS, timeout=3, task_id="task-goal",
            goal_caller=goal_caller,
        )
        self.assertEqual(staged["decision_strategy"], "standard")
        self.assertEqual(goal["decision_strategy"], "llm")
        self.assertEqual(staged["status"], "candidate_ready")
        self.assertEqual(goal["status"], "candidate_ready")
        # Stage retains explicit review for ungrounded READY; Goal's validated
        # evidence-free fulfillment can complete through the same Task projection.
        self.assertEqual(task_projection(staged)["phase"], "awaiting_review")
        self.assertEqual(task_projection(goal)["phase"], "completed")
        self.assertEqual(stages, ["classify", "analyze", "analyze"])
        self.assertEqual({name for name, _ in goals}, {"MELCHIOR", "CASPER"})
        self.assertEqual(len(goal["turns"][0]["member_results"]), 2)
        self.assertEqual(goal["turns"][0]["diagnostic"]["mode"], "goal_llm_panel")

    async def test_saved_task_answer_reuses_task_and_same_core(self):
        base = {
            "task_id": "persisted-task", "user_raw": "辞書の説明",
            "status": "waiting_user", "next_step": "ask_user",
            "turns": [], "member_specs": SPECS, "observations": [],
            "pending_requests": [],
            "classification": _classification(),
            "policy_snapshot": None,
            "prompt_version": "test", "runtime_context": None,
            "previous_request_signatures": [],
        }

        async def stage_caller(envelope, **_kwargs):
            self.assertEqual(envelope["question_purpose"], "evaluate_observation")
            return {"status": "ok", "response": _stage_detail(
                "READY", answer="説明しました",
            )}

        async def goal_caller(_spec, envelope, **_kwargs):
            self.assertEqual(envelope["user_replies"], ["詳しく説明して"])
            return {"status": "ok", "response": _goal_decision(
                "respond", answer="より詳しい説明",
            )}

        standard = await continue_with_user_clarification_async(
            {**base, "decision_strategy": "standard"},
            "詳しく説明して", timeout=3, caller=stage_caller,
        )
        goal = await continue_with_user_clarification_async(
            {**base, "decision_strategy": "llm",
             "control_mode": "goal_loop_v1", "user_replies": [],
             "requires_source": False, "requires_web": False},
            "詳しく説明して", timeout=3, goal_caller=goal_caller,
        )
        self.assertEqual(standard["task_id"], goal["task_id"])
        self.assertEqual(standard["status"], "candidate_ready")
        self.assertEqual(goal["status"], "candidate_ready")
        self.assertEqual(goal["user_replies"], ["詳しく説明して"])
        self.assertEqual(standard["observations"][-1]["source"], "user_clarification")

    async def test_zero_enabled_goal_seats_fail_closed(self):
        with self.assertRaisesRegex(ValueError, "no_enabled_magi_member"):
            await start_dialogue_async(
                "確認", member_specs=[{**SPECS[0], "enabled": False}],
                decision_strategy="llm", timeout=3,
            )


if __name__ == "__main__":
    unittest.main()
