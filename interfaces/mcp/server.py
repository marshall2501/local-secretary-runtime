"""Read-only MCP adapter for external Secretary access.

The adapter deliberately reuses the already verified external-read REST boundary.
It does not receive PostgreSQL credentials and exposes no write/control tools.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from mcp.server.mcpserver import MCPServer

DEFAULT_API_BASE_URL = "http://127.0.0.1:8010"
DEFAULT_TOKEN_FILE = Path("secrets/secretary-external-read-token.txt")

mcp = MCPServer("local-secretary-read")


def _api_base_url() -> str:
    return os.environ.get("LSA_MCP_API_BASE_URL", DEFAULT_API_BASE_URL).rstrip("/")


def _token() -> str:
    configured = os.environ.get("LSA_MCP_EXTERNAL_READ_TOKEN_FILE")
    path = Path(configured) if configured else DEFAULT_TOKEN_FILE
    token = path.read_text(encoding="utf-8").strip()
    if len(token) < 32:
        raise RuntimeError("external read token must be at least 32 characters")
    return token


def _get(path: str, params: dict[str, Any]) -> Any:
    query = urllib.parse.urlencode(
        {key: value for key, value in params.items() if value is not None},
        doseq=True,
    )
    url = f"{_api_base_url()}{path}"
    if query:
        url += f"?{query}"
    request = urllib.request.Request(
        url,
        headers={"Authorization": f"Bearer {_token()}", "Accept": "application/json"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Secretary API HTTP {exc.code}: {body}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Secretary API unavailable: {exc.reason}") from exc


@mcp.tool()
def tasks(status: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
    """List Secretary tasks. Read-only."""
    if not 1 <= limit <= 200:
        raise ValueError("limit must be between 1 and 200")
    return _get("/tasks", {"status": status, "limit": limit})


@mcp.tool()
def memory_search(
    q: str | None = None,
    domain: str | None = None,
    kind: str | None = None,
    include_history: bool = False,
    limit: int = 20,
    offset: int = 0,
) -> dict[str, Any]:
    """Search structured Secretary memory. Read-only and deterministic."""
    if kind is not None and kind not in {"claim", "issue", "hypothesis", "source"}:
        raise ValueError("kind must be claim, issue, hypothesis, or source")
    if not 1 <= limit <= 200:
        raise ValueError("limit must be between 1 and 200")
    if offset < 0:
        raise ValueError("offset must be >= 0")
    return _get(
        "/memory/search",
        {
            "q": q,
            "domain": domain,
            "kind": kind,
            "include_history": str(include_history).lower(),
            "limit": limit,
            "offset": offset,
        },
    )


if __name__ == "__main__":
    mcp.run()
