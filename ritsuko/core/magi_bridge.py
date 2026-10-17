"""Bridge the state-driven MAGI dialogue to one bounded real PKB read.

This module contains only control/shape decisions. Concrete DB reads and Task
persistence stay in the caller so the bridge remains testable and replaceable.
"""
from __future__ import annotations

from copy import deepcopy

from ritsuko.application.observation_sources import (
    pending_source_requests,
    verified_source_observation,
)

CORE_SLICE = "ritsuko_magi_observation_v1"


def pending_pkb_request(session: dict) -> dict | None:
    """Backward-compatible wrapper for the former PKB-only bridge."""
    pending = [
        item for item in (session.get("pending_requests") or [])
        if isinstance(item, dict)
    ]
    if (
        session.get("status") != "waiting_information"
        or not pending
        or any(item.get("source") != "pkb" for item in pending)
    ):
        return None
    groups, _ = pending_source_requests(
        session,
        resource_catalog={"pkb": {"available": True}},
    )
    if len(groups) != 1 or groups[0].get("source") != "pkb":
        return None
    return groups[0]


def verified_pkb_observation(execution: dict, pending_request: dict) -> dict:
    """Backward-compatible wrapper for PKB Observation construction."""
    request = {"source": "pkb", **dict(pending_request)}
    observation = verified_source_observation(execution, request)
    if observation is None:
        raise ValueError("verified_pkb_observation_required")
    return observation


def reviewable_user_knowledge_proposal(session: dict) -> dict | None:
    """Return a deterministic review payload for a user-grounded knowledge proposal."""
    if session.get("status") != "proposal_ready":
        return None
    detail = session.get("detail") or {}
    if detail.get("state") != "KNOWLEDGE_CANDIDATE":
        return None
    if session.get("tool_read_executed") is not True:
        return None
    if not any(
        isinstance(item, dict)
        and item.get("source") == "pkb"
        and item.get("verified") is True
        for item in (session.get("observations") or [])
    ):
        return None

    user_observations = [
        item for item in (session.get("observations") or [])
        if isinstance(item, dict)
        and item.get("source") == "user_clarification"
        and str(item.get("text") or "").strip()
        and list(item.get("responds_to") or [])
    ]
    if not user_observations:
        return None
    latest = user_observations[-1]
    literal = str(latest.get("text") or "").strip()
    explicit_answer = str(detail.get("answer_candidate") or "").strip()
    knowledge = str(detail.get("knowledge_candidate") or "").strip()
    if not knowledge:
        return None
    folded = literal.casefold()
    if folded not in knowledge.casefold():
        return None
    if explicit_answer:
        if folded not in explicit_answer.casefold():
            return None
        answer = explicit_answer
        answer_source = "answer_candidate"
    else:
        # KNOWLEDGE_CANDIDATE legitimately permits answer_candidate=null.
        # The user-grounded knowledge candidate is a deterministic fallback so
        # proposal review can still present and return the exact accepted fact.
        answer = knowledge
        answer_source = "knowledge_candidate_fallback"
    return {
        "answer": answer,
        "answer_source": answer_source,
        "knowledge_candidate": knowledge,
        "user_text": literal,
        "responds_to": [str(x) for x in (latest.get("responds_to") or [])][:20],
    }


def reviewable_answer_candidate(session: dict) -> dict | None:
    """Offer a saved standard READY answer for explicit answer-only review.

    This is not a knowledge-memory proposal: no PKB write is implied.
    Candidate readiness is not itself Task completion; the owner must accept it.
    """
    if session.get("status") != "candidate_ready":
        return None
    detail = session.get("detail") or {}
    if detail.get("state") != "READY":
        return None
    if session.get("pending_requests"):
        return None
    answer = str(detail.get("answer_candidate") or "").strip()
    if not answer:
        return None
    return {
        "answer": answer,
        "answer_source": "answer_candidate",
        "knowledge_candidate": None,
        "user_text": "",
        "responds_to": [],
    }


