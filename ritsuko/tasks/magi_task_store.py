"""Persist the state-driven RITSUKO⇄MAGI vertical slice in Core tables."""
from __future__ import annotations

from copy import deepcopy
from uuid import UUID

from psycopg.types.json import Jsonb

from ritsuko.core.magi_bridge import (
    CORE_SLICE,
    proposal_review_observation,
    reviewable_user_knowledge_proposal,
    task_projection,
)
from pkb.memory_contracts import MemoryIntake


def create_task(db, *, task_id: UUID, request: str, member_specs: list[dict]) -> None:
    checkpoint = {
        "core_slice": CORE_SLICE,
        "phase": "orient",
        "protocol": "d19-state-driven-v4",
        "selected_capability": None,
        "question": None,
        "message": None,
        "member_specs": deepcopy(member_specs),
        "magi_session": None,
        "final_core_decision": None,
    }
    with db.transaction(), db.cursor() as cur:
        cur.execute(
            """INSERT INTO secretary.tasks
               (id, request, requested_by, domain, completion_criteria,
                permission_scope, status, checkpoint)
               VALUES (%s, %s, 'local_user', 'general', %s, %s, 'running', %s)""",
            (
                task_id,
                request,
                "Return a grounded answer from bounded PKB evidence or stop safely.",
                Jsonb({
                    "pkb_read": True,
                    "finance_read": False,
                    "web_research": False,
                    "external_actions": False,
                    "cloud_private_pkb_context": False,
                }),
                Jsonb(checkpoint),
            ),
        )
        cur.execute(
            """INSERT INTO secretary.audit_events
               (actor, event_type, task_id, object_type, object_id, details)
               VALUES ('ritsuko_core', 'core.magi.task_started',
                       %s, 'task', %s, %s)""",
            (
                task_id,
                task_id,
                Jsonb({
                    "core_slice": CORE_SLICE,
                    "member_count": sum(
                        1 for item in member_specs if item.get("enabled")
                    ),
                }),
            ),
        )


def claim_user_resume(
    db,
    *,
    task_id: UUID,
    reply_length: int,
    reply_fingerprint: str,
) -> tuple[dict, str | None]:
    """Atomically claim or resume one waiting MAGI Task for a user reply."""
    fingerprint = str(reply_fingerprint or "").strip().lower()
    if (
        len(fingerprint) != 64
        or any(ch not in "0123456789abcdef" for ch in fingerprint)
    ):
        raise ValueError("invalid_user_reply_fingerprint")

    with db.transaction(), db.cursor() as cur:
        cur.execute(
            """SELECT status, checkpoint
               FROM secretary.tasks
               WHERE id=%s
               FOR UPDATE""",
            (task_id,),
        )
        row = cur.fetchone()
        if row is None:
            raise ValueError("unknown_task")
        status, checkpoint = row[0], row[1] or {}
        if checkpoint.get("core_slice") != CORE_SLICE:
            raise ValueError("not_magi_observation_task")
        session = checkpoint.get("magi_session")
        if not isinstance(session, dict) or session.get("status") != "waiting_user":
            raise ValueError("task_not_waiting_for_user")

        pending_ids = [
            str(item.get("request_id"))
            for item in (session.get("pending_requests") or [])
            if isinstance(item, dict) and item.get("request_id")
        ][:20]
        resume = checkpoint.get("user_resume")
        if status == "running":
            if (
                not isinstance(resume, dict)
                or resume.get("status") != "processing"
            ):
                raise ValueError("task_not_waiting_external")
            if resume.get("reply_fingerprint") != fingerprint:
                raise ValueError("user_resume_reply_mismatch")
            cur.execute(
                """INSERT INTO secretary.audit_events
                   (actor, event_type, task_id, object_type, object_id, details)
                   VALUES ('ritsuko_core', 'core.magi.user_reply_resumed',
                           %s, 'task', %s, %s)""",
                (
                    task_id,
                    task_id,
                    Jsonb({
                        "reply_length": max(0, int(reply_length)),
                        "pending_request_ids": pending_ids,
                    }),
                ),
            )
            return deepcopy(session), checkpoint.get("selected_capability")

        if status != "waiting_external":
            raise ValueError("task_not_waiting_external")
        if isinstance(resume, dict) and resume.get("status") == "retry_required":
            prior_fingerprint = str(resume.get("reply_fingerprint") or "")
            if prior_fingerprint and prior_fingerprint != fingerprint:
                raise ValueError("user_resume_reply_mismatch")

        user_resume = {
            "status": "processing",
            "reply_length": max(0, int(reply_length)),
            "reply_fingerprint": fingerprint,
            "pending_request_ids": pending_ids,
        }
        next_checkpoint = {
            **checkpoint,
            "phase": "orient",
            "question": None,
            "reason": "user_reply_received",
            "user_resume": user_resume,
        }
        cur.execute(
            """UPDATE secretary.tasks
               SET status='running', checkpoint=%s
               WHERE id=%s""",
            (Jsonb(next_checkpoint), task_id),
        )
        cur.execute(
            """INSERT INTO secretary.audit_events
               (actor, event_type, task_id, object_type, object_id, details)
               VALUES ('ritsuko_core', 'core.magi.user_reply_received',
                       %s, 'task', %s, %s)""",
            (
                task_id,
                task_id,
                Jsonb({
                    "reply_length": max(0, int(reply_length)),
                    "pending_request_ids": pending_ids,
                }),
            ),
        )
        return deepcopy(session), checkpoint.get("selected_capability")


