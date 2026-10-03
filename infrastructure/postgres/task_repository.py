"""PostgreSQL adapter for the current RITSUKO Task API use cases."""
from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from uuid import UUID

from psycopg.types.json import Jsonb

from ritsuko.tasks.service import TaskConflictError, TaskNotFoundError


class PostgresTaskRepository:
    def __init__(self, connect: Callable):
        self._connect = connect

    def create_task(self, *, request: str, actor: str, domain: str,
                    completion_criteria: str, entity_id: UUID | None,
                    due_at: datetime | None) -> dict:
        with self._connect() as db:
            with db.cursor() as cur:
                cur.execute(
                    """INSERT INTO secretary.tasks
                       (request, requested_by, domain, completion_criteria,
                        entity_id, due_at, status)
                       VALUES (%s, %s, %s, %s, %s, %s, 'pending')
                       RETURNING id, status, revision, created_at""",
                    (request, actor, domain, completion_criteria, entity_id, due_at),
                )
                created = cur.fetchone()
                cur.execute(
                    """INSERT INTO secretary.audit_events
                       (actor, event_type, object_type, object_id)
                       VALUES (%s, 'task.created', 'task', %s)""",
                    (actor, created["id"]),
                )
                return created

    def create_prototype_task(self, *, request: str, actor: str, domain: str,
                              completion_criteria: str, entity_id: UUID | None,
                              due_at: datetime | None,
                              steps: tuple[str, ...]) -> dict:
        with self._connect() as db:
            with db.cursor() as cur:
                cur.execute(
                    """INSERT INTO secretary.tasks
                       (request, requested_by, domain, completion_criteria,
                        entity_id, due_at, status)
                       VALUES (%s, %s, %s, %s, %s, %s, 'pending')
                       RETURNING id, status, revision, created_at""",
                    (request, actor, domain, completion_criteria, entity_id, due_at),
                )
                created = cur.fetchone()
                created_steps = []
                for step_order, description in enumerate(steps):
                    cur.execute(
                        """INSERT INTO secretary.task_steps
                           (task_id, step_order, description)
                           VALUES (%s, %s, %s)
                           RETURNING id, step_order, description, status""",
                        (created["id"], step_order, description),
                    )
                    created_steps.append(cur.fetchone())
                cur.execute(
                    """INSERT INTO secretary.audit_events
                       (actor, event_type, task_id, object_type, object_id)
                       VALUES (%s, 'prototype.task_created', %s, 'task', %s)""",
                    (actor, created["id"], created["id"]),
                )
        return {**created, "steps": created_steps, "phase": "planned"}

    def run_prototype_task(self, task_id: UUID, actor: str,
                           steps_contract: tuple[str, ...]) -> dict:
        with self._connect() as db:
            with db.cursor() as cur:
                cur.execute(
                    """SELECT id, domain, entity_id, completion_criteria, status, checkpoint
                       FROM secretary.tasks WHERE id = %s FOR UPDATE""",
                    (task_id,),
                )
                task = cur.fetchone()
                if task is None:
                    raise TaskNotFoundError("Task not found.")
                checkpoint = task["checkpoint"] or {}
                if task["status"] in ("completed", "waiting_external") and "g1" in checkpoint:
                    return checkpoint["g1"]
                if task["status"] != "pending":
                    raise TaskConflictError("Prototype can only run pending tasks.")

                cur.execute(
                    """SELECT id, step_order, status
                       FROM secretary.task_steps WHERE task_id = %s
                       ORDER BY step_order FOR UPDATE""",
                    (task_id,),
                )
                steps = cur.fetchall()
                if (len(steps) != len(steps_contract)
                        or [row["step_order"] for row in steps] != list(range(len(steps_contract)))
                        or any(row["status"] != "pending" for row in steps)):
                    raise TaskConflictError("Not a fresh G1 prototype task.")

                cur.execute(
                    """SELECT c.id, e.name AS entity_name, c.predicate, c.value,
                              c.verification_status, s.citation
                       FROM secretary.current_claims c
                       JOIN secretary.entities e ON e.id = c.entity_id
                       JOIN secretary.sources s ON s.id = c.source_id
                       WHERE e.domain = %s
                         AND (%s::uuid IS NULL OR c.entity_id = %s::uuid)
                       ORDER BY c.recorded_at DESC, c.id DESC LIMIT 20""",
                    (task["domain"], task["entity_id"], task["entity_id"]),
                )
                recalled = [{**row, "id": str(row["id"])} for row in cur.fetchall()]
                cur.execute(
                    """SELECT r.id, r.outcome, r.summary, a.tool, a.operation
                       FROM secretary.results r
                       JOIN secretary.actions a ON a.id = r.action_id
                       JOIN secretary.tasks t ON t.id = a.task_id
                       WHERE t.domain = %s AND t.id <> %s
                       ORDER BY r.recorded_at DESC, r.id DESC LIMIT 10""",
                    (task["domain"], task_id),
                )
                previous_results = [{**row, "id": str(row["id"])} for row in cur.fetchall()]
                cur.execute(
                    """UPDATE secretary.task_steps
                       SET status = 'completed', checkpoint = %s WHERE id = %s""",
                    (Jsonb({"recalled_claim_count": len(recalled),
                            "previous_result_count": len(previous_results)}), steps[0]["id"]),
                )
                cur.execute(
                    """UPDATE secretary.task_steps
                       SET status = 'completed', checkpoint = %s WHERE id = %s""",
                    (Jsonb({"plan": "simulated_read_only_diagnostic"}), steps[1]["id"]),
                )
                cur.execute(
                    """INSERT INTO secretary.sources
                       (source_type, uri, citation, retrieved_at, confidentiality, metadata)
                       VALUES ('tool', %s, %s, now(), 'private', %s)
                       RETURNING id""",
                    (f"tool://prototype/simulated-read-only/{task_id}",
                     "G1 simulated diagnostic; no real PC was inspected",
                     Jsonb({"prototype": True, "simulated": True})),
                )
                source_id = cur.fetchone()["id"]
                cur.execute(
                    """INSERT INTO secretary.actions
                       (task_id, step_id, actor, tool, operation, parameters,
                        risk, authorization_basis, status, idempotency_key,
                        reversible, started_at, finished_at)
                       VALUES (%s, %s, %s, 'prototype_mock', 'simulated_read_only',
                               %s, 'read_only', 'prototype_fixture_only', 'succeeded',
                               %s, true, now(), now())
                       RETURNING id""",
                    (task_id, steps[2]["id"], actor, Jsonb({"simulated": True}),
                     f"g1:{task_id}:simulated_read_only"),
                )
                action_id = cur.fetchone()["id"]
                result_summary = "Simulated diagnostic recorded; no actual PC inspected."
                cur.execute(
                    """INSERT INTO secretary.results
                       (action_id, source_id, outcome, summary, evidence, verified_by, verified_at)
                       VALUES (%s, %s, 'success', %s, %s, 'prototype_fixture', now())
                       RETURNING id""",
                    (action_id, source_id, result_summary,
                     Jsonb({"simulated": True, "recalled_claim_count": len(recalled)})),
                )
                result_id = cur.fetchone()["id"]
                cur.execute(
                    """UPDATE secretary.task_steps
                       SET status = 'completed', checkpoint = %s WHERE id = %s""",
                    (Jsonb({"action_id": str(action_id), "result_id": str(result_id),
                            "simulated": True}), steps[2]["id"]),
                )
                criterion = task["completion_criteria"].lower()
                criteria_met = (("模擬診断" in criterion and "記録" in criterion)
                                or "record a simulated diagnosis" in criterion)
                final_status = "completed" if criteria_met else "waiting_external"
                if criteria_met:
                    cur.execute(
                        """UPDATE secretary.task_steps
                           SET status = 'completed', checkpoint = %s WHERE id = %s""",
                        (Jsonb({"criteria_met": True, "scope": "simulated_result_only"}),
                         steps[3]["id"]),
                    )
                else:
                    cur.execute(
                        """UPDATE secretary.task_steps
                           SET checkpoint = %s WHERE id = %s""",
                        (Jsonb({"criteria_met": False,
                                "reason": "requires human verification of free-text criteria"}),
                         steps[3]["id"]),
                    )
                summary = {
                    "task_id": str(task_id), "status": final_status,
                    "phase": "recorded" if criteria_met else "needs_verification",
                    "simulated": True, "criteria_met": criteria_met,
                    "recalled_claims": recalled, "previous_results": previous_results,
                    "action_id": str(action_id), "result_id": str(result_id),
                    "result_summary": result_summary,
                }
                cur.execute(
                    """UPDATE secretary.tasks
                       SET status = %s, completed_at = CASE WHEN %s THEN now() ELSE NULL END,
                           checkpoint = %s WHERE id = %s""",
                    (final_status, criteria_met, Jsonb({"g1": summary}), task_id),
                )
                cur.execute(
                    """INSERT INTO secretary.audit_events
                       (actor, event_type, task_id, action_id, object_type, object_id, details)
                       VALUES (%s, 'prototype.mock_completed', %s, %s, 'task', %s, %s)""",
                    (actor, task_id, action_id, task_id,
                     Jsonb({"simulated": True, "criteria_met": criteria_met})),
                )
                return summary

    def read_prototype_task(self, task_id: UUID) -> dict:
        with self._connect() as db:
            with db.cursor() as cur:
                cur.execute(
                    """SELECT id, request, domain, completion_criteria, status,
                              revision, checkpoint, created_at, updated_at
                       FROM secretary.tasks WHERE id = %s""",
                    (task_id,),
                )
                task = cur.fetchone()
                if task is None:
                    raise TaskNotFoundError("Task not found.")
                cur.execute(
                    """SELECT id, step_order, description, status, checkpoint,
                              attempt_count, last_error, updated_at
                       FROM secretary.task_steps WHERE task_id = %s
                       ORDER BY step_order""",
                    (task_id,),
                )
                steps = cur.fetchall()
        checkpoint = task.get("checkpoint") or {}
        phase = checkpoint.get("g1", {}).get("phase", "planned")
        return {**task, "steps": steps, "phase": phase}

    def transition_task(self, task_id: UUID, revision: int,
                        expected_statuses: tuple[str, ...], destination: str,
                        actor: str) -> dict:
        with self._connect() as db:
            with db.cursor() as cur:
                cur.execute(
                    """UPDATE secretary.tasks SET status = %s
                       WHERE id = %s AND revision = %s AND status = ANY(%s)
                       RETURNING id, status, revision, updated_at""",
                    (destination, task_id, revision, list(expected_statuses)),
                )
                updated = cur.fetchone()
                if updated is None:
                    raise TaskConflictError(
                        "Task absent, revision changed, or transition not allowed."
                    )
                cur.execute(
                    """INSERT INTO secretary.audit_events
                       (actor, event_type, task_id, object_type, object_id)
                       VALUES (%s, %s, %s, 'task', %s)""",
                    (actor, f"task.{destination}", task_id, task_id),
                )
                return updated
