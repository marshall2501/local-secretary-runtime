"""Offline tests for separating model health, input reception and extraction."""
import json
import unittest

from interfaces.workbench.diagnostic_cases import (MODES, EPISODE_UNUSED, build_ollama_payload,
                                        request_for, judge_response)
from pkb.extraction_service import Extraction
from interfaces.workbench.gui_helpers import analyze_reply

EP = {"id": "pc-01",
      "text": "サブPCを架空GPUドライバーDRV-A1へ更新した。直後はゲームが軽くなった。",
      "domain": "pc", "source_kind": "user_statement"}


class DiagnosticCasesTests(unittest.TestCase):
    def test_all_modes_generate_messages_with_correct_format(self):
        self.assertEqual(len(MODES), 5)
        for mode in MODES:
            messages, as_json = request_for(EP, mode)
            self.assertEqual([m["role"] for m in messages], ["system", "user"])
            self.assertEqual(as_json, mode.startswith("抽出："))

    def test_echo_and_reasoning_do_not_receive_episode_text(self):
        for mode in ("疎通：固定文字列", "推論：簡単な計算"):
            messages, _ = request_for(EP, mode)
            self.assertNotIn(EP["text"], str(messages))

    def test_input_reception_receives_original_text(self):
        messages, _ = request_for(EP, "理解：原文復唱")
        self.assertEqual(messages[1]["content"], EP["text"])
        self.assertEqual(judge_response("理解：原文復唱", EP, EP["text"]), "原文完全一致")

    def test_basic_and_reasoning_checks_are_strict(self):
        self.assertEqual(judge_response("疎通：固定文字列", EP, "ABC123"), "固定文字列一致")
        self.assertEqual(judge_response("推論：簡単な計算", EP, "5"), "正答（5）")
        self.assertEqual(judge_response("推論：簡単な計算", EP, ""), "回答本文なし")

    def test_simplified_json_does_not_claim_semantic_accuracy(self):
        result = judge_response("抽出：簡略", EP, '{"entity":"サブPC","event":"更新"}')
        self.assertEqual(result, "簡略JSON構造OK・意味未検証")
        self.assertEqual(judge_response("抽出：簡略", EP, '{"x":1}'), "簡略JSONスキーマ不一致")

    def test_plain_text_does_not_report_invalid_json(self):
        result = analyze_reply(
            EP, {"message": {"content": "ABC123"}, "done_reason": "stop"},
            Extraction(EP["id"], (), ()), expect_json=False,
        )
        self.assertEqual(result["json_parse"], "not_requested")
        self.assertEqual(result["content_preview"], "ABC123")

    def test_basic_modes_ignore_episode_and_send_plain_text_request(self):
        other = {**EP, "text": "変更しても送信されない架空文章"}
        for mode in EPISODE_UNUSED:
            first = build_ollama_payload(EP, "qwen3.5:9b", 1100, "自動", mode)
            second = build_ollama_payload(other, "qwen3.5:9b", 1100, "自動", mode)
            self.assertEqual(first, second)
            self.assertNotIn("format", first)
            self.assertNotIn("think", first)
            self.assertNotIn(EP["text"], json.dumps(first, ensure_ascii=False))

    def test_actual_payload_preview_and_disabled_reasoning(self):
        payload = build_ollama_payload(
            EP, "qwen3.5:9b", 1100, "無効", "抽出：現行"
        )
        self.assertEqual(payload["format"], "json")
        self.assertIs(payload["think"], False)
        self.assertEqual(payload["options"]["num_predict"], 1100)
        self.assertEqual(payload["options"]["num_ctx"], 65536)
        self.assertIn(EP["text"], payload["messages"][1]["content"])
        self.assertNotIn("expected", json.dumps(payload))

    def test_invalid_mode_rejected(self):
        with self.assertRaises(ValueError):
            request_for(EP, "web_or_private_data")


if __name__ == "__main__":
    unittest.main()
