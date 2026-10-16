"""Opt-in, single-MAGI, public read-only goal evaluation.

The Task/Policy/Tool executor remains in RITSUKO. This module only asks MAGI
for a bounded decision and converts its proposal to a pending read/question.
"""
from __future__ import annotations

from copy import deepcopy
import re
from uuid import uuid4

from .async_execution import (
    _apply_detail_async, _call_panel_member_async, _emit_member_progress,
    apply_context_policy_to_envelope,
)
from .dialogue import _normalized_member_specs
from .goal_contract import VERSION, validate_goal_turn
from .runtime_context import create_runtime_context
from .protocol import default_resource_catalog
from ritsuko.application.observation_sources import AUTO_READ_SOURCES

# 「私の」「調査」などをWeb限定と取り違えない。暫定の明示要求
# GateでありSource選択と根拠充足の意味判断はMAGIが行う。
SOURCE_REQUEST = re.compile(
    r"(?i)(公式|出典|引用|資料|検索|調べて|調査|最新|今日|昨日|現在|最近|価格|私の|自分の|ウェブ|web|website|URL|ネット|サイト|source|reference)"
)
WEB_REQUEST = re.compile(
    r"(?i)(公式(?:サイト|Web|ページ|資料)?|最新(?:情報|ニュース)?|ウェブ|web|website|URL|ネット|サイト|オンライン)"
)
MAX_GOAL_TURNS = 6


def goal_requires_source(request: str) -> bool:
    """本人が根拠を求めている場合、出典不要回答として即完了させない。"""
    return bool(SOURCE_REQUEST.search(request))


def goal_requires_web(request: str) -> bool:
    """明示的な公開Web根拠要求だけをWeb制約として扱う。"""
    return bool(WEB_REQUEST.search(request))


def goal_task_projection(session: dict) -> dict:
    """保存済みGoal Task互換入口。確定判断はRITSUKOの共通Task投影が行う。"""
    from ritsuko.core.magi_bridge import task_projection

    return task_projection(session)

async def _apply_judgment(session: dict, decision: dict, *, timeout: float) -> None:
    """Adapt an LLM decision to the existing MAGI flow/state handler.

    The model evaluates Goal fulfillment; RITSUKO still checks recorded Result
    IDs and applies the same downstream state decisions used by standard MAGI.
    """
    session["goal_judgment"] = deepcopy(decision)
    session["user_question"] = None
    session["pending_requests"] = []
    next_step = decision["next"]
    evidence = decision["evidence"]
    ids = evidence["result_ids"]
    recorded = {
        str(o["result_id"]): o
        for o in session.get("observations") or []
        if isinstance(o, dict) and o.get("source") in AUTO_READ_SOURCES
        and o.get("result_id")
    }
    if (any(ref not in recorded for ref in ids)
            or (evidence["basis"] == "result_refs"
                and (not ids or any(not recorded[ref].get("verified")
                                    for ref in ids)))):
        session.update(status="stopped", next_step="unrecorded_task_result_reference")
        return
    if next_step == "respond":
        if (evidence["basis"] == "not_required"
                and (session.get("requires_source", session.get("requires_web", False))
                     or session.get("requires_web") or session.get("tool_read_executed"))):
            session.update(status="stopped", next_step="required_source_not_observed")
            return
        if evidence["basis"] != "not_required" and not ids:
            session.update(status="stopped", next_step="goal_evidence_missing")
            return
        state = "READY"
        information_requests = []
    elif next_step == "request_read":
        request = decision["read_request"]
        query = request["query"].strip()
        state = "NEED_INFORMATION"
        information_requests = [{
            "source": request["source"], "what": query, "reason": request["reason"],
        }]
    elif next_step == "ask_user":
        state = "NEED_CLARIFICATION"
        information_requests = []
    elif next_step == "wait":
        state = "NEED_INFORMATION"
        information_requests = []
        session["status_message"] = decision["reason"][:1000]
    else:
        state = "UNABLE"
        information_requests = []
        session["status_message"] = decision["reason"][:1000]

    # This is the same state application function used by the original mode.
    # No second mapping of request/user/candidate states is maintained here.
    detail = {
        "state": state,
        "understood_request": decision["goal"],
        "reason": decision["reason"],
        "information_requests": information_requests,
        "question_for_user": decision.get("question"),
        "answer_candidate": decision.get("answer"),
        "knowledge_candidate": None,
        "action_candidate": None,
    }
    await _apply_detail_async(
        session, detail, timeout=timeout, purpose="review_or_repair",
    )


