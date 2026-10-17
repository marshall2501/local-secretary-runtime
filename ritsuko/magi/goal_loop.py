"""LLM Goal evaluation for one or more configured MAGI members.

The Task/Policy/Tool executor remains in RITSUKO. This module only asks MAGI
for a bounded decision and converts its proposal to a pending read/question.
"""
from __future__ import annotations

from copy import deepcopy
import asyncio
import re
from uuid import uuid4

from .dialogue import _normalized_member_specs
from .goal_contract import VERSION, validate_goal_turn
from .runtime_context import create_runtime_context
from .protocol import default_resource_catalog
from ritsuko.application.observation_sources import AUTO_READ_SOURCES
from ritsuko.core.goal_evidence import check_goal_response_evidence

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
    from ritsuko.core.task_projection import task_projection

    return task_projection(session)

async def _apply_judgment(session: dict, decision: dict, *, timeout: float) -> None:
    """Historical entry: pass Goal proposal to the one RITSUKO state applier.

    This compatibility API is used by older isolated callers. It never
    invokes the former stage-based Task executor.
    """
    from ritsuko.core.judgment_application import apply_judgment
    await apply_judgment(
        session, decision, strategy="llm", purpose="evaluate_goal",
    )


async def _run_goal_turn(session: dict, *, timeout: float, caller=None,
                         stop_requested=None, on_turn_start=None,
                         on_member_progress=None,
                         member_specs_override=None, context_policy=None) -> dict:
    """Compatibility wrapper; proposal collection and Task state are separate."""
    from .goal_decision import evaluate_goal
    from ritsuko.core.judgment_application import apply_judgment

    decision = await evaluate_goal(
        session, timeout=timeout, caller=caller,
        stop_requested=stop_requested, on_turn_start=on_turn_start,
        on_member_progress=on_member_progress,
        member_specs_override=member_specs_override,
        context_policy=context_policy,
    )
    if decision is not None:
        await apply_judgment(
            session, decision, strategy="llm", purpose="evaluate_goal",
            stop_requested=stop_requested,
        )
    return session


async def start_goal_dialogue(user_raw: str, *, member_specs: list[dict],
                              timeout: float, task_id: str | None = None,
                              stop_requested=None, on_turn_start=None,
                              caller=None, policy_snapshot: dict | None = None,
                              on_member_progress=None) -> dict:
    """Compatibility entry using the same multi-MAGI judgment as new Tasks."""
    specs = [s for s in _normalized_member_specs(member_specs, "") if s["enabled"]]
    if not specs:
        raise ValueError("no_enabled_magi_member")
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
