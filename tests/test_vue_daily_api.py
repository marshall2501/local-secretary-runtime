# Provenance-Origin: original
# Provenance-AI: AI-generated
"""Task参照APIのHTTP契約をfake Queryで検査する。

実DBへの接続や本人認証の有効性は、このテストの対象ではない。
"""
from __future__ import annotations

import unittest
from pathlib import Path
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

    def test_task_command_preview_needs_explicit_write_flag(self):
        """コマンドプレビューはimportや通常実行では実DBを開かない。"""
        from contextlib import redirect_stderr
        from io import StringIO
        from interfaces.vue_daily.commands_preview import main
        with redirect_stderr(StringIO()), self.assertRaises(SystemExit) as result:
            main([])
        self.assertEqual(result.exception.code, 2)

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

    async def recover_pending(self, *, limit=20):
        """API起動時に未着手Taskだけを復旧させる入口を模倣する。"""
        self.calls.append(("recover_pending", limit))
        return []

    async def start(self, request, *, decision_strategy, task_id=None):
        """選択方式と再送時のTask UUIDを受け取るfake受付。"""
        self.calls.append(("start", request, decision_strategy, task_id))
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
        self.assertEqual(self.commands.calls[0], ("start", "調査", "llm", None))
        # UUID固定の再送では、HTTP経路が同じIDをCommandsへ渡す。
        stable_id = uuid4()
        replay = self.client.post(base + "/tasks", json={
            "request": "再送依頼", "decision_strategy": "standard",
            "task_id": str(stable_id),
        })
        self.assertEqual(replay.status_code, 202)
        self.assertEqual(self.commands.calls[1],
                         ("start", "再送依頼", "standard", stable_id))
        uid = str(self.commands.uid)
        answer = self.client.post(base + "/tasks/" + uid + "/answer", json={"reply": "追加"})
        self.assertEqual(answer.status_code, 202)
        review = self.client.post(base + "/tasks/" + uid + "/review", json={"decision": "answer_only"})
        self.assertEqual(review.status_code, 202)
        self.assertEqual(self.client.post(base + "/tasks/" + uid + "/stop").status_code, 200)
        self.assertEqual(self.client.get(base + "/operations/" + uid).json()["status"], "running")
        self.assertEqual([call[0] for call in self.commands.calls],
                         ["start", "start", "answer", "review", "stop", "operation"])
        self.assertEqual(self.client.get(base + "/tasks").status_code, 200)
        self.assertEqual(self.query.calls[-1][0], "recent")

    def test_pending_recovery_only_on_explicit_command_app_startup(self):
        """通常previewは復旧起動せず、bindされたCommandだけstartupで走る。"""
        base = "http://127.0.0.1:8097"
        with TestClient(create_app(queries=self.query, commands=self.commands),
                        base_url=base) as client:
            self.assertEqual(client.get("/api/v1/ritsuko/health").status_code, 200)
        self.assertEqual(self.commands.calls, [("recover_pending", 20)])

        with TestClient(create_app(queries=self.query), base_url=base) as client:
            self.assertEqual(client.get("/api/v1/ritsuko/health").json()["mode"],
                             "read_only")
        self.assertEqual(self.commands.calls, [("recover_pending", 20)])

    def test_explicit_command_preview_does_not_resume_old_pending_on_boot(self):
        """実DB操作Previewは明示操作のみ。起動だけでは旧pendingを動かさない。"""
        base = "http://127.0.0.1:8099"
        app = create_app(
            queries=self.query, commands=self.commands,
            recover_pending_at_startup=False,
        )
        with TestClient(app, base_url=base) as client:
            self.assertEqual(
                client.get("/api/v1/ritsuko/health").json()["mode"],
                "commands_enabled",
            )
            self.assertEqual(
                client.get("/api/v1/ritsuko/tasks").status_code, 200,
            )
        self.assertFalse(self.commands.calls)

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



class VueTaskInputLayoutTests(unittest.TestCase):
    """入力欄の消失と長い既存回答の下への埋没を防ぐ静的UI回帰。"""

    def test_new_task_form_is_visible_before_saved_answer(self):
        """実Task入力の位置・条件・8097→8099案内をソースで確認する。"""
        frontend = (
            Path(__file__).resolve().parents[1]
            / "interfaces/vue_daily/frontend/src/features/ritsuko"
        )
        page = (frontend / "RitsukoPage.vue").read_text(encoding="utf-8")
        panel = (
            frontend / "components/TaskCommandPanel.vue"
        ).read_text(encoding="utf-8")

        self.assertLess(page.index("<TaskCommandPanel"), page.index("<CurrentTask"))
        self.assertIn('<textarea id="task-request"', panel)
        self.assertIn('v-model="request"', panel)
        self.assertIn(':disabled="busy || !commandsEnabled"', panel)
        self.assertIn('href="http://127.0.0.1:8099/ui/"', panel)
        self.assertIn('@submit.prevent="emit(\'start\')"', panel)
        # ユーザーが確認するまで「実Task API有効」の場合以外は送信不可。
        self.assertIn(':disabled="busy || !commandsEnabled || !request.trim()"', panel)
        # かつ入力フォーム自体を commandsEnabled の v-if で隠さない。
        form_start = panel.index('<form class="command-form"')
        form_end = panel.index("</form>", form_start)
        self.assertNotIn('<template v-if="commandsEnabled">', panel[:form_start])
        self.assertIn('<textarea id="task-request"', panel[form_start:form_end])


if __name__ == "__main__":
    unittest.main()
