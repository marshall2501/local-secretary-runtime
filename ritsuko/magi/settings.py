"""Pure MAGI LLM profile/member settings rules plus persistence-port delegates.

MELCHIOR / CASPER / BALTHASAR are logical MAGI slots only. Provider and model
are independent assignments. PostgreSQL is the normal settings source; env
values are bootstrap/fallback defaults when no DB assignments exist yet.

Service Connections are the runtime source of endpoint and authentication
settings; LLM Profiles keep only model/runtime settings plus the Connection
reference. Legacy env values remain bootstrap/fallback only.
"""
from __future__ import annotations

import os

from integrations.connections.credential_resolver import env_name_to_credential_ref
from integrations.llm.ollama_runtime import (
    DEFAULT_MAGI_OLLAMA_NUM_PREDICT,
    DEFAULT_OLLAMA_CONTEXT_TOKENS,
    configured_magi_num_predict,
    normalize_context_tokens,
    normalize_magi_num_predict,
)
from integrations.connections.service_connections import (
    adapter_defaults as service_adapter_defaults,
    legacy_credential_env,
)

MEMBER_NAMES = ("MELCHIOR", "CASPER", "BALTHASAR")
PROVIDERS = ("ollama", "openai", "gemini")
DEFAULT_TIMEOUT_SECONDS = 120
DEFAULT_RETRY_HTTP_CODES = (429, 500, 502, 503, 504)
DEFAULT_RETRY_WITHIN_TURN = True


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    return value if value > 0 else default


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if 1 <= value <= 3600 else default


def provider_defaults(provider: str) -> tuple[str, str | None]:
    if provider not in PROVIDERS:
        raise ValueError("unsupported_provider")
    defaults = service_adapter_defaults(provider)
    return defaults["endpoint"], legacy_credential_env(defaults)


def normalize_retry_http_codes(value: object | None) -> tuple[int, ...]:
    if value is None:
        return DEFAULT_RETRY_HTTP_CODES
    if isinstance(value, str):
        raw_items = [item.strip() for item in value.split(",") if item.strip()]
    elif isinstance(value, (list, tuple, set)):
        raw_items = list(value)
    else:
        raise ValueError("invalid_retry_http_codes")

    codes: list[int] = []
    for item in raw_items:
        try:
            code = int(item)
        except (TypeError, ValueError) as exc:
            raise ValueError("invalid_retry_http_codes") from exc
        if not 400 <= code <= 599:
            raise ValueError("invalid_retry_http_codes")
        if code not in codes:
            codes.append(code)
    if len(codes) > 20:
        raise ValueError("too_many_retry_http_codes")
    return tuple(codes)


def _normalize_provider(value: object) -> str:
    provider = str(value or "").strip().lower()
    if provider not in PROVIDERS:
        raise ValueError("unsupported_provider")
    return provider


def _normalize_member(value: object) -> str:
    member = str(value or "").strip().upper()
    if member not in MEMBER_NAMES:
        raise ValueError("unknown_magi_member")
    return member


def _normalize_weight(value: object) -> float:
    weight = float(value)
    if not 0 < weight <= 100:
        raise ValueError("invalid_member_weight")
    return weight


def _normalize_timeout(value: object) -> int:
    timeout = int(value)
    if not 1 <= timeout <= 3600:
        raise ValueError("invalid_member_timeout")
    return timeout


