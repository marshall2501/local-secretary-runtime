# Provenance-Origin: original
# Provenance-AI: AI-generated
"""両判断方式が同じTask・Source・Action/Result契約を使う隔離縦断回帰。"""
from __future__ import annotations

import unittest
from uuid import uuid4

from ritsuko.core.observation_loop import run_observation_loop
from ritsuko.core.task_projection import task_projection


class SharedJudgmentTaskPipelineTests(unittest.IsolatedAsyncioTestCase):
    """判断結果だけを差し替え、RITSUKOの実行と永続化は一回にする。"""

    async def test_both_strategies_record_the_same_evidence_and_complete(self):
        """MAGIが決めたPKB読取を一つのRITSUKO Source Portで実行する。"""
        for strategy in ("standard", "llm"):
            with self.subTest(strategy=strategy):
                calls = []
                task_id = uuid4()

                async def start(request, **kwargs):
                    self.assertEqual(kwargs["task_id"], str(task_id))
                    return {
                        "task_id": str(task_id), "user_raw": request,
                        "decision_strategy": strategy,
                        "control_mode": "goal_loop_v1" if strategy == "llm" else None,
                        "status": "waiting_information",
                        "next_step": "read", "turns": [],
                        "pending_requests": [{
                            "request_id": "REQ-1", "source": "pkb",
                            "what": "登録されている構成", "reason": "根拠の確認",
                        }],
                        "observations": [],
                    }

                def execute(_text, pending, _session):
                    calls.append(("read", pending["source"]))
                    return {
                        "status": "ok", "source": "pkb",
                        "verified_by": "deterministic_pkb_query",
                        "capability": "pkb_search",
                        "total": 1, "answer": "保存済みの構成",
                        "result": {
                            "result_kind": "search",
                            "items": [{"text": "保存済みの構成"}],
                        },
                    }

                def record(record_task_id, execution, _pending):
                    self.assertEqual(record_task_id, task_id)
                    self.assertEqual(execution["source"], "pkb")
                    calls.append(("record", "pkb"))
                    return ("action-1", "result-1")

                async def continue_with_result(session, observations, **_kwargs):
                    self.assertEqual(len(observations), 1)
                    observation = observations[0]
                    self.assertEqual(observation["action_id"], "action-1")
                    self.assertEqual(observation["result_id"], "result-1")
                    self.assertTrue(observation["verified"])
                    result = {
                        **session,
                        "status": "candidate_ready",
                        "next_step": "review_answer_candidate",
                        "pending_requests": [],
                        "observations": list(observations),
                        "tool_read_executed": True,
                        "detail": {"state": "READY", "answer_candidate": "結果を確認しました"},
                    }
                    if strategy == "llm":
                        result["goal_judgment"] = {
                            "next": "respond", "fulfillment": "met",
                            "answer": "結果を確認しました",
                            "evidence": {
                                "basis": "result_refs", "result_ids": ["result-1"],
                            },
                        }
                    return result

                def persist(record_task_id, session, _capability):
                    self.assertEqual(record_task_id, task_id)
                    projected = task_projection(session)
                    self.assertEqual(projected["task_status"], "completed")
                    self.assertEqual(projected["message"], "結果を確認しました")
                    calls.append(("persist", "completed"))

                result = await run_observation_loop(
                    "構成を調べて", member_specs=[{"enabled": True, "name": "MELCHIOR"}],
                    timeout=5, task_id=task_id,
                    create_task_record=lambda *_args: calls.append(("create", "task")),
                    execute_source_request=execute,
                    record_source_read_record=record,
                    persist_session_record=persist,
                    fail_task_record=lambda *_args: self.fail("unwanted failure"),
                    dialogue_starter=start,
                    observation_continuation=continue_with_result,
                    resource_catalog={"pkb": {"available": True}},
                )
                self.assertEqual(result["status"], "candidate_ready")
                self.assertEqual(calls, [
                    ("create", "task"), ("read", "pkb"),
                    ("record", "pkb"), ("persist", "completed"),
                ])


if __name__ == "__main__":
    unittest.main()
