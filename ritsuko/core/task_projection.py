# Provenance-Origin: original
# Provenance-AI: AI-generated
"""Project MAGI decision outcomes to one persisted RITSUKO Task state.

Both judgment strategies share this Task status/phase/next-step mapping.
The model's opinion is never allowed to fabricate a Source or Result; its
recorded Goal claim is checked by the same RITSUKO evidence gate.
"""
from __future__ import annotations

from ritsuko.application.observation_sources import AUTO_READ_SOURCES
from ritsuko.core.goal_evidence import check_goal_response_evidence


def _completion_from_goal_claim(session: dict, claim: dict) -> dict:
    """Validate the model's Goal completion claim without executing its text."""
    evidence_status, available = check_goal_response_evidence(
        session, claim.get("evidence") or {},
    )
    answer = claim.get("answer")
    references = (claim.get("evidence") or {}).get("result_ids") or []
    if (
        claim.get("next") == "respond"
        and claim.get("fulfillment") == "met"
        and isinstance(answer, str)
        and answer.strip()
        and not session.get("pending_requests")
        and evidence_status in {"verified_results", "source_free"}
    ):
        # Provider citations are a provider-level assurance, not a second
        # independent verification of the full underlying web content.
        provider_note = (
            "\n\n注: 出典は検索Providerが報告した引用であり、本文の独立確認は未実施です。"
            if evidence_status == "verified_results" and any(
                available[result_id].get("verification_level") == "provider_cited"
                for result_id in references
            )
            else ""
        )
        return {
            "task_status": "completed", "phase": "completed",
            "next_step": "respond", "message": answer.strip() + provider_note,
            "question": None,
            "reason": (
                "goal_evaluated_with_recorded_evidence"
                if evidence_status == "verified_results"
                else "goal_evaluated_source_free"
            ),
        }
    return {
        "task_status": "failed", "phase": "failed",
        "next_step": "goal_evidence_or_source_requirement_not_met",
        "message": session.get("status_message"), "question": None,
        "reason": "goal_evidence_or_source_requirement_not_met",
    }


def _completion_from_standard_candidate(session: dict) -> dict:
    """Resolve a stage-based READY answer using verified source evidence.

    A READY sentence alone cannot prove a read. Existing ungrounded standard
    answers still require the user's explicit review, not invented completion.
    """
    detail = session.get("detail") or {}
    answer = detail.get("answer_candidate")
    grounded = (
        session.get("tool_read_executed") is True
        and any(
            isinstance(obs, dict)
            and obs.get("verified") is True
            and obs.get("source") in AUTO_READ_SOURCES
            # The Result ID is the actual persisted read provenance.
            and obs.get("result_id")
            for obs in session.get("observations") or []
        )
        and not session.get("pending_requests")
    )
    if isinstance(answer, str) and answer.strip() and grounded:
        return {
            "task_status": "completed", "phase": "completed",
            "next_step": "respond", "message": answer.strip(),
            "question": None,
            "reason": "validated_answer_candidate_after_verified_observation",
        }
    return {
        "task_status": "waiting_external", "phase": "awaiting_review",
        "next_step": "review_answer_candidate", "message": None,
        "question": None,
        "reason": "answer_candidate_not_grounded_by_verified_required_observation",
    }



def can_requeue_paused_task(status: str, checkpoint: dict, action_count: int) -> bool:
    """停止済みTaskを同じIDで「未実行から再開」できる条件を統一する。

    前回のSource/Action、本人回答、記憶Reviewが残るTaskを新規のように
    再実行してはいけない。この入口は実行済みStepの復旧機構ではない。
    Repositoryは同じ条件をFOR UPDATEの中で再検査する。

    Args:
        status: DBに確定したTask状態。
        checkpoint: Task内の固定された判断方式・設定・停止履歴。
        action_count: 同じTaskに永続化済みのAction件数。

    Returns:
        bool: 原文・設定を保持したまま未実行Taskを再キュー可能ならTrue。
    """
    if status != "paused" or not isinstance(checkpoint, dict):
        return False
    intent = checkpoint.get("start_intent")
    if (
        checkpoint.get("core_slice") != "ritsuko_magi_observation_v1"
        or not isinstance(intent, dict)
        or intent.get("state") not in {"pending", "claimed"}
        or intent.get("decision_strategy") not in {"standard", "llm"}
        or checkpoint.get("stop_requested") is not True
        or checkpoint.get("active_attempt_id") is not None
        or checkpoint.get("magi_session") is not None
        or checkpoint.get("user_resume") is not None
        or checkpoint.get("proposal_review") is not None
        or checkpoint.get("proposal_memory_intake") is not None
        or action_count != 0
    ):
        return False
    members = checkpoint.get("member_specs")
    return isinstance(members, list) and any(
        isinstance(item, dict) and item.get("enabled") is True
        for item in members
    )


def task_projection(session: dict) -> dict:
    """Map all MAGI session outcomes through one RITSUKO Task state machine.

    The decision strategy selects *how* an answer is proposed. Projection does
    not select a different Executor, Persistence path or state mapping. An
    explicit Goal assertion is validated; otherwise stage-based READY is
    evaluated from the recorded Observation. Old Goal sessions without an
    assertion must fail closed rather than impersonate stage-based evidence.
    """
    status = session.get("status")
    if status == "candidate_ready":
        claim = session.get("goal_judgment")
        if isinstance(claim, dict):
            return _completion_from_goal_claim(session, claim)
        # Legacy or incomplete Goal sessions must never acquire completion
        # through the stage-based fallback without their model's Goal claim.
        if (
            session.get("decision_strategy") == "llm"
            or session.get("control_mode") == "goal_loop_v1"
        ):
            return _completion_from_goal_claim(session, {})
        return _completion_from_standard_candidate(session)

    if status == "waiting_user":
        return {
            "task_status": "waiting_external", "phase": "awaiting_clarification",
            "next_step": "ask_user", "message": None,
            "question": session.get("user_question"),
            "reason": session.get("next_step"),
        }

    if status == "waiting_information":
        return {
            "task_status": "waiting_external", "phase": "awaiting_information",
            "next_step": "review_information_requests",
            "message": session.get("status_message"),
            "question": None, "reason": session.get("next_step"),
        }

    if status == "proposal_ready":
        return {
            "task_status": "waiting_external", "phase": "awaiting_review",
            "next_step": "review_proposal", "message": None,
            "question": None, "reason": session.get("next_step"),
        }

    if status == "stopped" and session.get("next_step") == "user_requested_stop":
        return {
            "task_status": "paused", "phase": "paused",
            "next_step": "user_requested_stop", "message": None,
            "question": None, "reason": "user_requested_stop",
        }

    return {
        "task_status": "failed" if status == "stopped" else "running",
        "phase": "failed" if status == "stopped" else "orient",
        "next_step": session.get("next_step"),
        "message": session.get("status_message"),
        "question": session.get("user_question"),
        "reason": session.get("next_step"),
    }
