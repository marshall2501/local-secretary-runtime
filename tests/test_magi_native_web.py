from __future__ import annotations

import unittest
from types import SimpleNamespace

from ritsuko.application.context_policy import public_web_search_permission
from ritsuko.magi.native_web import (
    gemini_web_result,
    openai_web_result,
    search_magi_web,
)


def _policy(*grants):
    return {
        "profile_id": "fixture-lan",
        "profile_name": "fixture", "revision": 2,
        "grants": list(grants),
    }


def _grant(label, scope, decision):
    return {"label": label, "destination_scope": scope, "decision": decision,
            "mask_rules": {"tokens": []}}


class WebToolPolicyTests(unittest.TestCase):
    def test_web_search_uses_existing_profile_grant_not_llm_context_grant(self):
        only_context = _policy(
            _grant("llm.send_context.private", "any", "allow"))
        self.assertFalse(public_web_search_permission(only_context)["allowed"])
        self.assertEqual(
            public_web_search_permission(only_context)["reason"],
            "web_search_missing")
        allowed = _policy(_grant("web.search", "remote", "allow"))
        self.assertTrue(public_web_search_permission(allowed)["allowed"])
        self.assertEqual(
            public_web_search_permission(allowed)["policy_revision"], 2)

    def test_remote_search_cannot_be_authorized_by_local_grant(self):
        policy = _policy(
            _grant("web.search", "local", "allow"),
            _grant("web.search", "remote", "deny"))
        self.assertFalse(public_web_search_permission(policy)["allowed"])
        self.assertEqual(
            public_web_search_permission(policy)["reason"], "web_search_deny")

    def test_missing_snapshot_or_confirmation_fails_closed(self):
        self.assertFalse(public_web_search_permission(None)["allowed"])
        pending = _policy(_grant("web.search", "remote", "require_confirmation"))
        self.assertFalse(public_web_search_permission(pending)["allowed"])

    def test_duplicate_web_grants_rejected(self):
        snap = _policy(
            _grant("web.search", "any", "allow"),
            _grant("web.search", "any", "deny"))
        with self.assertRaises(ValueError):
            public_web_search_permission(snap)


class WebResultContractTests(unittest.TestCase):
    def test_openai_requires_real_tool_call_and_citation(self):
        response = {
            "output": [
                {"type": "web_search_call", "status": "completed",
                 "action": {"type": "search"}},
                {"type": "message", "content": [
                    {"type": "output_text", "text": "Default is None.",
                     "annotations": [
                         {"type": "url_citation", "title": "Official Python docs",
                          "url": "https://docs.python.org/3/library/stdtypes.html"},
                         {"type": "url_citation", "url": "http://localhost/private"},
                     ]},
                ]},
            ],
        }
        result = openai_web_result(response)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(len(result["citations"]), 1)
        self.assertEqual(openai_web_result({
            "output": response["output"][1:]
        })["status"], "no_search")
        self.assertEqual(openai_web_result({
            "output": response["output"][:1]
        })["status"], "no_citations")

    def test_gemini_requires_grounding_metadata_not_hallucinated_link(self):
        grounded = {
            "candidates": [{"content": {"parts": [{"text": "Missing key returns None."}]},
                            "groundingMetadata": {
                                "webSearchQueries": ["python dict.get official"],
                                "groundingChunks": [{"web": {
                                    "uri": "https://docs.python.org/3/library/stdtypes.html",
                                    "title": "Mapping Types"}},
                                ]}}],
        }
        self.assertEqual(gemini_web_result(grounded)["status"], "ok")
        grounded["candidates"][0]["groundingMetadata"]["webSearchQueries"] = []
        self.assertEqual(gemini_web_result(grounded)["status"], "no_citations")


