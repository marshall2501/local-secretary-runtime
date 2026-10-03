from __future__ import annotations

import unittest

from ritsuko.core.core_synthesis import synthesize


PERMISSIONS = {
    "pkb_read": True,
    "finance_read": True,
    "web_research": True,
    "external_actions": False,
}


class CoreSynthesisTests(unittest.TestCase):
    def test_ambiguous_guard_narrows_compare_to_local_pkb(self):
        decision = synthesize(
            {"status": "question", "selected_capability": None},
            {
                "status": "ok",
                "next_step": "observe",
                "proposed_action": "pkb_web_compare",
            },
            permissions=PERMISSIONS,
        )
        self.assertEqual(decision.next_step, "observe")
        self.assertEqual(decision.selected_capability, "pkb_search")
        self.assertEqual(decision.scope_adjustment, "narrowed_to_safe_prefix")

    def test_ambiguous_guard_allows_safe_local_probe(self):
        decision = synthesize(
            {"status": "question", "selected_capability": None},
            {
                "status": "ok",
                "next_step": "observe",
                "proposed_action": "pkb_search",
            },
            permissions=PERMISSIONS,
        )
        self.assertEqual(decision.next_step, "observe")
        self.assertEqual(decision.selected_capability, "pkb_search")
        self.assertEqual(decision.scope_adjustment, "safe_local_probe")

    def test_ambiguous_guard_blocks_external_web_expansion(self):
        decision = synthesize(
            {"status": "question", "selected_capability": None},
            {
                "status": "ok",
                "next_step": "observe",
                "proposed_action": "web_research",
            },
            permissions=PERMISSIONS,
        )
        self.assertEqual(decision.next_step, "clarify")
        self.assertIsNone(decision.selected_capability)
        self.assertEqual(decision.scope_adjustment, "external_scope_blocked")

    def test_ready_local_guard_narrows_broader_casper_proposal(self):
        decision = synthesize(
            {"status": "ready", "selected_capability": "pkb_search"},
            {
                "status": "ok",
                "next_step": "observe",
                "proposed_action": "pkb_web_compare",
            },
            permissions=PERMISSIONS,
        )
        self.assertEqual(decision.selected_capability, "pkb_search")
        self.assertEqual(decision.scope_adjustment, "narrowed_to_safe_prefix")

    def test_same_finance_observation_is_kept(self):
        decision = synthesize(
            {"status": "ready", "selected_capability": "finance_read"},
            {
                "status": "ok",
                "next_step": "observe",
                "proposed_action": "finance_read",
            },
            permissions=PERMISSIONS,
        )
        self.assertEqual(decision.next_step, "observe")
        self.assertEqual(decision.selected_capability, "finance_read")
        self.assertIsNone(decision.scope_adjustment)

    def test_casper_clarification_does_not_block_bounded_melchior_read(self):
        decision = synthesize(
            {"status": "ready", "selected_capability": "pkb_search"},
            {
                "status": "ok",
                "next_step": "clarify",
                "proposed_action": None,
            },
            permissions=PERMISSIONS,
        )
        self.assertEqual(decision.next_step, "observe")
        self.assertEqual(decision.selected_capability, "pkb_search")
        self.assertEqual(decision.scope_adjustment, "clarification_deferred")


if __name__ == "__main__":
    unittest.main()
