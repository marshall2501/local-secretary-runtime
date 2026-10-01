"""Bridge the state-driven MAGI dialogue to one bounded real PKB read.

This module contains only control/shape decisions. Concrete DB reads and Task
persistence stay in the caller so the bridge remains testable and replaceable.
"""
from __future__ import annotations

from copy import deepcopy

CORE_SLICE = "ritsuko_magi_observation_v1"


def pending_pkb_request(session: dict) -> dict | None:
    """Return one bounded PKB request when the current MAGI state permits it."""
    if session.get("status") != "waiting_information":
        return None
    pending = [
        item for item in (session.get("pending_requests") or [])
        if isinstance(item, dict)
    ]
    if not pending or any(item.get("source") != "pkb" for item in pending):
        return None
    return {
        "source": "pkb",
        "request_ids": [
            str(item.get("request_id"))
            for item in pending if item.get("request_id")
        ],
        "what": " / ".join(
            str(item.get("what") or "").strip()
            for item in pending if str(item.get("what") or "").strip()
        )[:4000],
        "reasons": [
            str(item.get("reason") or "").strip()[:1000]
            for item in pending if str(item.get("reason") or "").strip()
        ],
    }


def verified_pkb_observation(execution: dict, pending_request: dict) -> dict:
    """Build a bounded private Observation from a deterministic PKB read."""
    result = deepcopy(execution.get("result") or {})
    evidence_preview = []
    if isinstance(result.get("current"), list):
        evidence_preview.extend(result["current"][:12])
    if isinstance(result.get("items"), list):
        evidence_preview.extend(result["items"][:12])
    return {
        "source": "pkb",
        "verified": True,
        "confidentiality": "private",
        "text": str(execution.get("answer") or "").strip()[:4000],
        "responds_to": list(pending_request.get("request_ids") or []),
        "capability": "pkb_search",
        "result_kind": result.get("result_kind"),
        "result_count": int(execution.get("total") or 0),
        "evidence_preview": evidence_preview[:12],
    }


def task_projection(session: dict) -> dict:
    """Map one dialogue state to the deterministic Task state RITSUKO owns."""
    detail = session.get("detail") or {}
    status = session.get("status")
    answer = detail.get("answer_candidate")

    if status == "candidate_ready" and isinstance(answer, str) and answer.strip():
        return {
            "task_status": "completed",
            "phase": "completed",
            "next_step": "respond",
            "message": answer.strip(),
            "question": None,
            "reason": "validated_answer_candidate_after_verified_observation",
        }

    if status == "waiting_user":
        return {
            "task_status": "waiting_external",
            "phase": "awaiting_clarification",
            "next_step": "ask_user",
            "message": None,
            "question": session.get("user_question"),
            "reason": session.get("next_step"),
        }

    if status == "waiting_information":
        return {
            "task_status": "waiting_external",
            "phase": "awaiting_information",
            "next_step": "review_information_requests",
            "message": None,
            "question": None,
            "reason": session.get("next_step"),
        }

    if status == "proposal_ready":
        return {
            "task_status": "waiting_external",
            "phase": "awaiting_review",
            "next_step": "review_proposal",
            "message": None,
            "question": None,
            "reason": session.get("next_step"),
        }

    return {
        "task_status": "failed" if status == "stopped" else "running",
        "phase": "failed" if status == "stopped" else "orient",
        "next_step": session.get("next_step"),
        "message": None,
        "question": session.get("user_question"),
        "reason": session.get("next_step"),
    }
