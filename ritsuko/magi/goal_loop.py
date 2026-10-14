"""Opt-in, single-MAGI, public read-only goal evaluation.

The Task/Policy/Tool executor remains in RITSUKO. This module only asks MAGI
for a bounded decision and converts its proposal to a pending read/question.
"""
from __future__ import annotations

from copy import deepcopy
import re
from uuid import uuid4

from .async_execution import _call_panel_member_async
from .dialogue import _normalized_member_specs
from .goal_contract import VERSION, validate_goal_turn
from .runtime_context import create_runtime_context

# Fail closed for explicit requests to consult external sources; do not treat
# model-generated "not_required" as a substitute for a user-required Web read.
SOURCE_REQUEST = re.compile(
    r"(?i)(公式|出典|引用|資料|検索|調べて|調査|最新|今日|昨日|現在|最近|価格|私の|自分の|ウェブ|web|website|URL|ネット|サイト|source|reference)"
)
MAX_GOAL_TURNS = 6


def goal_requires_web(request: str) -> bool:
    return bool(SOURCE_REQUEST.search(request))


def goal_task_projection(session: dict) -> dict:
    """Translate one validated semantic proposal into RITSUKO Task state."""
    status = session.get("status")
    judgment = session.get("goal_judgment") or {}
    reason = str(session.get("next_step") or "goal_not_resolved")
    if status == "candidate_ready" and judgment.get("next") == "respond":
        evidence = judgment.get("evidence") or {}
        ids = evidence.get("result_ids") or []
        available = {
            str(o.get("result_id")): o
            for o in session.get("observations") or []
            if isinstance(o, dict) and o.get("verified") is True and o.get("result_id")
            and o.get("source") == "web"
        }
        valid_ref = (
            evidence.get("basis") == "result_refs" and bool(ids)
            and all(ref in available for ref in ids)
        )
        source_free = (
            evidence.get("basis") == "not_required"
            and not ids
            and not session.get("requires_web")
            and not session.get("tool_read_executed")
        )
        if judgment.get("fulfillment") == "met" and (valid_ref or source_free):
            answer = judgment.get("answer")
            if isinstance(answer, str) and answer.strip():
                # A provider citation is not independent page-content verification.
                disclaimer = ""
                if valid_ref and any(
                    available[ref].get("verification_level") == "provider_cited"
                    for ref in ids
                ):
                    disclaimer = "\\n\\n注: 出典は検索Providerが報告した引用であり、本文の独立確認は未実施です。"
                return {
                    "task_status": "completed", "phase": "completed",
                    "next_step": "respond", "message": answer.strip() + disclaimer,
                    "question": None, "reason": "goal_evaluated_with_recorded_evidence"
                    if valid_ref else "goal_evaluated_source_free",
                }
        reason = "goal_evidence_or_source_requirement_not_met"
        status = "stopped"
    if status == "waiting_user":
        return {
            "task_status": "waiting_external", "phase": "awaiting_clarification",
            "next_step": "ask_user", "message": None,
            "question": session.get("user_question"), "reason": reason,
        }
    if status == "waiting_information":
        return {
            "task_status": "waiting_external", "phase": "awaiting_information",
            "next_step": "review_information_requests",
            "message": str(session.get("status_message") or "") or None,
            "question": None, "reason": reason,
        }
    if status == "stopped" and reason == "user_requested_stop":
        return {
            "task_status": "paused", "phase": "paused",
            "next_step": "user_requested_stop", "message": None,
            "question": None, "reason": reason,
        }
    return {
        "task_status": "failed" if status == "stopped" else "running",
        "phase": "failed" if status == "stopped" else "orient",
        "next_step": reason, "message": session.get("status_message"),
        "question": None, "reason": reason,
    }


