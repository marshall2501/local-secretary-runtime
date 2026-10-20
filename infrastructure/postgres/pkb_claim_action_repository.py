# Provenance-Origin: original
# Provenance-AI: AI-assisted
"""Persist Task-scoped PKB write Actions before dispatch, and their Results after.

This uses the existing secretary.tasks/actions/results/sources/audit_events schema.
Unlike the read receipt, a write Action is reserved before touching the PKB.
All calls require an active, claimed Task attempt and an explicit PKB permission.
"""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, NAMESPACE_URL, uuid5

from psycopg.types.json import Jsonb

from ritsuko.core.magi_bridge import CORE_SLICE

OPERATION_ID = "pkb.register_claim"
ARGUMENT_KEYS = frozenset({
    "entity_id", "entity_mention", "predicate", "value",
    "evidence_start", "evidence_end",
})


def _task(cur, task_id: UUID, attempt_id: UUID) -> tuple[str, datetime]:
    cur.execute(
        """SELECT request, requested_by, status, permission_scope, checkpoint, created_at
           FROM secretary.tasks WHERE id=%s FOR UPDATE""",
        (task_id,),
    )
    row = cur.fetchone()
    if row is None:
        raise ValueError("unknown_task")
    request, requester, status, permissions, checkpoint, created_at = row
    if requester != "local_user":
        raise ValueError("pkb_write_requires_original_local_user_task")
    checkpoint = checkpoint or {}
    if (status != "running"
            or checkpoint.get("core_slice") != CORE_SLICE
            or checkpoint.get("active_attempt_id") != str(attempt_id)):
        raise ValueError("task_attempt_mismatch")
    if not isinstance(permissions, dict) or permissions.get("pkb_write") is not True:
        raise ValueError("pkb_write_not_permitted")
    if checkpoint.get("stop_requested") is True:
        raise ValueError("task_stop_requested")
    return request, created_at


def reserve(
    db, *, task_id: UUID, attempt_id: UUID, request_id: str,
    user_text: str, arguments: dict,
) -> dict:
    """Reserve one immutable Action for the MAGI request before PKB execution.

    A duplicate request is rejected, including a previously interrupted one.
    Recovery must reconcile that Action and PKB receipt instead of replaying a
    potentially committed write with new arguments or a new invocation ID.
    """
    if (not isinstance(request_id, str)
            or not request_id.startswith(f"REQ-{str(task_id)[:8]}-")
            or len(request_id) > 100 or not isinstance(arguments, dict)
            or set(arguments) != ARGUMENT_KEYS
            or not isinstance(user_text, str) or not user_text.strip()):
        raise ValueError("invalid_pkb_write_request")
    if (any(type(arguments[k]) is not str or not arguments[k].strip()
            for k in ("entity_id", "entity_mention", "predicate", "value"))
            or any(type(arguments[k]) is not int
                   for k in ("evidence_start", "evidence_end"))):
        raise ValueError("invalid_pkb_write_arguments")
    try:
        UUID(arguments["entity_id"])
    except (ValueError, TypeError, AttributeError) as exc:
        raise ValueError("invalid_pkb_entity_id") from exc
    from pkb.write_service import KNOWN_PREDICATES
    if arguments["predicate"] not in KNOWN_PREDICATES:
        raise ValueError("pkb_write_predicate_not_supported")
    if (not 0 <= arguments["evidence_start"] < arguments["evidence_end"] <= len(user_text)
            or arguments["entity_mention"] not in user_text[
                arguments["evidence_start"]:arguments["evidence_end"]
            ] or arguments["value"] not in user_text[
                arguments["evidence_start"]:arguments["evidence_end"]
            ]):
        raise ValueError("pkb_write_evidence_not_grounded")

    key = f"ritsuko-pkb:{task_id}:{request_id}"
    with db.transaction(), db.cursor() as cur:
        request, task_created_at = _task(cur, task_id, attempt_id)
        if request != user_text:
            raise ValueError("pkb_write_user_text_mismatch")
        cur.execute(
            "SELECT id FROM secretary.actions WHERE idempotency_key=%s",
            (key,),
        )
        if cur.fetchone() is not None:
            raise ValueError("pkb_write_already_reserved")
        cur.execute(
            """INSERT INTO secretary.actions
               (task_id, actor, tool, operation, parameters, risk,
                authorization_basis, status, idempotency_key, reversible, started_at)
               VALUES (%s, 'ritsuko_core', 'pkb', %s, %s, 'low',
                       'task_scoped_user_pkb_write', 'running', %s, false, now())
               RETURNING id""",
            (task_id, OPERATION_ID, Jsonb({
                "request_id": request_id, "arguments": arguments,
                "attempt_id": str(attempt_id),
            }), key),
        )
        action_id = cur.fetchone()[0]
        cur.execute(
            """INSERT INTO secretary.audit_events
               (actor,event_type,task_id,action_id,object_type,object_id,details)
               VALUES ('ritsuko_core','core.magi.pkb_write_reserved',
                       %s,%s,'task',%s,%s)""",
            (task_id, action_id, task_id, Jsonb({
                "operation_id": OPERATION_ID, "request_id": request_id,
            })),
        )
        # Task created_at is stable across crashes/retries; never use now()
        # as the original input timestamp for the PKB receipt.
        return {
            "action_id": str(action_id),
            "recorded_at": task_created_at,
            "occurred_at": task_created_at,
        }


