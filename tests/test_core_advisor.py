from __future__ import annotations

import unittest
from unittest.mock import patch
from urllib.error import URLError

from pkb_proto.core_advisor import AdvisorResult, advise, choose_model, diagnose_response, inspect_output
from pkb_proto.daily_pkb import _run_core_advisor_shadow


class CoreAdvisorTests(unittest.TestCase):
    def test_choose_model_prefers_llama_before_qwen(self):
        self.assertEqual(
            choose_model(["qwen3.5:9b", "llama3.1:8b"]),
            "llama3.1:8b",
        )

    def test_choose_model_honors_runtime_selection(self):
        self.assertEqual(
            choose_model(["llama3.1:8b", "gemma4:12b"], "gemma4:12b"),
            "gemma4:12b",
        )

    def test_choose_model_rejects_uninstalled_runtime_selection(self):
        with self.assertRaises(ValueError):
            choose_model(["llama3.1:8b"], "gemma4:12b")

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

    def test_response_diagnostic_keeps_only_contract_fields(self):
        diagnostic = diagnose_response(
            '{"situation":"ok","missing_information":[],"proposed_action":"pkb_search",'
            '"reason":"use PKB","expected_result":null,"private_reasoning":"do not keep"}'
        )
        self.assertTrue(diagnostic["json_valid"])
        self.assertEqual(
            diagnostic["safe_response"]["expected_result"],
            None,
        )
        self.assertEqual(
            diagnostic["field_types"]["expected_result"],
            "NoneType",
        )
        self.assertEqual(
            diagnostic["unexpected_keys"],
            ["private_reasoning"],
        )
        self.assertNotIn(
            "private_reasoning",
            diagnostic["safe_response"],
        )

    def test_response_diagnostic_does_not_store_non_json_prose(self):
        diagnostic = diagnose_response("thinking aloud and then maybe JSON")
        self.assertFalse(diagnostic["json_valid"])
        self.assertIsNone(diagnostic["safe_response"])
        self.assertEqual(diagnostic["raw_length"], 34)

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

    @patch("pkb_proto.daily_pkb._write_core_advisor_shadow", return_value=True)
    @patch("pkb_proto.daily_pkb.advise_core")
    @patch("pkb_proto.daily_pkb.choose_advisor_model", return_value="gemma3:12b")
    @patch("pkb_proto.daily_pkb.list_advisor_models", return_value=["gemma3:12b"])
    def test_async_worker_records_running_then_completed(
        self, _models, _choose, advise_mock, write_mock
    ):
        advise_mock.return_value = AdvisorResult(
            "ok",
            "match",
            model="gemma3:12b",
            situation="PKB lookup is sufficient.",
            proposed_action="pkb_search",
            reason="Use local PKB.",
            expected_result="Current driver",
            timeout_seconds=600,
        )
        from uuid import UUID
        task_id = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
        _run_core_advisor_shadow(
            task_id,
            "メインPCのGPUの現在のドライバーを調べて",
            "pkb_search",
            "ready",
            "gemma3:12b",
            600,
        )
        self.assertEqual(write_mock.call_count, 2)
        running = write_mock.call_args_list[0].args[1]
        final = write_mock.call_args_list[1].args[1]
        self.assertEqual(running["job_status"], "running")
        self.assertEqual(running["model"], "gemma3:12b")
        self.assertEqual(final["job_status"], "completed")
        self.assertEqual(final["comparison"], "match")
        self.assertEqual(final["model"], "gemma3:12b")
        self.assertEqual(final["timeout_seconds"], 600)
        self.assertIsNotNone(final["elapsed_seconds"])

    @patch("pkb_proto.daily_pkb._write_core_advisor_shadow", return_value=True)
    @patch("pkb_proto.daily_pkb.advise_core")
    @patch("pkb_proto.daily_pkb.choose_advisor_model", return_value="gemma3:12b")
    @patch("pkb_proto.daily_pkb.list_advisor_models", return_value=["gemma3:12b"])
    def test_async_worker_preserves_timeout_metadata(
        self, _models, _choose, advise_mock, write_mock
    ):
        advise_mock.return_value = AdvisorResult(
            "unavailable",
            "unavailable",
            model="gemma3:12b",
            error="TimeoutError",
            timeout_seconds=900,
        )
        from uuid import UUID
        _run_core_advisor_shadow(
            UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"),
            "test",
            "pkb_search",
            "ready",
            "gemma3:12b",
            900,
        )
        final = write_mock.call_args_list[-1].args[1]
        self.assertEqual(final["job_status"], "timeout")
        self.assertEqual(final["model"], "gemma3:12b")
        self.assertEqual(final["timeout_seconds"], 900)
        self.assertEqual(final["error"], "TimeoutError")

    @patch("pkb_proto.core_advisor.list_chat_models", return_value=["llama3.1:8b"])
    @patch("pkb_proto.core_advisor.urlopen", side_effect=URLError("offline"))
    def test_provider_error_never_raises_into_core(self, _urlopen, _models):
        result = advise(
            "メインPCのGPUを調べて",
            current_selection="pkb_search",
            deterministic_status="ready",
            timeout=0.1,
            model="llama3.1:8b",
        )
        self.assertEqual(result.status, "unavailable")
        self.assertEqual(result.comparison, "unavailable")
        self.assertEqual(result.model, "llama3.1:8b")
        self.assertEqual(result.timeout_seconds, 0.1)
        self.assertEqual(result.error, "URLError")


if __name__ == "__main__":
    unittest.main()
