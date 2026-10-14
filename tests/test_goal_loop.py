"""Slice A synthetic, isolated acceptance tests: no actual LLM/DB/network."""
from __future__ import annotations

import unittest
from functools import partial
from uuid import uuid4

from ritsuko.magi.goal_contract import validate_goal_turn
from ritsuko.magi.goal_loop import (
    start_goal_dialogue, continue_goal_observations, goal_task_projection,
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

    async def test_requires_explicit_single_llm(self):
        with self.assertRaisesRegex(ValueError, "one_enabled_member"):
            await start_goal_dialogue("Pythonの辞書は？",
                member_specs=SPEC + [{**SPEC[0], "name": "MELCHIOR"}], timeout=5)

    async def test_legacy_mode_unchanged_and_new_mode_is_opt_in(self):
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
        await app.start("legacy", member_specs=SPEC, timeout=5)
        self.assertNotIn("dialogue_starter", forwarded[-1])
        with self.assertRaisesRegex(ValueError, "goal_mode_policy_snapshot_required"):
            await app.start("new", member_specs=SPEC, timeout=5,
                            control_mode="goal_loop_v1")
        await app.start("new", member_specs=SPEC, timeout=5,
                        control_mode="goal_loop_v1",
                        policy_snapshot={"profile_id": "test", "revision": 1, "grants": []})
        self.assertIs(forwarded[-1]["dialogue_starter"].func, start_goal_dialogue)
        self.assertEqual(forwarded[-1]["dialogue_starter"].keywords["policy_snapshot"]["profile_id"], "test")
        self.assertIs(forwarded[-1]["observation_continuation"],
                      continue_goal_observations)
        progress = []
        await app.start("new", member_specs=SPEC, timeout=5,
                        control_mode="goal_loop_v1",
                        policy_snapshot={"profile_id": "test", "revision": 1, "grants": []},
                        on_member_progress=progress.append)
        self.assertIs(forwarded[-1]["dialogue_starter"].func, start_goal_dialogue)
        self.assertIsNotNone(
            forwarded[-1]["dialogue_starter"].keywords["on_member_progress"])
        self.assertIs(forwarded[-1]["observation_continuation"].func,
                      continue_goal_observations)
        self.assertIsNotNone(
            forwarded[-1]["observation_continuation"].keywords["on_member_progress"])


if __name__ == "__main__":
    unittest.main()
