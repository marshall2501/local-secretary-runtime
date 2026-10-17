# Provenance-Origin: original
# Provenance-AI: AI-generated
"""Vue日常UIの実ブラウザ隔離テスト専用。日常DB/MAGI/Secretへ接続しない。

8097のread-only UIと8099のTask-command UIを同じfixtureで動かし、
HTML/JS実ロードからTask受付・回答・Review・停止・F5まで検証する。
本番entrypointからimportしない。実Taskデータとの混用を防ぐ。
"""
from __future__ import annotations

import asyncio
from copy import deepcopy
from uuid import UUID, uuid5, NAMESPACE_DNS

from interfaces.vue_daily.app import create_app


class BrowserFixtureStore:
    """実DBの代わりに固定の匿名Taskをメモリに置く照会fixture。"""

    def __init__(self):
        """追加読み込みの検証用に12件の架空Taskを用意する。"""
        self.items: dict[str, dict] = {}
        for index in range(12):
            uid = str(uuid5(NAMESPACE_DNS, f"vue-browser-task-{index}"))
            self.items[uid] = {
                "id": uid, "request": f"架空の過去Task {index + 1}",
                "status": "completed", "revision": 1, "message": "保存済みの回答",
                "magi_session": {"status": "candidate_ready", "turns": [],
                                  "member_specs": []},
                "goal_definition": {
                    "resolution": "confirmed", "version": 2,
                    "interpreted_goal": "架空Goal",
                    "criteria": ["根拠が存在すること"],
                },
            }

    def _sorted(self, kind: str, limit: int, offset: int) -> list[dict]:
        """指定ビューに対応するタスク概要を新しい順で返す。"""
        records = list(self.items.values())[::-1]
        if kind == "open":
            records = [item for item in records if item["status"]
                       in {"pending", "running", "waiting_external", "paused"}]
        elif kind == "completed":
            records = [item for item in records if item["status"] == "completed"]
        return [deepcopy(item) for item in records[offset:offset + limit]]

    def recent(self, limit: int = 10, offset: int = 0) -> list[dict]:
        """最近のTaskを列挙する。"""
        return self._sorted("recent", limit, offset)

    def open(self, limit: int = 10, offset: int = 0) -> list[dict]:
        """処理途中Taskだけを列挙する。"""
        return self._sorted("open", limit, offset)

    def completed(self, limit: int = 10, offset: int = 0) -> list[dict]:
        """完了Taskだけを列挙する。"""
        return self._sorted("completed", limit, offset)

    def trace(self, task_id: UUID) -> dict:
        """Action・MAGI Turn・監査イベントを含むTask詳細を返す。"""
        task = self.items.get(str(task_id))
        if task is None:
            raise ValueError("not found")
        return {"task": deepcopy(task), "actions": [{
            "action_id": "synthetic-a1", "tool": "fake", "operation": "read",
            "action_status": "completed", "result_id": "synthetic-r1",
            "summary": "架空の検証済み結果", "verified_by": "fixture",
            "source_uri": "https://example.org/reference",
        }], "advisor_events": [{
            "event_type": "synthetic_verified", "occurred_at": "2026-10-17T00:00:00",
        }]}


