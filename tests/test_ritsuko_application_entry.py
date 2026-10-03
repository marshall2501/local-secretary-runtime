"""Offline tests for the common RITSUKO application entry."""
from __future__ import annotations

import unittest
from uuid import UUID

from ritsuko.application.entry import RitsukoApplicationEntry


class FakeRepository:
    def __init__(self):
        self.calls=[]
    def persist_initial(self, **kwargs):
        self.calls.append(kwargs)


class RitsukoApplicationEntryTests(unittest.TestCase):
    def make_entry(self, *, scoped, execution=None, comparison=None):
        repo=FakeRepository()
        queued=[]
        entry=RitsukoApplicationEntry(
            repo,
            load_context=lambda request: ({"PC":{"name":"PC"}}, {"version":"obs-v1"}),
            scope_request=lambda request, entities, observation: dict(scoped),
            execute_read=lambda capability, request: execution,
            execute_compare=lambda request: comparison,
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


if __name__ == "__main__":
    unittest.main()
