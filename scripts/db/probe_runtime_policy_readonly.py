"""Read-only active policy + synthetic MAGI authorization acceptance probe.

This script never queries PKB/Finance/Tasks or invokes an LLM. It connects only
to the locally configured production database as the existing daily-runtime
role, in a read-only session. Do not print credentials, raw grants or DB rows.
Run from the repository root with its existing virtual environment.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import psycopg

from infrastructure.postgres.runtime_policy_repository import (
    PostgresRuntimePolicyRepository,
)
from ritsuko.application.context_policy import restrict_members

EXPECTED_MEMBERS = ["MELCHIOR", "CASPER", "BALTHASAR"]


def configured_port(file: Path) -> int:
    """Read the existing dedicated port setting, never a shell override."""
    if not file.is_file():
        raise RuntimeError("local PostgreSQL port configuration missing")
    matches = [
        line.strip().split("=", 1)[1]
        for line in file.read_text(encoding="utf-8").splitlines()
        if re.fullmatch(r"LSA_DB_PORT=[0-9]+", line.strip())
    ]
    if len(matches) != 1:
        raise RuntimeError("local PostgreSQL port configuration invalid")
    port = int(matches[0])
    if not 1024 <= port <= 65535:
        raise RuntimeError("local PostgreSQL port out of range")
    return port


def read_runtime_secret(file: Path) -> str:
    """Use the already-provisioned account; never create or log a secret."""
    if not file.is_file():
        raise RuntimeError("daily runtime secret file missing")
    password = file.read_text(encoding="utf-8").strip()
    if len(password) < 32:
        raise RuntimeError("daily runtime secret invalid")
    return password


def evaluate_synthetic_observations(snapshot: dict) -> list[str]:
    """Evaluate real DB policy data against synthetic observations and endpoints."""
    session = {
        "status": "running",
        "observations": [
            {"source": "synthetic", "verified": True,
             "confidentiality": "private", "text": "FICTIONAL-PRIVATE"},
            {"source": "synthetic", "verified": True,
             "confidentiality": "sensitive", "text": "FICTIONAL-SENSITIVE"},
        ],
        "member_specs": [
            {"name": "MELCHIOR", "provider": "ollama",
             "endpoint": "http://secretary-ollama:11434", "enabled": True},
            {"name": "CASPER", "provider": "openai",
             "endpoint": "https://api.openai.com/v1", "enabled": True},
            {"name": "BALTHASAR", "provider": "gemini",
             "endpoint": "https://generativelanguage.googleapis.com",
             "enabled": True},
        ],
    }
    members, _context_policy = restrict_members(session, snapshot)
    names = [member["name"] for member in (members or [])]
    if snapshot.get("profile_name") != "verification-lan":
        raise RuntimeError("active LAN profile is not verification-lan")
    if names != EXPECTED_MEMBERS:
        raise RuntimeError("synthetic MAGI member authorization mismatch")
    return names


def main() -> None:
    port = configured_port(ROOT / ".env.postgres")
    password = read_runtime_secret(
        ROOT / "secrets" / "secretary-daily-runtime-password.txt"
    )

    def readonly_connection():
        db = psycopg.connect(
            host="127.0.0.1", port=port, dbname="secretary",
            user="secretary_daily_runtime", password=password,
            connect_timeout=5,
            options="-c default_transaction_read_only=on",
        )
        info = db.info
        if (
            info.dbname != "secretary"
            or info.user != "secretary_daily_runtime"
            or info.host not in {"127.0.0.1", "localhost", "::1"}
        ):
            db.close()
            raise RuntimeError("refusing unexpected database identity")
        try:
            with db.cursor() as cur:
                cur.execute("SHOW transaction_read_only")
                if cur.fetchone()[0] != "on":
                    raise RuntimeError("read-only database mode not confirmed")
        except Exception:
            db.close()
            raise
        return db

    repository = PostgresRuntimePolicyRepository(readonly_connection)
    snapshot = repository.load_snapshot("lan")
    names = evaluate_synthetic_observations(snapshot)
    print("Active profile:", snapshot["profile_name"])
    print("Allowed members:", ", ".join(names))
    print("PASS: read-only DB policy and synthetic MAGI authorization")
    print("No PKB read, DB write, or external LLM request was performed.")


if __name__ == "__main__":
    main()
