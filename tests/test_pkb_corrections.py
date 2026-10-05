"""Conservative correction tests; DB transaction is covered by isolated smoke."""
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace

from pkb.correction_service import explicit_reassignment_quote
from infrastructure.postgres.pkb_correction_repository import correct_entity
from pkb.ingestion_gate import InputRecord, ProposedClaim

AT = datetime(2026, 9, 22, tzinfo=timezone.utc)


class CorrectionTests(unittest.TestCase):
    def test_pc_fixture_explicit_reassignment(self):
        text = ("訂正：9月20日に話したDRV-A1への更新はサブPCではなくメインPCのこと。"
                "9月21日のサブPCの重さは訂正しない。")
        self.assertTrue(explicit_reassignment_quote(text, "サブPC", "メインPC", "DRV-A1"))

    def test_rc_fixture_explicit_reassignment(self):
        text = ("訂正：9月20日に交換したSERVO-X1はRCカーAではなくRCカーB。"
                "RCカーAの右旋回の問題はそのまま正しい。")
        self.assertTrue(explicit_reassignment_quote(text, "RCカーA", "RCカーB", "SERVO-X1"))

    def test_unrelated_or_ambiguous_reassignment_is_not_accepted(self):
        self.assertFalse(explicit_reassignment_quote(
            "RCカーAの旋回不良は未確認", "RCカーA", "RCカーB", "SERVO-X1"))
        self.assertFalse(explicit_reassignment_quote(
            "訂正：RCカーAではなくRCカーB", "RCカーA", "RCカーB", "SERVO-X1"))
        self.assertFalse(explicit_reassignment_quote(
            "訂正：RCカーBではなくRCカーAのSERVO-X1", "RCカーA", "RCカーB", "SERVO-X1"))

    def test_production_db_refused_before_sql(self):
        class Forbidden:
            info = SimpleNamespace(dbname="secretary", host="localhost",
                                   user="secretary_pkb_proto_writer_20260927")
            def transaction(self):
                raise AssertionError("no SQL allowed")
        record = InputRecord("fixture-correction", "user_statement", "fixture://correction",
                             "訂正：サブPCではなくメインPCのDRV-A1", AT, AT)
        claim = ProposedClaim("fake", "メインPC", "driver_updated", "DRV-A1", 0,
                              len(record.text), record.text, "correction",
                              "00000000-0000-0000-0000-000000000001")
        with self.assertRaisesRegex(ValueError, "Refusing non-isolated"):
            correct_entity(Forbidden(), record, claim)

    def test_non_fixture_rejected_before_sql(self):
        class Isolated:
            info = SimpleNamespace(dbname="secretary_pkb_proto_20260927",
                                   host="localhost",
                                   user="secretary_pkb_proto_writer_20260927")
            def transaction(self):
                raise AssertionError("no SQL allowed")
        record = InputRecord("fixture-correction", "user_statement", "personal://source",
                             "訂正：サブPCではなくメインPCのDRV-A1", AT, AT)
        claim = ProposedClaim("fake", "メインPC", "driver_updated", "DRV-A1", 0,
                              len(record.text), record.text, "correction",
                              "00000000-0000-0000-0000-000000000001")
        self.assertEqual(correct_entity(Isolated(), record, claim).reason,
                         "fictional_fixture_only")


if __name__ == "__main__":
    unittest.main()
