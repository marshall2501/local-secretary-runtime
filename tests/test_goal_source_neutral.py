# Provenance-Origin: original
# Provenance-AI: AI-generated
"""Goal判断を共通RITSUKOのPKB/Web/Finance Sourceで使う隔離契約テスト。

テスト中のTask・Source・LLM・Policyは全てfake。運用DB・外部送信なし。
"""
from __future__ import annotations

from functools import partial
import unittest
from uuid import uuid4

from ritsuko.core.task_execution import run_observation_loop
from ritsuko.magi.session_workflow import (
    continue_with_verified_observations_async,
    start_dialogue_async,
)
from ritsuko.magi.goal_contract import GOAL_SCHEMA, validate_goal_turn
from ritsuko.core.task_projection import task_projection
from ritsuko.core.judgment_application import apply_judgment


SPEC = [{
    "name": "MELCHIOR", "provider": "ollama", "model": "fake",
    "endpoint": "http://127.0.0.1:11434",
    "enabled": True, "weight": 1.0, "timeout_seconds": 5,
}]
POLICY = {
    "profile_id": "fake-profile", "profile_name": "daily-test",
    "revision": 1, "grants": [{
        "label": "llm.send_context.private", "destination_scope": "local",
        "decision": "allow", "mask_rules": {"tokens": []},
    }],
}


def judgment(next_step: str, *, source: str = "pkb", refs=None, answer=None,
             basis: str = "insufficient", reason: str = "根拠を確認する") -> dict:
    """実LLMを使わず、同じMAGIの構造化Goal提案を生成する。"""
    return {
        "goal": "本人が依頼した情報を確認する",
        "next": next_step,
        "fulfillment": "met" if next_step == "respond" else "partial",
        "answer": answer,
        "evidence": {
            "basis": basis, "result_ids": list(refs or []),
            "assessment": "Taskに記録された結果に限る",
        },
        "read_request": {
            "source": source, "query": "本人の対象情報",
            "reason": "対象となるSourceの読取が必要",
        } if next_step == "request_read" else None,
        "question": None,
        "reason": reason,
    }


