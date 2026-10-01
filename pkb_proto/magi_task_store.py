"""Persist the state-driven RITSUKO⇄MAGI vertical slice in Core tables."""
from __future__ import annotations

from copy import deepcopy
from uuid import UUID

from psycopg.types.json import Jsonb

from .magi_core_bridge import (
    CORE_SLICE,
    reviewable_user_knowledge_proposal,
    task_projection,
)
from .memory_contracts import MemoryIntake


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
) -> tuple[dict, str | None]:
    """Atomically claim one waiting MAGI Task before a user-resume LLM turn."""
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
        if status != "waiting_external":
            raise ValueError("task_not_waiting_external")
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
        next_checkpoint = {
            **checkpoint,
            "phase": "orient",
            "question": None,
            "reason": "user_reply_received",
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
    if status != "waiting_external":
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


def complete_answer_only(db, *, task_id: UUID) -> dict:
    with db.transaction(), db.cursor() as cur:
        checkpoint, proposal = _load_reviewable_proposal(cur, task_id)
        review = {
            "decision": "answer_only",
            "answer": proposal["answer"],
            "memory_intake": None,
        }
        checkpoint.update({
            "phase": "completed",
            "question": None,
            "message": proposal["answer"],
            "reason": "user_grounded_answer_only",
            "proposal_review": review,
            "final_core_decision": {
                "next_step": "respond",
                "reason": "user_grounded_answer_only",
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
               VALUES ('ritsuko_core', 'core.magi.proposal_answer_only',
                       %s, 'task', %s, %s)""",
            (
                task_id,
                task_id,
                Jsonb({
                    "responds_to": proposal["responds_to"],
                    "user_text_length": len(proposal["user_text"]),
                }),
            ),
        )
        return review


def prepare_memory_intake(db, *, task_id: UUID) -> MemoryIntake:
    with db.transaction(), db.cursor() as cur:
        checkpoint, proposal = _load_reviewable_proposal(cur, task_id)
        prepared = checkpoint.get("proposal_memory_intake")
        if isinstance(prepared, dict):
            return MemoryIntake(**prepared)
        intake = MemoryIntake.issue(proposal["knowledge_candidate"])
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


def complete_memory_review(db, *, task_id: UUID, memory_result: dict) -> dict:
    with db.transaction(), db.cursor() as cur:
        checkpoint, proposal = _load_reviewable_proposal(cur, task_id)
        prepared = checkpoint.get("proposal_memory_intake")
        if not isinstance(prepared, dict):
            raise ValueError("memory_intake_not_prepared")
        if memory_result.get("input_id") != prepared.get("input_id"):
            raise ValueError("memory_intake_result_mismatch")
        if memory_result.get("status") not in {"committed", "replayed"}:
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
            for item in (memory_result.get("candidates") or [])
            if isinstance(item, dict)
        ][:100]
        memory_summary = {
            "input_id": prepared["input_id"],
            "status": memory_result.get("status"),
            "source_id": memory_result.get("source_id"),
            "receipts": receipts,
        }
        review = {
            "decision": "remember",
            "answer": proposal["answer"],
            "memory_intake": memory_summary,
        }
        checkpoint.update({
            "phase": "completed",
            "question": None,
            "message": proposal["answer"],
            "reason": "user_confirmed_memory_intake",
            "proposal_review": review,
            "final_core_decision": {
                "next_step": "respond",
                "reason": "user_confirmed_memory_intake",
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
               VALUES ('ritsuko_core', 'core.magi.proposal_memory_reviewed',
                       %s, 'task', %s, %s)""",
            (
                task_id,
                task_id,
                Jsonb({
                    "input_id": prepared["input_id"],
                    "memory_status": memory_result.get("status"),
                    "decisions": [item.get("decision") for item in receipts],
                }),
            ),
        )
        return review


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