def abort_user_resume(
    db,
    *,
    task_id: UUID,
    error: str,
) -> None:
    """Return an interrupted user-resume turn to a retryable waiting state."""
    with db.transaction(), db.cursor() as cur:
        cur.execute(
            """SELECT status, checkpoint
               FROM secretary.tasks
               WHERE id=%s
               FOR UPDATE""",
            (task_id,),
        )
        row = cur.fetchone()
        if row is None:
            return
        status, checkpoint = row[0], row[1] or {}
        resume = checkpoint.get("user_resume")
        if (
            status != "running"
            or not isinstance(resume, dict)
            or resume.get("status") != "processing"
        ):
            return
        resume["status"] = "retry_required"
        resume["last_error_type"] = str(error)[:160]
        checkpoint.update({
            "phase": "awaiting_clarification",
            "question": (
                (checkpoint.get("magi_session") or {}).get("user_question")
            ),
            "reason": "user_resume_failed",
            "user_resume": resume,
            "final_core_decision": {
                "next_step": "retry_user_resume",
                "reason": "user_resume_failed",
                "task_status": "waiting_external",
            },
        })
        cur.execute(
            """UPDATE secretary.tasks
               SET status='waiting_external', checkpoint=%s, completed_at=NULL
               WHERE id=%s""",
            (Jsonb(checkpoint), task_id),
        )
        cur.execute(
            """INSERT INTO secretary.audit_events
               (actor, event_type, task_id, object_type, object_id, details)
               VALUES ('ritsuko_core', 'core.magi.user_reply_retry_required',
                       %s, 'task', %s, %s)""",
            (
                task_id,
                task_id,
                Jsonb({"error_type": str(error)[:160]}),
            ),
        )


def persist_session(
    db,
    *,
    task_id: UUID,
    session: dict,
    selected_capability: str | None = None,
) -> dict:
    projection = task_projection(session)
    checkpoint_patch = {
        "phase": projection["phase"],
        "selected_capability": selected_capability,
        "question": projection["question"],
        "message": projection["message"],
        "reason": projection["reason"],
        "magi_session": deepcopy(session),
        "final_core_decision": {
            "next_step": projection["next_step"],
            "reason": projection["reason"],
            "task_status": projection["task_status"],
        },
    }
    with db.transaction(), db.cursor() as cur:
        cur.execute(
            "SELECT checkpoint FROM secretary.tasks WHERE id=%s FOR UPDATE",
            (task_id,),
        )
        row = cur.fetchone()
        if row is None:
            raise ValueError("unknown_task")
        checkpoint = row[0] or {}
        checkpoint.update(checkpoint_patch)
        user_resume = checkpoint.get("user_resume")
        if isinstance(user_resume, dict) and user_resume.get("status") == "processing":
            user_resume["status"] = "completed"
            user_resume.pop("last_error_type", None)
            checkpoint["user_resume"] = user_resume
        cur.execute(
            """UPDATE secretary.tasks
               SET status=%s, checkpoint=%s,
                   completed_at=CASE WHEN %s='completed' THEN now() ELSE NULL END
               WHERE id=%s""",
            (
                projection["task_status"],
                Jsonb(checkpoint),
                projection["task_status"],
                task_id,
            ),
        )
        cur.execute(
            """INSERT INTO secretary.audit_events
               (actor, event_type, task_id, object_type, object_id, details)
               VALUES ('ritsuko_core', %s, %s, 'task', %s, %s)""",
            (
                (
                    "core.magi.completed"
                    if projection["task_status"] == "completed"
                    else "core.magi.state_saved"
                ),
                task_id,
                task_id,
                Jsonb({
                    "dialogue_status": session.get("status"),
                    "next_step": projection["next_step"],
                    "turn_count": len(session.get("turns") or []),
                    "tool_read_executed": bool(session.get("tool_read_executed")),
                }),
            ),
        )
    return projection