def fallback_member_specs(local_model: str | None = None) -> list[dict]:
    """Bootstrap defaults only; DB assignments supersede these once saved."""
    cloud_enabled = _env_bool("LSA_MAGI_CLOUD_ENABLED", False)
    legacy_models = {
        "MELCHIOR": local_model or "",
        "CASPER": os.environ.get("LSA_MAGI_CASPER_MODEL", "gpt-5.6-sol"),
        "BALTHASAR": os.environ.get("LSA_MAGI_BALTHASAR_MODEL", "gpt-5.6-terra"),
    }
    legacy_providers = {
        "MELCHIOR": "ollama",
        "CASPER": "openai",
        "BALTHASAR": "openai",
    }
    legacy_enabled = {
        "MELCHIOR": True,
        "CASPER": cloud_enabled and _env_bool("LSA_MAGI_CASPER_ENABLED", True),
        "BALTHASAR": cloud_enabled and _env_bool("LSA_MAGI_BALTHASAR_ENABLED", True),
    }

    specs = []
    for member in MEMBER_NAMES:
        prefix = "LSA_MAGI_" + member + "_"
        provider = _normalize_provider(
            os.environ.get(prefix + "PROVIDER") or legacy_providers[member]
        )
        model = (os.environ.get(prefix + "MODEL") or legacy_models[member] or "").strip()
        endpoint_default, credential_default = provider_defaults(provider)
        endpoint = (
            os.environ.get(prefix + "ENDPOINT")
            or (
                os.environ.get("OPENAI_BASE_URL")
                if provider == "openai"
                else os.environ.get("GEMINI_BASE_URL")
                if provider == "gemini"
                else os.environ.get("OLLAMA_HOST")
            )
            or endpoint_default
        ).strip()
        credential_env = os.environ.get(prefix + "CREDENTIAL_ENV") or credential_default
        configured_context = normalize_context_tokens(
            os.environ.get(prefix + "CONTEXT_TOKENS")
            or os.environ.get("LSA_OLLAMA_CONTEXT_TOKENS")
            or DEFAULT_OLLAMA_CONTEXT_TOKENS
        )
        enabled_default = legacy_enabled[member]
        enabled = _env_bool(prefix + "ENABLED", enabled_default)
        if provider != "ollama" and prefix + "PROVIDER" not in os.environ:
            enabled = enabled and cloud_enabled

        specs.append({
            "name": member,
            "profile_id": None,
            "connection_id": None,
            "profile_label": f"{provider} / {model or '-'}",
            "provider": provider,
            "model": model,
            "endpoint": endpoint,
            "credential_env": credential_env,
            "credential_ref": env_name_to_credential_ref(credential_env),
            "context_window_tokens": configured_context if provider == "ollama" else None,
            "ollama_num_predict": (
                configured_magi_num_predict() if provider == "ollama" else None
            ),
            "retry_http_codes": list(DEFAULT_RETRY_HTTP_CODES),
            "weight": _env_float(prefix + "WEIGHT", 1.0),
            "timeout_seconds": _env_int(prefix + "TIMEOUT_SECONDS", DEFAULT_TIMEOUT_SECONDS),
            "retry_within_turn": DEFAULT_RETRY_WITHIN_TURN,
            "enabled": bool(enabled and model),
            "settings_source": "env_fallback",
        })
    return specs


def _row_profile(row) -> dict:
    connection = {"credential_ref": row[5]}
    return {
        "id": str(row[0]),
        "display_name": row[1],
        "provider": row[2],
        "model": row[3],
        "endpoint": row[4],
        "credential_env": legacy_credential_env(connection),
        "credential_ref": row[5],
        "context_window_tokens": row[6],
        "ollama_num_predict": row[7],
        "retry_http_codes": list(row[8] or []),
        "enabled": bool(row[9] and row[12]),
        "connection_id": str(row[10]),
        "connection_display_name": row[11],
        "connection_enabled": bool(row[12]),
    }



def list_llm_profiles(repository, *, include_disabled: bool = False) -> list[dict]:
    return repository.list_llm_profiles(include_disabled=include_disabled)


def upsert_llm_profile(repository, **kwargs) -> dict:
    return repository.upsert_llm_profile(**kwargs)


def sync_ollama_profiles(repository, models: list[str], *, endpoint: str | None = None) -> list[dict]:
    return repository.sync_ollama_profiles(models, endpoint=endpoint)


def load_member_specs(repository) -> list[dict]:
    return repository.load_member_specs()


def save_member_assignments(repository, assignments: list[dict]) -> list[dict]:
    return repository.save_member_assignments(assignments)


def bootstrap_member_assignments(repository, fallback_specs: list[dict]) -> list[dict]:
    return repository.bootstrap_member_assignments(fallback_specs)
