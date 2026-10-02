"""Static guardrails for the read-only MCP adapter."""
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
SERVER = (ROOT / "mcp_adapter" / "server.py").read_text(encoding="utf-8")


class McpAdapterStaticTests(unittest.TestCase):
    def test_exposes_only_initial_read_tools(self):
        self.assertIn("def tasks(", SERVER)
        self.assertIn("def memory_search(", SERVER)
        self.assertNotIn("@mcp.tool()\ndef create_", SERVER)
        self.assertNotIn("@mcp.tool()\ndef run_", SERVER)

    def test_uses_external_read_rest_boundary(self):
        self.assertIn("secretary-external-read-token.txt", SERVER)
        self.assertIn('method="GET"', SERVER)
        self.assertNotIn("psycopg", SERVER)

    def test_stdio_entrypoint_has_no_stdout_logging(self):
        self.assertIn("mcp.run()", SERVER)
        self.assertNotIn("print(", SERVER)


if __name__ == "__main__":
    unittest.main()
