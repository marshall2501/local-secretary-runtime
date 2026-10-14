"""Isolated PostgreSQL acceptance for runtime policy profiles (no cloud requests).

Run only from tests/db/test-integration.ps1 in a newly created disposable
Compose project; refuse any production or non-loopback database.
"""
from __future__ import annotations

import asyncio
from copy import deepcopy
import json
import re
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import psycopg

from infrastructure.postgres.runtime_policy_repository import PostgresRuntimePolicyRepository
from ritsuko.application.context_policy import restrict_members, redact_observation_text
from infrastructure.postgres.magi_task_repository import PostgresMagiTaskRepository
from ritsuko.magi.async_execution import continue_with_user_clarification_async
from scripts.db.probe_magi_task_resume_rollback import exercise_transaction


def verify_fixture_identity(project: str, container: str) -> int:
    if not re.fullmatch(r"local-secretary-test-[0-9a-f]{12}", project):
        raise RuntimeError("not_disposable_project")
    if not re.fullmatch(r"[0-9a-f]{12,64}", container):
        raise RuntimeError("invalid_container")
    metadata = json.loads(subprocess.check_output(
        ["docker", "inspect", container], text=True
    ))[0]
    if (
        metadata["Config"]["Labels"].get("com.docker.compose.project") != project
        or metadata["Config"]["Labels"].get("com.docker.compose.service") != "secretary-postgres"
        or metadata["State"]["Status"] != "running"
    ):
        raise RuntimeError("not_isolated_postgres")
    bindings = metadata["NetworkSettings"]["Ports"]["5432/tcp"]
    if len(bindings) != 1 or bindings[0]["HostIp"] != "127.0.0.1":
        raise RuntimeError("non_loopback_postgres")
    port = int(bindings[0]["HostPort"])
    if (ROOT / ".env.postgres").read_text(encoding="utf-8").strip() != f"LSA_DB_PORT={port}":
        raise RuntimeError("disposable_port_mismatch")
    return port


def verify(connect):
    repo = PostgresRuntimePolicyRepository(connect)
    profiles = repo.list_profiles()
    labels = repo.list_labels()
    assert len(profiles) == 5, "seeded_profiles_missing"
    assert {"llm.send_context.private", "llm.send_context.sensitive"}.issubset(
        {row["label"] for row in labels}
    )
    production = repo.load_snapshot("lan")
    assert production["profile_name"] == "production-lan"
    specs = [
        {"name": "MELCHIOR", "provider": "ollama", "enabled": True,
         "endpoint": "http://secretary-ollama:11434"},
        {"name": "CASPER", "provider": "openai", "enabled": True,
         "endpoint": "https://api.openai.com/v1"},
    ]
    def candidate(snapshot):
        return {"policy_snapshot": snapshot, "member_specs": specs,
                "observations": [{"source": "pkb", "verified": True,
                                  "confidentiality": "private", "text": "CODE-123"}],
                "status": "running"}
    allowed, _ = restrict_members(candidate(production), production)
    assert [x["name"] for x in allowed] == ["MELCHIOR"]

    verification_id = next(row["id"] for row in profiles
                           if row["name"] == "verification-lan")
    verification = repo.activate("lan", verification_id)
    assert verification["profile_name"] == "verification-lan"
    allowed, _ = restrict_members(candidate(verification), verification)
    assert [x["name"] for x in allowed] == ["MELCHIOR", "CASPER"]

    repo.save_grant(
        verification_id, "llm.send_context.private", "remote", "redact",
        mask_tokens=["CODE-123"],
    )
    masked = repo.load_snapshot("lan")
    assert masked["revision"] > verification["revision"]
    members, policy = restrict_members(candidate(masked), masked)
    assert [x["name"] for x in members] == ["MELCHIOR", "CASPER"]
    assert policy["redaction_tokens"] == []
    assert redact_observation_text("CODE-123", policy["redaction_tokens"]) == "CODE-123"
    assert masked["profile_name"] == "verification-lan"

    production_id = next(row["id"] for row in profiles
                         if row["name"] == "production-lan")
    reverted = repo.activate("lan", production_id)
    assert reverted["profile_name"] == "production-lan"
    assert len(repo.list_grants(production_id)) >= 2
    verify_saved_task_resume_snapshot(connect, repo)
    # Reuse the exact production-role probe logic against this disposable DB.
    # Explicit ROLLBACK must leave no synthetic Task or audit events.
    with connect() as check:
        with check.cursor() as cur:
            cur.execute("SELECT count(*) FROM secretary.tasks")
            tasks_before = cur.fetchone()[0]
            cur.execute("SELECT count(*) FROM secretary.audit_events")
            audit_before = cur.fetchone()[0]
    disposable = connect()
    try:
        disposable.autocommit = True
        disposable.execute("BEGIN")
        current_name, allowed = exercise_transaction(disposable)
        assert current_name == "production-lan"
        assert allowed == ["MELCHIOR"]
    finally:
        disposable.rollback()
        disposable.close()
    with connect() as check:
        with check.cursor() as cur:
            cur.execute("SELECT count(*) FROM secretary.tasks")
            assert cur.fetchone()[0] == tasks_before, "rollback_left_task"
            cur.execute("SELECT count(*) FROM secretary.audit_events")
            assert cur.fetchone()[0] == audit_before, "rollback_left_audit"
    print("PASS: rollback-only operational-style synthetic Task probe.")
    print("PASS: isolated migration 009, policy repository, grants and profile activation.")