def proposal_review_observation(
    proposal: dict,
    *,
    decision: str,
    memory_summary: dict | None = None,
) -> dict:
    """Build the bounded private Observation produced by the user's review choice."""
    if decision not in {"answer_only", "remember"}:
        raise ValueError("invalid_proposal_review_decision")

    base = {
        "verified": True,
        "confidentiality": "private",
        "review_decision": decision,
        "answer": str(proposal.get("answer") or "").strip()[:4000],
        "answer_source": str(
            proposal.get("answer_source") or "answer_candidate"
        )[:80],
        "knowledge_candidate": str(
            proposal.get("knowledge_candidate") or ""
        ).strip()[:4000],
        "user_text": str(proposal.get("user_text") or "").strip()[:4000],
        "responds_to": [
            str(x) for x in (proposal.get("responds_to") or [])
        ][:20],
    }
    if decision == "answer_only":
        return {
            **base,
            "source": "proposal_review",
            "text": (
                "本人が回答候補を確認し、永続記憶への反映は行わず、"
                "この回答だけで元Taskを完了することを選択した。"
            ),
            "memory_intake": None,
        }

    summary = deepcopy(memory_summary or {})
    if summary.get("status") not in {"committed", "replayed"}:
        raise ValueError("memory_intake_not_committed")
    receipts = [
        {
            "candidate_id": item.get("candidate_id"),
            "decision": item.get("decision"),
            "reason": item.get("reason"),
            "claim_id": item.get("claim_id"),
            "pending_id": item.get("pending_id"),
            "derived_claim_ids": list(item.get("derived_claim_ids") or []),
        }
        for item in (summary.get("receipts") or [])
        if isinstance(item, dict)
    ][:100]
    bounded_summary = {
        "input_id": summary.get("input_id"),
        "status": summary.get("status"),
        "source_id": summary.get("source_id"),
        "receipts": receipts,
    }
    decisions = [str(item.get("decision") or "-") for item in receipts]
    return {
        **base,
        "source": "memory_intake",
        "text": (
            "本人が記憶反映を選択し、Memory Intakeが"
            + str(bounded_summary.get("status") or "-")
            + "で完了した。WriteDecision="
            + (", ".join(decisions) if decisions else "none")
            + "。pendingは確認待ち候補であり確定PKB current factではない。"
        )[:4000],
        "memory_intake": bounded_summary,
    }


def _goal_completion_projection(session: dict) -> dict:
    """MAGIのGoal充足提案をTask内に保存されたResultだけで評価する。

    根拠の意味的な十分性はMAGI提案だが、実在するSource/Resultとの
    突き合わせとTask確定はRITSUKOが行う。失敗読取や未登録IDは根拠でない。
    """
    from ritsuko.core.goal_evidence import check_goal_response_evidence

    judgment = session.get("goal_judgment") or {}
    evidence = judgment.get("evidence") or {}
    result_ids = evidence.get("result_ids") or []
    evidence_status, available = check_goal_response_evidence(session, evidence)
    valid_refs = evidence_status == "verified_results"
    source_free = evidence_status == "source_free"
    answer = judgment.get("answer")
    if (judgment.get("next") == "respond"
            and judgment.get("fulfillment") == "met"
            and (valid_refs or source_free)
            and isinstance(answer, str) and answer.strip()):
        note = ""
        if valid_refs and any(
            available[result_id].get("verification_level") == "provider_cited"
            for result_id in result_ids
        ):
            note = "\n\n注: 出典は検索Providerが報告した引用であり、本文の独立確認は未実施です。"
        return {
            "task_status": "completed", "phase": "completed",
            "next_step": "respond", "message": answer.strip() + note,
            "question": None,
            "reason": "goal_evaluated_with_recorded_evidence"
            if valid_refs else "goal_evaluated_source_free",
        }
    return {
        "task_status": "failed", "phase": "failed",
        "next_step": "goal_evidence_or_source_requirement_not_met",
        "message": session.get("status_message"),
        "question": None, "reason": "goal_evidence_or_source_requirement_not_met",
    }


def task_projection(session: dict) -> dict:
    """RITSUKOの単一Task状態変換。判断方式が違っても制御状態を統一する。"""
    goal_mode = (session.get("decision_strategy") == "llm"
                 or session.get("control_mode") == "goal_loop_v1")
    if goal_mode and session.get("status") == "candidate_ready":
        return _goal_completion_projection(session)
    detail = session.get("detail") or {}
    status = session.get("status")
    answer = detail.get("answer_candidate")

    verified_observations = [
        item for item in (session.get("observations") or [])
        if isinstance(item, dict)
        and item.get("verified") is True
        and item.get("source") in {"pkb", "web", "finance"}
    ]
    unresolved_requests = [
        item for item in (session.get("pending_requests") or [])
        if isinstance(item, dict)
        and item.get("request_id")
    ]
    if (
        status == "candidate_ready"
        and isinstance(answer, str)
        and answer.strip()
        and session.get("tool_read_executed") is True
        and verified_observations
        and not unresolved_requests
    ):
        return {
            "task_status": "completed",
            "phase": "completed",
            "next_step": "respond",
            "message": answer.strip(),
            "question": None,
            "reason": "validated_answer_candidate_after_verified_observation",
        }
    if status == "candidate_ready":
        return {
            "task_status": "waiting_external",
            "phase": "awaiting_review",
            "next_step": "review_answer_candidate",
            "message": None,
            "question": None,
            "reason": "answer_candidate_not_grounded_by_verified_required_observation",
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
            "message": session.get("status_message") if goal_mode else None,
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

    if status == "stopped" and session.get("next_step") == "user_requested_stop":
        return {
            "task_status": "paused",
            "phase": "paused",
            "next_step": "user_requested_stop",
            "message": None,
            "question": None,
            "reason": "user_requested_stop",
        }

    return {
        "task_status": "failed" if status == "stopped" else "running",
        "phase": "failed" if status == "stopped" else "orient",
        "next_step": session.get("next_step"),
        "message": session.get("status_message") if goal_mode else None,
        "question": session.get("user_question"),
        "reason": session.get("next_step"),
    }
