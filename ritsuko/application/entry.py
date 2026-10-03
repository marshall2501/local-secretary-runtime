"""Common RITSUKO application entry for natural-language secretary requests."""
from __future__ import annotations

from collections.abc import Callable
from uuid import UUID, uuid4


class RitsukoApplicationEntry:
    """Coordinate one request without depending on GUI, HTTP, or PostgreSQL."""

    def __init__(
        self,
        repository,
        *,
        load_context: Callable[[str], tuple[dict[str, dict], dict]],
        scope_request: Callable[[str, dict[str, dict], dict | None], dict],
        execute_read: Callable[[str, str], dict],
        execute_compare: Callable[..., dict],
        advisor_shadow_initial: Callable[[str | None, float], dict],
        queue_advisor: Callable[..., object],
    ):
        self._repository = repository
        self._load_context = load_context
        self._scope_request = scope_request
        self._execute_read = execute_read
        self._execute_compare = execute_compare
        self._advisor_shadow_initial = advisor_shadow_initial
        self._queue_advisor = queue_advisor

    def _execute_scoped(
        self, request: str, scoped: dict, *, target_override: str | None = None
    ) -> dict:
        capability = scoped["capability"]
        if capability == "pkb_web_compare":
            plan_result = self._execute_compare(request, target_override=target_override)
            executions = plan_result["executions"]
            answer = plan_result["answer"]
            comparison = plan_result["comparison"]
            result = {
                "status": "ok",
                "result_kind": "pkb_web_compare",
                "comparison": comparison,
            }
            total = sum(int(item.get("total") or 0) for item in executions)
        else:
            execution = self._execute_read(capability, request)
            executions = [execution]
            answer = execution["answer"]
            result = execution["result"]
            comparison = None
            total = execution["total"]
            plan_result = None

        enough = (
            all(int(item.get("total") or 0) > 0 for item in executions)
            and not (
                capability == "pkb_web_compare"
                and plan_result is not None
                and plan_result.get("needs_clarification")
            )
        )
        phase = "completed" if enough else "awaiting_clarification"
        task_status = "completed" if enough else "waiting_external"
        question = None if enough else (
            "比較に必要な記録またはWeb検索対象が不足しています。"
            "GPUのメーカー・モデルをPKBへ登録するか、依頼で明示してください。"
        )
        return {
            "capability": capability,
            "executions": executions,
            "answer": answer,
            "result": result,
            "comparison": comparison,
            "total": int(total),
            "enough": enough,
            "phase": phase,
            "task_status": task_status,
            "question": question,
            "plan_result": plan_result,
        }

    def request(
        self,
        text: str,
        *,
        advisor_model: str | None = None,
        advisor_timeout: float = 60.0,
        task_id: UUID | None = None,
    ) -> dict:
        request = text.strip()
        if not request:
            return {
                "status": "rejected",
                "phase": "input",
                "message": "依頼を入力してください。",
            }

        entities, observation_pack = self._load_context(request)
        scoped = self._scope_request(request, entities, observation_pack)
        advisor_shadow = self._advisor_shadow_initial(advisor_model, advisor_timeout)
        task_uuid = task_id or uuid4()
        outcome = self._execute_scoped(request, scoped) if scoped["status"] == "ready" else None

        self._repository.persist_initial(
            task_id=task_uuid,
            request=request,
            scoped=scoped,
            observation_pack=observation_pack,
            advisor_shadow=advisor_shadow,
            outcome=outcome,
        )

        baseline = {
            "member": "MELCHIOR",
            "status": scoped["status"],
            "selected_capability": scoped.get("capability"),
        }
        self._queue_advisor(
            task_uuid,
            request,
            baseline,
            observation_pack,
            advisor_model,
            advisor_timeout,
        )

        if outcome is None:
            return {
                "task_id": str(task_uuid),
                "status": "waiting_external",
                "phase": "awaiting_clarification",
                "message": "追加情報が必要です。",
                "question": scoped["question"],
                "selected_capability": scoped.get("capability"),
                "observation_pack": observation_pack,
                "magi_baseline": baseline,
                "advisor_shadow": advisor_shadow,
            }

        capability = outcome["capability"]
        result = outcome["result"]
        plan_result = outcome["plan_result"]
        return {
            "task_id": str(task_uuid),
            "status": outcome["task_status"],
            "phase": outcome["phase"],
            "message": outcome["answer"],
            "question": outcome["question"],
            "selected_capability": capability,
            "observation_pack": observation_pack,
            "magi_baseline": baseline,
            "capability_result": result,
            "comparison": outcome["comparison"],
            "advisor_shadow": advisor_shadow,
            "search": (
                plan_result["pkb"]
                if capability == "pkb_web_compare"
                else result if capability == "pkb_search" else None
            ),
            "finance": result if capability == "finance_read" else None,
            "web": (
                plan_result["web"]
                if capability == "pkb_web_compare"
                else result if capability == "web_research" else None
            ),
        }

    def resume(self, task_id: UUID, reply: str) -> dict:
        user_reply = reply.strip()
        if not user_reply:
            return {
                "task_id": str(task_id),
                "status": "waiting_external",
                "phase": "awaiting_clarification",
                "message": "追加回答を入力してください。",
            }

        with self._repository.resume_session(task_id) as session:
            state = session.state
            original_request = state["request"]
            current_domain = state["domain"]
            checkpoint = state["checkpoint"]
            entities, _ = self._load_context(original_request)

            prior_capability = checkpoint.get("selected_capability")
            if prior_capability == "pkb_web_compare":
                effective_request = original_request
                scoped = {
                    "status": "ready",
                    "capability": "pkb_web_compare",
                    "domain": current_domain or "pc",
                }
            else:
                effective_request = contextualize_reply(
                    original_request, user_reply, entities
                )
                scoped = self._scope_request(effective_request, entities, None)

            replies = list(checkpoint.get("user_replies") or [])
            replies.append(user_reply)
            session.audit_clarification(
                resolved=scoped["status"] == "ready",
                reason=scoped.get("reason"),
                reply_count=len(replies),
            )

            if scoped["status"] != "ready":
                next_checkpoint = {
                    **checkpoint,
                    "phase": "awaiting_clarification",
                    "question": scoped.get("question"),
                    "reason": scoped.get("reason"),
                    "user_replies": replies,
                    "effective_request": effective_request,
                }
                session.save_waiting_checkpoint(next_checkpoint)
                return {
                    "task_id": str(task_id),
                    "status": "waiting_external",
                    "phase": "awaiting_clarification",
                    "message": "まだ追加情報が必要です。",
                    "question": scoped["question"],
                }

            session.mark_running()
            outcome = self._execute_scoped(
                original_request if scoped["capability"] == "pkb_web_compare" else effective_request,
                scoped,
                target_override=(user_reply if scoped["capability"] == "pkb_web_compare" else None),
            )
            attempt = session.next_attempt()
            action_ids = []
            result_ids = []
            plan_result = outcome["plan_result"]
            for step_offset, execution in enumerate(outcome["executions"]):
                step = attempt + step_offset
                query = (
                    plan_result.get("web_query")
                    if (
                        scoped["capability"] == "pkb_web_compare"
                        and execution["capability"] == "web_research"
                        and plan_result is not None
                    )
                    else effective_request
                )
                action_id, result_id = session.record_execution(
                    execution=execution,
                    original_request=original_request,
                    user_reply=user_reply,
                    effective_request=effective_request,
                    query=query,
                    step=step,
                )
                action_ids.append(action_id)
                result_ids.append(result_id)

            action_id = action_ids[-1]
            result_id = result_ids[-1]
            next_checkpoint = {
                **checkpoint,
                "phase": outcome["phase"],
                "selected_capability": scoped["capability"],
                "action_id": str(action_id),
                "result_id": str(result_id),
                "action_ids": [str(value) for value in action_ids],
                "result_ids": [str(value) for value in result_ids],
                "result_count": outcome["total"],
                "comparison": outcome["comparison"],
                "question": outcome["question"],
                "reason": None,
                "user_replies": replies,
                "effective_request": effective_request,
            }
            session.finalize_resume(
                status=outcome["task_status"],
                domain=scoped.get("domain") or current_domain,
                checkpoint=next_checkpoint,
                action_id=action_id,
                event_type=(
                    "core.resumed_completed"
                    if outcome["total"] > 0
                    else "core.resumed_needs_more_context"
                ),
                details={
                    "capability": scoped["capability"],
                    "result_count": outcome["total"],
                    "reply_count": len(replies),
                    "action_count": len(action_ids),
                    "comparison": outcome["comparison"],
                },
            )

            result = outcome["result"]
            return {
                "task_id": str(task_id),
                "status": outcome["task_status"],
                "phase": outcome["phase"],
                "message": outcome["answer"],
                "question": outcome["question"],
                "selected_capability": scoped["capability"],
                "capability_result": result,
                "comparison": outcome["comparison"],
                "search": (
                    plan_result["pkb"]
                    if scoped["capability"] == "pkb_web_compare"
                    else result if scoped["capability"] == "pkb_search" else None
                ),
                "finance": result if scoped["capability"] == "finance_read" else None,
                "web": (
                    plan_result["web"]
                    if scoped["capability"] == "pkb_web_compare"
                    else result if scoped["capability"] == "web_research" else None
                ),
                "resumed": True,
                "effective_request": effective_request,
            }


def contextualize_reply(original_request: str, reply: str, entities: dict[str, dict]) -> str:
    """Carry one unique Entity from the original request into a short reply."""
    reply = reply.strip()
    if not reply:
        return reply
    ordered = sorted(entities.items(), key=lambda item: len(item[0]), reverse=True)
    if any(name in reply for name, _ in ordered):
        return reply
    original_mentions = [name for name, _ in ordered if name in original_request]
    if len(original_mentions) == 1:
        entity_name = original_mentions[0]
        if reply.startswith(("の", "について", "に関して")):
            return entity_name + reply
        return entity_name + "の" + reply
    return reply
