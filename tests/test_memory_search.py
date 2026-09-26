"""Offline tests of the deterministic, SQL-first memory enumeration API."""
import unittest
from unittest.mock import MagicMock, patch

from api.secretary_api import memory_search_sql, search_memory


class MemorySearchTests(unittest.TestCase):
    def test_claim_history_relation_is_static(self):
        self.assertIn("FROM secretary.current_claims c", memory_search_sql(False))
        self.assertIn("FROM secretary.claims c", memory_search_sql(True))
        self.assertNotIn("FROM secretary.claims c", memory_search_sql(False))
        for kind in ("'claim'", "'issue'", "'hypothesis'", "'source'"):
            self.assertIn(kind, memory_search_sql(False))

    def test_full_enumeration_is_paginated_and_counted(self):
        cur = MagicMock()
        cur.fetchone.return_value = {"total": 135}
        cur.fetchall.return_value = [{"kind": "claim", "title": "fictional"}]
        db = MagicMock()
        db.__enter__.return_value = db
        db.cursor.return_value.__enter__.return_value = cur
        with patch("api.secretary_api.connect", return_value=db):
            result = search_memory(
                q=None, domain=None, kind=None,
                include_history=False, limit=50, offset=100
            )
        self.assertEqual(result["total"], 135)
        self.assertEqual(result["offset"], 100)
        self.assertEqual(len(result["items"]), 1)
        calls = cur.execute.call_args_list
        self.assertEqual(len(calls), 3)
        self.assertIn("REPEATABLE READ, READ ONLY", calls[0].args[0])
        self.assertEqual(calls[1].args[1], (None,) * 6)
        self.assertEqual(calls[2].args[1], (None,) * 6 + (50, 100))
        self.assertIn("ORDER BY recorded_at DESC, kind ASC, id DESC", calls[2].args[0])

    def test_literal_query_never_interpolated_into_sql(self):
        cur = MagicMock()
        cur.fetchone.return_value = {"total": 0}
        cur.fetchall.return_value = []
        db = MagicMock()
        db.__enter__.return_value = db
        db.cursor.return_value.__enter__.return_value = cur
        with patch("api.secretary_api.connect", return_value=db):
            result = search_memory(
                q="  RAM_%  ", domain=" pc ", kind="claim",
                include_history=True, limit=10, offset=0
            )
        self.assertEqual(result["total"], 0)
        count_sql, params = cur.execute.call_args_list[1].args
        self.assertNotIn("RAM_%", count_sql)
        self.assertIn("strpos(lower", count_sql)
        self.assertEqual(params, ("pc", "pc", "claim", "claim", "RAM_%", "RAM_%"))
        self.assertIn("FROM secretary.claims c", count_sql)


if __name__ == "__main__":
    unittest.main()
