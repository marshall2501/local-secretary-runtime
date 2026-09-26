"""Offline regression tests for the first G1 vertical slice: persistent task + steps."""
import unittest
from unittest.mock import MagicMock, patch
from uuid import uuid4

from api.secretary_api import (
    PROTOTYPE_STEPS, CreateTask, create_prototype_task, read_prototype_task,
    run_prototype_task,
)


def fake_db(cursor):
    db = MagicMock()
    db.__enter__.return_value = db
    db.cursor.return_value.__enter__.return_value = cursor
    return db


class PrototypeFlowTests(unittest.TestCase):
    def test_single_request_persists_task_and_ordered_steps(self):
        task_id = uuid4()
        cur = MagicMock()
        cur.fetchone.side_effect = (
            [{"id": task_id, "status": "pending", "revision": 0, "created_at": None}]
            + [{"id": uuid4(), "step_order": i, "description": text, "status": "pending"}
               for i, text in enumerate(PROTOTYPE_STEPS)]
        )
        body = CreateTask(
            request="Fictional PC game slows down",
            domain="pc",
            completion_criteria="Record a simulated diagnosis",
        )
        with patch("api.secretary_api.connect", return_value=fake_db(cur)):
            result = create_prototype_task(body, actor="local_user")
        self.assertEqual(result["id"], task_id)
        self.assertEqual(result["phase"], "planned")
        self.assertEqual(len(result["steps"]), 4)
        calls = cur.execute.call_args_list
        self.assertEqual(len(calls), 6)  # 1 task + 4 steps + 1 audit
        for index, description in enumerate(PROTOTYPE_STEPS):
            sql, params = calls[index + 1].args
            self.assertIn("INSERT INTO secretary.task_steps", sql)
            self.assertEqual(params, (task_id, index, description))
        self.assertIn("prototype.task_created", calls[-1].args[0])

    def test_read_uses_db_steps_not_transient_python_state(self):
        task_id = uuid4()
        cur = MagicMock()
        cur.fetchone.return_value = {"id": task_id, "status": "pending", "revision": 0}
        cur.fetchall.return_value = [
            {"step_order": 0, "description": PROTOTYPE_STEPS[0], "status": "pending"}
        ]
        with patch("api.secretary_api.connect", return_value=fake_db(cur)):
            result = read_prototype_task(task_id, actor="local_user")
        self.assertEqual(result["id"], task_id)
        self.assertEqual(result["steps"][0]["description"], PROTOTYPE_STEPS[0])
        self.assertIn("ORDER BY step_order", cur.execute.call_args_list[-1].args[0])


    def test_run_recall_mock_result_and_record_are_one_transaction(self):
        task_id = uuid4()
        step_ids = [uuid4() for _ in PROTOTYPE_STEPS]
        source_id, action_id, result_id = uuid4(), uuid4(), uuid4()
        claim_id = uuid4()
        cur = MagicMock()
        cur.fetchone.side_effect = [
            {"id": task_id, "domain": "pc", "entity_id": None,
             "completion_criteria": "模擬診断の結果を記録する",
             "status": "pending", "checkpoint": {}},
            {"id": source_id}, {"id": action_id}, {"id": result_id},
        ]
        cur.fetchall.side_effect = [
            [{"id": sid, "step_order": n, "status": "pending"}
             for n, sid in enumerate(step_ids)],
            [{"id": claim_id, "entity_name": "架空テストPC",
              "predicate": "ram_gb", "value": 16,
              "verification_status": "unverified",
              "citation": "fictional test source"}],
            [],
        ]
        with patch("api.secretary_api.connect", return_value=fake_db(cur)):
            result = run_prototype_task(task_id, actor="local_user")
        self.assertEqual(result["task_id"], str(task_id))
        self.assertEqual(result["status"], "completed")
        self.assertTrue(result["simulated"])
        self.assertTrue(result["criteria_met"])
        self.assertEqual(result["recalled_claims"][0]["id"], str(claim_id))
        self.assertEqual(result["action_id"], str(action_id))
        self.assertEqual(result["result_id"], str(result_id))
        sql = "\n".join(call.args[0] for call in cur.execute.call_args_list)
        self.assertIn("FROM secretary.current_claims", sql)
        self.assertIn("INSERT INTO secretary.sources", sql)
        self.assertIn("INSERT INTO secretary.actions", sql)
        self.assertIn("INSERT INTO secretary.results", sql)
        self.assertIn("UPDATE secretary.tasks", sql)
        self.assertIn("'prototype.mock_completed'", sql)

    def test_run_is_idempotent_after_completion(self):
        task_id = uuid4()
        stored = {"task_id": str(task_id), "status": "completed", "simulated": True}
        cur = MagicMock()
        cur.fetchone.return_value = {
            "id": task_id, "status": "completed", "checkpoint": {"g1": stored},
        }
        with patch("api.secretary_api.connect", return_value=fake_db(cur)):
            result = run_prototype_task(task_id, actor="local_user")
        self.assertEqual(result, stored)
        self.assertEqual(len(cur.execute.call_args_list), 1)

    def test_unknown_criteria_require_verification(self):
        task_id = uuid4()
        step_ids = [uuid4() for _ in PROTOTYPE_STEPS]
        cur = MagicMock()
        cur.fetchone.side_effect = [
            {"id": task_id, "domain": "pc", "entity_id": None,
             "completion_criteria": "Solve the real PC performance problem",
             "status": "pending", "checkpoint": {}},
            {"id": uuid4()}, {"id": uuid4()}, {"id": uuid4()},
        ]
        cur.fetchall.side_effect = [
            [{"id": sid, "step_order": i, "status": "pending"}
             for i, sid in enumerate(step_ids)],
            [],
            [],
        ]
        with patch("api.secretary_api.connect", return_value=fake_db(cur)):
            result = run_prototype_task(task_id, actor="local_user")
        self.assertEqual(result["status"], "waiting_external")
        self.assertFalse(result["criteria_met"])
        self.assertEqual(result["phase"], "needs_verification")


if __name__ == "__main__":
    unittest.main()
