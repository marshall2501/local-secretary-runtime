# Provenance-Origin: original
# Provenance-AI: AI-generated
"""NiceGUIとVueから再利用するRITSUKO Task Compositionの隔離ノンデグレテスト。"""
from __future__ import annotations

import unittest
from uuid import uuid4

from bootstrap.ritsuko_task_composition import build_task_application


PORT_NAMES = (
    "create_task_record", "execute_source_request", "record_source_read_record",
    "persist_session_record", "fail_task_record", "claim_user_resume_record",
    "abort_user_resume_record", "claim_proposal_review_record",
    "finalize_proposal_review_record", "abort_proposal_review_record",
)


class TaskCompositionTests(unittest.IsolatedAsyncioTestCase):
    """実DBやLLMを使わず、共有ApplicationのPort注入を検証する。"""

    async def test_shared_start_answer_review_bind_same_port_objects(self):
        """Taskの開始・本人回答・Reviewで、必要なPortがRunnerへ渡ることを確認する。"""
        record = []
        ports = {name: (lambda *args, **kwargs: None) for name in PORT_NAMES}

        async def start_runner(request, **kwargs):
            """新規受付時に注入Portを記録し、質問待ち状態を模倣する。"""
            record.append(("start", request, kwargs))
            return {"task_id": "synthetic", "status": "waiting_user"}

        async def answer_runner(task_id, reply, **kwargs):
            """同じTask IDへの本人回答再開とPort配線を記録する。"""
            record.append(("answer", task_id, kwargs))
            return {"task_id": str(task_id), "status": "completed", "reply": reply}

        async def review_runner(task_id, decision, **kwargs):
            """Proposal Reviewの決定を外部Action無しで記録する。"""
            record.append(("review", task_id, kwargs))
            return {"task_id": str(task_id), "decision": decision}

        app = build_task_application(
            **ports, start_runner=start_runner,
            resume_runner=answer_runner, review_runner=review_runner,
        )
        uid = uuid4()
        specs = [{"name": "MELCHIOR", "enabled": True}]
        self.assertEqual((await app.start(
            "テスト", member_specs=specs, timeout=5, task_id=uid,
        ))["status"], "waiting_user")
        self.assertEqual((await app.answer(
            uid, "補足", timeout=5,
        ))["status"], "completed")
        self.assertEqual((await app.review(
            uid, "complete_answer_only", timeout=5,
        ))["decision"], "complete_answer_only")
        self.assertEqual([entry[0] for entry in record], ["start", "answer", "review"])

        start_kw, answer_kw, review_kw = (entry[2] for entry in record)
        for key in PORT_NAMES[:5]:
            self.assertIs(start_kw[key], ports[key])
        for key in (
            "claim_user_resume_record", "persist_session_record",
            "abort_user_resume_record", "fail_task_record",
        ):
            self.assertIs(answer_kw[key], ports[key])
        for key in PORT_NAMES[7:]:
            self.assertIs(review_kw[key], ports[key])
        self.assertEqual(start_kw["task_id"], uid)
        self.assertEqual(start_kw["member_specs"], specs)

    async def test_optional_action_writes_remain_disabled(self):
        """Action Serviceを注入しなければ外部writeを公開しない。"""
        no_op = lambda *args, **kwargs: None
        app = build_task_application(**dict.fromkeys(PORT_NAMES, no_op))
        with self.assertRaisesRegex(RuntimeError, "external_action_service_not_configured"):
            await app.propose_action(task_id=uuid4(), operation_id="x", value="x")


if __name__ == "__main__":
    unittest.main()
