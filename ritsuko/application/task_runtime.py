"""GUI-independent entry for the existing state-driven RITSUKO Task workflow.

The Core owns Task transitions and execution. This Application boundary only
wires persistence ports to the existing observation loop; it can also be used
from a trusted non-GUI adapter without duplicating its business decisions.
No external write, new API route, or database permission is introduced here.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable
from uuid import UUID


@dataclass(frozen=True)
class TaskRuntimePorts:
    create_task_record: Callable
    execute_source_request: Callable
    record_source_read_record: Callable
    persist_session_record: Callable
    fail_task_record: Callable
    claim_user_resume_record: Callable
    abort_user_resume_record: Callable
    claim_proposal_review_record: Callable
    finalize_proposal_review_record: Callable
    abort_proposal_review_record: Callable


class StateDrivenTaskApplication:
    """Expose start/answer/review as one stable, UI-neutral Task entry.

    Caller-specific LLM progress callbacks and cancellation stay optional.
    Task and MAGI state, approval, and completion decisions stay in the Core.
    """

    def __init__(self, ports: TaskRuntimePorts, *,
                 start_runner: Callable, resume_runner: Callable,
                 review_runner: Callable):
        self._ports = ports
        self._start_runner = start_runner
        self._resume_runner = resume_runner
        self._review_runner = review_runner

    async def start(self, request: str, *, member_specs: list[dict],
                    timeout: float, task_id: UUID | None = None,
                    stop_requested=None, on_turn_start=None,
                    dialogue_starter=None, observation_continuation=None) -> dict:
        options = {
            "member_specs": member_specs,
            "timeout": timeout,
            "create_task_record": self._ports.create_task_record,
            "execute_source_request": self._ports.execute_source_request,
            "record_source_read_record": self._ports.record_source_read_record,
            "persist_session_record": self._ports.persist_session_record,
            "fail_task_record": self._ports.fail_task_record,
            "task_id": task_id,
            "stop_requested": stop_requested,
            "on_turn_start": on_turn_start,
        }
        if dialogue_starter is not None:
            options["dialogue_starter"] = dialogue_starter
        if observation_continuation is not None:
            options["observation_continuation"] = observation_continuation
        return await self._start_runner(request, **options)

    async def answer(self, task_id: UUID, reply: str, *,
                     timeout: float, stop_requested=None,
                     on_turn_start=None, user_continuation=None) -> dict:
        options = {
            "timeout": timeout,
            "claim_user_resume_record": self._ports.claim_user_resume_record,
            "persist_session_record": self._ports.persist_session_record,
            "abort_user_resume_record": self._ports.abort_user_resume_record,
            "fail_task_record": self._ports.fail_task_record,
            "stop_requested": stop_requested,
            "on_turn_start": on_turn_start,
        }
        if user_continuation is not None:
            options["user_continuation"] = user_continuation
        return await self._resume_runner(task_id, reply, **options)

    async def review(self, task_id: UUID, decision: str, *,
                     timeout: float, memory_result: dict | None = None,
                     stop_requested=None, on_turn_start=None,
                     review_continuation=None) -> dict:
        options = {
            "timeout": timeout,
            "claim_proposal_review_record": self._ports.claim_proposal_review_record,
            "finalize_proposal_review_record": self._ports.finalize_proposal_review_record,
            "abort_proposal_review_record": self._ports.abort_proposal_review_record,
            "memory_result": memory_result,
            "stop_requested": stop_requested,
            "on_turn_start": on_turn_start,
        }
        if review_continuation is not None:
            options["review_continuation"] = review_continuation
        return await self._review_runner(task_id, decision, **options)
