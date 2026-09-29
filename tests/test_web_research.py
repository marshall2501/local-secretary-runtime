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



class _DriverDDGS:
    def __init__(self, timeout=8):
        self.timeout = timeout

    def text(self, query, **kwargs):
        return [
            {
                "title": "Board Vendor Product Support",
                "href": "https://vendor.example.com/support/product",
                "body": "Radeon RX 9070 XT support page",
            },
            {
                "title": "AMD Radeon RX 9070 XT Drivers and Downloads | Latest Version",
                "href": "https://www.amd.com/en/support/downloads/drivers/radeon-rx-9070-xt.html",
                "body": "AMD Software: Adrenalin Edition driver version 26.9.1 released 2026-09-25.",
            },
            {
                "title": "AMD Radeon RX 9070 XT Drivers and Downloads | Previous Versions",
                "href": "https://www.amd.com/en/support/downloads/previous-drivers/radeon-rx-9070-xt.html",
                "body": "Previous driver version 26.8.1 released 2026-08-20.",
            },
        ]

    def extract(self, url, fmt="text_plain"):
        if "previous-drivers" in url:
            return {
                "url": url,
                "content": "Previous versions. Driver version 26.8.1. 2026-08-20.",
            }
        if "amd.com" in url:
            return {
                "url": url,
                "content": "Latest Version. AMD Software: Adrenalin Edition 26.9.1. 2026-09-25.",
            }
        return {"url": url, "content": "Generic board support."}


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

    @patch("pkb_proto.web_research.DDGS", _DriverDDGS)
    def test_driver_research_reranks_evidence_and_extracts_version_candidates(self):
        result = research_web(
            "RX 9070 XTの最新ドライバーをWebで調べて",
            max_results=3,
            max_fetches=2,
            region="jp-jp",
        ).as_dict()
        self.assertEqual(result["intent"], "latest_driver")
        self.assertEqual(result["hits"][0]["domain"], "www.amd.com")
        self.assertEqual(result["hits"][0]["evidence_rank"], 1)
        self.assertEqual(result["hits"][0]["fetch_status"], "fetched")
        self.assertEqual(
            result["hits"][0]["authority_hint"],
            "primary_driver_source_candidate",
        )
        self.assertIn("26.9.1", result["hits"][0]["version_candidates"])
        self.assertIn("2026-09-25", result["hits"][0]["date_hints"])
        self.assertEqual(
            result["fact_summary"]["best_candidate"],
            "26.9.1",
        )
        self.assertIn(
            result["fact_summary"]["status"],
            {"single_candidate", "leading_consensus"},
        )

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
