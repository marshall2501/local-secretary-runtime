# Provenance-Origin: original
# Provenance-AI: AI-assisted
"""通常Taskの権限Snapshotと実PKB観測に基づく書込み前検査。"""
from __future__ import annotations

import unittest
from uuid import uuid4

from infrastructure.postgres.magi_task_repository import (
    PostgresMagiTaskRepository, create_task, reserve_start,
)
from ritsuko.application.pkb_claim_operation import require_verified_pkb_write_target


class _Cursor:
    """SQLを書き込まずに発行SQLと渡されたJsonbを確認する。"""

    def __init__(self, calls):
        self.calls = calls

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute(self, statement, parameters):
        self.calls.append((statement, parameters))

    def fetchone(self):
        return (uuid4(),)


class _Database:
    """通常DBを用いないTask受付のSQL監視用Connection。"""

    def __init__(self):
        self.calls = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def transaction(self):
        return self

    def cursor(self):
        return _Cursor(self.calls)

    def write_scope(self):
        rows = [params for sql, params in self.calls
                if "INSERT INTO secretary.tasks" in sql]
        assert len(rows) == 1
        return rows[0][3].obj


class TaskWritePermissionTests(unittest.TestCase):
    """書込み許可はdaily専用構成が作る新規Taskにだけ付く。"""

    def setUp(self):
        self.kwargs = {
            "task_id": uuid4(),
            "request": "作業用機材Aの機種はTEST-900です。記録して",
            "member_specs": [{"name": "MELCHIOR", "enabled": True}],
        }

    def test_existing_default_paths_remain_read_only(self):
        for accepting in (False, True):
            with self.subTest(accepting=accepting):
                db = _Database()
                if accepting:
                    reserve_start(db, **self.kwargs, decision_strategy="llm",
                                  policy_snapshot={"profile_id": "lan"})
                else:
                    create_task(db, **self.kwargs)
                self.assertNotIn("pkb_write", db.write_scope())

    def test_only_opted_in_new_tasks_get_persisted_write_scope(self):
        for accepting in (False, True):
            with self.subTest(accepting=accepting):
                db = _Database()
                if accepting:
                    self.assertTrue(reserve_start(
                        db, **self.kwargs, decision_strategy="llm",
                        policy_snapshot={"profile_id": "lan"},
                        allow_pkb_write=True,
                    ))
                else:
                    create_task(db, **self.kwargs, allow_pkb_write=True)
                self.assertIs(db.write_scope()["pkb_write"], True)

    def test_repository_instance_controls_the_permission(self):
        for allowed in (False, True):
            with self.subTest(allowed=allowed):
                db = _Database()
                repo = PostgresMagiTaskRepository(
                    lambda: db, allow_pkb_write=allowed,
                )
                self.assertTrue(repo.reserve_start(
                    **self.kwargs, decision_strategy="llm",
                    policy_snapshot={"profile_id": "lan"},
                ))
                self.assertEqual(
                    db.write_scope().get("pkb_write") is True, allowed,
                )


class VerifiedEntityWriteTests(unittest.TestCase):
    """MAGIが作った対象UUIDをPKB確認済みIDと一致させる。"""

    def setUp(self):
        self.entity = str(uuid4())
        self.other = str(uuid4())
        self.text = "作業用機材Aの機種はTEST-900です。PKBに記録して"
        self.arguments = {
            "entity_id": self.entity,
            "entity_mention": "作業用機材A",
            "predicate": "model",
            "value": "TEST-900",
        }
        self.observation = {
            "source": "pkb", "verified": True,
            "action_id": str(uuid4()), "result_id": str(uuid4()),
            "resolved_entity": {
                "id": self.entity, "name": "作業用機材A",
                "resolution": "pkb_unique_explicit_name",
            },
        }

    def test_verified_unique_existing_entity_is_allowed(self):
        require_verified_pkb_write_target(
            self.text, self.arguments, {"observations": [self.observation]},
        )

    def test_without_real_search_no_action_is_eligible(self):
        for session in (
            {}, {"observations": []},
            {"observations": [{**self.observation, "verified": False}]},
            {"observations": [{**self.observation, "result_id": None}]},
            {"observations": [{
                **self.observation, "resolved_entity": {
                    "id": self.other, "name": "作業用機材A",
                    "resolution": "pkb_unique_explicit_name",
                },
            }]},
        ):
            with self.subTest(session=session):
                with self.assertRaisesRegex(ValueError, "pkb_write_target_not_verified"):
                    require_verified_pkb_write_target(
                        self.text, self.arguments, session,
                    )

    def test_ambiguous_two_verified_entities_are_rejected(self):
        another = {
            **self.observation,
            "resolved_entity": {
                "id": self.other, "name": "別の機材",
                "resolution": "pkb_unique_explicit_name",
            },
        }
        with self.assertRaisesRegex(ValueError, "pkb_write_target_not_verified"):
            require_verified_pkb_write_target(
                self.text, self.arguments,
                {"observations": [self.observation, another]},
            )

    def test_forged_mention_and_invalid_id_are_rejected(self):
        for kwargs in (
            {"entity_id": "not-a-uuid"},
            {"entity_mention": "別の機材"},
            {"entity_mention": ""},
        ):
            with self.subTest(kwargs=kwargs):
                with self.assertRaisesRegex(ValueError, "pkb_write_target_not_verified"):
                    require_verified_pkb_write_target(
                        self.text, {**self.arguments, **kwargs},
                        {"observations": [self.observation]},
                    )

    def test_component_relation_uses_verified_id_not_guessed_alias(self):
        observation = {
            **self.observation,
            "resolved_entity": {
                "id": self.entity, "name": "機材A搭載部品",
                "resolution": "pkb_component_relation",
            },
        }
        require_verified_pkb_write_target(
            self.text, self.arguments, {"observations": [observation]},
        )


if __name__ == "__main__":
    unittest.main()
