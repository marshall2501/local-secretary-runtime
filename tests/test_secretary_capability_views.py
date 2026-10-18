# Provenance-Origin: original
# Provenance-AI: AI-generated
"""Single-Host Finance, Billing, Connections and Debug API contracts.

These tests never connect to the user's DB or real providers.
"""
from __future__ import annotations

from dataclasses import dataclass
import base64
import hashlib
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from capabilities.finance.finance_import import ImportPlan, ImportResult
from capabilities.finance.finance_preview import FinancePreview

from fastapi.testclient import TestClient
from interfaces.api.secretary_server import create_app


@dataclass
class FakeDashboard:
    transaction_count: int = 2
    calculation_target_count: int = 2
    income_total: int = 300
    expense_total: int = 100
    net_total: int = 200
    monthly: tuple = ()
    categories: tuple = ()
    recent_rows: tuple = ()
    import_batches: tuple = ()
    page: int = 1
    page_size: int = 25
    total_pages: int = 1
    sort_by: str = "date"
    sort_dir: str = "desc"
    row_mode: str = "calculation_target"


class FakeFinance:
    """Record filters passed to the existing Finance Application."""

    def __init__(self):
        self.calls = []

    def load_finance_dashboard(self, **filters):
        self.calls.append(filters)
        return FakeDashboard()

    def finance_filter_options(self):
        return {"accounts": ["架空銀行"], "major_categories": ["食費"]}

    def plan_import(self, preview, csv_bytes):
        self.calls.append(("plan", preview.filename, csv_bytes))
        return ImportPlan(hashlib.sha256(csv_bytes).hexdigest(), 1, 1, 0, 0, 0)

    def commit_import(self, preview, csv_bytes, filename):
        self.calls.append(("commit", filename, csv_bytes))
        return ImportResult(
            "committed", "fake-batch", hashlib.sha256(csv_bytes).hexdigest(),
            1, 1, 0, 0,
        )


class FakeConnections:
    """Return a credential-bearing sentinel that must never reach HTTP."""

    def list_service_connections(self, *, include_disabled=False, capability=None):
        return [{
            "id": "connection-1", "display_name": "架空API", "adapter_key": "openai",
            "endpoint": "https://example.org", "enabled": True,
            "connection_type": "api_key", "auth_configured": True,
            "auth_fields": ["api_key"],
            "auth_data": {"api_key": "DO-NOT-EXPOSE"},
            "credential_value": "DO-NOT-EXPOSE",
        }]

    def get_service_connection(self, connection_id):
        if connection_id != "connection-1":
            raise ValueError("not found")
        return {"id": connection_id, "display_name": "架空API",
                "adapter_key": "openai", "enabled": True,
                "capabilities": ["service_billing_read"]}

    def upsert_service_connection(self, **data):
        return {
            "id": "connection-1", "display_name": data["display_name"],
            "adapter_key": data["adapter_key"], "endpoint": data.get("endpoint"),
            "auth_configured": bool(data.get("auth_data")),
            "auth_fields": list((data.get("auth_data") or {}).keys()),
            "enabled": data.get("enabled", True),
        }


class FakeProfiles:
    """Existing Billing repository's list-only API."""

    def list_service_billing_profiles(self, *, include_disabled=False):
        return [{"id": "profile-1", "display_name": "利用料金",
                 "connection_id": "connection-1", "enabled": True,
                 "connection_enabled": True}]

    def upsert_service_billing_profile(self, **data):
        return {"id": "profile-1", **data}


