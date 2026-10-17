"""Operational-schema MAGI Task save/resume probe: synthetic data, rollback only.

Reads the actual active LAN Policy; creates one synthetic Task and audit events
within a single PostgreSQL transaction; resumes via the normal Repository and
MAGI policy path with a mocked provider; verifies checkpoint round trips; then
always ROLLBACKs. No persistent Task, audit, profile change, PKB read, or LLM I/O.
This does not replace the committed cross-connection test on disposable CI DB.
"""
from __future__ import annotations

import asyncio
from contextlib import contextmanager
from copy import deepcopy
from hashlib import sha256
from pathlib import Path
import sys
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import psycopg

from scripts.db.probe_runtime_policy_readonly import (
    configured_port, read_runtime_secret,
)
from infrastructure.postgres.magi_task_repository import PostgresMagiTaskRepository
from infrastructure.postgres.runtime_policy_repository import (
    PostgresRuntimePolicyRepository,
)
from ritsuko.application.context_policy import restrict_members
from ritsuko.magi.session_workflow import continue_with_user_clarification_async

DBNAME = "secretary"
DBUSER = "secretary_daily_runtime"


def connect_existing_daily_database():
    """Reuse the provisioned local credentials; never touch another database."""
    port = configured_port(ROOT / ".env.postgres")
    password = read_runtime_secret(
        ROOT / "secrets" / "secretary-daily-runtime-password.txt"
    )
    conn = psycopg.connect(
        host="127.0.0.1", port=port, dbname=DBNAME, user=DBUSER,
        password=password, connect_timeout=5, autocommit=True,
    )
    info = conn.info
    if (
        info.dbname != DBNAME or info.user != DBUSER
        or info.host not in {"127.0.0.1", "localhost", "::1"}
    ):
        conn.close()
        raise RuntimeError("unexpected database identity")
    return conn


def synthetic_specs():
    return [
        {"name": "MELCHIOR", "provider": "ollama", "model": "fictional-local",
         "endpoint": "http://secretary-ollama:11434", "enabled": True},
        {"name": "CASPER", "provider": "openai", "model": "fictional-cloud",
         "endpoint": "https://api.openai.com/v1", "enabled": True},
        {"name": "BALTHASAR", "provider": "gemini", "model": "fictional-cloud",
         "endpoint": "https://generativelanguage.googleapis.com/v1beta",
         "enabled": True},
    ]


def synthetic_waiting_session(task_id, snapshot, specs):
    return {
        "task_id": str(task_id), "status": "waiting_user",
        "next_step": "consider_user_question",
        "user_question": "Fictional clarification?",
        "user_raw": "FICTIONAL TASK: NO PERSONAL INFORMATION",
        "prompt_version": "test", "model": "",
        "turn_limit": 4, "turns": [],
        "classification": {
            "category": "INFORMATION",
            "understood_request": "fictional policy snapshot probe",
        },
        "detail": {"state": "NEED_INFORMATION"},
        "observations": [{
            "source": "pkb", "verified": True, "confidentiality": "private",
            "text": "FICTIONAL-PRIVATE-OBSERVATION-001",
        }],
        "member_specs": deepcopy(specs),
        "policy_snapshot": deepcopy(snapshot),
        "pending_requests": [], "conversation_context": [],
        "tool_read_executed": True,
    }


def exercise_transaction(conn):
    """Operate only inside the caller-owned uncommitted transaction."""
    @contextmanager
    def borrow_connection():
        yield conn

    task_repo = PostgresMagiTaskRepository(borrow_connection)
    policy_repo = PostgresRuntimePolicyRepository(borrow_connection)
    snapshot = policy_repo.load_snapshot("lan")
    if not isinstance(snapshot, dict) or snapshot.get("exposure") != "lan":
        raise RuntimeError("invalid active LAN policy")

    task_id = uuid4()
    specs = synthetic_specs()
    original = synthetic_waiting_session(task_id, snapshot, specs)

    # All inserts/updates use the existing production Repository, but remain
    # invisible to other DB sessions and are rolled back by the caller.
    task_repo.create_task(
        task_id=task_id, request=original["user_raw"], member_specs=specs,
    )
    task_repo.persist_session(task_id=task_id, session=original)
    with conn.cursor() as cur:
        cur.execute(
            "SELECT status, checkpoint FROM secretary.tasks WHERE id=%s",
            (task_id,),
        )
        status, checkpoint = cur.fetchone()
    if status != "waiting_external":
        raise RuntimeError("synthetic Task did not enter waiting state")
    if checkpoint["magi_session"]["policy_snapshot"] != snapshot:
        raise RuntimeError("saved policy snapshot differs from active policy")

    response_text = "fictional answer"
    loaded, capability = task_repo.claim_user_resume(
        task_id=task_id, reply_length=len(response_text),
        reply_fingerprint=sha256(response_text.encode("utf-8")).hexdigest(),
    )
    if capability is not None or loaded["policy_snapshot"] != snapshot:
        raise RuntimeError("Task claim changed policy snapshot")

    expected, _ = restrict_members(deepcopy(loaded), snapshot)
    expected_names = [member["name"] for member in (expected or [])]
    observed = []

    async def fake_panel(envelope, *, model, timeout, member_specs):
        observed.append([member["name"] for member in member_specs])
        return {
            "status": "unavailable", "response": None,
            "errors": ["synthetic_test_end"], "diagnostic": {},
        }

    resumed = asyncio.run(continue_with_user_clarification_async(
        loaded, response_text, timeout=1, caller=fake_panel,
    ))
    if observed != [expected_names]:
        raise RuntimeError("MAGI resume used unexpected member permissions")
    if resumed["policy_snapshot"] != snapshot:
        raise RuntimeError("resume altered original snapshot")
    task_repo.persist_session(task_id=task_id, session=resumed)
    with conn.cursor() as cur:
        cur.execute(
            "SELECT checkpoint->'magi_session'->'policy_snapshot' "
            "FROM secretary.tasks WHERE id=%s",
            (task_id,),
        )
        stored = cur.fetchone()[0]
    if stored != snapshot:
        raise RuntimeError("re-persist changed original snapshot")
    return snapshot["profile_name"], expected_names


def main():
    conn = connect_existing_daily_database()
    result = None
    try:
        # BEGIN is deliberate: repository nested transactions use SAVEPOINTs
        # and are never committed into the persistent operational database.
        conn.execute("BEGIN")
        result = exercise_transaction(conn)
    except Exception as exc:
        print("FAIL:", type(exc).__name__)
        return 1
    finally:
        try:
            conn.rollback()
        finally:
            conn.close()

    profile, names = result
    print("Active LAN Policy:", profile)
    print("Resumed synthetic MAGI members:", ", ".join(names))
    print("PASS: operational DB Task JSONB save, reload, claim, policy resume and re-save")
    print("ROLLBACK: no synthetic Task or audit entries were retained.")
    print("No production Policy switch, PKB read, or external LLM request.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
