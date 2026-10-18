# Provenance-Origin: original
# Provenance-AI: AI-generated
"""Isolated unified Secretary Server: one HTTP origin, no real DB/LLM."""
from __future__ import annotations

import logging
import logging.config
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from uuid import uuid4

from fastapi.testclient import TestClient
from uvicorn.config import LOGGING_CONFIG

from bootstrap.secretary_server import configure_daily_database, configure_runtime_environment
from infrastructure.secretary_logging import build_secretary_log_config
from interfaces.api.secretary_server import create_app


class Queries:
    """Fake persisted Task application query service."""

    def __init__(self):
        self.uid = uuid4()

    def recent(self, limit, offset):
        return [{"id": str(self.uid), "status": "waiting_external",
                 "request": "調査依頼", "revision": 1}]

    open = recent

    def completed(self, limit, offset):
        return []

    def trace(self, task_id):
        if task_id != self.uid:
            raise ValueError("missing task")
        return {"task": {"id": str(self.uid), "status": "waiting_external",
                         "phase": "awaiting_clarification",
                         "magi_session": {"status": "waiting_user"}},
                "actions": [], "advisor_events": []}


class Commands:
    """Fake TaskCommand executor: HTTP only, no real Task or LLM."""

    def __init__(self):
        self.calls = []

    async def start(self, request, *, decision_strategy, task_id=None):
        self.calls.append(("start", request, decision_strategy, task_id))
        return {"kind": "start", "status": "accepted",
                "task_id": str(task_id or uuid4())}

    async def answer(self, task_id, reply):
        self.calls.append(("answer", task_id, reply))
        return {"status": "accepted"}

    async def review(self, task_id, decision):
        self.calls.append(("review", task_id, decision))
        return {"status": "accepted"}

    async def resume(self, task_id):
        self.calls.append(("resume", task_id))
        return {"status": "accepted"}

    def stop(self, task_id):
        self.calls.append(("stop", task_id))
        return {"stop_requested": True}

    def operation(self, task_id):
        return {"status": "operation_not_in_process", "task_id": str(task_id)}


class Reader:
    """Fake independent PKB ReadService."""

    def search_memory(self, **filters):
        return {"total": 1, "limit": filters["limit"],
                "offset": filters["offset"],
                "items": [{"kind": "claim", "title": "架空メモリ"}]}


