"""DB-backed MAGI LLM profile and member assignment settings.

MELCHIOR / CASPER / BALTHASAR are logical MAGI slots only. Provider and model
are independent assignments. PostgreSQL is the normal settings source; env
values are bootstrap/fallback defaults when no DB assignments exist yet.

API secret values are deliberately not stored in these configuration tables.
Profiles store only the environment-variable name used to obtain a credential.
"""
from __future__ import annotations

import os
from uuid import UUID, uuid4

from .magi_client import OLLAMA

MEMBER_NAMES = ("MELCHIOR", "CASPER", "BALTHASAR")
PROVIDERS = ("ollama", "openai", "gemini")
DEFAULT_ENDPOINTS = {
    "ollama": OLLAMA,
    "openai": "https://api.openai.com/v1",
    "gemini": "https://generativelanguage.googleapis.com/v1beta",
}
DEFAULT_CREDENTIAL_ENVS = {
    "ollama": None,
    "openai": "OPENAI_API_KEY",
    "gemini": "GEMINI_API_KEY",
}
DEFAULT_TIMEOUT_SECONDS = 900


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
    endpoint = DEFAULT_ENDPOINTS[provider]
    credential_env = DEFAULT_CREDENTIAL_ENVS[provider]
    return endpoint, credential_env


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
        model = (
            os.environ.get(prefix + "MODEL")
            or legacy_models[member]
            or ""
        ).strip()
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
        credential_env = (
            os.environ.get(prefix + "CREDENTIAL_ENV")
            or credential_default
        )
        enabled_default = legacy_enabled[member]
        enabled = _env_bool(prefix + "ENABLED", enabled_default)
        if provider != "ollama" and prefix + "PROVIDER" not in os.environ:
            enabled = enabled and cloud_enabled

        specs.append({
            "name": member,
            "profile_id": None,
            "profile_label": f"{provider} / {model or '-'}",
            "provider": provider,
            "model": model,
            "endpoint": endpoint,
            "credential_env": credential_env,
            "weight": _env_float(prefix + "WEIGHT", 1.0),
            "timeout_seconds": _env_int(prefix + "TIMEOUT_SECONDS", DEFAULT_TIMEOUT_SECONDS),
            "enabled": bool(enabled and model),
            "settings_source": "env_fallback",
        })
    return specs


def _row_profile(row) -> dict:
    return {
        "id": str(row[0]),
        "display_name": row[1],
        "provider": row[2],
        "model": row[3],
        "endpoint": row[4],
        "credential_env": row[5],
        "enabled": bool(row[6]),
    }


def list_llm_profiles(db, *, include_disabled: bool = False) -> list[dict]:
    where = "" if include_disabled else "WHERE enabled"
    with db.cursor() as cur:
        cur.execute(
            f"""SELECT id, display_name, provider, model, endpoint, credential_env, enabled
                FROM secretary.llm_profiles
                {where}
                ORDER BY provider, display_name, model"""
        )
        return [_row_profile(row) for row in cur.fetchall()]


def upsert_llm_profile(
    db,
    *,
    provider: str,
    model: str,
    display_name: str | None = None,
    endpoint: str | None = None,
    credential_env: str | None = None,
    enabled: bool = True,
) -> dict:
    provider = _normalize_provider(provider)
    model = str(model or "").strip()
    if not model:
        raise ValueError("model_required")
    default_endpoint, default_credential = provider_defaults(provider)
    endpoint = str(endpoint or default_endpoint).strip()
    credential_env = (
        None if provider == "ollama"
        else str(credential_env or default_credential or "").strip() or None
    )
    display_name = str(display_name or f"{provider} / {model}").strip()
    if not display_name:
        raise ValueError("display_name_required")

    with db.cursor() as cur:
        cur.execute(
            """SELECT id
               FROM secretary.llm_profiles
               WHERE provider=%s AND model=%s
                 AND endpoint IS NOT DISTINCT FROM %s
                 AND credential_env IS NOT DISTINCT FROM %s""",
            (provider, model, endpoint, credential_env),
        )
        row = cur.fetchone()
        profile_id = row[0] if row else uuid4()
        if row:
            cur.execute(
                """UPDATE secretary.llm_profiles
                   SET display_name=%s, enabled=%s, updated_at=now()
                   WHERE id=%s""",
                (display_name, bool(enabled), profile_id),
            )
        else:
            cur.execute(
                """INSERT INTO secretary.llm_profiles
                   (id, display_name, provider, model, endpoint, credential_env, enabled)
                   VALUES (%s,%s,%s,%s,%s,%s,%s)""",
                (
                    profile_id, display_name, provider, model, endpoint,
                    credential_env, bool(enabled),
                ),
            )
    return {
        "id": str(profile_id),
        "display_name": display_name,
        "provider": provider,
        "model": model,
        "endpoint": endpoint,
        "credential_env": credential_env,
        "enabled": bool(enabled),
    }


