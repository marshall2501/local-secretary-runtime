"""Prototype-only persistence for PKB inputs requiring human review."""
from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from psycopg.types.json import Jsonb

DBNAME = "secretary_pkb_proto_20260927"
WRITER = "secretary_pkb_proto_writer_20260927"

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


def _allowed(db) -> bool:
    info = db.info
    return (
        (info.dbname or "") == DBNAME
        and (info.host or "") in ("localhost", "127.0.0.1", "::1")
        and (info.user or "") == WRITER
    )


def available(db) -> bool:
    if not _allowed(db):
        raise ValueError("Refusing non-isolated DB or non-dedicated writer")
    with db.cursor() as cur:
        cur.execute("SELECT to_regclass('secretary.pkb_pending_intake')")
        return cur.fetchone()[0] is not None


def enqueue(
    db,
    *,
    input_id: str,
    raw_text: str,
    reason: str,
    entity_id: str | None = None,
    predicate: str | None = None,
    proposed_value: str | None = None,
    interpreter_kind: str | None = None,
    interpreter_model: str | None = None,
) -> PendingResult:
    if not _allowed(db):
        raise ValueError("Refusing non-isolated DB or non-dedicated writer")
    if not available(db):
        return PendingResult("review", reason, None)
    entity_uuid = UUID(entity_id) if entity_id else None
    with db.transaction(), db.cursor() as cur:
        cur.execute(
            """INSERT INTO secretary.pkb_pending_intake
               (input_id, raw_text, reason, entity_id, predicate, proposed_value,
                interpreter_kind, interpreter_model)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
               ON CONFLICT (input_id) DO UPDATE SET input_id=EXCLUDED.input_id
               RETURNING id""",
            (
                input_id, raw_text, reason, entity_uuid, predicate, proposed_value,
                interpreter_kind, interpreter_model,
            ),
        )
        pending_id = cur.fetchone()[0]
    return PendingResult("review", reason, str(pending_id))


def list_pending(db, limit: int = 50) -> list[dict]:
    if not _allowed(db):
        raise ValueError("Refusing non-isolated DB or non-dedicated writer")
    if not available(db):
        return []
    with db.cursor() as cur:
        cur.execute(
            """SELECT p.id, p.raw_text, p.reason, p.review_status, p.recorded_at,
                      p.entity_id, e.name AS entity_name, p.predicate, p.proposed_value,
                      p.interpreter_kind, p.interpreter_model
               FROM secretary.pkb_pending_intake p
               LEFT JOIN secretary.entities e ON e.id=p.entity_id
               WHERE p.review_status='pending'
               ORDER BY p.recorded_at DESC
               LIMIT %s""",
            (limit,),
        )
        cols = [d.name for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]



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