class SecretaryServerTests(unittest.TestCase):
    """Validate the new canonical server as a working unified HTTP contract."""

    def setUp(self):
        self.query = Queries()
        self.commands = Commands()
        self.reader = Reader()
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dist = Path(self.tmp.name) / "dist"
        self.dist.mkdir()
        (self.dist / "index.html").write_text(
            '<html><body>Secretary UI fixture</body></html>', encoding="utf-8",
        )
        self.client = TestClient(
            create_app(queries=self.query, commands=self.commands,
                       read_service=self.reader, dist_dir=self.dist),
            base_url="http://127.0.0.1:8097",
        )

    def test_one_origin_serves_vue_tasks_and_independent_pkb(self):
        uid = str(self.query.uid)
        self.assertEqual(self.client.get("/").status_code, 200)
        self.assertIn("Secretary UI fixture", self.client.get("/ui/").text)
        self.assertEqual(self.client.get(
            "/api/v1/ritsuko/health",
        ).json()["mode"], "commands_enabled")
        tasks = self.client.get("/api/v1/ritsuko/tasks")
        self.assertEqual(tasks.status_code, 200)
        self.assertEqual(tasks.json()["items"][0]["id"], uid)
        self.assertEqual(self.client.get(
            "/api/v1/pkb/memory/search?q=架空&limit=2",
        ).json()["items"][0]["title"], "架空メモリ")

        started = self.client.post("/api/v1/ritsuko/tasks", json={
            "request": "調べて", "decision_strategy": "llm", "task_id": uid,
        })
        self.assertEqual(started.status_code, 202, started.text)
        self.assertEqual(self.client.post(
            "/api/v1/ritsuko/tasks/" + uid + "/answer",
            json={"reply": "補足"},
        ).status_code, 202)
        self.assertEqual(self.client.post(
            "/api/v1/ritsuko/tasks/" + uid + "/review",
            json={"decision": "answer_only"},
        ).status_code, 202)
        self.assertEqual(self.client.post(
            "/api/v1/ritsuko/tasks/" + uid + "/resume",
        ).status_code, 202)
        self.assertEqual(self.client.post(
            "/api/v1/ritsuko/tasks/" + uid + "/stop",
        ).status_code, 200)
        self.assertEqual(
            [call[0] for call in self.commands.calls],
            ["start", "answer", "review", "resume", "stop"],
        )

    def test_untrusted_host_is_denied_but_normal_localhost_client_can_open(self):
        self.assertEqual(self.client.get(
            "/api/v1/ritsuko/tasks", headers={"host": "other.example"},
        ).status_code, 403)
        self.assertEqual(self.client.get(
            "/ui/", headers={"sec-fetch-site": "cross-site"},
        ).status_code, 200)

    def test_explicit_daily_database_config_fails_closed_and_keeps_secret_local(self):
        root = Path(self.tmp.name)
        (root / ".env.postgres").write_text("LSA_DB_PORT=5544\n", encoding="utf-8")
        (root / "secrets").mkdir()
        (root / "secrets" / "secretary-daily-runtime-password.txt").write_text(
            "test-only-secret", encoding="utf-8",
        )
        with patch.dict(os.environ, {}, clear=True):
            configure_daily_database(root)
            self.assertEqual(os.environ["LSA_DAILY_DB_NAME"], "secretary")
            self.assertEqual(os.environ["LSA_PKB_DAILY_PORT"], "5544")
            self.assertTrue(os.environ["LSA_PKB_DAILY_SECRET"].endswith(
                "secretary-daily-runtime-password.txt"))
        (root / ".env.postgres").write_text("LSA_DB_PORT=abc\n", encoding="utf-8")
        with self.assertRaises(RuntimeError):
            configure_daily_database(root)

    def test_unified_server_reads_existing_magi_env_without_legacy_launcher(self):
        """Load only known runtime variables; duplicate settings fail cleanly."""
        root = Path(self.tmp.name)
        source = root / ".env"
        source.write_text(
            "OPENAI_BASE_URL=https://example.org/v1\n"
            "LSA_MAGI_CASPER_MODEL=gpt-fixture\n"
            "OTHER_APPLICATION_PASSWORD=not-loaded\n",
            encoding="utf-8",
        )
        with patch.dict(os.environ, {}, clear=True):
            configure_runtime_environment(root)
            self.assertEqual(os.environ["OPENAI_BASE_URL"], "https://example.org/v1")
            self.assertEqual(os.environ["LSA_MAGI_CASPER_MODEL"], "gpt-fixture")
            self.assertNotIn("OTHER_APPLICATION_PASSWORD", os.environ)
            source.write_text("OPENAI_BASE_URL=one\nOPENAI_BASE_URL=two\n",
                              encoding="utf-8")
            with self.assertRaises(RuntimeError):
                configure_runtime_environment(root)

    def test_single_rotating_file_contains_startup_and_access(self):
        path = Path(self.tmp.name) / "logs"
        config = build_secretary_log_config(log_dir=path)
        self.assertEqual(config["handlers"]["secretary_file"]["backupCount"], 5)
        self.assertEqual(config["handlers"]["secretary_file"]["maxBytes"], 10 * 1024 * 1024)
        try:
            logging.config.dictConfig(config)
            logging.getLogger("uvicorn.error").info("Secretary is ready")
            logging.getLogger("uvicorn.access").info(
                '%s - "%s %s HTTP/%s" %d',
                "127.0.0.1:5050", "GET", "/api/v1/ritsuko/tasks", "1.1", 200,
            )
        finally:
            logging.config.dictConfig(LOGGING_CONFIG)
        log = (path / "secretary.log").read_text(encoding="utf-8")
        self.assertIn("Secretary is ready", log)
        self.assertIn("GET /api/v1/ritsuko/tasks HTTP/1.1", log)
        self.assertEqual(log.count("[secretary]"), 2)


if __name__ == "__main__":
    unittest.main()
