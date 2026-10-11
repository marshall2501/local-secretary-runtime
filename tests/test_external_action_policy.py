"""Safety tests for isolated external Action policy; no HTTP or DB access."""
import unittest
from datetime import datetime, timedelta, timezone

from ritsuko.application.external_action_policy import (
    ActionPolicyError,
    RegisteredOperation,
    check_approved_action,
    prepare_action,
)


class ExternalActionPolicyTests(unittest.TestCase):
    def setUp(self):
        self.operation = RegisteredOperation(
            operation_id="fixture.setting.update",
            connection_id="fixture-local",
            required_permission="fixture_write",
            risk="high",
            input_fields=frozenset({"resource_id", "value"}),
        )
        self.registry = {self.operation.operation_id: self.operation}
        self.args = {"resource_id": "setting-1", "value": "enabled"}
        self.params = dict(
            task_id="task-1",
            operation_id=self.operation.operation_id,
            connection_id="fixture-local",
            arguments=self.args,
            operation_registry=self.registry,
            actor_permissions=frozenset({"fixture_write"}),
        )
        self.prepared = prepare_action(**self.params)
        self.now = datetime(2026, 10, 11, tzinfo=timezone.utc)
        self.approval = {
            "status": "approved",
            "approval_kind": "external_action",
            "task_id": "task-1",
            "scope": {"action_digest": self.prepared["action_digest"]},
            "expires_at": self.now + timedelta(minutes=5),
        }

    def test_approved_exact_action(self):
        check_approved_action(self.prepared, self.approval, now=self.now)

    def test_stable_idempotency_key_independent_of_field_order(self):
        reordered = {**self.params, "arguments": {"value": "enabled", "resource_id": "setting-1"}}
        self.assertEqual(
            prepare_action(**reordered)["idempotency_key"],
            self.prepared["idempotency_key"],
        )

    def test_reject_unknown_operation_and_connection(self):
        for change in (
            {"operation_id": "unregistered.operation"},
            {"connection_id": "other"},
            {"actor_permissions": frozenset()},
            {"arguments": {"resource_id": "setting-1", "value": "enabled", "url": "http://bad"}},
            {"arguments": {"resource_id": "", "value": "enabled"}},
        ):
            with self.subTest(change=change), self.assertRaises(ActionPolicyError):
                prepare_action(**{**self.params, **change})

    def test_reject_missing_denied_wrong_or_expired_approval(self):
        cases = [
            None,
            {**self.approval, "status": "pending"},
            {**self.approval, "approval_kind": "memory_write"},
            {**self.approval, "task_id": "task-2"},
            {**self.approval, "scope": {"action_digest": "other"}},
            {**self.approval, "expires_at": self.now},
            {**self.approval, "expires_at": self.now - timedelta(minutes=1)},
        ]
        for case in cases:
            with self.subTest(case=case), self.assertRaises(ActionPolicyError):
                check_approved_action(self.prepared, case, now=self.now)

    def test_changed_arguments_are_not_approved(self):
        altered = prepare_action(**{
            **self.params,
            "arguments": {"resource_id": "setting-1", "value": "disabled"},
        })
        with self.assertRaisesRegex(ActionPolicyError, "approval_scope_mismatch"):
            check_approved_action(altered, self.approval, now=self.now)

    def test_changed_target_is_not_approved(self):
        altered = prepare_action(**{
            **self.params,
            "arguments": {"resource_id": "setting-2", "value": "enabled"},
        })
        with self.assertRaisesRegex(ActionPolicyError, "approval_scope_mismatch"):
            check_approved_action(altered, self.approval, now=self.now)

    def test_no_secret_or_raw_url_is_accepted_as_extra_parameter(self):
        with self.assertRaises(ActionPolicyError):
            prepare_action(**{
                **self.params,
                "arguments": {**self.args, "authorization": "Bearer secret"},
            })


if __name__ == "__main__":
    unittest.main()
