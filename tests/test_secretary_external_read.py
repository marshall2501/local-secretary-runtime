# Provenance-Origin: original
# Provenance-AI: AI-generated
"""Same-host compatibility for existing read-only 8010 REST/MCP consumers."""
from __future__ import annotations

import unittest
from uuid import uuid4

from fastapi.testclient import TestClient

from interfaces.api.secretary_server import create_app


class FakeReadService:
    """One shared application, no legacy executor or database."""

    def __init__(self):
        self.calls = []

    def list_entities(self, **filters):
        self.calls.append(("entities", filters))
        return [{"name": "架空サブPC"}]

    def list_current_claims(self, **filters):
        self.calls.append(("claims", filters))
        return [{"predicate": "os"}]

    def list_tasks(self, **filters):
        self.calls.append(("tasks", filters))
        return [{"request": "架空Task"}]

    def search_memory(self, **filters):
        self.calls.append(("memory", filters))
        return {"total": 1, "items": [{"kind": "claim"}]}

    def search_experience(self, **filters):
        self.calls.append(("experience", filters))
        return {"total": 0, "items": []}


class ExternalReadTests(unittest.TestCase):
    """Check existing paths, filtering and read credential use on the new Host."""

    def setUp(self):
        self.service = FakeReadService()
        self.token = "read-only-test-token"
        self.client = TestClient(
            create_app(
                queries=object(), read_service=self.service,
                external_read_token_loader=lambda: self.token,
            ),
            base_url="http://127.0.0.1:8097",
        )
        self.header = {"Authorization": "Bearer " + self.token}

    def test_external_read_contract_on_same_host(self):
        tasks = self.client.get("/tasks?status=completed&limit=7",
                                headers=self.header)
        self.assertEqual(tasks.status_code, 200, tasks.text)
        self.assertEqual(tasks.json()[0]["request"], "架空Task")
        memory = self.client.get(
            "/memory/search?q=GPU&domain=pc&kind=claim&include_history=true",
            headers=self.header,
        )
        self.assertEqual(memory.status_code, 200, memory.text)
        self.assertEqual(memory.json()["total"], 1)
        self.assertEqual(self.client.get(
            "/entities?domain=pc", headers=self.header,
        ).status_code, 200)
        self.assertEqual(self.client.get(
            "/claims/current", params={"entity_id": str(uuid4())},
            headers=self.header,
        ).status_code, 200)
        self.assertEqual(self.client.get(
            "/experience/search", headers=self.header,
        ).status_code, 200)
        self.assertEqual(self.service.calls[0],
                         ("tasks", {"task_status": "completed", "limit": 7}))
        self.assertEqual(self.service.calls[1],
                         ("memory", {"q": "GPU", "domain": "pc", "kind": "claim",
                                     "include_history": True, "limit": 50, "offset": 0}))

    def test_external_token_is_required_and_external_task_write_not_exposed(self):
        for path in ("/tasks", "/memory/search", "/entities", "/experience/search"):
            self.assertEqual(self.client.get(path).status_code, 401)
        self.assertEqual(self.client.post("/tasks", headers=self.header).status_code, 405)
        self.assertEqual(self.client.get(
            "/tasks?status=completed&task_status=pending",
            headers=self.header,
        ).status_code, 422)
        self.assertEqual(self.service.calls, [])
        no_token = TestClient(
            create_app(queries=object(), read_service=self.service),
            base_url="http://127.0.0.1:8097",
        )
        self.assertEqual(no_token.get("/tasks").status_code, 503)


if __name__ == "__main__":
    unittest.main()
