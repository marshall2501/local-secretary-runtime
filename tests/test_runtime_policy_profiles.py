"""Isolated owner-managed policy tests. No PostgreSQL or cloud requests."""
from __future__ import annotations

from pathlib import Path
import unittest

from ritsuko.application.context_policy import (
    endpoint_scope, restrict_members, validate_policy_snapshot,
    redact_observation_text,
)
from ritsuko.magi.async_execution import (
    _private_observation_local_scope, _send_async, start_dialogue_async,
)


def profile(name="verification-lan", decision="allow", revision=1):
    return {
        "profile_id": "10000000-0000-0000-0000-000000000001",
        "profile_name": name, "revision": revision, "exposure": "lan",
        "grants": [
            {"label": "llm.send_context.private",
             "destination_scope": "any", "decision": decision,
             "mask_rules": {"tokens": ["SENSITIVE-123"] if decision == "redact" else []}},
            {"label": "llm.send_context.sensitive",
             "destination_scope": "any", "decision": decision,
             "mask_rules": {"tokens": ["SENSITIVE-123"] if decision == "redact" else []}},
        ],
    }


def specs():
    return [
        {"name": "MELCHIOR", "provider": "ollama",
         "endpoint": "http://secretary-ollama:11434", "model": "gemma4:12b", "enabled": True},
        {"name": "CASPER", "provider": "openai",
         "endpoint": "https://api.openai.com/v1", "model": "gpt-test", "enabled": True},
        {"name": "BALTHASAR", "provider": "ollama",
         "endpoint": "https://llm.example.com", "model": "remote-ollama", "enabled": True},
    ]


def session(policy):
    return {
        "policy_snapshot": policy, "member_specs": specs(),
        "observations": [
            {"source": "pkb", "verified": True, "confidentiality": "private",
             "text": "My id SENSITIVE-123"}
        ],
        "status": "running",
    }


class RuntimePolicyPureTests(unittest.TestCase):
    def test_verification_profile_allows_all_enabled_members(self):
        s = session(profile())
        members, context = _private_observation_local_scope(s)
        self.assertEqual([m["name"] for m in members],
                         ["MELCHIOR", "CASPER", "BALTHASAR"])
        self.assertEqual(context["mode"], "configured_policy")
        self.assertEqual(s["cloud_context_gate"]["status"], "applied")
        self.assertNotIn("redaction_tokens", s["cloud_context_gate"])

    def test_production_blocks_remote_even_for_ollama(self):
        p = profile(name="production-lan")
        p["grants"] = [
            {"label": "llm.send_context.private", "destination_scope": "local",
             "decision": "allow", "mask_rules": {}},
            {"label": "llm.send_context.private", "destination_scope": "remote",
             "decision": "deny", "mask_rules": {}},
        ]
        s = session(p)
        members, context = _private_observation_local_scope(s)
        self.assertEqual([m["name"] for m in members], ["MELCHIOR"])
        self.assertEqual(len(context["withheld_members"]), 2)

    def test_all_denied_stops_task(self):
        s = session(profile(decision="deny"))
        members, context = _private_observation_local_scope(s)
        self.assertEqual(members, [])
        self.assertIsNone(context)
        self.assertEqual(s["status"], "stopped")
        self.assertEqual(s["next_step"], "cloud_context_gate_no_allowed_member")

    def test_missing_snapshot_retains_existing_legacy_gate(self):
        s = session(None)
        members, context = _private_observation_local_scope(s)
        self.assertEqual([m["name"] for m in members],
                         ["MELCHIOR", "BALTHASAR"])
        self.assertEqual(context["mode"], "local_only_private_pkb")

    def test_unknown_or_missing_endpoint_is_not_local(self):
        self.assertEqual(endpoint_scope({"provider": "ollama",
                                         "endpoint": "https://llm.example.com"}), "remote")
        self.assertEqual(endpoint_scope({"provider": "ollama"}), "remote")
        self.assertEqual(endpoint_scope({"provider": "openai",
                                         "endpoint": "http://127.0.0.1:9000"}), "local")

    def test_snapshot_validation_and_rule_precedence(self):
        with self.assertRaises(ValueError):
            validate_policy_snapshot({"revision": 0, "profile_id": "x", "grants": []})
        p = profile()
        p["grants"].append({"label": "llm.send_context.private",
                            "destination_scope": "remote",
                            "decision": "deny", "mask_rules": {}})
        members, _ = restrict_members(session(p), p)
        self.assertEqual([m["name"] for m in members], ["MELCHIOR"])

    def test_sensitive_requires_its_own_allow(self):
        p = profile()
        p["grants"] = [p["grants"][0]]
        s = session(p)
        s["observations"].append({"source": "finance", "verified": True,
                                   "confidentiality": "sensitive", "text": "123"})
        members, _ = restrict_members(s, p)
        self.assertEqual(members, [])

    def test_unverified_observation_does_not_trigger_restriction(self):
        s = session(profile(decision="deny"))
        s["observations"][0]["verified"] = False
        members, context = restrict_members(s, s["policy_snapshot"])
        self.assertIsNone(members)
        self.assertIsNone(context)

    def test_literal_masking_not_regex(self):
        self.assertEqual(redact_observation_text("A.B A+B", ["A.B"]), "[MASKED] A+B")

    def test_mask_values_not_stored_in_diagnostic(self):
        s = session(profile(decision="redact"))
        _, context = restrict_members(s, s["policy_snapshot"])
        self.assertEqual(context["redaction_tokens"], ["SENSITIVE-123"])
        self.assertNotIn("SENSITIVE-123", str(s["cloud_context_gate"]))