def finish(
    db, *, task_id: UUID, attempt_id: UUID, action_id: UUID,
    execution: dict,
) -> tuple[str, str]:
    """Commit the PKB receipt as a Task Result; never complete the Task here."""
    if not isinstance(execution, dict) or execution.get("status") not in {
        "inserted", "replayed", "review", "rejected",
    }:
        raise ValueError("invalid_pkb_write_result")
    status = execution["status"]
    committed = status in {"inserted", "replayed"}
    if committed and not all(execution.get(k) for k in ("claim_id", "source_id")):
        raise ValueError("pkb_commit_receipt_missing")
    if any(k in execution for k in ("action_id", "result_id")):
        raise ValueError("model_supplied_result_identity_forbidden")
    outcome = "success" if committed else ("inconclusive" if status == "review" else "failure")
    with db.transaction(), db.cursor() as cur:
        _task(cur, task_id, attempt_id)
        cur.execute(
            """SELECT status, operation FROM secretary.actions
               WHERE id=%s AND task_id=%s FOR UPDATE""",
            (action_id, task_id),
        )
        row = cur.fetchone()
        if row != ("running", OPERATION_ID):
            raise ValueError("pkb_write_action_not_running")
        if committed:
            source_id = UUID(str(execution["source_id"]))
            cur.execute(
                "SELECT id FROM secretary.sources WHERE id=%s", (source_id,),
            )
            if cur.fetchone() is None:
                raise ValueError("pkb_write_source_missing")
        else:
            cur.execute(
                """INSERT INTO secretary.sources
                   (source_type,uri,citation,retrieved_at,confidentiality,metadata)
                   VALUES ('tool',%s,%s,now(),'private',%s) RETURNING id""",
                (
                    f"tool://ritsuko-magi/pkb-write/{task_id}/{action_id}",
                    "PKB structured registration was not committed",
                    Jsonb({"task_id": str(task_id), "action_id": str(action_id),
                           "write_status": status}),
                ),
            )
            source_id = cur.fetchone()[0]
        cur.execute(
            """INSERT INTO secretary.results
               (action_id,source_id,outcome,summary,evidence,error,verified_by,verified_at)
               VALUES (%s,%s,%s,%s,%s,%s,%s,
                       CASE WHEN %s THEN now() ELSE NULL END)
               RETURNING id""",
            (
                action_id, source_id, outcome,
                "PKB claim " + status, Jsonb({
                    "operation_id": OPERATION_ID,
                    "write_status": status,
                    "claim_id": execution.get("claim_id"),
                    "source_id": execution.get("source_id"),
                    "reason": str(execution.get("reason") or "")[:500],
                }),
                None if committed else str(execution.get("reason") or status)[:500],
                "pkb_repository_receipt" if committed else None, committed,
            ),
        )
        result_id = cur.fetchone()[0]
        cur.execute(
            """UPDATE secretary.actions SET status=%s,finished_at=now()
               WHERE id=%s""",
            ("succeeded" if committed else "failed", action_id),
        )
        cur.execute(
            """INSERT INTO secretary.audit_events
               (actor,event_type,task_id,action_id,object_type,object_id,details)
               VALUES ('ritsuko_core','core.magi.pkb_write_recorded',
                       %s,%s,'task',%s,%s)""",
            (task_id, action_id, task_id,
             Jsonb({"result_id": str(result_id), "write_status": status})),
        )
        return str(action_id), str(result_id)


