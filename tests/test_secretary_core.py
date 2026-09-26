"""Deterministic orchestration tests; no Ollama, API or real PC required.

Run in the isolated LangGraph venv:
python -m unittest discover -s tests -p "test_secretary_core.py" -v
"""
from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "secretary"))
import secretary_core


class ScriptedModel:
    def __init__(self, decisions, reviews=(True,)):
        self.decisions = list(decisions)
        self.reviews = list(reviews)
        self.manager_contexts = []

    def __call__(self, model, instruction, context):
        if "独立した回答監査担当" in instruction:
            verdict = self.reviews.pop(0) if self.reviews else True
            return {"supported": verdict, "feedback": (
                "適切な不確実性" if verdict else "模擬結果から原因確定はできない")}
        self.manager_contexts.append(context)
        if not self.decisions:
            raise AssertionError("Manager called too many times")
        return self.decisions.pop(0)


def delegate(which, query):
    return {"action": "delegate", "specialist": which,
            "query": query, "reason": "情報が不足", "used_ids": []}


def answer(text, used=()):
    return {"action": "answer", "response": text,
            "reason": "取得した根拠の範囲で回答", "used_ids": list(used)}


class SecretaryCoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.folder = Path(self.tmp.name)
        self.core = secretary_core.SecretaryCore(self.folder)

    def tearDown(self):
        self.tmp.cleanup()

    def start(self, script, question="架空テストPCの以前の経験を踏まえて原因わかる？"):
        with patch.object(secretary_core, "_json_llm", side_effect=script):
            return self.core.start(question, target="架空テストPC",
                                   domain="pc", mode="fixture")

    def test_manager_delegates_reviews_then_answers(self):
        script = ScriptedModel([
            delegate("memory", "以前の症状と対策"),
            delegate("research", "ゲーム突然終了の確認事項"),
            answer("模擬資料しかなく原因は特定できません。実際のログを確認してください。",
                   ["M1", "R1"]),
        ])
        result = self.start(script)
        self.assertEqual(result["status"], "answered")
        self.assertEqual([x["to"] for x in result["events"]
                          if x["type"] == "dispatch"], ["memory", "research"])
        self.assertEqual(len(result["observations"]), 2)
        self.assertTrue(any(x["type"] == "answer_review" and x["supported"]
                            for x in result["events"]))
        self.assertTrue((self.folder / (result["task_id"] + ".json")).exists())
        self.assertTrue(result["observations"][0]["records"][1]["simulated"])
        self.assertEqual(result["observations"][0]["records"][1]["linkage"],
                         "unlinked_same_domain_not_proof_of_target")

    def test_unchecked_history_cannot_be_declared_missing(self):
        script = ScriptedModel([
            answer("過去の記録はありません"),
            delegate("memory", "対象PCの記録を検索"),
            answer("未検証のRAM情報はあるが、模擬診断はPCに未紐付けです。", ["M1"]),
        ])
        result = self.start(script)
        self.assertEqual(result["status"], "answered")
        self.assertEqual(len([x for x in result["events"]
                              if x["type"] == "dispatch"]), 1)
        self.assertGreaterEqual(result["review_attempts"], 1)
        self.assertIn("未照会", script.manager_contexts[1]["validation_feedback"])

    def test_reviewer_rejects_overclaim_and_manager_reassigns(self):
        script = ScriptedModel([
            delegate("memory", "過去の確認"),
            answer("ドライバーが確実に原因です", ["M2"]),
            delegate("research", "架空ゲームの突然終了ガイド"),
            answer("原因は未特定で、実際のログが必要です。", ["R1"]),
        ], reviews=[False, True])
        result = self.start(script)
        self.assertEqual(result["status"], "answered")
        self.assertTrue(any(x["type"] == "answer_review" and
                            x["supported"] is False for x in result["events"]))
        self.assertEqual([x["to"] for x in result["events"]
                          if x["type"] == "dispatch"], ["memory", "research"])

    def test_question_pause_restarts_with_same_task(self):
        script = ScriptedModel([{
            "action": "ask_user", "response": "終了時のエラーは出ましたか？",
            "reason": "追加観測が必要",
        }])
        first = self.start(script, "ゲームが突然終了します。原因わかる？")
        self.assertEqual(first["status"], "waiting_user")
        self.assertEqual(self.core.resume(first["task_id"])["status"], "waiting_user")
        restarted = secretary_core.SecretaryCore(self.folder)
        script2 = ScriptedModel([
            answer("画面にエラーがないという情報だけでは原因未特定です。"),
        ])
        with patch.object(secretary_core, "_json_llm", side_effect=script2):
            second = restarted.resume(first["task_id"], "エラー表示はありません")
        self.assertEqual(second["status"], "answered")
        self.assertEqual(first["task_id"], second["task_id"])
        self.assertEqual(second["original_request"], first["original_request"])
        self.assertEqual(script2.manager_contexts[0]["new_user_information"],
                         "エラー表示はありません")

    def test_no_fictional_research_in_live_mode(self):
        state = {"mode": "live"}
        with self.assertRaisesRegex(RuntimeError, "Real research adapter"):
            self.core._research(state, "game crash")

    def test_wrong_fixture_entity_rejected(self):
        with self.assertRaisesRegex(ValueError, "Fixture supports"):
            self.core.start("原因わかる？", target="メインPC",
                            domain="pc", mode="fixture")


    def test_two_false_reviews_report_successful_memory_not_lookup_failure(self):
        # Actual sub-PC regression: memory returned M1/M2, but qwen3's
        # independent reviewer rejected even a properly qualified answer.
        script = ScriptedModel([
            delegate("memory", "以前の経験から原因を調べて"),
            answer("模擬記録のみであり、原因は特定できません。", ["M1", "M2"]),
            answer("対象の実機診断は未確認です。原因を特定できません。", ["M1"]),
        ], reviews=[False, False])
        result = self.start(script)
        self.assertEqual(result["status"], "answered")
        self.assertIn("記憶の照会は完了しました", result["answer"])
        self.assertIn("M1", result["answer"])
        self.assertIn("未検証", result["answer"])
        self.assertIn("M2", result["answer"])
        self.assertIn("模擬", result["answer"])
        self.assertIn("今回の対象に紐付けなし", result["answer"])
        self.assertNotIn("過去情報の確認が完了しなかった", result["answer"])
        self.assertTrue(any(x["type"] == "evidence_fallback" and
                            x["memory_retrieved"] for x in result["events"]))

    def test_lookup_error_is_not_reported_as_no_records(self):
        script = ScriptedModel([
            delegate("memory", "以前の経験"),
            answer("過去の記録はありません"),
            answer("取得できていないので分かりません"),
        ])
        with patch.object(secretary_core, "_json_llm", side_effect=script):
            with patch.object(self.core, "_memory",
                              side_effect=RuntimeError("API unavailable")):
                result = self.core.start(
                    "以前の経験から原因わかる？",
                    target="架空テストPC", domain="pc", mode="fixture")
        self.assertIn("取得に失敗", result["answer"])
        self.assertNotIn("記録が存在しない", result["answer"])
        self.assertFalse(any(x["type"] == "answer_review" for x in result["events"]))
        self.assertTrue(any(x["type"] == "evidence_fallback" and
                            not x["memory_retrieved"] for x in result["events"]))

    def test_exhausted_budget_uses_tool_evidence_without_llm(self):
        state = {
            "task_id": "11111111-1111-4111-8111-111111111111",
            "original_request": "以前の経験から原因わかる？",
            "target": "架空テストPC", "domain": "pc",
            "mode": "fixture", "model": "qwen3:8b",
            "status": "running", "turns": 9, "review_attempts": 0,
            "feedback": "", "observations": [{
                "specialist": "memory", "target": "架空テストPC",
                "source_mode": "fixture", "records": [{
                    "id": "M1", "text": "ram_gb=16",
                    "verification": "unverified", "simulated": False,
                    "linkage": "explicit_entity_match",
                }],
            }], "events": [],
        }
        with patch.object(secretary_core, "_json_llm",
                          side_effect=AssertionError("Do not call the model")):
            result = self.core.run(state)
        self.assertEqual(result["status"], "answered")
        self.assertIn("照会は完了", result["answer"])
        self.assertIn("M1", result["answer"])


if __name__ == "__main__":
    unittest.main()
