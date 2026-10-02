from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from pkb_proto.credential_resolver import (
    CredentialResolutionError,
    env_name_to_credential_ref,
    resolve_credential,
)
from pkb_proto.service_connections import (
    LLM_INFERENCE,
    PROVIDER_USAGE_READ,
    adapter_defaults,
    ensure_llm_connection,
    normalize_capabilities,
)


class ServiceConnectionContractTests(unittest.TestCase):
    def test_defaults_keep_connection_and_llm_semantics_separate(self):
        self.assertEqual(
            adapter_defaults("openai")["credential_ref"],
            "env:OPENAI_API_KEY",
        )
        self.assertEqual(
            adapter_defaults("gemini")["credential_ref"],
            "env:GEMINI_API_KEY",
        )
        self.assertIsNone(adapter_defaults("ollama")["credential_ref"])
        self.assertEqual(
            adapter_defaults("openai")["capabilities"],
            [LLM_INFERENCE],
        )

    def test_capabilities_are_deduplicated_and_not_permissions(self):
        self.assertEqual(
            normalize_capabilities([
                LLM_INFERENCE,
                PROVIDER_USAGE_READ,
                LLM_INFERENCE,
            ]),
            (LLM_INFERENCE, PROVIDER_USAGE_READ),
        )

    def test_env_credential_reference_never_contains_secret(self):
        ref = env_name_to_credential_ref("OPENAI_API_KEY")
        self.assertEqual(ref, "env:OPENAI_API_KEY")
        with patch.dict(
            os.environ,
            {"OPENAI_API_KEY": "secret-value"},
            clear=True,
        ):
            self.assertEqual(resolve_credential(ref), "secret-value")
        self.assertNotIn("secret-value", ref)

    def test_missing_credential_is_explicit_without_secret(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(
                CredentialResolutionError,
                "env:OPENAI_API_KEY",
            ):
                resolve_credential("env:OPENAI_API_KEY")

    def test_reusing_connection_does_not_reenable_disabled_connection(self):
        connection_id = "22222222-2222-2222-2222-222222222222"

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
                    connection_id,
                    [LLM_INFERENCE],
                    "OpenAI primary",
                    False,
                    {},
                )

        class DB:
            def __init__(self):
                self.cur = Cursor()

            def cursor(self):
                return self.cur

        db = DB()
        saved = ensure_llm_connection(
            db,
            provider="openai",
            endpoint="https://api.openai.com/v1",
            credential_env="OPENAI_API_KEY",
        )
        self.assertFalse(saved["enabled"])
        update_sql, update_params = db.cur.calls[-1]
        self.assertIn("UPDATE secretary.service_connections", update_sql)
        self.assertFalse(update_params[7])

    def test_invalid_credential_reference_is_rejected(self):
        with self.assertRaisesRegex(
            ValueError,
            "unsupported_credential_ref",
        ):
            resolve_credential("plain:secret")


if __name__ == "__main__":
    unittest.main()
