# Provenance-Origin: original
# Provenance-AI: AI-generated
"""共有MAGI TaskのRepository・Policy・Source結合を、DBとLLMを使わず検証する。

画面のテストではなく、NiceGUIから分離したComposition RootのPort実体と
権限Snapshot・Task ID・本人回答・Reviewの契約が維持されるかを見る。
"""
from __future__ import annotations

import asyncio
from copy import deepcopy
import unittest
from uuid import UUID

from bootstrap.magi_task_runtime import MagiTaskBindings, build_magi_task_commands


class FakeMagiRepository:
    """実PostgreSQLの代わりに、呼出し元が渡したTask IDと対話履歴を保持する。"""

    def __init__(self) -> None:
        """Task作成・保存・本人回答claim・Review呼出しを追跡可能にする。"""
        self.tasks: dict[UUID, dict] = {}
        self.calls: list[str] = []

    def create_task(self, *, task_id, request, member_specs) -> None:
        """受付時に同一Task IDの仮の行を作る（本物のDB commitではない）。"""
        self.calls.append("create")
        self.tasks[task_id] = {
            "id": str(task_id), "request": request, "status": "running",
            "member_specs": deepcopy(member_specs),
        }

    def reserve_start(self, *, task_id, request, member_specs,
                      decision_strategy, policy_snapshot):
        """最初にpending Taskを確定するfake transaction境界。"""
        if task_id in self.tasks:
            raise ValueError("duplicate_task")
        self.calls.append("reserve")
        self.tasks[task_id] = {
            "id": str(task_id), "request": request, "status": "pending",
            "member_specs": deepcopy(member_specs),
            "decision_strategy": decision_strategy,
            "policy_snapshot": deepcopy(policy_snapshot),
        }

    def claim_start(self, *, task_id, request, member_specs):
        """1件のpending TaskだけがWorkerを開始できる。"""
        task = self.tasks[task_id]
        if (task["status"] != "pending" or task["request"] != request
                or task["member_specs"] != member_specs):
            raise ValueError("task_start_not_pending")
        self.calls.append("claim_start")
        task["status"] = "running"

    def request_stop(self, *, task_id):
        """Task状態ごとの停止要求を保存し、未着手はpausedに確定する。"""
        task = self.tasks[task_id]
        if task["status"] not in {"pending", "running"}:
            raise ValueError("task_not_stoppable")
        self.calls.append("request_stop")
        task["stop_requested"] = True
        if task["status"] == "pending":
            task["status"] = "paused"
        return task["status"]

    def list_pending_starts(self, *, limit=20):
        """中断時にもfake DBに残る未着手Taskだけを列挙する。"""
        self.calls.append("list_pending")
        return [{
            "task_id": task_id, "request": item["request"],
            "member_specs": deepcopy(item["member_specs"]),
            "decision_strategy": item["decision_strategy"],
            "policy_snapshot": deepcopy(item["policy_snapshot"]),
        } for task_id, item in self.tasks.items()
          if item["status"] == "pending"][:limit]

    def persist_session(self, *, task_id, session, selected_capability=None):
        """対話セッションを保存し、待機状態だけQuery用に投影する。"""
        self.calls.append("persist")
        self.tasks[task_id]["magi_session"] = deepcopy(session)
        self.tasks[task_id]["status"] = (
            "waiting_external" if session["status"] in {"waiting_user", "proposal_ready"}
            else "completed"
        )
        return {"task_status": self.tasks[task_id]["status"]}

    def fail_task(self, *, task_id, error):
        """Runner例外の記録だけを模倣する。"""
        self.calls.append("fail")
        self.tasks[task_id]["status"] = "failed"

    def claim_user_resume(self, *, task_id, reply_length, reply_fingerprint):
        """保存済み対話を返し、本人回答のclaim契約を観測する。"""
        self.calls.append("claim_user")
        return deepcopy(self.tasks[task_id]["magi_session"]), None

    def abort_user_resume(self, *, task_id, error):
        """回答中断の通知を記録する。"""
        self.calls.append("abort_user")

    def claim_proposal_review(self, *, task_id, decision, memory_result=None):
        """回答のみReviewを既存Task IDの対話に結び付ける。"""
        self.calls.append("claim_review")
        return (
            deepcopy(self.tasks[task_id]["magi_session"]), None,
            {"source": "proposal_review", "verified": True, "text": decision},
            {},
        )

    def finalize_proposal_review(self, *, task_id, session, selected_capability=None):
        """再評価済み対話に対する確定Portの呼出しだけを記録する。"""
        self.calls.append("finalize_review")
        self.tasks[task_id]["magi_session"] = deepcopy(session)
        self.tasks[task_id]["status"] = "completed"
        return {"task_status": "completed"}

    def abort_proposal_review(self, *, task_id, error):
        """Review中断の通知を記録する。"""
        self.calls.append("abort_review")

    def record_source_read(self, *, task_id, execution, pending_request):
        """本テストでは実Sourceを使わないため呼出し有無だけ検出する。"""
        self.calls.append("source_read")
        return "a1", "r1"


