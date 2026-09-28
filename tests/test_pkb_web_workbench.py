"""Offline tests for local-only Workbench persistence and run-state isolation."""
import json
import tempfile
import time
import unittest
from pathlib import Path

from pkb_proto.web_workbench_core import ExperimentRunner, RunStore


class WorkbenchCoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "runs.sqlite3"
        self.store = RunStore(self.path)

    def tearDown(self):
        self.tmp.cleanup()

    @staticmethod
    def success(payload):
        return {"model": payload["model"], "done_reason": "stop",
                "prompt_eval_count": 82, "eval_count": 19,
                "message": {"content": '{"entity":"サブPC","event":"DRV-A1へ更新"}',
                            "thinking": "not to be stored"}}

    def runner(self, transport=None):
        return ExperimentRunner(self.store, transport=transport or self.success)

    def completed(self, run_id):
        for _ in range(100):
            result = self.store.get(run_id)
            if result["status"] not in ("queued", "running"):
                return result
            time.sleep(0.01)
        self.fail("Run did not finish")

    def test_submit_persist_reopen_and_rerun_with_real_payload(self):
        runner = self.runner()
        try:
            a = runner.submit(episode_id="pc-01", mode="抽出：簡略",
                              model="qwen3.5:9b", think="無効", verify_model=False)
            original = self.completed(a["id"])
            self.assertEqual(original["status"], "completed")
            self.assertEqual(original["payload"]["think"], False)
            self.assertEqual(original["result"]["json_parse"], "valid_json")
            self.assertNotIn("not to be stored", json.dumps(original, ensure_ascii=False))
            b = runner.rerun(a["id"], verify_model=False)
            other = self.completed(b["id"])
            self.assertNotEqual(other["id"], original["id"])
            self.assertEqual(other["payload"], original["payload"])
            self.assertEqual(len(self.store.list()), 2)
        finally:
            runner.close()
        reopened = RunStore(self.path)
        self.assertEqual(reopened.get(original["id"])["result"]["check"],
                         "簡略JSON構造OK・意味未検証")
        self.assertEqual(len(reopened.list()), 2)

    def test_missing_model_and_invalid_parameters_never_call_transport(self):
        calls = []
        runner = self.runner(lambda payload: calls.append(payload))
        try:
            for params in (
                {"episode_id": "other", "mode": "抽出：簡略", "model": "qwen3.5:9b"},
                {"episode_id": "pc-01", "mode": "private", "model": "qwen3.5:9b"},
                {"episode_id": "pc-01", "mode": "抽出：簡略", "model": "http://remote"},
                {"episode_id": "pc-01", "mode": "抽出：簡略",
                 "model": "qwen3.5:9b", "predict": 999},
            ):
                with self.assertRaises(ValueError):
                    runner.submit(**params, verify_model=False)
            runner.installed_models = lambda: ["llama3.1:8b"]
            with self.assertRaises(ValueError):
                runner.submit(episode_id="pc-01", mode="抽出：簡略",
                              model="qwen3.5:9b")
            self.assertFalse(calls)
            self.assertFalse(self.store.list())
        finally:
            runner.close()

    def test_length_without_final_content_is_not_success(self):
        def truncated(_payload):
            return {"done_reason": "length", "message": {
                "content": "", "thinking": "internal model thought"}}
        runner = self.runner(truncated)
        try:
            r = runner.submit(episode_id="pc-01", mode="抽出：簡略",
                              model="qwen3.5:9b", verify_model=False)
            result = self.completed(r["id"])
            self.assertEqual(result["status"], "check_failed")
            self.assertEqual(result["result"]["check"], "回答本文なし")
            self.assertNotIn("internal model thought", json.dumps(result, ensure_ascii=False))
        finally:
            runner.close()

    def test_timeout_recorded_separately_from_invalid_json(self):
        def timeout(_payload):
            raise TimeoutError("synthetic timeout")
        runner = self.runner(timeout)
        try:
            r = runner.submit(episode_id="pc-01", mode="抽出：簡略",
                              model="qwen3.5:9b", verify_model=False)
            result = self.completed(r["id"])
            self.assertEqual(result["status"], "timeout")
            self.assertIn("synthetic timeout", result["error"])
            self.assertIsNone(result["result"])
        finally:
            runner.close()

    def test_started_run_becomes_interrupted_after_restart(self):
        runner = self.runner()
        try:
            r = runner.submit(episode_id="pc-01", mode="抽出：簡略",
                              model="qwen3.5:9b", verify_model=False)
            self.completed(r["id"])
        finally:
            runner.close()
        with self.store._connect() as db:
            db.execute("UPDATE runs SET status='running', finished_at=NULL WHERE id=?",
                       (r["id"],))
        recovered = RunStore(self.path).get(r["id"])
        self.assertEqual(recovered["status"], "interrupted")
        self.assertIsNotNone(recovered["finished_at"])

    def test_fixed_echo_does_not_send_episode_text(self):
        echo = lambda payload: {"done_reason": "stop", "message": {"content": "ABC123"}}
        runner = self.runner(echo)
        try:
            r = runner.submit(episode_id="pc-01", mode="疎通：固定文字列",
                              model="llama3.1:8b", verify_model=False)
            result = self.completed(r["id"])
            self.assertEqual(result["status"], "completed")
            self.assertNotIn("format", result["payload"])
            self.assertNotIn("DRV-A1", json.dumps(result["payload"], ensure_ascii=False))
        finally:
            runner.close()


if __name__ == "__main__":
    unittest.main()
