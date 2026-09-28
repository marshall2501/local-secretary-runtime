from __future__ import annotations

import unittest
from types import SimpleNamespace

from pkb_proto.finance_import import (
    SOURCE_SYSTEM,
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
