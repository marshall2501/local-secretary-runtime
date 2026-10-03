from __future__ import annotations

import unittest

from ritsuko.core.core_coordinator import (
    can_auto_execute_ambiguous_probe,
    decide_after_observation,
    extend_observation_pack,
)


class CoreCoordinatorTests(unittest.TestCase):
    def test_ambiguous_synthesis_can_auto_execute_only_pkb_search(self):
        melchior = {"status": "question", "selected_capability": None}
        self.assertTrue(can_auto_execute_ambiguous_probe(
            melchior,
            {"status": "ok", "next_step": "observe", "selected_capability": "pkb_search"},
        ))
        self.assertFalse(can_auto_execute_ambiguous_probe(
            melchior,
            {"status": "ok", "next_step": "observe", "selected_capability": "finance_read"},
        ))
        self.assertFalse(can_auto_execute_ambiguous_probe(
            melchior,
            {"status": "ok", "next_step": "observe", "selected_capability": "web_research"},
        ))

    def test_ready_melchior_never_uses_ambiguous_auto_probe(self):
        self.assertFalse(can_auto_execute_ambiguous_probe(
            {"status": "ready", "selected_capability": "pkb_search"},
            {"status": "ok", "next_step": "observe", "selected_capability": "pkb_search"},
        ))

    def test_repeat_probe_is_not_auto_executed(self):
        self.assertFalse(can_auto_execute_ambiguous_probe(
            {"status": "question", "selected_capability": None},
            {"status": "ok", "next_step": "observe", "selected_capability": "pkb_search"},
            executed_capabilities=("pkb_search",),
        ))

    def test_observation_pack_adds_bounded_task_evidence(self):
        pack = extend_observation_pack(
            {"version": "magi_observation_v1", "matched_entities": [{"name": "メインPC"}]},
            {
                "capability": "pkb_search",
                "total": 2,
                "answer": "PKBの構成記録では、GPU1 / NIC1。",
                "result": {
                    "result_kind": "components",
                    "items": [{"component_name": f"C{i}"} for i in range(12)],
                },
            },
            cycle=1,
        )
        self.assertEqual(pack["version"], "magi_observation_v2")
        self.assertEqual(pack["task_progress"]["executed_capabilities"], ["pkb_search"])
        self.assertEqual(pack["task_progress"]["cycle"], 2)
        self.assertEqual(len(pack["task_observations"][0]["evidence_preview"]), 8)

    def test_second_cycle_repeat_with_evidence_responds(self):
        decision = decide_after_observation(
            {"next_step": "observe", "selected_capability": "pkb_search"},
            execution={"total": 2, "answer": "PKBで2件確認しました。"},
            executed_capabilities=("pkb_search",),
        )
        self.assertEqual(decision["next_step"], "respond")
        self.assertEqual(decision["reason"], "repeat_probe_stopped_with_evidence")
        self.assertIn("2件", decision["message"])

    def test_second_cycle_clarify_keeps_local_answer_and_specific_question(self):
        decision = decide_after_observation(
            {"next_step": "clarify", "selected_capability": None},
            execution={"total": 2, "answer": "GPU1とNIC1を確認しました。"},
            executed_capabilities=("pkb_search",),
        )
        self.assertEqual(decision["next_step"], "clarify")
        self.assertIn("GPU1", decision["message"])
        self.assertIn("GPUドライバー", decision["question"])


if __name__ == "__main__":
    unittest.main()
