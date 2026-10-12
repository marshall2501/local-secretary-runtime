"""Isolated PostgreSQL acceptance for runtime policy profiles (no cloud requests).

Run only from tests/db/test-integration.ps1 in a newly created disposable
Compose project; refuse any production or non-loopback database.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import psycopg

from infrastructure.postgres.runtime_policy_repository import PostgresRuntimePolicyRepository
from ritsuko.application.context_policy import restrict_members


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
    _, policy = restrict_members(candidate(masked), masked)
    assert policy["redaction_tokens"] == ["CODE-123"]
    assert "CODE-123" not in str(candidate(masked)["observations"][0].get("source"))

    production_id = next(row["id"] for row in profiles
                         if row["name"] == "production-lan")
    reverted = repo.activate("lan", production_id)
    assert reverted["profile_name"] == "production-lan"
    assert len(repo.list_grants(production_id)) >= 2
    print("PASS: isolated migration 009, policy repository, grants and profile activation.")


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
