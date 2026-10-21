# Provenance-Origin: original
# Provenance-AI: AI-generated
"""MAGI Goal judgment provider: one-to-three seats, no Task state writer.

The provider collects independently validated proposals and their diagnostics.
RITSUKO's common judgment application owns all Task state transitions.
"""
from __future__ import annotations

import asyncio
from copy import deepcopy

from ritsuko.magi.dialogue import select_weighted_consensus
from ritsuko.magi.goal_contract import VERSION, WRITE_OPERATION_ID, validate_goal_turn
from pkb.write_service import KNOWN_PREDICATES
from ritsuko.application.observation_sources import READ_OPERATIONS
from ritsuko.magi.protocol import default_resource_catalog


async def evaluate_goal(
    session: dict,
    *,
    timeout: float,
    caller=None,
    stop_requested=None,
    on_turn_start=None,
    on_member_progress=None,
    member_specs_override=None,
    context_policy=None,
) -> dict | None:
    """Return the chosen MAGI decision, never a completed Task."""
    from ritsuko.magi.async_execution import (
        _call_panel_member_async, _emit_member_progress,
        apply_context_policy_to_envelope,
    )

    if stop_requested is not None and stop_requested():
        session.update(status="stopped", next_step="user_requested_stop")
        return None
    if len(session["turns"]) >= session.get("turn_limit", 6):
        session.update(status="stopped", next_step="goal_turn_limit")
        return None
    number = len(session["turns"]) + 1
    if on_turn_start is not None:
        on_turn_start(number)

    catalog = default_resource_catalog()
    envelope = {
        "stage": "goal", "protocol_version": VERSION,
        "task_id": session["task_id"], "turn": number,
        "user_input": {"raw": session["user_raw"]},
        "runtime_context": deepcopy(session.get("runtime_context")),
        "goal": session.get("goal"),
        "observations": deepcopy((session.get("observations") or [])[-8:]),
        "previous_judgment": deepcopy(session.get("goal_judgment")),
        "user_replies": deepcopy(session.get("user_replies") or []),
        "resource_catalog": {
            **catalog,
            "web": {
                **catalog["web"],
                "capability": "provider_search_with_citations",
                "direct_page_content_fetch": False,
            },
        },
        # Advertise only operations actually handled by the common Source runner.
        # PKB writes will be added only after the structured write port exists.
        "operation_catalog": {
            operation_id: {"kind": "read", "source": source, "arguments": ["query"]}
            for operation_id, source in READ_OPERATIONS.items()
            if catalog.get(source, {}).get("available")
        } | ({
            WRITE_OPERATION_ID: {
                "kind": "write", "source": "pkb_write",
                "arguments": [
                    "entity_id", "entity_mention", "predicate", "value",
                    "evidence_start", "evidence_end",
                ],
                "predicates": sorted(KNOWN_PREDICATES),
                "note": "Use only PKB-verified existing IDs from observations; search first. For model/manufacturer, quote an explicit labeled value such as 機種は or メーカーは. Tentative user claims such as かもしれない/未確認/不明 are NOT confirmed facts: choose ask_user with a clarification question, never pkb.register_claim even if the Entity ID and value are known. Do not guess.",
            },
        } if session.get("pkb_claim_write_enabled") is True else {}),
        "question_from_ritsuko": "目標・必要な根拠・次の一手を判断してください",
    }
    envelope = apply_context_policy_to_envelope(envelope, context_policy)
    specs = (
        member_specs_override
        if member_specs_override is not None else session["member_specs"]
    )
    enabled = [spec for spec in specs if spec.get("enabled")]
    if not enabled:
        session.update(status="stopped", next_step="goal_model_not_permitted")
        return None

    async def evaluate_one(spec: dict) -> dict:
        progress = {
            "task_id": session["task_id"], "turn": number,
            "member": spec["name"], "provider": spec["provider"],
            "model": spec["model"],
        }
        _emit_member_progress(on_member_progress, {**progress, "state": "queued"})
        _emit_member_progress(on_member_progress, {**progress, "state": "running"})
        run = caller or _call_panel_member_async
        try:
            result = await run(spec, envelope, timeout=timeout)
            response = result.get("response")
            errors = list(result.get("errors") or [])
            if result.get("status") == "ok":
                errors.extend(validate_goal_turn(response))
            status = "ok" if result.get("status") == "ok" and not errors else (
                str(result.get("status") or "invalid")
                if result.get("status") not in (None, "ok") else "invalid"
            )
        except Exception as exc:
            response, errors, status, result = (
                None, [type(exc).__name__], "unavailable", {},
            )
        _emit_member_progress(on_member_progress, {
            **progress,
            "state": "completed" if status == "ok" else status,
            "elapsed_seconds": result.get("elapsed_seconds"),
            "validated_summary": str(response.get("reason") or "")
            if status == "ok" and isinstance(response, dict) else "",
            "error_kind": str(errors[0]) if errors else "",
        })
        return {
            **deepcopy(result), "name": spec["name"],
            "provider": spec["provider"], "model": spec["model"],
            "weight": spec.get("weight", 1.0), "status": status,
            "response": deepcopy(response), "errors": errors,
        }

    # This parallel panel only selects a proposal. No individual member runs
    # Sources or mutates the Task/Action/Result persistence state.
    results = await asyncio.gather(*(evaluate_one(spec) for spec in enabled))
    consensus = select_weighted_consensus("goal", results)
    status = consensus["status"]
    decision = consensus.get("response")
    session["turns"].append({
        "stage": "goal", "question_purpose": "evaluate_goal",
        "request_envelope": envelope, "status": status,
        "response": deepcopy(decision),
        "errors": [] if status == "ok" else [str(consensus.get("reason") or status)],
        "member_results": results,
        "diagnostic": {
            "mode": "goal_llm_panel",
            "enabled_members": [member["name"] for member in enabled],
        },
        "consensus": deepcopy(consensus),
    })
    if stop_requested is not None and stop_requested():
        session.update(status="stopped", next_step="user_requested_stop")
        return None
    if status != "ok":
        if status == "disagreement":
            session["magi_disagreement"] = deepcopy(consensus)
            session["user_question"] = (
                "MAGIの評価が分かれました。期待する結果や条件をもう少し詳しく教えてください。"
            )
            session.update(status="waiting_user", next_step="goal_disagreement")
        else:
            session.update(status="stopped", next_step="goal_member_invalid")
        return None
    session["magi_disagreement"] = None
    return decision
