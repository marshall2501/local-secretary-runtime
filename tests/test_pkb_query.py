"""Offline contract checks for parameterized bitemporal query (no DB needed)."""
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4
import unittest

from pkb_proto.query_service import ClaimQuery, _sql, query_claims, validate

UTC = timezone.utc


class QueryTests(unittest.TestCase):
    def test_default_is_bounded_current_view(self):
        query = ClaimQuery()
        validate(query)
        self.assertEqual(query.limit, 100)
        self.assertFalse(query.include_history)

    def test_exact_entity_scope(self):
        validate(ClaimQuery(entity_id=uuid4(), domain="pc", predicate="driver_updated"))

    def test_rejects_invalid_uuid_and_unbounded_page(self):
        for kwargs in ({"entity_id": "メインPC"}, {"limit": 101},
                       {"limit": 0}, {"offset": -1},
                       {"include_history": "yes"}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                validate(ClaimQuery(**kwargs))

    def test_rejects_naive_timestamps_and_invalid_range(self):
        with self.assertRaises(ValueError):
            validate(ClaimQuery(known_at=datetime(2026, 9, 22)))
        with self.assertRaises(ValueError):
            validate(ClaimQuery(effective_from=datetime(2026, 9, 23, tzinfo=UTC),
                                effective_before=datetime(2026, 9, 22, tzinfo=UTC)))

    def test_history_is_separate_from_current(self):
        sql = _sql()
        self.assertIn("c.recorded_at <= %(known_at)s", sql)
        self.assertIn("successor.recorded_at <= %(known_at)s", sql)
        self.assertIn("c.retracted_at <= %(known_at)s", sql)
        self.assertIn("NOT superseded_by_cutoff", sql)
        self.assertIn("s.citation AS source_citation", sql)
        self.assertNotIn("LIMIT 10", sql)

    def test_wrong_database_cannot_be_read(self):
        class Forbidden:
            info = SimpleNamespace(dbname="secretary", host="localhost",
                                   user="secretary_pkb_proto_writer_20260927")
            def transaction(self):
                raise AssertionError("must reject before SQL")
        with self.assertRaisesRegex(ValueError, "non-isolated"):
            query_claims(Forbidden(), ClaimQuery())

    def test_non_dedicated_login_cannot_read(self):
        class Forbidden:
            info = SimpleNamespace(dbname="secretary_pkb_proto_20260927",
                                   host="localhost", user="secretary_admin")
            def transaction(self):
                raise AssertionError("must reject before SQL")
        with self.assertRaisesRegex(ValueError, "non-dedicated"):
            query_claims(Forbidden(), ClaimQuery())


if __name__ == "__main__":
    unittest.main()
