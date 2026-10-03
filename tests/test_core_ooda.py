import copy
import asyncio
import inspect
import unittest
from unittest.mock import patch

from nicegui import Client, core, ui
from interfaces.web import app as daily_pkb
from ritsuko.core.core_ooda import derive_ooda


class OodaRulesTests(unittest.TestCase):
    def test_phase_and_reason_for_existing_states(self):
        cases = [
            ({"status": "received", "phase": "input"}, [], "observe"),
            ({"status": "running"}, [], "observe"),
            ({"status": "running", "phase": "decide"}, [], "decide"),
            ({"status": "running", "selected_capability": "finance_read"}, [], "decide"),
            ({"status": "waiting_external", "selected_capability": "pkb_web_compare"}, [], "orient"),
            ({"status": "running", "phase": "awaiting_clarification"}, [], "orient"),
            ({"status": "awaiting_clarification"}, [], "orient"),
            ({"status": "waiting_external", "phase": "awaiting_clarification"},
             [{"action_status": "running"}], "act"),
            ({"status": "running", "phase": "decide"}, [{"action_status": "running"}], "act"),
            ({"status": "waiting_external"}, [{"action_status": "succeeded"}], "orient"),
            ({"status": "running", "selected_capability": "web_research"},
             [{"action_status": "succeeded"}], "orient"),
            ({"status": "running", "phase": "orient"}, [], "orient"),
            ({"status": "running", "phase": "act"}, [], "act"),
        ]
        for task, actions, expected in cases:
            with self.subTest(task=task, actions=actions):
                before = copy.deepcopy((task, actions))
                result = derive_ooda(task, actions)
                self.assertEqual(result.phase, expected)
                self.assertTrue(result.reason)
                self.assertIn("（", result.label)
                self.assertEqual((task, actions), before)

    def test_terminal_state_overrides_stale_phase_and_action(self):
        for status in ("completed", "failed", "error", "cancelled", "rejected"):
            with self.subTest(status=status):
                result = derive_ooda({"status": status, "phase": "act"},
                                     [{"action_status": "running"}])
                self.assertEqual(result.terminal, status)
                self.assertEqual(result.phase, "decide" if status == "completed" else None)

    def test_empty_and_unknown_states_are_not_active_steps(self):
        for task in (None, {}, {"status": "new_unknown_state", "phase": "future"}):
            self.assertIsNone(derive_ooda(task).phase)


class OodaPageTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.item = {"id": "11111111-1111-1111-1111-111111111111",
                     "status": "waiting_external", "phase": "awaiting_clarification",
                     "request": "架空Task", "revision": 1,
                     "selected_capability": "pkb_web_compare"}
        self.trace = {"task": {**self.item, "updated_at": "fixture"}, "actions": []}
        self.patches = [
            patch.object(core, "loop", asyncio.get_running_loop()),
            patch.object(daily_pkb, "load_open_core_tasks", return_value=[self.item]),
            patch.object(daily_pkb, "load_recent_core_tasks", return_value=[]),
            patch.object(daily_pkb, "load_core_task_trace", side_effect=lambda _: self.trace),
        ]
        for p in self.patches:
            p.start()
        self.client = Client(ui.page("/ooda-test"))
        with self.client:
            daily_pkb.core_page()

    async def asyncTearDown(self):
        self.client.delete()
        for p in reversed(self.patches):
            p.stop()

    def texts(self):
        return [getattr(e, "text", "") for e in self.client.elements.values()]

    def active_steps(self):
        return [e for e in self.client.elements.values() if e._props.get("aria-current") == "step"]

    def click_handler(self, text):
        element = next(e for e in self.client.elements.values() if getattr(e, "text", "") == text)
        listener = next(v.handler for v in element._event_listeners.values() if v.type == "click")
        # Await the registered application callback directly so failures cannot
        # be swallowed by NiceGUI's background event exception handler.
        return inspect.getclosurevars(listener).nonlocals["callback"]

    async def click(self, text):
        result = self.click_handler(text)()
        if inspect.isawaitable(result):
            await result
        await self.flush()

    async def flush(self):
        for _ in range(4):
            await asyncio.sleep(0)

    async def test_saved_task_switch_uses_action_snapshot_and_terminal_outside_loop(self):
        self.assertEqual(self.active_steps(), [])
        with self.client:
            await self.click("開く")
            self.assertEqual(len(self.active_steps()), 1)
            self.assertIn("Orient", self.active_steps()[0].text)
            self.assertIn("OODA: Orient（状況把握・意味づけ）", self.texts())
            self.trace["task"].update(status="running", phase="decide")
            self.trace["actions"] = [{"action_status": "running", "tool": "fixture",
                                      "operation": "read", "risk": "read_only"}]
            await self.click("開く")
            self.assertIn("Act", self.active_steps()[0].text)
            self.assertIn("OODA: Act（行動・実行）", self.texts())
            self.trace["task"].update(status="completed", phase="completed")
            await self.click("開く")
            self.assertEqual(self.active_steps(), [])
            self.assertIn("最終状態: completed（OODA外）", self.texts())

    async def test_request_clears_previous_phase_then_shows_new_wait(self):
        async def request_stub(*_):
            await self.flush()
            self.assertEqual(len(self.active_steps()), 1)
            self.assertIn("Observe", self.active_steps()[0].text)
            return {"task_id": self.item["id"], "status": "waiting_external",
                    "phase": "awaiting_clarification"}

        with self.client:
            await self.click("開く")
            with patch.object(daily_pkb.run, "io_bound", side_effect=request_stub):
                await self.click("依頼する")
            self.assertIn("Orient", self.active_steps()[0].text)

    async def test_resume_observes_reply_then_removes_active_step_on_completion(self):
        async def resume_stub(*_):
            await self.flush()
            self.assertIn("Observe", self.active_steps()[0].text)
            self.trace["task"].update(status="completed", phase="completed")
            return {"task_id": self.item["id"], "status": "completed", "phase": "completed"}

        with self.client:
            await self.click("開く")
            with patch.object(daily_pkb.run, "io_bound", side_effect=resume_stub):
                await self.click("同じTaskを再開")
            self.assertEqual(self.active_steps(), [])
            self.assertIn("最終状態: completed（OODA外）", self.texts())

    async def test_trace_failure_uses_current_result_without_stale_action(self):
        with self.client:
            self.trace["task"].update(status="running", phase="act")
            await self.click("開く")
            with patch.object(daily_pkb, "load_core_task_trace", side_effect=RuntimeError("fixture")):
                await self.click("開く")
            self.assertIn("Orient", self.active_steps()[0].text)
            self.assertIn("Traceを取得できません: fixture", self.texts())


if __name__ == "__main__":
    unittest.main()
