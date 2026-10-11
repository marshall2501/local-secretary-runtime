"""PostgreSQL adapter for one explicitly-approved external Action on a MAGI Task.

Uses the pre-existing secretary.tasks/approvals/actions/results/sources/audit_events
schema. No migration, new DB role, or external HTTP call occurs here.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from uuid import UUID

from psycopg.types.json import Jsonb

from ritsuko.core.magi_bridge import CORE_SLICE


class PostgresExternalActionStore:
    def __init__(self, connect):
        self._connect = connect

    @staticmethod
    def _task(cur, task_id):
        cur.execute(
            """SELECT status, permission_scope, checkpoint
               FROM secretary.tasks WHERE id=%s FOR UPDATE""", (task_id,),
        )
        row = cur.fetchone()
        if row is None:
            raise ValueError("unknown_task")
        status, scope, checkpoint = row
        if not isinstance(scope, dict) or scope.get("external_actions") is not True:
            raise ValueError("external_action_not_permitted")
        if not isinstance(checkpoint, dict) or checkpoint.get("core_slice") != CORE_SLICE:
            raise ValueError("not_magi_observation_task")
        return status, deepcopy(checkpoint)

    @staticmethod
    def _approval(cur, approval_id, task_id):
        cur.execute(
            """SELECT status, scope, expires_at FROM secretary.approvals
               WHERE id=%s AND task_id=%s AND approval_kind='external_action'
               FOR UPDATE""", (approval_id, task_id),
        )
        row = cur.fetchone()
        if row is None:
            raise ValueError("unknown_external_action_approval")
        return row

    @staticmethod
    def _action(cur, approval_id, task_id):
        cur.execute(
            """SELECT id, status FROM secretary.actions
               WHERE approval_id=%s AND task_id=%s FOR UPDATE""",
            (approval_id, task_id),
        )
        row = cur.fetchone()
        if row is None:
            raise ValueError("unknown_external_action")
        return row

    @staticmethod
    def _record_audit(cur, *, task_id, approval_id, action_id, event_type, details):
        cur.execute(
            """INSERT INTO secretary.audit_events
               (actor, event_type, task_id, approval_id, action_id,
                object_type, object_id, details)
               VALUES ('ritsuko_core', %s, %s, %s, %s, 'task', %s, %s)""",
            (event_type, task_id, approval_id, action_id, task_id, Jsonb(details)),
        )

    def offer(self, *, task_id: UUID, operation: dict, arguments: dict,
              requested_by: str, expires_at: datetime) -> dict:
        if arguments.keys() != {"value"} or not isinstance(arguments["value"], str):
            raise ValueError("invalid_external_action_arguments")
        if operation.get("required_permission") != "external_actions":
            raise ValueError("invalid_operation_permission")
        if expires_at <= datetime.now(timezone.utc):
            raise ValueError("approval_already_expired")
        with self._connect() as db, db.transaction(), db.cursor() as cur:
            status, checkpoint = self._task(cur, task_id)
            if status not in {"running", "waiting_external"}:
                raise ValueError("task_not_ready_for_action")
            if checkpoint.get("external_action") is not None:
                raise ValueError("external_action_already_offered")
            session = checkpoint.get("magi_session")
            if not isinstance(session, dict) or session.get("status") != "proposal_ready":
                raise ValueError("action_proposal_not_ready")
            detail = session.get("detail")
            if (not isinstance(detail, dict)
                    or detail.get("state") != "ACTION_PROPOSAL"
                    or not isinstance(detail.get("action_candidate"), str)
                    or not detail["action_candidate"].strip()):
                raise ValueError("magi_action_proposal_missing")
            # The model's free-form explanation is evidence, NOT executable
            # operation data. Only the registered descriptor and user-reviewed
            # structured arguments enter the approval snapshot.
            snapshot = {
                "operation": deepcopy(operation),
                "arguments": deepcopy(arguments),
                "proposal_text": detail["action_candidate"][:500],
                "confirmation_basis": "trusted_local_explicit_approval",
            }
            cur.execute(
                """INSERT INTO secretary.approvals
                   (approval_kind, task_id, status, scope, requested_by, expires_at)
                   VALUES ('external_action', %s, 'pending', %s, %s, %s)
                   RETURNING id""",
                (task_id, Jsonb(snapshot), requested_by, expires_at),
            )
            approval_id = cur.fetchone()[0]
            checkpoint["external_action"] = {
                "approval_id": str(approval_id), "status": "pending",
                "operation_id": operation["operation_id"],
                "resource_id": operation["resource_id"],
            }
            cur.execute(
                """UPDATE secretary.tasks
                   SET status='waiting_approval', checkpoint=%s, completed_at=NULL
                   WHERE id=%s""",
                (Jsonb(checkpoint), task_id),
            )
            self._record_audit(
                cur, task_id=task_id, approval_id=approval_id, action_id=None,
                event_type="external_action.approval_requested",
                details={"operation_id": operation["operation_id"],
                         "resource_id": operation["resource_id"]},
            )
            return {"approval_id": str(approval_id), "status": "pending",
                    "operation": deepcopy(operation), "arguments": deepcopy(arguments)}

    def decide(self, *, task_id: UUID, approval_id: UUID, approved: bool,
               actor: str) -> dict:
        with self._connect() as db, db.transaction(), db.cursor() as cur:
            status, checkpoint = self._task(cur, task_id)
            if status != "waiting_approval":
                raise ValueError("task_not_waiting_approval")
            state = checkpoint.get("external_action")
            if not isinstance(state, dict) or state.get("approval_id") != str(approval_id):
                raise ValueError("approval_task_mismatch")
            approval_status, snapshot, expires_at = self._approval(
                cur, approval_id, task_id,
            )
            if approval_status != "pending":
                raise ValueError("approval_already_decided")
            if expires_at <= datetime.now(timezone.utc):
                raise ValueError("approval_expired")
            decision = "approved" if approved else "rejected"
            cur.execute(
                """UPDATE secretary.approvals
                   SET status=%s, decided_by=%s, decided_at=now()
                   WHERE id=%s AND status='pending'""",
                (decision, actor, approval_id),
            )
            action_id = None
            if approved:
                operation = snapshot["operation"]
                arguments = snapshot["arguments"]
                cur.execute(
                    """INSERT INTO secretary.actions
                       (task_id, approval_id, actor, tool, operation, parameters,
                        risk, authorization_basis, status, idempotency_key, reversible)
                       VALUES (%s, %s, %s, %s, %s, %s, 'high',
                               'explicit_external_action_approval', 'planned',
                               %s, false)
                       RETURNING id""",
                    (task_id, approval_id, actor, operation["service_id"],
                     operation["operation_id"],
                     Jsonb({"resource_id": operation["resource_id"], **arguments}),
                     f"external-action:{task_id}:{approval_id}"),
                )
                action_id = cur.fetchone()[0]
            state.update(status=decision, action_id=str(action_id) if action_id else None)
            checkpoint["external_action"] = state
            cur.execute(
                """UPDATE secretary.tasks SET status=%s, checkpoint=%s
                   WHERE id=%s""",
                ("waiting_approval" if approved else "waiting_external",
                 Jsonb(checkpoint), task_id),
            )
            self._record_audit(
                cur, task_id=task_id, approval_id=approval_id,
                action_id=action_id, event_type="external_action.approval_decided",
                details={"decision": decision},
            )
            return {"approval_id": str(approval_id), "status": decision,
                    "action_id": str(action_id) if action_id else None}

    def claim(self, *, task_id: UUID, approval_id: UUID) -> dict:
        with self._connect() as db, db.transaction(), db.cursor() as cur:
            status, checkpoint = self._task(cur, task_id)
            state = checkpoint.get("external_action") or {}
            if (status != "waiting_approval" or
                    state.get("approval_id") != str(approval_id) or
                    state.get("status") != "approved"):
                raise ValueError("action_not_claimable")
            approval_status, snapshot, expires_at = self._approval(
                cur, approval_id, task_id,
            )
            if approval_status != "approved" or expires_at <= datetime.now(timezone.utc):
                raise ValueError("action_approval_invalid_or_expired")
            action_id, action_status = self._action(cur, approval_id, task_id)
            if action_status != "planned":
                raise ValueError("action_already_claimed")
            cur.execute(
                """UPDATE secretary.actions SET status='running', started_at=now()
                   WHERE id=%s AND status='planned'""", (action_id,),
            )
            state["status"] = "running"
            checkpoint["external_action"] = state
            cur.execute(
                """UPDATE secretary.tasks SET status='running', checkpoint=%s
                   WHERE id=%s""", (Jsonb(checkpoint), task_id),
            )
            self._record_audit(
                cur, task_id=task_id, approval_id=approval_id,
                action_id=action_id, event_type="external_action.execution_claimed",
                details={"idempotency_key": f"external-action:{task_id}:{approval_id}"},
            )
            return deepcopy(snapshot)

    def _end(self, *, task_id: UUID, approval_id: UUID, verified: bool,
             evidence: dict) -> dict:
        with self._connect() as db, db.transaction(), db.cursor() as cur:
            task_status_before, checkpoint = self._task(cur, task_id)
            state = checkpoint.get("external_action") or {}
            if task_status_before != "running":
                raise ValueError("task_not_running")
            if state.get("approval_id") != str(approval_id) or state.get("status") != "running":
                raise ValueError("action_not_running")
            action_id, action_status = self._action(cur, approval_id, task_id)
            if action_status != "running":
                raise ValueError("action_not_running")
            outcome = "success" if verified else "inconclusive"
            action_status = "succeeded" if verified else "unknown"
            task_status = "completed" if verified else "waiting_external"
            cur.execute(
                """INSERT INTO secretary.sources
                   (source_type, uri, citation, retrieved_at, confidentiality, metadata)
                   VALUES ('tool', %s, %s, now(), 'private', %s)
                   RETURNING id""",
                (f"tool://external-action/{task_id}/{action_id}",
                 "External API action verification observation",
                 Jsonb({"task_id": str(task_id), "approval_id": str(approval_id),
                        "verification": evidence.get("verification", "unknown")})),
            )
            source_id = cur.fetchone()[0]
            cur.execute(
                """INSERT INTO secretary.results
                   (action_id, source_id, outcome, summary, evidence,
                    verified_by, verified_at)
                   VALUES (%s, %s, %s, %s, %s, %s, %s)""",
                (action_id, source_id, outcome,
                 "Readback matched expected value" if verified else "Result uncertain",
                 Jsonb(evidence), "fixture_readback" if verified else None,
                 datetime.now(timezone.utc) if verified else None),
            )
            cur.execute(
                """UPDATE secretary.actions SET status=%s, finished_at=now()
                   WHERE id=%s""", (action_status, action_id),
            )
            state["status"] = action_status
            checkpoint["external_action"] = state
            checkpoint["final_core_decision"] = {
                "next_step": "respond" if verified else "verify_or_recover",
                "reason": "action_readback_verified" if verified else "action_outcome_unknown",
                "task_status": task_status,
            }
            cur.execute(
                """UPDATE secretary.tasks SET status=%s, checkpoint=%s,
                   completed_at=CASE WHEN %s='completed' THEN now() ELSE NULL END
                   WHERE id=%s""",
                (task_status, Jsonb(checkpoint), task_status, task_id),
            )
            self._record_audit(
                cur, task_id=task_id, approval_id=approval_id,
                action_id=action_id,
                event_type="external_action.verified" if verified else "external_action.unknown",
                details={"verified": verified, "status": action_status},
            )
            return {"task_id": str(task_id), "approval_id": str(approval_id),
                    "action_id": str(action_id), "status": action_status,
                    "verified": verified, "task_status": task_status}

    def finish(self, *, task_id: UUID, approval_id: UUID,
               verified: bool, evidence: dict) -> dict:
        return self._end(task_id=task_id, approval_id=approval_id,
                         verified=verified, evidence=evidence)

    def mark_unknown(self, *, task_id: UUID, approval_id: UUID,
                     error_type: str) -> dict:
        return self._end(task_id=task_id, approval_id=approval_id,
                         verified=False,
                         evidence={"verification": "unconfirmed",
                                   "error_type": str(error_type)[:80]})
