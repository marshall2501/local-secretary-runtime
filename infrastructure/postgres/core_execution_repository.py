"""PostgreSQL persistence adapter for initial RITSUKO request execution."""
from __future__ import annotations

from collections.abc import Callable
from contextlib import ExitStack
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


class _PostgresResumeSession:
    def __init__(self, connect: Callable, task_id: UUID):
        self._connect = connect
        self._task_id = task_id
        self._stack = None
        self._cur = None
        self.state = None

    def __enter__(self):
        self._stack = ExitStack()
        db = self._stack.enter_context(self._connect())
        self._stack.enter_context(db.transaction())
        self._cur = self._stack.enter_context(db.cursor())
        self._cur.execute(
            """SELECT id, request, domain, status, checkpoint
               FROM secretary.tasks WHERE id=%s FOR UPDATE""",
            (self._task_id,),
        )
        row = self._cur.fetchone()
        if row is None:
            self._stack.close()
            raise ValueError("Taskが見つかりません。")
        checkpoint = row[4] or {}
        if row[3] != "waiting_external":
            self._stack.close()
            raise ValueError("waiting_external のTaskだけ再開できます。")
        if checkpoint.get("core_slice") != "daily_read_only_v1":
            self._stack.close()
            raise ValueError("このTaskは日常Core最小縦断のTaskではありません。")
        self.state = {
            "request": row[1],
            "domain": row[2],
            "status": row[3],
            "checkpoint": checkpoint,
        }
        return self

    def __exit__(self, exc_type, exc, tb):
        return self._stack.__exit__(exc_type, exc, tb)

    def audit_clarification(self, *, resolved: bool, reason: str | None,
                            reply_count: int) -> None:
        self._cur.execute(
            """INSERT INTO secretary.audit_events
               (actor, event_type, task_id, object_type, object_id, details)
               VALUES ('daily_core', 'core.clarification_received',
                       %s, 'task', %s, %s)""",
            (
                self._task_id, self._task_id,
                Jsonb({"resolved": resolved, "reason": reason, "reply_count": reply_count}),
            ),
        )

    def save_waiting_checkpoint(self, checkpoint: dict) -> None:
        self._cur.execute(
            "UPDATE secretary.tasks SET checkpoint=%s WHERE id=%s",
            (Jsonb(checkpoint), self._task_id),
        )

    def mark_running(self) -> None:
        self._cur.execute(
            "UPDATE secretary.tasks SET status='running' WHERE id=%s",
            (self._task_id,),
        )

    def next_attempt(self) -> int:
        self._cur.execute(
            "SELECT count(*) FROM secretary.actions WHERE task_id=%s",
            (self._task_id,),
        )
        return int(self._cur.fetchone()[0]) + 1

    def record_execution(self, *, execution: dict, original_request: str,
                         user_reply: str, effective_request: str, query: str,
                         step: int) -> tuple[UUID, UUID]:
        execution_result = execution["result"]
        execution_total = int(execution.get("total") or 0)
        self._cur.execute(
            """INSERT INTO secretary.sources
               (source_type, uri, citation, retrieved_at, confidentiality, metadata)
               VALUES ('tool', %s, %s, now(), 'private', %s) RETURNING id""",
            (
                f"tool://daily-core/{execution['source_slug']}/{self._task_id}/{step}",
                "Secretary Core resumed " + execution["citation"],
                Jsonb({
                    "task_id": str(self._task_id),
                    "capability": execution["capability"],
                    "original_request": original_request,
                    "user_reply": user_reply,
                    "effective_request": effective_request,
                    "result_count": execution_total,
                    **(execution.get("source_metadata") or {}),
                }),
            ),
        )
        source_id = self._cur.fetchone()[0]
        self._cur.execute(
            """INSERT INTO secretary.actions
               (task_id, actor, tool, operation, parameters, risk,
                authorization_basis, status, idempotency_key, reversible,
                started_at, finished_at)
               VALUES (%s, 'daily_core', %s, %s, %s, 'read_only',
                       'localhost_read_only', 'succeeded', %s, true, now(), now())
               RETURNING id""",
            (
                self._task_id, execution["tool"], execution["operation"],
                Jsonb({"query": query, "bounded": True, "resumed": True, "step": step}),
                f"daily-core:{self._task_id}:{execution['source_slug']}:{step}",
            ),
        )
        action_id = self._cur.fetchone()[0]
        self._cur.execute(
            """INSERT INTO secretary.results
               (action_id, source_id, outcome, summary, evidence, verified_by, verified_at)
               VALUES (%s, %s, %s, %s, %s, %s, now()) RETURNING id""",
            (
                action_id, source_id,
                "success" if execution_total > 0 else "inconclusive",
                execution["answer"],
                Jsonb({
                    "result_kind": execution_result.get("result_kind"),
                    "total": execution_total,
                    "data": execution_result,
                    "resumed": True,
                }),
                execution["verified_by"],
            ),
        )
        result_id = self._cur.fetchone()[0]
        return action_id, result_id

    def finalize_resume(self, *, status: str, domain: str, checkpoint: dict,
                        action_id: UUID, event_type: str, details: dict) -> None:
        self._cur.execute(
            """UPDATE secretary.tasks
               SET status=%s, domain=%s, checkpoint=%s,
                   completed_at=CASE WHEN %s='completed' THEN now() ELSE NULL END
               WHERE id=%s""",
            (status, domain, Jsonb(checkpoint), status, self._task_id),
        )
        self._cur.execute(
            """INSERT INTO secretary.audit_events
               (actor, event_type, task_id, action_id, object_type, object_id, details)
               VALUES ('daily_core', %s, %s, %s, 'task', %s, %s)""",
            (event_type, self._task_id, action_id, self._task_id, Jsonb(details)),
        )


def _resume_session(self, task_id: UUID):
    return _PostgresResumeSession(self._connect, task_id)


PostgresCoreExecutionRepository.resume_session = _resume_session