class BrowserFixtureCommands:
    """fake RITSUKO受付。実MAGI/DB呼出しをしない。"""

    def __init__(self, store: BrowserFixtureStore):
        """両FastAPIで共有する架空Task保存領域を受け取る。"""
        self.store = store

    async def start(self, request: str, *, decision_strategy: str,
                    task_id: UUID | None = None) -> dict:
        """新規依頼をfake Taskとして保存し、本人回答待ちにする。"""
        uid = str(task_id or uuid5(NAMESPACE_DNS, request))
        if uid not in self.store.items:
            is_stop = "停止試験" in request
            is_ready_candidate = "回答候補試験" in request
            is_memory_candidate = "記憶反映試験" in request
            self.store.items[uid] = {
                "id": uid, "request": request,
                "status": "pending" if is_stop else "waiting_external",
                "phase": ("observe" if is_stop else "awaiting_review"
                          if is_ready_candidate or is_memory_candidate
                          else "awaiting_clarification"),
                "revision": 1, "question": (
                    None if is_ready_candidate or is_memory_candidate else "好きな言語を教えてください。"
                ),
                "magi_session": {
                    "decision_strategy": decision_strategy,
                    "status": (
                        "running" if is_stop else
                        "proposal_ready" if is_memory_candidate else
                        "candidate_ready" if is_ready_candidate else "waiting_user"
                    ),
                    "next_step": (
                        "review_proposal" if is_memory_candidate else
                        "review_answer_candidate" if is_ready_candidate else "ask_user"
                    ),
                    "detail": (
                        {"state": "KNOWLEDGE_CANDIDATE",
                         "answer_candidate": "本人が確認したOSはFedoraです。",
                         "knowledge_candidate": "本人が確認したOSはFedoraです。"}
                        if is_memory_candidate else
                        {"state": "READY",
                         "answer_candidate": "辞書はキーと値の組です。"}
                        if is_ready_candidate else None
                    ),
                    "tool_read_executed": is_memory_candidate,
                    "observations": [
                        {"source": "pkb", "verified": True, "text": "確認が必要"},
                        {"source": "user_clarification", "verified": False,
                         "text": "Fedora", "responds_to": ["REQ-1"]},
                    ] if is_memory_candidate else [],
                    "pending_requests": [],
                    "member_specs": [
                        {"name": "MELCHIOR", "enabled": True, "provider": "ollama", "model": "fixture"},
                        {"name": "CASPER", "enabled": True, "provider": "openai", "model": "fixture"},
                        {"name": "BALTHASAR", "enabled": True, "provider": "google", "model": "fixture"},
                    ],
                    "turns": [{"stage": "classify", "status": "ok",
                               "member_results": [
                                   {"name": name, "status": "ok", "elapsed_seconds": 0.1}
                                   for name in ("MELCHIOR", "CASPER", "BALTHASAR")
                               ]}],
                },
                "goal_definition": {
                    "resolution": "proposed", "version": 1,
                    "interpreted_goal": "本人に説明する",
                    "criteria": ["簡潔に回答する"],
                },
            }
        return {"task_id": uid, "kind": "start", "status": "accepted"}

    async def answer(self, task_id: UUID, reply: str) -> dict:
        """同じTaskに本人回答を紐づけ、確認待ち候補へ進める。"""
        task = self.store.items[str(task_id)]
        task["status"] = "waiting_external"
        task["revision"] += 1
        task["phase"] = "awaiting_review"
        # This synthetic answer is an ordinary READY candidate, not an
        # invented user-grounded PKB knowledge proposal.
        task["magi_session"]["status"] = "candidate_ready"
        task["magi_session"]["detail"] = {
            "state": "READY", "answer_candidate": f"回答候補：{reply}",
        }
        task["magi_session"]["pending_requests"] = []
        task["message"] = f"回答候補：{reply}"
        task["question"] = None
        return {"task_id": str(task_id), "kind": "answer", "status": "accepted"}

    async def review(self, task_id: UUID, decision: str) -> dict:
        """回答だけのReviewを模擬し、Taskを完了にする。"""
        task = self.store.items[str(task_id)]
        task["status"] = "completed"
        task["phase"] = "completed"
        task["revision"] += 1
        task["magi_session"]["status"] = "candidate_ready"
        task["message"] = (
            "架空Taskの記憶反映を確定しました。"
            if decision == "remember" else "架空Taskの回答を確定しました。"
        )
        return {"task_id": str(task_id), "kind": "review", "status": "accepted"}

    def stop(self, task_id: UUID) -> dict:
        """pending Taskに停止要求を記録して一時停止する。"""
        task = self.store.items[str(task_id)]
        task["status"] = "paused"
        task["phase"] = "paused"
        task["stop_requested"] = True
        task["revision"] += 1
        return {"task_id": str(task_id), "status": "paused", "stop_requested": True}

    def operation(self, task_id: UUID) -> dict:
        """受付Jobの代わりに架空Taskの保存状態を返す。"""
        if str(task_id) not in self.store.items:
            raise KeyError("missing")
        task_status = self.store.items[str(task_id)]["status"]
        operation = {
            "task_id": str(task_id), "kind": None,
            "status": "running" if task_status == "pending" else "operation_not_in_process",
            "source": "fixture", "task_status": task_status,
        }
        if task_status == "pending":
            # Explicit synthetic progress; the fixture has no real model worker.
            operation["live_turn"] = 1
            operation["member_progress"] = {
                "MELCHIOR": {
                    "member": "MELCHIOR", "provider": "ollama",
                    "model": "fixture", "state": "running", "turn": 1,
                },
            }
        return operation


async def serve() -> None:
    """既存の本番サーバーと独立したCIコンテナで2つのfake UIを起動する。"""
    import uvicorn

    store = BrowserFixtureStore()
    readonly = create_app(queries=store)
    writable = create_app(
        queries=store, commands=BrowserFixtureCommands(store),
        recover_pending_at_startup=False,
    )
    servers = [
        uvicorn.Server(uvicorn.Config(readonly, host="127.0.0.1", port=8097, log_level="warning")),
        uvicorn.Server(uvicorn.Config(writable, host="127.0.0.1", port=8099, log_level="warning")),
    ]
    await asyncio.gather(*(server.serve() for server in servers))


if __name__ == "__main__":
    asyncio.run(serve())
