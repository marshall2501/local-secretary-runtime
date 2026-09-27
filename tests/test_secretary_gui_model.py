"""GUI presentation and user-question tests; runs without the NiceGUI package."""
from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "secretary"))
from secretary_core import SecretaryCore, load_state, save_state
from secretary_gui_model import (
    can_reply, can_retry, event_rows, evidence_rows, task_summaries,
)


class GuiModelTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.folder = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def make_task(self, *, status="waiting_user", request="今どんな状態？"):
        task_id = str(uuid.uuid4())
        state = {
            "task_id": task_id, "original_request": request,
            "target": "架空テストPC", "domain": "pc", "mode": "fixture",
            "model": "qwen3:8b", "status": status,
            "awaiting": "終了時のエラーは出ましたか？",
            "answer": "終了時のエラーは出ましたか？",
            "events": [
                {"type": "decision", "action": "ask_user",
                 "reason": "原因を絞るには現在のエラーが必要"},
                {"type": "finish", "status": status},
            ],
            "observations": [{
                "specialist": "memory", "records": [
                    {"id": "M1", "text": "ram_gb=16",
                     "source": "fictional user statement",
                     "verification": "unverified",
                     "linkage": "explicit_entity_match", "simulated": False},
                    {"id": "M2", "text": "Simulated diagnostic only",
                     "source": "fictional diagnostic", "simulated": True,
                     "linkage": "unlinked_same_domain_not_proof_of_target"},
                ],
            }],
        }
        save_state(state, self.folder)
        return state

    def test_saved_requests_are_listed_without_exposing_raw_json(self):
        first = self.make_task(request="以前の経験を調べて")
        summaries = task_summaries(self.folder)
        self.assertEqual(len(summaries), 1)
        self.assertEqual(summaries[0]["task_id"], first["task_id"])
        self.assertEqual(summaries[0]["request"], "以前の経験を調べて")
        self.assertEqual(summaries[0]["status_label"], "回答待ち")
        (self.folder / "not-a-task.json").write_text("invalid", encoding="utf-8")
        self.assertEqual(len(task_summaries(self.folder)), 1)

    def test_timeline_includes_decisions_and_questions(self):
        state = self.make_task()
        events = event_rows(state)
        self.assertEqual(events[0]["title"], "統括役の判断")
        self.assertTrue(any("ユーザーに質問" in d for d in events[0]["details"]))
        self.assertEqual(events[1]["title"], "今回の処理を終了")
        state["events"].append({"type": "evidence_unchanged",
                                "feedback": "新しい資料はありません"})
        repeated = event_rows(state)[-1]
        self.assertEqual(repeated["title"], "再調査したが新しい資料なし")
        self.assertIn("新しい資料はありません", repeated["details"])

    def test_evidence_marks_unverified_and_unlinked_simulation(self):
        state = self.make_task()
        rows = evidence_rows(state)
        self.assertEqual(rows[0]["id"], "M1")
        self.assertIn("未検証", rows[0]["flags"])
        self.assertEqual(rows[1]["id"], "M2")
        self.assertIn("模擬", rows[1]["flags"])
        self.assertIn("対象との紐付けなし", rows[1]["flags"])

    def test_only_waiting_task_accepts_reply(self):
        state = self.make_task()
        self.assertTrue(can_reply(state))
        self.assertFalse(can_retry(state, set()))
        state["status"] = "answered"
        self.assertFalse(can_reply(state))
        state["status"] = "running"
        self.assertFalse(can_retry(state, {state["task_id"]}))
        self.assertTrue(can_retry(state, set()))

    def test_preallocated_gui_id_and_manual_answer_resume(self):
        task_id = str(uuid.uuid4())
        core = SecretaryCore(self.folder)
        with patch("secretary_core._json_llm", return_value={
            "action": "ask_user", "reason": "現在の症状を確認",
            "response": "エラー表示はありましたか？",
        }):
            paused = core.start(
                "ゲームが突然終了した。原因わかる？",
                target="架空テストPC", domain="pc", mode="fixture",
                task_id=task_id,
            )
        self.assertEqual(paused["status"], "waiting_user")
        self.assertEqual(paused["task_id"], task_id)
        self.assertTrue(can_reply(load_state(task_id, self.folder)))
        fresh = SecretaryCore(self.folder)
        with patch("secretary_core._json_llm", side_effect=[
            {"action": "answer", "response": (
                "エラー表示がないという追加情報だけでは原因は未特定です。"
                "別のログを確認する必要があります。"
            ), "reason": "取得済みの情報で限定回答", "used_ids": []},
            {"supported": True, "feedback": "実観測に即した限定的回答"},
        ]):
            ended = fresh.resume(task_id, "エラー表示はありません")
        self.assertEqual(ended["status"], "answered")
        self.assertEqual(ended["task_id"], task_id)
        self.assertTrue(any(e.get("type") == "user_update" and
                            e.get("text") == "エラー表示はありません"
                            for e in ended["events"]))
        self.assertFalse(can_reply(ended))
        self.assertEqual(load_state(task_id, self.folder)["status"], "answered")
        with self.assertRaisesRegex(ValueError, "already exists"):
            fresh.start("誤って同じIDで新規依頼", target="架空テストPC",
                        domain="pc", mode="fixture", task_id=task_id)


if __name__ == "__main__":
    unittest.main()
