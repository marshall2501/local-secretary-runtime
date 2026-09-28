"""Read-only Entity detail projection for the isolated PKB prototype."""
from __future__ import annotations

from uuid import UUID

from .entity_model_service import DBNAME, WRITER


def _guard(db) -> None:
    info = db.info
    if (
        (info.dbname or "") != DBNAME
        or (info.host or "") not in ("localhost", "127.0.0.1", "::1")
        or (info.user or "") != WRITER
    ):
        raise ValueError("Refusing non-isolated DB or non-dedicated writer")


def load_entity_detail(db, entity_id: str) -> dict | None:
    """Build one generic Entity view from Entity/Claim/Relation/Source data."""
    _guard(db)
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
        state_attribute_rows = cur.fetchall()

        current = []
        history = []
        for row in state_attribute_rows:
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