def _load_reviewable_proposal(cur, task_id: UUID) -> tuple[dict, dict]:
    cur.execute(
        """SELECT status, checkpoint
           FROM secretary.tasks
           WHERE id=%s
           FOR UPDATE""",
        (task_id,),
    )
    row = cur.fetchone()
    if row is None:
        raise ValueError("unknown_task")
    status, checkpoint = row[0], row[1] or {}
    if status not in {"waiting_external", "running"}:
        raise ValueError("task_not_reviewable")
    if status == "running":
        review = checkpoint.get("proposal_review")
        if not isinstance(review, dict) or review.get("status") != "processing":
            raise ValueError("task_not_waiting_external")
    if checkpoint.get("core_slice") != CORE_SLICE:
        raise ValueError("not_magi_observation_task")
    session = checkpoint.get("magi_session")
    if not isinstance(session, dict):
        raise ValueError("missing_magi_session")
    proposal = reviewable_user_knowledge_proposal(session)
    if proposal is None:
        raise ValueError("knowledge_proposal_not_user_grounded")
    return checkpoint, proposal


def prepare_memory_intake(db, *, task_id: UUID) -> MemoryIntake:
    with db.transaction(), db.cursor() as cur:
        checkpoint, proposal = _load_reviewable_proposal(cur, task_id)
        active_review = checkpoint.get("proposal_review")
        if (
            isinstance(active_review, dict)
            and active_review.get("status") == "processing"
            and active_review.get("decision") != "remember"
        ):
            raise ValueError("proposal_review_already_processing")
        prepared = checkpoint.get("proposal_memory_intake")
        if isinstance(prepared, dict):
            return MemoryIntake(**prepared)
        issued = MemoryIntake.issue(proposal["knowledge_candidate"])
        intake = MemoryIntake(
            contract_version=issued.contract_version,
            input_id=issued.input_id,
            raw_text=issued.raw_text,
            source_kind=issued.source_kind,
            source_ref=(
                f"fixture://magi-task/{task_id}/{issued.input_id}"
            ),
            recorded_at=issued.recorded_at,
            confidentiality=issued.confidentiality,
            timezone=issued.timezone,
            observed_at=issued.observed_at,
        )
        prepared = {
            "contract_version": intake.contract_version,
            "input_id": intake.input_id,
            "raw_text": intake.raw_text,
            "source_kind": intake.source_kind,
            "source_ref": intake.source_ref,
            "recorded_at": intake.recorded_at,
            "confidentiality": intake.confidentiality,
            "timezone": intake.timezone,
            "observed_at": intake.observed_at,
        }
        checkpoint["proposal_memory_intake"] = prepared
        checkpoint["reason"] = "memory_intake_prepared"
        cur.execute(
            "UPDATE secretary.tasks SET checkpoint=%s WHERE id=%s",
            (Jsonb(checkpoint), task_id),
        )
        cur.execute(
            """INSERT INTO secretary.audit_events
               (actor, event_type, task_id, object_type, object_id, details)
               VALUES ('ritsuko_core', 'core.magi.memory_intake_prepared',
                       %s, 'task', %s, %s)""",
            (
                task_id,
                task_id,
                Jsonb({
                    "input_id": intake.input_id,
                    "candidate_length": len(proposal["knowledge_candidate"]),
                    "confirmation_basis": "user_clicked_displayed_candidate",
                }),
            ),
        )
        return intake


