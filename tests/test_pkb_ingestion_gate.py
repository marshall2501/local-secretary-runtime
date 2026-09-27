"""Offline tests for the PKB ingestion gate; no database or LLM needed."""
import unittest
from datetime import datetime, timezone

from pkb_proto.ingestion_gate import InputRecord, ProposedClaim, Route, assess


AT = datetime(2026, 9, 27, tzinfo=timezone.utc)
ALIASES = {"pc-main": {"メインPC"}, "pc-sub": {"サブPC"}, "rc-a": {"RCカーA"}, "rc-b": {"RCカーB"}}


def record(text="メインPCをDRV-A2に更新した。", kind="user_statement", **kwargs):
    return InputRecord(input_id="fictional-01", source_kind=kind,
                       source_ref="fixture://fictional-01", text=text,
                       recorded_at=AT, occurred_at=AT, **kwargs)


def proposal(text="メインPCをDRV-A2に更新した。", **kwargs):
    return ProposedClaim(entity_key="pc-main", entity_mention="メインPC",
                         predicate="driver_updated", value="DRV-A2",
                         evidence_start=0, evidence_end=len(text),
                         evidence_quote=text, **kwargs)


class IngestionGateTests(unittest.TestCase):
    def test_user_statement_is_only_eligible_not_written(self):
        result = assess(record(), proposal(), aliases=ALIASES)
        self.assertEqual(result.route, Route.AUTO_CANDIDATE)
        self.assertEqual(result.reason, "eligible_for_db_conflict_check")

    def test_fabricated_quote_is_rejected(self):
        result = assess(record(), proposal(text="メインPCをDRV-A3に更新した。"), aliases=ALIASES)
        self.assertEqual(result.route, Route.REJECT)

    def test_wrong_entity_mention_is_review(self):
        claim = ProposedClaim(entity_key="pc-main", entity_mention="サブPC",
                              predicate="driver_updated", value="DRV-A1",
                              evidence_start=0, evidence_end=20,
                              evidence_quote="サブPCをDRV-A1に更新した。")
        text = "サブPCをDRV-A1に更新した。"
        claim = ProposedClaim(entity_key="pc-main", entity_mention="サブPC",
                              predicate="driver_updated", value="DRV-A1",
                              evidence_start=0, evidence_end=len(text),
                              evidence_quote=text)
        self.assertEqual(assess(record(text), claim, aliases=ALIASES).route, Route.REVIEW)

    def test_unknown_entity_is_review(self):
        text = "RCカーCのサーボを交換した。"
        claim = ProposedClaim(entity_key="rc-c", entity_mention="RCカーC",
                              predicate="servo_updated", value="X1",
                              evidence_start=0, evidence_end=len(text), evidence_quote=text)
        self.assertEqual(assess(record(text), claim, aliases=ALIASES).route, Route.REVIEW)

    def test_external_source_cannot_be_promoted(self):
        for kind in ("file", "web", "tool", "service"):
            with self.subTest(kind=kind):
                result = assess(record(kind=kind), proposal(), aliases=ALIASES)
                self.assertEqual(result.route, Route.REVIEW)

    def test_explicit_correction_needs_transaction(self):
        text = "訂正：メインPCの更新だった。"
        claim = ProposedClaim(entity_key="pc-main", entity_mention="メインPC",
                              predicate="driver_updated", value="DRV-A1",
                              evidence_start=0, evidence_end=len(text),
                              evidence_quote=text, intent="correction",
                              corrects_claim_id="claim-01")
        result = assess(record(text), claim, aliases=ALIASES, known_claim_ids={"claim-01"})
        self.assertEqual(result.route, Route.REVIEW)
        self.assertEqual(result.reason, "correction_needs_transactional_handling")

    def test_missing_correction_target_is_review(self):
        text = "訂正：メインPCの更新だった。"
        claim = ProposedClaim(entity_key="pc-main", entity_mention="メインPC",
                              predicate="driver_updated", value="DRV-A1",
                              evidence_start=0, evidence_end=len(text),
                              evidence_quote=text, intent="correction")
        self.assertEqual(assess(record(text), claim, aliases=ALIASES).reason,
                         "correction_target_unresolved")

    def test_timezone_is_required(self):
        old = record()
        invalid = InputRecord(input_id=old.input_id, source_kind=old.source_kind,
                              source_ref=old.source_ref, text=old.text,
                              recorded_at=datetime(2026, 9, 27), occurred_at=AT)
        self.assertEqual(assess(invalid, proposal(), aliases=ALIASES).route, Route.REJECT)

    def test_restricted_input_requires_review(self):
        self.assertEqual(assess(record(confidentiality="restricted"), proposal(),
                                aliases=ALIASES).route, Route.REVIEW)

    def test_missing_identity_is_rejected(self):
        old = record()
        invalid = InputRecord(input_id="", source_kind=old.source_kind,
                              source_ref=old.source_ref, text=old.text,
                              recorded_at=AT, occurred_at=AT)
        self.assertEqual(assess(invalid, proposal(), aliases=ALIASES).route, Route.REJECT)


if __name__ == "__main__":
    unittest.main()
