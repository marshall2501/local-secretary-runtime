# Provenance-Origin: original
# Provenance-AI: AI-generated
"""GUI非依存のTask受付・停止要求・再開競合をDBなしで検証する。

実Task Repositoryや外部MAGIを使わないため、このテストの完了は
実DB/外部Sourceを接続した日常運用の受入ではない。
"""
from __future__ import annotations

import asyncio
import unittest
from uuid import uuid4

from ritsuko.application.task_commands import (
    TaskCommandConflict, TaskCommandNotFound, TaskCommands,
)


class FakeQueries:
    """受付前のTask状態を固定値として返し、DBのclaim判定は再現しない。"""

    def __init__(self):
        """既知Taskを一件用意し、回答とReviewの準備状態を切り替えられるようにする。"""
        self.uid = uuid4()
        self.task = {
            "status": "waiting_external",
            "magi_session": {
                "status": "waiting_user",
                "member_specs": [{"name": "MELCHIOR", "enabled": True, "timeout_seconds": 9}],
            },
        }

    def trace(self, uid):
        """既知Taskだけ詳細を返す。存在しないIDはQueryの不在エラーを模倣する。"""
        if uid != self.uid:
            raise ValueError("unknown_task")
        return {"task": self.task, "actions": [], "advisor_events": []}


class FakeApplication:
    """共通Applicationのstart/answer/review呼び出しだけを観測する試験用入口。"""

    def __init__(self):
        """実行開始と完了を分け、受付202と停止要求の動作を観測可能にする。"""
        self.called = []
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    async def start(self, request, **kwargs):
        """指定Task ID、MAGI設定、協調停止関数を受け取って完了を待つ。"""
        self.called.append(("start", request, kwargs))
        self.entered.set()
        await self.release.wait()
        return {"status": "waiting_user"}

    async def answer(self, task_id, text, **kwargs):
        """本人回答を同一Task IDで処理し、入力とTimeoutを記録する。"""
        self.called.append(("answer", task_id, text, kwargs))
        return {"status": "proposal_ready"}

    async def review(self, task_id, decision, **kwargs):
        """回答のみReviewの指定を記録し、Task確定は本テストでは行わない。"""
        self.called.append(("review", task_id, decision, kwargs))
        return {"status": "review_evaluated"}


class TaskCommandsTests(unittest.IsolatedAsyncioTestCase):
    """実Taskコマンドの受付状況と保存済みTask確認の責務分離を検査する。"""

    async def test_start_stop_and_completion_are_not_same_status(self):
        """202受付・停止要求・Runner終了を区別し、Taskを偽完了にしない。"""
        app = FakeApplication()
        queries = FakeQueries()
        commands = TaskCommands(
            app, queries,
            lambda: [{"name": "MELCHIOR", "enabled": True, "timeout_seconds": 7}],
        )
        accepted = await commands.start("  調査依頼  ")
        uid = accepted["task_id"]
        self.assertEqual(accepted["status"], "accepted")
        self.assertFalse(accepted["stop_requested"])
        await asyncio.wait_for(app.entered.wait(), timeout=1)
        self.assertEqual(commands.operation(__import__("uuid").UUID(uid))["status"], "running")
        stop = commands.stop(__import__("uuid").UUID(uid))
        self.assertTrue(stop["stop_requested"])
        self.assertTrue(app.called[0][2]["stop_requested"]())
        self.assertEqual(app.called[0][1], "調査依頼")
        self.assertEqual(app.called[0][2]["timeout"], 7)
        app.release.set()
        for _ in range(10):
            await asyncio.sleep(0)
            if commands.operation(__import__("uuid").UUID(uid))["status"] == "execution_finished":
                break
        self.assertEqual(commands.operation(__import__("uuid").UUID(uid))["status"], "execution_finished")
        with self.assertRaisesRegex(TaskCommandConflict, "no_running_operation"):
            commands.stop(__import__("uuid").UUID(uid))

    async def test_answer_review_guard_and_missing_id(self):
        """回答・Reviewを保存済みTaskの状態で絞り、不明IDと未準備記憶反映を拒否する。"""
        queries = FakeQueries()
        app = FakeApplication()
        commands = TaskCommands(app, queries, lambda: [])
        with self.assertRaises(TaskCommandNotFound):
            await commands.answer(uuid4(), "追加")
        with self.assertRaisesRegex(ValueError, "empty_reply"):
            await commands.answer(queries.uid, "  ")

        accepted = await commands.answer(queries.uid, "本人の補足")
        self.assertEqual(accepted["kind"], "answer")
        for _ in range(10):
            await asyncio.sleep(0)
            if app.called:
                break
        self.assertEqual(app.called[0][0:3], ("answer", queries.uid, "本人の補足"))
        self.assertEqual(app.called[0][3]["timeout"], 9)

        queries.task["magi_session"]["status"] = "proposal_ready"
        with self.assertRaisesRegex(ValueError, "review_decision_not_enabled"):
            await commands.review(queries.uid, "remember")
        result = await commands.review(queries.uid, "answer_only")
        self.assertEqual(result["kind"], "review")
        for _ in range(10):
            await asyncio.sleep(0)
            if len(app.called) >= 2:
                break
        self.assertEqual(app.called[1][0:3], ("review", queries.uid, "answer_only"))
        with self.assertRaises(TaskCommandNotFound):
            commands.operation(uuid4())

    async def test_same_task_inflight_conflict(self):
        """同じTaskを二重に処理する受付を拒否するが、DB claimの代用にはしない。"""
        app = FakeApplication()
        queries = FakeQueries()
        commands = TaskCommands(app, queries, lambda: [])
        first = await commands.answer(queries.uid, "追加")
        self.assertEqual(first["status"], "accepted")
        with self.assertRaisesRegex(TaskCommandConflict, "already_running"):
            await commands.answer(queries.uid, "別の追加")
        await asyncio.sleep(0)
        self.assertEqual(commands.operation(queries.uid)["status"], "execution_finished")


if __name__ == "__main__":
    unittest.main()
