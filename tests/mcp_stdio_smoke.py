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
            task_result = await session.call_tool("tasks", {"limit": 1})

    if task_result.is_error:
        raise RuntimeError(f"tasks MCP call failed: {task_result.content!r}")
    if not task_result.structured_content:
        raise RuntimeError(f"tasks MCP call returned no structured content: {task_result.content!r}")
    value = task_result.structured_content.get("value")
    if not isinstance(value, list):
        raise RuntimeError(f"unexpected tasks payload: {task_result.structured_content!r}")

    names = [tool.name for tool in result.tools]
    expected = ["memory_search", "tasks"]
    if sorted(names) != expected:
        raise RuntimeError(f"unexpected MCP tools: {names!r}")
    print("MCP stdio discovery OK:", ", ".join(sorted(names)))
    print(f"MCP tasks call OK: {len(value)} task(s) returned")


if __name__ == "__main__":
    asyncio.run(main())