class RuntimePolicyAsyncTests(unittest.IsolatedAsyncioTestCase):
    async def test_mask_applied_before_any_provider_receives_envelope(self):
        captured = []

        async def stub(envelope, *, model, timeout, member_specs):
            captured.append(envelope)
            return {"status": "unavailable", "response": None, "errors": ["fixture"],
                    "diagnostic": {}}

        raw = session(profile(decision="redact"))
        _, policy = restrict_members(raw, raw["policy_snapshot"])
        raw.update({
            "turns": [], "task_id": "fixture-1", "prompt_version": "test",
            "model": "", "user_raw": "SENSITIVE-123",
            "classification": {"state": "fixture"}, "detail": None,
            "pending_requests": [],
        })
        await _send_async(
            raw, "analyze", "fixture", "SENSITIVE-123",
            caller=stub, timeout=1, context_policy=policy
        )
        self.assertEqual(len(captured), 1)
        self.assertNotIn("SENSITIVE-123", str(captured[0]))
        self.assertEqual(raw["turns"][0]["request_envelope"]["user_input"]["raw"],
                         "[MASKED]")
        self.assertNotIn("redaction_tokens", str(raw["turns"][0]["context_policy"]))

    async def test_new_task_has_immutable_policy_snapshot(self):
        captured = []
        async def stub(envelope, *, model, timeout, member_specs):
            captured.append(envelope)
            return {"status": "unavailable", "response": None,
                    "errors": ["fixture"], "diagnostic": {}}
        p = profile()
        task = await start_dialogue_async(
            "sample input", member_specs=specs(), policy_snapshot=p,
            caller=stub, timeout=1,
        )
        p["revision"] = 99
        self.assertEqual(task["policy_snapshot"]["revision"], 1)
        self.assertTrue(captured)


class RuntimePolicySchemaContractTests(unittest.TestCase):
    def test_migration_and_fresh_initializer_list_are_in_sync(self):
        root = Path(__file__).resolve().parents[1]
        sql = (root / "db/migrations/009_runtime_policy_profiles.sql").read_text(
            encoding="utf-8"
        )
        init = (root / "scripts/db/initialize-fresh-production.ps1").read_text(
            encoding="utf-8"
        )
        for table in (
            "permission_labels", "policy_profiles",
            "policy_grants", "policy_activations"
        ):
            self.assertIn("CREATE TABLE " + table, sql)
        self.assertIn("009_runtime_policy_profiles.sql", init)
        self.assertIn("9|009_runtime_policy_profiles.sql", init)
        self.assertIn("('lan', '10000000-0000-0000-0000-000000000002')", sql)


if __name__ == "__main__":
    unittest.main()
