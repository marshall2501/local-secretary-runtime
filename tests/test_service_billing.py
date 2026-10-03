from __future__ import annotations

import os
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from capabilities.service_billing.service import (
    ServiceBillingError,
    read_google_cloud_month_billing,
    read_openai_month_billing,
)
from integrations.connections.service_connections import SERVICE_BILLING_READ


def openai_connection(credential_ref: str = "env:OPENAI_ADMIN_KEY") -> dict:
    return {
        "id": "11111111-1111-1111-1111-111111111111",
        "display_name": "OpenAI Billing",
        "adapter_key": "openai",
        "endpoint": "https://api.openai.com/v1",
        "credential_ref": credential_ref,
        "capabilities": [SERVICE_BILLING_READ],
        "enabled": True,
    }


def google_connection() -> dict:
    return {
        "id": "22222222-2222-2222-2222-222222222222",
        "display_name": "Google Cloud Billing",
        "adapter_key": "google_cloud",
        "endpoint": "https://monitoring.googleapis.com/v3",
        "credential_ref": None,
        "capabilities": [SERVICE_BILLING_READ],
        "config_data": {
            "credential_provider": "google_adc",
            "project_id": "project-test",
            "target_principal": "billing-reader@project-test.iam.gserviceaccount.com",
        },
        "enabled": True,
    }


def series(model: str, limit_name: str, *values: int) -> dict:
    return {
        "metric": {"labels": {"model": model, "limit_name": limit_name, "method": ""}},
        "points": [{"value": {"int64Value": str(value)}} for value in values],
    }


class ServiceBillingTests(unittest.TestCase):
    def test_missing_key_is_explicit(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(ServiceBillingError, "connection credential"):
                read_openai_month_billing(
                    openai_connection(),
                    datetime(2026, 10, 2, tzinfo=timezone.utc),
                )

    @patch("capabilities.service_billing.service._paged")
    def test_openai_billing_is_normalized(self, paged):
        paged.side_effect = [
            [{"results": [
                {"model": "gpt-a", "input_tokens": 100, "output_tokens": 20, "num_model_requests": 2},
                {"model": "gpt-b", "input_tokens": 50, "output_tokens": 30, "num_model_requests": 1},
            ]}],
            [{"results": [{"amount": {"currency": "usd", "value": 1.25}}]}],
        ]
        with patch.dict(os.environ, {"OPENAI_ADMIN_KEY": "secret-test-key"}, clear=True):
            result = read_openai_month_billing(
                openai_connection(),
                datetime(2026, 10, 2, 3, 4, tzinfo=timezone.utc),
            )
        self.assertEqual(
            result["activity"]["totals"],
            {"input_tokens": 150, "output_tokens": 50, "requests": 3},
        )
        self.assertEqual(result["charges"], {"status": "known", "values": {"usd": 1.25}})
        self.assertEqual(result["activity"]["by_model"][0]["model"], "gpt-a")
        self.assertEqual(result["subscription"]["status"], "unknown")
        self.assertEqual(result["limits"]["status"], "unknown")
        self.assertNotIn("secret-test-key", str(result))

    def test_connection_without_billing_capability_is_rejected(self):
        connection = openai_connection()
        connection["capabilities"] = ["llm_inference"]
        with self.assertRaisesRegex(ServiceBillingError, "service_billing_read"):
            read_openai_month_billing(
                connection,
                datetime(2026, 10, 2, tzinfo=timezone.utc),
            )

    @patch("capabilities.service_billing.service._google_authorized_session")
    @patch("capabilities.service_billing.service._google_time_series")
    def test_google_cloud_deduplicates_per_user_quota_series(self, time_series, auth):
        auth.return_value = object()

        def fake_series(_session, *, metric_type, **_kwargs):
            if metric_type.endswith("generate_content_paid_tier_input_token_count/usage"):
                return [
                    series("gemini-3.8-flash", "GenerateContentPaidTierInputTokensPerModelPerMinute", 3303, 1016),
                    series("gemini-3.8-flash", "GenerateContentPaidTierInputTokensPerModelPerMinutePerUser", 3303, 1016),
                ]
            if metric_type.endswith("generate_requests_per_model/usage"):
                return [
                    series("gemini-3.8-flash", "GenerateRequestsPerMinutePerProjectPerModel", 2, 1),
                ]
            if metric_type.endswith("generate_content_usage_output_token_count"):
                return [series("gemini-3.8-flash", "", 321)]
            if metric_type.endswith("generate_content_paid_tier_input_token_count/limit"):
                return [series("gemini-3.8-flash", "GenerateContentPaidTierInputTokensPerModelPerMinute", 1000000)]
            if metric_type.endswith("generate_requests_per_model/limit"):
                return [series("gemini-3.8-flash", "GenerateRequestsPerMinutePerProjectPerModel", 1000)]
            return []

        time_series.side_effect = fake_series
        result = read_google_cloud_month_billing(
            google_connection(),
            datetime(2026, 10, 3, 4, 0, tzinfo=timezone.utc),
        )
        self.assertEqual(result["activity"]["totals"]["input_tokens"], 4319)
        self.assertEqual(result["activity"]["totals"]["requests"], 3)
        self.assertEqual(result["activity"]["totals"]["output_tokens"], 321)
        self.assertEqual(result["activity"]["breakdown"]["paid_input_tokens"], 4319)
        self.assertEqual(result["charges"]["status"], "unknown")
        self.assertEqual(result["limits"]["status"], "known")
        self.assertEqual(
            result["credential_source"],
            "google_adc_service_account_impersonation",
        )


if __name__ == "__main__":
    unittest.main()
