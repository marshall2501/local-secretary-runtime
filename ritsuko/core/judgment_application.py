# Provenance-Origin: original
# Provenance-AI: AI-generated
"""One RITSUKO decision application for both MAGI judgment strategies.

MAGI selects meaning/next action. This module alone translates a validated
proposal into a Task session transition. It never executes a Tool, writes a DB,
or accepts a model-provided Action/Result as an execution receipt.
"""
from __future__ import annotations

from copy import deepcopy

from ritsuko.core.goal_evidence import check_goal_response_evidence
from ritsuko.application.observation_sources import READ_OPERATIONS
from ritsuko.magi.dialogue import (
    _grounded_empty_finance_answer,
    _latest_verified_pkb_observation,
    _request_signatures,
    _turn_limit,
    _wait_for_user_after_exhausted_pkb,
)


def _record_requests(session: dict, requests: list[dict]) -> None:
    """Assign RITSUKO-owned request IDs; never trust model-generated IDs."""
    session["pending_requests"] = [
        {
            "request_id": (
                f"REQ-{session['task_id'][:8]}-{len(session['turns']):02d}-{index:02d}"
            ),
            **deepcopy(item),
        }
        for index, item in enumerate(requests, 1)
    ]


def _goal_as_detail(session: dict, decision: dict) -> dict | None:
    """Check Goal evidence before converting it to the shared proposal form."""
    session["goal"] = decision["goal"]
    session["goal_judgment"] = deepcopy(decision)
    status, _ = check_goal_response_evidence(session, decision["evidence"])
    if status == "unrecorded_task_result_reference":
        session.update(status="stopped", next_step=status)
        return None

    next_step = decision["next"]
    if next_step == "respond" and status not in {"verified_results", "source_free"}:
        session.update(status="stopped", next_step=status)
        return None
    if next_step in {"invoke", "request_read"}:
        # `invoke` is the single future Operation dispatch case. Initially
        # only existing read operations are registered, so the proven Source
        # executor and its persisted Result/Source IDs remain authoritative.
        if next_step == "invoke":
            proposal = decision["operation_request"]
            if not isinstance(proposal, dict):
                session.update(status="stopped", next_step="invalid_operation_request")
                return None
            source = READ_OPERATIONS.get(proposal.get("operation_id"))
            if source is None or not isinstance(proposal.get("arguments"), dict):
                session.update(status="stopped", next_step="operation_not_registered")
                return None
            request = {
                "source": source,
                "query": proposal["arguments"].get("query"),
                "reason": proposal.get("reason"),
            }
        else:
            request = decision["read_request"]
        state = "NEED_INFORMATION"
        requests = [{
            "source": request["source"],
            "what": request["query"].strip(),
            "reason": request["reason"],
        }]
    elif next_step == "ask_user":
        state, requests = "NEED_CLARIFICATION", []
    elif next_step == "respond":
        state, requests = "READY", []
    elif next_step == "wait":
        session["status_message"] = decision["reason"][:1000]
        session.update(status="waiting_information", next_step="wait_for_external")
        return None
    else:
        session["status_message"] = decision["reason"][:1000]
        session.update(status="stopped", next_step="unable")
        return None

    return {
        "state": state,
        "understood_request": decision["goal"],
        "reason": decision["reason"],
        "information_requests": requests,
        "question_for_user": decision.get("question"),
        "answer_candidate": decision.get("answer"),
        "knowledge_candidate": None,
        "action_candidate": None,
    }


