"""Slice A synthetic, isolated acceptance tests: no actual LLM/DB/network."""
from __future__ import annotations

import unittest
from functools import partial
from uuid import uuid4

from ritsuko.magi.async_execution import (
    start_dialogue_async, continue_with_verified_observations_async,
    continue_with_user_clarification_async,
)
from ritsuko.magi.goal_contract import validate_goal_turn
from ritsuko.magi.goal_loop import (
    start_goal_dialogue, continue_goal_observations, goal_task_projection,
    _run_goal_turn,
)
from ritsuko.core.observation_loop import run_observation_loop
from ritsuko.application.task_runtime import StateDrivenTaskApplication, TaskRuntimePorts


SPEC = [{"name": "CASPER", "provider": "openai",
         "model": "synthetic", "enabled": True, "weight": 1.0,
         "timeout_seconds": 30}]


def judgment(next_step, *, goal="Python の公開情報について回答する", answer=None,
             fulfillment="unknown", basis="insufficient", refs=None, request=None,
             question=None, reason="判断理由"):
    return {
        "goal": goal, "next": next_step, "fulfillment": fulfillment,
        "answer": answer, "evidence": {
            "basis": basis, "result_ids": list(refs or []),
            "assessment": "今回のGoalについて根拠の確認範囲を示す",
        },
        "read_request": request, "question": question, "reason": reason,
    }


class GoalContractTests(unittest.TestCase):
    def test_source_free_and_web_read_are_distinct_valid_proposals(self):
        direct = judgment("respond", answer="辞書はkey/value構造です。",
                          fulfillment="met", basis="not_required")
        read = judgment("request_read", request={
            "source": "web", "query": "Python dict.get official docs",
            "reason": "本人が公式資料を要求",
        })
        self.assertEqual(validate_goal_turn(direct), [])
        self.assertEqual(validate_goal_turn(read), [])

    def test_rejects_model_claim_of_missing_evidence(self):
        x = judgment("respond", answer="確認済みです", fulfillment="met",
                     basis="result_refs", refs=[])
        self.assertIn("evidence:missing_refs", validate_goal_turn(x))
        x["read_request"] = {"source": "external_service",
                             "query": "do something", "reason": "X"}
        self.assertIn("read_request:invalid", validate_goal_turn(x))

    def test_insufficient_but_recorded_result_may_explain_missing_coverage(self):
        """A cited search Result can be insufficient for direct-page verification."""
        existing = str(uuid4())
        x = judgment("request_read", fulfillment="partial", basis="insufficient",
                     refs=[existing], request={
                         "source": "web", "query": "official page source",
                         "reason": "provider citation is not direct content verification",
                     })
        self.assertEqual(validate_goal_turn(x), [])
        # Refs are never allowed when the model claims NO evidence is needed.
        x["evidence"]["basis"] = "not_required"
        self.assertIn("evidence:unexpected_refs", validate_goal_turn(x))
        x["evidence"]["basis"] = "insufficient"
        x["next"] = "respond"
        x["read_request"] = None
        x["answer"] = "Unverified source is sufficient"
        x["fulfillment"] = "met"
        self.assertIn("respond:insufficient_evidence", validate_goal_turn(x))

    def test_malformed_and_dangerous_state_pairs(self):
        x = judgment("respond", answer="result", fulfillment="unknown",
                     basis="not_required")
        self.assertIn("respond:answer_or_goal_missing", validate_goal_turn(x))
        x = judgment("ask_user")
        self.assertIn("ask_user:question_required", validate_goal_turn(x))


