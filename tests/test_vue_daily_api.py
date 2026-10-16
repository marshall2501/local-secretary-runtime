# Provenance-Origin: original
# Provenance-AI: AI-generated
"""Task参照APIのHTTP契約をfake Queryで検査する。

実DBへの接続や本人認証の有効性は、このテストの対象ではない。
"""
from __future__ import annotations

import unittest
from uuid import UUID, uuid4

from fastapi.testclient import TestClient

from interfaces.vue_daily.app import create_app


class FakeQueries:
    """DBを使わずTask Query Application契約だけを再現する。"""

    def __init__(self):
        """Task IDと呼び出し履歴を保持する隔離Query fixtureを用意する。"""
        self.uid = uuid4()
        self.calls = []
        self.task = {
            "id": str(self.uid), "request": "既存Task", "status": "completed",
            "revision": 1, "created_at": "2026-10-15T12:00:00+09:00",
        }

    def recent(self, limit, offset):
        """受領した表示範囲を記録し、固定の最近Taskを返す。"""
        self.calls.append(("recent", limit, offset))
        return [self.task]

    def open(self, limit, offset):
        """進行中一覧の空結果を返して表示条件の切替を検査する。"""
        self.calls.append(("open", limit, offset))
        return []

    def completed(self, limit, offset):
        """「完了」を示す固定のテストTaskを返し、一覧の絞り込みを確認する。"""
        self.calls.append(("completed", limit, offset))
        return [self.task]

    def trace(self, task_id: UUID):
        """既知IDのTask履歴だけ返し、不明IDでは404相当を検査させる。"""
        self.calls.append(("trace", task_id))
        if task_id != self.uid:
            raise ValueError("missing")
        return {
            "task": self.task, "actions": [{"result_id": "r1", "outcome": "ok"}],
            "advisor_events": [],
        }


class VueDailyApiTests(unittest.TestCase):
    """ローカルTask参照APIの応答形式・アクセス制限をfake Queryで確認する。"""

    def setUp(self):
        """固定Taskを返すfake QueryとローカルURLのHTTPテスト環境を作る。"""
        self.queries = FakeQueries()
        self.client = TestClient(
            create_app(queries=self.queries),
            base_url="http://127.0.0.1:8097",
        )

    def test_saved_task_list_and_trace_are_transport_only(self):
        """一覧の種類・範囲とTask履歴をHTTP経由で取得し、値の引渡しを検証する。"""
        response = self.client.get("/api/v1/ritsuko/tasks?limit=5&offset=2")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["items"][0]["status"], "completed")
        self.assertEqual(response.json()["source"], "persisted")
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.assertEqual(self.queries.calls[-1], ("recent", 5, 2))

        response = self.client.get("/api/v1/ritsuko/tasks?view=open")
        self.assertEqual(response.json()["items"], [])
        self.assertEqual(self.queries.calls[-1], ("open", 10, 0))
        response = self.client.get("/api/v1/ritsuko/tasks?view=completed")
        self.assertEqual(response.json()["items"][0]["revision"], 1)

        trace = self.client.get(f"/api/v1/ritsuko/tasks/{self.queries.uid}")
        self.assertEqual(trace.status_code, 200)
        self.assertEqual(trace.json()["trace"]["task"]["status"], "completed")
        self.assertEqual(trace.json()["trace"]["actions"][0]["result_id"], "r1")
        self.assertEqual(self.queries.calls[-1], ("trace", self.queries.uid))
        self.assertEqual(self.client.get(
            f"/api/v1/ritsuko/tasks/{uuid4()}"
        ).status_code, 404)

    def test_unbound_commands_remain_disabled_and_external_access_denied(self):
        """Task依存を注入しなければ操作は503で、外部Origin等も拒否する。"""
        self.assertEqual(self.client.get(
            "/api/v1/ritsuko/health"
        ).json(), {"status": "ok", "synthetic": False, "mode": "read_only"})
        self.assertEqual(self.client.post(
            "/api/v1/ritsuko/tasks", json={"request": "do something", "decision_strategy": "llm"}
        ).status_code, 503)
        self.assertEqual(self.client.post(
            "/api/v1/ritsuko/tasks/" + str(self.queries.uid) + "/answer",
            json={"reply": "test"},
        ).status_code, 503)
        self.assertEqual(self.client.get(
            "/api/v1/ritsuko/tasks", headers={"Host": "evil.invalid"},
        ).status_code, 403)
        self.assertEqual(self.client.get(
            "/api/v1/ritsuko/tasks",
            headers={"Origin": "https://evil.invalid"},
        ).status_code, 403)
        self.assertEqual(self.client.get(
            "/api/v1/ritsuko/tasks",
            headers={"Sec-Fetch-Site": "cross-site"},
        ).status_code, 403)
        self.assertEqual(self.client.get(
            "/api/v1/ritsuko/tasks",
            headers={"X-Forwarded-For": "10.0.0.1"},
        ).status_code, 403)
        self.assertEqual(self.client.get(
            "/api/v1/ritsuko/tasks?limit=51"
        ).status_code, 422)


