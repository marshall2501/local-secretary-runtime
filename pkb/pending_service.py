"""Pure PKB Pending semantics and persistence-port delegates."""
from __future__ import annotations

from dataclasses import dataclass

SUPPORTED_ACCEPT_ACTIONS = {
    "driver_updated": ("更新した", "更新しておいた", "アップデートした"),
    "servo_updated": ("交換した", "取り替えた"),
}
BLOCKED_ACCEPT_TEXT = ("かもしれない", "未確認", "不明", "ではなく", "訂正", "らしい")


@dataclass(frozen=True)
class PendingResult:
    status: str
    reason: str
    pending_id: str | None = None
    claim_id: str | None = None
    source_id: str | None = None


def acceptance_eligible(row: dict) -> bool:
    """Only structured, explicit conflict rows may be manually promoted."""
    predicate = row.get("predicate")
    raw_text = row.get("raw_text") or ""
    value = row.get("proposed_value") or ""
    entity_name = row.get("entity_name") or ""
    return (
        row.get("review_status", "pending") == "pending"
        and row.get("reason") in {
            "existing_claim_requires_conflict_resolution",
            "model_candidate_needs_user_confirmation",
        }
        and bool(row.get("entity_id"))
        and predicate in SUPPORTED_ACCEPT_ACTIONS
        and bool(value)
        and entity_name in raw_text
        and value in raw_text
        and any(phrase in raw_text for phrase in SUPPORTED_ACCEPT_ACTIONS[predicate])
        and not any(marker in raw_text for marker in BLOCKED_ACCEPT_TEXT)
        and (
            row.get("reason") != "model_candidate_needs_user_confirmation"
            or (
                row.get("interpreter_kind") == "local_ollama"
                and bool(row.get("interpreter_model"))
            )
        )
    )


def enqueue(repository, **kwargs) -> PendingResult:
    return repository.enqueue_pending(**kwargs)


def list_pending(repository, limit: int = 50) -> list[dict]:
    return repository.list_pending(limit)


def accept_pending(repository, pending_id: str) -> PendingResult:
    return repository.accept_pending(pending_id)


def review_pending(repository, pending_id: str, decision: str) -> PendingResult:
    return repository.review_pending(pending_id, decision)


def list_reviewed(repository, limit: int = 20) -> list[dict]:
    return repository.list_reviewed(limit)
