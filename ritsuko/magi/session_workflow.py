# Provenance-Origin: original
# Provenance-AI: AI-generated
"""MAGI Task Session workflow, independent of concrete LLM transport.

RITSUKO owns task execution, state application and Result verification.
This module prepares the persistent MAGI session and resumes judgments after
Source observations, user answers and review receipts. The only MAGI-specific
branches are the two interchangeable judgment Strategies.
"""
from __future__ import annotations

from copy import deepcopy
from . import async_execution as transport
from .async_execution import call_guided_panel_async
from .dialogue import (
    MAX_TURNS, PROMPT_VERSION, _compose_question, _normalized_member_specs,
    _request_signatures, _turn_limit, _extend_turn_limit_for_user_resume,
)
from .judgment_strategies import strategy_for_name, strategy_for_session
from .settings import DEFAULT_TIMEOUT_SECONDS
from .runtime_context import create_runtime_context
from ritsuko.application.context_policy import restrict_members as restrict_members_by_profile


async def _advance_by_strategy(
    session: dict, *, timeout: float, caller=call_guided_panel_async,
    stop_requested=None, on_turn_start=None, on_member_progress=None,
    goal_caller=None, member_specs_override=None, context_policy=None,
) -> dict:
    """永続Sessionの判断Adapterだけを選び、共通Task実行処理へ結果を返す。"""
    return await strategy_for_session(session).advance(
        session, timeout=timeout, caller=caller,
        goal_caller=goal_caller,
        stop_requested=stop_requested, on_turn_start=on_turn_start,
        on_member_progress=on_member_progress,
        member_specs_override=member_specs_override,
        context_policy=context_policy,
    )


async def start_dialogue_async(
    user_raw: str,
    *,
    model: str = "",
    member_specs: list[dict] | None = None,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    caller=call_guided_panel_async,
    stop_requested=None,
    on_turn_start=None,
    task_id: str | None = None,
    task_timezone: str = "Asia/Tokyo",
    task_locale: str = "ja-JP",
    policy_snapshot: dict | None = None,
    decision_strategy: str = "standard",
    on_member_progress=None,
    goal_caller=None,
) -> dict:
    strategy = strategy_for_name(decision_strategy)
    specs = _normalized_member_specs(member_specs, model)
    from uuid import uuid4

    session = {
        "task_id": str(task_id or uuid4()),
        "user_raw": user_raw.strip(),
        "model": model,
        "prompt_version": PROMPT_VERSION,
        "runtime_context": create_runtime_context(timezone_name=task_timezone, locale=task_locale),
        "magi_mode": "weighted_panel_async",
        "decision_strategy": decision_strategy,
        "member_specs": specs,
        "status": "running",
        "next_step": "classify",
        "classification": None,
        "detail": None,
        "observations": [],
        "pending_requests": [],
        "previous_request_signatures": [],
        "conversation_context": [],
        "user_question": None,
        "magi_disagreement": None,
        "user_source_reviewed": False,
        "last_question_purpose": None,
        "turns": [],
        "turn_limit": MAX_TURNS,
        "legacy_router_used": False,
        "tool_read_executed": False,
        "policy_snapshot": deepcopy(policy_snapshot) if policy_snapshot else None,
    }
    if not session["user_raw"]:
        session.update(status="stopped", next_step="invalid_input")
        return session
    if not [spec for spec in specs if spec["enabled"]]:
        if strategy.name == "llm":
            raise ValueError("no_enabled_magi_member")
        session.update(status="stopped", next_step="no_magi_member")
        return session

    strategy.initialize(session)
    return await strategy.start(
        session, timeout=timeout, caller=caller, goal_caller=goal_caller,
        stop_requested=stop_requested, on_turn_start=on_turn_start,
        on_member_progress=on_member_progress,
    )

