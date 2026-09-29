from __future__ import annotations

import unittest
from unittest.mock import patch

from pkb_proto.web_research import (
    MAX_EXCERPT_CHARS,
    _safe_external_url,
    research_web,
)


class _FakeDDGS:
    def __init__(self, timeout=8):
        self.timeout = timeout
        self.extract_calls = []

    def text(self, query, **kwargs):
        return [
            {
                "title": "Official Example",
                "href": "https://example.com/official",
                "body": "Official result snippet",
            },
            {
                "title": "Second Example",
                "href": "https://example.org/second",
                "body": "Second result snippet",
            },
            {
                "title": "Blocked",
                "href": "http://127.0.0.1/private",
                "body": "Must not fetch local addresses",
            },
        ]

    def extract(self, url, fmt="text_plain"):
        self.extract_calls.append(url)
        return {"url": url, "content": "x" * (MAX_EXCERPT_CHARS + 100)}


class WebResearchTests(unittest.TestCase):
    def test_safe_external_url_rejects_local_and_private_targets(self):
        self.assertFalse(_safe_external_url("http://127.0.0.1/test"))
        self.assertFalse(_safe_external_url("http://localhost/test"))
        self.assertFalse(_safe_external_url("http://192.168.1.10/test"))
        self.assertFalse(_safe_external_url("file:///etc/passwd"))
        self.assertTrue(_safe_external_url("https://example.com/test"))

    @patch("pkb_proto.web_research.DDGS", _FakeDDGS)
    def test_research_is_bounded_and_truncates_fetched_content(self):
        result = research_web(
            "example query",
            max_results=3,
            max_fetches=2,
            region="jp-jp",
        ).as_dict()
        self.assertEqual(result["provider"], "ddgs")
        self.assertEqual(result["query"], "example query")
        self.assertEqual(result["region"], "jp-jp")
        self.assertEqual(result["total"], 3)
        self.assertEqual(result["hits"][0]["fetch_status"], "fetched")
        self.assertEqual(len(result["hits"][0]["excerpt"]), MAX_EXCERPT_CHARS)
        self.assertEqual(result["hits"][1]["fetch_status"], "fetched")
        self.assertEqual(result["hits"][2]["fetch_status"], "blocked_url")

    @patch("pkb_proto.web_research.DDGS", _FakeDDGS)
    def test_query_and_result_bounds_fail_closed(self):
        with self.assertRaises(ValueError):
            research_web("", max_results=3)
        with self.assertRaises(ValueError):
            research_web("x" * 501, max_results=3)
        with self.assertRaises(ValueError):
            research_web("query", max_results=6)
        with self.assertRaises(ValueError):
            research_web("query", max_fetches=3)


if __name__ == "__main__":
    unittest.main()
