from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from integrations.connections.credential_resolver import (
    CredentialResolutionError,
    env_name_to_credential_ref,
    register_connection_credential_loader,
    resolve_connection_credential,
    resolve_credential,
)
from integrations.connections.service_connections import (
    CONNECTION_TYPES,
    LLM_INFERENCE,
    SERVICE_BILLING_READ,
    adapter_defaults,
    connection_adapter_keys,
    normalize_capabilities,
    normalize_connection_type,
)


class ServiceConnectionContractTests(unittest.TestCase):
    def tearDown(self):
        register_connection_credential_loader(None)

    def test_adapter_defaults_include_connection_type(self):
        self.assertEqual(adapter_defaults("openai")["connection_type"], "api_key")
        self.assertEqual(adapter_defaults("gemini")["connection_type"], "api_key")
        self.assertEqual(adapter_defaults("ollama")["connection_type"], "none")
        self.assertEqual(adapter_defaults("google_cloud")["connection_type"], "external_credentials")
        self.assertIn("google_cloud", connection_adapter_keys())
        self.assertEqual(
            adapter_defaults("google_cloud")["capabilities"],
            [SERVICE_BILLING_READ],
        )
        self.assertEqual(
            adapter_defaults("openai")["capabilities"],
            [LLM_INFERENCE],
        )

    def test_connection_types_are_bounded(self):
        self.assertEqual(
            tuple(CONNECTION_TYPES),
            ("none", "api_key", "username_password", "oauth2", "external_credentials"),
        )
        self.assertEqual(normalize_connection_type("api_key"), "api_key")
        with self.assertRaisesRegex(ValueError, "invalid_connection_type"):
            normalize_connection_type("mystery")

    def test_capabilities_are_deduplicated_and_not_permissions(self):
        self.assertEqual(
            normalize_capabilities([
                LLM_INFERENCE,
                SERVICE_BILLING_READ,
                LLM_INFERENCE,
            ]),
            (LLM_INFERENCE, SERVICE_BILLING_READ),
        )

    def test_env_credential_reference_is_bootstrap_fallback(self):
        ref = env_name_to_credential_ref("OPENAI_API_KEY")
        self.assertEqual(ref, "env:OPENAI_API_KEY")
        with patch.dict(
            os.environ,
            {"OPENAI_API_KEY": "secret-value"},
            clear=True,
        ):
            self.assertEqual(resolve_credential(ref), "secret-value")
        self.assertNotIn("secret-value", ref)

    def test_connection_credential_loader_wins_over_env_fallback(self):
        register_connection_credential_loader(
            lambda connection_id: (
                "db-secret" if connection_id == "connection-1" else None
            )
        )
        with patch.dict(
            os.environ,
            {"OPENAI_API_KEY": "env-secret"},
            clear=True,
        ):
            self.assertEqual(
                resolve_connection_credential(
                    "connection-1",
                    "env:OPENAI_API_KEY",
                ),
                "db-secret",
            )

    def test_connection_credential_falls_back_to_env_during_migration(self):
        register_connection_credential_loader(lambda _connection_id: None)
        with patch.dict(
            os.environ,
            {"OPENAI_API_KEY": "env-secret"},
            clear=True,
        ):
            self.assertEqual(
                resolve_connection_credential(
                    "connection-1",
                    "env:OPENAI_API_KEY",
                ),
                "env-secret",
            )

    def test_missing_credential_is_explicit_without_secret(self):
        register_connection_credential_loader(lambda _connection_id: None)
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(
                CredentialResolutionError,
                "connection credential is not configured",
            ):
                resolve_connection_credential(
                    "connection-1",
                    "env:OPENAI_API_KEY",
                )

    def test_invalid_credential_reference_is_rejected(self):
        with self.assertRaisesRegex(
            ValueError,
            "unsupported_credential_ref",
        ):
            resolve_credential("plain:secret")


if __name__ == "__main__":
    unittest.main()
