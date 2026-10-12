"""RITSUKO's explicit, bounded external Action decision boundary.

This service is not exposed over REST/MCP and never accepts an arbitrary URL.
It can only run an operation that the application registered in advance.
The first registered operation is a disposable loopback HTTP fixture.
"""
from __future__ import annotations

from ritsuko.application.external_action_policy import (RegisteredOperation as Operation, validate_operation_arguments)
from datetime import datetime, timedelta, timezone
from functools import partial
from typing import Protocol
from uuid import UUID

import anyio


class ActionDenied(ValueError):
    """Safe denial; never include credentials or external response bodies."""


class ActionStore(Protocol):
    def offer(self, *, task_id: UUID, operation: dict, arguments: dict,
              requested_by: str, expires_at: datetime) -> dict: ...

    def decide(self, *, task_id: UUID, approval_id: UUID, approved: bool,
               actor: str) -> dict: ...

    def claim(self, *, task_id: UUID, approval_id: UUID) -> dict: ...

    def finish(self, *, task_id: UUID, approval_id: UUID, verified: bool,
               evidence: dict) -> dict: ...

    def mark_unknown(self, *, task_id: UUID, approval_id: UUID,
                     error_type: str) -> dict: ...


class ActionAdapter(Protocol):
    async def write_and_readback(self, *, value: str, timeout: float) -> dict: ...


FIXTURE_OPERATION = Operation(
    operation_id="fixture.setting.update",
    service_id="loopback_fixture",
    connection_id="isolated-fixture-no-secret",
    resource_id="fixture.setting",
    required_permission="external_actions",
    effect="change disposable fixture setting",
    verification_kind="readback_equals",
    risk="high",
    input_fields=frozenset({"resource_id", "value"}),
)


FIXTURE_LOW_IMPACT_OPERATION = Operation(
    operation_id="fixture.setting.update.low",
    service_id="loopback_fixture",
    connection_id="isolated-fixture-no-secret",
    resource_id="fixture.setting",
    required_permission="external_actions",
    effect="change disposable fixture setting (preauthorized low impact)",
    verification_kind="readback_equals",
    risk="low",
    input_fields=frozenset({"resource_id", "value"}),
)

class ExternalActionService:
    """Keep validation, approval, dispatch, and observed result separate.

    The Postgres store owns Task-level authorization, approval snapshot and
    idempotent reservation; no LLM or browser may create those records itself.
    """

    def __init__(self, store: ActionStore, operations: dict[str, tuple[Operation, ActionAdapter]]):
        self._store = store
        self._operations = dict(operations)

    def _lookup(self, operation_id: str) -> tuple[Operation, ActionAdapter]:
        entry = self._operations.get(operation_id)
        if entry is None or entry[0].operation_id != operation_id:
            raise ActionDenied("operation_not_registered")
        return entry

    async def offer(self, *, task_id: UUID, operation_id: str,
                    value: str, requested_by: str = "ritsuko",
                    lifetime_seconds: int = 900) -> dict:
        operation, _ = self._lookup(operation_id)
        if (type(value) is not str or not value or len(value) > 128
                or any(ord(c) < 32 for c in value)):
            raise ActionDenied("invalid_fixture_setting")
        if type(lifetime_seconds) is not int or not 1 <= lifetime_seconds <= 3600:
            raise ActionDenied("invalid_approval_lifetime")
        validate_operation_arguments(
            operation, {"resource_id": operation.resource_id, "value": value},
        )
        # The store must independently verify the persisted Task permission
        # scope. A registered low-impact descriptor alone is not authorization.
        offer_options = {
            "task_id": task_id,
            "operation": operation.snapshot(),
            "arguments": {"value": value},
            "requested_by": requested_by,
            "expires_at": datetime.now(timezone.utc) + timedelta(seconds=lifetime_seconds),
        }
        if operation.risk == "low":
            offer_options["preapproved"] = True
        return await anyio.to_thread.run_sync(partial(
            self._store.offer, **offer_options,
        ))

    async def decide(self, *, task_id: UUID, approval_id: UUID,
                     approved: bool, actor: str) -> dict:
        if type(approved) is not bool or actor != "local_user":
            raise ActionDenied("trusted_local_approval_required")
        return await anyio.to_thread.run_sync(partial(
            self._store.decide, task_id=task_id, approval_id=approval_id,
            approved=approved, actor=actor,
        ))

    async def execute(self, *, task_id: UUID, approval_id: UUID,
                      timeout: float = 5.0) -> dict:
        if not 0 < timeout <= 30:
            raise ActionDenied("invalid_action_timeout")
        # The reservation is persisted *before* making an external call.
        # A second caller or a restarted process may not dispatch it again.
        claimed = await anyio.to_thread.run_sync(partial(
            self._store.claim, task_id=task_id, approval_id=approval_id,
        ))
        try:
            spec, adapter = self._lookup(claimed["operation"]["operation_id"])
            if claimed["operation"] != spec.snapshot():
                raise ActionDenied("operation_snapshot_mismatch")
            if (set(claimed["arguments"]) != {"value"}
                    or type(claimed["arguments"]["value"]) is not str):
                raise ActionDenied("invalid_approved_arguments")
            value = claimed["arguments"]["value"]
            observed = await adapter.write_and_readback(value=value, timeout=timeout)
            if type(observed) is not dict:
                raise ValueError("invalid_verification_result")
            actual = observed.get("observed")
            verified = (
                observed.get("write_ack") is True
                and type(actual) is str
                and actual == value
            )
            evidence = {
                "verification": spec.verification_kind,
                "expected": value,
                "observed": actual if isinstance(actual, str) else None,
                "write_ack": observed.get("write_ack") is True,
            }
        except BaseException as exc:
            # Cancellation and timeouts after dispatch have an UNKNOWN outcome.
            # Shield just the short local persistence operation, not the network.
            with anyio.CancelScope(shield=True):
                await anyio.to_thread.run_sync(partial(
                    self._store.mark_unknown, task_id=task_id,
                    approval_id=approval_id, error_type=type(exc).__name__,
                ))
            raise
        return await anyio.to_thread.run_sync(partial(
            self._store.finish, task_id=task_id, approval_id=approval_id,
            verified=verified, evidence=evidence,
        ))
