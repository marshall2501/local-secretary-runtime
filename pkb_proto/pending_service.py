"""Prototype-only persistence for PKB inputs requiring human review."""
from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

DBNAME = "secretary_pkb_proto_20260927"
WRITER = "secretary_pkb_proto_writer_20260927"


@dataclass(frozen=True)
class PendingResult:
    status: str
    reason: str
    pending_id: str | None = None


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
) -> PendingResult:
    if not _allowed(db):
        raise ValueError("Refusing non-isolated DB or non-dedicated writer")
    if not available(db):
        return PendingResult("review", reason, None)
    entity_uuid = UUID(entity_id) if entity_id else None
    with db.transaction(), db.cursor() as cur:
        cur.execute(
            """INSERT INTO secretary.pkb_pending_intake
               (input_id, raw_text, reason, entity_id, predicate, proposed_value)
               VALUES (%s,%s,%s,%s,%s,%s)
               ON CONFLICT (input_id) DO UPDATE SET input_id=EXCLUDED.input_id
               RETURNING id""",
            (input_id, raw_text, reason, entity_uuid, predicate, proposed_value),
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
                      e.name AS entity_name, p.predicate, p.proposed_value
               FROM secretary.pkb_pending_intake p
               LEFT JOIN secretary.entities e ON e.id=p.entity_id
               WHERE p.review_status='pending'
               ORDER BY p.recorded_at DESC
               LIMIT %s""",
            (limit,),
        )
        cols = [d.name for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]
