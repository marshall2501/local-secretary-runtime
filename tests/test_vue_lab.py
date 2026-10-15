"""Vue版秘書AIの入口契約を架空Taskのみで検証する。

本番DB・Cloud LLM・外部Action・Web UI稼働ポートにアクセスしない。
"""
from __future__ import annotations

import time
import unittest
from uuid import uuid4

from fastapi.testclient import TestClient

from interfaces.vue_lab.app import create_app
from interfaces.vue_lab.task_service import IsolatedTaskService

TOKEN = "isolated-vue-lab-only-credential-at-least-32"


class VueLabApiTests(unittest.TestCase):
    """共通RITSUKO Applicationを使う隔離APIの状態・認証・更新を確認する。"""

    def setUp(self):
        """毎テスト専用の合成TaskストアとHTTPプロセスを用意する。"""
        self.service = IsolatedTaskService()
        self.app = create_app(service=self.service, token=TOKEN)
        self.context = TestClient(
            self.app, base_url="http://127.0.0.1:8098",
        )
        self.client = self.context.__enter__()

    def tearDown(self):
        """ASGIイベントループを終了し、架空状態を捨てる。"""
        self.context.__exit__(None, None, None)

    def login(self) -> str:
        """認証CookieとCSRFを取得する。"""
        response = self.client.post("/api/lab/login", json={"token": TOKEN})
        self.assertEqual(response.status_code, 200)
        self.assertIn("httponly", response.headers["set-cookie"].lower())
        self.assertIn("samesite=strict", response.headers["set-cookie"].lower())
        return response.json()["csrf"]

    def wait_status(self, task_id: str, expected: str) -> dict:
        """ポーリング契約で合成Taskの終状態まで待つ。"""
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline:
            response = self.client.get(f"/api/lab/tasks/{task_id}")
            self.assertEqual(response.status_code, 200)
            task = response.json()
            if task["status"] == expected:
                return task
            time.sleep(0.025)
        self.fail(f"synthetic task did not reach {expected}")

    def test_auth_host_origin_csrf_and_no_real_capabilities(self):
        """未認証・CSRFなし・違反Host/Originを拒否し、本番機能を公開しない。"""
        self.assertEqual(self.client.get("/api/lab/tasks").status_code, 401)
        self.assertEqual(self.client.post("/api/lab/login", json={
            "token": "incorrect-token-not-accepted-even-if-long"
        }).status_code, 401)
        self.assertEqual(self.client.get("/api/lab/health").json()["synthetic"], True)
        token = self.login()
        self.assertEqual(self.client.post("/api/lab/tasks", json={
            "request": "synthetic request"
        }).status_code, 403)
        self.assertEqual(self.client.post("/api/lab/tasks", headers={
            "X-CSRF-Token": token, "Origin": "http://evil.invalid"
        }, json={"request": "synthetic request"}).status_code, 403)
        self.assertEqual(self.client.get("/api/lab/tasks", headers={
            "Host": "evil.invalid"
        }).status_code, 403)
        self.assertEqual(self.client.get("/api/lab/tasks", headers={
            "X-Forwarded-For": "1.2.3.4"
        }).status_code, 403)
        self.assertEqual(self.client.post("/api/lab/tasks", headers={
            "X-CSRF-Token": token
        }, json={"request": " "}).status_code, 422)
        self.assertEqual(self.client.post("/tasks", headers={
            "X-CSRF-Token": token
        }, json={"request": "synthetic"}).status_code, 404)

    def test_shared_application_task_start_answer_completed_and_f5_readback(self):
        """RITSUKO共通ApplicationのTask IDを再利用し完了状態を照会できる。"""
        csrf = self.login()
        accepted = self.client.post("/api/lab/tasks", json={
            "request": "Pythonの辞書とは何ですか？"
        }, headers={"X-CSRF-Token": csrf})
        self.assertEqual(accepted.status_code, 202)
        created = accepted.json()
        self.assertTrue(created["synthetic"])
        uid = created["task_id"]
        self.assertIn(created["status"], ("queued", "running", "waiting_user"))
        pending = self.wait_status(uid, "waiting_user")
        self.assertIn("架空テスト", pending["question"])
        self.assertEqual(self.client.get("/api/lab/session").status_code, 200)
        # ブラウザF5後の照会と同じCookie + Task IDによる復元。
        self.assertEqual(self.client.get(f"/api/lab/tasks/{uid}").json(), pending)
        accepted2 = self.client.post(f"/api/lab/tasks/{uid}/answer", json={
            "reply": "Pythonのdictの概要"
        }, headers={"X-CSRF-Token": csrf})
        self.assertEqual(accepted2.status_code, 202)
        finished = self.wait_status(uid, "completed")
        self.assertEqual(finished["task_id"], uid)
        self.assertIn("架空テスト回答", finished["message"])
        self.assertTrue(any(row["task_id"] == uid for row in
                            self.client.get("/api/lab/tasks").json()["items"]))
        # 重複回答で別の完了を作らない。
        self.assertEqual(self.client.post(f"/api/lab/tasks/{uid}/answer", json={
            "reply": "again"
        }, headers={"X-CSRF-Token": csrf}).status_code, 409)
        self.assertEqual(self.client.post(f"/api/lab/tasks/{uid}/stop", json={},
            headers={"X-CSRF-Token": csrf}).status_code, 409)

    def test_stop_uses_same_task_and_no_silent_completion(self):
        """停止要求後は架空Taskが勝手に完了と表示されない。"""
        csrf = self.login()
        started = self.client.post("/api/lab/tasks", json={"request": "stop test"},
                                   headers={"X-CSRF-Token": csrf}).json()
        uid = started["task_id"]
        result = self.client.post(f"/api/lab/tasks/{uid}/stop", json={},
                                  headers={"X-CSRF-Token": csrf})
        self.assertEqual(result.status_code, 200)
        paused = self.wait_status(uid, "paused")
        self.assertFalse(paused["message"])
        self.assertEqual(self.client.get(f"/api/lab/tasks/{uuid4()}").status_code, 404)

    def test_logout_removes_session_without_deleting_fixture(self):
        """ログアウトでCookieは失効するがTask正本は削除しない。"""
        csrf = self.login()
        self.assertEqual(self.client.post("/api/lab/logout", json={},
                            headers={"X-CSRF-Token": csrf}).status_code, 200)
        self.assertEqual(self.client.get("/api/lab/tasks").status_code, 401)
        self.assertEqual(self.service.list_tasks(), [])

    def test_external_source_and_review_are_not_enabled(self):
        """架空fixtureで本番SourceやProposal Reviewを実行できない。"""
        with self.assertRaisesRegex(RuntimeError, "synthetic_source_access_disabled"):
            self.service._reject_source()
        with self.assertRaisesRegex(ValueError, "proposal_review_out_of_scope"):
            self.service._unsupported_review()


if __name__ == "__main__":
    unittest.main()
