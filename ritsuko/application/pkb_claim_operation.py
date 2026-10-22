# Provenance-Origin: original
# Provenance-AI: AI-assisted
"""Execute one Task-scoped PKB write through a durable RITSUKO Action.

This is an Application operation, not another Task controller. MAGI proposes
the structured arguments, but only the Task store issues the Action ID and
checks persisted permission, attempt and original user statement.
"""
from __future__ import annotations

from uuid import UUID

from pkb.application.structured_claim import (
    register_structured_claim, lookup_structured_claim_receipt,
)
from pkb.write_service import normalize_explicit_attribute_evidence

OPERATION_ID = "pkb.register_claim"


def require_verified_pkb_write_target(
    user_text: str, arguments: dict, session: dict,
) -> None:
    """実PKB読取で解決した単一EntityだけをAction予約前に許可する。

    MAGIが任意のUUIDを生成しても書込み先には採用しない。PKBの
    実読取結果からRITSUKOが作成した検証済みObservationと照合する。
    """
    if not isinstance(arguments, dict) or not isinstance(session, dict):
        raise ValueError("pkb_write_target_not_verified")
    target = arguments.get("entity_id")
    mention = arguments.get("entity_mention")
    if not isinstance(target, str) or not isinstance(mention, str):
        raise ValueError("pkb_write_target_not_verified")
    if not mention.strip() or mention not in user_text:
        raise ValueError("pkb_write_target_not_verified")
    try:
        target_id = UUID(target)
    except (TypeError, ValueError, AttributeError) as exc:
        raise ValueError("pkb_write_target_not_verified") from exc

    observed = []
    for observation in session.get("observations") or []:
        if not isinstance(observation, dict):
            continue
        if (observation.get("source") != "pkb"
                or observation.get("verified") is not True
                or not observation.get("action_id")
                or not observation.get("result_id")):
            continue
        resolved = observation.get("resolved_entity")
        if not isinstance(resolved, dict):
            continue
        if resolved.get("resolution") not in {
            "pkb_unique_explicit_name", "pkb_component_relation",
        }:
            continue
        try:
            resolved_id = UUID(str(resolved["id"]))
        except (KeyError, TypeError, ValueError, AttributeError):
            continue
        resolved_name = resolved.get("name")
        if not isinstance(resolved_name, str) or not resolved_name.strip():
            continue
        observed.append((resolved_id, resolved_name, resolved["resolution"]))

    # Ambiguous or absent observations must not be resolved by the model.
    if not observed or {row[0] for row in observed} != {target_id}:
        raise ValueError("pkb_write_target_not_verified")
    # An ordinary name-based search only authorizes the exact name in the
    # original request. Component IDs are already resolved by the PKB's
    # deterministic parent/relation read, and may have different display names.
    if any(
        resolution == "pkb_unique_explicit_name" and name != mention
        for _, name, resolution in observed
    ):
        raise ValueError("pkb_write_target_not_verified")


def invoke_pkb_claim(
    *, task_store, pkb_repository, task_id: UUID, attempt_id: UUID,
    request_id: str, user_text: str, arguments: dict,
) -> dict:
    """Reserve → invoke existing PKB writer → persist Result for MAGI.

    If anything fails after reservation, persist UNKNOWN when possible.
    Never generate a second Action ID or automatically resend the write.
    """
    # Derive final evidence offsets from the immutable user text before the
    # durable Action reservation. Persist exactly the arguments later sent to
    # PKB; receipt reconciliation must never regenerate a different payload.
    grounded_arguments = normalize_explicit_attribute_evidence(
        user_text, arguments,
    )
    reservation = task_store.reserve(
        task_id=task_id, attempt_id=attempt_id,
        request_id=request_id, user_text=user_text, arguments=grounded_arguments,
    )
    action_id = UUID(reservation["action_id"])
    try:
        result = register_structured_claim(
            pkb_repository,
            task_id=str(task_id), invocation_id=str(action_id),
            user_text=user_text,
            recorded_at=reservation["recorded_at"],
            occurred_at=reservation["occurred_at"],
            **grounded_arguments,
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


def reconcile_pkb_claim(
    *, task_store, pkb_repository, task_id: UUID, action_id: UUID,
) -> dict | None:
    """Reconcile exactly one persisted Action; never re-dispatch a Claim write.

    Return None when PKB has no authoritative matching receipt. UNKNOWN then
    remains unresolved and must not be treated as successful or re-sent.
    """
    context = task_store.load_reconciliation(
        task_id=task_id, action_id=action_id,
    )
    receipt = lookup_structured_claim_receipt(
        pkb_repository, task_id=str(task_id), invocation_id=str(action_id),
        user_text=context["user_text"],
        recorded_at=context["recorded_at"],
        occurred_at=context["occurred_at"],
        **context["arguments"],
    )
    if receipt is None:
        return None
    action, result = task_store.reconcile_committed(
        task_id=task_id, action_id=action_id, receipt=receipt,
    )
    return {
        "operation_id": OPERATION_ID,
        "source": "pkb_write",
        "capability": "pkb_structured_claim",
        "confidentiality": "private",
        "verified": True,
        "verification_level": "pkb_repository_receipt_reconciled",
        "text": "PKB claim reconciled from existing receipt",
        "result_kind": "pkb_claim_write",
        "write_status": "replayed",
        "claim_id": receipt["claim_id"],
        "source_id": receipt["source_id"],
        "action_id": str(action),
        "result_id": str(result),
        "responds_to": [context["request_id"]],
    }
