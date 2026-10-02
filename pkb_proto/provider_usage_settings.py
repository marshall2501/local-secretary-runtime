"""DB-backed Provider Usage consumer definitions.

The consumer profile owns only the purpose-specific setting and an explicit
connection_id. Connection/authentication itself lives in service_connections.
"""
from __future__ import annotations

import os
from uuid import UUID, uuid4

from .service_connections import (
    PROVIDER_USAGE_READ,
    ensure_openai_usage_connection,
    get_service_connection,
)


def _row_profile(row) -> dict:
    return {
        "id": str(row[0]),
        "display_name": row[1],
        "connection_id": str(row[2]),
        "enabled": bool(row[3]),
        "connection_name": row[4],
        "adapter_key": row[5],
        "connection_enabled": bool(row[6]),
        "connection_type": row[7],
        "capabilities": list(row[8] or []),
    }


def list_provider_usage_profiles(
    db,
    *,
    include_disabled: bool = False,
) -> list[dict]:
    where = "" if include_disabled else "WHERE p.enabled AND c.enabled"
    with db.cursor() as cur:
        cur.execute(
            f"""SELECT p.id, p.display_name, p.connection_id, p.enabled,
                       c.display_name, c.adapter_key, c.enabled,
                       c.connection_type, c.capabilities
                FROM secretary.provider_usage_profiles p
                JOIN secretary.service_connections c ON c.id=p.connection_id
                {where}
                ORDER BY p.display_name, p.id"""
        )
        return [_row_profile(row) for row in cur.fetchall()]


def upsert_provider_usage_profile(
    db,
    *,
    display_name: str,
    connection_id: str,
    enabled: bool = True,
    profile_id: str | None = None,
) -> dict:
    name = str(display_name or "").strip()
    if not name or len(name) > 200:
        raise ValueError("display_name_required")
    connection = get_service_connection(db, connection_id)
    if PROVIDER_USAGE_READ not in set(connection.get("capabilities") or []):
        raise ValueError("connection_missing_provider_usage_read")
    if not connection.get("enabled") and enabled:
        raise ValueError("disabled_connection")

    with db.cursor() as cur:
        if profile_id:
            try:
                profile_uuid = UUID(str(profile_id))
            except ValueError as exc:
                raise ValueError("invalid_provider_usage_profile_id") from exc
            cur.execute(
                "SELECT id FROM secretary.provider_usage_profiles WHERE id=%s",
                (profile_uuid,),
            )
            if cur.fetchone() is None:
                raise ValueError("unknown_provider_usage_profile")
        else:
            cur.execute(
                """SELECT id
                   FROM secretary.provider_usage_profiles
                   WHERE lower(display_name)=lower(%s)""",
                (name,),
            )
            row = cur.fetchone()
            profile_uuid = row[0] if row else uuid4()

        cur.execute(
            """INSERT INTO secretary.provider_usage_profiles
               (id, display_name, connection_id, enabled, updated_at)
               VALUES (%s,%s,%s,%s,now())
               ON CONFLICT (id) DO UPDATE SET
                 display_name=EXCLUDED.display_name,
                 connection_id=EXCLUDED.connection_id,
                 enabled=EXCLUDED.enabled,
                 updated_at=now()""",
            (profile_uuid, name, UUID(connection["id"]), bool(enabled)),
        )

    rows = list_provider_usage_profiles(db, include_disabled=True)
    for item in rows:
        if item["id"] == str(profile_uuid):
            return item
    raise ValueError("provider_usage_profile_save_failed")


def bootstrap_openai_usage_profile(db) -> dict | None:
    """Import legacy OPENAI_ADMIN_KEY once, never OPENAI_API_KEY fallback."""
    existing = list_provider_usage_profiles(db, include_disabled=True)
    for item in existing:
        if item["display_name"].lower() == "openai usage":
            return item

    if not os.environ.get("OPENAI_ADMIN_KEY", "").strip():
        return None

    connection = ensure_openai_usage_connection(db)
    return upsert_provider_usage_profile(
        db,
        display_name="OpenAI Usage",
        connection_id=connection["id"],
        enabled=True,
    )
