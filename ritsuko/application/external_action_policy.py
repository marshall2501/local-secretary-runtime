"""Deterministic, deny-by-default checks for the isolated external Action slice.

This module does not perform HTTP requests, persist approvals, or expose an API.
The executor must acquire a durable idempotency reservation before network I/O.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping


class ActionPolicyError(ValueError):
    """An action must not be sent when this exception is raised."""


@dataclass(frozen=True)
class RegisteredOperation:
    operation_id: str
    connection_id: str
    required_permission: str
    risk: str
    input_fields: frozenset[str]
    resource_field: str = "resource_id"
    # Execution metadata is optional for pure policy tests, but required
    # when an operation is dispatched by ExternalActionService.
    service_id: str = ""
    resource_id: str = ""
    effect: str = ""
    verification_kind: str = "readback_equals"
    kind: str = "write"

    def snapshot(self) -> dict:
        """One canonical, immutable operation descriptor for persistence."""
        if any(not field or len(field) > 100 for field in (
            self.operation_id, self.service_id, self.connection_id,
            self.resource_id, self.required_permission, self.effect,
        )):
            raise ActionPolicyError("invalid_action_operation")
        if self.kind != "write" or self.verification_kind != "readback_equals":
            raise ActionPolicyError("unsupported_action_operation")
        return {
            "operation_id": self.operation_id,
            "service_id": self.service_id,
            "connection_id": self.connection_id,
            "resource_id": self.resource_id,
            "required_permission": self.required_permission,
            "effect": self.effect,
            "verification_kind": self.verification_kind,
            "kind": self.kind,
            "risk": self.risk,
        }

    def __post_init__(self) -> None:
        if (
            not self.operation_id
            or not self.connection_id
            or not self.required_permission
            or not self.input_fields
            or self.resource_field not in self.input_fields
            or self.risk not in {"low", "high"}
        ):
            raise ActionPolicyError("invalid_registered_operation")


def _canonical(value: Mapping[str, Any]) -> str:
    try:
        return json.dumps(
            dict(value),
            sort_keys=True,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        )
    except (TypeError, ValueError) as exc:
        raise ActionPolicyError("invalid_action_arguments") from exc


def prepare_action(
    *,
    task_id: str,
    operation_id: str,
    connection_id: str,
    arguments: Mapping[str, Any],
    operation_registry: Mapping[str, RegisteredOperation],
    actor_permissions: frozenset[str],
) -> dict[str, Any]:
    """Resolve only an allowlisted operation and bind its exact target and args."""
    operation = operation_registry.get(operation_id)
    if operation is None:
        raise ActionPolicyError("operation_not_registered")
    if connection_id != operation.connection_id:
        raise ActionPolicyError("connection_mismatch")
    if operation.required_permission not in actor_permissions:
        raise ActionPolicyError("permission_denied")
    if not task_id or not isinstance(arguments, Mapping):
        raise ActionPolicyError("invalid_action_request")
    if set(arguments) != operation.input_fields:
        raise ActionPolicyError("invalid_action_fields")
    if any(
        isinstance(v, (dict, list, tuple, set)) or v is None
        for v in arguments.values()
    ):
        raise ActionPolicyError("unsupported_argument_value")
    target = arguments.get(operation.resource_field)
    if not isinstance(target, str) or not target.strip():
        raise ActionPolicyError("invalid_target")
    args = dict(arguments)
    canonical = _canonical(
        {
            "task_id": task_id,
            "operation_id": operation_id,
            "connection_id": connection_id,
            "arguments": args,
        }
    )
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return {
        "task_id": task_id,
        "operation_id": operation_id,
        "connection_id": connection_id,
        "target": target,
        "arguments": args,
        "risk": operation.risk,
        "idempotency_key": "external-action:" + digest,
        "action_digest": digest,
    }


def check_approved_action(
    prepared: Mapping[str, Any],
    approval: Mapping[str, Any] | None,
    *,
    now: datetime | None = None,
) -> None:
    """Require an exact, unexpired, Task-bound approval before any write."""
    if not isinstance(approval, Mapping):
        raise ActionPolicyError("approval_required")
    if approval.get("status") != "approved":
        raise ActionPolicyError("approval_not_granted")
    if approval.get("approval_kind") != "external_action":
        raise ActionPolicyError("wrong_approval_kind")
    if str(approval.get("task_id") or "") != prepared["task_id"]:
        raise ActionPolicyError("approval_task_mismatch")
    scope = approval.get("scope")
    if not isinstance(scope, Mapping):
        raise ActionPolicyError("invalid_approval_scope")
    if scope.get("action_digest") != prepared["action_digest"]:
        raise ActionPolicyError("approval_scope_mismatch")
    expires = approval.get("expires_at")
    if not isinstance(expires, datetime) or expires.tzinfo is None:
        raise ActionPolicyError("invalid_approval_expiry")
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        raise ActionPolicyError("invalid_current_time")
    if current >= expires:
        raise ActionPolicyError("approval_expired")
