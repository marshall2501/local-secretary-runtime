"""PostgreSQL adapter for read-only RITSUKO Task history and trace."""
from __future__ import annotations

from collections.abc import Callable
from uuid import UUID


class PostgresCoreTaskQueryRepository:
    def __init__(self, connect: Callable):
        self._connect = connect

    @staticmethod
    def _summary(row, *, checkpoint_index: int, action_index: int,
                 result_index: int, completed_index: int | None = None) -> dict:
        checkpoint = row[checkpoint_index] or {}
        item = {
            "id": str(row[0]), "request": row[1], "status": row[2],
            "revision": row[3], "created_at": row[4], "updated_at": row[5],
            "core_slice": checkpoint.get("core_slice"), "phase": checkpoint.get("phase"),
            "selected_capability": checkpoint.get("selected_capability"),
            "question": checkpoint.get("question"), "message": checkpoint.get("message"),
            "effective_request": checkpoint.get("effective_request"),
            "user_replies": list(checkpoint.get("user_replies") or []),
            "comparison": checkpoint.get("comparison"),
            "advisor_shadow": checkpoint.get("advisor_shadow"),
            "action_count": row[action_index], "result_count": row[result_index],
        }
        if completed_index is not None:
            item["completed_at"] = row[completed_index]
        return item

    def recent(self, limit: int, offset: int) -> list[dict]:
        with self._connect() as db:
            with db.cursor() as cur:
                cur.execute(
                    """SELECT t.id, t.request, t.status, t.revision,
                              t.created_at, t.updated_at, t.completed_at,
                              t.checkpoint,
                              count(DISTINCT a.id) AS action_count,
                              count(DISTINCT r.id) AS result_count
                       FROM secretary.tasks t
                       LEFT JOIN secretary.actions a ON a.task_id=t.id
                       LEFT JOIN secretary.results r ON r.action_id=a.id
                       WHERE t.requested_by='local_user'
                         AND COALESCE(t.checkpoint->>'core_slice', '') IN
                             ('daily_read_only_v1','ritsuko_magi_observation_v1')
                       GROUP BY t.id
                       ORDER BY t.updated_at DESC, t.id DESC
                       LIMIT %s OFFSET %s""",
                    (limit, offset),
                )
                rows = cur.fetchall()
        result=[]
        for row in rows:
            checkpoint=row[7] or {}
            result.append({
                "id":str(row[0]),"request":row[1],"status":row[2],"revision":row[3],
                "created_at":row[4],"updated_at":row[5],"completed_at":row[6],
                "core_slice":checkpoint.get("core_slice"),"phase":checkpoint.get("phase"),
                "selected_capability":checkpoint.get("selected_capability"),
                "action_count":row[8],"result_count":row[9],
            })
        return result

    def open(self, limit: int, offset: int) -> list[dict]:
        with self._connect() as db:
            with db.cursor() as cur:
                cur.execute(
                    """SELECT t.id, t.request, t.status, t.revision,
                              t.created_at, t.updated_at, t.checkpoint,
                              count(DISTINCT a.id) AS action_count,
                              count(DISTINCT r.id) AS result_count
                       FROM secretary.tasks t
                       LEFT JOIN secretary.actions a ON a.task_id=t.id
                       LEFT JOIN secretary.results r ON r.action_id=a.id
                       WHERE t.requested_by='local_user'
                         AND COALESCE(t.checkpoint->>'core_slice', '') IN
                             ('daily_read_only_v1','ritsuko_magi_observation_v1')
                         AND t.status IN ('waiting_external', 'running', 'paused')
                       GROUP BY t.id
                       ORDER BY t.updated_at DESC, t.id DESC
                       LIMIT %s OFFSET %s""",
                    (limit, offset),
                )
                rows=cur.fetchall()
        result=[]
        for row in rows:
            checkpoint=row[6] or {}
            result.append({
                "id":str(row[0]),"request":row[1],"status":row[2],"revision":row[3],
                "created_at":row[4],"updated_at":row[5],
                "core_slice":checkpoint.get("core_slice"),"phase":checkpoint.get("phase"),
                "selected_capability":checkpoint.get("selected_capability"),
                "question":checkpoint.get("question"),"message":checkpoint.get("message"),
                "effective_request":checkpoint.get("effective_request"),
                "user_replies":list(checkpoint.get("user_replies") or []),
                "advisor_shadow":checkpoint.get("advisor_shadow"),
                "action_count":row[7],"result_count":row[8],
            })
        return result

    def completed(self, limit: int, offset: int) -> list[dict]:
        with self._connect() as db:
            with db.cursor() as cur:
                cur.execute(
                    """SELECT t.id, t.request, t.status, t.revision,
                              t.created_at, t.updated_at, t.completed_at,
                              t.checkpoint,
                              count(DISTINCT a.id) AS action_count,
                              count(DISTINCT r.id) AS result_count
                       FROM secretary.tasks t
                       LEFT JOIN secretary.actions a ON a.task_id=t.id
                       LEFT JOIN secretary.results r ON r.action_id=a.id
                       WHERE t.requested_by='local_user'
                         AND COALESCE(t.checkpoint->>'core_slice', '') IN
                             ('daily_read_only_v1','ritsuko_magi_observation_v1')
                         AND t.status='completed'
                       GROUP BY t.id
                       ORDER BY COALESCE(t.completed_at, t.updated_at) DESC, t.id DESC
                       LIMIT %s OFFSET %s""",
                    (limit, offset),
                )
                rows=cur.fetchall()
        result=[]
        for row in rows:
            checkpoint=row[7] or {}
            result.append({
                "id":str(row[0]),"request":row[1],"status":row[2],"revision":row[3],
                "created_at":row[4],"updated_at":row[5],"completed_at":row[6],
                "core_slice":checkpoint.get("core_slice"),"phase":checkpoint.get("phase"),
                "selected_capability":checkpoint.get("selected_capability"),
                "question":checkpoint.get("question"),"message":checkpoint.get("message"),
                "effective_request":checkpoint.get("effective_request"),
                "user_replies":list(checkpoint.get("user_replies") or []),
                "comparison":checkpoint.get("comparison"),
                "advisor_shadow":checkpoint.get("advisor_shadow"),
                "action_count":row[8],"result_count":row[9],
            })
        return result

    def trace(self, task_id: UUID) -> dict:
        with self._connect() as db:
            with db.cursor() as cur:
                cur.execute(
                    """SELECT id, request, domain, status, revision,
                              created_at, updated_at, completed_at, checkpoint
                       FROM secretary.tasks WHERE id=%s""",
                    (task_id,),
                )
                task=cur.fetchone()
                if task is None:
                    raise ValueError("Taskが見つかりません。")
                cur.execute(
                    """SELECT a.id, a.tool, a.operation, a.risk, a.status,
                              a.recorded_at, a.started_at, a.finished_at,
                              r.id, r.outcome, r.summary, r.verified_by,
                              r.verified_at, s.uri
                       FROM secretary.actions a
                       LEFT JOIN secretary.results r ON r.action_id=a.id
                       LEFT JOIN secretary.sources s ON s.id=r.source_id
                       WHERE a.task_id=%s ORDER BY a.recorded_at, a.id""",
                    (task_id,),
                )
                action_rows=cur.fetchall()
                cur.execute(
                    """SELECT event_type, occurred_at
                       FROM secretary.audit_events
                       WHERE task_id=%s
                         AND actor IN ('daily_core_advisor', 'ritsuko_core')
                       ORDER BY occurred_at, id""",
                    (task_id,),
                )
                advisor_event_rows=cur.fetchall()
        checkpoint=task[8] or {}
        return {
            "task": {
                "id":str(task[0]),"request":task[1],"domain":task[2],"status":task[3],
                "revision":task[4],"created_at":task[5],"updated_at":task[6],
                "completed_at":task[7],"core_slice":checkpoint.get("core_slice"),
                "phase":checkpoint.get("phase"),
                "selected_capability":checkpoint.get("selected_capability"),
                "question":checkpoint.get("question"),
                "effective_request":checkpoint.get("effective_request"),
                "user_replies":list(checkpoint.get("user_replies") or []),
                "observation_pack":checkpoint.get("observation_pack"),
                "magi_baseline":checkpoint.get("magi_baseline"),
                "advisor_shadow":checkpoint.get("advisor_shadow"),
                "message":checkpoint.get("message"),
                "cooperative_result":checkpoint.get("cooperative_result"),
                "cooperative_cycle":checkpoint.get("cooperative_cycle"),
                "reason":checkpoint.get("reason"),
                "final_core_decision":checkpoint.get("final_core_decision"),
                "result_count":checkpoint.get("result_count"),
                "magi_session":checkpoint.get("magi_session"),
                "proposal_review":checkpoint.get("proposal_review"),
                "proposal_memory_intake":checkpoint.get("proposal_memory_intake"),
                "user_resume":checkpoint.get("user_resume"),
            },
            "actions": [
                {
                    "action_id":str(row[0]),"tool":row[1],"operation":row[2],
                    "risk":row[3],"action_status":row[4],"recorded_at":row[5],
                    "started_at":row[6],"finished_at":row[7],
                    "result_id":str(row[8]) if row[8] else None,
                    "outcome":row[9],"summary":row[10],"verified_by":row[11],
                    "verified_at":row[12],"source_uri":row[13],
                } for row in action_rows
            ],
            "advisor_events": [
                {"event_type":row[0],"occurred_at":row[1]}
                for row in advisor_event_rows
            ],
        }