class FakeCommands:
    """HTTP transportの呼出しだけを確認するTaskコマンド代役。DB操作は行わない。"""

    def __init__(self):
        """固定Task IDを保持し、APIから呼び出した操作を記録する。"""
        self.uid = uuid4()
        self.calls = []

    async def start(self, request, *, decision_strategy):
        """選択した判断方式を含む非同期の受付契約だけ模倣する。"""
        self.calls.append(("start", request, decision_strategy))
        return {"task_id": str(self.uid), "kind": "start", "status": "accepted"}

    async def answer(self, task_id, reply):
        """APIから受け取った本人回答のTask IDと本文を記録する。"""
        self.calls.append(("answer", task_id, reply))
        return {"task_id": str(task_id), "kind": "answer", "status": "accepted"}

    async def review(self, task_id, decision):
        """回答のみReviewの依頼を記録する。MAGI実行はしない。"""
        self.calls.append(("review", task_id, decision))
        return {"task_id": str(task_id), "kind": "review", "status": "accepted"}

    def stop(self, task_id):
        """停止要求の配送だけ記録し、Task停止確定にはしない。"""
        self.calls.append(("stop", task_id))
        return {"task_id": str(task_id), "status": "running", "stop_requested": True}

    def operation(self, task_id):
        """実Taskとは別のプロセス内Operation状態を返す。"""
        self.calls.append(("operation", task_id))
        return {"task_id": str(task_id), "status": "running", "stop_requested": False}


class VueDailyCommandApiTests(unittest.TestCase):
    """Task操作APIを注入した場合だけ受付し、既存のGETを壊さないことを確認する。"""

    def setUp(self):
        """fake Queryとfake Commandを注入したHTTP TestClientを用意する。"""
        self.query = FakeQueries()
        self.commands = FakeCommands()
        self.client = TestClient(
            create_app(queries=self.query, commands=self.commands),
            base_url="http://127.0.0.1:8097",
        )

    def test_opt_in_task_commands_return_accepted_without_persisting(self):
        """受付は202であり、Taskの確定・完了を返したと見なさない。"""
        base = "/api/v1/ritsuko"
        self.assertEqual(self.client.get(base + "/health").json()["mode"], "commands_enabled")
        start = self.client.post(base + "/tasks", json={
            "request": "調査", "decision_strategy": "llm",
        })
        self.assertEqual(start.status_code, 202)
        self.assertEqual(start.json()["status"], "accepted")
        self.assertEqual(self.commands.calls[0], ("start", "調査", "llm"))
        uid = str(self.commands.uid)
        answer = self.client.post(base + "/tasks/" + uid + "/answer", json={"reply": "追加"})
        self.assertEqual(answer.status_code, 202)
        review = self.client.post(base + "/tasks/" + uid + "/review", json={"decision": "answer_only"})
        self.assertEqual(review.status_code, 202)
        self.assertEqual(self.client.post(base + "/tasks/" + uid + "/stop").status_code, 200)
        self.assertEqual(self.client.get(base + "/operations/" + uid).json()["status"], "running")
        self.assertEqual([call[0] for call in self.commands.calls],
                         ["start", "answer", "review", "stop", "operation"])
        self.assertEqual(self.client.get(base + "/tasks").status_code, 200)
        self.assertEqual(self.query.calls[-1][0], "recent")

    def test_restart_without_in_memory_operation_uses_persisted_task_only(self):
        """Commandサービス再構築後、Taskの実状態だけ取得しWorker生存は推測しない。"""
        from ritsuko.application.task_commands import TaskCommands

        new_service = TaskCommands(object(), self.query, lambda: [])
        app = create_app(queries=self.query, commands=new_service)
        client = TestClient(app, base_url="http://127.0.0.1:8097")
        self.query.task["status"] = "running"
        response = client.get(
            "/api/v1/ritsuko/operations/" + str(self.query.uid),
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["status"], "operation_not_in_process")
        self.assertEqual(payload["task_status"], "running")
        self.assertEqual(payload["source"], "persisted_task")
        self.assertTrue(payload["execution_reconciliation_required"])
        self.assertIsNone(payload["stop_requested"])
        self.assertNotIn("request", payload)
        self.assertEqual(
            client.post("/api/v1/ritsuko/tasks/" + str(self.query.uid) + "/stop").status_code,
            409,
        )
        self.assertEqual(
            client.get("/api/v1/ritsuko/operations/" + str(uuid4())).status_code,
            404,
        )

    def test_invalid_input_and_cross_origin_do_not_call_commands(self):
        """無効な入力・他Originの書込要求を拒否し、コマンドを呼び出さない。"""
        base = "/api/v1/ritsuko"
        self.assertEqual(self.client.post(base + "/tasks", json={
            "request": "", "decision_strategy": "standard",
        }).status_code, 422)
        self.assertEqual(self.client.post(base + "/tasks", json={
            "request": "依頼",
        }).status_code, 422)
        self.assertEqual(self.client.post(base + "/tasks", json={
            "request": "依頼", "decision_strategy": "future_mode",
        }).status_code, 422)
        self.assertEqual(self.client.post(
            base + "/tasks", json={"request": "依頼", "decision_strategy": "llm"},
            headers={"Origin": "https://evil.invalid"},
        ).status_code, 403)
        self.assertEqual(self.client.post(
            base + "/tasks/" + str(self.commands.uid) + "/review",
            json={"decision": "arbitrary"},
        ).status_code, 422)
        self.assertEqual(self.commands.calls, [])


if __name__ == "__main__":
    unittest.main()