async def continue_with_observation_async(
    session: dict,
    observation_text: str,
    *,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    caller=call_guided_panel_async,
    stop_requested=None,
    on_turn_start=None,
) -> dict:
    updated = deepcopy(session)
    if updated.get("status") != "waiting_information":
        raise ValueError("not_waiting_for_information")
    if not isinstance(observation_text, str) or not observation_text.strip():
        raise ValueError("empty_observation")
    if len(updated["turns"]) >= _turn_limit(updated):
        updated.update(status="stopped", next_step="max_turns_reached")
        return updated

    updated["observations"].append({
        "source": "manual_test_input",
        "verified": False,
        "text": observation_text.strip()[:4000],
        "responds_to": [x["request_id"] for x in updated["pending_requests"]],
    })
    updated["pending_requests"] = []
    updated["status"] = "running"
    updated["next_step"] = "evaluate_observation"
    return await _advance_by_strategy(
        updated,
        timeout=timeout,
        caller=caller,
        stop_requested=stop_requested,
        on_turn_start=on_turn_start,
    )


def _private_observation_local_scope(
    session: dict,
) -> tuple[list[dict] | None, dict | None]:
    """Apply the Task's recorded profile; do not revive obsolete local-only rules."""
    snapshot = session.get("policy_snapshot")
    if snapshot is not None:
        return restrict_members_by_profile(session, snapshot)
    if any(
        isinstance(item, dict)
        and item.get("verified") is True
        and item.get("confidentiality") in {"private", "sensitive"}
        for item in session.get("observations") or []
    ):
        raise ValueError("task_policy_snapshot_required")
    return None, None


def _private_pkb_local_scope(session: dict):
    """Compatibility entry point for current callers; same recorded-policy contract."""
    return _private_observation_local_scope(session)


async def continue_with_verified_observations_async(
    session: dict,
    observations: list[dict],
    *,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    caller=call_guided_panel_async,
    stop_requested=None,
    on_turn_start=None,
    on_member_progress=None,
    goal_caller=None,
) -> dict:
    """Continue from one bounded batch of RITSUKO-verified read Observations."""
    updated = deepcopy(session)
    if updated.get("status") != "waiting_information":
        raise ValueError("not_waiting_for_information")
    if not isinstance(observations, list) or not observations:
        raise ValueError("verified_observations_required")
    judgment = strategy_for_session(updated)
    judgment.prepare_resume(updated)
    if len(updated["turns"]) >= _turn_limit(updated):
        updated.update(status="stopped", next_step="max_turns_reached")
        return updated

    safe_observations = []
    resolved_ids: set[str] = set()
    for observation in observations[:12]:
        if not isinstance(observation, dict):
            raise ValueError("invalid_observation")
        # A failed read is a recorded failure, not a verified Source.
        # Tool失敗は検証済み根拠でなく、Goal判断だけが記録済み失敗を再評価する。
        failed_goal_read = (
            judgment.allows_recorded_read_failure
            and observation.get("source") in {"pkb", "web", "finance"}
            and observation.get("verified") is False
            and bool(observation.get("result_id"))
            and bool(observation.get("action_id"))
        )
        if (
            observation.get("source") not in {"pkb", "web", "finance"}
            or (observation.get("verified") is not True and not failed_goal_read)
        ):
            raise ValueError("verified_read_observation_required")
        text_value = str(observation.get("text") or "").strip()
        if not text_value:
            raise ValueError("empty_observation")
        safe = deepcopy(observation)
        safe["text"] = text_value[:4000]
        if isinstance(safe.get("evidence_preview"), list):
            safe["evidence_preview"] = safe["evidence_preview"][:12]
        safe["responds_to"] = [
            str(value)
            for value in (safe.get("responds_to") or [])
            if str(value).strip()
        ][:20]
        resolved_ids.update(safe["responds_to"])
        safe_observations.append(safe)

    updated["observations"].extend(safe_observations)
    updated["pending_requests"] = [
        item
        for item in (updated.get("pending_requests") or [])
        if not (
            isinstance(item, dict)
            and str(item.get("request_id") or "") in resolved_ids
        )
    ]
    updated["tool_read_executed"] = True
    updated["status"] = "running"
    updated["next_step"] = "evaluate_observation"

    local_specs, context_policy = _private_observation_local_scope(updated)
    if local_specs == []:
        return updated
    return await _advance_by_strategy(
        updated, timeout=timeout, caller=caller,
        stop_requested=stop_requested, on_turn_start=on_turn_start,
        on_member_progress=on_member_progress, goal_caller=goal_caller,
        member_specs_override=local_specs, context_policy=context_policy,
    )


