# Provenance-Origin: original
# Provenance-AI: AI-generated
"""PKB daily Application entry through the unified Secretary Server, no DB."""
from __future__ import annotations

from dataclasses import dataclass
import unittest
from unittest.mock import patch
from uuid import uuid4

from fastapi.testclient import TestClient

from interfaces.api.secretary_server import create_app


@dataclass
class Decision:
    status: str
    reason: str = "recorded"


class FakePkb:
    """Capture user commands without touching personal memory."""

    def __init__(self):
        self.calls = []

    def list_pending(self, limit=50):
        self.calls.append(("pending", limit))
        return [{"id": "candidate-1", "status": "pending"}]

    def list_reviewed(self, limit=20):
        self.calls.append(("reviewed", limit))
        return [{"id": "candidate-2", "review_status": "needs_edit"}]

    def accept_pending(self, pending_id):
        self.calls.append(("accept", pending_id))
        return Decision("accepted")

    def review_pending(self, pending_id, decision):
        self.calls.append(("review", pending_id, decision))
        return Decision(decision)


class UnifiedPkbCommandTests(unittest.TestCase):
    """Test API validation, application delegation, and no-write-if-unbound."""

    def setUp(self):
        self.repo = FakePkb()
        self.interpreter = lambda text, entities: None
        self.client = TestClient(
            create_app(
                queries=object(), pkb_repository=self.repo,
                pkb_interpreter=self.interpreter,
            ),
            base_url="http://127.0.0.1:8097",
        )

    def test_register_and_correct_delegate_to_independent_pkb(self):
        with patch("interfaces.api.pkb_commands.register_text") as register:
            register.return_value = {"status": "registered", "id": "pkb-1"}
            result = self.client.post("/api/v1/pkb/register",
                                      json={"text": "メモリ登録"})
            self.assertEqual(result.status_code, 200, result.text)
            self.assertEqual(result.json()["status"], "registered")
            register.assert_called_once_with(
                "メモリ登録", repository=self.repo, interpreter=self.interpreter,
            )
        with patch("interfaces.api.pkb_commands.correct_text") as correction:
            correction.return_value = {"status": "corrected"}
            result = self.client.post("/api/v1/pkb/correct",
                                      json={"text": "旧情報を訂正"})
            self.assertEqual(result.status_code, 200, result.text)
            correction.assert_called_once_with("旧情報を訂正", repository=self.repo)

    def test_pending_accept_review_and_history(self):
        self.assertEqual(self.client.get("/api/v1/pkb/pending?limit=2").json()[0]["id"],
                         "candidate-1")
        self.assertEqual(self.client.get("/api/v1/pkb/reviewed").json()[0]["review_status"],
                         "needs_edit")
        self.assertEqual(self.client.post(
            "/api/v1/pkb/pending/candidate-1/accept",
        ).json()["status"], "accepted")
        self.assertEqual(self.client.post(
            "/api/v1/pkb/pending/candidate-2/review",
            json={"decision": "needs_edit"},
        ).json()["status"], "needs_edit")
        self.assertEqual(self.repo.calls, [
            ("pending", 2), ("reviewed", 20),
            ("accept", "candidate-1"), ("review", "candidate-2", "needs_edit"),
        ])

    def test_invalid_requests_and_unconfigured_ports_reject_without_execution(self):
        self.assertEqual(self.client.post("/api/v1/pkb/register",
                                          json={"text": ""}).status_code, 422)
        self.assertEqual(self.client.get("/api/v1/pkb/pending?limit=101").status_code, 422)
        self.assertEqual(self.client.post(
            "/api/v1/pkb/pending/id/review", json={"decision": "accepted"},
        ).status_code, 422)
        self.assertEqual(self.client.post(
            "/api/v1/pkb/register", headers={"Host": "other.example"},
            json={"text": "メモリ登録"},
        ).status_code, 403)
        blank = TestClient(create_app(queries=object()),
                           base_url="http://127.0.0.1:8097")
        self.assertEqual(blank.post("/api/v1/pkb/register",
                                    json={"text": "メモリ登録"}).status_code, 503)
        self.assertEqual(blank.get("/api/v1/pkb/pending").status_code, 503)
        self.assertEqual(self.repo.calls, [])


if __name__ == "__main__":
    unittest.main()
