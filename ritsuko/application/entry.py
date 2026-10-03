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

    def _execute_scoped(self, request: str, scoped: dict) -> dict:
        capability = scoped["capability"]
        if capability == "pkb_web_compare":
            plan_result = self._execute_compare(request)
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