def _memory_summary_for_review(
    checkpoint: dict,
    memory_result: dict | None,
) -> dict:
    prepared = checkpoint.get("proposal_memory_intake")
    if not isinstance(prepared, dict):
        raise ValueError("memory_intake_not_prepared")
    result = memory_result or {}
    if result.get("input_id") != prepared.get("input_id"):
        raise ValueError("memory_intake_result_mismatch")
    if result.get("status") not in {"committed", "replayed"}:
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
        for item in (result.get("candidates") or [])
        if isinstance(item, dict)
    ][:100]
    return {
        "input_id": prepared.get("input_id"),
        "status": result.get("status"),
        "source_id": result.get("source_id"),
        "receipts": receipts,
    }


def claim_proposal_review(
    db,
    *,
    task_id: UUID,
    decision: str,
    memory_result: dict | None = None,
) -> tuple[dict, str | None, dict, dict]:
    """Atomically claim a reviewed proposal for one final MAGI re-evaluation."""
    if decision not in {"answer_only", "remember"}:
        raise ValueError("invalid_proposal_review_decision")

    with db.transaction(), db.cursor() as cur:
        checkpoint, proposal = _load_reviewable_proposal(cur, task_id)
        session = checkpoint.get("magi_session")
        if not isinstance(session, dict):
            raise ValueError("missing_magi_session")

        active_review = checkpoint.get("proposal_review")
        if (
            isinstance(active_review, dict)
            and active_review.get("status") == "processing"
        ):
            if active_review.get("decision") != decision:
                raise ValueError("proposal_review_already_processing")
            memory_summary = deepcopy(active_review.get("memory_intake"))
            if decision == "remember":
                supplied = _memory_summary_for_review(checkpoint, memory_result)
                if supplied.get("input_id") != (memory_summary or {}).get("input_id"):
                    raise ValueError("proposal_review_memory_mismatch")
            observation = proposal_review_observation(
                proposal,
                decision=decision,
                memory_summary=memory_summary,
            )
            cur.execute(
                """INSERT INTO secretary.audit_events
                   (actor, event_type, task_id, object_type, object_id, details)
                   VALUES ('ritsuko_core', 'core.magi.proposal_review_resumed',
                           %s, 'task', %s, %s)""",
                (
                    task_id,
                    task_id,
                    Jsonb({"decision": decision}),
                ),
            )
            return (
                deepcopy(session),
                checkpoint.get("selected_capability"),
                observation,
                deepcopy(active_review),
            )

        if decision == "answer_only":
            if isinstance(checkpoint.get("proposal_memory_intake"), dict):
                raise ValueError("memory_review_already_prepared")
            memory_summary = None
        else:
            memory_summary = _memory_summary_for_review(
                checkpoint,
                memory_result,
            )

        review = {
            "decision": decision,
            "status": "processing",
            "answer": proposal["answer"],
            "answer_source": proposal.get("answer_source", "answer_candidate"),
            "knowledge_candidate": proposal["knowledge_candidate"],
            "user_text": proposal["user_text"],
            "responds_to": list(proposal["responds_to"]),
            "memory_intake": deepcopy(memory_summary),
        }
        observation = proposal_review_observation(
            proposal,
            decision=decision,
            memory_summary=memory_summary,
        )
        checkpoint.update({
            "phase": "orient",
            "question": None,
            "message": None,
            "reason": "proposal_review_re_evaluation",
            "proposal_review": deepcopy(review),
        })
        cur.execute(
            """UPDATE secretary.tasks
               SET status='running', checkpoint=%s, completed_at=NULL
               WHERE id=%s""",
            (Jsonb(checkpoint), task_id),
        )
        cur.execute(
            """INSERT INTO secretary.audit_events
               (actor, event_type, task_id, object_type, object_id, details)
               VALUES ('ritsuko_core', 'core.magi.proposal_review_started',
                       %s, 'task', %s, %s)""",
            (
                task_id,
                task_id,
                Jsonb({
                    "decision": decision,
                    "answer_source": proposal.get(
                        "answer_source", "answer_candidate"
                    ),
                    "memory_status": (
                        memory_summary.get("status")
                        if isinstance(memory_summary, dict)
                        else None
                    ),
                    "responds_to": list(proposal["responds_to"]),
                }),
            ),
        )
        return (
            deepcopy(session),
            checkpoint.get("selected_capability"),
            observation,
            review,
        )


