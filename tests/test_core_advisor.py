from __future__ import annotations

import unittest
from unittest.mock import patch
from urllib.error import URLError

from pkb_proto.core_advisor import advise, choose_model, inspect_output


class CoreAdvisorTests(unittest.TestCase):
    def test_choose_model_prefers_llama_before_qwen(self):
        self.assertEqual(
            choose_model(["qwen3.5:9b", "llama3.1:8b"]),
            "llama3.1:8b",
        )

    def test_valid_matching_proposal(self):
        result = inspect_output(
            {
                "situation": "PKB現在値とWeb最新値の比較が必要です。",
                "missing_information": [],
                "proposed_action": "pkb_web_compare",
                "reason": "現在値と公開情報の両方が必要です。",
                "expected_result": "両者の比較結果",
            },
            {"pkb_search", "web_research", "pkb_web_compare"},
            "pkb_web_compare",
        )
        self.assertEqual(result.status, "ok")
        self.assertEqual(result.comparison, "match")
        self.assertEqual(result.proposed_action, "pkb_web_compare")

    def test_valid_mismatch_is_observed_not_rejected(self):
        result = inspect_output(
            {
                "situation": "Webだけでよいと判断しました。",
                "missing_information": [],
                "proposed_action": "web_research",
                "reason": "公開情報が必要です。",
                "expected_result": "Web調査結果",
            },
            {"pkb_search", "web_research", "pkb_web_compare"},
            "pkb_web_compare",
        )
        self.assertEqual(result.status, "ok")
        self.assertEqual(result.comparison, "mismatch")
        self.assertEqual(result.proposed_action, "web_research")

    def test_invalid_json_is_fail_closed(self):
        result = inspect_output(
            "not json",
            {"pkb_search", "web_research"},
            "pkb_search",
        )
        self.assertEqual(result.status, "invalid")
        self.assertEqual(result.comparison, "invalid")
        self.assertEqual(result.error, "invalid_json")

    def test_unknown_capability_is_invalid(self):
        result = inspect_output(
            {
                "situation": "何か実行します。",
                "missing_information": [],
                "proposed_action": "delete_everything",
                "reason": "test",
                "expected_result": "test",
            },
            {"pkb_search", "web_research"},
            "pkb_search",
        )
        self.assertEqual(result.status, "invalid")
        self.assertEqual(result.error, "unknown_capability")

    def test_no_proposal_can_match_clarification_state(self):
        result = inspect_output(
            {
                "situation": "対象が不足しています。",
                "missing_information": ["対象GPU"],
                "proposed_action": None,
                "reason": "対象を特定できません。",
                "expected_result": "追加情報",
            },
            {"pkb_search", "web_research"},
            None,
        )
        self.assertEqual(result.status, "ok")
        self.assertEqual(result.comparison, "match")
        self.assertIsNone(result.proposed_action)

    @patch("pkb_proto.core_advisor._chat_models", return_value=["llama3.1:8b"])
    @patch("pkb_proto.core_advisor.urlopen", side_effect=URLError("offline"))
    def test_provider_error_never_raises_into_core(self, _urlopen, _models):
        result = advise(
            "メインPCのGPUを調べて",
            current_selection="pkb_search",
            deterministic_status="ready",
            timeout=0.1,
        )
        self.assertEqual(result.status, "unavailable")
        self.assertEqual(result.comparison, "unavailable")
        self.assertEqual(result.error, "URLError")


if __name__ == "__main__":
    unittest.main()
