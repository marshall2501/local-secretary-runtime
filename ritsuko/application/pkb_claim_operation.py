# Provenance-Origin: original
# Provenance-AI: AI-assisted
"""Execute one Task-scoped PKB write through a durable RITSUKO Action.

This is an Application operation, not another Task controller. MAGI proposes
the structured arguments, but only the Task store issues the Action ID and
checks persisted permission, attempt and original user statement.
"""
from __future__ import annotations

from uuid import UUID

from pkb.application.structured_claim import register_structured_claim

OPERATION_ID = "pkb.register_claim"


def invoke_pkb_claim(
    *, task_store, pkb_repository, task_id: UUID, attempt_id: UUID,
    request_id: str, user_text: str, arguments: dict,
) -> dict:
    """Reserve → invoke existing PKB writer → persist Result for MAGI.

    If anything fails after reservation, persist UNKNOWN when possible.
    Never generate a second Action ID or automatically resend the write.
    """
    reservation = task_store.reserve(
        task_id=task_id, attempt_id=attempt_id,
        request_id=request_id, user_text=user_text, arguments=arguments,
    )
    action_id = UUID(reservation["action_id"])
    try:
        result = register_structured_claim(
            pkb_repository,
            task_id=str(task_id), invocation_id=str(action_id),
            user_text=user_text,
            recorded_at=reservation["recorded_at"],
            occurred_at=reservation["occurred_at"],
            **arguments,
        )
        recorded_action_id, recorded_result_id = task_store.finish(
            task_id=task_id, attempt_id=attempt_id,
            action_id=action_id, execution=result,
        )
    except BaseException as exc:
        # Persisting UNKNOWN is more important than an ungrounded retry.
        # If the result was already committed before a transport error, the
        # store refuses to downgrade a succeeded Action to UNKNOWN.
        try:
            task_store.mark_unknown(
                task_id=task_id, action_id=action_id,
                error_type=type(exc).__name__,
            )
        except Exception:
            pass
        raise
    verified = result["status"] in {"inserted", "replayed"}
    return {
        "operation_id": OPERATION_ID,
        "source": "pkb_write",
        "capability": "pkb_structured_claim",
        "confidentiality": "private",
        "verified": verified,
        "verification_level": "pkb_repository_receipt" if verified else "not_committed",
        "text": (
            "PKB claim " + result["status"]
            + (": " + str(result["reason"]) if result.get("reason") else "")
        ),
        "result_kind": "pkb_claim_write",
        "write_status": result["status"],
        "claim_id": result.get("claim_id"),
        "source_id": result.get("source_id"),
        "action_id": str(recorded_action_id),
        "result_id": str(recorded_result_id),
        "responds_to": [request_id],
    }