def finalize_proposal_review(
    db,
    *,
    task_id: UUID,
    session: dict,
    selected_capability: str | None = None,
) -> dict:
    """Finalize only after a successful, task-matched MAGI review-result turn."""
    if str(session.get("task_id") or "") != str(task_id):
        raise ValueError("proposal_review_task_mismatch")
    if session.get("status") != "review_evaluated":
        raise ValueError("proposal_review_not_evaluated")
    if session.get("next_step") != "ritsuko_finalize_review":
        raise ValueError("proposal_review_not_ready_for_finalization")
    turns = session.get("turns") or []
    if not turns:
        raise ValueError("proposal_review_turn_missing")
    last_turn = turns[-1]
    if (
        last_turn.get("question_purpose") != "evaluate_review_result"
        or last_turn.get("status") != "ok"
    ):
        raise ValueError("proposal_review_turn_not_ok")
    envelope = last_turn.get("request_envelope") or {}
    if str(envelope.get("task_id") or "") != str(task_id):
        raise ValueError("proposal_review_turn_task_mismatch")
    turn_response = last_turn.get("response")
    if not isinstance(turn_response, dict):
        raise ValueError("proposal_review_turn_response_missing")
    if turn_response != (session.get("post_review_evaluation") or {}):
        raise ValueError("proposal_review_evaluation_mismatch")

    with db.transaction(), db.cursor() as cur:
        cur.execute(
            """SELECT status, checkpoint
               FROM secretary.tasks
               WHERE id=%s
               FOR UPDATE""",
            (task_id,),
        )
        row = cur.fetchone()
        if row is None:
            raise ValueError("unknown_task")
        status, checkpoint = row[0], row[1] or {}
        if status != "running":
            raise ValueError("proposal_review_task_not_running")
        if checkpoint.get("core_slice") != CORE_SLICE:
            raise ValueError("not_magi_observation_task")

        review = checkpoint.get("proposal_review")
        if not isinstance(review, dict) or review.get("status") != "processing":
            raise ValueError("proposal_review_not_claimed")
        answer = str(review.get("answer") or "").strip()
        if not answer:
            raise ValueError("proposal_review_answer_missing")

        evaluation = deepcopy(turn_response)
        if evaluation.get("state") not in {"READY", "KNOWLEDGE_CANDIDATE"}:
            raise ValueError("proposal_review_evaluation_not_finalizable")

        expected_source = (
            "proposal_review"
            if review.get("decision") == "answer_only"
            else "memory_intake"
        )
        review_observations = [
            item for item in (session.get("observations") or [])
            if isinstance(item, dict)
            and item.get("source") == expected_source
            and item.get("verified") is True
            and item.get("review_decision") == review.get("decision")
        ]
        if not review_observations:
            raise ValueError("proposal_review_observation_missing")
        latest_review_observation = review_observations[-1]
        if (
            str(latest_review_observation.get("answer") or "").strip()
            != answer
        ):
            raise ValueError("proposal_review_answer_mismatch")
        if review.get("decision") == "remember":
            expected_memory = review.get("memory_intake") or {}
            observed_memory = latest_review_observation.get("memory_intake") or {}
            if (
                observed_memory.get("input_id")
                != expected_memory.get("input_id")
            ):
                raise ValueError("proposal_review_memory_mismatch")

        review["status"] = "completed"
        review["magi_evaluation"] = {
            "state": evaluation.get("state"),
            "reason": evaluation.get("reason"),
            "answer_candidate": evaluation.get("answer_candidate"),
        }
        decision = str(review.get("decision") or "")
        reason = (
            "proposal_review_evaluated_answer_only"
            if decision == "answer_only"
            else "proposal_review_evaluated_memory"
        )
        checkpoint.update({
            "phase": "completed",
            "selected_capability": (
                selected_capability
                if selected_capability is not None
                else checkpoint.get("selected_capability")
            ),
            "question": None,
            "message": answer,
            "reason": reason,
            "proposal_review": review,
            "magi_session": deepcopy(session),
            "final_core_decision": {
                "next_step": "respond",
                "reason": reason,
                "task_status": "completed",
            },
        })
        cur.execute(
            """UPDATE secretary.tasks
               SET status='completed', checkpoint=%s, completed_at=now()
               WHERE id=%s""",
            (Jsonb(checkpoint), task_id),
        )
        cur.execute(
            """INSERT INTO secretary.audit_events
               (actor, event_type, task_id, object_type, object_id, details)
               VALUES ('ritsuko_core', 'core.magi.proposal_review_completed',
                       %s, 'task', %s, %s)""",
            (
                task_id,
                task_id,
                Jsonb({
                    "decision": decision,
                    "magi_state": evaluation.get("state"),
                    "turn_count": len(turns),
                    "memory_status": (
                        (review.get("memory_intake") or {}).get("status")
                        if isinstance(review.get("memory_intake"), dict)
                        else None
                    ),
                }),
            ),
        )
        return deepcopy(review)


