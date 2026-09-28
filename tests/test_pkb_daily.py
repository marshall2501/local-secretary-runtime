from __future__ import annotations

import unittest
from uuid import UUID

from pkb_proto.daily_pkb import parse_correction, parse_query, parse_write
from pkb_proto.daily_interpreter import inspect_output
from pkb_proto.entity_model_service import classify_predicate
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
    "メインPCのGPU": {
        "id": "44444444-4444-4444-4444-444444444444",
        "name": "メインPCのGPU",
        "domain": "pc",
        "entity_type": "gpu",
    },
    "メインPCのNIC": {
        "id": "55555555-5555-5555-5555-555555555555",
        "name": "メインPCのNIC",
        "domain": "pc",
        "entity_type": "network_adapter",
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

    def test_daily_interpreter_grounded_candidate(self):
        raw = {
            "candidate": {
                "entity_mention": "メインPC",
                "predicate": "driver_updated",
                "value": "DRV-Z10",
                "quote": "メインPCのドライバーをDRV-Z10へ更新した",
            }
        }
        result = inspect_output(
            "今日はメインPCのドライバーをDRV-Z10へ更新した。",
            set(ENTITIES),
            raw,
        )
        self.assertEqual(result.status, "candidate")
        self.assertEqual(result.candidate.entity_mention, "メインPC")
        self.assertEqual(result.candidate.value, "DRV-Z10")

    def test_daily_interpreter_uncertainty_is_not_candidate(self):
        raw = {
            "candidate": {
                "entity_mention": "サブPC",
                "predicate": "driver_updated",
                "value": "DRV-X",
                "quote": "サブPCをDRV-Xへ更新したかもしれない",
            }
        }
        result = inspect_output(
            "サブPCをDRV-Xへ更新したかもしれない。",
            set(ENTITIES),
            raw,
        )
        self.assertEqual(result.status, "no_candidate")

    def test_daily_interpreter_multiple_entities_is_invalid(self):
        raw = {
            "candidate": {
                "entity_mention": "メインPC",
                "predicate": "driver_updated",
                "value": "DRV-Z10",
                "quote": "メインPCをDRV-Z10へ更新した",
            }
        }
        result = inspect_output(
            "メインPCをDRV-Z10へ更新した。サブPCも確認した。",
            set(ENTITIES),
            raw,
        )
        self.assertEqual(result.status, "invalid")

    def test_model_candidate_is_acceptance_eligible_only_with_provenance(self):
        row = {
            "review_status": "pending",
            "reason": "model_candidate_needs_user_confirmation",
            "entity_id": UUID("11111111-1111-1111-1111-111111111111"),
            "entity_name": "メインPC",
            "predicate": "driver_updated",
            "proposed_value": "DRV-Z10",
            "raw_text": "メインPCをDRV-Z10へ更新した。",
            "interpreter_kind": "local_ollama",
            "interpreter_model": "llama3.1:8b",
        }
        self.assertTrue(acceptance_eligible(row))
        row["interpreter_model"] = None
        self.assertFalse(acceptance_eligible(row))

    def test_daily_interpreter_bounded_natural_update_wording(self):
        raw = {
            "candidate": {
                "entity_mention": "メインPC",
                "predicate": "driver_updated",
                "value": "DRV-Z11",
                "quote": "メインPCのドライバーをDRV-Z11へ更新しておいた",
            }
        }
        result = inspect_output(
            "今日はメインPCのドライバーをDRV-Z11へ更新しておいた。",
            set(ENTITIES),
            raw,
        )
        self.assertEqual(result.status, "candidate")

    def test_model_candidate_natural_wording_is_acceptance_eligible(self):
        row = {
            "review_status": "pending",
            "reason": "model_candidate_needs_user_confirmation",
            "entity_id": UUID("11111111-1111-1111-1111-111111111111"),
            "entity_name": "メインPC",
            "predicate": "driver_updated",
            "proposed_value": "DRV-Z11",
            "raw_text": "今日はメインPCのドライバーをDRV-Z11へ更新しておいた。",
            "interpreter_kind": "local_ollama",
            "interpreter_model": "llama3.1:8b",
        }
        self.assertTrue(acceptance_eligible(row))

    def test_entity_model_semantic_classification(self):
        self.assertEqual(classify_predicate("driver_updated"), "event")
        self.assertEqual(classify_predicate("servo_updated"), "event")
        self.assertEqual(classify_predicate("current_driver"), "state")
        self.assertEqual(classify_predicate("manufacturer"), "attribute")
        self.assertIsNone(classify_predicate("unknown_predicate"))

    def test_daily_interpreter_recovers_exact_source_quote(self):
        raw = {
            "candidate": {
                "entity_mention": "メインPC",
                "predicate": "driver_updated",
                "value": "DRV-Z13",
                "quote": "メインPCのドライバーをDRV-Z13へ更新しておいたです",
            }
        }
        result = inspect_output(
            "さっきメインPCのドライバーをDRV-Z13へ更新しておいた。",
            set(ENTITIES),
            raw,
        )
        self.assertEqual(result.status, "candidate")
        self.assertEqual(result.candidate.value, "DRV-Z13")
        self.assertEqual(
            result.candidate.quote,
            "さっきメインPCのドライバーをDRV-Z13へ更新しておいた。",
        )

    def test_daily_interpreter_rejects_multiple_grounded_update_sentences(self):
        raw = {
            "candidate": {
                "entity_mention": "メインPC",
                "predicate": "driver_updated",
                "value": "DRV-Z13",
                "quote": "dummy",
            }
        }
        result = inspect_output(
            "メインPCをDRV-Z13へ更新した。メインPCをDRV-Z14へ更新した。",
            set(ENTITIES),
            raw,
        )
        self.assertEqual(result.status, "invalid")
        self.assertEqual(result.reason, "grounded_sentence_not_unique")

    def test_component_driver_update_targets_component_entity(self):
        result = parse_write(
            "メインPCのGPUドライバーをDRV-G1へ更新した。",
            set(ENTITIES),
        )
        self.assertEqual(result["status"], "parsed")
        self.assertEqual(result["entity"], "メインPCのGPU")
        self.assertEqual(result["predicate"], "driver_updated")
        self.assertEqual(result["value"], "DRV-G1")

    def test_current_driver_query_targets_component_state(self):
        query = parse_query("メインPCのGPUの現在のドライバー", ENTITIES)
        self.assertEqual(
            query.entity_id,
            UUID("44444444-4444-4444-4444-444444444444"),
        )
        self.assertEqual(query.predicate, "current_driver")

    def test_nested_component_entity_is_not_ambiguous_for_interpreter(self):
        raw = {
            "candidate": {
                "entity_mention": "メインPCのGPU",
                "predicate": "driver_updated",
                "value": "DRV-G2",
                "quote": "メインPCのGPUドライバーをDRV-G2へ更新しておいた。",
            }
        }
        result = inspect_output(
            "メインPCのGPUドライバーをDRV-G2へ更新しておいた。",
            set(ENTITIES),
            raw,
        )
        self.assertEqual(result.status, "candidate")
        self.assertEqual(result.candidate.entity_mention, "メインPCのGPU")


if __name__ == "__main__":
    unittest.main()
