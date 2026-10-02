"""DB-backed MAGI LLM profile and member assignment settings.

MELCHIOR / CASPER / BALTHASAR are logical MAGI slots only. Provider and model
are independent assignments. PostgreSQL is the normal settings source; env
values are bootstrap/fallback defaults when no DB assignments exist yet.

API secret values are deliberately not stored in these configuration tables.
Service Connections are the runtime source of endpoint and credential references;
LLM Profiles keep only model/runtime settings plus the Connection reference.
"""
from __future__ import annotations

import os
from uuid import UUID, uuid4

from .credential_resolver import env_name_to_credential_ref
from .ollama_runtime import (
    DEFAULT_MAGI_OLLAMA_NUM_PREDICT,
    DEFAULT_OLLAMA_CONTEXT_TOKENS,
    configured_magi_num_predict,
    normalize_context_tokens,
    normalize_magi_num_predict,
)
from .service_connections import (
    adapter_defaults as service_adapter_defaults,
    ensure_llm_connection,
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


def list_llm_profiles(db, *, include_disabled: bool = False) -> list[dict]:
    where = "" if include_disabled else "WHERE p.enabled AND c.enabled"
    with db.cursor() as cur:
        cur.execute(
            f"""SELECT p.id, p.display_name, c.adapter_key, p.model,
                       c.endpoint, c.credential_ref,
                       p.context_window_tokens, p.ollama_num_predict,
                       p.retry_http_codes, p.enabled,
                       c.id, c.display_name, c.enabled
                FROM secretary.llm_profiles p
                JOIN secretary.service_connections c ON c.id=p.connection_id
                {where}
                ORDER BY c.adapter_key, p.display_name, p.model"""
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
    context_window_tokens: int | None = None,
    ollama_num_predict: int | None = None,
    retry_http_codes: object | None = None,
    enabled: bool = True,
    profile_id: str | None = None,
) -> dict:
    provider = _normalize_provider(provider)
    model = str(model or "").strip()
    if not model:
        raise ValueError("model_required")

    default_endpoint, default_credential = provider_defaults(provider)
    connection = ensure_llm_connection(
        db,
        provider=provider,
        endpoint=str(endpoint or default_endpoint).strip(),
        credential_env=(
            None
            if provider == "ollama"
            else str(credential_env or default_credential or "").strip() or None
        ),
    )
    provider = connection["adapter_key"]
    endpoint = connection["endpoint"]
    credential_env = legacy_credential_env(connection)
    connection_id = UUID(connection["id"])

    requested_context = context_window_tokens
    requested_num_predict = ollama_num_predict
    requested_retry_codes = retry_http_codes
    display_name = str(display_name or f"{provider} / {model}").strip()
    if not display_name:
        raise ValueError("display_name_required")

    with db.cursor() as cur:
        if profile_id:
            try:
                requested_profile_id = UUID(str(profile_id))
            except ValueError as exc:
                raise ValueError("invalid_profile_id") from exc
            cur.execute(
                """SELECT id, context_window_tokens, ollama_num_predict, retry_http_codes
                   FROM secretary.llm_profiles
                   WHERE id=%s""",
                (requested_profile_id,),
            )
            row = cur.fetchone()
            if row is None:
                raise ValueError("unknown_profile")
            profile_uuid = row[0]
        else:
            cur.execute(
                """SELECT id, context_window_tokens, ollama_num_predict, retry_http_codes
                   FROM secretary.llm_profiles
                   WHERE connection_id=%s AND model=%s""",
                (connection_id, model),
            )
            row = cur.fetchone()
            profile_uuid = row[0] if row else uuid4()

        if provider == "ollama":
            context_window_tokens = normalize_context_tokens(
                requested_context if requested_context is not None
                else row[1] if row and row[1] is not None
                else DEFAULT_OLLAMA_CONTEXT_TOKENS
            )
            ollama_num_predict = normalize_magi_num_predict(
                requested_num_predict if requested_num_predict is not None
                else row[2] if row and row[2] is not None
                else DEFAULT_MAGI_OLLAMA_NUM_PREDICT
            )
        else:
            context_window_tokens = None
            ollama_num_predict = None

        retry_http_codes = normalize_retry_http_codes(
            requested_retry_codes
            if requested_retry_codes is not None
            else row[3] if row and row[3] is not None
            else DEFAULT_RETRY_HTTP_CODES
        )

        if row:
            cur.execute(
                """UPDATE secretary.llm_profiles
                   SET display_name=%s, connection_id=%s, model=%s,
                       context_window_tokens=%s, ollama_num_predict=%s,
                       retry_http_codes=%s, enabled=%s, updated_at=now()
                   WHERE id=%s""",
                (
                    display_name, connection_id, model,
                    context_window_tokens, ollama_num_predict,
                    list(retry_http_codes), bool(enabled), profile_uuid,
                ),
            )
        else:
            cur.execute(
                """INSERT INTO secretary.llm_profiles
                   (id, display_name, connection_id, model,
                    context_window_tokens, ollama_num_predict,
                    retry_http_codes, enabled)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""",
                (
                    profile_uuid, display_name, connection_id, model,
                    context_window_tokens, ollama_num_predict,
                    list(retry_http_codes), bool(enabled),
                ),
            )

    return {
        "id": str(profile_uuid),
        "display_name": display_name,
        "provider": provider,
        "model": model,
        "endpoint": endpoint,
        "credential_env": credential_env,
        "credential_ref": connection.get("credential_ref"),
        "context_window_tokens": context_window_tokens,
        "ollama_num_predict": ollama_num_predict,
        "retry_http_codes": list(retry_http_codes),
        "enabled": bool(enabled and connection["enabled"]),
        "connection_id": connection["id"],
        "connection_display_name": connection["display_name"],
        "connection_enabled": bool(connection["enabled"]),
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
            endpoint=endpoint or provider_defaults("ollama")[0],
            credential_env=None,
            context_window_tokens=None,
            ollama_num_predict=None,
            retry_http_codes=None,
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
        context_window_tokens=spec.get("context_window_tokens"),
        ollama_num_predict=spec.get("ollama_num_predict"),
        retry_http_codes=spec.get("retry_http_codes"),
        enabled=True,
    )
    return profile["id"]


def load_member_specs(db) -> list[dict]:
    with db.cursor() as cur:
        cur.execute(
            """SELECT a.member, a.profile_id, p.display_name, c.adapter_key, p.model,
                      c.endpoint, c.credential_ref, p.context_window_tokens,
                      p.ollama_num_predict, p.retry_http_codes,
                      a.weight, a.timeout_seconds, a.retry_within_turn,
                      a.enabled, p.enabled, c.enabled, c.id, c.display_name
               FROM secretary.magi_member_assignments a
               JOIN secretary.llm_profiles p ON p.id=a.profile_id
               JOIN secretary.service_connections c ON c.id=p.connection_id"""
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
            "connection_id": str(row[16]),
            "connection_display_name": row[17],
            "profile_label": row[2],
            "provider": row[3],
            "model": row[4],
            "endpoint": row[5],
            "credential_env": legacy_credential_env({"credential_ref": row[6]}),
            "credential_ref": row[6],
            "context_window_tokens": row[7],
            "ollama_num_predict": row[8],
            "retry_http_codes": list(row[9] or []),
            "weight": float(row[10]),
            "timeout_seconds": int(row[11]),
            "retry_within_turn": bool(row[12]),
            "enabled": bool(row[13] and row[14] and row[15]),
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
            "retry_within_turn": bool(
                item.get("retry_within_turn", DEFAULT_RETRY_WITHIN_TURN)
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
                    """SELECT p.enabled AND c.enabled
                       FROM secretary.llm_profiles p
                       JOIN secretary.service_connections c ON c.id=p.connection_id
                       WHERE p.id=%s""",
                    (item["profile_id"],),
                )
                profile = cur.fetchone()
                if profile is None:
                    raise ValueError("unknown_profile")
                if item["enabled"] and not profile[0]:
                    raise ValueError("disabled_profile")
                cur.execute(
                    """INSERT INTO secretary.magi_member_assignments
                       (member, profile_id, enabled, weight, timeout_seconds,
                        retry_within_turn, updated_at)
                       VALUES (%s,%s,%s,%s,%s,%s,now())
                       ON CONFLICT (member) DO UPDATE SET
                         profile_id=EXCLUDED.profile_id,
                         enabled=EXCLUDED.enabled,
                         weight=EXCLUDED.weight,
                         timeout_seconds=EXCLUDED.timeout_seconds,
                         retry_within_turn=EXCLUDED.retry_within_turn,
                         updated_at=now()""",
                    (
                        member, item["profile_id"], item["enabled"],
                        item["weight"], item["timeout_seconds"],
                        item["retry_within_turn"],
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
            "retry_within_turn": spec.get(
                "retry_within_turn", DEFAULT_RETRY_WITHIN_TURN
            ),
        })

    if not any(item["enabled"] for item in assignments):
        assignments[0]["enabled"] = True
    return save_member_assignments(db, assignments)
