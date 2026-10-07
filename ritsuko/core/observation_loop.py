"""RITSUKO state-driven observation loop for the first real PKB vertical slice.

The loop owns control order; concrete PKB and persistence adapters are injected.
Synchronous DB/PKB adapters run in AnyIO worker threads so external waiting does
not take over the event loop.
"""
from __future__ import annotations

from collections.abc import Callable
from hashlib import sha256
from uuid import UUID, uuid4

import anyio

from ritsuko.magi.async_execution import (
    continue_with_proposal_review_async,
    continue_with_user_clarification_async,
    continue_with_verified_observations_async,
    start_dialogue_async,
)
from ritsuko.application.observation_sources import (
    pending_source_requests,
    selected_capability_for_batch,
    verified_source_observation,
)
from ritsuko.magi.protocol import default_resource_catalog


async def run_observation_loop(
    user_raw: str,
    *,
    member_specs: list[dict],
    timeout: float,
    create_task_record: Callable,
    execute_source_request: Callable,
    record_source_read_record: Callable,
    persist_session_record: Callable,
    fail_task_record: Callable,
    stop_requested=None,
    on_turn_start=None,
    task_id: UUID | None = None,
    dialogue_starter=start_dialogue_async,
    observation_continuation=continue_with_verified_observations_async,
    resource_catalog: dict | None = None,
    max_observation_cycles: int = 4,
) -> dict:
    """Run one persisted Source-neutral RITSUKO→MAGI Observation loop."""
    request = str(user_raw or "").strip()
    if not request:
        raise ValueError("empty_request")
    if not member_specs:
        raise ValueError("member_specs_required")
    if type(max_observation_cycles) is not int or max_observation_cycles < 1:
        raise ValueError("invalid_observation_cycle_limit")

    task_uuid = task_id or uuid4()
    task_created = False
    all_executions: list[dict] = []
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

        catalog = resource_catalog or default_resource_catalog()
        cycles = 0
        while (
            session.get("status") == "waiting_information"
            and cycles < max_observation_cycles
            and not (
                stop_requested is not None
                and stop_requested()
            )
        ):
            groups, unavailable = pending_source_requests(
                session,
                resource_catalog=catalog,
            )
            session["unavailable_source_requests"] = unavailable[:20]
            if not groups:
                break

            results: list[dict | None] = [None] * len(groups)

            async def execute_one(index: int, pending: dict) -> None:
                try:
                    execution = await anyio.to_thread.run_sync(
                        execute_source_request,
                        request,
                        pending,
                        session,
                    )
                    if not isinstance(execution, dict):
                        raise ValueError("invalid_source_execution")
                    results[index] = execution
                except Exception as exc:
                    results[index] = {
                        "status": "error",
                        "source": pending.get("source"),
                        "capability": pending.get("capability"),
                        "tool": pending.get("source") or "source",
                        "operation": "read",
                        "total": 0,
                        "answer": "",
                        "result": {
                            "status": "error",
                            "result_kind": "source_read_error",
                            "error_type": type(exc).__name__,
                        },
                        "verified_by": None,
                        "confidentiality": pending.get("confidentiality"),
                        "error_type": type(exc).__name__,
                    }

            async with anyio.create_task_group() as tg:
                for index, pending in enumerate(groups):
                    tg.start_soon(execute_one, index, pending)

            observations = []
            batch_executions = []
            for pending, execution in zip(groups, results):
                if not isinstance(execution, dict):
                    continue
                execution.setdefault("source", pending.get("source"))
                execution.setdefault(
                    "capability",
                    pending.get("capability"),
                )
                batch_executions.append(execution)
                all_executions.append(execution)
                await anyio.to_thread.run_sync(
                    record_source_read_record,
                    task_uuid,
                    execution,
                    pending,
                )
                observation = verified_source_observation(
                    execution,
                    pending,
                )
                if observation is not None:
                    observations.append(observation)

            if not observations:
                break

            session = await observation_continuation(
                session,
                observations,
                timeout=timeout,
                stop_requested=stop_requested,
                on_turn_start=on_turn_start,
            )
            cycles += 1

        await anyio.to_thread.run_sync(
            persist_session_record,
            task_uuid,
            session,
            selected_capability_for_batch(all_executions),
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
    observation_continuation=None,
) -> dict:
    """Compatibility wrapper for callers/tests using the former PKB-only API."""

    def execute_source(request: str, pending: dict, _session: dict):
        if pending.get("source") != "pkb":
            raise ValueError("pkb_only_compatibility_wrapper")
        return execute_pkb_request(request, pending)

    continuation = observation_continuation
    if continuation is None:
        continuation = continue_with_verified_observations_async

    async def continue_batch(session: dict, observations: list[dict], **kwargs):
        if observation_continuation is not None and len(observations) == 1:
            return await observation_continuation(
                session,
                observations[0],
                **kwargs,
            )
        return await continuation(session, observations, **kwargs)

    return await run_observation_loop(
        user_raw,
        member_specs=member_specs,
        timeout=timeout,
        create_task_record=create_task_record,
        execute_source_request=execute_source,
        record_source_read_record=record_pkb_read_record,
        persist_session_record=persist_session_record,
        fail_task_record=fail_task_record,
        stop_requested=stop_requested,
        on_turn_start=on_turn_start,
        task_id=task_id,
        dialogue_starter=dialogue_starter,
        observation_continuation=continue_batch,
        resource_catalog={
            "pkb": {"available": True},
            "web": {"available": False},
            "finance": {"available": False},
        },
        max_observation_cycles=1,
    )



async def resume_user_answer(
    task_id: UUID,
    user_text: str,
    *,
    timeout: float,
    claim_user_resume_record: Callable,
    persist_session_record: Callable,
    abort_user_resume_record: Callable,
    fail_task_record: Callable | None = None,
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
            sha256(text.encode("utf-8")).hexdigest(),
        )
        claimed = True
        updated = await user_continuation(
            session,
            text,
            timeout=timeout,
            stop_requested=stop_requested,
            on_turn_start=on_turn_start,
        )
        if (
            updated.get("status") == "stopped"
            and updated.get("next_step") != "user_requested_stop"
        ):
            raise ValueError("user_resume_re_evaluation_failed")
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
                    abort_user_resume_record,
                    task_id,
                    type(exc).__name__,
                )
            except Exception:
                pass
        raise



async def review_proposal(
    task_id: UUID,
    decision: str,
    *,
    timeout: float,
    claim_proposal_review_record: Callable,
    finalize_proposal_review_record: Callable,
    abort_proposal_review_record: Callable,
    memory_result: dict | None = None,
    stop_requested=None,
    on_turn_start=None,
    review_continuation=continue_with_proposal_review_async,
) -> dict:
    """Re-evaluate one explicit proposal-review choice, then let RITSUKO finalize."""
    claimed = False
    try:
        session, selected_capability, observation, _review = (
            await anyio.to_thread.run_sync(
                claim_proposal_review_record,
                task_id,
                decision,
                memory_result,
            )
        )
        claimed = True
        updated = await review_continuation(
            session,
            observation,
            timeout=timeout,
            stop_requested=stop_requested,
            on_turn_start=on_turn_start,
        )
        if updated.get("status") != "review_evaluated":
            raise ValueError("proposal_review_re_evaluation_failed")
        await anyio.to_thread.run_sync(
            finalize_proposal_review_record,
            task_id,
            updated,
            selected_capability,
        )
        return updated
    except Exception as exc:
        if claimed:
            try:
                await anyio.to_thread.run_sync(
                    abort_proposal_review_record,
                    task_id,
                    type(exc).__name__,
                )
            except Exception:
                pass
        raise
