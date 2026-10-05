"""PostgreSQL adapter for Service Connection persistence."""
from __future__ import annotations

import json
import os
from uuid import UUID, uuid4

from integrations.connections.credential_resolver import (
    credential_ref_to_env_name,
    env_name_to_credential_ref,
    normalize_credential_ref,
    resolve_credential,
)
from integrations.connections.service_connections import (
    ADAPTER_DEFAULTS,
    LLM_INFERENCE,
    SERVICE_BILLING_READ,
    adapter_defaults,
    normalize_adapter_key,
    normalize_capabilities,
    normalize_connection_role,
    normalize_connection_type,
    normalize_json_object,
)

_CONNECTION_COLUMNS = """id, display_name, adapter_key, endpoint, credential_ref,
                        account_label, capabilities, nonsecret_config, enabled,
                        connection_type, connection_role, auth_data"""


def _row_connection(row) -> dict:
    auth_data = dict(row[11] or {})
    return {
        "id": str(row[0]),
        "display_name": row[1],
        "adapter_key": row[2],
        "endpoint": row[3],
        "credential_ref": row[4],
        "account_label": row[5],
        "capabilities": list(row[6] or []),
        "config_data": dict(row[7] or {}),
        "nonsecret_config": dict(row[7] or {}),
        "enabled": bool(row[8]),
        "connection_type": row[9],
        "connection_role": row[10],
        "auth_configured": bool(auth_data),
        "auth_fields": sorted(auth_data.keys()),
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
            f"""SELECT {_CONNECTION_COLUMNS}
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
            f"""SELECT {_CONNECTION_COLUMNS}
                FROM secretary.service_connections
                WHERE id=%s""",
            (connection_uuid,),
        )
        row = cur.fetchone()
    if row is None:
        raise ValueError("unknown_connection")
    return _row_connection(row)


def get_connection_auth_value(
    db,
    connection_id: str,
    field: str = "api_key",
) -> str | None:
    try:
        connection_uuid = UUID(str(connection_id))
    except ValueError as exc:
        raise ValueError("invalid_connection_id") from exc
    key = str(field or "").strip()
    if not key or len(key) > 100:
        raise ValueError("invalid_auth_field")
    with db.cursor() as cur:
        cur.execute(
            """SELECT auth_data
               FROM secretary.service_connections
               WHERE id=%s AND enabled""",
            (connection_uuid,),
        )
        row = cur.fetchone()
    if row is None:
        return None
    value = (dict(row[0] or {})).get(key)
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def upsert_service_connection(
    db,
    *,
    adapter_key: str,
    display_name: str,
    endpoint: str | None = None,
    credential_ref: str | None = None,
    account_label: str | None = None,
    capabilities: object | None = None,
    config_data: dict | None = None,
    nonsecret_config: dict | None = None,
    auth_data: dict | None = None,
    connection_type: str | None = None,
    connection_role: str | None = None,
    enabled: bool | None = None,
    connection_id: str | None = None,
) -> dict:
    key = normalize_adapter_key(adapter_key)
    defaults = ADAPTER_DEFAULTS.get(key)
    if defaults is None:
        raise ValueError("unsupported_adapter")

    name = str(display_name or "").strip()
    if not name or len(name) > 200:
        raise ValueError("display_name_required")
    endpoint_value = str(endpoint or defaults.get("endpoint") or "").strip()
    if not endpoint_value or len(endpoint_value) > 500:
        raise ValueError("endpoint_required")

    kind = normalize_connection_type(
        connection_type or defaults.get("connection_type")
    )
    role = normalize_connection_role(connection_role)
    credential_value = normalize_credential_ref(
        credential_ref
        if credential_ref is not None
        else defaults.get("credential_ref")
    )
    account_value = str(account_label or "").strip() or None
    if account_value and len(account_value) > 200:
        raise ValueError("account_label_too_long")

    requested_caps = (
        None if capabilities is None else normalize_capabilities(capabilities)
    )
    supported = set(defaults.get("supported_capabilities", ()))
    if requested_caps is not None and any(
        cap not in supported for cap in requested_caps
    ):
        raise ValueError("unsupported_adapter_capability")

    requested_config = (
        config_data if config_data is not None else nonsecret_config
    )

    with db.cursor() as cur:
        if connection_id:
            try:
                connection_uuid = UUID(str(connection_id))
            except ValueError as exc:
                raise ValueError("invalid_connection_id") from exc
            cur.execute(
                """SELECT id, capabilities, nonsecret_config, auth_data, enabled
                   FROM secretary.service_connections
                   WHERE id=%s""",
                (connection_uuid,),
            )
            row = cur.fetchone()
            if row is None:
                raise ValueError("unknown_connection")
        else:
            cur.execute(
                """SELECT id, capabilities, nonsecret_config, auth_data, enabled
                   FROM secretary.service_connections
                   WHERE lower(display_name)=lower(%s)""",
                (name,),
            )
            row = cur.fetchone()
            connection_uuid = row[0] if row else uuid4()

        caps = (
            normalize_capabilities(row[1] or [])
            if row and requested_caps is None
            else requested_caps
            if requested_caps is not None
            else normalize_capabilities(defaults.get("capabilities", ()))
        )
        config = (
            dict(row[2] or {})
            if row and requested_config is None
            else normalize_json_object(
                requested_config,
                field="config_data",
            )
        )
        auth = (
            dict(row[3] or {})
            if row and auth_data is None
            else normalize_json_object(auth_data, field="auth_data")
        )
        enabled_value = (
            bool(row[4])
            if row and enabled is None
            else True
            if enabled is None
            else bool(enabled)
        )

        if kind == "none":
            auth = {}
        elif (
            kind == "api_key"
            and auth
            and not str(auth.get("api_key") or "").strip()
        ):
            raise ValueError("api_key_required")
        elif kind == "username_password" and auth:
            if (
                not str(auth.get("username") or "").strip()
                or not str(auth.get("password") or "").strip()
            ):
                raise ValueError("username_password_required")
        elif kind == "oauth2" and auth:
            if not str(auth.get("client_id") or "").strip():
                raise ValueError("oauth2_client_id_required")
        elif kind == "external_credentials":
            auth = {}

        if key == "google_cloud":
            if kind != "external_credentials":
                raise ValueError("google_cloud_requires_external_credentials")
            credential_provider = str(
                config.get("credential_provider") or ""
            ).strip()
            project_id = str(config.get("project_id") or "").strip()
            target_principal = str(
                config.get("target_principal") or ""
            ).strip()
            if credential_provider != "google_adc":
                raise ValueError(
                    "google_cloud_credential_provider_required"
                )
            if not project_id:
                raise ValueError("google_cloud_project_id_required")
            if not target_principal:
                raise ValueError("google_cloud_target_principal_required")

        if row:
            cur.execute(
                """UPDATE secretary.service_connections
                   SET display_name=%s, adapter_key=%s, endpoint=%s,
                       credential_ref=%s, account_label=%s, capabilities=%s,
                       nonsecret_config=%s::jsonb, auth_data=%s::jsonb,
                       connection_type=%s, connection_role=%s,
                       enabled=%s, updated_at=now()
                   WHERE id=%s""",
                (
                    name,
                    key,
                    endpoint_value,
                    credential_value,
                    account_value,
                    list(caps),
                    json.dumps(config, ensure_ascii=False),
                    json.dumps(auth, ensure_ascii=False),
                    kind,
                    role,
                    enabled_value,
                    connection_uuid,
                ),
            )
        else:
            cur.execute(
                """INSERT INTO secretary.service_connections
                   (id, display_name, adapter_key, endpoint, credential_ref,
                    account_label, capabilities, nonsecret_config, auth_data,
                    connection_type, connection_role, enabled)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s,%s,%s)""",
                (
                    connection_uuid,
                    name,
                    key,
                    endpoint_value,
                    credential_value,
                    account_value,
                    list(caps),
                    json.dumps(config, ensure_ascii=False),
                    json.dumps(auth, ensure_ascii=False),
                    kind,
                    role,
                    enabled_value,
                ),
            )
    return get_service_connection(db, str(connection_uuid))


def bootstrap_connection_auth_from_env(db) -> int:
    with db.cursor() as cur:
        cur.execute(
            """SELECT id, credential_ref, connection_type
               FROM secretary.service_connections
               WHERE enabled
                 AND auth_data='{}'::jsonb
                 AND credential_ref LIKE 'env:%'"""
        )
        rows = cur.fetchall()

    updated = 0
    for connection_id, credential_ref, connection_type in rows:
        if connection_type != "api_key":
            continue
        try:
            value = resolve_credential(credential_ref)
        except Exception:
            continue
        if not value:
            continue
        with db.cursor() as cur:
            cur.execute(
                """UPDATE secretary.service_connections
                   SET auth_data=%s::jsonb, updated_at=now()
                   WHERE id=%s AND auth_data='{}'::jsonb""",
                (json.dumps({"api_key": value}), connection_id),
            )
        updated += 1
    return updated


def ensure_llm_connection(
    db,
    *,
    provider: str,
    endpoint: str | None = None,
    credential_env: str | None = None,
) -> dict:
    key = normalize_adapter_key(provider)
    defaults = adapter_defaults(key)
    credential_name = str(credential_env or "").strip() or None
    credential_ref = (
        env_name_to_credential_ref(credential_name)
        if credential_name
        else defaults["credential_ref"]
    )
    auth_data = None
    if defaults["connection_type"] == "api_key":
        env_name = credential_name or credential_ref_to_env_name(
            credential_ref
        )
        secret = os.environ.get(env_name or "", "").strip() if env_name else ""
        if secret:
            auth_data = {"api_key": secret}
    return upsert_service_connection(
        db,
        adapter_key=key,
        display_name=f"{key} / LLM",
        endpoint=endpoint or defaults["endpoint"],
        credential_ref=credential_ref,
        capabilities=[LLM_INFERENCE],
        auth_data=auth_data,
        connection_type=defaults["connection_type"],
        connection_role="llm",
        enabled=None,
    )


def ensure_openai_billing_connection(db) -> dict:
    secret = os.environ.get("OPENAI_ADMIN_KEY", "").strip()
    return upsert_service_connection(
        db,
        adapter_key="openai",
        display_name="OpenAI Admin",
        endpoint=ADAPTER_DEFAULTS["openai"]["endpoint"],
        credential_ref=env_name_to_credential_ref("OPENAI_ADMIN_KEY"),
        capabilities=[SERVICE_BILLING_READ],
        auth_data={"api_key": secret} if secret else None,
        connection_type="api_key",
        connection_role="service_billing",
        enabled=None,
    )


class PostgresServiceConnectionRepository:
    def __init__(self, connection_factory):
        self._connection_factory = connection_factory

    def list_service_connections(
        self,
        *,
        include_disabled: bool = False,
        capability: str | None = None,
    ) -> list[dict]:
        with self._connection_factory() as db:
            return list_service_connections(
                db,
                include_disabled=include_disabled,
                capability=capability,
            )

    def get_service_connection(self, connection_id: str) -> dict:
        with self._connection_factory() as db:
            return get_service_connection(db, connection_id)

    def get_connection_auth_value(
        self,
        connection_id: str,
        field: str = "api_key",
    ) -> str | None:
        with self._connection_factory() as db:
            return get_connection_auth_value(db, connection_id, field)

    def upsert_service_connection(self, **kwargs) -> dict:
        with self._connection_factory() as db:
            return upsert_service_connection(db, **kwargs)

    def bootstrap_connection_auth_from_env(self) -> int:
        with self._connection_factory() as db:
            return bootstrap_connection_auth_from_env(db)

    def ensure_llm_connection(
        self,
        *,
        provider: str,
        endpoint: str | None = None,
        credential_env: str | None = None,
    ) -> dict:
        with self._connection_factory() as db:
            return ensure_llm_connection(
                db,
                provider=provider,
                endpoint=endpoint,
                credential_env=credential_env,
            )

    def ensure_openai_billing_connection(self) -> dict:
        with self._connection_factory() as db:
            return ensure_openai_billing_connection(db)