def mark_unknown(
    db, *, task_id: UUID, action_id: UUID, error_type: str,
) -> tuple[str, str]:
    """Record a possibly dispatched write as UNKNOWN, never retry it blindly.

    An interrupted write may have committed in the PKB before its Task Result
    was saved. Recovery must reconcile the PKB receipt for this exact Action.
    This deliberately does not claim the user's Goal was fulfilled.
    """
    with db.transaction(), db.cursor() as cur:
        cur.execute(
            """SELECT status, operation FROM secretary.actions
               WHERE id=%s AND task_id=%s FOR UPDATE""",
            (action_id, task_id),
        )
        state = cur.fetchone()
        if state != ("running", OPERATION_ID):
            raise ValueError("pkb_write_action_not_running")
        cur.execute(
            """INSERT INTO secretary.sources
               (source_type,uri,citation,retrieved_at,confidentiality,metadata)
               VALUES ('tool',%s,%s,now(),'private',%s) RETURNING id""",
            (
                f"tool://ritsuko-magi/pkb-write-unknown/{task_id}/{action_id}",
                "PKB write outcome requires receipt reconciliation",
                Jsonb({"action_id": str(action_id), "error_type": str(error_type)[:80]}),
            ),
        )
        source_id = cur.fetchone()[0]
        cur.execute(
            """INSERT INTO secretary.results
               (action_id,source_id,outcome,summary,evidence,error)
               VALUES (%s,%s,'inconclusive',%s,%s,%s) RETURNING id""",
            (action_id, source_id, "PKB write outcome unknown",
             Jsonb({"operation_id": OPERATION_ID, "requires_reconciliation": True}),
             str(error_type)[:80]),
        )
        result_id = cur.fetchone()[0]
        cur.execute(
            """UPDATE secretary.actions SET status='unknown',finished_at=now()
               WHERE id=%s""", (action_id,),
        )
        cur.execute(
            """INSERT INTO secretary.audit_events
               (actor,event_type,task_id,action_id,object_type,object_id,details)
               VALUES ('ritsuko_core','core.magi.pkb_write_unknown',
                       %s,%s,'task',%s,%s)""",
            (task_id, action_id, task_id,
             Jsonb({"result_id": str(result_id), "error_type": str(error_type)[:80]})),
        )
        return str(action_id), str(result_id)



def load_reconciliation(db, *, task_id: UUID, action_id: UUID) -> dict:
    """Load the original persisted operation, never model-provided retry args."""
    with db.cursor() as cur:
        cur.execute(
            """SELECT t.request, t.requested_by, t.permission_scope, t.created_at,
                      a.status, a.operation, a.actor, a.authorization_basis,
                      a.parameters, a.idempotency_key
               FROM secretary.actions a
               JOIN secretary.tasks t ON t.id = a.task_id
               WHERE a.id=%s AND a.task_id=%s""",
            (action_id, task_id),
        )
        row = cur.fetchone()
    if row is None:
        raise ValueError("unknown_pkb_action")
    (request, requester, permissions, created_at, status, operation,
     actor, basis, parameters, key) = row
    if (requester != "local_user" or not isinstance(permissions, dict)
            or permissions.get("pkb_write") is not True):
        raise ValueError("pkb_write_not_permitted")
    if (operation != OPERATION_ID or actor != "ritsuko_core"
            or basis != "task_scoped_user_pkb_write"
            or status not in {"running", "unknown", "succeeded"}
            or not isinstance(parameters, dict)):
        raise ValueError("pkb_action_not_reconcilable")
    request_id = parameters.get("request_id")
    arguments = parameters.get("arguments")
    attempt_id = parameters.get("attempt_id")
    if (not isinstance(request_id, str)
            or not request_id.startswith(f"REQ-{str(task_id)[:8]}-")
            or key != f"ritsuko-pkb:{task_id}:{request_id}"
            or not isinstance(arguments, dict)
            or set(arguments) != ARGUMENT_KEYS
            or not isinstance(attempt_id, str)):
        raise ValueError("pkb_action_identity_mismatch")
    try:
        UUID(attempt_id)
    except (TypeError, ValueError, AttributeError) as exc:
        raise ValueError("pkb_action_attempt_invalid") from exc
    return {
        "user_text": request, "arguments": arguments,
        "recorded_at": created_at, "occurred_at": created_at,
        "request_id": request_id, "action_status": status,
    }


