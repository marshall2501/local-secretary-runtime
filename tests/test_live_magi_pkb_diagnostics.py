# Provenance-Origin: original
# Provenance-AI: AI-assisted
"""使い捨て実MAGI試験の診断がモデル本文を出力しないことを確認する。"""
from __future__ import annotations

from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
import runpy
import unittest


DIAGNOSTICS = runpy.run_path(
    str(Path(__file__).resolve().parent / "db" / "verify-pkb-claim-e2e.py")
)
DIAGNOSE = DIAGNOSTICS["report_live_goal_diagnostics"]
TARGET_INTEGRITY = DIAGNOSTICS["live_write_target_integrity"]


class LivePkbDiagnosticsTests(unittest.TestCase):
    """失敗の分析に必要な列挙値だけを標準出力へ残す。"""

    def test_decision_enums_and_counts_without_model_text(self):
        """Turnごとの操作・状態を数で表示し、機密文字列は出さない。"""
        session = {
            "status": "waiting_user", "user_raw": "PRIVATE_USER_TEXT",
            "turns": [
                {"status": "ok", "response": {
                    "next": "invoke",
                    "operation_request": {
                        "operation_id": "pkb.search",
                        "arguments": {"query": "PRIVATE_QUERY"},
                    },
                    "answer": "PRIVATE_ANSWER",
                }},
                {"status": "ok", "response": {
                    "next": "ask_user", "question": "PRIVATE_QUESTION",
                    "operation_request": None,
                }},
            ],
        }
        buffer = StringIO()
        with redirect_stdout(buffer):
            DIAGNOSE(session, ["search"], [], [1, 2])
        text = buffer.getvalue()
        self.assertIn("diagnostic: goal_turn_count=2", text)
        self.assertIn("diagnostic: pkb_read_count=1", text)
        self.assertIn("diagnostic: pkb_write_count=0", text)
        self.assertIn("next=invoke operation=pkb.search", text)
        self.assertIn("next=ask_user operation=none_or_other", text)
        self.assertNotIn("PRIVATE_", text)

    def test_unverified_uuid_cannot_be_used_as_a_pkb_write_target(self):
        """Valid UUID syntax is insufficient without recorded PKB source evidence."""
        verified_id = "f1c70000-0000-4000-8000-000000000001"
        unknown_id = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"
        session = {"observations": [{
            "source": "pkb", "verified": True,
            "action_id": "action-1", "result_id": "result-1",
            "resolved_entity": {"id": verified_id, "name": "作業用GPU-A"},
        }]}
        self.assertEqual(TARGET_INTEGRITY(session, "not-a-uuid"), (False, False))
        self.assertEqual(TARGET_INTEGRITY(session, unknown_id), (True, False))
        self.assertEqual(TARGET_INTEGRITY(session, verified_id), (True, True))
        session["observations"][0]["verified"] = False
        self.assertEqual(TARGET_INTEGRITY(session, verified_id), (True, False))
        session["observations"][0]["verified"] = True
        session["observations"][0]["result_id"] = None
        self.assertEqual(TARGET_INTEGRITY(session, verified_id), (True, False))
        self.assertEqual(TARGET_INTEGRITY({"observations": []}, verified_id), (True, False))

    def test_request_catalog_and_question_topics_are_metadata_only(self):
        """受け取ったOperation Catalogと質問テーマだけを出す。"""
        session = {
            "status": "waiting_user",
            "user_raw": "PRIVATE_USER_TEXT",
            "turns": [{
                "status": "ok",
                "request_envelope": {
                    "operation_catalog": {
                        "pkb.search": {"arguments": {"query": "PRIVATE_QUERY"}},
                        "pkb.register_claim": {"note": "PRIVATE_CATALOG"},
                    },
                    "user_input": {"raw": "PRIVATE_USER_TEXT"},
                },
                "response": {
                    "next": "ask_user",
                    "question": "対象のEntity IDが不明です。PRIVATE_QUESTION",
                    "reason": "登録先を識別してください PRIVATE_REASON",
                    "operation_request": None,
                },
            }],
        }
        buffer = StringIO()
        with redirect_stdout(buffer):
            DIAGNOSE(session, [], [], [1])
        result = buffer.getvalue()
        self.assertIn("goal_turn_1_pkb_search_offered=true", result)
        self.assertIn("goal_turn_1_pkb_write_offered=true", result)
        self.assertIn("goal_turn_1_ask_topic=entity_identity", result)
        self.assertNotIn("PRIVATE_", result)
        self.assertNotIn("Entity IDが不明", result)

    def test_generic_fictional_target_words_are_not_evidence_of_an_issue(self):
        """『架空』『対象』だけでは本人確認の理由を断定しない。"""
        session = {
            "status": "waiting_user",
            "turns": [{
                "status": "ok", "response": {
                    "next": "ask_user",
                    "question": "架空GPUという対象の記録ですか？",
                    "reason": "架空の対象を説明しています",
                    "operation_request": None,
                },
            }],
        }
        buffer = StringIO()
        with redirect_stdout(buffer):
            DIAGNOSE(session, [], [], [1])
        result = buffer.getvalue()
        self.assertIn("goal_turn_1_ask_topic=unclassified", result)
        self.assertNotIn("架空", result)
        self.assertNotIn("対象", result)

    def test_unexpected_or_invalid_response_never_printed(self):
        """不正な型や未知の識別子も内容を表示せず安全な分類へ落とす。"""
        session = {
            "status": ["PRIVATE_STATUS"],
            "turns": [{"status": ["PRIVATE_RESULT"], "response": {
                "next": ["PRIVATE_NEXT"],
                "operation_request": {"operation_id": ["PRIVATE_OP"]},
                "reason": "PRIVATE_REASON",
            }}],
        }
        buffer = StringIO()
        with redirect_stdout(buffer):
            DIAGNOSE(session, [], [], [1])
        text = buffer.getvalue()
        self.assertIn("diagnostic: goal_status=other", text)
        self.assertIn("status=other next=none_or_other operation=none_or_other", text)
        self.assertNotIn("PRIVATE_", text)


if __name__ == "__main__":
    unittest.main()
