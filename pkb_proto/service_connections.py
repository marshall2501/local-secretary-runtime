"""Shared connection registry for local and external service adapters.

A Service Connection describes how the runtime reaches one service endpoint and
which technical capabilities that connection can provide. Secret values are not
stored here: only credential references such as env:OPENAI_API_KEY.
"""
from __future__ import annotations

import json
import re
from uuid import UUID, uuid4

from .credential_resolver import (
    credential_ref_to_env_name,
    env_name_to_credential_ref,
    normalize_credential_ref,
)

LLM_INFERENCE = "llm_inference"
PROVIDER_USAGE_READ = "provider_usage_read"

ADAPTER_DEFAULTS = {
    "ollama": {
        "endpoint": "http://127.0.0.1:11434",
        "credential_ref": None,
        "capabilities": (LLM_INFERENCE,),
    },
    "openai": {
        "endpoint": "https://api.openai.com/v1",
        "credential_ref": "env:OPENAI_API_KEY",
        "capabilities": (LLM_INFERENCE,),
    },
    "gemini": {
        "endpoint": "https://generativelanguage.googleapis.com/v1beta",
        "credential_ref": "env:GEMINI_API_KEY",
        "capabilities": (LLM_INFERENCE,),
    },
}

_TOKEN = re.compile(r"^[a-z][a-z0-9_]{1,63}$")


def normalize_adapter_key(value: object) -> str:
    key = str(value or "").strip().lower()
    if not _TOKEN.fullmatch(key):
        raise ValueError("invalid_adapter_key")
    return key


def normalize_capabilities(value: object | None) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        items = [part.strip() for part in value.split(",") if part.strip()]
    elif isinstance(value, (list, tuple, set)):
        items = [str(part).strip() for part in value if str(part).strip()]
    else:
        raise ValueError("invalid_capabilities")
    result: list[str] = []
    for item in items:
        if not _TOKEN.fullmatch(item):
            raise ValueError("invalid_capability")
        if item not in result:
            result.append(item)
    if len(result) > 50:
        raise ValueError("too_many_capabilities")
    return tuple(result)


def adapter_defaults(adapter_key: str) -> dict:
    key = normalize_adapter_key(adapter_key)
    defaults = ADAPTER_DEFAULTS.get(key)
    if defaults is None:
        raise ValueError("unsupported_adapter")
    return {
        "adapter_key": key,
        "endpoint": defaults["endpoint"],
        "credential_ref": defaults["credential_ref"],
        "capabilities": list(defaults["capabilities"]),
    }


def _row_connection(row) -> dict:
    return {
        "id": str(row[0]),
        "display_name": row[1],
        "adapter_key": row[2],
        "endpoint": row[3],
        "credential_ref": row[4],
        "account_label": row[5],
        "capabilities": list(row[6] or []),
        "nonsecret_config": dict(row[7] or {}),
        "enabled": bool(row[8]),
    }


def list_service_connections(
    db,
    *,
    include_disabled: bool = False,
    capability: str | None = None,
) -> list[dict]:
    clauses = []
    params: list[object] = []
    if not include_disabled:
        clauses.append("enabled")
    if capability:
        cap = normalize_capabilities([capability])[0]
        clauses.append("%s = ANY(capabilities)")
        params.append(cap)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    with db.cursor() as cur:
        cur.execute(
            f"""SELECT id, display_name, adapter_key, endpoint, credential_ref,
                       account_label, capabilities, nonsecret_config, enabled
                FROM secretary.service_connections
                {where}
                ORDER BY adapter_key, display_name, id""",
            tuple(params),
        )
        return [_row_connection(row) for row in cur.fetchall()]


def get_service_connection(db, connection_id: str) -> dict:
    try:
        connection_uuid = UUID(str(connection_id))
    except ValueError as exc:
        raise ValueError("invalid_connection_id") from exc
    with db.cursor() as cur:
        cur.execute(
            """SELECT id, display_name, adapter_key, endpoint, credential_ref,
                      account_label, capabilities, nonsecret_config, enabled
               FROM secretary.service_connections
               WHERE id=%s""",
            (connection_uuid,),
        )
        row = cur.fetchone()
    if row is None:
        raise ValueError("unknown_connection")
    return _row_connection(row)


