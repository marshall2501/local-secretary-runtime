"""PostgreSQL adapter for MAGI LLM profiles and member assignments."""
from __future__ import annotations

from uuid import UUID, uuid4

from integrations.connections.service_connections import (
    LLM_INFERENCE,
    legacy_credential_env,
)
from infrastructure.postgres.service_connection_repository import (
    ensure_llm_connection,
    get_service_connection,
)
from integrations.llm.ollama_runtime import (
    DEFAULT_MAGI_OLLAMA_NUM_PREDICT,
    DEFAULT_OLLAMA_CONTEXT_TOKENS,
    normalize_context_tokens,
    normalize_magi_num_predict,
)
from ritsuko.magi.settings import (
    DEFAULT_RETRY_HTTP_CODES,
    DEFAULT_RETRY_WITHIN_TURN,
    DEFAULT_TIMEOUT_SECONDS,
    MEMBER_NAMES,
    _normalize_member,
    _normalize_provider,
    _normalize_timeout,
    _normalize_weight,
    _row_profile,
    normalize_retry_http_codes,
    provider_defaults,
)

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
    model: str,
    connection_id: str | None = None,
    provider: str | None = None,
    display_name: str | None = None,
    endpoint: str | None = None,
    credential_env: str | None = None,
    context_window_tokens: int | None = None,
    ollama_num_predict: int | None = None,
    retry_http_codes: object | None = None,
    enabled: bool = True,
    profile_id: str | None = None,
) -> dict:
    model = str(model or "").strip()
    if not model:
        raise ValueError("model_required")

    if connection_id:
        connection = get_service_connection(db, connection_id)
        if LLM_INFERENCE not in set(connection.get("capabilities") or []):
            raise ValueError("connection_missing_llm_inference")
        provider = _normalize_provider(connection["adapter_key"])
    else:
        provider = _normalize_provider(provider)
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
    connection_uuid = UUID(connection["id"])

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
                (connection_uuid, model),
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
                    display_name, connection_uuid, model,
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
                    profile_uuid, display_name, connection_uuid, model,
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
        connection_id=spec.get("connection_id"),
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


class PostgresMagiSettingsRepository:
    def __init__(self, connection_factory):
        self._connection_factory = connection_factory

    def list_llm_profiles(self, *, include_disabled: bool = False) -> list[dict]:
        with self._connection_factory() as db:
            return list_llm_profiles(db, include_disabled=include_disabled)

    def upsert_llm_profile(self, **kwargs) -> dict:
        with self._connection_factory() as db:
            return upsert_llm_profile(db, **kwargs)

    def sync_ollama_profiles(self, models: list[str], *, endpoint: str | None = None) -> list[dict]:
        with self._connection_factory() as db:
            return sync_ollama_profiles(db, models, endpoint=endpoint)

    def load_member_specs(self) -> list[dict]:
        with self._connection_factory() as db:
            return load_member_specs(db)

    def save_member_assignments(self, assignments: list[dict]) -> list[dict]:
        with self._connection_factory() as db:
            return save_member_assignments(db, assignments)

    def bootstrap_member_assignments(self, fallback_specs: list[dict]) -> list[dict]:
        with self._connection_factory() as db:
            return bootstrap_member_assignments(db, fallback_specs)