class FakeQueries:
    """MAGI Task Repositoryの保存内容をTask詳細形式で提供する。"""

    def __init__(self, repo: FakeMagiRepository) -> None:
        """同じTask IDの保存情報を参照する。"""
        self.repo = repo

    def trace(self, task_id: UUID) -> dict:
        """保存済みTaskだけ返し、不明TaskはQuery契約に合わせて拒否する。"""
        if task_id not in self.repo.tasks:
            raise ValueError("unknown_task")
        return {"task": deepcopy(self.repo.tasks[task_id]), "actions": []}


class SharedMagiRuntimeTests(unittest.IsolatedAsyncioTestCase):
    """非GUI構成のTask開始・再開・Review・権限Snapshot保存を検証する。"""

    async def test_start_answer_review_reuse_real_application_ports(self):
        """fake DB上の同一Task IDに、起動時PolicyとMAGI判断結果を保存する。"""
        repo = FakeMagiRepository()
        policies = []
        seen = []

        def load_policy() -> dict:
            """新規Taskの入口で一度だけ読む仮の権限Snapshotを返す。"""
            policies.append("loaded")
            return {"profile_id": "fixture-policy", "exposure": "lan"}

        async def start_dialogue(user_raw, *, policy_snapshot, **kwargs):
            """開始時の権限とTask IDがMAGIへ渡ることを観測する。"""
            seen.append(("start", policy_snapshot, kwargs["task_id"], kwargs["decision_strategy"]))
            return {
                "task_id": kwargs["task_id"], "status": "waiting_user",
                "member_specs": kwargs["member_specs"], "user_raw": user_raw,
                "decision_strategy": kwargs["decision_strategy"],
            }

        async def resume_dialogue(session, reply, **kwargs):
            """保存済みTaskの本人回答後にMAGIへ同じ対話を渡す。"""
            seen.append(("answer", reply, session["task_id"]))
            return {**session, "status": "proposal_ready"}

        async def evaluate_review(session, observation, **kwargs):
            """回答のみReviewの観測をMAGIへ渡して再評価済みを模倣する。"""
            seen.append(("review", observation["source"], session["task_id"]))
            return {**session, "status": "review_evaluated"}

        bindings = MagiTaskBindings(
            magi_repository=repo,
            pkb_repository=object(), finance_repository=object(),
            queries=FakeQueries(repo),
            member_specs_loader=lambda: [
                {"name": "MELCHIOR", "enabled": True, "timeout_seconds": 6},
            ],
            policy_snapshot_loader=load_policy,
        )
        commands = build_magi_task_commands(
            bindings, dialogue_starter=start_dialogue,
            user_continuation=resume_dialogue,
            review_continuation=evaluate_review,
        )
        started = await commands.start("対象の観測", decision_strategy="llm")
        uid = UUID(started["task_id"])
        await self._wait_for(commands, uid)
        self.assertEqual(repo.tasks[uid]["status"], "waiting_external")
        self.assertEqual(repo.calls[:3], ["reserve", "claim_start", "persist"])
        self.assertEqual(seen[0][1]["profile_id"], "fixture-policy")
        self.assertEqual(seen[0][2], str(uid))
        self.assertEqual(seen[0][3], "llm")
        self.assertEqual(repo.tasks[uid]["magi_session"]["decision_strategy"], "llm")
        self.assertEqual(policies, ["loaded"])
        self.assertEqual(repo.tasks[uid]["policy_snapshot"]["profile_id"], "fixture-policy")

        answered = await commands.answer(uid, "本人の回答")
        self.assertEqual(answered["kind"], "answer")
        await self._wait_for(commands, uid)
        self.assertEqual(repo.tasks[uid]["magi_session"]["status"], "proposal_ready")
        self.assertEqual(seen[1], ("answer", "本人の回答", str(uid)))
        self.assertEqual(policies, ["loaded"])
        self.assertEqual(repo.tasks[uid]["magi_session"]["decision_strategy"], "llm")
        self.assertIn("claim_user", repo.calls)

        accepted = await commands.review(uid, "answer_only")
        self.assertEqual(accepted["status"], "accepted")
        await self._wait_for(commands, uid)
        self.assertEqual(seen[2], ("review", "proposal_review", str(uid)))
        self.assertIn("claim_review", repo.calls)
        self.assertIn("finalize_review", repo.calls)
        self.assertEqual(policies, ["loaded"])

    async def test_restart_recovers_pending_once_and_never_replays_running(self):
        """受付済み未実行Taskのみ、新Applicationで同じIDからclaimする。"""
        from uuid import uuid4

        repo = FakeMagiRepository()
        task_id = uuid4()
        member_specs = [{"name": "MELCHIOR", "enabled": True, "timeout_seconds": 5}]
        stored_policy = {"profile_id": "saved-before-restart", "revision": 1}
        repo.reserve_start(
            task_id=task_id, request="再起動前の未着手Task",
            member_specs=member_specs, decision_strategy="llm",
            policy_snapshot=stored_policy,
        )
        calls = []
        loads = []

        async def fake_start(user_raw, *, policy_snapshot, **kwargs):
            """復旧中に現行Policyへすり替わらないことを確認する。"""
            calls.append((user_raw, kwargs["task_id"], policy_snapshot))
            return {
                "task_id": kwargs["task_id"], "user_raw": user_raw,
                "member_specs": kwargs["member_specs"],
                "decision_strategy": kwargs["decision_strategy"],
                "status": "waiting_user",
            }

        def latest_policy():
            """復旧時は新しいPolicy設定を読まない。"""
            loads.append("called")
            return {"profile_id": "current-but-not-for-existing-task"}

        bindings = MagiTaskBindings(
            magi_repository=repo, pkb_repository=object(),
            finance_repository=object(), queries=FakeQueries(repo),
            member_specs_loader=lambda: member_specs,
            policy_snapshot_loader=latest_policy,
        )
        commands = build_magi_task_commands(bindings, dialogue_starter=fake_start)
        recovered = await commands.recover_pending()
        self.assertEqual(len(recovered), 1)
        self.assertEqual(recovered[0]["task_id"], str(task_id))
        self.assertEqual(repo.tasks[task_id]["status"], "pending")
        await self._wait_for(commands, task_id)
        self.assertEqual(repo.calls.count("claim_start"), 1)
        self.assertEqual(repo.tasks[task_id]["status"], "waiting_external")
        self.assertEqual(calls, [(
            "再起動前の未着手Task", str(task_id), stored_policy,
        )])
        self.assertEqual(loads, [])
        # Task has left pending; a restarted Worker does not replay it.
        second = build_magi_task_commands(bindings, dialogue_starter=fake_start)
        self.assertEqual(await second.recover_pending(), [])
        self.assertEqual(repo.calls.count("claim_start"), 1)

    async def test_pending_stop_is_persisted_and_excluded_from_restart(self):
        """Stop済み未着手Taskは再起動後にMAGIを起動しない。"""
        from uuid import uuid4

        repo = FakeMagiRepository()
        task_id = uuid4()
        repo.reserve_start(
            task_id=task_id, request="中止する依頼",
            member_specs=[{"name": "MELCHIOR", "enabled": True, "timeout_seconds": 5}],
            decision_strategy="standard", policy_snapshot=None,
        )
        self.assertEqual(repo.request_stop(task_id=task_id), "paused")
        self.assertTrue(repo.tasks[task_id]["stop_requested"])
        self.assertEqual(repo.list_pending_starts(), [])

        bindings = MagiTaskBindings(
            magi_repository=repo, pkb_repository=object(),
            finance_repository=object(), queries=FakeQueries(repo),
            member_specs_loader=lambda: [],
            policy_snapshot_loader=lambda: None,
        )
        commands = build_magi_task_commands(bindings)
        self.assertEqual(await commands.recover_pending(), [])
        self.assertEqual(repo.calls.count("claim_start"), 0)

    async def test_failed_reservation_does_not_accept_or_schedule_task(self):
        """DB保存に失敗しても202相当の受付やMAGI実行へ進まない。"""
        repo = FakeMagiRepository()
        calls = []

        def broken_reserve(**_kwargs):
            """コミット失敗を再現する。"""
            raise RuntimeError("simulated_db_write_failure")

        repo.reserve_start = broken_reserve

        async def fake_start(*_args, **_kwargs):
            """予約が失敗したTaskで呼ばれてはならない。"""
            calls.append("llm")
            return {}

        bindings = MagiTaskBindings(
            magi_repository=repo, pkb_repository=object(),
            finance_repository=object(), queries=FakeQueries(repo),
            member_specs_loader=lambda: [{
                "name": "MELCHIOR", "enabled": True, "timeout_seconds": 5,
            }],
            policy_snapshot_loader=lambda: None,
        )
        commands = build_magi_task_commands(bindings, dialogue_starter=fake_start)
        with self.assertRaisesRegex(RuntimeError, "simulated_db_write_failure"):
            await commands.start("失敗すべき依頼", decision_strategy="llm")
        self.assertEqual(repo.tasks, {})
        self.assertEqual(commands._operations, {})
        self.assertEqual(commands._jobs, {})
        self.assertEqual(calls, [])

    async def _wait_for(self, commands, task_id: UUID) -> None:
        """Operationが終了するまで待つ。TaskのDB確定とは別に判定する。"""
        for _ in range(100):
            if commands.operation(task_id)["status"] not in {"accepted", "running"}:
                break
            await asyncio.sleep(0.01)
        self.assertEqual(commands.operation(task_id)["status"], "execution_finished")


if __name__ == "__main__":
    unittest.main()
