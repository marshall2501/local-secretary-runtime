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
        # Explicitly requested history is now obtained before the first
        # model decision. The reviewer rejects a subsequent false statement.
        script = ScriptedModel([
            answer("過去の記録はありません"),
            answer("未検証のRAM情報はあるが、模擬診断はPCに未紐付けです。", ["M1"]),
        ], reviews=[False, True])
        result = self.start(script)
        self.assertEqual(result["status"], "answered")
        self.assertEqual([e["to"] for e in result["events"]
                          if e["type"] == "dispatch"], ["memory"])
        self.assertEqual(result["events"][0]["action"], "delegate")
        self.assertEqual(script.manager_contexts[0]["previous_observations"][0]
                         ["specialist"], "memory")
        self.assertGreaterEqual(result["review_attempts"], 1)
        self.assertIn("独立監査", script.manager_contexts[1]["validation_feedback"])

    def test_reviewer_rejects_overclaim_and_manager_reassigns(self):
        script = ScriptedModel([
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
        self.assertTrue(any(e.get("draft_answer") == "ドライバーが確実に原因です"
                            for e in result["events"]))

    def test_question_pause_restarts_with_same_task(self):
        script = ScriptedModel([{
            "action": "ask_user", "response": "終了時のエラーは出ましたか？",
            "reason": "追加観測が必要",
        }])
        first = self.start(script, "ゲームが突然終了します。原因わかる？")
        self.assertEqual(first["status"], "waiting_user")
        self.assertEqual(first["events"][0]["question"], "終了時のエラーは出ましたか？")
        self.assertIsNone(first["events"][0]["specialist"])
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


    def test_history_question_always_recalled_before_asking_user(self):
        script = ScriptedModel([{
            "action": "ask_user", "response": "現在のエラー表示は？",
            "reason": "対象の履歴を参照後に必要となる追加情報",
        }])
        result = self.start(script)
        self.assertEqual(result["status"], "waiting_user")
        actions = [(e.get("type"), e.get("action"), e.get("to"))
                   for e in result["events"]]
        self.assertEqual(actions[0][1], "delegate")
        self.assertEqual(actions[1][2], "memory")
        self.assertEqual(actions[3][1], "ask_user")
        self.assertEqual(script.manager_contexts[0]["previous_observations"]
                         [0]["specialist"], "memory")

    def test_waiting_legacy_task_recalls_history_on_next_reply(self):
        # Reproduce the on-device GUI screenshot: after user reply, the
        # older task has two research records but NO memory attempt.
        task_id = "81ad1824-4787-4dbe-8318-6bcc0faf5350"
        state = {
            "task_id": task_id,
            "original_request": (
                "過去の経験を確認して原因の切り分けを進め、"
                "情報が不足していれば質問し、調査を続けて"),
            "target": "架空テストPC", "domain": "pc",
            "mode": "fixture", "model": "qwen3:8b",
            "status": "waiting_user", "turns": 4,
            "review_attempts": 0, "user_update": "10分で終了",
            "feedback": "", "events": [
                {"type": "decision", "action": "ask_user", "reason": "追加情報"},
                {"type": "finish", "status": "waiting_user"},
            ],
            "observations": [{
                "specialist": "research", "records": [{
                    "id": "R1", "simulated": True,
                    "text": "fictional fixture reference",
                }],
            }],
        }
        secretary_core.save_state(state, self.folder)
        script = ScriptedModel([{
            "action": "ask_user",
            "reason": "履歴は対象に未紐付け。追加の実症状が必要",
            "response": "再起動後も起きますか？",
        }])
        with patch.object(secretary_core, "_json_llm", side_effect=script):
            result = self.core.resume(task_id, "再起動後も発生します")
        self.assertEqual(result["task_id"], task_id)
        self.assertEqual(result["status"], "waiting_user")
        self.assertEqual([e["to"] for e in result["events"]
                          if e.get("type") == "dispatch"], ["memory"])
        self.assertEqual(script.manager_contexts[0]["new_user_information"],
                         "再起動後も発生します")
        self.assertEqual(script.manager_contexts[0]["previous_observations"]
                         [-1]["specialist"], "memory")

    def test_same_research_rejected_after_user_reply_without_new_input(self):
        query = "架空ゲームの10分後終了"
        state = {
            "task_id": "1f0ea887-ab28-48e6-a9a8-dca69de2fd4b",
            "original_request": "症状を調べて",
            "target": "架空テストPC", "domain": "pc",
            "mode": "fixture", "model": "qwen3:8b",
            "status": "running", "turns": 3,
            "user_update": "エラー表示なし",
            "feedback": "",
            "decision": delegate("research", query),
            "events": [
                {"type": "user_update", "text": "エラー表示なし"},
                {"type": "dispatch", "to": "research", "query": query,
                 "signature": ["research", query.casefold()]},
                {"type": "evaluation", "feedback": "架空資料"},
            ],
            "observations": [{
                "specialist": "research",
                "records": [{"id": "R1", "text": "fictional notes"}],
            }],
        }
        result = self.core.dispatch(state)
        self.assertEqual(len(result["observations"]), 1)
        self.assertEqual(result["events"][-1]["type"], "duplicate_blocked")
        self.assertIn("同一依頼", result["feedback"])

    def test_different_query_same_research_material_not_duplicated(self):
        record = {"id": "R1", "text": "fictional document", "simulated": True}
        state = {
            "task_id": "d59908db-353a-480e-bc41-1c337439a237",
            "original_request": "調べて",
            "target": "架空テストPC", "domain": "pc",
            "mode": "fixture", "model": "qwen3:8b",
            "status": "running", "turns": 2,
            "feedback": "",
            "decision": delegate("research", "別の検索語"),
            "events": [{
                "type": "dispatch", "to": "research", "query": "以前の検索",
                "signature": ["research", "以前の検索"],
            }],
            "observations": [{
                "specialist": "research", "records": [record],
            }],
        }
        with patch.object(self.core, "_research", return_value={
            "specialist": "research", "records": [record],
        }):
            result = self.core.dispatch(state)
        self.assertEqual(len(result["observations"]), 1)
        self.assertEqual(result["events"][-1]["type"], "evidence_unchanged")
        self.assertIn("新しい資料は得られなかった", result["feedback"])



    def test_explicit_ongoing_goal_stays_open_after_factual_report(self):
        # Based on the first full clipboard log: the reviewer approved a
        # factually cautious answer, yet the user explicitly required
        # investigation to continue if the cause remains unknown.
        request = (
            "過去の経験を確認し、原因の切り分けを進めてください。"
            "原因が不明という報告だけで調査を終了しないでください。"
        )
        script = ScriptedModel([
            answer("RAM容量の記録は未検証で、原因はまだ分かりません。", ["M1"]),
        ])
        result = self.start(script, question=request)
        self.assertEqual(result["status"], "waiting_user")
        self.assertIn("原因はまだ分かりません", result["latest_report"])
        self.assertEqual(secretary_core.load_state(result["task_id"], self.folder)["latest_report"], result["latest_report"])
        self.assertIn("まだ共有していない", result["awaiting"])
        self.assertTrue(any(e["type"] == "answer_review" and e["supported"]
                            for e in result["events"]))
        self.assertTrue(any(e["type"] == "goal_gate" and
                            e["status"] == "waiting_user"
                            for e in result["events"]))
        self.assertEqual(secretary_core.load_state(
            result["task_id"], self.folder)["status"], "waiting_user")

    def test_reopen_older_answered_continuation_keeps_same_id_and_budget(self):
        # Previously answered tasks were locked even though their explicit
        # original objective remained incomplete. The new GUI reopen is
        # an intentional user interaction; past events must remain visible.
        task_id = "6b5fe50f-b9d3-4689-a1c7-2c1f1a43bc04"
        request = (
            "過去の経験を調べて、必要な情報を質問し、調査を続けてください。"
        )
        previous_events = [
            {"type": "dispatch", "to": "research",
             "signature": ["research", "old query"]}
            for _ in range(secretary_core.MAX_DELEGATIONS)
        ]
        state = {
            "task_id": task_id, "original_request": request,
            "target": "架空テストPC", "domain": "pc",
            "mode": "fixture", "model": "qwen3:8b",
            "status": "answered", "turns": 9, "review_attempts": 2,
            "answer": "元の調査を完了せず途中で回答済みにした",
            "awaiting": "もう少し状況を教えてください",
            "feedback": "", "events": previous_events,
            "observations": [{
                "specialist": "memory", "source_mode": "fixture",
                "target": "架空テストPC",
                "records": [{"id": "M1", "text": "ram_gb=16",
                             "verification": "unverified"}],
            }],
        }
        secretary_core.save_state(state, self.folder)
        script = ScriptedModel([{
            "action": "ask_user", "response": "まだ未共有のログはありますか？",
            "reason": "未解決の原因分析を継続",
        }])
        with patch.object(secretary_core, "_json_llm", side_effect=script):
            result = self.core.reopen(task_id, "今回は新しいログがありません")
        self.assertEqual(result["status"], "waiting_user")
        self.assertEqual(result["task_id"], task_id)
        self.assertEqual(result["latest_report"],
                         "元の調査を完了せず途中で回答済みにした")
        self.assertEqual(result["awaiting"], "まだ未共有のログはありますか？")
        self.assertEqual(result["turns"], 1)
        self.assertEqual(result["session_event_start"], len(previous_events))
        saved = secretary_core.load_state(task_id, self.folder)
        self.assertEqual(saved["session_event_start"], len(previous_events))
        self.assertEqual(saved["latest_report"], result["latest_report"])
        self.assertEqual(result["review_attempts"], 0)
        self.assertEqual(sum(e.get("type") == "dispatch"
                             for e in result["events"]),
                         secretary_core.MAX_DELEGATIONS)
        self.assertTrue(any(e["type"] == "task_reopened"
                            for e in result["events"]))
        self.assertEqual(script.manager_contexts[0]["new_user_information"],
                         "今回は新しいログがありません")

    def test_one_off_question_still_finishes_and_cannot_reopen(self):
        result = self.start(
            ScriptedModel([answer("現時点の証拠では未特定です。")]),
            question="現状だけで何が分かるか教えてください",
        )
        self.assertEqual(result["status"], "answered")
        with self.assertRaisesRegex(ValueError, "ongoing intent"):
            self.core.reopen(result["task_id"])



if __name__ == "__main__":
    unittest.main()
