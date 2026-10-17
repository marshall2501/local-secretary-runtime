# Provenance-Origin: original
# Provenance-AI: AI-generated
"""Historical read-only Vue launcher: delegates all HTTP behavior to Secretary.

The canonical API/HTTP implementation is interfaces.api.secretary_server.
This wrapper survives only until the unified daily launcher is accepted.
"""
from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI

from interfaces.api.secretary_server import (
    JudgmentPreferencesBody,
    create_app as create_secretary_app,
)
from ritsuko.application.task_commands import TaskCommands


def create_app(*, queries, commands: TaskCommands | None = None,
               read_service=None,
               recover_pending_at_startup: bool = True) -> FastAPI:
    """Keep old isolated preview/test imports working without a second Router.

    Args:
        queries: Persisted Task query service.
        commands: Optional commands for historical preview tests.
        read_service: Shared independent PKB search application.
        recover_pending_at_startup: Preserve legacy startup semantics.

    Returns:
        FastAPI: The same canonical HTTP host as the new Secretary Server.
    """
    return create_secretary_app(
        queries=queries, commands=commands,
        read_service=read_service,
        recover_pending_at_startup=recover_pending_at_startup,
        dist_dir=Path(__file__).parent / "frontend" / "dist",
    )


def main() -> None:
    """Start the historical preview only; new features use the unified host."""
    from bootstrap.web_runtime import (
        build_core_task_queries, build_daily_memory_read_service,
    )
    from interfaces.vue_daily.server_logging import build_vue_log_config
    import uvicorn

    port = int(os.getenv("LSA_VUE_DAILY_PORT", "8097"))
    if port in {8010, 8015, 8092, 8093, 8098} or not 1024 <= port <= 65535:
        raise RuntimeError("invalid legacy Vue preview port")
    uvicorn.run(
        create_app(
            queries=build_core_task_queries(),
            read_service=build_daily_memory_read_service(),
        ), host="127.0.0.1", port=port,
        log_config=build_vue_log_config(server_name="vue-readonly-8097"),
    )


if __name__ == "__main__":
    main()