async def continue_with_verified_observation_async(
    session: dict,
    observation: dict,
    **kwargs,
) -> dict:
    """Compatibility wrapper for the former single-PKB Observation API."""
    return await continue_with_verified_observations_async(
        session,
        [observation],
        **kwargs,
    )


async def continue_with_proposal_review_async(
    session: dict,
    observation: dict,
    *,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    caller=call_guided_panel_async,
    stop_requested=None,
    on_turn_start=None,
    on_member_progress=None,
) -> dict:
    """Re-evaluate a user-approved proposal review before RITSUKO finalizes."""
    updated = deepcopy(session)
    if not isinstance(observation, dict):
        raise ValueError("invalid_review_observation")
    # Knowledge proposal and plain READY answer use the same user-approved
    # review/Task finalization pipeline. Plain READY never authorizes PKB write.
    if updated.get("status") not in {"proposal_ready", "candidate_ready"}:
        raise ValueError("not_waiting_for_proposal_review")
    if updated.get("status") == "candidate_ready":
        detail = updated.get("detail") or {}
        if (detail.get("state") != "READY"
                or not str(detail.get("answer_candidate") or "").strip()
                or observation.get("source") != "proposal_review"
                or observation.get("review_decision") != "answer_only"):
            raise ValueError("answer_candidate_review_not_permitted")
    if observation.get("source") not in {"proposal_review", "memory_intake"}:
        raise ValueError("invalid_review_observation_source")
    if observation.get("verified") is not True:
        raise ValueError("verified_review_observation_required")
    text = str(observation.get("text") or "").strip()
    if not text:
        raise ValueError("empty_review_observation")

    safe_observation = deepcopy(observation)
    safe_observation["text"] = text[:4000]
    safe_observation["responds_to"] = [
        str(value) for value in (safe_observation.get("responds_to") or [])
    ][:20]
    memory_summary = safe_observation.get("memory_intake")
    if isinstance(memory_summary, dict):
        memory_summary["receipts"] = [
            deepcopy(item)
            for item in (memory_summary.get("receipts") or [])
            if isinstance(item, dict)
        ][:100]

    _extend_turn_limit_for_user_resume(updated)
    updated["observations"].append(safe_observation)
    updated["status"] = "running"
    updated["next_step"] = "evaluate_review_result"

    local_specs, context_policy = _private_observation_local_scope(updated)
    if local_specs == []:
        return updated

    prompt = _compose_question(updated, "evaluate_review_result")
    response = await transport._send_async(
        updated,
        "analyze",
        "evaluate_review_result",
        prompt,
        timeout=timeout,
        caller=caller,
        stop_requested=stop_requested,
        on_turn_start=on_turn_start,
        on_member_progress=on_member_progress,
        member_specs_override=local_specs,
        context_policy=context_policy,
    )
    if response is None:
        return updated

    updated["post_review_evaluation"] = deepcopy(response)
    updated.update(
        status="review_evaluated",
        next_step="ritsuko_finalize_review",
    )
    return updated


async def continue_with_user_clarification_async(
    session: dict,
    user_text: str,
    *,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    caller=call_guided_panel_async,
    stop_requested=None,
    on_turn_start=None,
    on_member_progress=None,
    goal_caller=None,
) -> dict:
    updated = deepcopy(session)
    if updated.get("status") != "waiting_user":
        raise ValueError("not_waiting_for_user")
    if not isinstance(user_text, str) or not user_text.strip():
        raise ValueError("empty_user_clarification")

    text = user_text.strip()[:4000]
    updated.setdefault("conversation_context", []).append({
        "role": "user",
        "text": text,
    })
    updated["conversation_context"] = updated["conversation_context"][-4:]
    updated["user_question"] = None
    updated["magi_disagreement"] = None
    updated["status"] = "running"
    _extend_turn_limit_for_user_resume(updated)

    local_specs, context_policy = _private_observation_local_scope(updated)
    if local_specs == []:
        return updated

    return await strategy_for_session(updated).resume_user(
        updated, text, timeout=timeout, caller=caller,
        goal_caller=goal_caller,
        stop_requested=stop_requested, on_turn_start=on_turn_start,
        on_member_progress=on_member_progress,
        member_specs_override=local_specs, context_policy=context_policy,
    )
