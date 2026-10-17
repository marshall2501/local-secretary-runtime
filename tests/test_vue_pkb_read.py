# Provenance-Origin: original
# Provenance-AI: AI-generated
"""Offline verification of the shared PKB read route in the Vue host."""
from __future__ import annotations

import unittest
from fastapi.testclient import TestClient

from interfaces.vue_daily.app import create_app


class FakeMemoryReader:
    """Capture bounded filters without accessing a live PKB database."""

    def __init__(self):
        self.calls = []
        self.failure = False

    def search_memory(self, **filters):
        if self.failure:
            raise OSError("read failed")
        self.calls.append(filters)
        return {
            "total": 1, "limit": filters["limit"],
            "offset": filters["offset"],
            "include_history": filters["include_history"],
            "items": [{"kind": "claim", "title": "架空GPU"}],
        }


class VuePkbReadTests(unittest.TestCase):
    """Validate read-only wiring, bounded filters and fail-closed behavior."""

    def setUp(self):
        self.reader = FakeMemoryReader()
        self.client = TestClient(
            create_app(queries=object(), read_service=self.reader),
            base_url="http://127.0.0.1:8097",
        )

    def test_shared_search_contract_and_local_headers(self):
        response = self.client.get(
            "/api/v1/pkb/memory/search",
            params={
                "q": " GPU ", "domain": "pc", "kind": "claim",
                "include_history": "true", "limit": 5, "offset": 10,
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["items"][0]["title"], "架空GPU")
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertEqual(self.reader.calls, [{
            "q": " GPU ", "domain": "pc", "kind": "claim",
            "include_history": True, "limit": 5, "offset": 10,
        }])

    def test_unconfigured_or_failed_reader_does_not_fall_back_to_live_db(self):
        no_reader = TestClient(
            create_app(queries=object()),
            base_url="http://127.0.0.1:8097",
        )
        self.assertEqual(
            no_reader.get("/api/v1/pkb/memory/search").status_code, 503,
        )
        self.reader.failure = True
        self.assertEqual(
            self.client.get("/api/v1/pkb/memory/search").status_code, 503,
        )

    def test_invalid_filters_and_untrusted_hosts_are_rejected(self):
        self.assertEqual(self.client.get(
            "/api/v1/pkb/memory/search?kind=unknown",
        ).status_code, 422)
        self.assertEqual(self.client.get(
            "/api/v1/pkb/memory/search?limit=101",
        ).status_code, 422)
        self.assertEqual(self.client.get(
            "/api/v1/pkb/memory/search?q=" + "x" * 201,
        ).status_code, 422)
        self.assertEqual(self.client.get(
            "/api/v1/pkb/memory/search", headers={"Host": "evil.example"},
        ).status_code, 403)
        self.assertEqual(self.reader.calls, [])


if __name__ == "__main__":
    unittest.main()
