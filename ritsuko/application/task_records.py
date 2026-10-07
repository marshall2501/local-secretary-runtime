"""Application wrappers for persisted RITSUKO/MAGI Task state."""
from __future__ import annotations

from uuid import UUID

from pkb.memory_contracts import MemoryIntake
from ritsuko.tasks.magi_task_store import MagiTaskRepository


def create_task_record(repository: MagiTaskRepository, task_id: UUID, request: str,
                       member_specs: list[dict]) -> None:
    repository.create_task(
        task_id=task_id, request=request, member_specs=member_specs
    )


def claim_user_resume_record(repository: MagiTaskRepository, task_id: UUID,
                             reply_length: int, reply_fingerprint: str):
    return repository.claim_user_resume(
        task_id=task_id,
        reply_length=reply_length,
        reply_fingerprint=reply_fingerprint,
    )


def abort_user_resume_record(repository: MagiTaskRepository, task_id: UUID,
                             error_type: str) -> None:
    repository.abort_user_resume(task_id=task_id, error=error_type)


def claim_proposal_review_record(repository: MagiTaskRepository, task_id: UUID,
                                 decision: str, memory_result: dict | None):
    return repository.claim_proposal_review(
        task_id=task_id, decision=decision, memory_result=memory_result,
    )


def finalize_proposal_review_record(repository: MagiTaskRepository, task_id: UUID,
                                    session: dict,
                                    selected_capability: str | None) -> dict:
    return repository.finalize_proposal_review(
        task_id=task_id,
        session=session,
        selected_capability=selected_capability,
    )


def abort_proposal_review_record(repository: MagiTaskRepository, task_id: UUID,
                                 error_type: str) -> None:
    repository.abort_proposal_review(task_id=task_id, error=error_type)


def prepare_memory_intake_record(repository: MagiTaskRepository,
                                 task_id: UUID) -> MemoryIntake:
    return repository.prepare_memory_intake(task_id=task_id)


def persist_session_record(repository: MagiTaskRepository, task_id: UUID,
                           session: dict,
                           selected_capability: str | None = None) -> dict:
    return repository.persist_session(
        task_id=task_id,
        session=session,
        selected_capability=selected_capability,
    )


def record_source_read_record(repository: MagiTaskRepository, task_id: UUID,
                              execution: dict,
                              pending_request: dict) -> tuple[str, str]:
    return repository.record_source_read(
        task_id=task_id,
        execution=execution,
        pending_request=pending_request,
    )


def record_pkb_read_record(repository: MagiTaskRepository, task_id: UUID,
                           execution: dict,
                           pending_request: dict) -> tuple[str, str]:
    return repository.record_pkb_read(
        task_id=task_id,
        execution=execution,
        pending_request=pending_request,
    )


def fail_task_record(repository: MagiTaskRepository, task_id: UUID,
                     error_type: str) -> None:
    repository.fail_task(task_id=task_id, error=error_type)
