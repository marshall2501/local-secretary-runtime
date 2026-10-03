"""Prototype helpers for the compositional Entity model.

Entity identity stays shallow. Attributes/events/states remain sourced Claims;
Entity-to-Entity relationships use secretary.entity_relations.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from psycopg.types.json import Jsonb
from .memory_registry import EFFECT_RULES
from config.runtime_database import allowed_daily_connection

EVENT_PREDICATES = {
    "driver_updated",
    "servo_updated",
    "os_release_changed",
}
STATE_PREDICATES = {
    "current_driver",
    "current_servo",
    "current_os_release",
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

EVENT_TO_STATE = {key: rule.state_predicate for key, rule in EFFECT_RULES.items()}
STATEFUL_ENTITY_TYPES = {
    "gpu",
    "network_adapter",
    "rc_servo",
}
COMPONENT_ROLE_TOKENS = {
    "GPU": "primary_gpu",
    "NIC": "wired_nic",
}
ROLE_TO_HUMAN_TOKEN = {value: key for key, value in COMPONENT_ROLE_TOKENS.items()}


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
    return allowed_daily_connection(db)


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
    value: str | dict,
    evidence: str,
    valid_from: datetime,
    recorded_at: datetime,
) -> str | None:
    """Advance current State for supported component Events in the same transaction.

    State succession closes the previous validity interval. It does not retract
    or supersede the historical Event that caused the state change.
    """
    rule = EFFECT_RULES.get(event_predicate)
    if rule is None:
        return None
    state_predicate = rule.state_predicate
    value = rule.select(value)

    cur.execute(
        """SELECT entity_type FROM secretary.entities
           WHERE id=%s AND retired_at IS NULL""",
        (entity_id,),
    )
    row = cur.fetchone()
    if row is None or row[0] not in rule.entity_types:
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
                      r.relation_role,
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


def resolve_component_reference(db, parent_name: str, role_token: str) -> dict | None:
    """Resolve a human parent+role phrase to one active component Entity."""
    if not _allowed(db):
        raise ValueError("Refusing non-isolated DB or non-dedicated writer")
    role = COMPONENT_ROLE_TOKENS.get(role_token)
    if role is None:
        return None
    with db.cursor() as cur:
        cur.execute(
            """SELECT c.id, c.name, c.entity_type, r.relation_role
               FROM secretary.entity_relations r
               JOIN secretary.entities p ON p.id=r.subject_entity_id
               JOIN secretary.entities c ON c.id=r.object_entity_id
               WHERE p.name=%s
                 AND p.retired_at IS NULL
                 AND c.retired_at IS NULL
                 AND r.predicate='has_component'
                 AND r.relation_role=%s
                 AND r.valid_to IS NULL
                 AND r.retracted_at IS NULL""",
            (parent_name, role),
        )
        rows = cur.fetchall()
    if len(rows) != 1:
        return None
    row = rows[0]
    return {
        "id": str(row[0]),
        "name": row[1],
        "entity_type": row[2],
        "relation_role": row[3],
        "parent_name": parent_name,
        "role_token": role_token,
    }


def authoritative_entity_aliases(cur) -> dict[str, set[str]]:
    """Build aliases only from authoritative Entity names and active Relations.

    Human phrases like "メインPCのGPU" resolve to the normalized child Entity
    (e.g. GPU1) without making the parent-specific phrase the Entity identity.
    """
    cur.execute(
        """SELECT id, name
           FROM secretary.entities
           WHERE retired_at IS NULL"""
    )
    aliases = {str(entity_id): {name} for entity_id, name in cur.fetchall()}

    cur.execute(
        """SELECT p.name, c.id, r.relation_role
           FROM secretary.entity_relations r
           JOIN secretary.entities p ON p.id=r.subject_entity_id
           JOIN secretary.entities c ON c.id=r.object_entity_id
           WHERE r.predicate='has_component'
             AND r.relation_role IS NOT NULL
             AND r.valid_to IS NULL
             AND r.retracted_at IS NULL
             AND p.retired_at IS NULL
             AND c.retired_at IS NULL"""
    )
    for parent_name, child_id, relation_role in cur.fetchall():
        token = ROLE_TO_HUMAN_TOKEN.get(relation_role)
        if token is None:
            continue
        aliases.setdefault(str(child_id), set()).add(f"{parent_name}の{token}")
    return aliases


def load_entity_detail(db, entity_id: str) -> dict | None:
    """Build one generic Entity detail view from Entity/Claim/Relation/Source data."""
    if not _allowed(db):
        raise ValueError("Refusing non-isolated DB or non-dedicated writer")
    entity_uuid = UUID(entity_id)

    with db.cursor() as cur:
        cur.execute(
            """SELECT id, name, domain, entity_type
               FROM secretary.entities
               WHERE id=%s AND retired_at IS NULL""",
            (entity_uuid,),
        )
        entity = cur.fetchone()
        if entity is None:
            return None

        cur.execute(
            """SELECT c.id, c.predicate, c.value, c.semantic_kind,
                      c.valid_from, c.valid_to, c.recorded_at,
                      c.verification_status, s.uri
               FROM secretary.claims c
               JOIN secretary.sources s ON s.id=c.source_id
               WHERE c.entity_id=%s
                 AND c.retracted_at IS NULL
                 AND c.semantic_kind IN ('state','attribute')
               ORDER BY
                 CASE WHEN c.valid_to IS NULL THEN 0 ELSE 1 END,
                 c.valid_from DESC, c.recorded_at DESC""",
            (entity_uuid,),
        )
        current = []
        history = []
        for row in cur.fetchall():
            item = {
                "id": str(row[0]),
                "predicate": row[1],
                "value": row[2],
                "semantic_kind": row[3],
                "valid_from": row[4],
                "valid_to": row[5],
                "recorded_at": row[6],
                "verification_status": row[7],
                "source_uri": row[8],
            }
            (current if row[5] is None else history).append(item)

        cur.execute(
            """SELECT c.id, c.predicate, c.value, c.valid_from, c.recorded_at,
                      c.verification_status, s.uri
               FROM secretary.claims c
               JOIN secretary.sources s ON s.id=c.source_id
               WHERE c.entity_id=%s
                 AND c.retracted_at IS NULL
                 AND c.semantic_kind='event'
               ORDER BY c.valid_from DESC, c.recorded_at DESC
               LIMIT 100""",
            (entity_uuid,),
        )
        events = [
            {
                "id": str(row[0]),
                "predicate": row[1],
                "value": row[2],
                "valid_from": row[3],
                "recorded_at": row[4],
                "verification_status": row[5],
                "source_uri": row[6],
            }
            for row in cur.fetchall()
        ]

        cur.execute(
            """SELECT r.id, 'outgoing' AS direction,
                      r.predicate, r.relation_role,
                      other.id, other.name, other.entity_type,
                      r.valid_from, r.valid_to, s.uri
               FROM secretary.entity_relations r
               JOIN secretary.entities other ON other.id=r.object_entity_id
               JOIN secretary.sources s ON s.id=r.source_id
               WHERE r.subject_entity_id=%s AND r.retracted_at IS NULL
               UNION ALL
               SELECT r.id, 'incoming' AS direction,
                      r.predicate, r.relation_role,
                      other.id, other.name, other.entity_type,
                      r.valid_from, r.valid_to, s.uri
               FROM secretary.entity_relations r
               JOIN secretary.entities other ON other.id=r.subject_entity_id
               JOIN secretary.sources s ON s.id=r.source_id
               WHERE r.object_entity_id=%s AND r.retracted_at IS NULL
               ORDER BY direction, predicate, valid_from DESC""",
            (entity_uuid, entity_uuid),
        )
        relations = [
            {
                "id": str(row[0]),
                "direction": row[1],
                "predicate": row[2],
                "relation_role": row[3],
                "other_entity_id": str(row[4]),
                "other_entity_name": row[5],
                "other_entity_type": row[6],
                "valid_from": row[7],
                "valid_to": row[8],
                "source_uri": row[9],
            }
            for row in cur.fetchall()
        ]

    return {
        "entity": {
            "id": str(entity[0]),
            "name": entity[1],
            "domain": entity[2],
            "entity_type": entity[3],
        },
        "current": current,
        "history": history,
        "events": events,
        "relations": relations,
    }
