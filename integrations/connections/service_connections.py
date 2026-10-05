"""Service Connection domain rules and persistence-port delegates.

Connection/authentication data is persisted by a concrete repository. This
module owns validation/default semantics only and intentionally contains no SQL.
"""
from __future__ import annotations

import json
import re
from typing import Protocol

from .credential_resolver import credential_ref_to_env_name

LLM_INFERENCE = "llm_inference"
SERVICE_BILLING_READ = "service_billing_read"

CONNECTION_TYPES = (
    "none",
    "api_key",
    "username_password",
    "oauth2",
    "external_credentials",
)

ADAPTER_DEFAULTS = {
    "ollama": {
        "endpoint": "http://127.0.0.1:11434",
        "credential_ref": None,
        "connection_type": "none",
        "capabilities": (LLM_INFERENCE,),
        "supported_capabilities": (LLM_INFERENCE,),
        "config_data": {},
    },
    "openai": {
        "endpoint": "https://api.openai.com/v1",
        "credential_ref": "env:OPENAI_API_KEY",
        "connection_type": "api_key",
        "capabilities": (LLM_INFERENCE,),
        "supported_capabilities": (
            LLM_INFERENCE,
            SERVICE_BILLING_READ,
        ),
        "config_data": {},
    },
    "gemini": {
        "endpoint": "https://generativelanguage.googleapis.com/v1beta",
        "credential_ref": "env:GEMINI_API_KEY",
        "connection_type": "api_key",
        "capabilities": (LLM_INFERENCE,),
        "supported_capabilities": (LLM_INFERENCE,),
        "config_data": {},
    },
    "google_cloud": {
        "endpoint": "https://monitoring.googleapis.com/v3",
        "credential_ref": None,
        "connection_type": "external_credentials",
        "capabilities": (SERVICE_BILLING_READ,),
        "supported_capabilities": (SERVICE_BILLING_READ,),
        "config_data": {
            "credential_provider": "google_adc",
            "project_id": "",
            "target_principal": "",
        },
    },
}

_TOKEN = re.compile(r"^[a-z][a-z0-9_]{1,63}$")


class ServiceConnectionRepository(Protocol):
    def list_service_connections(
        self,
        *,
        include_disabled: bool = False,
        capability: str | None = None,
    ) -> list[dict]: ...

    def get_service_connection(self, connection_id: str) -> dict: ...

    def get_connection_auth_value(
        self,
        connection_id: str,
        field: str = "api_key",
    ) -> str | None: ...

    def upsert_service_connection(self, **kwargs) -> dict: ...

    def bootstrap_connection_auth_from_env(self) -> int: ...

    def ensure_llm_connection(
        self,
        *,
        provider: str,
        endpoint: str | None = None,
        credential_env: str | None = None,
    ) -> dict: ...

    def ensure_openai_billing_connection(self) -> dict: ...


def normalize_adapter_key(value: object) -> str:
    key = str(value or "").strip().lower()
    if not _TOKEN.fullmatch(key):
        raise ValueError("invalid_adapter_key")
    return key


def normalize_connection_type(value: object | None) -> str:
    kind = str(value or "").strip().lower()
    if kind not in CONNECTION_TYPES:
        raise ValueError("invalid_connection_type")
    return kind


def normalize_connection_role(value: object | None) -> str | None:
    role = str(value or "").strip().lower()
    if not role:
        return None
    if not _TOKEN.fullmatch(role):
        raise ValueError("invalid_connection_role")
    return role


def connection_adapter_keys() -> tuple[str, ...]:
    return tuple(ADAPTER_DEFAULTS)


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


def normalize_json_object(value: object | None, *, field: str) -> dict:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError(field + "_must_be_object")
    result = dict(value)
    if len(json.dumps(result, ensure_ascii=False)) > 20000:
        raise ValueError(field + "_too_large")
    return result


# Backward-compatible internal alias used by existing tests.
_normalize_json_object = normalize_json_object


def adapter_defaults(adapter_key: str) -> dict:
    key = normalize_adapter_key(adapter_key)
    defaults = ADAPTER_DEFAULTS.get(key)
    if defaults is None:
        raise ValueError("unsupported_adapter")
    return {
        "adapter_key": key,
        "endpoint": defaults["endpoint"],
        "credential_ref": defaults["credential_ref"],
        "connection_type": defaults["connection_type"],
        "capabilities": list(defaults["capabilities"]),
        "supported_capabilities": list(defaults["supported_capabilities"]),
        "config_data": dict(defaults.get("config_data") or {}),
    }


def legacy_credential_env(connection: dict) -> str | None:
    return credential_ref_to_env_name(connection.get("credential_ref"))


def list_service_connections(
    repository: ServiceConnectionRepository,
    *,
    include_disabled: bool = False,
    capability: str | None = None,
) -> list[dict]:
    return repository.list_service_connections(
        include_disabled=include_disabled,
        capability=capability,
    )


def get_service_connection(
    repository: ServiceConnectionRepository,
    connection_id: str,
) -> dict:
    return repository.get_service_connection(connection_id)


def get_connection_auth_value(
    repository: ServiceConnectionRepository,
    connection_id: str,
    field: str = "api_key",
) -> str | None:
    return repository.get_connection_auth_value(connection_id, field)


def upsert_service_connection(
    repository: ServiceConnectionRepository,
    **kwargs,
) -> dict:
    return repository.upsert_service_connection(**kwargs)


def bootstrap_connection_auth_from_env(
    repository: ServiceConnectionRepository,
) -> int:
    return repository.bootstrap_connection_auth_from_env()


def ensure_llm_connection(
    repository: ServiceConnectionRepository,
    *,
    provider: str,
    endpoint: str | None = None,
    credential_env: str | None = None,
) -> dict:
    return repository.ensure_llm_connection(
        provider=provider,
        endpoint=endpoint,
        credential_env=credential_env,
    )


def ensure_openai_billing_connection(
    repository: ServiceConnectionRepository,
) -> dict:
    return repository.ensure_openai_billing_connection()
