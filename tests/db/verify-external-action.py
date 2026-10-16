"""Disposable-only PostgreSQL + real loopback HTTP acceptance for RITSUKO Actions.

This script must be launched by tests/db/test-integration.ps1, which creates
its own randomly named Docker Compose project. It refuses other containers.
No production data, real credentials, or external service is accessed.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from uuid import UUID, uuid4

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import psycopg
from psycopg.types.json import Jsonb

from infrastructure.postgres.external_action_repository import PostgresExternalActionStore
from integrations.connections.loopback_setting_adapter import LoopbackSettingAdapter
from ritsuko.application.external_actions import (
    ExternalActionService, FIXTURE_OPERATION, FIXTURE_LOW_IMPACT_OPERATION,
)
from ritsuko.application.task_runtime import StateDrivenTaskApplication, TaskRuntimePorts
from ritsuko.core.magi_bridge import CORE_SLICE


def checked_disposable_port(project: str, container_id: str) -> int:
    if not re.fullmatch(r"local-secretary-test-[a-f0-9]{12}", project):
        raise RuntimeError("not_an_isolated_test_project")
    if not re.fullmatch(r"[0-9a-f]{12,64}", container_id):
        raise RuntimeError("invalid_disposable_container")
    inspected = json.loads(subprocess.check_output(
        ["docker", "inspect", container_id], text=True,
    ))[0]
    labels = inspected["Config"]["Labels"]
    if (labels.get("com.docker.compose.project") != project
            or labels.get("com.docker.compose.service") != "secretary-postgres"):
        raise RuntimeError("wrong_docker_project_or_service")
    if inspected["State"]["Status"] != "running":
        raise RuntimeError("isolated_database_not_running")
    bindings = inspected["NetworkSettings"]["Ports"]["5432/tcp"]
    if len(bindings) != 1 or bindings[0]["HostIp"] != "127.0.0.1":
        raise RuntimeError("non_loopback_database_binding")
    port = int(bindings[0]["HostPort"])
    env_file = Path(os.environ["LSA_TEST_DB_ENV_FILE"])
    if env_file.read_text(encoding="utf-8").strip() != f"LSA_DB_PORT={port}":
        raise RuntimeError("isolated_database_port_mismatch")
    return port


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def _write(self, payload):
        data = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_PUT(self):
        if self.path != "/value":
            self.send_error(404)
            return
        length = int(self.headers.get("Content-Length", "0"))
        if not 0 < length < 1024:
            self.send_error(422)
            return
        payload = json.loads(self.rfile.read(length))
        if set(payload) != {"value"} or type(payload["value"]) is not str:
            self.send_error(422)
            return
        with self.server.lock:
            self.server.value = payload["value"]
            self.server.writes += 1
        self._write({"accepted": True})

    def do_GET(self):
        if self.path != "/value":
            self.send_error(404)
            return
        with self.server.lock:
            value = "intentional-mismatch" if self.server.mismatch else self.server.value
        self._write({"value": value})


async def verify(connect, server):
    store = PostgresExternalActionStore(connect)
    adapter = LoopbackSettingAdapter(server.server_port)
    service = ExternalActionService(store, {
        FIXTURE_OPERATION.operation_id: (FIXTURE_OPERATION, adapter),
        FIXTURE_LOW_IMPACT_OPERATION.operation_id: (FIXTURE_LOW_IMPACT_OPERATION, adapter),
    })
    async def unused(*_a, **_kw):
        raise RuntimeError("run_not_required_for_seeded_fixture")
    ports = TaskRuntimePorts(*([unused] * 10))
    app = StateDrivenTaskApplication(
        ports, start_runner=unused, resume_runner=unused,
        review_runner=unused, action_service=service,
    )

    def seed(*, allow_low=False, external_actions=True):
        task_id = uuid4()
        scope = {
            "external_actions": external_actions,
            "external_actions_low_impact": allow_low,
        }
        checkpoint = {
            "core_slice": CORE_SLICE,
            "magi_session": {
                "status": "proposal_ready",
                "detail": {"state": "ACTION_PROPOSAL",
                           "action_candidate": "Change disposable fixture setting"},
            },
        }
        with connect() as db, db.transaction(), db.cursor() as cur:
            cur.execute(
                """INSERT INTO secretary.tasks
                   (id, request, requested_by, domain, completion_criteria,
                    permission_scope, status, checkpoint)
                   VALUES (%s, 'Test a registered disposable fixture',
                           'isolated_test', 'test', 'Verify exact readback',
                           %s, 'running', %s)""",
                (task_id, Jsonb(scope), Jsonb(checkpoint)),
            )
        return task_id

    def inspect(task_id, *, status, risk=None):
        with connect() as db, db.cursor() as cur:
            cur.execute("SELECT status FROM secretary.tasks WHERE id=%s", (task_id,))
            assert cur.fetchone()[0] == status
            cur.execute(
                """SELECT risk, status, authorization_basis
                   FROM secretary.actions WHERE task_id=%s""", (task_id,),
            )
            actions = cur.fetchall()
            if risk is not None:
                assert len(actions) == 1 and actions[0][0] == risk, actions
            cur.execute(
                """SELECT COUNT(*) FROM secretary.results r
                   JOIN secretary.actions a ON a.id=r.action_id
                   WHERE a.task_id=%s""", (task_id,),
            )
            count = cur.fetchone()[0]
            cur.execute(
                "SELECT COUNT(*) FROM secretary.audit_events WHERE task_id=%s",
                (task_id,),
            )
            audit = cur.fetchone()[0]
        return actions, count, audit

    # High-impact: cannot dispatch before an individual trusted decision.
    high = seed()
    offered = await app.propose_action(
        task_id=high, operation_id=FIXTURE_OPERATION.operation_id,
        value="high-verified",
    )
    assert offered["status"] == "pending"
    approval = UUID(offered["approval_id"])
    try:
        await app.execute_action(task_id=high, approval_id=approval)
        raise AssertionError("high-impact dispatched without approval")
    except ValueError:
        assert server.writes == 0
    await app.decide_action(
        task_id=high, approval_id=approval, approved=True, actor="local_user",
    )
    completed = await app.execute_action(task_id=high, approval_id=approval)
    assert completed["verified"] is True and completed["task_status"] == "completed"
    actions, result_count, audit_count = inspect(high, status="completed", risk="high")
    assert actions[0][2] == "explicit_external_action_approval"
    assert result_count == 1 and audit_count >= 3
    assert server.writes == 1
    try:
        await app.execute_action(task_id=high, approval_id=approval)
        raise AssertionError("replayed high-impact Action")
    except ValueError:
        assert server.writes == 1

    # Low-impact: only a preauthorized persisted Task, with no manual decision.
    low = seed(allow_low=True)
    offered = await app.propose_action(
        task_id=low, operation_id=FIXTURE_LOW_IMPACT_OPERATION.operation_id,
        value="low-verified",
    )
    assert offered["status"] == "approved"
    low_approval = UUID(offered["approval_id"])
    low_result = await app.execute_action(task_id=low, approval_id=low_approval)
    assert low_result["verified"] and low_result["task_status"] == "completed"
    actions, result_count, audit_count = inspect(low, status="completed", risk="low")
    assert actions[0][2] == "task_scoped_low_impact_policy"
    assert result_count == 1 and audit_count >= 3
    assert server.writes == 2

    # A registered low-impact descriptor never grants its own authorization.
    denied = seed(allow_low=False)
    try:
        await app.propose_action(
            task_id=denied, operation_id=FIXTURE_LOW_IMPACT_OPERATION.operation_id,
            value="must-not-send",
        )
        raise AssertionError("low-impact offer bypassed Task scope")
    except ValueError:
        assert server.writes == 2
    assert inspect(denied, status="running")[1] == 0

    # A mismatch cannot count as completed.
    server.mismatch = True
    unknown = seed(allow_low=True)
    offered = await app.propose_action(
        task_id=unknown, operation_id=FIXTURE_LOW_IMPACT_OPERATION.operation_id,
        value="not-verified",
    )
    result = await app.execute_action(
        task_id=unknown, approval_id=UUID(offered["approval_id"]),
    )
    assert result["verified"] is False
    assert result["task_status"] == "waiting_external"
    assert inspect(unknown, status="waiting_external", risk="low")[1] == 1
    server.mismatch = False

    # Simulated interruption after reservation: record UNKNOWN; never resend.
    interrupted = seed()
    offered = await app.propose_action(
        task_id=interrupted, operation_id=FIXTURE_OPERATION.operation_id,
        value="interrupted",
    )
    interrupted_approval = UUID(offered["approval_id"])
    await app.decide_action(
        task_id=interrupted, approval_id=interrupted_approval,
        approved=True, actor="local_user",
    )
    store.claim(task_id=interrupted, approval_id=interrupted_approval)
    writes_before = server.writes
    store.mark_unknown(
        task_id=interrupted, approval_id=interrupted_approval,
        error_type="SimulatedProcessInterruption",
    )
    assert inspect(interrupted, status="waiting_external", risk="high")[1] == 1
    try:
        await app.execute_action(task_id=interrupted, approval_id=interrupted_approval)
        raise AssertionError("replayed interrupted Action")
    except ValueError:
        assert server.writes == writes_before
    print("PASS: isolated production-schema Action policy, reservation, readback, audit and UNKNOWN")


def main():
    if len(sys.argv) != 3:
        raise RuntimeError("isolated_project_and_container_required")
    port = checked_disposable_port(sys.argv[1], sys.argv[2])
    password = (ROOT / "secrets/postgres-password.txt").read_text(encoding="utf-8").strip()
    if len(password) < 32:
        raise RuntimeError("isolated_password_invalid")

    def connect():
        return psycopg.connect(
            host="127.0.0.1", port=port, dbname="secretary",
            user="secretary_admin", password=password, connect_timeout=5,
        )

    http = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    http.lock = threading.Lock()
    http.value = "initial"
    http.writes = 0
    http.mismatch = False
    thread = threading.Thread(target=http.serve_forever, daemon=True)
    thread.start()
    try:
        asyncio.run(verify(connect, http))
    finally:
        http.shutdown()
        http.server_close()
        thread.join(timeout=5)


if __name__ == "__main__":
    main()
