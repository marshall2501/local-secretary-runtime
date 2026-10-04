"""Daily Web runtime port contract."""
from __future__ import annotations

import os
from collections.abc import Mapping

DEFAULT_DAILY_WEB_PORT = 8093
MIN_DAILY_WEB_PORT = 1024
MAX_DAILY_WEB_PORT = 65535


def resolve_daily_web_port(env: Mapping[str, str] | None = None) -> int:
    source = os.environ if env is None else env
    raw = str(source.get("LSA_DAILY_WEB_PORT", "") or "").strip()
    if not raw:
        return DEFAULT_DAILY_WEB_PORT
    try:
        port = int(raw)
    except ValueError as exc:
        raise RuntimeError("LSA_DAILY_WEB_PORT is invalid") from exc
    if not MIN_DAILY_WEB_PORT <= port <= MAX_DAILY_WEB_PORT:
        raise RuntimeError("LSA_DAILY_WEB_PORT is outside the allowed range")
    return port