class GoalSourceNeutralTests(unittest.IsolatedAsyncioTestCase):
    """既存Task Loop一つで本人情報の要求・根拠・完了を検証する。"""

    def test_source_contract_reuses_read_capabilities(self) -> None:
        """MAGIが選べるSourceはRITSUKOの既存自動read能力と一致する。"""
        from ritsuko.application.observation_sources import AUTO_READ_SOURCES
        self.assertEqual(
            set(GOAL_SCHEMA["properties"]["read_request"]["anyOf"][0]
                ["properties"]["source"]["enum"]),
            set(AUTO_READ_SOURCES),
        )
        for source in ("pkb", "web", "finance"):
            self.assertEqual(validate_goal_turn(judgment(
                "request_read", source=source,
            )), [])
        self.assertIn("read_request:invalid", validate_goal_turn(judgment(
            "request_read", source="external_service",
        )))

    async def test_invoke_read_uses_same_persisted_task_and_result(self) -> None:
        """MAGI invoke dispatches through the existing Source runner and loops."""
        task_id = uuid4()
        calls = []

        async def fake_member(_spec, envelope, **_kwargs):
            self.assertIn("pkb.search", envelope["operation_catalog"])
            if envelope["turn"] == 1:
                result = judgment("invoke")
                result["operation_request"] = {
                    "operation_id": "pkb.search",
                    "arguments": {"query": "架空PCの構成"},
                    "reason": "既存記憶を確認",
                }
                return {"status": "ok", "response": result}
            self.assertEqual(envelope["observations"][0]["result_id"], "result-invoke")
            self.assertEqual(envelope["observations"][0]["operation_ids"], ["pkb.search"])
            return {"status": "ok", "response": judgment(
                "respond", basis="result_refs", refs=["result-invoke"],
                answer="架空PCの保存構成",
            )}

        def execute(_request, pending, _session):
            self.assertEqual(pending["source"], "pkb")
            self.assertEqual(pending["operation_ids"], ["pkb.search"])
            self.assertEqual(pending["what"], "架空PCの構成")
            calls.append("executed")
            return {
                "status": "ok", "source": "pkb",
                "verified_by": "deterministic_pkb_query",
                "capability": "pkb_search", "total": 1,
                "answer": "架空PCの保存構成",
                "result": {"result_kind": "search", "items": [{"name": "架空PC"}]},
            }

        def record(_task, _execution, _pending):
            self.assertEqual(_task, task_id)
            calls.append("recorded")
            return ("action-invoke", "result-invoke")

        def persist(_task, session, _capability):
            self.assertEqual(_task, task_id)
            self.assertEqual(task_projection(session)["task_status"], "completed")
            calls.append("persisted")

        session = await run_observation_loop(
            "架空PCを調べて", member_specs=SPEC, timeout=5, task_id=task_id,
            create_task_record=lambda *_a: calls.append("created"),
            execute_source_request=execute,
            record_source_read_record=record,
            persist_session_record=persist,
            fail_task_record=lambda *_a: self.fail("unexpected failure"),
            dialogue_starter=partial(
                start_dialogue_async, decision_strategy="llm",
                goal_caller=fake_member, policy_snapshot=POLICY,
            ),
            observation_continuation=partial(
                continue_with_verified_observations_async,
                goal_caller=fake_member,
            ),
            resource_catalog={"pkb": {"available": True}},
        )
        self.assertEqual(len(session["turns"]), 2)
        self.assertEqual(calls, ["created", "executed", "recorded", "persisted"])

    async def test_private_source_is_chosen_by_magi_not_python(self) -> None:
        """私的Sourceの判断と選択はMAGI、実行はRITSUKOが行う。"""
        async def fake_member(_member, envelope, **_kwargs):
            self.assertEqual(envelope["user_input"]["raw"], "私の家計を調べて")
            self.assertIn("finance", envelope["resource_catalog"])
            return {"status": "ok", "response": judgment(
                "request_read", source="finance",
            )}
        session = await start_dialogue_async(
            "私の家計を調べて", member_specs=SPEC, timeout=5,
            decision_strategy="llm", policy_snapshot=POLICY,
            goal_caller=fake_member,
        )
        self.assertEqual(session["status"], "waiting_information")
        self.assertEqual(session["pending_requests"][0]["source"], "finance")
        self.assertFalse(session["tool_read_executed"])
        self.assertNotEqual(task_projection(session)["task_status"], "completed")

    async def test_pkb_and_finance_use_same_task_recording_and_evidence(self) -> None:
        """DB保存を模擬し、両Sourceで同一TaskのResultを参照して完了する。"""
        for source in ("pkb", "finance"):
            with self.subTest(source=source):
                result_id = str(uuid4())
                calls = []
                task_id = uuid4()

                async def fake_member(_member, envelope, **_kwargs):
                    """開始時は指定Source取得、読取後は保存済みIDで回答する。"""
                    self.assertIn(source, envelope["resource_catalog"])
                    self.assertTrue(envelope["resource_catalog"][source]["available"])
                    if envelope["turn"] == 1:
                        return {"status": "ok", "response": judgment(
                            "request_read", source=source,
                        )}
                    self.assertEqual(envelope["observations"][0]["result_id"], result_id)
                    self.assertEqual(envelope["observations"][0]["source"], source)
                    return {"status": "ok", "response": judgment(
                        "respond", refs=[result_id], basis="result_refs",
                        answer="保存された根拠に対応する回答",
                    )}

                def execute(_request, pending, _session):
                    """RITSUKOだけが実行するfake read。外部アクセスは行わない。"""
                    calls.append(("read", pending["source"]))
                    return {
                        "status": "ok", "source": source, "answer": "対象の仮データ",
                        "total": 1, "result": {"result_kind": "fake"},
                        "confidentiality": "private",
                    }

                def record(task, _execution, _pending):
                    """実Sourceが確定するAction/Result IDをRITSUKOが発行する。"""
                    self.assertEqual(task, task_id)
                    calls.append(("record", source))
                    return str(uuid4()), result_id

                def persist(task, session, _capability):
                    """確定結果と判断方式が同一Taskに残ることを検査する。"""
                    self.assertEqual(task, task_id)
                    self.assertEqual(session["decision_strategy"], "llm")
                    self.assertEqual(
                        task_projection(session)["task_status"], "completed",
                        msg=(session.get("status"), session.get("next_step"),
                             session.get("cloud_context_gate", {}).get("status"),
                             session.get("goal_judgment", {}).get("next")),
                    )
                    calls.append(("persist", source))

                session = await run_observation_loop(
                    "私の情報を確認して",
                    member_specs=SPEC, timeout=5, task_id=task_id,
                    create_task_record=lambda *_args: calls.append(("create", source)),
                    execute_source_request=execute,
                    record_source_read_record=record,
                    persist_session_record=persist,
                    fail_task_record=lambda *_args: calls.append(("fail", source)),
                    dialogue_starter=partial(
                        start_dialogue_async, decision_strategy="llm",
                        goal_caller=fake_member, policy_snapshot=POLICY,
                    ),
                    observation_continuation=partial(
                        continue_with_verified_observations_async,
                        goal_caller=fake_member,
                    ),
                )
                self.assertEqual(task_projection(session)["task_status"], "completed")
                self.assertEqual(calls, [
                    ("create", source), ("read", source),
                    ("record", source), ("persist", source),
                ])
                self.assertEqual(session["observations"][0]["verification_level"],
                                 "bounded_read")

    async def test_forged_and_failed_private_results_cannot_complete(self) -> None:
        """失敗した読取や別Task由来のIDを根拠として完了扱いにしない。"""
        session = {
            "status": "running", "next_step": "evaluate_goal",
            "observations": [{
                "source": "finance", "verified": False,
                "result_id": "recorded-failure", "verification_level": "read_failed",
            }],
            "goal_judgment": None, "pending_requests": [],
            "user_question": None,
        }
        await apply_judgment(session, judgment(
            "respond", refs=["recorded-failure"],
            basis="result_refs", answer="未確認の家計額",
        ), strategy="llm", purpose="evaluate_goal")
        self.assertNotEqual(task_projection(session)["task_status"], "completed")

        session["status"] = "running"
        await apply_judgment(session, judgment(
            "respond", refs=["other-task-result"], basis="result_refs",
            answer="別Taskの情報",
        ), strategy="llm", purpose="evaluate_goal")
        self.assertEqual(session["next_step"], "unrecorded_task_result_reference")


if __name__ == "__main__":
    unittest.main()
