from __future__ import annotations

import os
import unittest
from uuid import UUID
from unittest.mock import patch

from pkb_proto.magi_settings import (
    DEFAULT_RETRY_HTTP_CODES,
    DEFAULT_RETRY_WITHIN_TURN,
    DEFAULT_TIMEOUT_SECONDS,
    MEMBER_NAMES,
    fallback_member_specs,
    normalize_retry_http_codes,
    provider_defaults,
    save_member_assignments,
    upsert_llm_profile,
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
        self.assertTrue(all(
            item["retry_within_turn"] is DEFAULT_RETRY_WITHIN_TURN
            for item in specs
        ))
        self.assertTrue(all(
            item["retry_http_codes"] == list(DEFAULT_RETRY_HTTP_CODES)
            for item in specs
        ))

    def test_retry_http_codes_are_configurable_and_validated(self):
        self.assertEqual(
            normalize_retry_http_codes("503,429,503,504"),
            (503, 429, 504),
        )
        self.assertEqual(normalize_retry_http_codes(""), ())
        with self.assertRaisesRegex(ValueError, "invalid_retry_http_codes"):
            normalize_retry_http_codes("200,503")

    def test_existing_profile_can_be_updated_by_id(self):
        profile_id = UUID("11111111-1111-1111-1111-111111111111")
        connection_id = UUID("22222222-2222-2222-2222-222222222222")

        class Cursor:
            def __init__(self):
                self.calls = []

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def execute(self, sql, params):
                self.calls.append((sql, params))

            def fetchone(self):
                return (
                    profile_id,
                    65536,
                    4096,
                    list(DEFAULT_RETRY_HTTP_CODES),
                )

        class DB:
            def __init__(self):
                self.cur = Cursor()

            def cursor(self):
                return self.cur

        db = DB()
        connection = {
            "id": str(connection_id),
            "display_name": "ollama / LLM",
            "adapter_key": "ollama",
            "endpoint": "http://127.0.0.1:11434",
            "credential_ref": None,
            "enabled": True,
        }
        with patch(
            "pkb_proto.magi_settings.ensure_llm_connection",
            return_value=connection,
        ):
            saved = upsert_llm_profile(
                db,
                provider="ollama",
                model="qwen3.5:9b",
                display_name="Ollama / qwen3.5:9b",
                endpoint="http://127.0.0.1:11434",
                context_window_tokens=65536,
                ollama_num_predict=8192,
                retry_http_codes="429,503",
                profile_id=str(profile_id),
            )
        self.assertEqual(saved["id"], str(profile_id))
        self.assertEqual(saved["connection_id"], str(connection_id))
        self.assertEqual(saved["ollama_num_predict"], 8192)
        self.assertEqual(saved["retry_http_codes"], [429, 503])
        update_sql, update_params = db.cur.calls[-1]
        self.assertIn("UPDATE secretary.llm_profiles", update_sql)
        self.assertEqual(update_params[-1], profile_id)
        self.assertEqual(update_params[4], 8192)
        self.assertEqual(update_params[5], [429, 503])

    def test_member_retry_toggle_is_persisted(self):
        class Context:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

        class Cursor(Context):
            def __init__(self):
                self.calls = []
                self.last_sql = ""

            def execute(self, sql, params):
                self.last_sql = sql
                self.calls.append((sql, params))

            def fetchone(self):
                return (True,)

        class DB:
            def __init__(self):
                self.cur = Cursor()

            def cursor(self):
                return self.cur

            def transaction(self):
                return Context()

        db = DB()
        assignments = [
            {
                "name": name,
                "profile_id": f"11111111-1111-1111-1111-11111111111{index}",
                "enabled": True,
                "weight": 1.0,
                "timeout_seconds": 120,
                "retry_within_turn": name != "BALTHASAR",
            }
            for index, name in enumerate(MEMBER_NAMES, start=1)
        ]
        with patch(
            "pkb_proto.magi_settings.load_member_specs",
            return_value=assignments,
        ):
            save_member_assignments(db, assignments)

        writes = [
            params for sql, params in db.cur.calls
            if "INSERT INTO secretary.magi_member_assignments" in sql
        ]
        self.assertEqual(len(writes), 3)
        self.assertEqual([params[-1] for params in writes], [True, True, False])

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