def _apply_judgment(session: dict, decision: dict) -> None:
    """RITSUKO checks proposal against its locally recorded observations."""
    session["goal_judgment"] = deepcopy(decision)
    session["detail"] = {"answer_candidate": decision.get("answer")}
    session["user_question"] = None
    session["pending_requests"] = []
    next_step = decision["next"]
    evidence = decision["evidence"]
    ids = evidence["result_ids"]
    trusted = {
        str(o["result_id"])
        for o in session.get("observations") or []
        if isinstance(o, dict) and o.get("source") == "web"
        and o.get("verified") is True and o.get("result_id")
    }
    if evidence["basis"] == "result_refs" and (
        not ids or any(ref not in trusted for ref in ids)
    ):
        session.update(status="stopped", next_step="unrecorded_task_result_reference")
        return
    if next_step == "respond":
        if (evidence["basis"] == "not_required" and
                (session["requires_web"] or session.get("tool_read_executed"))):
            session.update(status="stopped", next_step="required_source_not_observed")
            return
        if evidence["basis"] == "not_required" or ids:
            session.update(status="candidate_ready", next_step="review_goal_answer")
            return
        session.update(status="stopped", next_step="goal_evidence_missing")
        return
    if next_step == "request_read":
        request = decision["read_request"]
        query = request["query"].strip()
        if query in session["previous_queries"]:
            session.update(status="stopped", next_step="repeated_goal_query")
            return
        session["previous_queries"].append(query)
        session["pending_requests"] = [{
            "request_id": "REQ-" + session["task_id"][:8] + "-" + str(len(session["turns"])),
            "source": "web", "what": query, "reason": request["reason"],
        }]
        session.update(status="waiting_information", next_step="review_information_requests")
        return
    if next_step == "ask_user":
        session["user_question"] = decision["question"]
        session.update(status="waiting_user", next_step="ask_user_for_information")
        return
    if next_step == "wait":
        session["status_message"] = decision["reason"][:1000]
        session.update(status="waiting_information", next_step="goal_wait")
        return
    session["status_message"] = decision["reason"][:1000]
    session.update(status="stopped", next_step="goal_unable")


async def _run_goal_turn(session: dict, *, timeout: float, caller=None,
                         stop_requested=None, on_turn_start=None) -> dict:
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
        "resource_catalog": {"web": {"available": True, "access": "bounded_read_only"}},
        "question_from_ritsuko": "目標・必要な根拠・次の一手を判断してください",
    }
    spec = session["member_specs"][0]
    run = caller or _call_panel_member_async
    result = await run(spec, envelope, timeout=timeout)
    response = result.get("response")
    errors = list(result.get("errors") or [])
    if result.get("status") == "ok":
        errors += validate_goal_turn(response)
    status = "ok" if result.get("status") == "ok" and not errors else "invalid"
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
    _apply_judgment(session, response)
    return session


async def start_goal_dialogue(user_raw: str, *, member_specs: list[dict],
                              timeout: float, task_id: str | None = None,
                              stop_requested=None, on_turn_start=None,
                              caller=None, policy_snapshot: dict | None = None) -> dict:
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
        "requires_web": goal_requires_web(request),
        "goal": None, "goal_judgment": None, "observations": [],
        "detail": None, "turns": [], "pending_requests": [],
        "previous_queries": [], "user_replies": [], "tool_read_executed": False,
        "user_question": None, "status_message": None,
    }
    return await _run_goal_turn(session, timeout=timeout, caller=caller,
                                stop_requested=stop_requested, on_turn_start=on_turn_start)


async def continue_goal_observations(session: dict, observations: list[dict], *,
                                     timeout: float, stop_requested=None,
                                     on_turn_start=None, caller=None) -> dict:
    if session.get("control_mode") != VERSION or session.get("status") != "waiting_information":
        raise ValueError("goal_not_waiting_for_information")
    updated = deepcopy(session)
    if not observations:
        raise ValueError("goal_observations_required")
    for obs in observations:
        if (not isinstance(obs, dict) or obs.get("source") != "web"
                or not obs.get("result_id")):
            raise ValueError("goal_web_recorded_observation_required")
        updated["observations"].append(deepcopy(obs))
    updated["tool_read_executed"] = True
    updated["pending_requests"] = []
    updated.update(status="running", next_step="evaluate_observation")
    return await _run_goal_turn(updated, timeout=timeout, caller=caller,
                                stop_requested=stop_requested, on_turn_start=on_turn_start)


async def continue_goal_user(session: dict, user_text: str, *, timeout: float,
                             stop_requested=None, on_turn_start=None,
                             caller=None) -> dict:
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
                                stop_requested=stop_requested, on_turn_start=on_turn_start)
