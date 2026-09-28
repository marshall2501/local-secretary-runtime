"""Prototype helpers for the compositional Entity model.

Entity identity stays shallow. Attributes/events/states remain sourced Claims;
Entity-to-Entity relationships use secretary.entity_relations.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from psycopg.types.json import Jsonb

DBNAME = "secretary_pkb_proto_20260927"
WRITER = "secretary_pkb_proto_writer_20260927"

EVENT_PREDICATES = {
    "driver_updated",
    "servo_updated",
}
STATE_PREDICATES = {
    "current_driver",
    "current_servo",
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

EVENT_TO_STATE = {
    "driver_updated": "current_driver",
    "servo_updated": "current_servo",
}
STATEFUL_ENTITY_TYPES = {
    "gpu",
    "network_adapter",
    "rc_servo",
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


def advance_state_for_event(
    cur,
    *,
    entity_id: UUID,
    source_id: UUID,
    event_predicate: str,
    value: str,
    evidence: str,
    valid_from: datetime,
    recorded_at: datetime,
) -> str | None:
    """Advance current State for supported component Events in the same transaction.

    State succession closes the previous validity interval. It does not retract
    or supersede the historical Event that caused the state change.
    """
    state_predicate = EVENT_TO_STATE.get(event_predicate)
    if state_predicate is None:
        return None

    cur.execute(
        """SELECT entity_type FROM secretary.entities
           WHERE id=%s AND retired_at IS NULL""",
        (entity_id,),
    )
    row = cur.fetchone()
    if row is None or row[0] not in STATEFUL_ENTITY_TYPES:
        return None

    cur.execute(
        """SELECT id, valid_from
           FROM secretary.claims
           WHERE entity_id=%s AND predicate=%s
             AND semantic_kind='state'
             AND valid_to IS NULL AND retracted_at IS NULL
           FOR UPDATE""",
        (entity_id, state_predicate),
    )
    current = cur.fetchone()
    if current is not None:
        current_id, current_from = current
        if current_from >= valid_from:
            raise ValueError("state_time_not_monotonic")
        cur.execute(
            """UPDATE secretary.claims SET valid_to=%s
               WHERE id=%s AND valid_to IS NULL""",
            (valid_from, current_id),
        )
        if cur.rowcount != 1:
            raise RuntimeError("state_transition_lost_lock")

    cur.execute(
        """INSERT INTO secretary.claims
           (entity_id, source_id, claim_type, semantic_kind, predicate,
            value, evidence, origin, verification_status, valid_from, recorded_at)
           VALUES (%s,%s,'fact','state',%s,%s,%s,
                   'user_explicit','unverified',%s,%s)
           RETURNING id""",
        (
            entity_id, source_id, state_predicate, Jsonb(value),
            evidence, valid_from, recorded_at,
        ),
    )
    return str(cur.fetchone()[0])


def list_components(db, parent_entity_id: UUID) -> list[dict]:
    """Read current component relations with the current_driver State, if any."""
    if not _allowed(db):
        raise ValueError("Refusing non-isolated DB or non-dedicated writer")
    with db.cursor() as cur:
        cur.execute(
            """SELECT r.id AS relation_id,
                      p.id AS parent_id, p.name AS parent_name,
                      r.predicate AS relation_predicate,
                      c.id AS component_id, c.name AS component_name,
                      c.entity_type AS component_type,
                      st.value AS current_driver,
                      st.valid_from AS state_valid_from,
                      src.uri AS state_source_uri
               FROM secretary.entity_relations r
               JOIN secretary.entities p ON p.id=r.subject_entity_id
               JOIN secretary.entities c ON c.id=r.object_entity_id
               LEFT JOIN LATERAL (
                   SELECT cl.value, cl.valid_from, cl.source_id
                   FROM secretary.claims cl
                   WHERE cl.entity_id=c.id
                     AND cl.predicate='current_driver'
                     AND cl.semantic_kind='state'
                     AND cl.valid_to IS NULL
                     AND cl.retracted_at IS NULL
                   ORDER BY cl.valid_from DESC, cl.recorded_at DESC
                   LIMIT 1
               ) st ON TRUE
               LEFT JOIN secretary.sources src ON src.id=st.source_id
               WHERE r.subject_entity_id=%s
                 AND r.predicate='has_component'
                 AND r.valid_to IS NULL
                 AND r.retracted_at IS NULL
               ORDER BY c.name""",
            (parent_entity_id,),
        )
        cols = [d.name for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]