def abort_proposal_review(
    db,
    *,
    task_id: UUID,
    error: str,
) -> None:
    """Return an interrupted review to awaiting_review without losing the proposal."""
    with db.transaction(), db.cursor() as cur:
        cur.execute(
            """SELECT status, checkpoint
               FROM secretary.tasks
               WHERE id=%s
               FOR UPDATE""",
            (task_id,),
        )
        row = cur.fetchone()
        if row is None:
            return
        status, checkpoint = row[0], row[1] or {}
        review = checkpoint.get("proposal_review")
        if status != "running" or not isinstance(review, dict):
            return
        if review.get("status") != "processing":
            return

        review["status"] = "retry_required"
        review["last_error_type"] = str(error)[:160]
        checkpoint.update({
            "phase": "awaiting_review",
            "question": None,
            "message": None,
            "reason": "proposal_review_re_evaluation_failed",
            "proposal_review": review,
            "final_core_decision": {
                "next_step": "retry_proposal_review",
                "reason": "proposal_review_re_evaluation_failed",
                "task_status": "waiting_external",
            },
        })
        cur.execute(
            """UPDATE secretary.tasks
               SET status='waiting_external', checkpoint=%s, completed_at=NULL
               WHERE id=%s""",
            (Jsonb(checkpoint), task_id),
        )
        cur.execute(
            """INSERT INTO secretary.audit_events
               (actor, event_type, task_id, object_type, object_id, details)
               VALUES ('ritsuko_core', 'core.magi.proposal_review_retry_required',
                       %s, 'task', %s, %s)""",
            (
                task_id,
                task_id,
                Jsonb({
                    "decision": review.get("decision"),
                    "error_type": str(error)[:160],
                }),
            ),
        )