def sync_ollama_profiles(db, models: list[str], *, endpoint: str | None = None) -> list[dict]:
    synced = []
    for model in models:
        model = str(model or "").strip()
        if not model:
            continue
        synced.append(upsert_llm_profile(
            db,
            provider="ollama",
            model=model,
            display_name="Ollama / " + model,
            endpoint=endpoint or DEFAULT_ENDPOINTS["ollama"],
            credential_env=None,
            enabled=True,
        ))
    return synced


def _profile_id_for_spec(db, spec: dict) -> str:
    profile = upsert_llm_profile(
        db,
        provider=spec["provider"],
        model=spec["model"],
        display_name=spec.get("profile_label"),
        endpoint=spec.get("endpoint"),
        credential_env=spec.get("credential_env"),
        enabled=True,
    )
    return profile["id"]


def load_member_specs(db) -> list[dict]:
    with db.cursor() as cur:
        cur.execute(
            """SELECT a.member, a.profile_id, p.display_name, p.provider, p.model,
                      p.endpoint, p.credential_env, a.weight, a.timeout_seconds,
                      a.enabled, p.enabled
               FROM secretary.magi_member_assignments a
               JOIN secretary.llm_profiles p ON p.id=a.profile_id"""
        )
        rows = {row[0]: row for row in cur.fetchall()}

    specs = []
    for member in MEMBER_NAMES:
        row = rows.get(member)
        if row is None:
            continue
        specs.append({
            "name": member,
            "profile_id": str(row[1]),
            "profile_label": row[2],
            "provider": row[3],
            "model": row[4],
            "endpoint": row[5],
            "credential_env": row[6],
            "weight": float(row[7]),
            "timeout_seconds": int(row[8]),
            "enabled": bool(row[9] and row[10]),
            "settings_source": "database",
        })
    return specs


def save_member_assignments(db, assignments: list[dict]) -> list[dict]:
    by_member = {}
    for item in assignments:
        member = _normalize_member(item.get("name"))
        if member in by_member:
            raise ValueError("duplicate_magi_member")
        profile_id = str(item.get("profile_id") or "").strip()
        if not profile_id:
            raise ValueError("profile_required")
        try:
            UUID(profile_id)
        except ValueError as exc:
            raise ValueError("invalid_profile_id") from exc
        by_member[member] = {
            "name": member,
            "profile_id": profile_id,
            "enabled": bool(item.get("enabled")),
            "weight": _normalize_weight(item.get("weight", 1.0)),
            "timeout_seconds": _normalize_timeout(
                item.get("timeout_seconds", DEFAULT_TIMEOUT_SECONDS)
            ),
        }

    if set(by_member) != set(MEMBER_NAMES):
        raise ValueError("all_magi_members_required")
    if not any(item["enabled"] for item in by_member.values()):
        raise ValueError("at_least_one_member_required")

    with db.transaction():
        with db.cursor() as cur:
            for member in MEMBER_NAMES:
                item = by_member[member]
                cur.execute(
                    "SELECT enabled FROM secretary.llm_profiles WHERE id=%s",
                    (item["profile_id"],),
                )
                profile = cur.fetchone()
                if profile is None:
                    raise ValueError("unknown_profile")
                if item["enabled"] and not profile[0]:
                    raise ValueError("disabled_profile")
                cur.execute(
                    """INSERT INTO secretary.magi_member_assignments
                       (member, profile_id, enabled, weight, timeout_seconds, updated_at)
                       VALUES (%s,%s,%s,%s,%s,now())
                       ON CONFLICT (member) DO UPDATE SET
                         profile_id=EXCLUDED.profile_id,
                         enabled=EXCLUDED.enabled,
                         weight=EXCLUDED.weight,
                         timeout_seconds=EXCLUDED.timeout_seconds,
                         updated_at=now()""",
                    (
                        member, item["profile_id"], item["enabled"],
                        item["weight"], item["timeout_seconds"],
                    ),
                )
    return load_member_specs(db)


def bootstrap_member_assignments(db, fallback_specs: list[dict]) -> list[dict]:
    existing = load_member_specs(db)
    if existing:
        return existing

    assignments = []
    for spec in fallback_specs:
        profile_id = _profile_id_for_spec(db, spec)
        assignments.append({
            "name": spec["name"],
            "profile_id": profile_id,
            "enabled": spec["enabled"],
            "weight": spec["weight"],
            "timeout_seconds": spec["timeout_seconds"],
        })

    if not any(item["enabled"] for item in assignments):
        assignments[0]["enabled"] = True
    return save_member_assignments(db, assignments)
