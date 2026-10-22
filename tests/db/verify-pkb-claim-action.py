# Provenance-Origin: original
# Provenance-AI: AI-assisted
"""Disposable-PostgreSQL acceptance for the guarded PKB claim Action store.

Only a test-owned Docker project may be used. No PKB claim is actually written
by this script; it checks pre-dispatch reservation, privilege and Task Results.
"""
from __future__ import annotations

import sys
from pathlib import Path
from uuid import UUID, uuid4
import runpy

import psycopg

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from infrastructure.postgres.magi_task_repository import PostgresMagiTaskRepository
from infrastructure.postgres.pkb_claim_action_repository import PostgresPkbClaimActionStore

PORT_CHECK = runpy.run_path(
    str(Path(__file__).with_name("verify-durable-task-start.py"))
)["disposable_port"]

TEXT = "架空GPUをDRV-200へ更新した。"
ARGS = {
    "entity_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
    "entity_mention": "架空GPU",
    "predicate": "driver_updated",
    "value": "DRV-200",
    "evidence_start": 0, "evidence_end": len(TEXT),
}
MEMBERS = [{
    "name": "MELCHIOR", "model": "synthetic", "provider": "ollama",
    "enabled": True, "timeout_seconds": 5,
}]
POLICY = {"profile_id": "disposable", "revision": 1, "grants": []}


def run(connect):
    repo = PostgresMagiTaskRepository(connect)
    allowed_repo = PostgresMagiTaskRepository(connect, allow_pkb_write=True)
    store = PostgresPkbClaimActionStore(connect)

    def seed(*, authorized):
        task_id = uuid4()
        # Only the opted-in daily composition writes the permission at INSERT.
        target_repo = allowed_repo if authorized else repo
        assert target_repo.reserve_start(
            task_id=task_id, request=TEXT, member_specs=MEMBERS,
            decision_strategy="llm", policy_snapshot=POLICY,
        )
        attempt_id = target_repo.claim_start(
            task_id=task_id, request=TEXT, member_specs=MEMBERS,
        )
        with connect() as db, db.cursor() as cur:
            cur.execute("SELECT permission_scope FROM secretary.tasks WHERE id=%s", (task_id,))
            permissions = cur.fetchone()[0]
            assert (permissions.get("pkb_write") is True) == authorized
        request_id = f"REQ-{str(task_id)[:8]}-01-01"
        return task_id, attempt_id, request_id

    denied, attempt, request_id = seed(authorized=False)
    try:
        store.reserve(
            task_id=denied, attempt_id=attempt, request_id=request_id,
            user_text=TEXT, arguments=ARGS,
        )
        raise AssertionError("unauthorized PKB Action was reserved")
    except ValueError as exc:
        assert str(exc) == "pkb_write_not_permitted", exc

    first, attempt, request_id = seed(authorized=True)
    reserved = store.reserve(
        task_id=first, attempt_id=attempt, request_id=request_id,
        user_text=TEXT, arguments=ARGS,
    )
    action_id = reserved["action_id"]
    assert reserved["recorded_at"].utcoffset() is not None
    try:
        store.reserve(
            task_id=first, attempt_id=attempt, request_id=request_id,
            user_text=TEXT, arguments=ARGS,
        )
        raise AssertionError("duplicate write Action was reserved")
    except ValueError as exc:
        assert str(exc) == "pkb_write_already_reserved", exc
    action, result = store.finish(
        task_id=first, attempt_id=attempt, action_id=UUID(action_id),
        execution={"status": "review", "reason": "ambiguous_fixture_entity"},
    )
    assert action == action_id and result
    with connect() as db, db.cursor() as cur:
        cur.execute(
            """SELECT a.status, a.risk, r.outcome, t.status
               FROM secretary.actions a
               JOIN secretary.results r ON r.action_id=a.id
               JOIN secretary.tasks t ON t.id=a.task_id
               WHERE a.id=%s""",
            (action_id,),
        )
        assert cur.fetchone() == ("failed", "low", "inconclusive", "running")
    try:
        store.finish(
            task_id=first, attempt_id=attempt,
            action_id=UUID(action_id),
            execution={"status": "review", "reason": "repeat"},
        )
        raise AssertionError("duplicate result was committed")
    except ValueError as exc:
        assert str(exc) == "pkb_write_action_not_running", exc

    interrupted, attempt, request_id = seed(authorized=True)
    reserved = store.reserve(
        task_id=interrupted, attempt_id=attempt,
        request_id=request_id, user_text=TEXT, arguments=ARGS,
    )
    action_id = UUID(reserved["action_id"])
    action, result = store.mark_unknown(
        task_id=interrupted, action_id=action_id,
        error_type="SimulatedInterruptedAfterDispatch",
    )
    assert action == str(action_id) and result
    with connect() as db, db.cursor() as cur:
        cur.execute(
            """SELECT a.status,r.outcome
               FROM secretary.actions a JOIN secretary.results r ON r.action_id=a.id
               WHERE a.id=%s""",
            (action_id,),
        )
        assert cur.fetchone() == ("unknown", "inconclusive")
    print("PASS: isolated new Task PKB permission/Action/replay/Result/unknown")


def main():
    if len(sys.argv) != 3:
        raise RuntimeError("isolated_project_and_container_required")
    port = PORT_CHECK(sys.argv[1], sys.argv[2])
    password = (ROOT / "secrets/postgres-password.txt").read_text(encoding="utf-8").strip()
    if len(password) < 32:
        raise RuntimeError("isolated_password_invalid")

    def connect():
        return psycopg.connect(
            host="127.0.0.1", port=port, dbname="secretary",
            user="secretary_admin", password=password, connect_timeout=5,
        )

    run(connect)


if __name__ == "__main__":
    main()
