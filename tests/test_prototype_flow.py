"""Offline regression tests for the first G1 vertical slice: persistent task + steps."""
import unittest
from unittest.mock import MagicMock, patch
from uuid import uuid4

from api.secretary_api import (
    PROTOTYPE_STEPS, CreateTask, create_prototype_task, read_prototype_task,
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


if __name__ == "__main__":
    unittest.main()
