# Provenance-Origin: original
# Provenance-AI: AI-generated
"""UIのない別FastAPI Hostでも同一RITSUKO Task APIを組み込める隔離試験。"""
from __future__ import annotations

import unittest
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient

from interfaces.api.ritsuko_tasks import create_ritsuko_task_router


class Queries:
    """Task Query Portの保存済みデータをfakeで返す。"""

    def __init__(self):
        """既存Task IDを固定した参照fixtureを作る。"""
        self.uid = uuid4()

    def recent(self, limit, offset):
        """画面固有の処理を使わず保存済みTask一覧を返す。"""
        return [{"id": str(self.uid), "status": "waiting_external",
                 "revision": 1, "request": "共通API確認"}]

    def open(self, limit, offset):
        """未完了のTask一覧を返す。"""
        return self.recent(limit, offset)

    def completed(self, limit, offset):
        """完了Taskを捏造しない。"""
        return []

    def trace(self, task_id):
        """永続Taskの確定状態とAction履歴を返す。"""
        if task_id != self.uid:
            raise ValueError("task_not_found")
        return {
            "task": {"id": str(task_id), "status": "waiting_external",
                     "phase": "awaiting_clarification",
                     "magi_session": {"status": "waiting_user"}},
            "actions": [], "advisor_events": [],
        }


class Commands:
    """共通Task Command契約の呼出しだけを収集する。"""

    def __init__(self):
        """実Taskや外部LLMを起動せずに操作履歴を記録する。"""
        self.calls = []

    async def start(self, request, *, decision_strategy, task_id=None):
        """Task受付の引数を保持し、APIの202に必要な値を返す。"""
        self.calls.append(("start", request, decision_strategy, task_id))
        return {"kind": "start", "status": "accepted"}

    async def answer(self, task_id, reply):
        """同一Taskへの本人回答を記録する。"""
        self.calls.append(("answer", task_id, reply))
        return {"kind": "answer", "status": "accepted"}

    async def review(self, task_id, decision):
        """本人選択のReview内容をそのまま残す。"""
        self.calls.append(("review", task_id, decision))
        return {"kind": "review", "status": "accepted"}

    def operation(self, task_id):
        """未確定Jobの状態をTask完成と混同せずに返す。"""
        self.calls.append(("operation", task_id))
        return {"status": "operation_not_in_process", "task_id": str(task_id)}

    async def resume(self, task_id):
        """保存済みTask IDの明示再開を共通Routerが変えず渡すことを確認する。"""
        self.calls.append(("resume", task_id))
        return {"kind": "start", "status": "accepted", "task_id": str(task_id)}

    def stop(self, task_id):
        """停止要求を受付けた事実だけを返す。"""
        self.calls.append(("stop", task_id))
        return {"status": "operation_not_in_process", "stop_requested": True}


class SharedRitsukoHttpRouterTests(unittest.TestCase):
    """Vueと無関係のHostから同じTask Route群を利用できることを検査する。"""

    def test_router_is_reusable_without_vue_and_read_only_by_default(self):
        """UIをmountせずにTask参照し、Commands未注入ならwriteを拒否する。"""
        queries = Queries()
        host = FastAPI()
        host.include_router(create_ritsuko_task_router(queries=queries))
        client = TestClient(host)
        uri = f"/api/v1/ritsuko/tasks/{queries.uid}"
        self.assertEqual(client.get("/api/v1/ritsuko/tasks").status_code, 200)
        self.assertEqual(client.get(uri).json()["trace"]["task"]["phase"],
                         "awaiting_clarification")
        self.assertEqual(client.get(
            f"/api/v1/ritsuko/tasks/{uuid4()}"
        ).status_code, 404)
        self.assertEqual(client.post(
            "/api/v1/ritsuko/tasks",
            json={"request": "一般説明", "decision_strategy": "llm"},
        ).status_code, 503)
        self.assertEqual(client.get(f"/api/v1/ritsuko/operations/{queries.uid}").status_code, 503)
        self.assertEqual(client.post(uri + "/resume").status_code, 503)

    def test_router_passes_identical_command_contract_when_port_injected(self):
        """UI以外のHostからもStrategyやTask IDを変えず受付ける。"""
        queries, commands = Queries(), Commands()
        host = FastAPI()
        host.include_router(create_ritsuko_task_router(
            queries=queries, commands=commands,
        ))
        client = TestClient(host)
        url = f"/api/v1/ritsuko/tasks/{queries.uid}"
        self.assertEqual(client.post(
            "/api/v1/ritsuko/tasks",
            json={"request": "調べて", "decision_strategy": "llm",
                  "task_id": str(queries.uid)},
        ).status_code, 202)
        self.assertEqual(client.post(url + "/answer", json={"reply": "本人回答"}).status_code, 202)
        self.assertEqual(client.post(url + "/review", json={"decision": "answer_only"}).status_code, 202)
        self.assertEqual(client.post(url + "/resume").status_code, 202)
        self.assertEqual(client.post(url + "/stop").status_code, 200)
        self.assertEqual(client.get(
            f"/api/v1/ritsuko/operations/{queries.uid}"
        ).status_code, 200)
        self.assertEqual(commands.calls, [
            ("start", "調べて", "llm", queries.uid),
            ("answer", queries.uid, "本人回答"),
            ("review", queries.uid, "answer_only"),
            ("resume", queries.uid),
            ("stop", queries.uid),
            ("operation", queries.uid),
        ])
