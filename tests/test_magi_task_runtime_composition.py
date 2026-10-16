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
        self.assertEqual(repo.calls[:2], ["create", "persist"])
        self.assertEqual(seen[0][1]["profile_id"], "fixture-policy")
        self.assertEqual(seen[0][2], str(uid))
        self.assertEqual(seen[0][3], "llm")
        self.assertEqual(repo.tasks[uid]["magi_session"]["decision_strategy"], "llm")
        self.assertEqual(policies, ["loaded"])

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

    async def _wait_for(self, commands, task_id: UUID) -> None:
        """Operationが終了するまで待つ。TaskのDB確定とは別に判定する。"""
        for _ in range(100):
            if commands.operation(task_id)["status"] not in {"accepted", "running"}:
                break
            await asyncio.sleep(0.01)
        self.assertEqual(commands.operation(task_id)["status"], "execution_finished")


if __name__ == "__main__":
    unittest.main()
