"""PostgreSQL persistence adapter for initial RITSUKO request execution."""
from __future__ import annotations

from collections.abc import Callable
from uuid import UUID

from psycopg.types.json import Jsonb


class PostgresCoreExecutionRepository:
    def __init__(self, connect: Callable):
        self._connect = connect

    def persist_initial(
        self,
        *,
        task_id: UUID,
        request: str,
        scoped: dict,
        observation_pack: dict,
        advisor_shadow: dict,
        outcome: dict | None,
    ) -> None:
        baseline = {
            "member": "MELCHIOR",
            "status": scoped["status"],
            "selected_capability": scoped.get("capability"),
        }
        domain = scoped.get("domain") or "general"
        initial_status = "running" if scoped["status"] == "ready" else "waiting_external"
        checkpoint = {
            "core_slice": "daily_read_only_v1",
            "phase": "decide" if scoped["status"] == "ready" else "awaiting_clarification",
            "selected_capability": scoped.get("capability"),
            "question": scoped.get("question"),
            "reason": scoped.get("reason"),
            "observation_pack": observation_pack,
            "magi_baseline": baseline,
            "advisor_shadow": advisor_shadow,
        }
        with self._connect() as db:
            with db.transaction():
                with db.cursor() as cur:
                    cur.execute(
                        """INSERT INTO secretary.tasks
                           (id, request, requested_by, domain, completion_criteria,
                            permission_scope, status, checkpoint)
                           VALUES (%s, %s, 'local_user', %s, %s, %s, %s, %s)""",
                        (
                            task_id, request, domain,
                            "Return bounded local evidence with provenance or ask for clarification.",
                            Jsonb({
                                "pkb_read": True, "finance_read": True,
                                "web_research": True, "pkb_web_compare": True,
                                "external_actions": False,
                            }),
                            initial_status, Jsonb(checkpoint),
                        ),
                    )
                    if outcome is None:
                        cur.execute(
                            """INSERT INTO secretary.audit_events
                               (actor, event_type, task_id, object_type, object_id, details)
                               VALUES ('daily_core', 'core.awaiting_clarification',
                                       %s, 'task', %s, %s)""",
                            (task_id, task_id, Jsonb({
                                "reason": scoped["reason"],
                                "advisor_shadow": advisor_shadow,
                            })),
                        )
                        return

                    action_ids = []
                    result_ids = []
                    for attempt, execution in enumerate(outcome["executions"], start=1):
                        execution_result = execution["result"]
                        execution_total = int(execution.get("total") or 0)
                        cur.execute(
                            """INSERT INTO secretary.sources
                               (source_type, uri, citation, retrieved_at,
                                confidentiality, metadata)
                               VALUES ('tool', %s, %s, now(), 'private', %s)
                               RETURNING id""",
                            (
                                f"tool://daily-core/{execution['source_slug']}/{task_id}/{attempt}",
                                execution["citation"],
                                Jsonb({
                                    "task_id": str(task_id),
                                    "capability": execution["capability"],
                                    "query": request,
                                    "result_count": execution_total,
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
                               VALUES (%s, 'daily_core', %s, %s, %s,
                                       'read_only', 'localhost_read_only',
                                       'succeeded', %s, true, now(), now())
                               RETURNING id""",
                            (
                                task_id, execution["tool"], execution["operation"],
                                Jsonb({"query": request, "bounded": True, "step": attempt}),
                                f"daily-core:{task_id}:{execution['source_slug']}:{attempt}",
                            ),
                        )
                        action_id = cur.fetchone()[0]
                        action_ids.append(action_id)
                        cur.execute(
                            """INSERT INTO secretary.results
                               (action_id, source_id, outcome, summary, evidence,
                                verified_by, verified_at)
                               VALUES (%s, %s, %s, %s, %s, %s, now())
                               RETURNING id""",
                            (
                                action_id, source_id,
                                "success" if execution_total > 0 else "inconclusive",
                                execution["answer"],
                                Jsonb({
                                    "result_kind": execution_result.get("result_kind"),
                                    "total": execution_total,
                                    "data": execution_result,
                                }),
                                execution["verified_by"],
                            ),
                        )
                        result_ids.append(cur.fetchone()[0])

                    action_id = action_ids[-1]
                    result_id = result_ids[-1]
                    final_checkpoint = {
                        "core_slice": "daily_read_only_v1",
                        "phase": outcome["phase"],
                        "selected_capability": outcome["capability"],
                        "action_id": str(action_id), "result_id": str(result_id),
                        "action_ids": [str(value) for value in action_ids],
                        "result_ids": [str(value) for value in result_ids],
                        "result_count": outcome["total"],
                        "comparison": outcome["comparison"],
                        "question": outcome["question"],
                        "observation_pack": observation_pack,
                        "magi_baseline": baseline,
                        "advisor_shadow": advisor_shadow,
                    }
                    cur.execute(
                        """UPDATE secretary.tasks
                           SET status=%s, checkpoint=%s,
                               completed_at=CASE WHEN %s='completed' THEN now() ELSE NULL END
                           WHERE id=%s""",
                        (
                            outcome["task_status"], Jsonb(final_checkpoint),
                            outcome["task_status"], task_id,
                        ),
                    )
                    cur.execute(
                        """INSERT INTO secretary.audit_events
                           (actor, event_type, task_id, action_id,
                            object_type, object_id, details)
                           VALUES ('daily_core', %s, %s, %s, 'task', %s, %s)""",
                        (
                            "core.completed" if outcome["total"] > 0 else "core.needs_more_context",
                            task_id, action_id, task_id,
                            Jsonb({
                                "capability": outcome["capability"],
                                "result_count": outcome["total"],
                                "action_count": len(action_ids),
                                "comparison": outcome["comparison"],
                                "advisor_shadow": advisor_shadow,
                            }),
                        ),
                    )
