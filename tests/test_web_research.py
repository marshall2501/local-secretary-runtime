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



class _MixedVersionDDGS:
    def __init__(self, timeout=8):
        self.timeout = timeout

    def text(self, query, **kwargs):
        return [
            {
                "title": "AMD Software: SI Driver for Radeon RX 9070 XT Driver Version Release Notes",
                "href": "https://www.amd.com/en/resources/support-articles/release-notes/si-driver.html",
                "body": "SI Driver Version 25.10.2 for Radeon RX 9070 XT.",
            },
            {
                "title": "Radeon RX 9070 XT",
                "href": "https://www.amd.com/en/products/graphics/radeon-rx-9070-xt.html",
                "body": "Radeon RX 9070 XT driver 26.5.2.",
            },
            {
                "title": "AMD Radeon RX 9070 XT Drivers and Downloads | Previous Versions",
                "href": "https://www.amd.com/en/support/downloads/previous-drivers/radeon-rx-9070-xt.html",
                "body": "Previous driver version 26.1.1.",
            },
        ]

    def extract(self, url, fmt="text_plain"):
        if "si-driver" in url:
            return {"url": url, "content": "SI Driver Version 25.10.2."}
        if "previous-drivers" in url:
            return {"url": url, "content": "Previous driver version 26.1.1."}
        return {"url": url, "content": "Radeon RX 9070 XT driver 26.5.2."}



class _SamePageMultipleAdrenalinDDGS:
    def __init__(self, timeout=8):
        self.timeout = timeout

    def text(self, query, **kwargs):
        return [
            {
                "title": "AMD Radeon RX 9070 XT Drivers and Downloads | Latest Version",
                "href": "https://www.amd.com/en/support/downloads/drivers/radeon-rx-9070-xt.html",
                "body": (
                    "AMD Software: Adrenalin Edition 26.8.1 release date 2026-08-20. "
                    "AMD Software: Adrenalin Edition 26.9.1 release date 2026-09-03."
                ),
            },
            {
                "title": "AMD Radeon RX 9070 XT Release Notes",
                "href": "https://www.amd.com/en/resources/support-articles/release-notes/rn-rad-win-26-9-1.html",
                "body": "Latest AMD Software: Adrenalin Edition 26.9.1 released 2026-09-03.",
            },
        ]

    def extract(self, url, fmt="text_plain"):
        if "rn-rad-win-26-9-1" in url:
            return {
                "url": url,
                "content": (
                    "Latest release. AMD Software: Adrenalin Edition 26.9.1. "
                    "Release date: 2026-09-03."
                ),
            }
        return {
            "url": url,
            "content": (
                "AMD Software: Adrenalin Edition 26.8.1. Release date: 2026-08-20. "
                "AMD Software: Adrenalin Edition 26.9.1. Release date: 2026-09-03."
            ),
        }



class _PrimaryWithoutVersionDDGS:
    def __init__(self, timeout=8):
        self.timeout = timeout

    def text(self, query, **kwargs):
        return [
            {
                "title": "AMD Radeon RX 9070 XT Drivers and Downloads - Latest Version",
                "href": "https://www.amd.com/en/support/downloads/drivers/radeon-rx-9070-xt.html",
                "body": "Official AMD driver download page for Radeon RX 9070 XT.",
            },
            {
                "title": "Latest AMD Radeon Graphics Drivers 26.9.1 WHQL Download",
                "href": "https://third.example.com/amd-driver-26-9-1",
                "body": "AMD Radeon driver version 26.9.1.",
            },
            {
                "title": "AMD Software Adrenalin 26.6.4 driver download",
                "href": "https://other.example.net/adrenalin-26-6-4",
                "body": "AMD Software: Adrenalin Edition 26.6.4.",
            },
        ]

    def extract(self, url, fmt="text_plain"):
        if "amd.com" in url:
            return {
                "url": url,
                "content": "Official Radeon RX 9070 XT driver download page without a visible version number.",
            }
        if "26-9-1" in url:
            return {"url": url, "content": "Driver version 26.9.1."}
        return {"url": url, "content": "Adrenalin Edition 26.6.4."}


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

    @patch("pkb_proto.web_research.DDGS", _MixedVersionDDGS)
    def test_semantically_different_driver_versions_are_not_cross_compared(self):
        result = research_web(
            "RX 9070 XTの最新ドライバーをWebで調べて",
            max_results=3,
            max_fetches=2,
            region="jp-jp",
        ).as_dict()
        summary = result["fact_summary"]
        groups = {group["kind"]: group for group in summary["groups"]}
        self.assertEqual(groups["driver_version"]["best_candidate"], "26.5.2")
        self.assertEqual(
            groups["si_driver_version"]["best_candidate"],
            "25.10.2",
        )
        self.assertNotEqual(summary["status"], "conflicting_candidates")
        historical = {
            group["kind"]: group for group in summary["historical_groups"]
        }
        self.assertEqual(
            historical["driver_version"]["best_candidate"],
            "26.1.1",
        )

    @patch("pkb_proto.web_research.DDGS", _SamePageMultipleAdrenalinDDGS)
    def test_same_page_multiple_versions_uses_nearby_release_dates(self):
        result = research_web(
            "RX 9070 XTの最新ドライバーをWebで調べて",
            max_results=2,
            max_fetches=2,
            region="jp-jp",
        ).as_dict()
        summary = result["fact_summary"]
        groups = {group["kind"]: group for group in summary["groups"]}
        adrenalin = groups["adrenalin_version"]
        self.assertEqual(adrenalin["best_candidate"], "26.9.1")
        self.assertEqual(adrenalin["status"], "latest_by_date")
        candidates = {row["value"]: row for row in adrenalin["candidates"]}
        self.assertEqual(candidates["26.8.1"]["latest_date"], "2026-08-20")
        self.assertEqual(candidates["26.9.1"]["latest_date"], "2026-09-03")

    @patch("pkb_proto.web_research.DDGS", _PrimaryWithoutVersionDDGS)
    def test_primary_domain_without_current_version_does_not_promote_secondary_candidate(self):
        result = research_web(
            "Radeon RX 9070 XT latest driver official",
            max_results=3,
            max_fetches=2,
            region="jp-jp",
        ).as_dict()
        summary = result["fact_summary"]
        self.assertEqual(summary["primary_domains"], ["www.amd.com"])
        self.assertEqual(
            summary["status"],
            "primary_source_no_current_candidate",
        )
        self.assertIsNone(summary["best_candidate"])
        secondary_values = {
            candidate["value"]
            for group in summary["groups"]
            for candidate in group["candidates"]
        }
        self.assertIn("26.9.1", secondary_values)
        self.assertIn("26.6.4", secondary_values)

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
