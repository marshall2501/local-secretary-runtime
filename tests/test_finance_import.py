from __future__ import annotations

import unittest
from types import SimpleNamespace

from pkb_proto.finance_import import (
    SOURCE_SYSTEM,
    _finance_filter_clause,
    _guard_db,
    source_sha256,
    transaction_content_hash,
    transaction_payload,
)


ROW = {
    "date": "2026-09-28",
    "content": "テスト支出",
    "amount": -1234,
    "account": "カードA",
    "major_category": "食費",
    "minor_category": "その他",
    "memo": "",
    "is_transfer": False,
    "calculation_target": True,
    "external_id": "mf-001",
}


class FinanceImportTests(unittest.TestCase):
    def test_source_sha_is_stable(self):
        self.assertEqual(source_sha256(b"abc"), source_sha256(b"abc"))
        self.assertEqual(len(source_sha256(b"abc")), 64)

    def test_transaction_hash_changes_with_source_content(self):
        a = transaction_content_hash(ROW)
        changed = dict(ROW)
        changed["major_category"] = "日用品"
        self.assertNotEqual(a, transaction_content_hash(changed))

    def test_payload_keeps_external_id_and_integer_jpy(self):
        payload = transaction_payload(ROW)
        self.assertEqual(payload["external_id"], "mf-001")
        self.assertEqual(payload["amount_jpy"], -1234)
        self.assertEqual(SOURCE_SYSTEM, "moneyforward_me")


    def test_filter_clause_builds_parameterized_constraints(self):
        clause, params = _finance_filter_clause(
            start_date="2026-09-01",
            end_date="2026-09-30",
            account="カードA",
            major_category="食費",
            search_text="スーパー",
        )
        self.assertIn("t.transaction_date >= %s::date", clause)
        self.assertIn("t.transaction_date <= %s::date", clause)
        self.assertIn("a.external_name = %s", clause)
        self.assertIn("c.major_name = %s", clause)
        self.assertIn("t.description ILIKE %s", clause)
        self.assertEqual(params[0], SOURCE_SYSTEM)
        self.assertEqual(params[1:5], ["2026-09-01", "2026-09-30", "カードA", "食費"])
        self.assertEqual(params[5:], ["%スーパー%"] * 5)

    def test_filter_clause_without_filters_scopes_only_source_system(self):
        clause, params = _finance_filter_clause()
        self.assertEqual(clause, "t.source_system=%s")
        self.assertEqual(params, [SOURCE_SYSTEM])

    def test_guard_rejects_production_database(self):
        db = SimpleNamespace(info=SimpleNamespace(
            dbname="secretary",
            user="secretary_pkb_proto_writer_20260927",
            host="localhost",
        ))
        with self.assertRaisesRegex(ValueError, "non-prototype"):
            _guard_db(db)

    def test_guard_rejects_admin_user(self):
        db = SimpleNamespace(info=SimpleNamespace(
            dbname="secretary_pkb_proto_20260927",
            user="secretary_admin",
            host="localhost",
        ))
        with self.assertRaisesRegex(ValueError, "non-dedicated"):
            _guard_db(db)

    def test_guard_rejects_remote_host(self):
        db = SimpleNamespace(info=SimpleNamespace(
            dbname="secretary_pkb_proto_20260927",
            user="secretary_pkb_proto_writer_20260927",
            host="example.invalid",
        ))
        with self.assertRaisesRegex(ValueError, "non-local"):
            _guard_db(db)


if __name__ == "__main__":
    unittest.main()
