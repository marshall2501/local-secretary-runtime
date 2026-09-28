from __future__ import annotations

import unittest
from uuid import UUID

from pkb_proto.daily_pkb import parse_correction, parse_query, parse_write
from pkb_proto.pending_service import acceptance_eligible


ENTITIES = {
    "メインPC": {
        "id": "11111111-1111-1111-1111-111111111111",
        "name": "メインPC",
        "domain": "pc",
        "entity_type": "computer",
    },
    "サブPC": {
        "id": "22222222-2222-2222-2222-222222222222",
        "name": "サブPC",
        "domain": "pc",
        "entity_type": "computer",
    },
    "RCカーB": {
        "id": "33333333-3333-3333-3333-333333333333",
        "name": "RCカーB",
        "domain": "rc",
        "entity_type": "rc_car",
    },
}


class DailyPKBParserTests(unittest.TestCase):
    def test_explicit_pc_update(self):
        result = parse_write("メインPCをDRV-A3へ更新した。", set(ENTITIES))
        self.assertEqual(result["status"], "parsed")
        self.assertEqual(result["entity"], "メインPC")
        self.assertEqual(result["predicate"], "driver_updated")
        self.assertEqual(result["value"], "DRV-A3")

    def test_explicit_rc_servo_replacement(self):
        result = parse_write("RCカーBのサーボをSERVO-X3へ交換した。", set(ENTITIES))
        self.assertEqual(result["status"], "parsed")
        self.assertEqual(result["entity"], "RCカーB")
        self.assertEqual(result["predicate"], "servo_updated")
        self.assertEqual(result["value"], "SERVO-X3")

    def test_unknown_entity_goes_to_review(self):
        result = parse_write("未知PCをDRV-Xへ更新した。", set(ENTITIES))
        self.assertEqual(result["status"], "review")
        self.assertEqual(result["reason"], "unknown_or_ambiguous_entity")

    def test_explicit_correction(self):
        result = parse_correction(
            "訂正：サブPCではなくメインPCをDRV-A1へ更新した。",
            set(ENTITIES),
        )
        self.assertEqual(result["status"], "parsed")
        self.assertEqual(result["old_entity"], "サブPC")
        self.assertEqual(result["new_entity"], "メインPC")
        self.assertEqual(result["value"], "DRV-A1")

    def test_query_uses_known_entity_and_history(self):
        query = parse_query("サブPCのドライバー更新履歴", ENTITIES)
        self.assertEqual(
            query.entity_id,
            UUID("22222222-2222-2222-2222-222222222222"),
        )
        self.assertEqual(query.predicate, "driver_updated")
        self.assertTrue(query.include_history)

    def test_query_without_known_terms_stays_scoped_only_by_available_filters(self):
        query = parse_query("全部の記録", ENTITIES)
        self.assertIsNone(query.entity_id)
        self.assertIsNone(query.predicate)
        self.assertTrue(query.include_history)

    def test_structured_conflict_is_acceptance_eligible(self):
        row = {
            "review_status": "pending",
            "reason": "existing_claim_requires_conflict_resolution",
            "entity_id": UUID("22222222-2222-2222-2222-222222222222"),
            "entity_name": "サブPC",
            "predicate": "driver_updated",
            "proposed_value": "DRV-A9",
            "raw_text": "サブPCをDRV-A9へ更新した。",
        }
        self.assertTrue(acceptance_eligible(row))

    def test_unstructured_pending_is_not_acceptance_eligible(self):
        row = {
            "review_status": "pending",
            "reason": "unsupported_natural_language_in_first_slice",
            "entity_id": None,
            "entity_name": None,
            "predicate": None,
            "proposed_value": None,
            "raw_text": "サブPCの調子が最近よくない気がする。",
        }
        self.assertFalse(acceptance_eligible(row))


if __name__ == "__main__":
    unittest.main()
