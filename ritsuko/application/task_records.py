"""Connection-bound wrappers for persisted RITSUKO/MAGI Task state."""
from __future__ import annotations

from uuid import UUID

from pkb.memory_contracts import MemoryIntake
from ritsuko.tasks.magi_task_store import (
    abort_proposal_review,
    abort_user_resume,
    claim_proposal_review,
    claim_user_resume,
    create_task,
    fail_task,
    finalize_proposal_review,
    persist_session,
    prepare_memory_intake,
    record_pkb_read,
)


def create_task_record(connection_factory, task_id: UUID, request: str,
                       member_specs: list[dict]) -> None:
    with connection_factory() as db:
        create_task(db, task_id=task_id, request=request, member_specs=member_specs)


def claim_user_resume_record(connection_factory, task_id: UUID,
                             reply_length: int, reply_fingerprint: str):
    with connection_factory() as db:
        return claim_user_resume(
            db, task_id=task_id, reply_length=reply_length,
            reply_fingerprint=reply_fingerprint,
        )


def abort_user_resume_record(connection_factory, task_id: UUID, error_type: str) -> None:
    with connection_factory() as db:
        abort_user_resume(db, task_id=task_id, error=error_type)


def claim_proposal_review_record(connection_factory, task_id: UUID,
                                 decision: str, memory_result: dict | None):
    with connection_factory() as db:
        return claim_proposal_review(
            db, task_id=task_id, decision=decision, memory_result=memory_result,
        )


def finalize_proposal_review_record(connection_factory, task_id: UUID,
                                    session: dict, selected_capability: str | None) -> dict:
    with connection_factory() as db:
        return finalize_proposal_review(
            db, task_id=task_id, session=session,
            selected_capability=selected_capability,
        )


def abort_proposal_review_record(connection_factory, task_id: UUID,
                                 error_type: str) -> None:
    with connection_factory() as db:
        abort_proposal_review(db, task_id=task_id, error=error_type)


def prepare_memory_intake_record(connection_factory, task_id: UUID) -> MemoryIntake:
    with connection_factory() as db:
        return prepare_memory_intake(db, task_id=task_id)


def persist_session_record(connection_factory, task_id: UUID, session: dict,
                           selected_capability: str | None = None) -> dict:
    with connection_factory() as db:
        return persist_session(
            db, task_id=task_id, session=session,
            selected_capability=selected_capability,
        )


def record_pkb_read_record(connection_factory, task_id: UUID, execution: dict,
                           pending_request: dict) -> tuple[str, str]:
    with connection_factory() as db:
        return record_pkb_read(
            db, task_id=task_id, execution=execution,
            pending_request=pending_request,
        )


def fail_task_record(connection_factory, task_id: UUID, error_type: str) -> None:
    with connection_factory() as db:
        fail_task(db, task_id=task_id, error=error_type)
