"""Offline regression tests for action/result recall and strict entity attribution."""
import unittest
from unittest.mock import MagicMock, patch

from fastapi import HTTPException
from api import secretary_api as api
from scripts.secretary import ask


def fake_db(cursor):
    db = MagicMock()
    db.__enter__.return_value = db
    db.cursor.return_value.__enter__.return_value = cursor
    return db


class ExperienceRecallTests(unittest.TestCase):
    def test_api_returns_domain_actions_without_inventing_entity(self):
        cur = MagicMock()
        cur.fetchone.return_value = {"total": 1}
        cur.fetchall.return_value = [
            {"domain": "pc", "entity_name": None, "tool": "prototype_mock",
             "summary": "Only a fictional diagnostic", "outcome": "success"},
        ]
        with patch.object(api, "connect", return_value=fake_db(cur)):
            result = api.search_experience(domain="pc", entity_name=None,
                                           limit=20, offset=0)
        self.assertEqual(result["total"], 1)
        self.assertIsNone(result["items"][0]["entity_name"])
        self.assertIn("unlinked", result["scope"])
        sql = "\n".join(call.args[0] for call in cur.execute.call_args_list)
        self.assertIn("LEFT JOIN secretary.entities", sql)
        self.assertIn("LEFT JOIN secretary.results", sql)
        self.assertEqual(cur.execute.call_args_list[-1].args[1],
                         ("pc", "pc", None, None, 20, 0))

    def test_entity_filter_requires_domain(self):
        with self.assertRaises(HTTPException) as err:
            api.search_experience(domain=None, entity_name="架空テストPC",
                                  limit=20, offset=0)
        self.assertEqual(err.exception.status_code, 422)

    def test_cli_does_not_attribute_domain_only_task_to_target_pc(self):
        result = {"total": 2, "items": [
            {"entity_name": None, "domain": "pc", "tool": "prototype_mock",
             "operation": "simulated_read_only", "summary": "Fictional result",
             "evidence": {"simulated": True}, "outcome": "success",
             "task_request": "Generic PC test"},
            {"entity_name": "別PC", "domain": "pc", "tool": "prototype_mock",
             "operation": "simulated_read_only", "summary": "Other PC",
             "evidence": {"simulated": True}, "outcome": "success"},
        ]}
        with patch.object(ask, "search_experience", return_value=result) as read:
            records, coverage = ask.experience_records(
                "架空テストPCの過去の対策結果は？", "unused-token",
                "pc", "架空テストpc",
            )
        self.assertEqual(read.call_args.kwargs["domain"], "pc")
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["association"], "unlinked_same_domain")
        self.assertTrue(records[0]["simulated"])
        self.assertIn("linked_to_named_entity=0", coverage)

    def test_question_recall_includes_history_but_labels_unlinked(self):
        question = "架空テストPCについて過去の障害と実施した対策の結果は？"
        remembered = {"entity_name": "架空テストPC", "kind": "claim",
                      "title": "ram_gb", "value_text": "16"}
        experience = {"total": 1, "items": [
            {"entity_name": None, "domain": "pc", "tool": "prototype_mock",
             "operation": "simulated_read_only",
             "summary": "Fictional simulated diagnostic",
             "evidence": {"simulated": True}, "outcome": "success"},
        ]}
        with patch.object(ask, "query_plan", return_value=("障害", "pc")), \
             patch.object(ask, "search", side_effect=[
                 {"total": 0, "items": []},
                 {"total": 1, "items": [remembered]},
             ]), \
             patch.object(ask, "search_experience", return_value=experience):
            records, coverage = ask.records_for_answer(question, "unused-token")
        self.assertEqual(len(records), 2)
        self.assertEqual(records[0]["title"], "ram_gb")
        self.assertEqual(records[1]["association"], "unlinked_same_domain")
        self.assertIn("experience_search_total=1", coverage)


if __name__ == "__main__":
    unittest.main()
