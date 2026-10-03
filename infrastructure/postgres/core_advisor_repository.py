"""PostgreSQL persistence adapter for RITSUKO cooperative Advisor state."""
from __future__ import annotations

from uuid import UUID

from psycopg.types.json import Jsonb


def write_advisor_shadow(connection_factory, task_id: UUID, shadow: dict,
                         event_type: str) -> bool:
    with connection_factory() as db:
        with db.transaction():
            with db.cursor() as cur:
                cur.execute(
                    """UPDATE secretary.tasks
                       SET checkpoint=jsonb_set(
                           COALESCE(checkpoint, '{}'::jsonb),
                           '{advisor_shadow}', %s, true)
                       WHERE id=%s""",
                    (Jsonb(shadow), task_id),
                )
                if cur.rowcount != 1:
                    return False
                cur.execute(
                    """INSERT INTO secretary.audit_events
                       (actor, event_type, task_id, object_type, object_id, details)
                       VALUES ('daily_core_advisor', %s, %s, 'task', %s, %s)""",
                    (event_type, task_id, task_id, Jsonb({"advisor_shadow": shadow})),
                )
    return True


def claim_cooperative_probe(connection_factory, task_id: UUID, capability: str) -> bool:
    with connection_factory() as db:
        with db.transaction():
            with db.cursor() as cur:
                cur.execute(
                    """SELECT status, checkpoint FROM secretary.tasks
                       WHERE id=%s FOR UPDATE""", (task_id,),
                )
                row=cur.fetchone()
                if row is None:
                    return False
                status, checkpoint=row[0], row[1] or {}
                if status != "waiting_external" or list(checkpoint.get("user_replies") or []):
                    return False
                next_checkpoint={
                    **checkpoint, "phase":"act", "selected_capability":capability,
                    "question":None, "reason":"magi_cooperative_probe",
                    "cooperative_cycle":1,
                    "cooperative_resume":{
                        "phase":checkpoint.get("phase"),
                        "selected_capability":checkpoint.get("selected_capability"),
                        "question":checkpoint.get("question"),
                        "reason":checkpoint.get("reason"),
                    },
                }
                cur.execute(
                    """UPDATE secretary.tasks SET status='running', checkpoint=%s WHERE id=%s""",
                    (Jsonb(next_checkpoint), task_id),
                )
                cur.execute(
                    """INSERT INTO secretary.audit_events
                       (actor,event_type,task_id,object_type,object_id,details)
                       VALUES ('daily_core','core.magi.probe_claimed',%s,'task',%s,%s)""",
                    (task_id,task_id,Jsonb({"capability":capability,"cycle":1})),
                )
    return True


def record_cooperative_probe(connection_factory, task_id: UUID, request: str,
                             execution: dict, observation_pack: dict) -> tuple[UUID, UUID]:
    execution_result=execution["result"]
    execution_total=int(execution.get("total") or 0)
    with connection_factory() as db:
        with db.transaction():
            with db.cursor() as cur:
                cur.execute(
                    """INSERT INTO secretary.sources
                       (source_type,uri,citation,retrieved_at,confidentiality,metadata)
                       VALUES ('tool',%s,%s,now(),'private',%s) RETURNING id""",
                    (
                        f"tool://daily-core/{execution['source_slug']}/{task_id}/magi-1",
                        execution["citation"],
                        Jsonb({
                            "task_id":str(task_id),"capability":execution["capability"],
                            "query":request,"result_count":execution_total,
                            "magi_cooperative":True,"cycle":1,
                            **(execution.get("source_metadata") or {}),
                        }),
                    ),
                )
                source_id=cur.fetchone()[0]
                cur.execute(
                    """INSERT INTO secretary.actions
                       (task_id,actor,tool,operation,parameters,risk,authorization_basis,
                        status,idempotency_key,reversible,started_at,finished_at)
                       VALUES (%s,'daily_core',%s,%s,%s,'read_only','magi_local_pkb_read',
                               'succeeded',%s,true,now(),now()) RETURNING id""",
                    (
                        task_id,execution["tool"],execution["operation"],
                        Jsonb({"query":request,"bounded":True,"magi_cooperative":True,"cycle":1}),
                        f"daily-core:{task_id}:magi:{execution['source_slug']}:1",
                    ),
                )
                action_id=cur.fetchone()[0]
                cur.execute(
                    """INSERT INTO secretary.results
                       (action_id,source_id,outcome,summary,evidence,verified_by,verified_at)
                       VALUES (%s,%s,%s,%s,%s,%s,now()) RETURNING id""",
                    (
                        action_id,source_id,
                        "success" if execution_total > 0 else "inconclusive",
                        execution["answer"],
                        Jsonb({
                            "result_kind":execution_result.get("result_kind"),
                            "total":execution_total,"data":execution_result,
                            "magi_cooperative":True,"cycle":1,
                        }),
                        execution["verified_by"],
                    ),
                )
                result_id=cur.fetchone()[0]
                cur.execute(
                    """SELECT checkpoint FROM secretary.tasks WHERE id=%s FOR UPDATE""",
                    (task_id,),
                )
                checkpoint=(cur.fetchone() or [{}])[0] or {}
                action_ids=list(checkpoint.get("action_ids") or [])+[str(action_id)]
                result_ids=list(checkpoint.get("result_ids") or [])+[str(result_id)]
                next_checkpoint={
                    **checkpoint,"phase":"orient","selected_capability":execution["capability"],
                    "action_id":str(action_id),"result_id":str(result_id),
                    "action_ids":action_ids,"result_ids":result_ids,
                    "result_count":execution_total,"observation_pack":observation_pack,
                    "cooperative_result":execution_result,"cooperative_cycle":2,
                }
                cur.execute(
                    """UPDATE secretary.tasks SET checkpoint=%s WHERE id=%s""",
                    (Jsonb(next_checkpoint),task_id),
                )
                cur.execute(
                    """INSERT INTO secretary.audit_events
                       (actor,event_type,task_id,action_id,object_type,object_id,details)
                       VALUES ('daily_core','core.magi.probe_observed',%s,%s,'task',%s,%s)""",
                    (task_id,action_id,task_id,Jsonb({
                        "capability":execution["capability"],"result_count":execution_total,"cycle":1,
                    })),
                )
    return action_id,result_id


