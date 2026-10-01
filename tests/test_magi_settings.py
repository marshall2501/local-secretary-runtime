from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from pkb_proto.magi_settings import (
    DEFAULT_TIMEOUT_SECONDS,
    MEMBER_NAMES,
    fallback_member_specs,
    provider_defaults,
    save_member_assignments,
)
from pkb_proto.ollama_runtime import (
    DEFAULT_MAGI_OLLAMA_NUM_PREDICT,
    DEFAULT_OLLAMA_CONTEXT_TOKENS,
)


class MagiSettingsTests(unittest.TestCase):
    def test_member_labels_do_not_imply_provider(self):
        env = {
            "LSA_MAGI_CLOUD_ENABLED": "1",
            "LSA_MAGI_MELCHIOR_PROVIDER": "openai",
            "LSA_MAGI_MELCHIOR_MODEL": "cloud-a",
            "LSA_MAGI_MELCHIOR_ENABLED": "1",
            "LSA_MAGI_CASPER_PROVIDER": "ollama",
            "LSA_MAGI_CASPER_MODEL": "local-b",
            "LSA_MAGI_CASPER_ENABLED": "1",
            "LSA_MAGI_BALTHASAR_PROVIDER": "gemini",
            "LSA_MAGI_BALTHASAR_MODEL": "cloud-c",
            "LSA_MAGI_BALTHASAR_ENABLED": "1",
        }
        with patch.dict(os.environ, env, clear=True):
            specs = fallback_member_specs("unused-local-default")
        by_name = {item["name"]: item for item in specs}
        self.assertEqual(by_name["MELCHIOR"]["provider"], "openai")
        self.assertEqual(by_name["CASPER"]["provider"], "ollama")
        self.assertEqual(by_name["BALTHASAR"]["provider"], "gemini")
        self.assertTrue(all(item["enabled"] for item in specs))

    def test_fallback_keeps_current_legacy_bootstrap_compatible(self):
        env = {
            "LSA_MAGI_CLOUD_ENABLED": "1",
            "LSA_MAGI_CASPER_MODEL": "gpt-test",
            "LSA_MAGI_CASPER_ENABLED": "1",
            "LSA_MAGI_BALTHASAR_ENABLED": "0",
        }
        with patch.dict(os.environ, env, clear=True):
            specs = fallback_member_specs("gemma3:12b")
        self.assertEqual([item["name"] for item in specs], list(MEMBER_NAMES))
        self.assertEqual(specs[0]["provider"], "ollama")
        self.assertEqual(specs[0]["model"], "gemma3:12b")
        self.assertEqual(specs[1]["provider"], "openai")
        self.assertEqual(specs[1]["model"], "gpt-test")
        self.assertTrue(specs[1]["enabled"])
        self.assertFalse(specs[2]["enabled"])

    def test_provider_defaults_have_separate_credential_aliases(self):
        self.assertIsNone(provider_defaults("ollama")[1])
        self.assertEqual(provider_defaults("openai")[1], "OPENAI_API_KEY")
        self.assertEqual(provider_defaults("gemini")[1], "GEMINI_API_KEY")

    def test_default_ollama_context_is_64k_and_cloud_has_no_context(self):
        with patch.dict(os.environ, {}, clear=True):
            specs = fallback_member_specs("gemma3:12b")
        by_name = {item["name"]: item for item in specs}
        self.assertEqual(DEFAULT_OLLAMA_CONTEXT_TOKENS, 65536)
        self.assertEqual(DEFAULT_MAGI_OLLAMA_NUM_PREDICT, 4096)
        self.assertEqual(by_name["MELCHIOR"]["context_window_tokens"], 65536)
        self.assertEqual(by_name["MELCHIOR"]["ollama_num_predict"], 4096)
        self.assertIsNone(by_name["CASPER"]["context_window_tokens"])
        self.assertIsNone(by_name["CASPER"]["ollama_num_predict"])
        self.assertIsNone(by_name["BALTHASAR"]["context_window_tokens"])
        self.assertIsNone(by_name["BALTHASAR"]["ollama_num_predict"])

    def test_ollama_context_env_override_is_used_for_bootstrap(self):
        env = {
            "LSA_MAGI_MELCHIOR_CONTEXT_TOKENS": "32768",
        }
        with patch.dict(os.environ, env, clear=True):
            specs = fallback_member_specs("qwen3.5:9b")
        self.assertEqual(specs[0]["context_window_tokens"], 32768)

    def test_default_member_timeout_is_120_seconds(self):
        with patch.dict(os.environ, {}, clear=True):
            specs = fallback_member_specs("gemma3:12b")
        self.assertEqual(DEFAULT_TIMEOUT_SECONDS, 120)
        self.assertTrue(all(item["timeout_seconds"] == 120 for item in specs))

    def test_assignment_validation_requires_all_three_slots(self):
        with self.assertRaisesRegex(ValueError, "all_magi_members_required"):
            save_member_assignments(
                None,
                [{
                    "name": "MELCHIOR",
                    "profile_id": "11111111-1111-1111-1111-111111111111",
                    "enabled": True,
                    "weight": 1.0,
                    "timeout_seconds": 900,
                }],
            )

    def test_assignment_validation_requires_one_enabled_slot(self):
        items = [
            {
                "name": name,
                "profile_id": f"11111111-1111-1111-1111-11111111111{index}",
                "enabled": False,
                "weight": 1.0,
                "timeout_seconds": 900,
            }
            for index, name in enumerate(MEMBER_NAMES, start=1)
        ]
        with self.assertRaisesRegex(ValueError, "at_least_one_member_required"):
            save_member_assignments(None, items)


if __name__ == "__main__":
    unittest.main()