def upsert_service_connection(
    db,
    *,
    adapter_key: str,
    display_name: str | None = None,
    endpoint: str | None = None,
    credential_ref: str | None = None,
    account_label: str | None = None,
    capabilities: object | None = None,
    nonsecret_config: dict | None = None,
    enabled: bool = True,
    connection_id: str | None = None,
) -> dict:
    key = normalize_adapter_key(adapter_key)
    defaults = ADAPTER_DEFAULTS.get(key) or {}
    endpoint_value = str(endpoint or defaults.get("endpoint") or "").strip()
    if not endpoint_value or len(endpoint_value) > 500:
        raise ValueError("endpoint_required")
    credential_value = normalize_credential_ref(
        credential_ref if credential_ref is not None else defaults.get("credential_ref")
    )
    account_value = str(account_label or "").strip() or None
    if account_value and len(account_value) > 200:
        raise ValueError("account_label_too_long")
    caps = normalize_capabilities(
        capabilities if capabilities is not None else defaults.get("capabilities", ())
    )
    config = dict(nonsecret_config or {})
    if len(json.dumps(config, ensure_ascii=False)) > 20000:
        raise ValueError("nonsecret_config_too_large")
    name = str(display_name or f"{key} / {account_value or endpoint_value}").strip()
    if not name or len(name) > 200:
        raise ValueError("display_name_required")

    with db.cursor() as cur:
        row = None
        if connection_id:
            try:
                connection_uuid = UUID(str(connection_id))
            except ValueError as exc:
                raise ValueError("invalid_connection_id") from exc
            cur.execute(
                """SELECT id, capabilities
                   FROM secretary.service_connections
                   WHERE id=%s""",
                (connection_uuid,),
            )
            row = cur.fetchone()
            if row is None:
                raise ValueError("unknown_connection")
        else:
            cur.execute(
                """SELECT id, capabilities
                   FROM secretary.service_connections
                   WHERE adapter_key=%s
                     AND endpoint=%s
                     AND credential_ref IS NOT DISTINCT FROM %s
                     AND account_label IS NOT DISTINCT FROM %s""",
                (key, endpoint_value, credential_value, account_value),
            )
            row = cur.fetchone()
            connection_uuid = row[0] if row else uuid4()

        if row:
            merged_caps = normalize_capabilities([*(row[1] or []), *caps])
            cur.execute(
                """UPDATE secretary.service_connections
                   SET display_name=%s, adapter_key=%s, endpoint=%s,
                       credential_ref=%s, account_label=%s, capabilities=%s,
                       nonsecret_config=%s::jsonb, enabled=%s, updated_at=now()
                   WHERE id=%s""",
                (
                    name, key, endpoint_value, credential_value, account_value,
                    list(merged_caps),
                    json.dumps(config, ensure_ascii=False),
                    bool(enabled), connection_uuid,
                ),
            )
            caps = merged_caps
        else:
            cur.execute(
                """INSERT INTO secretary.service_connections
                   (id, display_name, adapter_key, endpoint, credential_ref,
                    account_label, capabilities, nonsecret_config, enabled)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s)""",
                (
                    connection_uuid, name, key, endpoint_value, credential_value,
                    account_value, list(caps),
                    json.dumps(config, ensure_ascii=False), bool(enabled),
                ),
            )

    return {
        "id": str(connection_uuid),
        "display_name": name,
        "adapter_key": key,
        "endpoint": endpoint_value,
        "credential_ref": credential_value,
        "account_label": account_value,
        "capabilities": list(caps),
        "nonsecret_config": config,
        "enabled": bool(enabled),
    }


def ensure_llm_connection(
    db,
    *,
    provider: str,
    endpoint: str | None = None,
    credential_env: str | None = None,
) -> dict:
    key = normalize_adapter_key(provider)
    defaults = adapter_defaults(key)
    return upsert_service_connection(
        db,
        adapter_key=key,
        display_name=f"{key} / LLM",
        endpoint=endpoint or defaults["endpoint"],
        credential_ref=(
            env_name_to_credential_ref(credential_env)
            if credential_env
            else defaults["credential_ref"]
        ),
        capabilities=[LLM_INFERENCE],
        enabled=True,
    )


def ensure_openai_usage_connection(db, *, prefer_admin: bool = True) -> dict:
    credential_env = "OPENAI_ADMIN_KEY" if prefer_admin else "OPENAI_API_KEY"
    return upsert_service_connection(
        db,
        adapter_key="openai",
        display_name="OpenAI Usage / Costs",
        endpoint=ADAPTER_DEFAULTS["openai"]["endpoint"],
        credential_ref=env_name_to_credential_ref(credential_env),
        capabilities=[PROVIDER_USAGE_READ],
        enabled=True,
    )


def legacy_credential_env(connection: dict) -> str | None:
    return credential_ref_to_env_name(connection.get("credential_ref"))