class WebToolPanelTests(unittest.IsolatedAsyncioTestCase):
    async def test_three_members_receive_same_query_and_independent_tool_access(self):
        calls = []
        query = "Python dict.get official documentation"
        def fake_local_search(text):
            self.assertEqual(text, query)
            return SimpleNamespace(as_dict=lambda: {"hits": [{
                "title": "Python docs", "url": "https://docs.python.org/3/library/stdtypes.html",
                "fetch_status": "fetched", "excerpt": "If key is in dictionary..."
            }]})

        async def transport(method, url, *, headers, json_body, timeout,
                            retry_enabled, retry_http_codes):
            calls.append((url, json_body))
            if url.endswith("/responses"):
                self.assertEqual(json_body["input"], query)
                self.assertEqual(json_body["tools"][0]["type"], "web_search")
                return {
                    "output": [
                        {"type": "web_search_call", "status": "completed"},
                        {"type": "message", "content": [{
                            "type": "output_text", "text": "OpenAI grounded answer",
                            "annotations": [{"type": "url_citation",
                                             "url": "https://docs.python.org/3/",
                                             "title": "Python"}]}]},
                    ],
                    "usage": {"input_tokens": 17, "output_tokens": 20},
                }, {}
            if ":generateContent" in url:
                self.assertEqual(json_body["tools"], [{"google_search": {}}])
                return {
                    "candidates": [{"content": {"parts": [{"text": "Gemini answer"}]},
                                    "groundingMetadata": {
                                        "webSearchQueries": ["dict get"],
                                        "groundingChunks": [{"web": {
                                            "uri": "https://docs.python.org/ja/3/",
                                            "title": "Python docs"}}]}}],
                }, {}
            self.assertTrue(url.endswith("/api/chat"), url)
            self.assertNotIn("Authorization", headers)
            if json_body.get("tools"):
                self.assertEqual(len(json_body["tools"]), 1)
                return {
                    "message": {"role": "assistant", "content": "",
                                "tool_calls": [{"function": {
                                    "name": "web_search", "arguments": {"query": query}
                                }}]},
                }, {}
            self.assertEqual(json_body["messages"][-1]["tool_name"], "web_search")
            return {"message": {"role": "assistant", "content": "Ollama answer"}}, {}

        member_specs = [
            {"name": "CASPER", "provider": "openai", "model": "gpt-test",
             "enabled": True, "endpoint": "https://api.openai.com/v1"},
            {"name": "BALTHASAR", "provider": "gemini", "model": "gemini-test",
             "enabled": True, "endpoint": "https://generativelanguage.googleapis.com/v1beta"},
            {"name": "MELCHIOR", "provider": "ollama", "model": "local-test",
             "enabled": True, "endpoint": "http://127.0.0.1:11434"},
        ]
        out = await search_magi_web(
            query, member_specs, transport=transport,
            credential_loader=lambda *_: "synthetic-credential",
            local_search=fake_local_search,
        )
        self.assertEqual(out["status"], "ok")
        self.assertEqual({x["provider"] for x in out["result"]["member_runs"]},
                         {"openai", "gemini", "ollama"})
        self.assertEqual(len(out["result"]["hits"]), 3)
        self.assertEqual(out["verified_by"], "provider_reported_web_citations")
        self.assertEqual(len(calls), 4)
        self.assertNotIn("synthetic-credential", str(out))

    async def test_one_failed_member_does_not_block_cloud_evidence(self):
        query = "public source search"
        async def transport(method, url, *, headers, json_body, timeout,
                            retry_enabled, retry_http_codes):
            if "/api/chat" in url:
                raise TimeoutError("local_model_timeout")
            return {
                "output": [{"type": "web_search_call", "status": "completed"},
                           {"type": "message", "content": [{
                               "type": "output_text", "text": "Result",
                               "annotations": [{"type": "url_citation",
                                                "url": "https://docs.python.org/3/"}]
                           }]}]
            }, {}
        out = await search_magi_web(query, [
            {"name": "CASPER", "provider": "openai", "model": "test",
             "endpoint": "https://api.openai.com/v1"},
            {"name": "MELCHIOR", "provider": "ollama", "model": "test",
             "endpoint": "http://localhost:11434"},
        ], transport=transport, credential_loader=lambda *_: "fixture")
        self.assertEqual(out["status"], "ok")
        self.assertEqual(out["result"]["member_runs"][1]["status"], "unavailable")
        self.assertEqual(len(out["result"]["hits"]), 1)

    async def test_ollama_tool_cannot_change_approved_public_query(self):
        async def transport(method, url, *, headers, json_body, timeout,
                            retry_enabled, retry_http_codes):
            return {"message": {"tool_calls": [{"function": {
                "name": "web_search", "arguments": {"query": "http://localhost/admin"}
            }}]}}, {}
        out = await search_magi_web(
            "public driver query",
            [{"name": "MELCHIOR", "provider": "ollama", "model": "local",
              "endpoint": "http://localhost:11434"}],
            transport=transport,
            local_search=lambda _: self.fail("Must not search model-controlled URL"),
        )
        self.assertEqual(out["status"], "error")
        self.assertEqual(out["result"]["member_runs"][0]["status"], "no_tool_call")


