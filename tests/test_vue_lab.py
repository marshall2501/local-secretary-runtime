# Provenance-Origin: original
# Provenance-AI: AI-generated
"""Vue版秘書AIの架空Task入口をログイン不要のlocalhost環境で検証する。

実運用DB、Cloud LLM、外部Actionへ接続せず、本人の試作操作を確認する。
"""
from __future__ import annotations

import time
import unittest
from uuid import uuid4

from fastapi.testclient import TestClient

from interfaces.vue_lab.app import create_app
from interfaces.vue_lab.task_service import IsolatedTaskService


class VueLabApiTests(unittest.TestCase):
    """架空Task用APIとRITSUKO共通Applicationの状態遷移を確認する。"""

    def setUp(self):
        """テスト専用の合成TaskストアとTestClientを作る。"""
        self.service = IsolatedTaskService()
        self.app = create_app(service=self.service)
        self.context = TestClient(self.app, base_url="http://127.0.0.1:8098")
        self.client = self.context.__enter__()

    def tearDown(self):
        """クライアントのイベントループを閉じ、架空Taskを破棄する。"""
        self.context.__exit__(None, None, None)

    def wait_status(self, task_id: str, expected: str) -> dict:
        """同一Task IDの状態を、短時間だけ繰り返し確認する。"""
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline:
            response = self.client.get(f"/api/lab/tasks/{task_id}")
            self.assertEqual(response.status_code, 200)
            task = response.json()
            if task["status"] == expected:
                return task
            time.sleep(0.025)
        self.fail(f"synthetic task did not reach {expected}")

    def test_loginless_localhost_and_boundary(self):
        """ログイン不要で架空Taskを読めるが、外部Host/Originを拒否する。"""
        self.assertEqual(self.client.get("/api/lab/tasks").status_code, 200)
        self.assertEqual(self.client.get("/api/lab/health").json()["synthetic"], True)
        self.assertEqual(self.client.get("/api/lab/session").status_code, 404)
        self.assertEqual(self.client.post("/api/lab/login", json={}).status_code, 404)
        self.assertEqual(self.client.get("/api/lab/tasks", headers={
            "Host": "evil.invalid"
        }).status_code, 403)
        self.assertEqual(self.client.post("/api/lab/tasks", headers={
            "Origin": "http://evil.invalid"
        }, json={"request": "synthetic"}).status_code, 403)
        self.assertEqual(self.client.post("/api/lab/tasks", headers={
            "Sec-Fetch-Site": "cross-site"
        }, json={"request": "synthetic"}).status_code, 403)
        self.assertEqual(self.client.get("/api/lab/tasks", headers={
            "X-Forwarded-For": "1.2.3.4"
        }).status_code, 403)
        self.assertEqual(self.client.post("/api/lab/tasks", json={
            "request": " "
        }).status_code, 422)
        self.assertEqual(self.client.post("/tasks", json={
            "request": "synthetic"
        }).status_code, 404)

    def test_shared_application_task_start_answer_completed_and_repeated_get(self):
        """架空Taskの開始・回答・完了と同一IDの再GETを確認する。

        ブラウザのF5操作や実DB再起動後の復元を検証する試験ではない。
        """
        accepted = self.client.post("/api/lab/tasks", json={
            "request": "Pythonの辞書とは何ですか？"
        })
        self.assertEqual(accepted.status_code, 202)
        created = accepted.json()
        self.assertTrue(created["synthetic"])
        uid = created["task_id"]
        self.assertIn(created["status"], ("queued", "running", "waiting_user"))
        pending = self.wait_status(uid, "waiting_user")
        self.assertIn("架空テスト", pending["question"])
        self.assertEqual(self.client.get(f"/api/lab/tasks/{uid}").json(), pending)
        accepted2 = self.client.post(f"/api/lab/tasks/{uid}/answer", json={
            "reply": "Pythonのdictの概要"
        })
        self.assertEqual(accepted2.status_code, 202)
        finished = self.wait_status(uid, "completed")
        self.assertEqual(finished["task_id"], uid)
        self.assertIn("架空テスト回答", finished["message"])
        self.assertTrue(any(row["task_id"] == uid for row in
                            self.client.get("/api/lab/tasks").json()["items"]))
        self.assertEqual(self.client.post(f"/api/lab/tasks/{uid}/answer", json={
            "reply": "again"
        }).status_code, 409)
        self.assertEqual(self.client.post(f"/api/lab/tasks/{uid}/stop", json={}).status_code, 409)

    def test_stop_uses_same_task_and_no_silent_completion(self):
        """停止要求は架空Taskの状態へ反映し、勝手に完了としない。"""
        started = self.client.post("/api/lab/tasks", json={"request": "stop test"}).json()
        uid = started["task_id"]
        result = self.client.post(f"/api/lab/tasks/{uid}/stop", json={})
        self.assertEqual(result.status_code, 200)
        paused = self.wait_status(uid, "paused")
        self.assertFalse(paused["message"])
        self.assertEqual(self.client.get(f"/api/lab/tasks/{uuid4()}").status_code, 404)

    def test_external_source_and_review_are_not_enabled(self):
        """実データ取得やProposal Reviewはこの隔離試作から実行できない。"""
        with self.assertRaisesRegex(RuntimeError, "synthetic_source_access_disabled"):
            self.service._reject_source()
        with self.assertRaisesRegex(ValueError, "proposal_review_out_of_scope"):
            self.service._unsupported_review()


if __name__ == "__main__":
    unittest.main()
