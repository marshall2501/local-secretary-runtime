from __future__ import annotations

import unittest

from pkb_proto.magi_observation_loop import run_pkb_observation_loop


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

    async def test_mixed_information_sources_wait_without_auto_read(self):
        calls = []
        specs = [{"name": "MELCHIOR", "provider": "ollama", "enabled": True}]

        def create_task(*args):
            calls.append("create")

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

        def execute(*args):
            calls.append("execute")
            raise AssertionError("mixed request must not auto execute")

        def record(*args):
            calls.append("record")

        def persist(task_id, session, capability):
            calls.append(("persist", capability, session["status"]))

        def fail(*args):
            calls.append("fail")

        session = await run_pkb_observation_loop(
            "比較して",
            member_specs=specs,
            timeout=10,
            create_task_record=create_task,
            execute_pkb_request=execute,
            record_pkb_read_record=record,
            persist_session_record=persist,
            fail_task_record=fail,
            dialogue_starter=starter,
        )
        self.assertEqual(session["status"], "waiting_information")
        self.assertNotIn("execute", calls)
        self.assertIn(("persist", None, "waiting_information"), calls)


if __name__ == "__main__":
    unittest.main()
