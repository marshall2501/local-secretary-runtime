"""Isolated owner-managed policy tests. No PostgreSQL or cloud requests."""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts.db.probe_runtime_policy_readonly import (
    configured_port,
    evaluate_synthetic_observations,
)
from scripts.db.probe_magi_cloud_synthetic import (
    ProbeError, select_cloud_specs, synthetic_session, validate_endpoint,
)

from ritsuko.application.context_policy import (
    endpoint_scope, restrict_members, resolve_grant, validate_policy_snapshot,
    redact_observation_text,
)
from ritsuko.magi.async_execution import (
    _private_observation_local_scope, _send_async, start_dialogue_async,
    continue_with_verified_observations_async,
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
        self.assertEqual(len(members), 3)
        self.assertEqual(context["mode"], "verification_unrestricted")
        self.assertEqual(s["cloud_context_gate"]["status"], "bypassed")
        self.assertNotIn("redaction_tokens", s["cloud_context_gate"])

    def test_verification_bypasses_denial_and_redaction(self):
        p = profile(name="verification-lan", decision="deny")
        s = session(p)
        members, context = restrict_members(s, p)
        self.assertEqual(len(members), 3)
        self.assertEqual(context["redaction_tokens"], [])
        self.assertEqual(s["status"], "running")
        self.assertEqual(s["cloud_context_gate"]["mode"], "verification_unrestricted")

    def test_production_profile_remains_configurable(self):
        s = session(profile(name="production-lan", decision="redact"))
        members, context = restrict_members(s, s["policy_snapshot"])
        self.assertEqual(len(members), 3)
        self.assertEqual(context["redaction_tokens"], ["SENSITIVE-123"])

    def test_verification_remote_grant_inherits_any_allow(self):
        # Initial Migration 009 inserts 'any/allow' only; the settings UI
        # must not invent an explicit remote deny where no rule exists.
        rules = profile()["grants"]
        for scope in ("local", "remote"):
            selected, origin = resolve_grant(
                rules, "llm.send_context.private", scope
            )
            self.assertEqual(selected["decision"], "allow")
            self.assertEqual(selected["destination_scope"], "any")
            self.assertEqual(origin, "inherited")

    def test_exact_remote_grant_overrides_inherited_allow(self):
        rules = profile()["grants"]
        rules.append({
            "label": "llm.send_context.private",
            "destination_scope": "remote",
            "decision": "deny",
            "mask_rules": {},
        })
        remote, remote_origin = resolve_grant(
            rules, "llm.send_context.private", "remote"
        )
        local, local_origin = resolve_grant(
            rules, "llm.send_context.private", "local"
        )
        self.assertEqual((remote["decision"], remote_origin), ("deny", "exact"))
        self.assertEqual((local["decision"], local_origin), ("allow", "inherited"))

    def test_missing_grant_is_not_a_stored_deny(self):
        found, origin = resolve_grant(
            profile()["grants"], "llm.send_context.other", "remote"
        )
        self.assertIsNone(found)
        self.assertEqual(origin, "missing")

    def test_settings_ui_uses_same_grant_precedence_as_runtime(self):
        root = Path(__file__).resolve().parents[1]
        settings = (root / "interfaces/web/pages/settings.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("resolve_grant(grants, label, scope)", settings)
        self.assertIn("すべての送信先から継承", settings)
        self.assertIn("該当Grantなし／未設定", settings)

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
        s = session(profile(name="production-lan", decision="deny"))
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
        p = profile(name="production-lan")
        p["grants"].append({"label": "llm.send_context.private",
                            "destination_scope": "remote",
                            "decision": "deny", "mask_rules": {}})
        members, _ = restrict_members(session(p), p)
        self.assertEqual([m["name"] for m in members], ["MELCHIOR"])

    def test_sensitive_requires_its_own_allow(self):
        p = profile(name="production-lan")
        p["grants"] = [p["grants"][0]]
        s = session(p)
        s["observations"].append({"source": "finance", "verified": True,
                                   "confidentiality": "sensitive", "text": "123"})
        members, _ = restrict_members(s, p)
        self.assertEqual(members, [])

    def test_unverified_observation_does_not_trigger_restriction(self):
        s = session(profile(name="production-lan", decision="deny"))
        s["observations"][0]["verified"] = False
        members, context = restrict_members(s, s["policy_snapshot"])
        self.assertIsNone(members)
        self.assertIsNone(context)

    def test_literal_masking_not_regex(self):
        self.assertEqual(redact_observation_text("A.B A+B", ["A.B"]), "[MASKED] A+B")

    def test_mask_values_not_stored_in_diagnostic(self):
        s = session(profile(name="production-lan", decision="redact"))
        _, context = restrict_members(s, s["policy_snapshot"])
        self.assertEqual(context["redaction_tokens"], ["SENSITIVE-123"])
        self.assertNotIn("SENSITIVE-123", str(s["cloud_context_gate"]))


class RuntimePolicyLiveSyntheticPreflightTests(unittest.TestCase):
    """Preflight is entirely offline and never resolves credentials."""

    @staticmethod
    def configured_cloud_members():
        return [
            {"name": "MELCHIOR", "provider": "ollama",
             "endpoint": "http://secretary-ollama:11434",
             "model": "fixture-local", "enabled": True},
            {"name": "CASPER", "provider": "openai",
             "endpoint": "https://api.openai.com/v1",
             "connection_id": "fixture-o", "model": "fixture-openai",
             "enabled": True},
            {"name": "BALTHASAR", "provider": "gemini",
             "endpoint": "https://generativelanguage.googleapis.com/v1beta",
             "connection_id": "fixture-g", "model": "fixture-gemini",
             "enabled": True},
        ]

    def test_approved_profile_only_selects_two_cloud_members(self):
        specs, ctx = select_cloud_specs(
            self.configured_cloud_members(), profile()
        )
        self.assertEqual([x["name"] for x in specs],
                         ["CASPER", "BALTHASAR"])
        self.assertTrue(all(not x["retry_within_turn"] for x in specs))
        self.assertTrue(all(x["timeout_seconds"] == 60 for x in specs))
        self.assertEqual(ctx["profile_name"], "verification-lan")
        state = synthetic_session(specs, profile())
        texts = " ".join(x["text"] for x in state["observations"])
        self.assertIn("FICTIONAL-", texts)
        self.assertEqual(len(state["turns"]), 0)

    def test_rejects_production_profile_even_with_cloud_members(self):
        with self.assertRaisesRegex(ProbeError, "not verification-lan"):
            select_cloud_specs(self.configured_cloud_members(),
                               profile(name="production-lan"))

    def test_unapproved_host_and_provider_redirect_are_refused(self):
        members = self.configured_cloud_members()
        for endpoint in (
            "http://api.openai.com/v1",
            "https://api.openai.com.evil.example/v1",
            "https://api.openai.com/v1?redirect=https://elsewhere",
            "https://api.openai.com/v2",
        ):
            changed = [dict(item) for item in members]
            changed[1]["endpoint"] = endpoint
            with self.assertRaises(ProbeError):
                select_cloud_specs(changed, profile())
        validate_endpoint(members[1])
        validate_endpoint(members[2])

    def test_no_live_request_without_explicit_flag(self):
        root = Path(__file__).resolve().parents[1]
        source = (root / "scripts/db/probe_magi_cloud_synthetic.py").read_text(
            encoding="utf-8"
        )
        self.assertIn('if not args.live_synthetic:', source)
        self.assertIn('options="-c default_transaction_read_only=on"', source)
        self.assertIn('register_connection_credential_loader(None)', source)


class RuntimePolicyReadonlyProbeTests(unittest.TestCase):
    def test_real_policy_synthetic_evaluation_accepts_fixture(self):
        self.assertEqual(
            evaluate_synthetic_observations(profile()), 
            ["MELCHIOR", "CASPER", "BALTHASAR"],
        )

    def test_probe_fails_when_production_profile_selected(self):
        p = profile(name="production-lan")
        with self.assertRaisesRegex(RuntimeError, "not verification-lan"):
            evaluate_synthetic_observations(p)

    def test_probe_refuses_port_outside_configured_range(self):
        with tempfile.TemporaryDirectory() as directory:
            filename = Path(directory) / ".env.postgres"
            filename.write_text("LSA_DB_PORT=55432" + chr(10), encoding="utf-8")
            self.assertEqual(configured_port(filename), 55432)
            filename.write_text("LSA_DB_PORT=2" + chr(10), encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "out of range"):
                configured_port(filename)

    def test_probe_enforces_read_only_connection_without_cloud_api(self):
        file = (Path(__file__).resolve().parents[1]
                / "scripts/db/probe_runtime_policy_readonly.py").read_text(
                    encoding="utf-8"
                )
        self.assertIn("default_transaction_read_only=on", file)
        self.assertIn("SHOW transaction_read_only", file)
        self.assertNotIn("request_json_with_retry", file)


class RuntimePolicyAsyncTests(unittest.IsolatedAsyncioTestCase):
    async def test_mask_applied_before_any_provider_receives_envelope(self):
        captured = []

        async def stub(envelope, *, model, timeout, member_specs):
            captured.append(envelope)
            return {"status": "unavailable", "response": None, "errors": ["fixture"],
                    "diagnostic": {}}

        raw = session(profile(name="production-lan", decision="redact"))
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


class RuntimePolicyRealAsyncPathTests(unittest.IsolatedAsyncioTestCase):
    """Run the real continuation, panel fan-out and provider routing with fake I/O."""

    @staticmethod
    def waiting_session(policy):
        s = session(deepcopy(policy))
        s["member_specs"] = specs()
        s["member_specs"][2].update(
            provider="gemini",
            endpoint="https://generativelanguage.googleapis.com/v1beta",
            model="fixture-gemini",
        )
        s.update(
            status="waiting_information",
            task_id="synthetic-permission-task",
            prompt_version="test",
            model="",
            user_raw="synthetic request SENSITIVE-123",
            turns=[],
            classification={"category": "INFORMATION"},
            detail={"state": "NEED_INFORMATION"},
            pending_requests=[{"request_id": "REQ-1", "source": "pkb"}],
        )
        return s

    @staticmethod
    def observation():
        return {
            "source": "pkb", "verified": True, "confidentiality": "sensitive",
            "text": "FICTIONAL SENSITIVE-123",
            "responds_to": ["REQ-1"],
        }

    async def execute_with_fake_providers(self, prepared):
        calls = []

        async def fake_provider(envelope, **kwargs):
            calls.append((envelope["magi_member"], envelope))
            return {
                "status": "unavailable", "response": None,
                "errors": ["synthetic_provider_result"], "diagnostic": {},
            }

        # No HTTP clients, real providers, DB, PKB or runtime credentials.
        with (
            patch("ritsuko.magi.async_execution._call_ollama_guided_async",
                  side_effect=fake_provider),
            patch("ritsuko.magi.async_execution._call_openai_guided_async",
                  side_effect=fake_provider),
            patch("ritsuko.magi.async_execution._call_gemini_guided_async",
                  side_effect=fake_provider),
        ):
            result = await continue_with_verified_observations_async(
                prepared, [self.observation()], timeout=1
            )
        return result, calls

    async def test_verification_allows_real_async_panel_provider_fanout(self):
        result, calls = await self.execute_with_fake_providers(
            self.waiting_session(profile())
        )
        self.assertEqual({name for name, _ in calls},
                         {"MELCHIOR", "CASPER", "BALTHASAR"})
        self.assertEqual(
            set(result["turns"][0]["diagnostic"]["enabled_members"]),
            {"MELCHIOR", "CASPER", "BALTHASAR"},
        )
        self.assertEqual(result["cloud_context_gate"]["status"], "bypassed")
        self.assertEqual(result["turns"][0]["question_purpose"],
                         "evaluate_observation")
        self.assertEqual(result["cloud_context_gate"]["profile_name"],
                         "verification-lan")

    async def test_production_filters_remote_before_real_async_panel(self):
        p = profile(name="production-lan", decision="deny")
        p["grants"] += [
            {"label": label, "destination_scope": "local",
             "decision": "allow", "mask_rules": {"tokens": []}}
            for label in ("llm.send_context.private", "llm.send_context.sensitive")
        ]
        result, calls = await self.execute_with_fake_providers(
            self.waiting_session(p)
        )
        self.assertEqual([name for name, _ in calls], ["MELCHIOR"])
        self.assertEqual(result["turns"][0]["diagnostic"]["enabled_members"],
                         ["MELCHIOR"])
        self.assertEqual(
            {item["name"] for item in result["cloud_context_gate"]["withheld_members"]},
            {"CASPER", "BALTHASAR"},
        )

    async def test_mask_applies_to_all_fake_provider_envelopes(self):
        result, calls = await self.execute_with_fake_providers(
            self.waiting_session(profile(name="production-lan", decision="redact"))
        )
        self.assertEqual({name for name, _ in calls},
                         {"MELCHIOR", "CASPER", "BALTHASAR"})
        for _, envelope in calls:
            self.assertNotIn("SENSITIVE-123", str(envelope))
            self.assertIn("[MASKED]", str(envelope["observations"]))
        self.assertNotIn("SENSITIVE-123",
                         str(result["turns"][0]["request_envelope"]))
        self.assertNotIn("SENSITIVE-123",
                         str(result["turns"][0]["context_policy"]))

    async def test_all_denied_stops_before_provider_calls(self):
        result, calls = await self.execute_with_fake_providers(
            self.waiting_session(profile(name="production-lan", decision="deny"))
        )
        self.assertEqual(calls, [])
        self.assertEqual(result["status"], "stopped")
        self.assertEqual(result["next_step"],
                         "cloud_context_gate_no_allowed_member")
        self.assertEqual(result["turns"], [])

    async def test_task_snapshot_not_overwritten_by_new_profile(self):
        original = profile()
        prepared = self.waiting_session(original)
        # Simulate a different currently-active Profile. Existing Tasks retain
        # their policy_snapshot; there is no DB lookup during continuation.
        original["grants"] = profile(decision="deny")["grants"]
        result, calls = await self.execute_with_fake_providers(prepared)
        self.assertEqual(result["policy_snapshot"]["grants"][0]["decision"],
                         "allow")
        self.assertEqual({name for name, _ in calls},
                         {"MELCHIOR", "CASPER", "BALTHASAR"})


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
