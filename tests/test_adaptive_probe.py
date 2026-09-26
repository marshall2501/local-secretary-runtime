"""Offline tests for the cross-domain next-action experiment (no Ollama or DB)."""
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "secretary"
sys.path.insert(0, str(SCRIPT))
spec = importlib.util.spec_from_file_location("adaptive_probe", SCRIPT / "adaptive_probe.py")
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


def fixture(name):
    return json.loads((SCRIPT / "fixtures" / name).read_text(encoding="utf-8"))


def decision(action, **kwargs):
    return {"action": action, "reason": "Expected information from previous evidence",
            "expected_observation": "New fact for the next decision", **kwargs}


class AdaptiveProbeTests(unittest.TestCase):
    def test_new_evidence_changes_next_action(self):
        scenario = fixture("adaptive_game.json")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            state = probe.load_state(path, scenario)

            def choose(ctx):
                events = ctx["previous_decisions_and_observations"]
                if not events:
                    return decision("memory_search", query="架空ゲームA", domain="pc")
                if events[-1]["decision"]["action"] == "memory_search":
                    return decision("research", query="crash")
                if events[-1]["decision"]["action"] == "research":
                    return decision("simulated_tool", tool="read_crash_log")
                return decision("research", query="driver")

            fake_memory = lambda q, domain: {"total": 0, "items": []}
            observed = []
            for _ in range(4):
                e = probe.step(scenario, state, chooser=choose,
                               memory_reader=fake_memory)
                observed.append(e["decision"]["action"])
                probe.checkpoint(path, state)
            self.assertEqual(observed, ["memory_search", "research",
                                        "simulated_tool", "research"])
            self.assertEqual(state["events"][2]["observation"]["simulated"], True)
            reloaded = probe.load_state(path, scenario)
            self.assertEqual(len(reloaded["events"]), 4)
            self.assertEqual(reloaded["events"][0]["observation"]["total"], 0)

    def test_identical_action_does_not_loop_without_reassessment(self):
        scenario = fixture("adaptive_shopping.json")
        state = probe.load_state(Path("/no/existing/checkpoint/needed"), scenario)
        chooser = lambda _: decision("research", query="notebook")
        first = probe.step(scenario, state, chooser=chooser)
        second = probe.step(scenario, state, chooser=chooser)
        self.assertIn("sources", first["observation"])
        self.assertEqual(second["observation"]["status"], "review_needed")
        self.assertEqual(state["status"], "waiting_replan")

    def test_japanese_free_form_query_browses_existing_local_sources(self):
        scenario = fixture("adaptive_game.json")
        result = probe.research_local(
            scenario, "ドライババージョン2.0の更新内容と、ゲームAのクラッシュ原因に関する情報"
        )
        self.assertEqual(result["total_matches"], 0)
        self.assertEqual(result["browse_total"], 2)
        self.assertEqual(len(result["sources"]), 2)
        self.assertIn("catalog browse", result["coverage"])
        self.assertTrue(all(s["origin"] == "fictional local research fixture"
                            for s in result["sources"]))

    def test_non_machine_scenario_uses_same_core(self):
        scenario = fixture("adaptive_shopping.json")
        state = probe.load_state(Path("/no/existing/checkpoint/needed"), scenario)
        event = probe.step(scenario, state,
                           chooser=lambda _: decision("simulated_tool", tool="check_stock_x"))
        self.assertEqual(state["domain"], "shopping")
        self.assertFalse(event["observation"]["observation"]["in_stock"])

    def test_completion_claim_requires_independent_verification(self):
        scenario = fixture("adaptive_shopping.json")
        state = probe.load_state(Path("/no/existing/checkpoint/needed"), scenario)
        event = probe.step(scenario, state,
                           chooser=lambda _: decision("propose_complete"))
        self.assertEqual(event["observation"]["completion"], "proposed_only")
        self.assertEqual(state["status"], "awaiting_verification")

    def test_user_question_persists_and_can_resume(self):
        scenario = fixture("adaptive_shopping.json")
        state = probe.load_state(Path("/no/existing/checkpoint/needed"), scenario)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            probe.step(scenario, state,
                       chooser=lambda _: decision("ask_user", question="配送期限は？"))
            self.assertEqual(state["status"], "waiting_user")
            probe.checkpoint(path, state)
            reloaded = probe.load_state(path, scenario)
            self.assertEqual(reloaded["events"][0]["observation"]["question"], "配送期限は？")
            reloaded["user_replies"].append("来週")
            reloaded["status"] = "active"
            probe.checkpoint(path, reloaded)
            self.assertEqual(probe.load_state(path, scenario)["user_replies"], ["来週"])


if __name__ == "__main__":
    unittest.main()
