"""Offline tests: the DB writer is intentionally not run against real DB."""
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

from pkb.ingestion_gate import InputRecord, ProposedClaim
from pkb.write_service import _literal_gate, payload_hash
from infrastructure.postgres.pkb_write_repository import write_one

AT = datetime(2026, 9, 27, tzinfo=timezone.utc)


def record(input_id="test-01", text="メインPCをDRV-A2へ更新した。"):
    return InputRecord(input_id, "user_statement", "fixture://pc-04", text, AT, AT)


def proposal(text="メインPCをDRV-A2へ更新した。", value="DRV-A2"):
    return ProposedClaim(
        entity_key=str(uuid4()), entity_mention="メインPC",
        predicate="driver_updated", value=value,
        evidence_start=0, evidence_end=len(text), evidence_quote=text,
    )


class WriteSliceTests(unittest.TestCase):
    def test_digest_stable_for_same_input(self):
        r, c = record(), proposal()
        self.assertEqual(payload_hash(r, c), payload_hash(r, c))
        self.assertEqual(len(payload_hash(r, c)), 64)

    def test_digest_changes_when_payload_changes(self):
        self.assertNotEqual(payload_hash(record("first"), proposal()),
                            payload_hash(record("second"), proposal()))

    def test_literal_update(self):
        self.assertIsNone(_literal_gate(record(), proposal()))

    def test_fabricated_value_rejected(self):
        self.assertEqual(_literal_gate(record(), proposal(value="DRV-A3")),
                         "value_or_action_not_explicit_in_quote")

    def test_uncertain_language_needs_review(self):
        text = "メインPCをDRV-A2へ更新したかもしれない。"
        self.assertEqual(_literal_gate(record(text), proposal(text)),
                         "uncertain_or_correction_language")

    def test_prototype_dbname_guard_before_any_sql(self):
        class ForbiddenDB:
            info = SimpleNamespace(dbname="secretary", host="localhost")
            def transaction(self):
                raise AssertionError("must not open a transaction")

        with self.assertRaisesRegex(ValueError, "Refusing non-prototype"):
            write_one(ForbiddenDB(), record(), proposal())

    def test_nonlocal_host_guard_before_any_sql(self):
        class ForbiddenDB:
            info = SimpleNamespace(dbname="secretary_pkb_proto_20260927", host="example.invalid", user="secretary_pkb_proto_writer_20260927")
            def transaction(self):
                raise AssertionError("must not open a transaction")

        with self.assertRaisesRegex(ValueError, "non-local"):
            write_one(ForbiddenDB(), record(), proposal())

    def test_non_dedicated_login_guard_before_any_sql(self):
        class ForbiddenDB:
            info = SimpleNamespace(dbname="secretary_pkb_proto_20260927", host="localhost", user="secretary_admin")
            def transaction(self):
                raise AssertionError("must not open a transaction")
        with self.assertRaisesRegex(ValueError, "Refusing non-prototype"):
            write_one(ForbiddenDB(), record(), proposal())

    def test_only_fixture_sources_in_first_slice(self):
        class IsolatedDB:
            info = SimpleNamespace(dbname="secretary_pkb_proto_20260927", host="localhost", user="secretary_pkb_proto_writer_20260927")
            def transaction(self):
                raise AssertionError("must reject before opening a transaction")

        r = InputRecord("input", "user_statement", "real://not-allowed",
                        "メインPCをDRV-A2へ更新した。", AT, AT)
        self.assertEqual(write_one(IsolatedDB(), r, proposal()).reason,
                         "fictional_fixture_only")

    def test_event_semantics_are_append_only(self):
        from pkb.entity_model_service import classify_predicate
        self.assertEqual(classify_predicate("driver_updated"), "event")
        self.assertEqual(classify_predicate("servo_updated"), "event")

    def test_current_driver_semantics_are_state(self):
        from pkb.entity_model_service import classify_predicate
        self.assertEqual(classify_predicate("current_driver"), "state")


if __name__ == "__main__":
    unittest.main()
