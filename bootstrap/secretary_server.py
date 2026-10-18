# Provenance-Origin: original
# Provenance-AI: AI-generated
"""Composition root and executable entry for the unified local Secretary Server.

This process serves Vue and owner Task and PKB read/write routes through a single
FastAPI application. Importing this module does not connect to a real DB.
"""
from __future__ import annotations

import os
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

def external_read_token() -> str | None:
    """Read the existing local MCP read credential, if configured.

    The missing credential leaves compatibility routes unavailable until
    an actual caller is migrated. This never changes external clients.
    """
    raw = os.environ.get("LSA_EXTERNAL_READ_TOKEN_FILE", "").strip()
    file = Path(raw) if raw else ROOT / "secrets" / "secretary-external-read-token.txt"
    if not file.is_file():
        return None
    return file.read_text(encoding="utf-8").strip() or None



def configure_daily_database(root: Path = ROOT) -> None:
    """Load the existing daily DB location without reading or printing secrets.

    Args:
        root: Existing Secretary runtime source directory.

    Raises:
        RuntimeError: Required local database setting or secret is missing.
    """
    env_file = root / ".env.postgres"
    try:
        lines = env_file.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError as exc:
        raise RuntimeError("daily database settings file missing") from exc
    ports = [s.partition("=")[2] for s in lines if s.startswith("LSA_DB_PORT=")]
    if len(ports) != 1 or not ports[0].isdigit() or not 1024 <= int(ports[0]) <= 65535:
        raise RuntimeError("expected exactly one valid LSA_DB_PORT setting")

    secret = root / "secrets" / "secretary-daily-runtime-password.txt"
    if not secret.is_file():
        raise RuntimeError("daily runtime secret file missing")

    os.environ.update({
        "LSA_DAILY_DB_NAME": "secretary",
        "LSA_PKB_DAILY_PORT": ports[0],
        "LSA_PKB_DAILY_SECRET": str(secret),
    })


def build_daily_server():
    """Compose Task read/write and independent PKB read into one ASGI host.

    Returns:
        FastAPI: Canonical owner-local Secretary Server, with Task commands.

    Constructing this does not start Task work or modify the database.
    """
    # DB module constants are resolved at import time, so the calling main()
    # sets the environment first. No prototype/preview/permission-mode selector.
    from bootstrap.magi_task_runtime import build_daily_magi_task_commands
    from bootstrap.web_runtime import (
        build_core_task_queries, build_daily_memory_read_service,
        build_pkb_repository,
    )
    from interfaces.api.secretary_server import create_app
    from pkb.daily_interpreter import interpret as interpret_daily

    return create_app(
        queries=build_core_task_queries(),
        commands=build_daily_magi_task_commands(),
        read_service=build_daily_memory_read_service(),
        pkb_repository=build_pkb_repository(),
        pkb_interpreter=interpret_daily,
        external_read_token_loader=external_read_token,
        recover_pending_at_startup=False,
    )


def main() -> None:
    """Start only the unified localhost Server, without touching old services."""
    import uvicorn
    from infrastructure.secretary_logging import build_secretary_log_config

    configure_daily_database()
    port = int(os.environ.get("LSA_SECRETARY_PORT", "8097"))
    if not 1024 <= port <= 65535:
        raise RuntimeError("invalid LSA_SECRETARY_PORT")
    server = build_daily_server()
    uvicorn.run(
        server, host="127.0.0.1", port=port,
        log_config=build_secretary_log_config(),
    )


if __name__ == "__main__":
    main()