def record_pkb_read(
    db,
    *,
    task_id: UUID,
    execution: dict,
    pending_request: dict,
) -> tuple[str, str]:
    request_ids = list(pending_request.get("request_ids") or [])
    request_key = request_ids[0] if request_ids else "pkb"
    idempotency_key = f"ritsuko-magi:{task_id}:pkb:{request_key}"
    with db.transaction(), db.cursor() as cur:
        cur.execute(
            """SELECT a.id, r.id
               FROM secretary.actions a
               LEFT JOIN secretary.results r ON r.action_id=a.id
               WHERE a.idempotency_key=%s""",
            (idempotency_key,),
        )
        existing = cur.fetchone()
        if existing is not None and existing[1] is not None:
            return str(existing[0]), str(existing[1])

        cur.execute(
            """INSERT INTO secretary.sources
               (source_type, uri, citation, retrieved_at, confidentiality, metadata)
               VALUES ('tool', %s, %s, now(), 'private', %s)
               RETURNING id""",
            (
                f"tool://ritsuko-magi/pkb/{task_id}/{request_key}",
                execution.get("citation")
                or "RITSUKO bounded read-only PKB result",
                Jsonb({
                    "task_id": str(task_id),
                    "capability": "pkb_search",
                    "request_ids": request_ids,
                    "what": pending_request.get("what"),
                    "result_count": int(execution.get("total") or 0),
                    **(execution.get("source_metadata") or {}),
                }),
            ),
        )
        source_id = cur.fetchone()[0]

        cur.execute(
            """INSERT INTO secretary.actions
               (task_id, actor, tool, operation, parameters, risk,
                authorization_basis, status, idempotency_key,
                reversible, started_at, finished_at)
               VALUES (%s, 'ritsuko_core', %s, %s, %s,
                       'read_only', 'ritsuko_magi_pkb_read_v1',
                       'succeeded', %s, true, now(), now())
               RETURNING id""",
            (
                task_id,
                execution.get("tool") or "pkb",
                execution.get("operation") or "search",
                Jsonb({
                    "what": pending_request.get("what"),
                    "request_ids": request_ids,
                    "bounded": True,
                    "cloud_context_gate": "local_only_private_pkb",
                }),
                idempotency_key,
            ),
        )
        action_id = cur.fetchone()[0]

        result = execution.get("result") or {}
        cur.execute(
            """INSERT INTO secretary.results
               (action_id, source_id, outcome, summary, evidence,
                verified_by, verified_at)
               VALUES (%s, %s, %s, %s, %s, %s, now())
               RETURNING id""",
            (
                action_id,
                source_id,
                "success" if int(execution.get("total") or 0) > 0 else "inconclusive",
                str(execution.get("answer") or ""),
                Jsonb({
                    "result_kind": result.get("result_kind"),
                    "total": int(execution.get("total") or 0),
                    "data": result,
                    "responds_to": request_ids,
                }),
                execution.get("verified_by") or "deterministic_pkb_query",
            ),
        )
        result_id = cur.fetchone()[0]
        cur.execute(
            """SELECT checkpoint FROM secretary.tasks WHERE id=%s FOR UPDATE""",
            (task_id,),
        )
        checkpoint = (cur.fetchone() or [{}])[0] or {}
        checkpoint.update({
            "phase": "observe",
            "selected_capability": "pkb_search",
            "action_id": str(action_id),
            "result_id": str(result_id),
            "result_count": int(execution.get("total") or 0),
        })
        cur.execute(
            "UPDATE secretary.tasks SET checkpoint=%s WHERE id=%s",
            (Jsonb(checkpoint), task_id),
        )
        cur.execute(
            """INSERT INTO secretary.audit_events
               (actor, event_type, task_id, action_id,
                object_type, object_id, details)
               VALUES ('ritsuko_core', 'core.magi.pkb_observed',
                       %s, %s, 'task', %s, %s)""",
            (
                task_id,
                action_id,
                task_id,
                Jsonb({
                    "request_ids": request_ids,
                    "result_count": int(execution.get("total") or 0),
                    "cloud_context_gate": "local_only_private_pkb",
                }),
            ),
        )
    return str(action_id), str(result_id)


def fail_task(db, *, task_id: UUID, error: str) -> None:
    """Fail one persisted MAGI Task without storing secret-bearing exception detail."""
    with db.transaction(), db.cursor() as cur:
        cur.execute(
            "SELECT checkpoint FROM secretary.tasks WHERE id=%s FOR UPDATE",
            (task_id,),
        )
        row = cur.fetchone()
        if row is None:
            return
        checkpoint = row[0] or {}
        checkpoint.update({
            "phase": "failed",
            "reason": "magi_observation_loop_failed",
            "error_type": str(error)[:160],
            "final_core_decision": {
                "next_step": "stop",
                "reason": "magi_observation_loop_failed",
                "task_status": "failed",
            },
        })
        cur.execute(
            """UPDATE secretary.tasks
               SET status='failed', checkpoint=%s, completed_at=NULL
               WHERE id=%s""",
            (Jsonb(checkpoint), task_id),
        )
        cur.execute(
            """INSERT INTO secretary.audit_events
               (actor, event_type, task_id, object_type, object_id, details)
               VALUES ('ritsuko_core', 'core.magi.failed',
                       %s, 'task', %s, %s)""",
            (
                task_id,
                task_id,
                Jsonb({"error_type": str(error)[:160]}),
            ),
        )
