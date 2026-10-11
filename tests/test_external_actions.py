"""Isolated, real-loopback tests for the external Action service.

No production database, credentials, user data, or public network is used.
The disposable store models the existing Task/Approval/Action uniqueness rules;
PostgreSQL integration still requires a separate isolated-DB check.
"""
from __future__ import annotations

import json
import threading
import unittest
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from uuid import UUID, uuid4

from integrations.connections.loopback_setting_adapter import LoopbackSettingAdapter
from ritsuko.application.external_actions import (
    ActionDenied,
    ExternalActionService,
    FIXTURE_OPERATION,
)


class _FixtureHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _send(self, status, value):
        payload = json.dumps(value).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):
        if self.path != "/value":
            return self._send(404, {})
        state = self.server.fixture
        with state["lock"]:
            value = state["value"]
            if state["mismatch"]:
                value = "deliberately-different"
        self._send(200, {"value": value})

    def do_PUT(self):
        if self.path != "/value":
            return self._send(404, {})
        size = int(self.headers.get("Content-Length", 0))
        if not 0 < size < 1024:
            return self._send(422, {"error": "invalid_size"})
        raw = json.loads(self.rfile.read(size))
        if set(raw) != {"value"} or not isinstance(raw["value"], str):
            return self._send(422, {"error": "bad_input"})
        state = self.server.fixture
        with state["lock"]:
            state["value"] = raw["value"]
            state["writes"] += 1
        self._send(200, {"accepted": True})


class _MemoryStore:
    def __init__(self):
        self.lock = threading.Lock()
        self.tasks = {}
        self.approvals = {}
        self.actions = {}
        self.audit = []
        self.results = []

    def new_task(self, *, external_actions=True, state="ACTION_PROPOSAL"):
        task_id = uuid4()
        self.tasks[task_id] = {
            "status": "running",
            "external_actions": external_actions,
            "session": {"status": "proposal_ready",
                        "detail": {"state": state,
                                   "action_candidate": "Change the fixture setting"}},
            "approval_id": None,
        }
        return task_id

    def offer(self, *, task_id, operation, arguments, requested_by, expires_at):
        with self.lock:
            task = self.tasks[task_id]
            if not task["external_actions"]:
                raise ValueError("external_action_not_permitted")
            if task["session"]["detail"]["state"] != "ACTION_PROPOSAL":
                raise ValueError("magi_action_proposal_missing")
            if task["approval_id"] is not None:
                raise ValueError("external_action_already_offered")
            approval_id = uuid4()
            self.approvals[approval_id] = {
                "task_id": task_id, "operation": dict(operation),
                "arguments": dict(arguments), "expires_at": expires_at,
                "status": "pending",
            }
            task.update(status="waiting_approval", approval_id=approval_id)
            self.audit.append("approval_requested")
            return {"approval_id": str(approval_id), "status": "pending",
                    "operation": dict(operation), "arguments": dict(arguments)}

    def decide(self, *, task_id, approval_id, approved, actor):
        with self.lock:
            a = self.approvals[approval_id]
            if (a["task_id"] != task_id or a["status"] != "pending"
                    or a["expires_at"] <= datetime.now(timezone.utc)):
                raise ValueError("approval_invalid")
            a["status"] = "approved" if approved else "rejected"
            if approved:
                self.actions[approval_id] = "planned"
            self.audit.append("approval_decided")
            return {"approval_id": str(approval_id), "status": a["status"]}

    def claim(self, *, task_id, approval_id):
        with self.lock:
            a = self.approvals[approval_id]
            if (a["task_id"] != task_id or a["status"] != "approved"
                    or a["expires_at"] <= datetime.now(timezone.utc)
                    or self.actions.get(approval_id) != "planned"):
                raise ValueError("action_not_claimable")
            self.actions[approval_id] = "running"
            self.audit.append("execution_claimed")
            return {"operation": dict(a["operation"]),
                    "arguments": dict(a["arguments"])}

    def finish(self, *, task_id, approval_id, verified, evidence):
        with self.lock:
            if self.actions.get(approval_id) != "running":
                raise ValueError("action_not_running")
            self.actions[approval_id] = "succeeded" if verified else "unknown"
            self.results.append({"verified": verified, "evidence": evidence})
            self.audit.append("verified" if verified else "unknown")
            return {"status": self.actions[approval_id], "verified": verified,
                    "task_status": "completed" if verified else "waiting_external"}

    def mark_unknown(self, *, task_id, approval_id, error_type):
        return self.finish(task_id=task_id, approval_id=approval_id,
                           verified=False, evidence={"error_type": error_type})


class ExternalActionsTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = {
            "value": "initial", "writes": 0, "mismatch": False,
            "lock": threading.Lock(),
        }
        cls.http = ThreadingHTTPServer(("127.0.0.1", 0), _FixtureHandler)
        cls.http.fixture = cls.fixture
        cls.thread = threading.Thread(
            target=cls.http.serve_forever, daemon=True,
        )
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.http.shutdown()
        cls.http.server_close()
        cls.thread.join(timeout=5)

    def setUp(self):
        self.store = _MemoryStore()
        self.adapter = LoopbackSettingAdapter(self.http.server_port)
        self.service = ExternalActionService(
            self.store, {FIXTURE_OPERATION.operation_id: (FIXTURE_OPERATION, self.adapter)},
        )
        with self.fixture["lock"]:
            self.fixture["value"] = "initial"
            self.fixture["writes"] = 0
            self.fixture["mismatch"] = False

    async def approved(self, value="new-value"):
        task_id = self.store.new_task()
        offered = await self.service.offer(
            task_id=task_id, operation_id=FIXTURE_OPERATION.operation_id,
            value=value,
        )
        approval_id = UUID(offered["approval_id"])
        decided = await self.service.decide(
            task_id=task_id, approval_id=approval_id, approved=True,
            actor="local_user",
        )
        self.assertEqual(decided["status"], "approved")
        return task_id, approval_id

    async def test_approved_action_readback_records_success_once(self):
        task_id, approval_id = await self.approved()
        result = await self.service.execute(task_id=task_id, approval_id=approval_id)
        self.assertTrue(result["verified"])
        self.assertEqual(result["task_status"], "completed")
        self.assertEqual(self.fixture["value"], "new-value")
        self.assertEqual(self.fixture["writes"], 1)
        self.assertEqual(len(self.store.results), 1)
        self.assertIn("verified", self.store.audit)
        with self.assertRaisesRegex(ValueError, "action_not_claimable"):
            await self.service.execute(task_id=task_id, approval_id=approval_id)
        self.assertEqual(self.fixture["writes"], 1)

    async def test_no_write_without_authorization(self):
        task_id = self.store.new_task(external_actions=False)
        with self.assertRaisesRegex(ValueError, "external_action_not_permitted"):
            await self.service.offer(
                task_id=task_id, operation_id=FIXTURE_OPERATION.operation_id,
                value="blocked",
            )
        self.assertEqual(self.fixture["writes"], 0)
        task_id = self.store.new_task()
        with self.assertRaises(ActionDenied):
            await self.service.offer(task_id=task_id,
                                     operation_id="unsupported.delete",
                                     value="blocked")
        self.assertEqual(self.fixture["writes"], 0)

    async def test_rejected_expired_and_wrong_actor_never_send(self):
        task_id = self.store.new_task()
        offered = await self.service.offer(
            task_id=task_id, operation_id=FIXTURE_OPERATION.operation_id,
            value="nope",
        )
        approval_id = UUID(offered["approval_id"])
        with self.assertRaises(ActionDenied):
            await self.service.decide(task_id=task_id, approval_id=approval_id,
                                      approved=True, actor="service")
        result = await self.service.decide(
            task_id=task_id, approval_id=approval_id,
            approved=False, actor="local_user",
        )
        self.assertEqual(result["status"], "rejected")
        with self.assertRaises(ValueError):
            await self.service.execute(task_id=task_id, approval_id=approval_id)
        self.assertEqual(self.fixture["writes"], 0)

        other_task, other_approval = await self.approved("expired")
        self.store.approvals[other_approval]["expires_at"] = datetime(
            2020, 1, 1, tzinfo=timezone.utc,
        )
        with self.assertRaises(ValueError):
            await self.service.execute(task_id=other_task,
                                       approval_id=other_approval)
        self.assertEqual(self.fixture["writes"], 0)

    async def test_missing_magi_proposal_blocks_offer(self):
        task_id = self.store.new_task(state="READY")
        with self.assertRaises(ValueError):
            await self.service.offer(task_id=task_id,
                                     operation_id=FIXTURE_OPERATION.operation_id,
                                     value="blocked")
        self.assertEqual(self.fixture["writes"], 0)

    async def test_readback_mismatch_is_unknown_not_completed(self):
        task_id, approval_id = await self.approved("expected")
        with self.fixture["lock"]:
            self.fixture["mismatch"] = True
        result = await self.service.execute(task_id=task_id, approval_id=approval_id)
        self.assertEqual(result["status"], "unknown")
        self.assertFalse(result["verified"])
        self.assertEqual(result["task_status"], "waiting_external")
        self.assertEqual(self.fixture["writes"], 1)
        with self.assertRaises(ValueError):
            await self.service.execute(task_id=task_id, approval_id=approval_id)
        self.assertEqual(self.fixture["writes"], 1)

    async def test_concurrent_dispatch_one_claim_wins(self):
        task_id, approval_id = await self.approved("parallel")
        async def run():
            try:
                return await self.service.execute(task_id=task_id,
                                                  approval_id=approval_id)
            except ValueError:
                return None
        import asyncio
        a, b = await asyncio.gather(run(), run())
        self.assertEqual(sum(x is not None for x in (a, b)), 1)
        self.assertEqual(self.fixture["writes"], 1)

    async def test_transport_failure_marks_unknown_and_no_resend(self):
        task_id, approval_id = await self.approved("unreachable")
        self.service = ExternalActionService(
            self.store,
            {FIXTURE_OPERATION.operation_id: (FIXTURE_OPERATION,
                                              LoopbackSettingAdapter(65534))},
        )
        with self.assertRaises(Exception):
            await self.service.execute(task_id=task_id, approval_id=approval_id,
                                       timeout=1)
        self.assertEqual(self.store.actions[approval_id], "unknown")
        self.assertEqual(self.fixture["writes"], 0)
        with self.assertRaises(ValueError):
            await self.service.execute(task_id=task_id, approval_id=approval_id)
