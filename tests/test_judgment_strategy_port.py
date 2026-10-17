# Provenance-Origin: original
# Provenance-AI: AI-generated
"""MAGIの判断Adapterを交換してもRITSUKOのTask実行入口が変わらない回帰。"""
from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from ritsuko.magi.async_execution import (
    start_dialogue_async, continue_with_user_clarification_async,
)
from ritsuko.magi.judgment_strategies import (
    strategy_for_name, strategy_for_session,
)

SPECS = [
    {"name": name, "provider": "openai", "model": "fake",
     "enabled": True, "weight": 1.0, "timeout_seconds": 10}
    for name in ("MELCHIOR", "CASPER")
]


class JudgmentAdapterContractTests(unittest.IsolatedAsyncioTestCase):
    """Task Runnerが実装差ではなく永続化した判断方式だけを選ぶことを確認する。"""

    def test_stored_strategy_selects_same_adapter_even_after_restart(self):
        """開始時のStrategyと復元SessionのStrategyが一致する。"""
        standard = strategy_for_name("standard")
        goal = strategy_for_name("llm")
        self.assertIs(strategy_for_session({"decision_strategy": "standard"}), standard)
        self.assertIs(strategy_for_session({"decision_strategy": "llm"}), goal)
        self.assertIs(strategy_for_session({"control_mode": "goal_loop_v1"}), goal)
        self.assertFalse(standard.allows_recorded_read_failure)
        self.assertTrue(goal.allows_recorded_read_failure)
        with self.assertRaisesRegex(ValueError, "unknown_flow_decision_strategy"):
            strategy_for_name("unconfigured")

    async def test_new_task_uses_same_entry_but_only_selected_judgment(self):
        """共通Session上で従来方式だけclassifyし、Goal方式は初手からGoal評価する。"""
        async def stage_advance(session, **_kwargs):
            """既存段階MAGIの判断入口を置き換える模擬実行。"""
            return {**session, "status": "waiting_user", "next_step": "ask_user"}

        async def goal_advance(session, **_kwargs):
            """Goal判断入口へ渡されたTaskと席を保存せず観察する。"""
            return {**session, "status": "waiting_user", "next_step": "goal_ask_user"}

        with (patch("ritsuko.magi.async_execution._send_async",
                    new=AsyncMock(return_value={"multiple_requests": False})) as classify,
              patch("ritsuko.magi.async_execution._advance_async",
                    new=AsyncMock(side_effect=stage_advance)) as staged,
              patch("ritsuko.magi.goal_loop._run_goal_turn",
                    new=AsyncMock(side_effect=goal_advance)) as goal):
            a = await start_dialogue_async(
                "調査をお願いします", decision_strategy="standard",
                member_specs=SPECS, timeout=3, task_id="task-stage",
            )
            b = await start_dialogue_async(
                "調査をお願いします", decision_strategy="llm",
                member_specs=SPECS, timeout=3, task_id="task-goal",
            )
        self.assertEqual((a["task_id"], b["task_id"]), ("task-stage", "task-goal"))
        self.assertEqual((a["decision_strategy"], b["decision_strategy"]),
                         ("standard", "llm"))
        self.assertEqual(len(a["member_specs"]), 2)
        self.assertEqual(len(b["member_specs"]), 2)
        self.assertEqual(classify.await_count, 1)
        self.assertEqual(staged.await_count, 1)
        self.assertEqual(goal.await_count, 1)
        self.assertEqual(b["control_mode"], "goal_loop_v1")
        self.assertIn("requires_source", b)

    async def test_existing_task_answer_uses_matching_adapter_without_new_task(self):
        """保存済みTaskを本人回答で再開しても方式・Task IDを変えない。"""
        base = {
            "task_id": "persisted-task", "user_raw": "目的",
            "status": "waiting_user", "next_step": "ask_user",
            "turns": [], "member_specs": SPECS, "observations": [],
            "pending_requests": [], "classification": {"multiple_requests": False},
            "policy_snapshot": None,
        }
        async def stage_detail(session, _response, **_kwargs):
            """標準Adapterの再評価結果を同一Taskで返す。"""
            return {**session, "status": "candidate_ready"}

        async def goal_turn(session, **_kwargs):
            """Goal Adapterの再評価結果を同一Taskで返す。"""
            return {**session, "status": "candidate_ready"}

        with (patch("ritsuko.magi.async_execution._send_async",
                    new=AsyncMock(return_value={"state": "READY"})) as sender,
              patch("ritsuko.magi.async_execution._apply_detail_async",
                    new=AsyncMock(side_effect=stage_detail)) as applied,
              patch("ritsuko.magi.goal_loop._run_goal_turn",
                    new=AsyncMock(side_effect=goal_turn)) as evaluated):
            standard = await continue_with_user_clarification_async(
                {**base, "decision_strategy": "standard"},
                "ユーザーの追加情報", timeout=3,
            )
            goal = await continue_with_user_clarification_async(
                {**base, "decision_strategy": "llm",
                 "control_mode": "goal_loop_v1", "user_replies": []},
                "ユーザーの追加情報", timeout=3,
            )
        self.assertEqual(standard["task_id"], goal["task_id"])
        self.assertEqual(standard["decision_strategy"], "standard")
        self.assertEqual(goal["decision_strategy"], "llm")
        self.assertEqual(sender.await_count, 1)
        self.assertEqual(applied.await_count, 1)
        self.assertEqual(evaluated.await_count, 1)
        self.assertEqual(goal["user_replies"], ["ユーザーの追加情報"])
        self.assertEqual(standard["observations"][-1]["source"], "user_clarification")

    async def test_absent_goal_member_still_fails_without_provider_call(self):
        """Goal方式では有効席が0なら勝手に標準方式へfallbackしない。"""
        with self.assertRaisesRegex(ValueError, "no_enabled_magi_member"):
            await start_dialogue_async(
                "確認", member_specs=[{**SPECS[0], "enabled": False}],
                decision_strategy="llm", timeout=3,
            )
