from __future__ import annotations

import os
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from pkb_proto.provider_usage import (
    ProviderUsageError,
    read_openai_month_usage,
)
from pkb_proto.service_connections import PROVIDER_USAGE_READ


def openai_connection(
    credential_ref: str = "env:OPENAI_ADMIN_KEY",
) -> dict:
    return {
        "id": "11111111-1111-1111-1111-111111111111",
        "display_name": "OpenAI Usage / Costs",
        "adapter_key": "openai",
        "endpoint": "https://api.openai.com/v1",
        "credential_ref": credential_ref,
        "capabilities": [PROVIDER_USAGE_READ],
        "enabled": True,
    }


class ProviderUsageTests(unittest.TestCase):
    def test_missing_key_is_explicit(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(
                ProviderUsageError,
                "connection credential",
            ):
                read_openai_month_usage(
                    openai_connection(),
                    datetime(2026, 10, 2, tzinfo=timezone.utc),
                )

    @patch("pkb_proto.provider_usage._paged")
    def test_openai_usage_is_normalized(self, paged):
        paged.side_effect = [
            [{
                "results": [
                    {
                        "model": "gpt-a",
                        "input_tokens": 100,
                        "output_tokens": 20,
                        "num_model_requests": 2,
                    },
                    {
                        "model": "gpt-b",
                        "input_tokens": 50,
                        "output_tokens": 30,
                        "num_model_requests": 1,
                    },
                ]
            }],
            [{
                "results": [{
                    "amount": {
                        "currency": "usd",
                        "value": 1.25,
                    }
                }]
            }],
        ]
        with patch.dict(
            os.environ,
            {"OPENAI_ADMIN_KEY": "secret-test-key"},
            clear=True,
        ):
            result = read_openai_month_usage(
                openai_connection(),
                datetime(2026, 10, 2, 3, 4, tzinfo=timezone.utc),
            )
        self.assertEqual(
            result["usage"]["totals"],
            {
                "input_tokens": 150,
                "output_tokens": 50,
                "requests": 3,
            },
        )
        self.assertEqual(
            result["costs"],
            {
                "status": "known",
                "values": {"usd": 1.25},
            },
        )
        self.assertEqual(
            result["credential_source"],
            "service_connection",
        )
        self.assertEqual(
            result["usage"]["by_model"][0]["model"],
            "gpt-a",
        )
        self.assertEqual(result["limits"]["status"], "unknown")
        self.assertEqual(result["credits"]["status"], "unknown")
        self.assertNotIn("secret-test-key", str(result))

    @patch("pkb_proto.provider_usage._paged")
    def test_api_key_connection_is_explicit_not_implicit_fallback(self, paged):
        paged.side_effect = [[], []]
        with patch.dict(
            os.environ,
            {"OPENAI_API_KEY": "fallback-secret"},
            clear=True,
        ):
            result = read_openai_month_usage(
                openai_connection("env:OPENAI_API_KEY"),
                datetime(2026, 10, 2, tzinfo=timezone.utc),
            )
        self.assertEqual(
            result["credential_source"],
            "service_connection",
        )
        self.assertNotIn("fallback-secret", str(result))

    def test_connection_without_usage_capability_is_rejected(self):
        connection = openai_connection()
        connection["capabilities"] = ["llm_inference"]
        with self.assertRaisesRegex(
            ProviderUsageError,
            "provider_usage_read",
        ):
            read_openai_month_usage(
                connection,
                datetime(2026, 10, 2, tzinfo=timezone.utc),
            )


if __name__ == "__main__":
    unittest.main()