async def apply_judgment(
    session: dict,
    decision: dict,
    *,
    strategy: str,
    purpose: str,
    repair=None,
    stop_requested=None,
) -> dict:
    """Apply a validated MAGI proposal using one Task state transition path.

    Args:
        session: In-flight session owned by the RITSUKO Task runner.
        decision: Schema-checked standard detail or Goal decision.
        strategy: Persisted Task judgment strategy (standard or llm).
        purpose: MAGI question purpose, for bounded standard repair.
        repair: Optional async function(issue) returning a replacement detail.
        stop_requested: Task-scoped cancellation query.

    Returns:
        The same session, containing proposed state for the single DB writer.
    """
    if stop_requested is not None and stop_requested():
        session.update(status="stopped", next_step="user_requested_stop")
        return session

    if strategy == "llm":
        detail = _goal_as_detail(session, decision)
        if detail is None:
            return session
    elif strategy == "standard":
        detail = deepcopy(decision)
    else:
        raise ValueError("unknown_flow_decision_strategy")

    session["detail"] = detail
    session["user_question"] = None
    state = detail["state"]

    # Standard can ask MAGI to correct a proposal, but the resulting decision
    # still passes this same application. This is not another Task executor.
    if (
        strategy == "standard" and repair is not None
        and purpose != "review_or_repair" and len(session["turns"]) < _turn_limit(session)
    ):
        issue = None
        if state == "READY" and not session.get("observations"):
            issue = (
                "新しいObservationがないREADY候補。回答と作業予定を区別し、"
                "根拠不要な一般説明なら回答を確定する"
            )
        elif state == "NEED_INFORMATION" and any(
            item.get("source") == "user"
            for item in detail["information_requests"]
        ) and not session.get("user_source_reviewed"):
            session["user_source_reviewed"] = True
            issue = (
                "source=userが提案された。利用可能なSourceで解消可能な"
                "不足情報ならまずそのSourceを選ぶ"
            )
        if issue is not None:
            replacement = await repair(issue)
            if replacement is None:
                return session
            return await apply_judgment(
                session, replacement, strategy=strategy,
                purpose="review_or_repair",
                stop_requested=stop_requested,
            )

    if state == "READY":
        session["pending_requests"] = []
        session.update(status="candidate_ready", next_step="review_answer_candidate")
        return session

    if state in {"ACTION_PROPOSAL", "KNOWLEDGE_CANDIDATE"}:
        session["pending_requests"] = []
        session.update(status="proposal_ready", next_step="review_proposal")
        return session

    if state == "NEED_CLARIFICATION":
        if len(session["turns"]) >= _turn_limit(session):
            session.update(status="stopped", next_step="max_turns_reached_with_user_question")
        else:
            session["pending_requests"] = []
            session["user_question"] = detail["question_for_user"]
            session.update(status="waiting_user", next_step="consider_user_question")
        return session

    if state != "NEED_INFORMATION":
        session.update(status="stopped", next_step="unable")
        return session

    requests = detail["information_requests"]
    if strategy == "standard":
        empty_answer = _grounded_empty_finance_answer(session, requests)
        if empty_answer is not None:
            session["detail"] = {
                **detail, "state": "READY", "information_requests": [],
                "answer_candidate": empty_answer,
                "knowledge_candidate": None, "action_candidate": None,
            }
            session["ritsuko_grounded_fallback"] = {
                "kind": "verified_empty_finance", "source": "finance",
                "result_count": 0,
            }
            session["pending_requests"] = []
            session.update(status="candidate_ready", next_step="review_answer_candidate")
            return session

        if _latest_verified_pkb_observation(session) is not None and requests:
            if all(item.get("source") == "pkb" for item in requests):
                # A single MAGI repair opportunity distinguishes missing
                # attributes from an actually different next read. Afterwards
                # RITSUKO stops repetitive reads and requests user context.
                if (
                    repair is not None and purpose != "review_or_repair"
                    and len(session["turns"]) < _turn_limit(session)
                ):
                    replacement = await repair(
                        "verified PKB Observation直後のPKB再要求。未確認だった"
                        "blocking属性だけをuserに聞くか、既存Observationで答える"
                    )
                    if replacement is None:
                        return session
                    return await apply_judgment(
                        session, replacement, strategy=strategy,
                        purpose="review_or_repair",
                        stop_requested=stop_requested,
                    )
                return _wait_for_user_after_exhausted_pkb(session, requests)

    signatures = _request_signatures(requests)
    prior = session.setdefault("previous_request_signatures", [])
    repeated = bool(session.get("observations")) and bool(signatures) and all(
        signature in prior for signature in signatures
    )
    if repeated:
        session.update(status="stopped", next_step="repeated_request_without_progress")
        return session
    for signature in signatures:
        if signature not in prior:
            prior.append(signature)
    _record_requests(session, requests)
    if len(session["turns"]) >= _turn_limit(session):
        session.update(status="stopped", next_step="max_turns_reached_with_pending_information")
    elif requests and all(item.get("source") == "user" for item in requests):
        session["user_question"] = "確認したいこと: " + " / ".join(
            item["what"] for item in requests
        )
        session.update(status="waiting_user", next_step="ask_user_for_information")
    else:
        session.update(status="waiting_information", next_step="review_information_requests")
    return session