class WebTaskRoundtripTests(unittest.IsolatedAsyncioTestCase):
    async def test_cited_web_results_return_to_same_task_as_bounded_observation(self):
        import anyio
        from ritsuko.application.observation_sources import (
            verified_source_observation, web_query_from_request,
        )
        from ritsuko.core.magi_bridge import task_projection
        from ritsuko.core.task_execution import run_observation_loop

        query = "Public official Python dict.get definition"
        events = []
        specs = [{"name": "CASPER", "enabled": True, "provider": "openai",
                  "model": "fixture", "endpoint": "https://api.openai.com/v1"}]
        policy = _policy(_grant("web.search", "remote", "allow"))

        async def transport(method, url, *, headers, json_body, timeout,
                            retry_enabled, retry_http_codes):
            events.append(("external", json_body["input"]))
            return {
                "output": [
                    {"type": "web_search_call", "status": "completed"},
                    {"type": "message", "content": [
                        {"type": "output_text",
                         "text": "dict.get returns default if the key is missing.",
                         "annotations": [{
                             "type": "url_citation",
                             "url": "https://docs.python.org/3/library/stdtypes.html",
                             "title": "Mapping Types",
                         }]}
                    ]},
                ]
            }, {}

        async def starter(request, *, task_id, **kwargs):
            return {
                "task_id": task_id, "user_raw": request,
                "status": "waiting_information",
                "member_specs": specs, "policy_snapshot": policy,
                "pending_requests": [{
                    "request_id": "REQ-1", "source": "web", "what": query,
                    "reason": "official evidence requested",
                }],
                "observations": [], "turns": [{"stage": "classify"}, {"stage": "analyze"}],
            }

        def execute(raw, pending, session):
            events.append(("read", pending["source"]))
            self.assertTrue(public_web_search_permission(
                session["policy_snapshot"])["allowed"])
            safe = web_query_from_request(pending, session)
            return anyio.run(
                lambda: search_magi_web(
                    safe, session["member_specs"], transport=transport,
                    credential_loader=lambda *_: "synthetic-credential",
                )
            )

        def record(task_id, execution, pending):
            events.append(("record", execution["verified_by"], str(task_id)))
            self.assertEqual(execution["source"], "web")
            self.assertEqual(execution["status"], "ok")
            self.assertEqual(execution["result"]["hits"][0]["provider"], "openai")
            self.assertEqual(
                verified_source_observation(execution, pending)["source_metadata"]
                ["member_runs"][0]["provider"], "openai")
            return ("action-id", "result-id")

        async def continue_observation(session, observations, **kwargs):
            self.assertEqual(len(observations), 1)
            self.assertEqual(observations[0]["source"], "web")
            self.assertTrue(observations[0]["verified"])
            self.assertIn("https://docs.python.org", observations[0]["text"])
            return {
                **session, "status": "candidate_ready", "pending_requests": [],
                "observations": observations, "tool_read_executed": True,
                "detail": {"answer_candidate":
                           "dict.get returns default; source https://docs.python.org/3/library/stdtypes.html"},
            }

        def persist(task_id, session, selected):
            events.append(("persist", str(task_id), session["status"]))
            self.assertEqual(task_projection(session)["task_status"], "completed")

        result = await run_observation_loop(
            "Use official docs to explain dict.get",
            member_specs=specs, timeout=20,
            create_task_record=lambda *args: events.append(("create",)),
            execute_source_request=execute,
            record_source_read_record=record,
            persist_session_record=persist,
            fail_task_record=lambda *args: self.fail("must not fail Task"),
            dialogue_starter=starter,
            observation_continuation=continue_observation,
            resource_catalog={"web": {"available": True}},
        )
        self.assertEqual(result["status"], "candidate_ready")
        self.assertEqual(
            [event[0] for event in events],
            ["create", "read", "external", "record", "persist"],
        )


if __name__ == "__main__":
    unittest.main()
