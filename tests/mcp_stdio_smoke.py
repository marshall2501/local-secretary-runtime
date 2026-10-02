"""Live stdio MCP smoke test: initialize server and discover read-only tools."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client


ROOT = Path(__file__).resolve().parents[1]


async def main() -> None:
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "mcp_adapter.server"],
        cwd=str(ROOT),
    )
    async with stdio_client(params) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            result = await session.list_tools()

    names = [tool.name for tool in result.tools]
    expected = ["memory_search", "tasks"]
    if sorted(names) != expected:
        raise RuntimeError(f"unexpected MCP tools: {names!r}")
    print("MCP stdio discovery OK:", ", ".join(sorted(names)))


if __name__ == "__main__":
    asyncio.run(main())