class DailyCapabilityApiTests(unittest.TestCase):
    """Check real router wiring with injected fake capability repositories."""

    def setUp(self):
        self.finance = FakeFinance()
        self.connections = FakeConnections()
        self.billing = FakeProfiles()
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.client = TestClient(create_app(
            queries=object(), finance_repository=self.finance,
            service_connections=self.connections, billing_settings=self.billing,
        ), base_url="http://127.0.0.1:8097")

    def test_finance_uses_existing_app_filters_and_prevents_invalid_requests(self):
        response = self.client.get("/api/v1/finance/dashboard",
                                   params={"row_mode": "all", "page": 3,
                                           "account": "架空銀行", "recent_limit": 12})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["net_total"], 200)
        self.assertEqual(self.finance.calls[0]["row_mode"], "all")
        self.assertEqual(self.finance.calls[0]["page"], 3)
        self.assertEqual(self.finance.calls[0]["account"], "架空銀行")
        self.assertEqual(self.finance.calls[0]["recent_limit"], 12)
        self.assertEqual(self.client.get(
            "/api/v1/finance/options").json()["accounts"], ["架空銀行"])
        self.assertEqual(self.client.get(
            "/api/v1/finance/dashboard?row_mode=bad").status_code, 422)
        self.assertEqual(self.client.get(
            "/api/v1/finance/dashboard?page=0").status_code, 422)

    def test_moneyforward_preview_requires_explicit_matching_commit(self):
        """No Finance write during preview; the confirmed hash is enforced."""
        raw = b"not-a-real-finance-csv"
        encoded = base64.b64encode(raw).decode()
        preview = FinancePreview(
            filename="synthetic.csv", row_count=1,
            start_date="2026-10-01", end_date="2026-10-01",
            calculation_target_count=1, transfer_count=0,
            unique_id_count=1, duplicate_id_count=0,
            income_total=100, expense_total=0, net_total=100,
            monthly=[], categories=[], accounts=[], recent_rows=[],
            transactions=[{"external_id": "test-1"}],
        )
        body = {"filename": "synthetic.csv", "csv_base64": encoded}
        with patch("interfaces.api.finance.analyze_moneyforward_csv",
                   return_value=preview):
            planned = self.client.post("/api/v1/finance/import/plan", json=body)
            self.assertEqual(planned.status_code, 200, planned.text)
            self.assertEqual(planned.json()["plan"]["inserted"], 1)
            self.assertEqual(self.finance.calls[-1][0], "plan")
            self.assertEqual(self.client.post(
                "/api/v1/finance/import/commit", json=body,
            ).status_code, 422)
            self.assertEqual(self.client.post(
                "/api/v1/finance/import/commit", json={
                    **body, "expected_sha256": "a" * 64,
                },
            ).status_code, 409)
            confirmed = self.client.post(
                "/api/v1/finance/import/commit", json={
                    **body,
                    "expected_sha256": planned.json()["plan"]["source_sha256"],
                },
            )
            self.assertEqual(confirmed.status_code, 200, confirmed.text)
            self.assertEqual(confirmed.json()["status"], "committed")
            self.assertEqual(self.finance.calls[-1][0], "commit")

    def test_connections_and_billing_on_demand(self):
        rows = self.client.get("/api/v1/connections")
        self.assertEqual(rows.status_code, 200, rows.text)
        self.assertEqual(rows.json()[0]["auth_configured"], True)
        self.assertNotIn("DO-NOT-EXPOSE", rows.text)
        profiles = self.client.get("/api/v1/billing/profiles")
        self.assertEqual(profiles.status_code, 200, profiles.text)
        self.assertEqual(profiles.json()[0]["id"], "profile-1")
        with patch("interfaces.api.daily_overview.read_service_billing_snapshot",
                   return_value={"provider": "fake", "usage": 9}) as read:
            details = self.client.get(
                "/api/v1/billing/profiles/profile-1/snapshot")
            self.assertEqual(details.status_code, 200, details.text)
            self.assertEqual(details.json()["usage"], 9)
            read.assert_called_once()
        self.assertEqual(
            self.client.get("/api/v1/billing/profiles/unknown/snapshot").status_code,
            404,
        )
        self.assertEqual(self.client.post("/api/v1/connections").status_code, 405)

    def test_settings_update_uses_existing_services_without_echoing_auth(self):
        """Settings updates are UI actions, not a new credential repository."""
        result = self.client.post("/api/v1/connections", json={
            "adapter_key": "openai", "display_name": "架空API",
            "connection_type": "api_key", "auth_data": {
                "api_key": "fixture-test-secret",
            },
        })
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(result.json()["auth_configured"], True)
        self.assertNotIn("fixture-test-secret", result.text)
        created = self.client.post("/api/v1/billing/profiles", json={
            "display_name": "架空 Usage", "connection_id": "connection-1",
            "enabled": True,
        })
        self.assertEqual(created.status_code, 200, created.text)
        self.assertEqual(created.json()["display_name"], "架空 Usage")

    def test_debug_runtime_and_environment_reuse_existing_diagnostics(self):
        with patch("interfaces.api.daily_overview.runtime_snapshot",
                   return_value={"status": "ok", "pid": 99}) as read:
            self.assertEqual(self.client.get(
                "/api/v1/debug/runtime").json()["pid"], 99)
            read.assert_called_once()
        with patch("interfaces.api.daily_overview.environment_snapshot",
                   return_value=[{"name": "TEST", "value": "NOT SET"}]):
            self.assertEqual(self.client.get(
                "/api/v1/debug/environment").json()[0]["name"], "TEST")
        self.assertEqual(self.client.get("/api/v1/debug/database").status_code, 503)

    def test_unconfigured_capability_interfaces_return_service_unavailable(self):
        client = TestClient(create_app(queries=object()),
                            base_url="http://127.0.0.1:8097")
        for endpoint in (
            "/api/v1/finance/dashboard", "/api/v1/finance/options",
            "/api/v1/connections", "/api/v1/billing/profiles",
        ):
            self.assertEqual(client.get(endpoint).status_code, 503)


if __name__ == "__main__":
    unittest.main()
