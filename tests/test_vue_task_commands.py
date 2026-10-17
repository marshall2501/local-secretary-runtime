# Provenance-Origin: original
# Provenance-AI: AI-generated
"""GUI非依存のTask受付・停止要求・再開競合をDBなしで検証する。

実Task Repositoryや外部MAGIを使わないため、このテストの完了は
実DB/外部Sourceを接続した日常運用の受入ではない。
"""
from __future__ import annotations

import asyncio
import unittest
from uuid import UUID, uuid4

from bootstrap.ritsuko_task_composition import build_task_application

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
        self.block_answer = False

    async def start(self, request, **kwargs):
        """指定Task ID、MAGI設定、協調停止関数を受け取って完了を待つ。"""
        self.called.append(("start", request, kwargs))
        self.entered.set()
        await self.release.wait()
        return {"status": "waiting_user"}

    async def answer(self, task_id, text, **kwargs):
        """本人回答を同一Task IDで処理し、入力とTimeoutを記録する。"""
        self.called.append(("answer", task_id, text, kwargs))
        if self.block_answer:
            self.entered.set()
            await self.release.wait()
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
        self.assertEqual(commands.operation(UUID(uid))["status"], "running")
        stop = commands.stop(UUID(uid))
        self.assertTrue(stop["stop_requested"])
        self.assertTrue(app.called[0][2]["stop_requested"]())
        self.assertEqual(app.called[0][1], "調査依頼")
        self.assertEqual(app.called[0][2]["timeout"], 7)
        app.release.set()
        for _ in range(10):
            await asyncio.sleep(0)
            if commands.operation(UUID(uid))["status"] == "execution_finished":
                break
        self.assertEqual(commands.operation(UUID(uid))["status"], "execution_finished")
        with self.assertRaisesRegex(TaskCommandConflict, "no_running_operation"):
            commands.stop(UUID(uid))

    async def test_live_seat_events_are_bounded_and_not_task_completion(self):
        """処理中の席別進捗は一時Operationだけに保持し、Task完了と混同しない。"""
        app = FakeApplication()
        commands = TaskCommands(app, FakeQueries(), lambda: [
            {"name": "MELCHIOR", "enabled": True, "timeout_seconds": 5},
        ])
        accepted = await commands.start("席別進捗の表示")
        task_id = UUID(accepted["task_id"])
        await asyncio.wait_for(app.entered.wait(), timeout=1)
        progress = app.called[-1][2]["on_member_progress"]
        progress({
            "task_id": str(task_id), "member": "MELCHIOR",
            "provider": "ollama", "model": "fake", "turn": 1,
            "state": "running", "secret": "must-not-be-exposed",
        })
        live = commands.operation(task_id)
        self.assertEqual(live["status"], "running")
        self.assertEqual(live["member_progress"]["MELCHIOR"]["state"], "running")
        self.assertNotIn("secret", str(live))
        # A different Task cannot poison another Task's visual progress.
        progress({
            "task_id": str(uuid4()), "member": "CASPER",
            "state": "completed", "turn": 1,
        })
        self.assertEqual(len(commands.operation(task_id)["member_progress"]), 1)
        progress({
            "task_id": str(task_id), "member": "CASPER",
            "state": "queued", "turn": 2,
        })
        self.assertEqual(set(commands.operation(task_id)["member_progress"]), {"CASPER"})
        app.release.set()
        for _ in range(20):
            await asyncio.sleep(0)
            if commands.operation(task_id)["status"] == "execution_finished":
                break
        self.assertEqual(commands.operation(task_id)["status"], "execution_finished")

    async def test_remember_review_reuses_checked_memory_receipt(self):
        """明示的な記憶反映だけMemory Intakeを経由し、共通TaskへReceiptを渡す。"""
        queries = FakeQueries()
        queries.task["phase"] = "awaiting_review"
        queries.task["magi_session"] = {
            "status": "proposal_ready", "tool_read_executed": True,
            "member_specs": [
                {"name": "MELCHIOR", "enabled": True, "timeout_seconds": 5},
            ],
            "detail": {
                "state": "KNOWLEDGE_CANDIDATE",
                "answer_candidate": "使用OSはFedoraです。",
                "knowledge_candidate": "使用OSはFedora",
            },
            "observations": [
                {"source": "pkb", "verified": True},
                {"source": "user_clarification", "text": "Fedora",
                 "responds_to": ["REQ-1"]},
            ],
        }
        receipt = {"status": "committed", "input_id": "input-1", "receipts": []}
        calls = []
        def writer(task_id):
            calls.append(task_id)
            return receipt

        app = FakeApplication()
        commands = TaskCommands(
            app, queries, lambda: [], memory_review_writer=writer,
        )
        accepted = await commands.review(queries.uid, "remember")
        self.assertEqual(accepted["status"], "accepted")
        for _ in range(30):
            await asyncio.sleep(0)
            if commands.operation(queries.uid)["status"] == "execution_finished":
                break
        self.assertEqual(commands.operation(queries.uid)["status"], "execution_finished")
        self.assertEqual(calls, [queries.uid])
        self.assertEqual(app.called[0][0:3], ("review", queries.uid, "remember"))
        self.assertEqual(app.called[0][3]["memory_result"], receipt)

        # A different Task with no configured memory writer cannot write.
        no_writer = TaskCommands(FakeApplication(), queries, lambda: [])
        with self.assertRaisesRegex(ValueError, "review_decision_not_enabled"):
            await no_writer.review(queries.uid, "remember")

    async def test_remember_never_finishes_without_committed_receipt(self):
        """Memory Intake失敗や未確認提案はMAGI再評価・Task確定へ進まない。"""
        queries = FakeQueries()
        queries.task["phase"] = "awaiting_review"
        session = {
            "status": "proposal_ready", "tool_read_executed": True,
            "member_specs": [],
            "detail": {
                "state": "KNOWLEDGE_CANDIDATE",
                "knowledge_candidate": "Fedoraの利用",
                "answer_candidate": "Fedoraの利用",
            },
            "observations": [
                {"source": "pkb", "verified": True},
                {"source": "user_clarification", "text": "Fedora",
                 "responds_to": ["REQ-1"]},
            ],
        }
        queries.task["magi_session"] = session
        app = FakeApplication()
        commands = TaskCommands(
            app, queries, lambda: [],
            memory_review_writer=lambda _uid: {"status": "pending"},
        )
        await commands.review(queries.uid, "remember")
        for _ in range(30):
            await asyncio.sleep(0.01)
            if commands.operation(queries.uid)["status"] == "execution_failed":
                break
        self.assertEqual(commands.operation(queries.uid)["status"], "execution_failed")
        self.assertEqual(app.called, [])

        # MAGI-only knowledge candidate lacking a verified user assertion
        # must not make the Memory Intake port run at all.
        session["observations"] = [{"source": "pkb", "verified": True}]
        with self.assertRaisesRegex(TaskCommandConflict, "task_not_waiting_for_review"):
            await commands.review(queries.uid, "remember")

    async def test_both_judgment_methods_share_task_runner_with_multiple_magi_seats(self):
        """判断方式とMAGI席数は独立し、共通Task Applicationへ同じ形で渡る。"""
        for count in (1, 2, 3):
            app = FakeApplication()
            specs = [
                {"name": name, "enabled": True, "timeout_seconds": 9}
                for name in ("MELCHIOR", "CASPER", "BALTHASAR")[:count]
            ]
            chosen = []
            def starter(strategy):
                chosen.append(strategy)
                return lambda *_args, **_kwargs: None
            commands = TaskCommands(
                app, FakeQueries(), lambda: specs,
                dialogue_starter_factory=starter,
            )
            for strategy in ("standard", "llm"):
                app.entered.clear()
                app.release.clear()
                accepted = await commands.start(
                    f"各席{count}方式{strategy}", decision_strategy=strategy,
                )
                await asyncio.wait_for(app.entered.wait(), timeout=1)
                called = app.called[-1]
                self.assertEqual(called[0], "start")
                self.assertEqual(called[2]["member_specs"], specs)
                self.assertTrue(callable(called[2]["dialogue_starter"]))
                self.assertEqual(chosen[-1], strategy)
                app.release.set()
                for _ in range(30):
                    await asyncio.sleep(0)
                    if commands.operation(UUID(accepted["task_id"]))["status"] == "execution_finished":
                        break
                self.assertEqual(
                    commands.operation(UUID(accepted["task_id"]))["status"],
                    "execution_finished",
                )
            self.assertEqual(len(app.called), 2)
            self.assertTrue(all(spec["enabled"] for spec in specs))

    async def test_judgment_strategy_does_not_change_member_assignments(self):
        """LLM判断に1席制限を置かず、現在のMAGI席設定を一切書き換えない。"""
        app = FakeApplication()
        specs = [
            {"name": "MELCHIOR", "enabled": True, "timeout_seconds": 9},
            {"name": "CASPER", "enabled": True, "timeout_seconds": 10},
        ]
        commands = TaskCommands(app, FakeQueries(), lambda: specs)
        with self.assertRaisesRegex(ValueError, "unknown_flow_decision_strategy"):
            await commands.start("依頼", decision_strategy="new_mode")
        # 実LLMの配線がないfixtureは席数によらず拒否する。
        with self.assertRaisesRegex(TaskCommandConflict, "llm_strategy_not_connected"):
            await commands.start("依頼", decision_strategy="llm")
        self.assertEqual([item["enabled"] for item in specs], [True, True])
        self.assertEqual(commands._operations, {})
        specs[1]["enabled"] = False
        with self.assertRaisesRegex(TaskCommandConflict, "llm_strategy_not_connected"):
            await commands.start("依頼", decision_strategy="llm")
        self.assertEqual(commands._operations, {})

        # standard判断は複数席でも1席へ書き換えず、既存Applicationへ渡す。
        specs[1]["enabled"] = True
        accepted = await commands.start("依頼", decision_strategy="standard")
        await asyncio.wait_for(app.entered.wait(), timeout=1)
        self.assertEqual(accepted["kind"], "start")
        self.assertEqual(app.called[0][2]["member_specs"], specs)
        self.assertEqual([item["enabled"] for item in specs], [True, True])
        app.release.set()
        for _ in range(10):
            await asyncio.sleep(0)
            if commands.operation(UUID(accepted["task_id"]))["status"] == "execution_finished":
                break
        self.assertEqual(commands.operation(UUID(accepted["task_id"]))["status"],
                         "execution_finished")

    async def test_operation_after_restart_reports_db_state_without_fake_worker(self):
        """APIインスタンス再生成後、既存TaskとWorker稼働情報を混同しない。"""
        queries = FakeQueries()
        commands = TaskCommands(FakeApplication(), queries, lambda: [])
        for saved_status in ("waiting_external", "running", "completed", "paused", "failed"):
            with self.subTest(saved_status=saved_status):
                queries.task["status"] = saved_status
                result = commands.operation(queries.uid)
                self.assertEqual(result["task_id"], str(queries.uid))
                self.assertEqual(result["status"], "operation_not_in_process")
                self.assertEqual(result["source"], "persisted_task")
                self.assertEqual(result["task_status"], saved_status)
                self.assertIsNone(result["stop_requested"])
                self.assertEqual(
                    result["execution_reconciliation_required"],
                    saved_status == "running",
                )
                self.assertNotIn("magi_session", result)
        with self.assertRaises(TaskCommandNotFound):
            commands.operation(uuid4())
        with self.assertRaisesRegex(TaskCommandConflict, "no_running_operation"):
            commands.stop(queries.uid)

    async def test_stop_persisted_task_after_restart(self):
        """DBに残るTaskへ停止要求を記録し、Worker停止完了とは表示しない。"""
        queries = FakeQueries()
        calls = []

        def request_stop(*, task_id):
            calls.append(task_id)
            return "paused"

        commands = TaskCommands(
            FakeApplication(), queries, lambda: [],
            request_stop=request_stop,
        )
        result = commands.stop(queries.uid)
        self.assertEqual(calls, [queries.uid])
        self.assertEqual(result["status"], "operation_not_in_process")
        self.assertEqual(result["source"], "persisted_task")
        self.assertEqual(result["task_status"], "paused")
        self.assertTrue(result["stop_requested"])
        self.assertFalse(result["execution_reconciliation_required"])

    async def test_stop_persisted_running_requires_reconciliation(self):
        """DBのrunningを停止完了や生存Workerの証拠として扱わない。"""
        queries = FakeQueries()
        commands = TaskCommands(
            FakeApplication(), queries, lambda: [],
            request_stop=lambda *, task_id: "running",
        )
        result = commands.stop(queries.uid)
        self.assertEqual(result["task_id"], str(queries.uid))
        self.assertEqual(result["source"], "persisted_task")
        self.assertEqual(result["task_status"], "running")
        self.assertTrue(result["stop_requested"])
        self.assertTrue(result["execution_reconciliation_required"])
        self.assertEqual(result["status"], "operation_not_in_process")

    async def test_stop_persisted_failure_contracts(self):
        """DB不在・状態拒否・不正戻り値は停止要求成功と表示しない。"""
        queries = FakeQueries()

        def missing(*, task_id):
            """Repositoryの対象Task不在を再現する。"""
            raise ValueError("task_not_found")

        def rejected(*, task_id):
            """確定済みTaskの停止拒否を再現する。"""
            raise ValueError("task_not_stoppable")

        cases = (
            (missing, TaskCommandNotFound, "task_not_found"),
            (rejected, TaskCommandConflict, "task_not_stoppable"),
            (lambda *, task_id: None, TaskCommandConflict, "invalid_persisted_stop_status"),
            (lambda *, task_id: "completed", TaskCommandConflict, "invalid_persisted_stop_status"),
        )
        for stop_impl, expected, message in cases:
            with self.subTest(expected=message):
                commands = TaskCommands(
                    FakeApplication(), queries, lambda: [], request_stop=stop_impl,
                )
                with self.assertRaisesRegex(expected, message):
                    commands.stop(queries.uid)
                self.assertEqual(commands._operations, {})

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

    async def test_saved_standard_ready_answer_can_be_reviewed(self):
        """従来方式の候補が確認待ちで止まり、APIから完了できない回帰を防ぐ。"""
        queries = FakeQueries()
        queries.task["magi_session"] = {
            "status": "candidate_ready",
            "detail": {"state": "READY", "answer_candidate": "辞書の説明"},
            "pending_requests": [],
            "member_specs": [{"name": "MELCHIOR", "enabled": True, "timeout_seconds": 9}],
        }
        app = FakeApplication()
        commands = TaskCommands(app, queries, lambda: [])
        accepted = await commands.review(queries.uid, "answer_only")
        self.assertEqual(accepted["kind"], "review")
        for _ in range(20):
            await asyncio.sleep(0)
            if app.called:
                break
        self.assertEqual(app.called[-1][0:3], ("review", queries.uid, "answer_only"))
        queries.task["magi_session"]["pending_requests"] = [{"source": "web"}]
        with self.assertRaisesRegex(TaskCommandConflict, "task_not_waiting_for_review"):
            await commands.review(queries.uid, "answer_only")

    async def test_same_task_inflight_conflict(self):
        """同じTaskを二重に処理する受付を拒否するが、DB claimの代用にはしない。"""
        app = FakeApplication()
        app.block_answer = True
        queries = FakeQueries()
        commands = TaskCommands(app, queries, lambda: [])
        first = await commands.answer(queries.uid, "追加")
        self.assertEqual(first["status"], "accepted")
        # 1回目のRunnerが確実に処理中になるまで待ってから競合を試す。
        await asyncio.wait_for(app.entered.wait(), timeout=1)
        with self.assertRaisesRegex(TaskCommandConflict, "already_running"):
            await commands.answer(queries.uid, "別の追加")
        app.release.set()
        for _ in range(10):
            await asyncio.sleep(0)
            if commands.operation(queries.uid)["status"] == "execution_finished":
                break
        self.assertEqual(commands.operation(queries.uid)["status"], "execution_finished")


    async def test_coordinator_uses_shared_ritsuko_application(self):
        """共通ApplicationからRunnerへTask IDと停止要求が正しく渡ることを確認する。"""
        calls = []
        entered = asyncio.Event()
        release = asyncio.Event()
        no_op = lambda *args, **kwargs: None

        async def start_runner(request, **kwargs):
            """受付された同一Task IDと停止関数を観測し、外部処理は実行しない。"""
            calls.append((request, kwargs))
            entered.set()
            await release.wait()
            return {"status": "waiting_user"}

        app = build_task_application(
            create_task_record=no_op,
            execute_source_request=no_op,
            record_source_read_record=no_op,
            persist_session_record=no_op,
            fail_task_record=no_op,
            claim_user_resume_record=no_op,
            abort_user_resume_record=no_op,
            claim_proposal_review_record=no_op,
            finalize_proposal_review_record=no_op,
            abort_proposal_review_record=no_op,
            start_runner=start_runner,
        )
        commands = TaskCommands(
            app, FakeQueries(),
            lambda: [{"name": "MELCHIOR", "enabled": True, "timeout_seconds": 6}],
        )
        accepted = await commands.start("共有経路のテスト")
        await asyncio.wait_for(entered.wait(), timeout=1)
        self.assertEqual(str(calls[0][1]["task_id"]), accepted["task_id"])
        commands.stop(UUID(accepted["task_id"]))
        self.assertTrue(calls[0][1]["stop_requested"]())
        release.set()
        for _ in range(10):
            await asyncio.sleep(0)
            if commands.operation(UUID(accepted["task_id"]))["status"] == "execution_finished":
                break
        self.assertEqual(commands.operation(UUID(accepted["task_id"]))["status"],
                         "execution_finished")


if __name__ == "__main__":
    unittest.main()