def accept_pending(db, pending_id: str) -> PendingResult:
    """Promote one explicitly structured Pending row into a Claim atomically."""
    if not _allowed(db):
        raise ValueError("Refusing non-isolated DB or non-dedicated writer")
    if not available(db):
        return PendingResult("review", "pending_storage_unavailable", None)
    try:
        pending_uuid = UUID(pending_id)
    except (ValueError, TypeError) as exc:
        raise ValueError("Invalid pending ID") from exc

    with db.transaction(), db.cursor() as cur:
        cur.execute(
            """SELECT p.id, p.input_id, p.raw_text, p.reason, p.entity_id,
                      p.predicate, p.proposed_value, p.review_status,
                      p.recorded_at, p.accepted_source_id, p.accepted_claim_id,
                      p.interpreter_kind, p.interpreter_model,
                      e.name AS entity_name, e.retired_at
               FROM secretary.pkb_pending_intake p
               LEFT JOIN secretary.entities e ON e.id=p.entity_id
               WHERE p.id=%s
               FOR UPDATE OF p""",
            (pending_uuid,),
        )
        row = cur.fetchone()
        if row is None:
            return PendingResult("review", "pending_item_not_found", pending_id)

        keys = (
            "id", "input_id", "raw_text", "reason", "entity_id", "predicate",
            "proposed_value", "review_status", "recorded_at",
            "accepted_source_id", "accepted_claim_id",
            "interpreter_kind", "interpreter_model",
            "entity_name", "retired_at",
        )
        item = dict(zip(keys, row))
        if item["review_status"] == "accepted":
            return PendingResult(
                "accepted", "pending_already_promoted", pending_id,
                str(item["accepted_claim_id"]), str(item["accepted_source_id"]),
            )
        if item["review_status"] != "pending":
            return PendingResult("review", "pending_item_already_resolved", pending_id)
        if item["retired_at"] is not None or not acceptance_eligible(item):
            return PendingResult("review", "pending_not_eligible_for_acceptance", pending_id)

        source_uri = "fixture://daily-pkb/pending/" + str(item["id"])
        cur.execute(
            """INSERT INTO secretary.sources
               (source_type, uri, citation, retrieved_at, recorded_at,
                confidentiality, metadata)
               VALUES ('user_statement',%s,%s,%s,%s,'private',%s)
               RETURNING id""",
            (
                source_uri,
                item["input_id"],
                item["recorded_at"],
                item["recorded_at"],
                Jsonb({
                    "fictional_only": True,
                    "pending_intake_id": str(item["id"]),
                    "original_text": item["raw_text"],
                    "promoted_by_user_review": True,
                    "interpreter_kind": item["interpreter_kind"],
                    "interpreter_model": item["interpreter_model"],
                }),
            ),
        )
        source_id = cur.fetchone()[0]

        cur.execute(
            """INSERT INTO secretary.claims
               (entity_id, source_id, claim_type, predicate, value, evidence,
                origin, verification_status, valid_from, recorded_at)
               VALUES (%s,%s,'fact',%s,%s,%s,'user_explicit','unverified',%s,now())
               RETURNING id""",
            (
                item["entity_id"],
                source_id,
                item["predicate"],
                Jsonb(item["proposed_value"]),
                item["raw_text"],
                item["recorded_at"],
            ),
        )
        claim_id = cur.fetchone()[0]

        cur.execute(
            """UPDATE secretary.pkb_pending_intake
               SET review_status='accepted', reviewed_at=now(),
                   accepted_source_id=%s, accepted_claim_id=%s
               WHERE id=%s AND review_status='pending'
               RETURNING id""",
            (source_id, claim_id, pending_uuid),
        )
        if cur.fetchone() is None:
            raise RuntimeError("Pending acceptance lost its lock-protected state")

    return PendingResult(
        "accepted", "pending_promoted_to_claim", pending_id,
        str(claim_id), str(source_id),
    )

def review_pending(db, pending_id: str, decision: str) -> PendingResult:
    """Resolve one pending input without promoting it to a Claim.

    This first review slice intentionally supports only reject / needs_edit.
    Accepted promotion needs a structured candidate and a separate safe path.
    """
    if not _allowed(db):
        raise ValueError("Refusing non-isolated DB or non-dedicated writer")
    if decision not in {"rejected", "needs_edit"}:
        raise ValueError("Only rejected or needs_edit is allowed in this review slice")
    if not available(db):
        return PendingResult("review", "pending_storage_unavailable", None)
    try:
        pending_uuid = UUID(pending_id)
    except (ValueError, TypeError) as exc:
        raise ValueError("Invalid pending ID") from exc
    with db.transaction(), db.cursor() as cur:
        cur.execute(
            """UPDATE secretary.pkb_pending_intake
               SET review_status=%s, reviewed_at=now()
               WHERE id=%s AND review_status='pending'
               RETURNING id""",
            (decision, pending_uuid),
        )
        row = cur.fetchone()
    if row is None:
        return PendingResult("review", "pending_item_not_found_or_already_resolved", pending_id)
    return PendingResult(decision, "pending_review_recorded", str(row[0]))


def list_reviewed(db, limit: int = 20) -> list[dict]:
    if not _allowed(db):
        raise ValueError("Refusing non-isolated DB or non-dedicated writer")
    if not available(db):
        return []
    with db.cursor() as cur:
        cur.execute(
            """SELECT p.id, p.raw_text, p.reason, p.review_status,
                      p.recorded_at, p.reviewed_at,
                      e.name AS entity_name
               FROM secretary.pkb_pending_intake p
               LEFT JOIN secretary.entities e ON e.id=p.entity_id
               WHERE p.review_status <> 'pending'
               ORDER BY p.reviewed_at DESC NULLS LAST, p.recorded_at DESC
               LIMIT %s""",
            (limit,),
        )
        cols = [d.name for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]
