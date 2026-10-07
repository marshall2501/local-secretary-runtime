from __future__ import annotations

import unittest

from ritsuko.core.observation_loop import (
    review_proposal,
    run_observation_loop,
    run_pkb_observation_loop,
)


class MagiObservationLoopTests(unittest.IsolatedAsyncioTestCase):
    async def test_pkb_request_runs_read_records_observation_and_persists(self):
        calls = []
        specs = [{"name": "MELCHIOR", "provider": "ollama", "enabled": True}]

        def create_task(task_id, request, member_specs):
            calls.append(("create", str(task_id), request, member_specs))

        async def starter(request, **kwargs):
            calls.append(("dialogue", kwargs["task_id"]))
            return {
                "task_id": kwargs["task_id"],
                "status": "waiting_information",
                "pending_requests": [{
                    "request_id": "REQ-1",
                    "source": "pkb",
                    "what": "メインPCのGPUモデル",
                    "reason": "回答に必要",
                }],
                "member_specs": specs,
                "observations": [],
                "turns": [{}, {}],
            }

        def execute(request, pending):
            calls.append(("execute", pending["request_ids"]))
            return {
                "capability": "pkb_search",
                "tool": "pkb",
                "operation": "entity_detail",
                "total": 1,
                "answer": "PKBの記録では Radeon RX 9070 XT。",
                "verified_by": "deterministic_pkb_query",
                "result": {
                    "result_kind": "entity_detail",
                    "current": [{"predicate": "model", "value": "Radeon RX 9070 XT"}],
                },
            }

        def record(task_id, execution, pending):
            calls.append(("record", str(task_id), pending["request_ids"]))
            return ("action-1", "result-1")

        async def continue_observation(session, observation, **kwargs):
            calls.append(("continue", observation["verified"], observation["source"]))
            updated = dict(session)
            updated.update({
                "status": "candidate_ready",
                "next_step": "review_answer_candidate",
                "tool_read_executed": True,
                "observations": [observation],
                "detail": {
                    "answer_candidate": "メインPCのGPUは Radeon RX 9070 XT です。"
                },
                "turns": [{}, {}, {}],
            })
            return updated

        def persist(task_id, session, capability):
            calls.append(("persist", str(task_id), capability, session["status"]))
            return {"task_status": "completed"}

        def fail(task_id, error):
            calls.append(("fail", str(task_id), error))

        session = await run_pkb_observation_loop(
            "メインPCのGPUの種類は？",
            member_specs=specs,
            timeout=10,
            create_task_record=create_task,
            execute_pkb_request=execute,
            record_pkb_read_record=record,
            persist_session_record=persist,
            fail_task_record=fail,
            dialogue_starter=starter,
            observation_continuation=continue_observation,
        )
        self.assertEqual(session["status"], "candidate_ready")
        self.assertEqual(
            [item[0] for item in calls],
            ["create", "dialogue", "execute", "record", "continue", "persist"],
        )
        self.assertEqual(calls[-1][2], "pkb_search")
        self.assertFalse(any(item[0] == "fail" for item in calls))

    async def test_mixed_pkb_and_web_sources_execute_in_one_bounded_batch(self):
        calls = []
        specs = [{"name": "MELCHIOR", "provider": "ollama", "enabled": True}]

        def create_task(*args):
            calls.append(("create",))

        async def starter(request, **kwargs):
            return {
                "task_id": kwargs["task_id"],
                "status": "waiting_information",
                "pending_requests": [
                    {"request_id": "REQ-1", "source": "pkb", "what": "local"},
                    {"request_id": "REQ-2", "source": "web", "what": "fresh"},
                ],
                "member_specs": specs,
                "observations": [],
                "turns": [{}, {}],
            }

        def execute(_request, pending, _session):
            source = pending["source"]
            calls.append(("execute", source))
            if source == "pkb":
                return {
                    "status": "ok",
                    "source": "pkb",
                    "capability": "pkb_search",
                    "confidentiality": "private",
                    "tool": "pkb",
                    "operation": "search",
                    "total": 1,
                    "answer": "local fact",
                    "verified_by": "deterministic_pkb_query",
                    "result": {"result_kind": "search", "items": [{"v": "local"}]},
                }
            return {
                "status": "ok",
                "source": "web",
                "capability": "web_research",
                "confidentiality": "public",
                "tool": "web",
                "operation": "research",
                "total": 1,
                "answer": "fresh public fact",
                "verified_by": "bounded_web_retrieval",
                "result": {"result_kind": "web_research", "hits": [{"title": "official"}]},
            }

        def record(_task_id, execution, pending):
            calls.append(("record", pending["source"], execution["status"]))
            return ("a", "r")

        async def continue_batch(session, observations, **kwargs):
            calls.append(("continue", sorted(x["source"] for x in observations)))
            updated = dict(session)
            updated.update({
                "status": "candidate_ready",
                "next_step": "review_answer_candidate",
                "tool_read_executed": True,
                "pending_requests": [],
                "observations": list(observations),
                "detail": {"answer_candidate": "comparison"},
            })
            return updated

        def persist(_task_id, session, capability):
            calls.append(("persist", capability, session["status"]))

        def fail(*args):
            calls.append(("fail",))

        session = await run_observation_loop(
            "比較して",
            member_specs=specs,
            timeout=10,
            create_task_record=create_task,
            execute_source_request=execute,
            record_source_read_record=record,
            persist_session_record=persist,
            fail_task_record=fail,
            dialogue_starter=starter,
            observation_continuation=continue_batch,
            resource_catalog={
                "pkb": {"available": True},
                "web": {"available": True},
                "finance": {"available": True},
            },
        )
        self.assertEqual(session["status"], "candidate_ready")
        self.assertEqual(
            {item[1] for item in calls if item[0] == "execute"},
            {"pkb", "web"},
        )
        self.assertIn(("continue", ["pkb", "web"]), calls)
        self.assertIn(("persist", "observation_read", "candidate_ready"), calls)
        self.assertNotIn(("fail",), calls)

    async def test_unavailable_files_are_not_executed_or_faked(self):
        specs = [{"name": "MELCHIOR", "provider": "ollama", "enabled": True}]
        calls = []

        async def starter(request, **kwargs):
            return {
                "task_id": kwargs["task_id"],
                "status": "waiting_information",
                "pending_requests": [{
                    "request_id": "REQ-F",
                    "source": "files",
                    "what": "document",
                }],
                "member_specs": specs,
                "observations": [],
                "turns": [{}, {}],
            }

        def persist(_task_id, session, capability):
            calls.append(("persist", capability, session["status"]))
            self.assertEqual(session["observations"], [])
            self.assertEqual(
                session["unavailable_source_requests"][0]["source"],
                "files",
            )

        session = await run_observation_loop(
            "ファイルを見て",
            member_specs=specs,
            timeout=10,
            create_task_record=lambda *args: None,
            execute_source_request=lambda *args: (_ for _ in ()).throw(
                AssertionError("files must not execute")
            ),
            record_source_read_record=lambda *args: None,
            persist_session_record=persist,
            fail_task_record=lambda *args: None,
            dialogue_starter=starter,
            resource_catalog={
                "pkb": {"available": True},
                "web": {"available": True},
                "finance": {"available": True},
                "files": {"available": False},
            },
        )
        self.assertEqual(session["status"], "waiting_information")
        self.assertEqual(calls, [("persist", None, "waiting_information")])


    async def test_review_proposal_claims_re_evaluates_and_finalizes(self):
        calls = []
        task_id = __import__("uuid").UUID(
            "11111111-1111-4111-8111-111111111111"
        )
        session = {
            "task_id": str(task_id),
            "status": "proposal_ready",
            "turns": [{}, {}, {}, {}, {}],
        }
        observation = {
            "source": "memory_intake",
            "verified": True,
            "text": "Memory Intake committed / pending",
        }

        def claim(claimed_id, decision, memory_result):
            calls.append(("claim", str(claimed_id), decision, memory_result))
            return (
                dict(session),
                "pkb_search",
                dict(observation),
                {"decision": decision, "status": "processing"},
            )

        async def continue_review(saved, observed, **kwargs):
            calls.append(("continue", saved["task_id"], observed["source"]))
            updated = dict(saved)
            updated.update(
                status="review_evaluated",
                next_step="ritsuko_finalize_review",
                post_review_evaluation={"state": "READY"},
                turns=[*saved["turns"], {
                    "question_purpose": "evaluate_review_result",
                    "status": "ok",
                }],
            )
            return updated

        def finalize(saved_id, updated, capability):
            calls.append((
                "finalize",
                str(saved_id),
                capability,
                updated["status"],
            ))

        def abort(*args):
            calls.append(("abort",))

        updated = await review_proposal(
            task_id,
            "remember",
            timeout=10,
            claim_proposal_review_record=claim,
            finalize_proposal_review_record=finalize,
            abort_proposal_review_record=abort,
            memory_result={"status": "committed", "input_id": "input-1"},
            review_continuation=continue_review,
        )

        self.assertEqual(updated["status"], "review_evaluated")
        self.assertEqual(
            [item[0] for item in calls],
            ["claim", "continue", "finalize"],
        )
        self.assertEqual(calls[0][2], "remember")
        self.assertEqual(calls[-1][2], "pkb_search")

    async def test_review_proposal_returns_task_to_retry_on_re_evaluation_failure(self):
        calls = []
        task_id = __import__("uuid").UUID(
            "11111111-1111-4111-8111-111111111111"
        )

        def claim(claimed_id, decision, memory_result):
            calls.append(("claim", decision))
            return (
                {
                    "task_id": str(task_id),
                    "status": "proposal_ready",
                    "turns": [{}, {}, {}, {}, {}],
                },
                "pkb_search",
                {
                    "source": "proposal_review",
                    "verified": True,
                    "text": "answer only",
                },
                {"decision": decision, "status": "processing"},
            )

        async def fail_review(*args, **kwargs):
            return {
                "task_id": str(task_id),
                "status": "stopped",
                "next_step": "magi_unavailable",
            }

        def finalize(*args):
            calls.append(("finalize",))

        def abort(saved_id, error_type):
            calls.append(("abort", str(saved_id), error_type))

        with self.assertRaisesRegex(
            ValueError,
            "proposal_review_re_evaluation_failed",
        ):
            await review_proposal(
                task_id,
                "answer_only",
                timeout=10,
                claim_proposal_review_record=claim,
                finalize_proposal_review_record=finalize,
                abort_proposal_review_record=abort,
                review_continuation=fail_review,
            )

        self.assertEqual(
            [item[0] for item in calls],
            ["claim", "abort"],
        )

    async def test_resume_user_answer_claims_same_task_continues_and_persists(self):
        calls = []
        task_id = __import__("uuid").UUID("11111111-1111-4111-8111-111111111111")
        session = {
            "task_id": str(task_id),
            "status": "waiting_user",
            "next_step": "ask_user_after_exhausted_pkb",
            "pending_requests": [{"request_id": "REQ-1", "source": "user"}],
            "turns": [{}, {}, {}, {}],
        }

        def claim(claimed_id, reply_length, reply_fingerprint):
            calls.append((
                "claim",
                str(claimed_id),
                reply_length,
                reply_fingerprint,
            ))
            return (dict(session), "pkb_search")

        async def continue_user(saved, text, **kwargs):
            calls.append(("continue", saved["task_id"], text))
            updated = dict(saved)
            updated.update(
                status="candidate_ready",
                next_step="review_answer_candidate",
                detail={"answer_candidate": "Radeon RX 9070 XT"},
            )
            return updated

        def persist(saved_id, updated, capability):
            calls.append(("persist", str(saved_id), capability, updated["status"]))

        def abort(*args):
            calls.append(("abort",))

        def fail(*args):
            calls.append(("fail",))

        from ritsuko.core.observation_loop import resume_user_answer
        updated = await resume_user_answer(
            task_id,
            "Radeon RX 9070 XT",
            timeout=10,
            claim_user_resume_record=claim,
            persist_session_record=persist,
            abort_user_resume_record=abort,
            fail_task_record=fail,
            user_continuation=continue_user,
        )
        self.assertEqual(updated["status"], "candidate_ready")
        self.assertEqual(
            [item[0] for item in calls],
            ["claim", "continue", "persist"],
        )
        self.assertEqual(calls[0][1], str(task_id))
        self.assertEqual(len(calls[0][3]), 64)
        self.assertEqual(calls[-1][2], "pkb_search")
        self.assertNotIn("abort", [item[0] for item in calls])


    async def test_user_resume_stopped_model_result_aborts_instead_of_persisting_failure(self):
        calls = []
        task_id = __import__("uuid").UUID(
            "11111111-1111-4111-8111-111111111111"
        )

        def claim(claimed_id, reply_length, reply_fingerprint):
            calls.append(("claim",))
            return (
                {
                    "task_id": str(task_id),
                    "status": "waiting_user",
                    "pending_requests": [{"request_id": "REQ-1"}],
                    "turns": [{}, {}, {}, {}],
                },
                "pkb_search",
            )

        async def stopped_continue(*args, **kwargs):
            return {
                "task_id": str(task_id),
                "status": "stopped",
                "next_step": "magi_unavailable",
            }

        def persist(*args):
            calls.append(("persist",))

        def abort(saved_id, error_type):
            calls.append(("abort", error_type))

        with self.assertRaisesRegex(
            ValueError,
            "user_resume_re_evaluation_failed",
        ):
            from ritsuko.core.observation_loop import resume_user_answer
            await resume_user_answer(
                task_id,
                "Radeon RX 9070 XT",
                timeout=10,
                claim_user_resume_record=claim,
                persist_session_record=persist,
                abort_user_resume_record=abort,
                user_continuation=stopped_continue,
            )

        self.assertEqual([item[0] for item in calls], ["claim", "abort"])
        self.assertEqual(calls[-1][1], "ValueError")

    async def test_user_resume_failure_aborts_to_retryable_state(self):
        calls = []
        task_id = __import__("uuid").UUID(
            "11111111-1111-4111-8111-111111111111"
        )

        def claim(claimed_id, reply_length, reply_fingerprint):
            calls.append(("claim", reply_length, reply_fingerprint))
            return (
                {
                    "task_id": str(task_id),
                    "status": "waiting_user",
                    "pending_requests": [{"request_id": "REQ-1"}],
                    "turns": [{}, {}, {}, {}],
                },
                "pkb_search",
            )

        async def fail_continue(*args, **kwargs):
            raise RuntimeError("transport failed")

        def persist(*args):
            calls.append(("persist",))

        def abort(saved_id, error_type):
            calls.append(("abort", str(saved_id), error_type))

        with self.assertRaisesRegex(RuntimeError, "transport failed"):
            from ritsuko.core.observation_loop import resume_user_answer
            await resume_user_answer(
                task_id,
                "Radeon RX 9070 XT",
                timeout=10,
                claim_user_resume_record=claim,
                persist_session_record=persist,
                abort_user_resume_record=abort,
                user_continuation=fail_continue,
            )

        self.assertEqual([item[0] for item in calls], ["claim", "abort"])
        self.assertEqual(calls[-1][2], "RuntimeError")


if __name__ == "__main__":
    unittest.main()
