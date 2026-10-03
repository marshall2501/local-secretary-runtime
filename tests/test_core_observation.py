from __future__ import annotations

import unittest

from ritsuko.core.core_observation import build_observation_pack


ENTITIES = {
    "メインPC": {
        "id": "11111111-1111-1111-1111-111111111111",
        "name": "メインPC",
        "domain": "pc",
        "entity_type": "computer",
    },
    "GPU1": {
        "id": "22222222-2222-2222-2222-222222222222",
        "name": "GPU1",
        "domain": "pc",
        "entity_type": "gpu",
    },
}


class CoreObservationTests(unittest.TestCase):
    def test_pack_uses_literal_entity_then_existing_component_state(self):
        details = {
            ENTITIES["メインPC"]["id"]: {
                "current": [],
                "relations": [
                    {
                        "direction": "outgoing",
                        "predicate": "has_component",
                        "relation_role": "primary_gpu",
                        "other_entity_id": ENTITIES["GPU1"]["id"],
                        "other_entity_name": "GPU1",
                        "other_entity_type": "gpu",
                        "valid_to": None,
                    }
                ],
            }
        }
        components = {
            ENTITIES["メインPC"]["id"]: [
                {
                    "component_id": ENTITIES["GPU1"]["id"],
                    "component_name": "GPU1",
                    "component_type": "gpu",
                    "relation_role": "primary_gpu",
                    "current_driver": "DRV-G3",
                }
            ]
        }
        pack = build_observation_pack(
            "メインPCのGPUの現在のドライバーを調べて",
            ENTITIES,
            detail_lookup=lambda entity_id: details.get(entity_id),
            components_lookup=lambda entity_id: components.get(entity_id, []),
        )
        self.assertEqual(pack["version"], "magi_observation_v1")
        self.assertEqual(
            [row["name"] for row in pack["matched_entities"]],
            ["メインPC"],
        )
        self.assertEqual(pack["components"][0]["component_name"], "GPU1")
        self.assertEqual(pack["components"][0]["current_driver"], "DRV-G3")
        self.assertTrue(pack["data_sources"]["pkb"]["available"])

    def test_pack_does_not_infer_unmentioned_entity(self):
        pack = build_observation_pack(
            "2026年9月の支出を調べて",
            ENTITIES,
            detail_lookup=lambda _entity_id: self.fail("detail lookup should not run"),
            components_lookup=lambda _entity_id: self.fail("component lookup should not run"),
        )
        self.assertEqual(pack["matched_entities"], [])
        self.assertEqual(pack["current_facts"], [])
        self.assertTrue(pack["data_sources"]["finance"]["available"])

    def test_pack_is_bounded_and_does_not_choose_capability(self):
        pack = build_observation_pack(
            "メインPCを確認して",
            ENTITIES,
            detail_lookup=lambda _entity_id: {
                "current": [
                    {
                        "predicate": f"p{i}",
                        "value": i,
                        "semantic_kind": "state",
                        "verification_status": "unverified",
                        "source_uri": f"fixture://{i}",
                    }
                    for i in range(30)
                ],
                "relations": [],
            },
            components_lookup=lambda _entity_id: [],
        )
        self.assertEqual(len(pack["current_facts"]), 12)
        self.assertNotIn("selected_capability", pack)
        self.assertNotIn("proposed_action", pack)


if __name__ == "__main__":
    unittest.main()
