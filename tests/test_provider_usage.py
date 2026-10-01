from __future__ import annotations

import os
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from pkb_proto.provider_usage import ProviderUsageError, read_openai_month_usage


class ProviderUsageTests(unittest.TestCase):
    def test_missing_key_is_explicit(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(ProviderUsageError, "APIキー"):
                read_openai_month_usage(datetime(2026, 10, 2, tzinfo=timezone.utc))

    @patch("pkb_proto.provider_usage._paged")
    def test_openai_usage_is_normalized(self, paged):
        paged.side_effect = [
            [{
                "results": [
                    {"model": "gpt-a", "input_tokens": 100, "output_tokens": 20, "num_model_requests": 2},
                    {"model": "gpt-b", "input_tokens": 50, "output_tokens": 30, "num_model_requests": 1},
                ]
            }],
            [{"results": [{"amount": {"currency": "usd", "value": 1.25}}]}],
        ]
        with patch.dict(os.environ, {"OPENAI_ADMIN_KEY": "secret-test-key"}, clear=True):
            result = read_openai_month_usage(datetime(2026, 10, 2, 3, 4, tzinfo=timezone.utc))
        self.assertEqual(result["totals"], {"input_tokens": 150, "output_tokens": 50, "requests": 3})
        self.assertEqual(result["costs"], {"usd": 1.25})
        self.assertEqual(result["credential_env"], "OPENAI_ADMIN_KEY")
        self.assertEqual(result["by_model"][0]["model"], "gpt-a")
        self.assertNotIn("secret-test-key", str(result))

    @patch("pkb_proto.provider_usage._paged")
    def test_existing_api_key_is_fallback(self, paged):
        paged.side_effect = [[], []]
        with patch.dict(os.environ, {"OPENAI_API_KEY": "fallback-secret"}, clear=True):
            result = read_openai_month_usage(datetime(2026, 10, 2, tzinfo=timezone.utc))
        self.assertEqual(result["credential_env"], "OPENAI_API_KEY")
        self.assertNotIn("fallback-secret", str(result))


if __name__ == "__main__":
    unittest.main()
