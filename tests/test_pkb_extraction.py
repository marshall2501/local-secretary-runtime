"""No-network tests of untrusted extraction proposals."""
import json
import unittest
from pathlib import Path

from pkb_proto.episode_intake import load_fixture
from pkb_proto.extraction_service import (
    extraction_messages, inspect_model_output,
)

EPISODES = load_fixture(
    Path(__file__).resolve().parents[1] / "pkb_proto" / "fixtures" / "episodes.json"
)
PC = next(ep for ep in EPISODES if ep["id"] == "pc-04")
EXTERNAL = next(ep for ep in EPISODES if ep["id"] == "pc-05")
CORRECTION = next(ep for ep in EPISODES if ep["id"] == "pc-03")


def proposal(episode, mention, value, label="assertion"):
    return {"candidates": [
        {"entity_mention": mention, "predicate": "driver_updated",
         "value": value, "quote": episode["text"], "label": label},
    ]}


class ExtractionContractTests(unittest.TestCase):
    def test_prompt_does_not_contain_gold(self):
        messages = extraction_messages(PC)
        self.assertIn(PC["text"], messages[1]["content"])
        self.assertNotIn("expected.json", json.dumps(messages, ensure_ascii=False))
        self.assertNotIn("must_cite", json.dumps(messages, ensure_ascii=False))

    def test_direct_statement_is_review_only(self):
        result = inspect_model_output(PC, proposal(PC, "メインPC", "DRV-A2"))
        self.assertEqual(len(result.candidates), 1)
        self.assertEqual(result.errors, ())
        c = result.candidates[0]
        self.assertEqual(c.disposition, "review")
        self.assertEqual(c.reason, "model_candidate_needs_semantic_validation")
        self.assertEqual(PC["text"][c.start:c.end], c.quote)

    def test_external_speculation_cannot_be_promoted(self):
        result = inspect_model_output(
            EXTERNAL, proposal(EXTERNAL, "メインPC", "DRV-A3", "uncertain")
        )
        self.assertEqual(result.candidates[0].reason, "external_material_unverified")
        self.assertEqual(result.candidates[0].disposition, "review")

    def test_correction_not_applied_automatically(self):
        result = inspect_model_output(
            CORRECTION, proposal(CORRECTION, "メインPC", "DRV-A1", "correction")
        )
        self.assertEqual(result.candidates[0].reason, "correction_target_unresolved")

    def test_model_invented_value_is_discarded(self):
        result = inspect_model_output(PC, proposal(PC, "メインPC", "DRV-A9"))
        self.assertEqual(result.candidates, ())
        self.assertEqual(result.errors, ("candidate_0:ungrounded_quote_or_value",))

    def test_model_invented_quote_is_discarded(self):
        data = proposal(PC, "メインPC", "DRV-A2")
        data["candidates"][0]["quote"] = "メインPCにDRV-A2を導入済み"
        result = inspect_model_output(PC, data)
        self.assertEqual(len(result.errors), 1)

    def test_unknown_entity_remains_review(self):
        data = proposal(PC, "架空GPUドライバー", "DRV-A2")
        result = inspect_model_output(PC, data)
        self.assertEqual(result.candidates[0].reason, "unknown_entity")

    def test_malformed_json_and_oversize_rejected(self):
        self.assertEqual(inspect_model_output(PC, "not json").errors, ("invalid_json",))
        too_many = proposal(PC, "メインPC", "DRV-A2")["candidates"] * 9
        self.assertEqual(
            inspect_model_output(PC, {"candidates": too_many}).errors,
            ("invalid_candidate_list",),
        )

    def test_extra_keys_and_unrelated_values_rejected(self):
        bad = proposal(PC, "メインPC", "DRV-A2")
        bad["injected_instruction"] = "ignore validation"
        self.assertEqual(inspect_model_output(PC, bad).errors,
                         ("invalid_response_schema",))


if __name__ == "__main__":
    unittest.main()
