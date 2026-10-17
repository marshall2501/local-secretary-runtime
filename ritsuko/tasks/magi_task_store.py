"""Persistence port for state-driven RITSUKO⇄MAGI Task state."""
from __future__ import annotations

from typing import Protocol
from uuid import UUID

from pkb.memory_contracts import MemoryIntake


class MagiTaskRepository(Protocol):
    def reserve_start(
        self, *, task_id: UUID, request: str, member_specs: list[dict],
        decision_strategy: str, policy_snapshot: dict | None,
    ) -> bool: ...

    def claim_start(
        self, *, task_id: UUID, request: str, member_specs: list[dict],
    ) -> UUID: ...

    def list_pending_starts(self, *, limit: int = 20) -> list[dict]: ...

    def requeue_paused(self, *, task_id: UUID) -> dict: ...

    def request_stop(self, *, task_id: UUID) -> str: ...
    def create_task(
        self, *, task_id: UUID, request: str, member_specs: list[dict]
    ) -> None: ...

    def claim_user_resume(
        self, *, task_id: UUID, reply_length: int, reply_fingerprint: str
    ) -> tuple[dict, str | None]: ...

    def abort_user_resume(self, *, task_id: UUID, error: str) -> None: ...

    def persist_session(
        self,
        *,
        task_id: UUID,
        session: dict,
        selected_capability: str | None = None,
        expected_attempt_id: UUID | None = None,
    ) -> dict: ...

    def prepare_memory_intake(self, *, task_id: UUID) -> MemoryIntake: ...

    def claim_proposal_review(
        self,
        *,
        task_id: UUID,
        decision: str,
        memory_result: dict | None = None,
    ) -> tuple[dict, str | None, dict, dict]: ...

    def finalize_proposal_review(
        self,
        *,
        task_id: UUID,
        session: dict,
        selected_capability: str | None = None,
    ) -> dict: ...

    def abort_proposal_review(self, *, task_id: UUID, error: str) -> None: ...

    def record_source_read(
        self, *, task_id: UUID, execution: dict, pending_request: dict,
        expected_attempt_id: UUID | None = None,
    ) -> tuple[str, str]: ...

    def record_pkb_read(
        self, *, task_id: UUID, execution: dict, pending_request: dict
    ) -> tuple[str, str]: ...

    def fail_task(self, *, task_id: UUID, error: str,
                  expected_attempt_id: UUID | None = None) -> None: ...


def create_task(repository: MagiTaskRepository, **kwargs) -> None:
    repository.create_task(**kwargs)


def claim_user_resume(repository: MagiTaskRepository, **kwargs):
    return repository.claim_user_resume(**kwargs)


def abort_user_resume(repository: MagiTaskRepository, **kwargs) -> None:
    repository.abort_user_resume(**kwargs)


def persist_session(repository: MagiTaskRepository, **kwargs) -> dict:
    return repository.persist_session(**kwargs)


def prepare_memory_intake(
    repository: MagiTaskRepository, **kwargs
) -> MemoryIntake:
    return repository.prepare_memory_intake(**kwargs)


def claim_proposal_review(repository: MagiTaskRepository, **kwargs):
    return repository.claim_proposal_review(**kwargs)


def finalize_proposal_review(
    repository: MagiTaskRepository, **kwargs
) -> dict:
    return repository.finalize_proposal_review(**kwargs)


def abort_proposal_review(repository: MagiTaskRepository, **kwargs) -> None:
    repository.abort_proposal_review(**kwargs)


def record_source_read(repository: MagiTaskRepository, **kwargs) -> tuple[str, str]:
    return repository.record_source_read(**kwargs)


def record_pkb_read(repository: MagiTaskRepository, **kwargs) -> tuple[str, str]:
    return repository.record_pkb_read(**kwargs)


def fail_task(repository: MagiTaskRepository, **kwargs) -> None:
    repository.fail_task(**kwargs)
