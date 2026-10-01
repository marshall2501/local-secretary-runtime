"""RITSUKO state-driven observation loop for the first real PKB vertical slice.

The loop owns control order; concrete PKB and persistence adapters are injected.
Synchronous DB/PKB adapters run in AnyIO worker threads so external waiting does
not take over the event loop.
"""
from __future__ import annotations

from collections.abc import Callable
from uuid import UUID, uuid4

import anyio

from .magi_async import (
    continue_with_user_clarification_async,
    continue_with_verified_observation_async,
    start_dialogue_async,
)
from .magi_core_bridge import pending_pkb_request, verified_pkb_observation


async def run_pkb_observation_loop(
    user_raw: str,
    *,
    member_specs: list[dict],
    timeout: float,
    create_task_record: Callable,
    execute_pkb_request: Callable,
    record_pkb_read_record: Callable,
    persist_session_record: Callable,
    fail_task_record: Callable,
    stop_requested=None,
    on_turn_start=None,
    task_id: UUID | None = None,
    dialogue_starter=start_dialogue_async,
    observation_continuation=continue_with_verified_observation_async,
) -> dict:
    """Run one persisted RITSUKO→MAGI→PKB→MAGI vertical slice.

    v1 intentionally auto-executes only all-PKB read requests. Other sources
    remain waiting for later policy/capability work.
    """
    request = str(user_raw or "").strip()
    if not request:
        raise ValueError("empty_request")
    if not member_specs:
        raise ValueError("member_specs_required")

    task_uuid = task_id or uuid4()
    task_created = False
    try:
        await anyio.to_thread.run_sync(
            create_task_record,
            task_uuid,
            request,
            member_specs,
        )
        task_created = True

        session = await dialogue_starter(
            request,
            member_specs=member_specs,
            timeout=timeout,
            stop_requested=stop_requested,
            on_turn_start=on_turn_start,
            task_id=str(task_uuid),
        )

        pkb_request = pending_pkb_request(session)
        if pkb_request is not None and not (
            stop_requested is not None and stop_requested()
        ):
            execution = await anyio.to_thread.run_sync(
                execute_pkb_request,
                request,
                pkb_request,
            )
            await anyio.to_thread.run_sync(
                record_pkb_read_record,
                task_uuid,
                execution,
                pkb_request,
            )
            observation = verified_pkb_observation(execution, pkb_request)
            session = await observation_continuation(
                session,
                observation,
                timeout=timeout,
                stop_requested=stop_requested,
                on_turn_start=on_turn_start,
            )

        await anyio.to_thread.run_sync(
            persist_session_record,
            task_uuid,
            session,
            "pkb_search" if session.get("tool_read_executed") else None,
        )
        return session
    except Exception as exc:
        if task_created:
            try:
                await anyio.to_thread.run_sync(
                    fail_task_record,
                    task_uuid,
                    type(exc).__name__,
                )
            except Exception:
                pass
        raise



async def resume_user_answer(
    task_id: UUID,
    user_text: str,
    *,
    timeout: float,
    claim_user_resume_record: Callable,
    persist_session_record: Callable,
    fail_task_record: Callable,
    stop_requested=None,
    on_turn_start=None,
    user_continuation=continue_with_user_clarification_async,
) -> dict:
    """Resume one persisted waiting_user MAGI Task with the same Task ID."""
    text = str(user_text or "").strip()
    if not text:
        raise ValueError("empty_user_clarification")

    claimed = False
    try:
        session, selected_capability = await anyio.to_thread.run_sync(
            claim_user_resume_record,
            task_id,
            len(text),
        )
        claimed = True
        updated = await user_continuation(
            session,
            text,
            timeout=timeout,
            stop_requested=stop_requested,
            on_turn_start=on_turn_start,
        )
        await anyio.to_thread.run_sync(
            persist_session_record,
            task_id,
            updated,
            selected_capability,
        )
        return updated
    except Exception as exc:
        if claimed:
            try:
                await anyio.to_thread.run_sync(
                    fail_task_record,
                    task_id,
                    type(exc).__name__,
                )
            except Exception:
                pass
        raise