def reconcile_committed(
    db, *, task_id: UUID, action_id: UUID, receipt: dict,
) -> tuple[str, str]:
    """Record a verified PKB receipt without ever sending another write.

    Keep earlier UNKNOWN evidence as an audit record; a new success Result
    must reference the PKB's own Source. Task completion belongs to RITSUKO.
    """
    if (not isinstance(receipt, dict)
            or receipt.get("status") != "replayed"
            or not receipt.get("claim_id") or not receipt.get("source_id")):
        raise ValueError("pkb_reconciliation_requires_committed_receipt")
    claim_id = UUID(str(receipt["claim_id"]))
    source_id = UUID(str(receipt["source_id"]))
    input_id = str(uuid5(
        NAMESPACE_URL, f"local-secretary:pkb-claim:{task_id}:{action_id}",
    ))
    with db.transaction(), db.cursor() as cur:
        # Lock this Action: repeated reconciliation must return one Result.
        cur.execute(
            """SELECT status FROM secretary.actions
               WHERE id=%s AND task_id=%s AND operation=%s FOR UPDATE""",
            (action_id, task_id, OPERATION_ID),
        )
        state = cur.fetchone()
        if not state or state[0] not in {"running", "unknown", "succeeded"}:
            raise ValueError("pkb_action_not_reconcilable")
        context = load_reconciliation(db, task_id=task_id, action_id=action_id)
        if context["action_status"] != state[0]:
            raise ValueError("pkb_action_changed_during_reconciliation")
        cur.execute(
            """SELECT 1 FROM secretary.pkb_input_receipts
               WHERE input_id=%s AND source_id=%s AND claim_id=%s""",
            (input_id, source_id, claim_id),
        )
        if cur.fetchone() is None:
            raise ValueError("pkb_reconciliation_receipt_missing")
        if state[0] == "succeeded":
            cur.execute(
                """SELECT id FROM secretary.results
                   WHERE action_id=%s AND outcome='success'
                     AND verified_by IN (
                         'pkb_repository_receipt',
                         'pkb_repository_receipt_reconciled')
                   ORDER BY recorded_at DESC, id DESC LIMIT 1""",
                (action_id,),
            )
            row = cur.fetchone()
            if row is None:
                raise ValueError("pkb_reconciliation_succeeded_result_missing")
            return str(action_id), str(row[0])
        cur.execute(
            """INSERT INTO secretary.results
               (action_id,source_id,outcome,summary,evidence,verified_by,verified_at)
               VALUES (%s,%s,'success',%s,%s,%s,now()) RETURNING id""",
            (
                action_id, source_id, "PKB claim reconciled from existing receipt",
                Jsonb({
                    "operation_id": OPERATION_ID,
                    "write_status": "replayed",
                    "claim_id": str(claim_id), "source_id": str(source_id),
                    "reconciled_from": state[0],
                }),
                "pkb_repository_receipt_reconciled",
            ),
        )
        result_id = cur.fetchone()[0]
        cur.execute(
            """UPDATE secretary.actions SET status='succeeded',finished_at=now()
               WHERE id=%s""", (action_id,),
        )
        cur.execute(
            """INSERT INTO secretary.audit_events
               (actor,event_type,task_id,action_id,object_type,object_id,details)
               VALUES ('ritsuko_core','core.magi.pkb_write_reconciled',
                       %s,%s,'task',%s,%s)""",
            (task_id, action_id, task_id,
             Jsonb({"result_id": str(result_id), "claim_id": str(claim_id)})),
        )
        return str(action_id), str(result_id)


class PostgresPkbClaimActionStore:
    """Connection-owning adapter sharing the existing Secretary PostgreSQL DB."""

    def __init__(self, connect):
        self._connect = connect

    def reserve(self, **kwargs) -> dict:
        with self._connect() as db:
            return reserve(db, **kwargs)

    def finish(self, **kwargs) -> tuple[str, str]:
        with self._connect() as db:
            return finish(db, **kwargs)

    def mark_unknown(self, **kwargs) -> tuple[str, str]:
        with self._connect() as db:
            return mark_unknown(db, **kwargs)

    def load_reconciliation(self, **kwargs) -> dict:
        with self._connect() as db:
            return load_reconciliation(db, **kwargs)

    def reconcile_committed(self, **kwargs) -> tuple[str, str]:
        with self._connect() as db:
            return reconcile_committed(db, **kwargs)
