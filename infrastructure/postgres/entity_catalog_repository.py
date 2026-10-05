"""PostgreSQL Entity catalog adapter."""
from __future__ import annotations

from psycopg.types.json import Jsonb


class PostgresEntityCatalogRepository:
    def __init__(self, connection_factory):
        self.connection_factory = connection_factory

    def create_or_get(
        self,
        *,
        name: str,
        domain: str,
        entity_type: str,
        actor: str,
    ) -> dict:
        with self.connection_factory() as db, db.transaction(), db.cursor() as cur:
            # Serialize same-name onboarding so two UI requests cannot create
            # ambiguous active canonical names.
            cur.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(lower(%s), 3))",
                (name,),
            )
            cur.execute(
                """SELECT id, name, domain, entity_type
                   FROM secretary.entities
                   WHERE lower(name)=lower(%s) AND retired_at IS NULL
                   ORDER BY created_at, id""",
                (name,),
            )
            rows = cur.fetchall()
            if len(rows) > 1:
                raise ValueError(
                    "同名の有効Entityが複数存在するため追加できません。既存Entityを整理してください。"
                )
            if rows:
                identifier, existing_name, existing_domain, existing_type = rows[0]
                if existing_domain != domain or existing_type != entity_type:
                    raise ValueError(
                        "同名Entityが別のdomain/typeで既に存在します。既存Entityを利用してください。"
                    )
                return {
                    "id": str(identifier),
                    "name": existing_name,
                    "domain": existing_domain,
                    "entity_type": existing_type,
                    "created": False,
                }

            cur.execute(
                """INSERT INTO secretary.entities (name, domain, entity_type)
                   VALUES (%s,%s,%s)
                   RETURNING id, name, domain, entity_type""",
                (name, domain, entity_type),
            )
            identifier, created_name, created_domain, created_type = cur.fetchone()
            cur.execute(
                """INSERT INTO secretary.audit_events
                   (actor, event_type, object_type, object_id, details)
                   VALUES (%s,'memory.entity_created','entity',%s,%s)""",
                (
                    actor,
                    identifier,
                    Jsonb({
                        "domain": created_domain,
                        "entity_type": created_type,
                        "source": "daily_gui",
                    }),
                ),
            )
            return {
                "id": str(identifier),
                "name": created_name,
                "domain": created_domain,
                "entity_type": created_type,
                "created": True,
            }
