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

    def test_boundary_does_not_open_task_write_or_external_access(self):
        """Task更新APIが存在せず、想定外のHost/Originを拒否することを検査する。"""
        self.assertEqual(self.client.get(
            "/api/v1/ritsuko/health"
        ).json(), {"status": "ok", "synthetic": False, "mode": "read_only"})
        self.assertEqual(self.client.post(
            "/api/v1/ritsuko/tasks", json={"request": "do something"}
        ).status_code, 405)
        self.assertEqual(self.client.post(
            "/api/v1/ritsuko/tasks/" + str(self.queries.uid) + "/answer",
            json={"reply": "test"},
        ).status_code, 404)
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


if __name__ == "__main__":
    unittest.main()