class GoalLoopTests(unittest.IsolatedAsyncioTestCase):
    async def test_llm_flow_uses_same_member_scope_and_masking_as_standard(self):
        async def first(_spec, _envelope, **_kwargs):
            return {"status": "ok", "response": judgment(
                "ask_user", question="確認が必要です",
            )}

        session = await start_goal_dialogue(
            "一般情報", member_specs=SPEC, timeout=5, caller=first,
        )
        session["observations"].append({
            "source": "web", "verified": True,
            "text": "PRIVATE-TOKEN is in this source",
        })
        original = session["observations"][-1]["text"]
        captured = []

        async def later(_spec, envelope, **_kwargs):
            captured.append(envelope)
            return {"status": "ok", "response": judgment(
                "wait", reason="次の処理を待ちます",
            )}

        await _run_goal_turn(
            session, timeout=5, caller=later,
            member_specs_override=SPEC,
            context_policy={"mode": "masked",
                            "redaction_tokens": ["PRIVATE-TOKEN"]},
        )
        self.assertEqual(len(captured), 1)
        self.assertNotIn("PRIVATE-TOKEN", str(captured[0]))
        self.assertNotIn("redaction_tokens", captured[0].get("context_policy", {}))
        self.assertEqual(session["observations"][-1]["text"], original)

        calls = []
        async def blocked(*_args, **_kwargs):
            calls.append(True)
            self.fail("a denied MAGI member must not receive context")

        session["status"] = "running"
        await _run_goal_turn(
            session, timeout=5, caller=blocked,
            member_specs_override=[{**SPEC[0], "enabled": False}],
        )
        self.assertEqual(calls, [])
        self.assertEqual(session["next_step"], "goal_model_not_permitted")

    async def test_shared_dialogue_selects_llm_judgment_without_new_task_loop(self):
        result_id = str(uuid4())
        observed_stages = []

        async def responder(_spec, envelope, **_kwargs):
            observed_stages.append(envelope["stage"])
            if envelope["turn"] == 1:
                return {"status": "ok", "response": judgment(
                    "request_read", request={
                        "source": "web", "query": "Python dict official",
                        "reason": "Official source requested",
                    },
                )}
            self.assertEqual(envelope["observations"][-1]["result_id"], result_id)
            return {"status": "ok", "response": judgment(
                "respond", answer="Python公式資料により確認できた範囲",
                fulfillment="met", basis="result_refs", refs=[result_id],
            )}

        session = await start_dialogue_async(
            "公式のPython資料を調べて", member_specs=SPEC, timeout=5,
            decision_strategy="llm", goal_caller=responder,
        )
        self.assertEqual(session["decision_strategy"], "llm")
        self.assertEqual(session["status"], "waiting_information")
        self.assertEqual(observed_stages, ["goal"])
        updated = await continue_with_verified_observations_async(
            session, [{
                "source": "web", "verified": True,
                "result_id": result_id, "action_id": str(uuid4()),
                "verification_level": "provider_cited",
                "text": "Python provider citation",
                "responds_to": [session["pending_requests"][0]["request_id"]],
            }], timeout=5, goal_caller=responder,
        )
        self.assertEqual(updated["task_id"], session["task_id"])
        self.assertEqual(updated["status"], "candidate_ready")
        self.assertEqual(goal_task_projection(updated)["task_status"], "completed")
        self.assertEqual(observed_stages, ["goal", "goal"])

    async def test_shared_task_loop_persists_llm_decision_and_recorded_result(self):
        recorded_result_id = str(uuid4())
        writes = []

        def record(_task, _execution, _request):
            writes.append("record")
            return str(uuid4()), recorded_result_id

        async def responder(_spec, envelope, **_kwargs):
            if envelope["turn"] == 1:
                return {"status": "ok", "response": judgment(
                    "request_read", request={
                        "source": "web", "query": "Official Python dict",
                        "reason": "Required source",
                    },
                )}
            self.assertEqual(envelope["observations"][0]["result_id"], recorded_result_id)
            return {"status": "ok", "response": judgment(
                "respond", fulfillment="met",
                answer="Python official reference, provider cited",
                basis="result_refs", refs=[recorded_result_id],
            )}

        def persist(_id, session, _cap):
            writes.append("persist")
            self.assertEqual(goal_task_projection(session)["task_status"], "completed")

        result = await run_observation_loop(
            "Python公式サイトを調べて",
            member_specs=SPEC, timeout=5,
            create_task_record=lambda *_: writes.append("create"),
            execute_source_request=lambda *_: {
                "status": "ok", "source": "web", "answer": "Provider citation",
                "verified_by": "provider_reported_web_citations",
                "capability": "web_research", "total": 1,
                "result": {"result_kind": "web_research"},
            },
            record_source_read_record=record,
            persist_session_record=persist,
            fail_task_record=lambda *_: self.fail("unexpected Task failure"),
            dialogue_starter=partial(
                start_dialogue_async, decision_strategy="llm",
                goal_caller=responder,
            ),
            observation_continuation=partial(
                continue_with_verified_observations_async,
                goal_caller=responder,
            ),
            resource_catalog={"web": {"available": True}},
        )
        self.assertEqual(result["status"], "candidate_ready")
        self.assertEqual(writes, ["create", "record", "persist"])

    async def test_shared_dialogue_resumes_llm_user_answer(self):
        async def responder(_spec, envelope, **_kwargs):
            if envelope["turn"] == 1:
                return {"status": "ok", "response": judgment(
                    "ask_user", question="説明するPythonの版を教えてください",
                )}
            self.assertEqual(envelope["user_replies"], ["3.12"])
            return {"status": "ok", "response": judgment(
                "respond", answer="Python 3.12について説明します",
                fulfillment="met", basis="not_required",
            )}

        session = await start_dialogue_async(
            "Pythonの説明をして", member_specs=SPEC, timeout=5,
            decision_strategy="llm", goal_caller=responder,
        )
        self.assertEqual(session["status"], "waiting_user")
        updated = await continue_with_user_clarification_async(
            session, "3.12", timeout=5, goal_caller=responder,
        )
        self.assertEqual(updated["task_id"], session["task_id"])
        self.assertEqual(updated["status"], "candidate_ready")
        self.assertEqual(goal_task_projection(updated)["task_status"], "completed")

    async def test_old_goal_session_resumes_through_new_common_user_path(self):
        async def responder(_spec, envelope, **_kwargs):
            if envelope["turn"] == 1:
                return {"status": "ok", "response": judgment(
                    "ask_user", question="どのPython版？",
                )}
            self.assertEqual(envelope["user_replies"], ["3.12"])
            return {"status": "ok", "response": judgment(
                "respond", answer="Python 3.12 です",
                fulfillment="met", basis="not_required",
            )}

        old_session = await start_goal_dialogue(
            "Python版を説明して", member_specs=SPEC, timeout=5,
            caller=responder,
        )
        self.assertNotIn("decision_strategy", old_session)
        result = await continue_with_user_clarification_async(
            old_session, "3.12", timeout=5, goal_caller=responder,
        )
        self.assertEqual(result["task_id"], old_session["task_id"])
        self.assertEqual(result["status"], "candidate_ready")
        self.assertEqual(goal_task_projection(result)["task_status"], "completed")

    async def test_shared_dialogue_preserves_failed_web_read_as_unverified(self):
        async def responder(_spec, envelope, **_kwargs):
            if envelope["turn"] == 1:
                return {"status": "ok", "response": judgment(
                    "request_read", request={"source": "web", "query": "Official docs",
                                              "reason": "Read requested"},
                )}
            self.assertFalse(envelope["observations"][-1]["verified"])
            return {"status": "ok", "response": judgment(
                "wait", reason="Web read failed; awaiting next attempt",
            )}

        session = await start_dialogue_async(
            "公式資料を調査して", member_specs=SPEC, timeout=5,
            decision_strategy="llm", goal_caller=responder,
        )
        failed = await continue_with_verified_observations_async(
            session, [{
                "source": "web", "verified": False,
                "result_id": str(uuid4()), "action_id": str(uuid4()),
                "text": "HTTPError",
            }], timeout=5, goal_caller=responder,
        )
        self.assertEqual(failed["status"], "waiting_information")
        self.assertNotEqual(goal_task_projection(failed)["task_status"], "completed")

    async def test_explicitly_unneeded_sources_allow_real_goal_answer(self):
        """実機事象: 『外部検索と個人PKBは不要』をSource必須と誤認しない。"""
        from ritsuko.magi.goal_loop import (
            goal_requires_source, goal_requires_web,
        )

        request = (
            "Pythonの辞書（dict）とは何か、簡単なコード例を1つ添えて日本語で"
            "説明してください。外部検索や個人PKBの参照は不要です。"
        )
        self.assertFalse(goal_requires_source(request))
        self.assertFalse(goal_requires_web(request))
        self.assertTrue(goal_requires_source("Python公式資料でdict.getを調べて"))
        self.assertTrue(goal_requires_web("公式サイトで調べて"))
        self.assertFalse(goal_requires_source("Web検索は不要です。辞書の説明だけをしてください。"))

        async def respond(_spec, _envelope, **_kwargs):
            return {"status": "ok", "response": judgment(
                "respond", goal="Pythonの辞書をコード例付きで説明する",
                answer="辞書はキーと値の対応です。person = {'name': '田中'}",
                fulfillment="met", basis="not_required",
            )}

        # Both legacy compatibility entry and the one canonical daily Task
        # session must accept the same Source-free response.
        for start in ("compatibility", "shared"):
            with self.subTest(start=start):
                if start == "compatibility":
                    session = await start_goal_dialogue(
                        request, member_specs=SPEC, timeout=5, caller=respond,
                    )
                else:
                    session = await start_dialogue_async(
                        request, member_specs=SPEC, timeout=5,
                        decision_strategy="llm", goal_caller=respond,
                    )
                self.assertFalse(session["requires_source"])
                self.assertFalse(session["requires_web"])
                self.assertEqual(session["status"], "candidate_ready")
                self.assertEqual(goal_task_projection(session)["task_status"], "completed")
                self.assertEqual(
                    goal_task_projection(session)["reason"], "goal_evaluated_source_free",
                )
                self.assertEqual(session["observations"], [])

    async def test_source_free_can_complete_without_fake_read(self):
        async def responder(_spec, envelope, **_kw):
            self.assertEqual(envelope["stage"], "goal")
            return {"status": "ok", "response": judgment(
                "respond", answer="辞書はキーと値の組です。",
                fulfillment="met", basis="not_required")}
        s = await start_goal_dialogue(
            "Pythonの辞書って何？", member_specs=SPEC,
            timeout=5, caller=responder, task_id=str(uuid4()),
        )
        self.assertEqual(s["status"], "candidate_ready")
        p = goal_task_projection(s)
        self.assertEqual(p["task_status"], "completed")
        self.assertEqual(p["reason"], "goal_evaluated_source_free")
        self.assertFalse(s["tool_read_executed"])

    async def test_member_progress_reports_actual_running_on_each_goal_turn(self):
        """UI must never show a Goal member queued while its LLM call runs."""
        events = []
        result_id = str(uuid4())

        async def responder(_spec, envelope, **_kw):
            self.assertEqual(events[-1]["state"], "running")
            self.assertEqual(events[-1]["turn"], envelope["turn"])
            if envelope["turn"] == 1:
                return {"status": "ok", "elapsed_seconds": 1.5,
                        "response": judgment("request_read", request={
                            "source": "web", "query": "Python dict.get docs",
                            "reason": "Web result needed"})}
            return {"status": "ok", "elapsed_seconds": 2.3,
                    "response": judgment(
                        "respond", answer="Cited provider information, unverified body",
                        fulfillment="met", basis="result_refs", refs=[result_id])}

        session = await start_goal_dialogue(
            "Python公式資料のdict.getを検索して", member_specs=SPEC,
            timeout=5, caller=responder, on_member_progress=events.append,
        )
        self.assertEqual([e["state"] for e in events],
                         ["queued", "running", "completed"])
        self.assertEqual([e["turn"] for e in events], [1, 1, 1])

        session = await continue_goal_observations(
            session, [{
                "source": "web", "result_id": result_id,
                "action_id": str(uuid4()), "verified": True,
                "verification_level": "provider_cited", "text": "Provider citation",
            }],
            timeout=5, caller=responder, on_member_progress=events.append,
        )
        self.assertEqual([e["state"] for e in events[3:]],
                         ["queued", "running", "completed"])
        self.assertEqual([e["turn"] for e in events[3:]], [2, 2, 2])
        self.assertEqual({e["task_id"] for e in events}, {session["task_id"]})
        self.assertEqual(events[-1]["elapsed_seconds"], 2.3)
        self.assertEqual(session["status"], "candidate_ready")

    async def test_goal_invalid_member_reports_invalid_not_queued(self):
        events = []
        async def invalid_reply(_spec, _envelope, **_kw):
            return {"status": "ok", "response": judgment(
                "respond", answer="Missing evidence", fulfillment="unknown",
                basis="insufficient")}
        session = await start_goal_dialogue(
            "Pythonの辞書とは？", member_specs=SPEC, timeout=5,
            caller=invalid_reply, on_member_progress=events.append,
        )
        self.assertEqual(session["next_step"], "goal_member_invalid")
        self.assertEqual([e["state"] for e in events],
                         ["queued", "running", "invalid"])
        self.assertEqual(events[-1]["error_kind"], "respond:answer_or_goal_missing")

    async def test_explicit_official_source_cannot_be_dismissed_as_unneeded(self):
        async def responder(*_args, **_kwargs):
            return {"status": "ok", "response": judgment(
                "respond", answer="公式サイトで確認しました",
                fulfillment="met", basis="not_required")}
        s = await start_goal_dialogue(
            "Python公式資料でdict.getを調べて", member_specs=SPEC,
            timeout=5, caller=responder,
        )
        self.assertEqual(s["status"], "stopped")
        self.assertEqual(s["next_step"], "required_source_not_observed")
        self.assertNotEqual(goal_task_projection(s)["task_status"], "completed")

    async def test_shared_ritsuko_loop_three_llm_goal_seats_source_free(self):
        """MAGIの席数が3でも共通Task/Observationに1回だけ保存する。"""
        calls = []
        specs = [{**SPEC[0], "name": name}
                 for name in ("MELCHIOR", "CASPER", "BALTHASAR")]
        async def responder(spec, envelope, **_kwargs):
            calls.append(("llm", spec["name"], envelope["turn"]))
            return {"status": "ok", "response": judgment(
                "respond", goal="辞書の説明をする", fulfillment="met",
                basis="not_required", answer="辞書はキーと値の組です。",
            )}
        def create(*_args):
            calls.append(("create",))
        def persist(_id, session, _cap):
            calls.append(("persist",))
            self.assertEqual(goal_task_projection(session)["task_status"], "completed")
        session = await run_observation_loop(
            "Pythonの辞書を簡潔に説明して",
            member_specs=specs, timeout=5,
            create_task_record=create,
            execute_source_request=lambda *_args: self.fail("Source-free should not read"),
            record_source_read_record=lambda *_args: self.fail("No Result required"),
            persist_session_record=persist,
            fail_task_record=lambda *_args: self.fail("No fail"),
            dialogue_starter=partial(
                start_dialogue_async, decision_strategy="llm",
                goal_caller=responder,
            ),
            observation_continuation=partial(
                continue_with_verified_observations_async,
                goal_caller=responder,
            ),
        )
        self.assertEqual(session["status"], "candidate_ready")
        self.assertEqual(goal_task_projection(session)["task_status"], "completed")
        self.assertEqual(len(session["turns"][0]["member_results"]), 3)
        self.assertEqual(calls.count(("create",)), 1)
        self.assertEqual(calls.count(("persist",)), 1)
        self.assertEqual(len([x for x in calls if x[0] == "llm"]), 3)

    async def test_web_result_id_roundtrip_and_projection(self):
        calls = []
        result_id = str(uuid4())
        def create(*args):
            calls.append("create")
        def record(_task_id, _execution, _request):
            calls.append("record")
            return str(uuid4()), result_id
        def execute(_request, pending, _session):
            self.assertEqual(pending["source"], "web")
            calls.append("read")
            return {
                "status": "ok", "source": "web",
                "capability": "web_research", "tool": "web",
                "operation": "research", "total": 1,
                "verified_by": "provider_reported_web_citations",
                "answer": "Providerが報告した公開資料",
                "source_metadata": {
                    "provider": "openai", "web_sources": [{
                        "title": "Python docs", "url": "https://docs.python.org/3/library/stdtypes.html",
                        "fetch_status": "provider_cited",
                    }],
                },
                "result": {"result_kind": "web_research"},
            }
        async def responder(_spec, envelope, **_kw):
            calls.append("llm")
            if envelope["turn"] == 1:
                return {"status": "ok", "response": judgment(
                    "request_read", request={"source": "web",
                                             "query": "Python dict get official",
                                             "reason": "Source要求"},
                )}
            obs = envelope["observations"]
            self.assertEqual(obs[0]["result_id"], result_id)
            self.assertEqual(obs[0]["verification_level"], "provider_cited")
            return {"status": "ok", "response": judgment(
                "respond", answer="Provider引用から確認できた範囲ではdefaultを返します。本文の独立確認は未実施。",
                fulfillment="met", basis="result_refs", refs=[result_id],
            )}
        def persist(_id, session, cap):
            calls.append("persist")
            self.assertEqual(goal_task_projection(session)["task_status"], "completed")
        s = await run_observation_loop(
            "Python公式資料でdict.getを調べて",
            member_specs=SPEC, timeout=5,
            create_task_record=create, execute_source_request=execute,
            record_source_read_record=record, persist_session_record=persist,
            fail_task_record=lambda *_: None,
            dialogue_starter=partial(start_goal_dialogue, caller=responder),
            observation_continuation=partial(continue_goal_observations, caller=responder),
            resource_catalog={"web": {"available": True}},
        )
        self.assertEqual(s["status"], "candidate_ready")
        self.assertEqual(calls, ["create", "llm", "read", "record", "llm", "persist"])

    async def test_forged_result_ref_does_not_complete(self):
        async def caller(_spec, envelope, **_kw):
            if envelope["turn"] == 1:
                return {"status": "ok", "response": judgment(
                    "request_read", request={"source": "web", "query": "Python docs",
                                             "reason": "read"})}
            return {"status": "ok", "response": judgment(
                "respond", answer="出典を確認した", fulfillment="met",
                basis="result_refs", refs=["different-task-result"])}
        s = await start_goal_dialogue(
            "Python公式資料を調べて", member_specs=SPEC, timeout=5, caller=caller,
        )
        s = await continue_goal_observations(
            s, [{"source": "web", "result_id": str(uuid4()),
                 "action_id": str(uuid4()), "verified": True,
                 "verification_level": "provider_cited", "text": "provider citation"}],
            timeout=5, caller=caller,
        )
        self.assertEqual(s["next_step"], "unrecorded_task_result_reference")
        self.assertEqual(goal_task_projection(s)["task_status"], "failed")

    async def test_partial_search_result_references_do_not_abort_new_read(self):
        """Regression for the actual 2026-10-14 second MAGI turn."""
        result_id = str(uuid4())
        async def caller(_spec, envelope, **_kw):
            if envelope["turn"] == 1:
                return {"status": "ok", "response": judgment(
                    "request_read", request={
                        "source": "web", "query": "Python dict.get official docs",
                        "reason": "Need official result",
                    })}
            self.assertEqual(
                envelope["resource_catalog"]["web"]["direct_page_content_fetch"], False
            )
            self.assertEqual(envelope["observations"][0]["result_id"], result_id)
            return {"status": "ok", "response": judgment(
                "request_read", fulfillment="partial",
                basis="insufficient", refs=[result_id], request={
                    "source": "web", "query": "More context for dict.get",
                    "reason": "Only provider citation, not direct page content",
                })}
        session = await start_goal_dialogue(
            "Python公式資料をWeb検索して調べて", member_specs=SPEC,
            timeout=5, caller=caller,
        )
        updated = await continue_goal_observations(
            session,
            [{"source": "web", "result_id": result_id,
              "action_id": str(uuid4()), "verified": True,
              "verification_level": "provider_cited",
              "text": "Provider reported Python official citation"}],
            timeout=5, caller=caller,
        )
        self.assertEqual(updated["turns"][1]["status"], "ok")
        self.assertEqual(updated["turns"][1]["errors"], [])
        self.assertEqual(updated["status"], "waiting_information")
        self.assertEqual(updated["pending_requests"][0]["source"], "web")
        self.assertEqual(goal_task_projection(updated)["task_status"], "waiting_external")

    async def test_forged_partial_result_reference_is_rejected(self):
        async def caller(_spec, envelope, **_kw):
            if envelope["turn"] == 1:
                return {"status": "ok", "response": judgment(
                    "request_read", request={
                        "source": "web", "query": "Python official doc",
                        "reason": "Need a citation",
                    })}
            return {"status": "ok", "response": judgment(
                "request_read", fulfillment="partial", basis="insufficient",
                refs=["another-task-result"], request={
                    "source": "web", "query": "Python further details",
                    "reason": "The citation is not enough",
                })}
        s = await start_goal_dialogue(
            "Python公式資料で確認して", member_specs=SPEC, timeout=5,
            caller=caller,
        )
        s = await continue_goal_observations(
            s, [{"source": "web", "result_id": str(uuid4()),
                 "action_id": str(uuid4()), "verified": True,
                 "verification_level": "provider_cited", "text": "citation"}],
            timeout=5, caller=caller,
        )
        self.assertEqual(s["next_step"], "unrecorded_task_result_reference")
        self.assertEqual(goal_task_projection(s)["task_status"], "failed")

    async def test_failed_read_not_valid_for_answer(self):
        async def caller(_spec, envelope, **_kw):
            if envelope["turn"] == 1:
                return {"status": "ok", "response": judgment(
                    "request_read", request={"source": "web", "query": "Python docs",
                                             "reason": "Source必要"})}
            return {"status": "ok", "response": judgment(
                "wait", reason="Tool失敗のため再実行待ち")}
        s = await start_goal_dialogue(
            "Python公式情報を調べて", member_specs=SPEC, timeout=5, caller=caller,
        )
        s = await continue_goal_observations(
            s, [{"source": "web", "verified": False, "text": "HTTPError",
                 "result_id": str(uuid4()), "action_id": str(uuid4()),
                 "verification_level": "read_failed"}],
            timeout=5, caller=caller,
        )
        self.assertEqual(s["status"], "waiting_information")
        self.assertEqual(goal_task_projection(s)["task_status"], "waiting_external")

    async def test_user_answer_rejoins_same_goal_task(self):
        from ritsuko.magi.goal_loop import continue_goal_user
        replies = []
        async def responder(_spec, envelope, **_kw):
            replies.extend(envelope.get("user_replies") or [])
            if envelope["turn"] == 1:
                return {"status": "ok", "response": judgment(
                    "ask_user", question="Pythonのどの版を説明しますか？")}
            return {"status": "ok", "response": judgment(
                "respond", answer="3.12として説明します。",
                fulfillment="met", basis="not_required")}
        s = await start_goal_dialogue(
            "Pythonについて説明して", member_specs=SPEC, timeout=5,
            caller=responder,
        )
        self.assertEqual(goal_task_projection(s)["phase"], "awaiting_clarification")
        t = await continue_goal_user(s, "3.12", timeout=5, caller=responder)
        self.assertEqual(t["task_id"], s["task_id"])
        self.assertEqual(t["status"], "candidate_ready")
        self.assertEqual(goal_task_projection(t)["task_status"], "completed")
        self.assertEqual(replies, ["3.12"])

    async def test_two_and_three_llm_seats_use_one_goal_task(self):
        """Two/three independent MAGI opinions share one Goal and Task control."""
        events = []
        async def decide(spec, envelope, **_kwargs):
            self.assertEqual(envelope["stage"], "goal")
            return {"status": "ok", "response": judgment(
                "respond", goal="Pythonの辞書の説明",
                fulfillment="met", basis="not_required",
                answer="辞書はキーと値の組です。", reason=spec["name"],
            )}
        for names in [["CASPER", "MELCHIOR"], ["CASPER", "MELCHIOR", "BALTHASAR"]]:
            events.clear()
            specs = [{**SPEC[0], "name": name} for name in names]
            session = await start_goal_dialogue(
                "Pythonの辞書を説明して", member_specs=specs, timeout=5,
                caller=decide, on_member_progress=events.append,
            )
            self.assertEqual(session["status"], "candidate_ready")
            self.assertEqual(goal_task_projection(session)["task_status"], "completed")
            self.assertEqual(len(session["turns"]), 1)
            turn = session["turns"][0]
            self.assertEqual(len(turn["member_results"]), len(names))
            self.assertEqual(set(turn["diagnostic"]["enabled_members"]), set(names))
            self.assertEqual(
                {event["member"] for event in events if event["state"] == "completed"},
                set(names),
            )

    async def test_llm_member_disagreement_requests_clarification(self):
        """Tied incompatible Goal decisions must not silently complete a Task."""
        specs = [{**SPEC[0], "name": name}
                 for name in ("CASPER", "MELCHIOR")]
        async def diverge(spec, _envelope, **_kwargs):
            if spec["name"] == "CASPER":
                reply = judgment("respond", answer="辞書です",
                                 fulfillment="met", basis="not_required")
            else:
                reply = judgment("ask_user", fulfillment="unknown",
                                 question="何を確認しますか？")
            return {"status": "ok", "response": reply}
        session = await start_goal_dialogue(
            "Pythonの辞書について", member_specs=specs, timeout=5,
            caller=diverge,
        )
        self.assertEqual(session["status"], "waiting_user")
        self.assertEqual(session["next_step"], "goal_disagreement")
        self.assertEqual(goal_task_projection(session)["task_status"], "waiting_external")
        self.assertEqual(len(session["turns"][0]["member_results"]), 2)

    async def test_task_application_selects_judgment_callbacks_without_mode_branch(self):
        from unittest.mock import Mock
        names = list(TaskRuntimePorts.__dataclass_fields__)
        ports = TaskRuntimePorts(**{k: Mock() for k in names})
        forwarded = []

        async def runner(*args, **kwargs):
            forwarded.append(kwargs)
            return {"status": "ok"}

        app = StateDrivenTaskApplication(
            ports, start_runner=runner,
            resume_runner=runner, review_runner=runner,
        )
        await app.start("standard", member_specs=SPEC, timeout=5)
        self.assertNotIn("dialogue_starter", forwarded[-1])
        snapshot = {"profile_id": "test", "revision": 1, "grants": []}
        progress = []
        goal_starter = partial(
            start_goal_dialogue, policy_snapshot=snapshot,
            on_member_progress=progress.append,
        )
        goal_observe = partial(
            continue_goal_observations, on_member_progress=progress.append,
        )
        await app.start(
            "goal", member_specs=SPEC, timeout=5,
            dialogue_starter=goal_starter,
            observation_continuation=goal_observe,
        )
        self.assertIs(forwarded[-1]["dialogue_starter"], goal_starter)
        self.assertIs(forwarded[-1]["observation_continuation"], goal_observe)
        self.assertEqual(forwarded[-1]["dialogue_starter"].keywords["policy_snapshot"], snapshot)
        self.assertNotIn("control_mode", forwarded[-1])
        self.assertNotIn("policy_snapshot", forwarded[-1])


if __name__ == "__main__":
    unittest.main()