async def _run_goal_turn(session: dict, *, timeout: float, caller=None,
                         stop_requested=None, on_turn_start=None,
                         on_member_progress=None,
                         member_specs_override=None, context_policy=None) -> dict:
    if stop_requested is not None and stop_requested():
        session.update(status="stopped", next_step="user_requested_stop")
        return session
    if len(session["turns"]) >= MAX_GOAL_TURNS:
        session.update(status="stopped", next_step="goal_turn_limit")
        return session
    number = len(session["turns"]) + 1
    if on_turn_start:
        on_turn_start(number)
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
            **default_resource_catalog(),
            "web": {
                **default_resource_catalog()["web"],
                "capability": "provider_search_with_citations",
                "direct_page_content_fetch": False,
            },
        },
        "question_from_ritsuko": "目標・必要な根拠・次の一手を判断してください",
    }
    envelope = apply_context_policy_to_envelope(envelope, context_policy)
    specs = (member_specs_override if member_specs_override is not None
             else session["member_specs"])
    enabled = [spec for spec in specs if spec.get("enabled")]
    if len(enabled) != 1:
        session.update(status="stopped", next_step="goal_model_not_permitted")
        return session
    spec = enabled[0]
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
    except Exception as exc:
        _emit_member_progress(on_member_progress, {
            **progress, "state": "unavailable",
            "error_kind": type(exc).__name__,
        })
        raise
    response = result.get("response")
    errors = list(result.get("errors") or [])
    if result.get("status") == "ok":
        errors += validate_goal_turn(response)
    status = "ok" if result.get("status") == "ok" and not errors else "invalid"
    _emit_member_progress(on_member_progress, {
        **progress, "state": "completed" if status == "ok" else (
            str(result.get("status") or "invalid")
            if result.get("status") not in (None, "ok") else "invalid"
        ),
        "elapsed_seconds": result.get("elapsed_seconds"),
        "validated_summary": str(response.get("reason") or "")
        if status == "ok" and isinstance(response, dict) else "",
        "error_kind": str(errors[0]) if errors else "",
    })
    session["turns"].append({
        "stage": "goal", "question_purpose": "evaluate_goal",
        "request_envelope": envelope, "status": status,
        "response": deepcopy(response), "errors": errors,
        "member_results": [deepcopy(result)], "diagnostic": deepcopy(result.get("diagnostic") or {}),
    })
    if stop_requested is not None and stop_requested():
        session.update(status="stopped", next_step="user_requested_stop")
        return session
    if status != "ok":
        session.update(status="stopped", next_step="goal_member_invalid")
        return session
    session["goal"] = response["goal"]
    await _apply_judgment(session, response, timeout=timeout)
    return session


async def start_goal_dialogue(user_raw: str, *, member_specs: list[dict],
                              timeout: float, task_id: str | None = None,
                              stop_requested=None, on_turn_start=None,
                              caller=None, policy_snapshot: dict | None = None,
                              on_member_progress=None) -> dict:
    """Explicit goal_loop_v1 only. One enabled member avoids implicit consensus."""
    specs = [s for s in _normalized_member_specs(member_specs, "") if s["enabled"]]
    if len(specs) != 1:
        raise ValueError("goal_mode_requires_one_enabled_member")
    request = str(user_raw or "").strip()
    if not request:
        raise ValueError("empty_request")
    session = {
        "task_id": str(task_id or uuid4()), "user_raw": request,
        "policy_snapshot": deepcopy(policy_snapshot),
        "control_mode": VERSION, "member_specs": specs,
        "runtime_context": create_runtime_context(timezone_name="Asia/Tokyo", locale="ja-JP"),
        "status": "running", "next_step": "evaluate_goal",
        "requires_source": goal_requires_source(request),
        "requires_web": goal_requires_web(request),
        "goal": None, "goal_judgment": None, "observations": [],
        "detail": None, "turns": [], "pending_requests": [],
        "previous_queries": [], "previous_request_signatures": [],
        "turn_limit": MAX_GOAL_TURNS, "user_replies": [], "tool_read_executed": False,
        "user_question": None, "status_message": None,
    }
    return await _run_goal_turn(session, timeout=timeout, caller=caller,
                                stop_requested=stop_requested, on_turn_start=on_turn_start,
                                on_member_progress=on_member_progress)


async def continue_goal_observations(session: dict, observations: list[dict], *,
                                     timeout: float, stop_requested=None,
                                     on_turn_start=None, caller=None,
                                     on_member_progress=None) -> dict:
    if session.get("control_mode") != VERSION or session.get("status") != "waiting_information":
        raise ValueError("goal_not_waiting_for_information")
    updated = deepcopy(session)
    if not observations:
        raise ValueError("goal_observations_required")
    for obs in observations:
        if (not isinstance(obs, dict)
                or obs.get("source") not in AUTO_READ_SOURCES
                or not obs.get("result_id")):
            raise ValueError("goal_recorded_observation_required")
        updated["observations"].append(deepcopy(obs))
    updated["tool_read_executed"] = True
    updated["pending_requests"] = []
    updated.update(status="running", next_step="evaluate_observation")
    return await _run_goal_turn(updated, timeout=timeout, caller=caller,
                                stop_requested=stop_requested, on_turn_start=on_turn_start,
                                on_member_progress=on_member_progress)


async def continue_goal_user(session: dict, user_text: str, *, timeout: float,
                             stop_requested=None, on_turn_start=None,
                             caller=None, on_member_progress=None) -> dict:
    """Resume a previously saved public Goal Task with one clarifying answer."""
    if session.get("control_mode") != VERSION or session.get("status") != "waiting_user":
        raise ValueError("goal_not_waiting_for_user")
    reply = str(user_text or "").strip()
    if not reply:
        raise ValueError("empty_goal_clarification")
    updated = deepcopy(session)
    updated.setdefault("user_replies", []).append(reply[:4000])
    updated["user_replies"] = updated["user_replies"][-3:]
    updated["user_question"] = None
    updated.update(status="running", next_step="evaluate_user_clarification")
    return await _run_goal_turn(updated, timeout=timeout, caller=caller,
                                stop_requested=stop_requested, on_turn_start=on_turn_start,
                                on_member_progress=on_member_progress)
