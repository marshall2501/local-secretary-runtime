"""Prototype helpers for the compositional Entity model.

Entity identity stays shallow. Attributes/events/states remain sourced Claims;
Entity-to-Entity relationships use secretary.entity_relations.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

DBNAME = "secretary_pkb_proto_20260927"
WRITER = "secretary_pkb_proto_writer_20260927"

EVENT_PREDICATES = {
    "driver_updated",
    "servo_updated",
}
STATE_PREDICATES = {
    "current_driver",
    "installed",
}
ATTRIBUTE_PREDICATES = {
    "manufacturer",
    "model",
    "serial_number",
    "purchase_date",
}
RELATION_PREDICATES = {
    "has_component",
    "owned_by",
    "belongs_to_project",
    "uses_account",
}


@dataclass(frozen=True)
class RelationResult:
    status: str
    reason: str
    relation_id: str | None = None


def classify_predicate(predicate: str) -> str | None:
    if predicate in EVENT_PREDICATES:
        return "event"
    if predicate in STATE_PREDICATES:
        return "state"
    if predicate in ATTRIBUTE_PREDICATES:
        return "attribute"
    return None


def _allowed(db) -> bool:
    info = db.info
    return (
        (info.dbname or "") == DBNAME
        and (info.host or "") in ("localhost", "127.0.0.1", "::1")
        and (info.user or "") == WRITER
    )


def create_relation(
    db,
    *,
    subject_entity_id: str,
    predicate: str,
    object_entity_id: str,
    source_id: str,
    valid_from: datetime,
) -> RelationResult:
    if not _allowed(db):
        raise ValueError("Refusing non-isolated DB or non-dedicated writer")
    if predicate not in RELATION_PREDICATES:
        return RelationResult("review", "relation_predicate_not_supported")
    if valid_from.utcoffset() is None:
        raise ValueError("valid_from must be timezone-aware")
    subject = UUID(subject_entity_id)
    obj = UUID(object_entity_id)
    source = UUID(source_id)
    if subject == obj:
        return RelationResult("rejected", "self_relation_not_allowed")

    with db.transaction(), db.cursor() as cur:
        cur.execute(
            """SELECT id FROM secretary.entities
               WHERE id IN (%s,%s) AND retired_at IS NULL""",
            (subject, obj),
        )
        if len(cur.fetchall()) != 2:
            return RelationResult("review", "relation_entity_missing_or_retired")

        cur.execute(
            """SELECT id FROM secretary.sources WHERE id=%s""",
            (source,),
        )
        if cur.fetchone() is None:
            return RelationResult("review", "relation_source_missing")

        cur.execute(
            """INSERT INTO secretary.entity_relations
               (subject_entity_id, predicate, object_entity_id, source_id, valid_from)
               VALUES (%s,%s,%s,%s,%s)
               ON CONFLICT DO NOTHING
               RETURNING id""",
            (subject, predicate, obj, source, valid_from),
        )
        row = cur.fetchone()
        if row is not None:
            return RelationResult("inserted", "relation_committed", str(row[0]))

        cur.execute(
            """SELECT id FROM secretary.entity_relations
               WHERE subject_entity_id=%s AND predicate=%s AND object_entity_id=%s
                 AND valid_to IS NULL AND retracted_at IS NULL
               ORDER BY recorded_at DESC LIMIT 1""",
            (subject, predicate, obj),
        )
        existing = cur.fetchone()
        return RelationResult(
            "replayed",
            "same_active_relation_already_exists",
            str(existing[0]) if existing else None,
        )