def verify_saved_task_resume_snapshot(connect, policy_repo):
    """Exercise the persisted JSONB Task after profile changes without real LLMs."""
    task_repo = PostgresMagiTaskRepository(connect)
    profiles = {item["name"]: item["id"] for item in policy_repo.list_profiles()}
    specs = [
        {"name": "MELCHIOR", "provider": "ollama", "model": "fixture-local",
         "endpoint": "http://secretary-ollama:11434", "enabled": True},
        {"name": "CASPER", "provider": "openai", "model": "fixture-cloud",
         "endpoint": "https://api.openai.com/v1", "enabled": True},
        {"name": "BALTHASAR", "provider": "gemini", "model": "fixture-cloud",
         "endpoint": "https://generativelanguage.googleapis.com/v1beta",
         "enabled": True},
    ]
    cases = (
        ("production-lan", "verification-lan", ["MELCHIOR"]),
        ("verification-lan", "production-lan",
         ["MELCHIOR", "CASPER", "BALTHASAR"]),
    )
    try:
        for original_name, switched_name, expected in cases:
            saved_policy = policy_repo.activate("lan", profiles[original_name])
            task_id = uuid4()
            session = {
                "task_id": str(task_id), "status": "waiting_user",
                "next_step": "consider_user_question",
                "user_question": "Synthetic clarification?",
                "user_raw": "Synthetic task without personal data.",
                "prompt_version": "test", "model": "", "turn_limit": 4,
                "turns": [],
                "classification": {
                    "category": "INFORMATION",
                    "understood_request": "synthetic policy test",
                },
                "detail": {"state": "NEED_INFORMATION"},
                "observations": [{
                    "source": "pkb", "verified": True,
                    "confidentiality": "private",
                    "text": "FICTIONAL-PRIVATE-OBSERVATION",
                }],
                "member_specs": deepcopy(specs),
                "policy_snapshot": deepcopy(saved_policy),
                "pending_requests": [],
                "conversation_context": [],
                "tool_read_executed": True,
            }
            task_repo.create_task(
                task_id=task_id, request=session["user_raw"],
                member_specs=specs,
            )
            task_repo.persist_session(task_id=task_id, session=session)

            with connect() as db, db.cursor() as cur:
                cur.execute(
                    "SELECT status, checkpoint FROM secretary.tasks WHERE id=%s",
                    (task_id,),
                )
                status, checkpoint = cur.fetchone()
            assert status == "waiting_external"
            assert checkpoint["magi_session"]["policy_snapshot"] == saved_policy

            active = policy_repo.activate("lan", profiles[switched_name])
            assert active["profile_id"] != saved_policy["profile_id"]
            loaded, capability = task_repo.claim_user_resume(
                task_id=task_id, reply_length=18, reply_fingerprint="a" * 64,
            )
            assert capability is None
            assert loaded["policy_snapshot"] == saved_policy

            captured = []

            async def fake_panel(envelope, *, model, timeout, member_specs):
                captured.append([member["name"] for member in member_specs])
                return {
                    "status": "unavailable", "response": None,
                    "errors": ["fixture_termination"], "diagnostic": {},
                }

            resumed = asyncio.run(continue_with_user_clarification_async(
                loaded, "fictional answer", timeout=1, caller=fake_panel,
            ))
            assert captured == [expected], "resumed_task_policy_changed"
            assert resumed["policy_snapshot"] == saved_policy
            assert (resumed["turns"][0]["context_policy"]["profile_id"]
                    == saved_policy["profile_id"])
            task_repo.persist_session(task_id=task_id, session=resumed)
            with connect() as db, db.cursor() as cur:
                cur.execute(
                    "SELECT checkpoint->'magi_session'->'policy_snapshot' "
                    "FROM secretary.tasks WHERE id=%s", (task_id,),
                )
                assert cur.fetchone()[0] == saved_policy
    finally:
        policy_repo.activate("lan", profiles["production-lan"])
    print("PASS: isolated Task JSONB save, switch, resume and preserved Policy (both directions).")



def main():
    if len(sys.argv) != 3:
        raise RuntimeError("disposable_project_and_container_required")
    port = verify_fixture_identity(sys.argv[1], sys.argv[2])
    password = (ROOT / "secrets/postgres-password.txt").read_text(encoding="utf-8").strip()
    if len(password) < 32:
        raise RuntimeError("invalid_disposable_password")
    def connect():
        return psycopg.connect(
            host="127.0.0.1", port=port, dbname="secretary",
            user="secretary_admin", password=password, connect_timeout=5,
        )
    verify(connect)


if __name__ == "__main__":
    main()
