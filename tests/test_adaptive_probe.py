"""Offline tests for the cross-domain next-action experiment (no Ollama or DB)."""
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

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
    def test_selected_model_is_passed_to_ollama(self):
        proposal = decision("wait")
        with patch.object(probe.ask, "ollama",
                          return_value=json.dumps(proposal)) as mock:
            result = probe.choose_next({"goal": "fictional task"},
                                       model="local-other:latest")
        self.assertEqual(result["action"], "wait")
        self.assertEqual(mock.call_args.kwargs["model"], "local-other:latest")
        self.assertTrue(mock.call_args.kwargs["json_output"])

    def test_checkpoints_are_isolated_by_model(self):
        scenario = fixture("adaptive_game.json")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "trial.json"
            initial = probe.load_state(path, scenario, model="qwen3:8b")
            probe.checkpoint(path, initial)
            continued = probe.load_state(path, scenario, model="qwen3:8b")
            self.assertEqual(continued["trial_id"], initial["trial_id"])
            with self.assertRaisesRegex(ValueError, "Checkpoint uses"):
                probe.load_state(path, scenario, model="another-model:9b")

    def test_per_request_ollama_model_override(self):
        with patch.object(probe.ask, "request_json",
                          return_value={"message": {"content": "{}"}}) as req:
            probe.ask.ollama([{"role": "user", "content": "test"}],
                             model="another-model:9b")
        self.assertEqual(req.call_args.kwargs["body"]["model"],
                         "another-model:9b")

    def test_recall_separates_named_entity_from_unlinked_history(self):
        scenario = fixture("adaptive_game.json")
        state = probe.load_state(Path("/no/existing/checkpoint/needed"), scenario)
        history = {"total": 3, "items": [
            {"task_id": "linked", "entity_name": "架空テストPC",
             "tool": "prototype_mock", "summary": "Linked simulated trial",
             "evidence": {"simulated": True}},
            {"task_id": "different", "entity_name": "別PC",
             "tool": "prototype_mock", "summary": "Other device"},
            {"task_id": "unlinked", "entity_name": None,
             "tool": "prototype_mock", "summary": "Unknown exact PC"},
        ]}
        event = probe.step(
            scenario, state, chooser=lambda _: decision(
                "memory_search", query="架空テストPC", domain="pc"
            ),
            memory_reader=lambda q, d: {"total": 0, "items": []},
            experience_reader=lambda d: history,
        )
        recalled = event["observation"]["past_actions_and_results"]
        self.assertEqual([r["task_id"] for r in recalled],
                         ["linked", "unlinked"])
        self.assertEqual(recalled[0]["association"], "explicit_entity_match")
        self.assertEqual(recalled[1]["association"], "domain_only_unlinked")
        self.assertTrue(recalled[0]["simulated"])

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
