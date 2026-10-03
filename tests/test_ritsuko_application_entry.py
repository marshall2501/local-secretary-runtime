"""Offline tests for the common RITSUKO application entry."""
from __future__ import annotations

import unittest
from uuid import UUID

from ritsuko.application.entry import RitsukoApplicationEntry


class FakeResumeSession:
    def __init__(self, state):
        self.state = state
        self.calls = []
        self.recorded = []
    def __enter__(self): return self
    def __exit__(self, exc_type, exc, tb): return False
    def audit_clarification(self, **kwargs): self.calls.append(("audit", kwargs))
    def save_waiting_checkpoint(self, checkpoint): self.calls.append(("waiting", checkpoint))
    def mark_running(self): self.calls.append(("running", None))
    def next_attempt(self): return 4
    def record_execution(self, **kwargs):
        self.recorded.append(kwargs)
        n=len(self.recorded)
        return UUID(int=100+n), UUID(int=200+n)
    def finalize_resume(self, **kwargs): self.calls.append(("finalize", kwargs))


class FakeRepository:
    def __init__(self):
        self.calls=[]
        self.resume_state=None
        self.resume_sessions=[]
    def persist_initial(self, **kwargs):
        self.calls.append(kwargs)
    def resume_session(self, task_id):
        session=FakeResumeSession(self.resume_state)
        self.resume_sessions.append(session)
        return session


class RitsukoApplicationEntryTests(unittest.TestCase):
    def make_entry(self, *, scoped, execution=None, comparison=None):
        repo=FakeRepository()
        queued=[]
        entry=RitsukoApplicationEntry(
            repo,
            load_context=lambda request: ({"メインPC":{"name":"メインPC"}}, {"version":"obs-v1"}),
            scope_request=lambda request, entities, observation: dict(scoped),
            execute_read=lambda capability, request: execution,
            execute_compare=lambda request, **kwargs: comparison,
            advisor_shadow_initial=lambda model, timeout: {"job_status":"queued","model":model},
            queue_advisor=lambda *args: queued.append(args),
        )
        return entry,repo,queued

    def test_empty_request_never_persists(self):
        entry,repo,queued=self.make_entry(scoped={"status":"question"})
        self.assertEqual(entry.request("  ")["status"],"rejected")
        self.assertEqual(repo.calls,[])
        self.assertEqual(queued,[])

    def test_clarification_persists_and_queues_same_task(self):
        entry,repo,queued=self.make_entry(scoped={
            "status":"question","question":"対象は？","reason":"ambiguous","domain":"general"
        })
        task_id=UUID("00000000-0000-0000-0000-000000000001")
        result=entry.request("調べて",task_id=task_id)
        self.assertEqual(result["status"],"waiting_external")
        self.assertEqual(result["task_id"],str(task_id))
        self.assertIsNone(repo.calls[0]["outcome"])
        self.assertEqual(queued[0][0],task_id)

    def test_direct_read_returns_shared_capability_result(self):
        execution={
            "capability":"pkb_search","result":{"status":"ok","result_kind":"claims","total":1},
            "answer":"answer","total":1,"tool":"pkb","operation":"search",
            "source_slug":"pkb-search","citation":"c","verified_by":"v",
        }
        entry,repo,queued=self.make_entry(
            scoped={"status":"ready","capability":"pkb_search","domain":"pc"},
            execution=execution,
        )
        result=entry.request("PCの構成",task_id=UUID(int=2))
        self.assertEqual(result["status"],"completed")
        self.assertEqual(result["search"]["total"],1)
        self.assertEqual(repo.calls[0]["outcome"]["executions"],[execution])

    def test_compare_can_require_clarification(self):
        compare={
            "executions":[{
                "capability":"pkb_search","result":{"total":1},"answer":"local","total":1,
                "tool":"pkb","operation":"search","source_slug":"pkb-search",
                "citation":"c","verified_by":"v",
            }],
            "answer":"need model","comparison":{"status":"insufficient_target"},
            "pkb":{"total":1},"web":None,"needs_clarification":True,
        }
        entry,repo,_=self.make_entry(
            scoped={"status":"ready","capability":"pkb_web_compare","domain":"pc"},
            comparison=compare,
        )
        result=entry.request("比較",task_id=UUID(int=3))
        self.assertEqual(result["status"],"waiting_external")
        self.assertEqual(result["comparison"]["status"],"insufficient_target")
        self.assertIsNone(result["web"])

    def test_resume_keeps_same_task_and_persists_read_result(self):
        execution={
            "capability":"pkb_search","result":{"status":"ok","result_kind":"claims","total":1},
            "answer":"answer","total":1,"tool":"pkb","operation":"search",
            "source_slug":"pkb-search","citation":"c","verified_by":"v",
        }
        entry,repo,_=self.make_entry(
            scoped={"status":"ready","capability":"pkb_search","domain":"pc"},
            execution=execution,
        )
        repo.resume_state={
            "request":"メインPCについて調べて","domain":"pc","status":"waiting_external",
            "checkpoint":{"core_slice":"daily_read_only_v1","user_replies":[]},
        }
        task_id=UUID(int=9)
        result=entry.resume(task_id,"GPUの現在のドライバーを調べて")
        self.assertEqual(result["task_id"],str(task_id))
        self.assertEqual(result["status"],"completed")
        self.assertEqual(result["effective_request"],"メインPCのGPUの現在のドライバーを調べて")
        session=repo.resume_sessions[0]
        self.assertEqual(session.recorded[0]["step"],4)
        self.assertEqual(session.calls[-1][0],"finalize")

    def test_resume_unresolved_saves_waiting_checkpoint_without_execution(self):
        entry,repo,_=self.make_entry(scoped={
            "status":"question","question":"対象は？","reason":"ambiguous","domain":"general"
        })
        repo.resume_state={
            "request":"調べて","domain":"general","status":"waiting_external",
            "checkpoint":{"core_slice":"daily_read_only_v1","user_replies":[]},
        }
        result=entry.resume(UUID(int=10),"まだわからない")
        self.assertEqual(result["status"],"waiting_external")
        session=repo.resume_sessions[0]
        self.assertEqual(session.recorded,[])
        self.assertEqual(session.calls[-1][0],"waiting")


if __name__ == "__main__":
    unittest.main()
