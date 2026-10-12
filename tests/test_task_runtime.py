"""Isolated contract checks for the GUI-neutral RITSUKO Task entry."""
import unittest
from unittest.mock import Mock
from uuid import uuid4

from ritsuko.application.task_runtime import (
    StateDrivenTaskApplication,
    TaskRuntimePorts,
)


class TaskRuntimeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        names = [
            "create_task_record", "execute_source_request",
            "record_source_read_record", "persist_session_record",
            "fail_task_record", "claim_user_resume_record",
            "abort_user_resume_record", "claim_proposal_review_record",
            "finalize_proposal_review_record", "abort_proposal_review_record",
        ]
        self.ports = TaskRuntimePorts(**{name: Mock(name=name) for name in names})
        self.calls = []

        async def start(*args, **kwargs):
            self.calls.append(("start", args, kwargs))
            return {"task_id": "start", "status": "waiting_user"}

        async def resume(*args, **kwargs):
            self.calls.append(("resume", args, kwargs))
            return {"task_id": "resume", "status": "candidate_ready"}

        async def review(*args, **kwargs):
            self.calls.append(("review", args, kwargs))
            return {"task_id": "review", "status": "review_evaluated"}

        self.app = StateDrivenTaskApplication(
            self.ports, start_runner=start,
            resume_runner=resume, review_runner=review,
        )

    async def test_start_forwards_only_runtime_ports_and_options(self):
        task_id = uuid4()
        result = await self.app.start(
            "調べて", member_specs=[{"member": "MELCHIOR"}],
            timeout=30, task_id=task_id,
            dialogue_starter=lambda *a, **k: None,
            observation_continuation=lambda *a, **k: None,
        )
        self.assertEqual(result["status"], "waiting_user")
        kind, args, options = self.calls.pop()
        self.assertEqual(kind, "start")
        self.assertEqual(args, ("調べて",))
        self.assertIs(options["create_task_record"], self.ports.create_task_record)
        self.assertIs(options["execute_source_request"], self.ports.execute_source_request)
        self.assertIs(options["record_source_read_record"], self.ports.record_source_read_record)
        self.assertIs(options["persist_session_record"], self.ports.persist_session_record)
        self.assertIs(options["fail_task_record"], self.ports.fail_task_record)
        self.assertEqual(options["task_id"], task_id)
        self.assertNotIn("ui", options)

    async def test_answer_resumes_same_task_with_persistence_ports(self):
        task_id = uuid4()
        result = await self.app.answer(task_id, "GPUは9070XT", timeout=60)
        self.assertEqual(result["task_id"], "resume")
        kind, args, options = self.calls.pop()
        self.assertEqual((kind, args), ("resume", (task_id, "GPUは9070XT")))
        self.assertIs(options["claim_user_resume_record"], self.ports.claim_user_resume_record)
        self.assertIs(options["abort_user_resume_record"], self.ports.abort_user_resume_record)
        self.assertIs(options["persist_session_record"], self.ports.persist_session_record)

    async def test_review_reuses_same_task_and_review_ports(self):
        task_id = uuid4()
        result = await self.app.review(task_id, "remember", timeout=60,
                                       memory_result={"candidates": []})
        self.assertEqual(result["task_id"], "review")
        kind, args, options = self.calls.pop()
        self.assertEqual((kind, args), ("review", (task_id, "remember")))
        self.assertIs(options["claim_proposal_review_record"], self.ports.claim_proposal_review_record)
        self.assertIs(options["finalize_proposal_review_record"], self.ports.finalize_proposal_review_record)
        self.assertIs(options["abort_proposal_review_record"], self.ports.abort_proposal_review_record)
        self.assertEqual(options["memory_result"], {"candidates": []})


if __name__ == "__main__":
    unittest.main()