def finalize_cooperative_probe(connection_factory, task_id: UUID, final_decision: dict,
                               final_observation_pack: dict, execution: dict) -> None:
    next_step=final_decision["next_step"]
    task_status="completed" if next_step=="respond" else "waiting_external"
    phase="completed" if next_step=="respond" else "awaiting_clarification"
    with connection_factory() as db:
        with db.transaction():
            with db.cursor() as cur:
                cur.execute("SELECT checkpoint FROM secretary.tasks WHERE id=%s FOR UPDATE",(task_id,))
                row=cur.fetchone()
                if row is None:
                    return
                checkpoint=row[0] or {}
                next_checkpoint={
                    **checkpoint,"phase":phase,"selected_capability":execution["capability"],
                    "question":final_decision.get("question"),"message":final_decision.get("message"),
                    "reason":final_decision.get("reason"),"observation_pack":final_observation_pack,
                    "cooperative_result":execution.get("result"),"cooperative_cycle":2,
                    "final_core_decision":{**final_decision,"task_status":task_status},
                }
                cur.execute(
                    """UPDATE secretary.tasks SET status=%s,checkpoint=%s,
                       completed_at=CASE WHEN %s='completed' THEN now() ELSE NULL END WHERE id=%s""",
                    (task_status,Jsonb(next_checkpoint),task_status,task_id),
                )
                cur.execute(
                    """INSERT INTO secretary.audit_events
                       (actor,event_type,task_id,object_type,object_id,details)
                       VALUES ('daily_core',%s,%s,'task',%s,%s)""",
                    (
                        "core.magi.responded" if next_step=="respond" else "core.magi.clarify_after_probe",
                        task_id,task_id,Jsonb({
                            "next_step":next_step,"capability":execution["capability"],
                            "reason":final_decision.get("reason"),"cycle":2,
                        }),
                    ),
                )


def fail_cooperative_probe(connection_factory, task_id: UUID, error: str) -> None:
    with connection_factory() as db:
        with db.transaction():
            with db.cursor() as cur:
                cur.execute("SELECT checkpoint FROM secretary.tasks WHERE id=%s FOR UPDATE",(task_id,))
                row=cur.fetchone()
                if row is None:
                    return
                checkpoint=row[0] or {}
                next_checkpoint={
                    **checkpoint,"phase":"failed","question":None,
                    "reason":"magi_cooperative_probe_failed","cooperative_error":error,
                }
                cur.execute(
                    """UPDATE secretary.tasks SET status='failed',checkpoint=%s WHERE id=%s""",
                    (Jsonb(next_checkpoint),task_id),
                )


def restore_interrupted_cooperative_probe(connection_factory, task_id: UUID,
                                           error: str) -> bool:
    with connection_factory() as db:
        with db.transaction():
            with db.cursor() as cur:
                cur.execute(
                    """SELECT status,checkpoint FROM secretary.tasks WHERE id=%s FOR UPDATE""",
                    (task_id,),
                )
                row=cur.fetchone()
                if row is None:
                    return False
                status,checkpoint=row[0],row[1] or {}
                if status!="running" or checkpoint.get("core_slice")!="daily_read_only_v1":
                    return False
                if (checkpoint.get("reason")!="magi_cooperative_probe"
                        and checkpoint.get("cooperative_cycle") not in {1,2}):
                    return False
                resume=checkpoint.get("cooperative_resume")
                if not isinstance(resume,dict):
                    resume={}
                next_checkpoint={
                    **checkpoint,
                    "phase":resume.get("phase") or "awaiting_clarification",
                    "selected_capability":resume.get("selected_capability"),
                    "question":resume.get("question") or "バックグラウンドAdvisorが中断されました。同じTaskを再開してください。",
                    "reason":resume.get("reason") or "advisor_interrupted",
                    "cooperative_error":error,"cooperative_interrupted":True,
                }
                cur.execute(
                    """UPDATE secretary.tasks SET status='waiting_external',checkpoint=%s,
                       completed_at=NULL WHERE id=%s""",
                    (Jsonb(next_checkpoint),task_id),
                )
                cur.execute(
                    """INSERT INTO secretary.audit_events
                       (actor,event_type,task_id,object_type,object_id,details)
                       VALUES ('daily_core','core.advisor.task_restored',%s,'task',%s,%s)""",
                    (task_id,task_id,Jsonb({"error":error})),
                )
    return True
