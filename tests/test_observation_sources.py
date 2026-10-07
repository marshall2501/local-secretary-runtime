from __future__ import annotations

from datetime import date
import unittest

from ritsuko.application.observation_sources import (
    finance_query_from_request,
    pending_source_requests,
    selected_capability_for_batch,
    verified_source_observation,
    web_query_from_request,
)


class ObservationSourceTests(unittest.TestCase):
    def test_pending_requests_group_only_available_executors(self):
        session = {
            "status": "waiting_information",
            "pending_requests": [
                {"request_id": "P1", "source": "pkb", "what": "local"},
                {"request_id": "W1", "source": "web", "what": "public"},
                {"request_id": "F1", "source": "finance", "what": "先月"},
                {"request_id": "X1", "source": "files", "what": "document"},
            ],
        }
        groups, unavailable = pending_source_requests(
            session,
            resource_catalog={
                "pkb": {"available": True},
                "web": {"available": True},
                "finance": {"available": True},
                "files": {"available": False},
            },
        )
        self.assertEqual([x["source"] for x in groups], ["pkb", "web", "finance"])
        self.assertEqual([x["source"] for x in unavailable], ["files"])

    def test_verified_observation_preserves_privacy_and_links(self):
        observation = verified_source_observation(
            {
                "status": "ok",
                "source": "finance",
                "capability": "finance_read",
                "confidentiality": "private",
                "total": 3,
                "answer": "収支はプラスです。",
                "verified_by": "deterministic_finance_query",
                "result": {
                    "result_kind": "finance_summary",
                    "recent_rows": [{"amount": 100}],
                },
            },
            {
                "source": "finance",
                "request_ids": ["F1"],
            },
        )
        self.assertEqual(observation["source"], "finance")
        self.assertEqual(observation["confidentiality"], "private")
        self.assertTrue(observation["verified"])
        self.assertEqual(observation["responds_to"], ["F1"])

    def test_failed_read_does_not_create_verified_observation(self):
        self.assertIsNone(verified_source_observation(
            {
                "status": "error",
                "source": "web",
                "answer": "",
                "result": {"result_kind": "source_read_error"},
            },
            {"source": "web", "request_ids": ["W1"]},
        ))

    def test_web_query_blocks_private_derived_term_without_safe_mark(self):
        session = {
            "user_raw": "メインPCのGPUと最新ドライバーを比較して",
            "observations": [{
                "source": "pkb",
                "verified": True,
                "confidentiality": "private",
                "evidence_preview": [
                    {"predicate": "model", "value": "Secret Model 1234"},
                ],
            }],
        }
        with self.assertRaisesRegex(
            ValueError,
            "web_query_requires_private_context_approval",
        ):
            web_query_from_request(
                {"what": "Secret Model 1234 最新ドライバー"},
                session,
            )

    def test_web_query_allows_explicit_public_safe_term(self):
        session = {
            "user_raw": "メインPCのGPUと最新ドライバーを比較して",
            "observations": [{
                "source": "pkb",
                "verified": True,
                "confidentiality": "private",
                "evidence_preview": [
                    {"predicate": "model", "value": "Radeon RX 9070 XT"},
                ],
                "public_query_terms": ["Radeon RX 9070 XT"],
            }],
        }
        query = web_query_from_request(
            {"what": "Radeon RX 9070 XT 最新ドライバー"},
            session,
        )
        self.assertIn("Radeon RX 9070 XT", query)

    def test_finance_relative_last_month_is_normalized(self):
        query = finance_query_from_request(
            {"what": "先月の家計の収支"},
            today=date(2026, 10, 7),
        )
        self.assertTrue(query.startswith("2026年9月 "))

    def test_selected_capability_reports_batch(self):
        self.assertEqual(
            selected_capability_for_batch([
                {"status": "ok", "capability": "pkb_search"},
                {"status": "ok", "capability": "web_research"},
            ]),
            "observation_read",
        )


if __name__ == "__main__":
    unittest.main()
