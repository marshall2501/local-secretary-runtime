"""Daily PKB Web UI prototype on an isolated fictional PostgreSQL DB.

This is the first user-facing PKB slice, separate from the developer Workbench.
It deliberately refuses the production DB and accepts only the existing
secretary_pkb_proto_20260927 fixture database through the dedicated writer role.

Run with: python -m pkb_proto.daily_pkb
"""
from __future__ import annotations

import inspect
import json
import os
import re
import time
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Event
from uuid import UUID, uuid4

import psycopg
from psycopg.types.json import Jsonb
from fastapi import HTTPException
from nicegui import app, context, run, ui
from pydantic import BaseModel, Field

from .background_jobs import DaemonSerialBackgroundExecutor
from pkb.correction_service import correct_entity
from ritsuko.core.core_ooda import OODA_PHASES, derive_ooda
from ritsuko.core.core_observation import build_observation_pack
from .core_advisor import advise as advise_core, choose_model as choose_advisor_model, list_chat_models as list_advisor_models
from .core_synthesis import synthesize as synthesize_magi
from ritsuko.core.core_coordinator import (
    can_auto_execute_ambiguous_probe,
    decide_after_observation,
    extend_observation_pack,
)
from ritsuko.magi.protocol import build_request_envelope, default_resource_catalog
from ritsuko.magi.client import (
    call_member as call_magi_member,
    choose_model as choose_magi_model,
    list_chat_models as list_magi_models,
)
from ritsuko.magi.dialogue import MAX_TURNS, export_dialogue
from ritsuko.core.magi_bridge import reviewable_user_knowledge_proposal
from ritsuko.magi.async_execution import (
    continue_with_observation_async,
)
from ritsuko.core.observation_loop import (
    review_proposal as review_magi_proposal,
    run_pkb_observation_loop,
    resume_user_answer,
)
from ritsuko.tasks.magi_task_store import (
    abort_proposal_review as abort_magi_proposal_review,
    abort_user_resume as abort_magi_user_resume,
    claim_proposal_review as claim_magi_proposal_review,
    claim_user_resume as claim_magi_user_resume,
    create_task as create_magi_core_task,
    fail_task as fail_magi_core_task,
    finalize_proposal_review as finalize_magi_proposal_review,
    persist_session as persist_magi_core_session,
    prepare_memory_intake as prepare_magi_memory_intake,
    record_pkb_read as record_magi_pkb_read,
)
from ritsuko.magi.settings import (
    DEFAULT_RETRY_HTTP_CODES,
    DEFAULT_RETRY_WITHIN_TURN,
    DEFAULT_TIMEOUT_SECONDS,
    MEMBER_NAMES,
    PROVIDERS,
    bootstrap_member_assignments,
    fallback_member_specs,
    list_llm_profiles,
    load_member_specs,
    normalize_retry_http_codes,
    provider_defaults,
    save_member_assignments,
    sync_ollama_profiles,
    upsert_llm_profile,
)
from integrations.llm.ollama_runtime import (
    DEFAULT_MAGI_OLLAMA_NUM_PREDICT,
    DEFAULT_OLLAMA_CONTEXT_TOKENS,
    OLLAMA_CONTEXT_OPTIONS,
    OLLAMA_NUM_PREDICT_OPTIONS,
)
from pkb.daily_interpreter import interpret as interpret_daily

from pkb.entity_model_service import (
    COMPONENT_ROLE_TOKENS,
    load_entity_detail,
    list_components,
    resolve_component_reference,
)
from capabilities.finance.finance_preview import analyze_moneyforward_csv
from capabilities.finance.finance_import import (
    commit_import,
    finance_filter_options,
    load_finance_dashboard,
    plan_import,
)
from pkb.ingestion_gate import InputRecord, ProposedClaim
from pkb.query_service import ClaimQuery, query_claims
from pkb.write_service import write_one
from capabilities.web_research.web_research import research_web
from pkb.pending_service import (accept_pending, acceptance_eligible, enqueue as enqueue_pending,
    list_pending, list_reviewed, review_pending)
from capabilities.service_billing.service import ServiceBillingError, read_service_billing_snapshot
from capabilities.service_billing.settings import (
    bootstrap_openai_billing_profile,
    list_service_billing_profiles,
    upsert_service_billing_profile,
)
from integrations.connections.credential_resolver import register_connection_credential_loader
from integrations.connections.service_connections import (
    CONNECTION_TYPES,
    LLM_INFERENCE,
    SERVICE_BILLING_READ,
    adapter_defaults as connection_adapter_defaults,
    bootstrap_connection_auth_from_env,
    connection_adapter_keys,
    get_connection_auth_value,
    get_service_connection,
    list_service_connections,
    upsert_service_connection,
)
from .system_debug import (
    database_snapshot as build_database_snapshot,
    environment_snapshot as build_environment_snapshot,
    runtime_snapshot as build_runtime_snapshot,
)

def _notify_client(client, message: str, *, type: str) -> None:
    """Send a notification through a stable client context.

    Refreshable panels can delete the slot that originated an async callback.
    Re-entering the captured client avoids resolving ui.notify through that
    deleted slot after the panel has been refreshed.
    """
    with client:
        ui.notify(message, type=type)


DBNAME = "secretary_pkb_proto_20260927"
WRITER = "secretary_pkb_proto_writer_20260927"
HOST = "127.0.0.1"

# UI color semantics: green=create/confirm, blue=read/search, orange=edit/review, red=reject/destructive.

PKB_UI_DEFAULT_OPEN = {
    "write": True,
    "correction": False,
    "search": True,
    "entities": False,
    "pending": True,
    "reviewed": False,
    "limits": False,
}

PKB_TAB_ORDER = ("record", "search")
PKB_TAB_LABELS = {
    "record": "記録・訂正",
    "search": "検索・Entity",
}
PKB_TAB_DEFAULT = "record"
PKB_DRAWER_PAGE_SIZE = 4

# Legacy Entity accordion defaults.
# Kept only so existing ui_preferences.json can be migrated safely.
ENTITY_UI_DEFAULT_OPEN = {
    "current": True,
    "relations": True,
    "events": True,
    "history": False,
}

ENTITY_TAB_ORDER = ("overview", "history", "relations", "sources")
ENTITY_TAB_LABELS = {
    "overview": "概要",
    "history": "履歴",
    "relations": "関連",
    "sources": "出典",
}
ENTITY_TAB_DEFAULT = "overview"
ENTITY_TAB_DEFAULT_VISIBLE = {
    key: True for key in ENTITY_TAB_ORDER
}

FINANCE_UI_DEFAULT_OPEN = {
    "filter": True,
    "stored": True,
    "monthly": True,
    "categories": False,
    "details": False,
    "imports": False,
    "csv": False,
}
CORE_UI_DEFAULT_OPEN = {
    "trace": True,
    "screen_log": True,
}
FINANCE_PAGE_SIZE_DEFAULT = 25
FINANCE_PAGE_SIZE_OPTIONS = (25, 50, 100)
CORE_TASK_PAGE_SIZE_OPTIONS = tuple(range(1, 11))
CORE_TASK_LIST_DEFAULTS = {"open_limit": 5, "completed_limit": 5}
CORE_FLOW_STEPS = (
    "User request", "Observation v1", "MELCHIOR + CASPER", "Synthesis",
    "bounded Action / Result", "Observation v2", "re-Orient",
    "Coordinator Guard", "FINAL CORE DECISION",
)
CORE_ADVISOR_TIMEOUT_OPTIONS = (30, 60, 120, 180, 300, 600, 900)

UI_VISIBILITY_DEFAULT = {
    "pkb": {key: True for key in PKB_UI_DEFAULT_OPEN},
    "entity": dict(ENTITY_TAB_DEFAULT_VISIBLE),
    "finance": {key: True for key in FINANCE_UI_DEFAULT_OPEN},
    "core": {
        "trace": True,
        "screen_log": True,
        "limits": True,
    },
}


def _default_ui_preferences() -> dict:
    return {
        "pkb": dict(PKB_UI_DEFAULT_OPEN),
        "entity": {
            "default_tab": ENTITY_TAB_DEFAULT,
        },
        "finance": {
            **FINANCE_UI_DEFAULT_OPEN,
            "recent_limit": FINANCE_PAGE_SIZE_DEFAULT,
        },
        "core": {**CORE_UI_DEFAULT_OPEN, **CORE_TASK_LIST_DEFAULTS},
        "core_advisor_model": None,
        "core_advisor_timeout": 60,
        "visibility": {
            section: dict(values)
            for section, values in UI_VISIBILITY_DEFAULT.items()
        },
    }


def _preferences_path() -> Path:
    override = os.environ.get("LSA_UI_PREFERENCES_PATH", "").strip()
    if override:
        return Path(override)
    # Runtime-only preferences live under gitignored data/, not in PKB data
    # and not in source control. This also survives server restarts.
    return Path(__file__).resolve().parents[1] / "data" / "ui_preferences.json"


def _validate_ui_preferences(raw: object) -> dict:
    result = _default_ui_preferences()
    if not isinstance(raw, dict):
        return result

    pkb = raw.get("pkb")
    if isinstance(pkb, dict):
        for key in PKB_UI_DEFAULT_OPEN:
            value = pkb.get(key)
            if isinstance(value, bool):
                result["pkb"][key] = value

    # New Entity preference:
    #   entity.default_tab = overview/history/relations/sources
    #
    # Old preferences used accordion booleans:
    #   current / relations / events / history
    #
    # If a new default_tab does not exist, use the first legacy section which
    # was configured to open. This keeps old ui_preferences.json usable.
    entity = raw.get("entity")
    if isinstance(entity, dict):
        default_tab = entity.get("default_tab")
        if default_tab in ENTITY_TAB_ORDER:
            result["entity"]["default_tab"] = default_tab
        elif "default_tab" not in entity:
            for legacy_key, tab_key in (
                ("current", "overview"),
                ("events", "history"),
                ("history", "history"),
                ("relations", "relations"),
            ):
                if entity.get(legacy_key) is True:
                    result["entity"]["default_tab"] = tab_key
                    break

    finance = raw.get("finance")
    if isinstance(finance, dict):
        for key in FINANCE_UI_DEFAULT_OPEN:
            value = finance.get(key)
            if isinstance(value, bool):
                result["finance"][key] = value
        page_size = finance.get("recent_limit")
        if type(page_size) is int and page_size in FINANCE_PAGE_SIZE_OPTIONS:
            result["finance"]["recent_limit"] = page_size

    core = raw.get("core")
    if isinstance(core, dict):
        for key in CORE_UI_DEFAULT_OPEN:
            value = core.get(key)
            if isinstance(value, bool):
                result["core"][key] = value

        for key in CORE_TASK_LIST_DEFAULTS:
            value = core.get(key)
            if type(value) is int and value in CORE_TASK_PAGE_SIZE_OPTIONS:
                result["core"][key] = value

    advisor_model = raw.get("core_advisor_model")
    if isinstance(advisor_model, str):
        advisor_model = advisor_model.strip()
        if (
            advisor_model
            and len(advisor_model) <= 100
            and not any(ch.isspace() for ch in advisor_model)
        ):
            result["core_advisor_model"] = advisor_model

    advisor_timeout = raw.get("core_advisor_timeout")
    if (
        type(advisor_timeout) is int
        and advisor_timeout in CORE_ADVISOR_TIMEOUT_OPTIONS
    ):
        result["core_advisor_timeout"] = advisor_timeout

    visibility = raw.get("visibility")
    if isinstance(visibility, dict):
        # PKB / Finance / Core keep their current schema.
        for section, defaults in UI_VISIBILITY_DEFAULT.items():
            if section == "entity":
                continue
            section_values = visibility.get(section)
            if not isinstance(section_values, dict):
                continue
            for key in defaults:
                value = section_values.get(key)
                if isinstance(value, bool):
                    result["visibility"][section][key] = value

        # Entity visibility supports both the new tab schema and the old
        # accordion schema.
        entity_visibility = visibility.get("entity")
        if isinstance(entity_visibility, dict):
            has_new_keys = (
                "overview" in entity_visibility
                or "sources" in entity_visibility
            )

            if has_new_keys:
                for key in ENTITY_TAB_ORDER:
                    value = entity_visibility.get(key)
                    if isinstance(value, bool):
                        result["visibility"]["entity"][key] = value
            else:
                current_value = entity_visibility.get("current")
                if isinstance(current_value, bool):
                    result["visibility"]["entity"]["overview"] = current_value

                relations_value = entity_visibility.get("relations")
                if isinstance(relations_value, bool):
                    result["visibility"]["entity"]["relations"] = relations_value

                legacy_history_values = [
                    entity_visibility[key]
                    for key in ("events", "history")
                    if isinstance(entity_visibility.get(key), bool)
                ]
                if legacy_history_values:
                    result["visibility"]["entity"]["history"] = any(
                        legacy_history_values
                    )

                # "sources" did not exist in the old GUI.
                # New installations and migrated old settings show it by default.
                result["visibility"]["entity"]["sources"] = True

    # Entity detail must always have at least one visible tab.
    entity_visible = result["visibility"]["entity"]
    if not any(entity_visible.values()):
        entity_visible["overview"] = True

    # A hidden tab cannot be the initial tab.
    preferred_tab = result["entity"]["default_tab"]
    if not entity_visible.get(preferred_tab, False):
        result["entity"]["default_tab"] = next(
            key for key in ENTITY_TAB_ORDER if entity_visible.get(key, False)
        )

    return result


def load_ui_preferences(path: Path | None = None) -> dict:
    target = path or _preferences_path()
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, UnicodeDecodeError, json.JSONDecodeError):
        return _default_ui_preferences()
    return _validate_ui_preferences(raw)


def save_ui_preferences(preferences: dict, path: Path | None = None) -> dict:
    target = path or _preferences_path()
    validated = _validate_ui_preferences(preferences)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_text(
        json.dumps(validated, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(target)
    return validated


_UI_PREFERENCES = load_ui_preferences()

# Current navigation state is separate from saved defaults. It preserves the
# user's current accordion layout while moving between pages, while settings
# can deliberately reset the live state to newly saved defaults.
_PKB_UI_OPEN = dict(_UI_PREFERENCES["pkb"])
_FINANCE_UI_OPEN = {
    key: _UI_PREFERENCES["finance"][key]
    for key in FINANCE_UI_DEFAULT_OPEN
}
_CORE_UI_OPEN = {key: _UI_PREFERENCES["core"][key] for key in CORE_UI_DEFAULT_OPEN}


def _apply_ui_preferences(preferences: dict) -> None:
    global _UI_PREFERENCES
    validated = _validate_ui_preferences(preferences)
    _UI_PREFERENCES = validated
    _PKB_UI_OPEN.clear()
    _PKB_UI_OPEN.update(validated["pkb"])
    _FINANCE_UI_OPEN.clear()
    _FINANCE_UI_OPEN.update(
        {key: validated["finance"][key] for key in FINANCE_UI_DEFAULT_OPEN}
    )
    _CORE_UI_OPEN.clear()
    _CORE_UI_OPEN.update({key: validated["core"][key] for key in CORE_UI_DEFAULT_OPEN})


def _set_pkb_ui_open(key: str, value: bool) -> None:
    if key not in PKB_UI_DEFAULT_OPEN:
        raise KeyError("unknown PKB accordion key")
    _PKB_UI_OPEN[key] = bool(value)


def _set_finance_ui_open(key: str, value: bool) -> None:
    if key not in FINANCE_UI_DEFAULT_OPEN:
        raise KeyError("unknown finance accordion key")
    _FINANCE_UI_OPEN[key] = bool(value)


def _set_core_ui_open(key: str, value: bool) -> None:
    if key not in CORE_UI_DEFAULT_OPEN:
        raise KeyError("unknown Core accordion key")
    _CORE_UI_OPEN[key] = bool(value)


def _save_core_advisor_settings(model: str | None, timeout_seconds: int) -> tuple[str | None, int]:
    preferences = {
        **_UI_PREFERENCES,
        "pkb": dict(_UI_PREFERENCES["pkb"]),
        "entity": dict(_UI_PREFERENCES["entity"]),
        "finance": dict(_UI_PREFERENCES["finance"]),
        "core": dict(_UI_PREFERENCES["core"]),
        "visibility": {
            section: dict(values)
            for section, values in _UI_PREFERENCES["visibility"].items()
        },
        "core_advisor_model": model,
        "core_advisor_timeout": timeout_seconds,
    }
    saved = save_ui_preferences(preferences)
    _apply_ui_preferences(saved)
    return saved.get("core_advisor_model"), int(saved["core_advisor_timeout"])


def _block_visibility_class(section: str, key: str) -> str:
    visible = _UI_PREFERENCES["visibility"][section][key]
    return "" if visible else " hidden"

def _entity_tab_config(
    preferences: dict | None = None,
) -> tuple[dict[str, bool], str]:
    prefs = preferences or _UI_PREFERENCES

    visible = {
        key: bool(
            prefs.get("visibility", {})
            .get("entity", {})
            .get(key, False)
        )
        for key in ENTITY_TAB_ORDER
    }

    if not any(visible.values()):
        visible["overview"] = True

    preferred = (
        prefs.get("entity", {}).get("default_tab")
        or ENTITY_TAB_DEFAULT
    )

    if (
        preferred not in ENTITY_TAB_ORDER
        or not visible.get(preferred, False)
    ):
        preferred = next(
            key for key in ENTITY_TAB_ORDER if visible.get(key, False)
        )

    return visible, preferred


def _entity_source_rows(detail: dict) -> list[dict]:
    """Deduplicate Sources already referenced by Entity claims/relations."""
    sources: dict[str, dict] = {}

    for section_key, section_label in (
        ("current", "現在"),
        ("events", "Event"),
        ("history", "履歴"),
        ("relations", "関連"),
    ):
        for row in detail.get(section_key) or []:
            uri = str(row.get("source_uri") or "").strip()
            if not uri:
                continue

            item = sources.setdefault(
                uri,
                {
                    "source_uri": uri,
                    "areas": set(),
                    "reference_count": 0,
                },
            )
            item["areas"].add(section_label)
            item["reference_count"] += 1

    result = []
    for index, uri in enumerate(sorted(sources), start=1):
        item = sources[uri]
        result.append(
            {
                "id": str(index),
                "source_uri": uri,
                "used_by": " / ".join(sorted(item["areas"])),
                "reference_count": item["reference_count"],
            }
        )

    return result


COMPONENT_WRITE_PATTERN = re.compile(
    r"^(?P<parent>.+?)の(?P<role>GPU|NIC)ドライバーを"
    r"(?P<value>[A-Za-z0-9._-]+)へ"
    r"(?P<action>更新した|更新しておいた|アップデートした)[。.]?$"
)
COMPONENT_STATE_QUERY_PATTERN = re.compile(
    r"^(?P<parent>.+?)の(?P<role>GPU|NIC)の?"
    r"(?:現在の|今の|現行の?)?ドライバー"
)

ACTION_PATTERNS = (
    ("driver_updated", re.compile(r"^(?P<entity>.+?)(?:を)?(?P<value>[A-Za-z0-9._-]+)へ更新した[。.]?$")),
    ("servo_updated", re.compile(r"^(?P<entity>.+?)(?:のサーボ)?を(?P<value>[A-Za-z0-9._-]+)へ交換した[。.]?$")),
)
CORRECTION_PATTERNS = (
    ("driver_updated", re.compile(
        r"^訂正[。：: ]*(?P<old>.+?)ではなく(?P<new>.+?)(?:を)?(?P<value>[A-Za-z0-9._-]+)へ更新した[。.]?$"
    )),
    ("servo_updated", re.compile(
        r"^訂正[。：: ]*(?P<old>.+?)ではなく(?P<new>.+?)(?:のサーボ)?を(?P<value>[A-Za-z0-9._-]+)へ交換した[。.]?$"
    )),
)


def _port() -> int:
    raw = os.environ.get("LSA_PKB_DAILY_PORT", "")
    try:
        value = int(raw)
    except ValueError as exc:
        raise RuntimeError("LSA_PKB_DAILY_PORT is missing or invalid") from exc
    if not 1024 <= value <= 65535:
        raise RuntimeError("PKB DB port is outside the allowed range")
    return value


def _secret_path() -> Path:
    raw = os.environ.get("LSA_PKB_DAILY_SECRET", "")
    path = Path(raw) if raw else Path()
    if not raw or not path.is_file():
        raise RuntimeError("LSA_PKB_DAILY_SECRET does not point to the prototype secret")
    return path


def connection():
    password = _secret_path().read_text(encoding="utf-8").strip()
    if len(password) < 32:
        raise RuntimeError("Invalid prototype writer secret")
    db = psycopg.connect(
        dbname=DBNAME,
        host=HOST,
        port=_port(),
        user=WRITER,
        password=password,
        connect_timeout=5,
        autocommit=True,
    )
    info = db.info
    if (info.dbname or "") != DBNAME or (info.user or "") != WRITER or (info.host or "") not in (
        "127.0.0.1", "localhost", "::1"
    ):
        db.close()
        raise RuntimeError("Refusing a non-isolated PKB database")
    return db


def _connection_credential_loader(connection_id: str) -> str | None:
    with connection() as db:
        return get_connection_auth_value(db, connection_id, "api_key")


register_connection_credential_loader(_connection_credential_loader)


def _load_magi_configuration(
    installed_ollama_models: list[str],
    default_local_model: str | None,
) -> tuple[list[dict], list[dict]]:
    """Load DB settings, importing env defaults only when DB has no assignments."""
    with connection() as db:
        bootstrap_connection_auth_from_env(db)
        sync_ollama_profiles(
            db,
            installed_ollama_models,
            endpoint=os.environ.get("OLLAMA_HOST") or None,
        )
        bootstrap_member_assignments(
            db, fallback_member_specs(default_local_model)
        )
        return list_llm_profiles(db), load_member_specs(db)


def _save_magi_assignments(assignments: list[dict]) -> list[dict]:
    with connection() as db:
        return save_member_assignments(db, assignments)


def _register_magi_profile(
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
    profile_id: str | None = None,
) -> dict:
    with connection() as db:
        return upsert_llm_profile(
            db,
            model=model,
            connection_id=connection_id,
            provider=provider,
            display_name=display_name,
            endpoint=endpoint,
            credential_env=credential_env,
            context_window_tokens=context_window_tokens,
            ollama_num_predict=ollama_num_predict,
            retry_http_codes=retry_http_codes,
            enabled=True,
            profile_id=profile_id,
        )


def _entities(db) -> list[dict]:
    with db.cursor() as cur:
        cur.execute(
            """SELECT id, name, domain, entity_type
               FROM secretary.entities
               WHERE retired_at IS NULL
               ORDER BY domain, name"""
        )
        return [
            {"id": str(row[0]), "name": row[1], "domain": row[2], "entity_type": row[3]}
            for row in cur.fetchall()
        ]


def _entity_map(db) -> dict[str, dict]:
    return {row["name"]: row for row in _entities(db)}


def parse_component_write(text: str) -> dict | None:
    match = COMPONENT_WRITE_PATTERN.fullmatch(text.strip())
    if not match:
        return None
    return {
        "parent": match.group("parent").strip(),
        "role": match.group("role"),
        "predicate": "driver_updated",
        "value": match.group("value"),
        "action": match.group("action"),
    }


def parse_write(text: str, entity_names: set[str]) -> dict | None:
    text = text.strip()
    for predicate, pattern in ACTION_PATTERNS:
        match = pattern.fullmatch(text)
        if not match:
            continue
        entity = match.group("entity").strip()
        if entity not in entity_names:
            return {"status": "review", "reason": "unknown_or_ambiguous_entity", "entity": entity}
        return {
            "status": "parsed",
            "intent": "assertion",
            "entity": entity,
            "predicate": predicate,
            "value": match.group("value"),
        }
    return None


def parse_correction(text: str, entity_names: set[str]) -> dict | None:
    text = text.strip()
    for predicate, pattern in CORRECTION_PATTERNS:
        match = pattern.fullmatch(text)
        if not match:
            continue
        old_name = match.group("old").strip()
        new_name = match.group("new").strip()
        if old_name not in entity_names or new_name not in entity_names:
            return {
                "status": "review",
                "reason": "unknown_or_ambiguous_entity",
                "old_entity": old_name,
                "new_entity": new_name,
            }
        if old_name == new_name:
            return {"status": "review", "reason": "correction_entities_are_same"}
        return {
            "status": "parsed",
            "intent": "correction",
            "old_entity": old_name,
            "new_entity": new_name,
            "predicate": predicate,
            "value": match.group("value"),
        }
    return None


def parse_query(text: str, entities: dict[str, dict]) -> ClaimQuery:
    q = text.strip()
    entity_id = None
    for name, row in sorted(entities.items(), key=lambda item: len(item[0]), reverse=True):
        if name in q:
            entity_id = UUID(row["id"])
            break
    predicate = None
    if "ドライバ" in q or "DRV-" in q:
        predicate = (
            "current_driver"
            if any(word in q for word in ("現在", "今の", "現行", "現在値"))
            else "driver_updated"
        )
    elif "サーボ" in q or "SERVO-" in q:
        predicate = "servo_updated"
    include_history = any(word in q for word in ("履歴", "過去", "全部", "すべて", "訂正前"))
    return ClaimQuery(
        entity_id=entity_id,
        predicate=predicate,
        effective_at=(datetime.now(timezone.utc) if predicate == "current_driver" else None),
        include_history=include_history,
        limit=100,
        offset=0,
    )


def _serialize_page(page) -> dict:
    items = []
    for row in page.items:
        item = {}
        for key, value in row.items():
            if isinstance(value, (datetime, UUID)):
                item[key] = str(value)
            else:
                item[key] = value
        items.append(item)
    return {
        "status": page.status,
        "total": page.total,
        "items": items,
        "known_at": page.known_at.isoformat(),
        "include_history": page.include_history,
    }


def register_text(text: str) -> dict:
    text = text.strip()
    if not text:
        return {"status": "rejected", "reason": "empty_input"}
    with connection() as db:
        entities = _entity_map(db)
        component_parsed = parse_component_write(text)
        component_entity = None
        resolved_entity_mention = None
        if component_parsed is not None:
            component_entity = resolve_component_reference(
                db, component_parsed["parent"], component_parsed["role"]
            )
            if component_entity is None:
                parsed = {
                    "status": "review",
                    "reason": "component_relation_not_unique_or_missing",
                }
            else:
                resolved_entity_mention = (
                    component_parsed["parent"] + "の" + component_parsed["role"]
                )
                parsed = {
                    "status": "parsed",
                    "intent": "assertion",
                    "entity": component_entity["name"],
                    "predicate": component_parsed["predicate"],
                    "value": component_parsed["value"],
                }
                entities[component_entity["name"]] = {
                    "id": component_entity["id"],
                    "name": component_entity["name"],
                    "domain": "pc",
                    "entity_type": component_entity["entity_type"],
                }
        else:
            parsed = parse_write(text, set(entities))
        if parsed is None or parsed["status"] != "parsed":
            interpreted = interpret_daily(text, set(entities))
            if interpreted.status == "candidate" and interpreted.candidate is not None:
                candidate = interpreted.candidate
                entity = entities[candidate.entity_mention]
                result = enqueue_pending(
                    db,
                    input_id="daily-pkb-model-review-" + uuid4().hex,
                    raw_text=text,
                    reason="model_candidate_needs_user_confirmation",
                    entity_id=entity["id"],
                    predicate=candidate.predicate,
                    proposed_value=candidate.value,
                    interpreter_kind="local_ollama",
                    interpreter_model=interpreted.model,
                )
                payload = asdict(result)
                payload["message"] = (
                    "ローカルLLMが出典付き候補を作成しました。"
                    "自動登録せず、確認待ちから承認・要修正・却下を選べます。"
                )
                return payload

            reason = {
                "no_candidate": "local_interpreter_no_safe_candidate:" + interpreted.reason,
                "invalid": "local_interpreter_invalid_candidate:" + interpreted.reason,
                "unavailable": "local_interpreter_unavailable:" + interpreted.reason,
            }.get(interpreted.status, "local_interpreter_no_safe_candidate")
            result = enqueue_pending(
                db,
                input_id="daily-pkb-review-" + uuid4().hex,
                raw_text=text,
                reason=reason,
                interpreter_kind=("local_ollama" if interpreted.model else None),
                interpreter_model=interpreted.model,
            )
            payload = asdict(result)
            payload["message"] = (
                "安全に構造化できなかったため、元の入力をそのまま確認待ちに保存しました。"
            )
            return payload
        entity = entities[parsed["entity"]]
        input_id = "daily-pkb-" + uuid4().hex
        now = datetime.now(timezone.utc)
        record = InputRecord(
            input_id=input_id,
            source_kind="user_statement",
            source_ref="fixture://daily-pkb/" + input_id,
            text=text,
            recorded_at=now,
            occurred_at=now,
        )
        claim = ProposedClaim(
            entity_key=entity["id"],
            entity_mention=resolved_entity_mention or entity["name"],
            predicate=parsed["predicate"],
            value=parsed["value"],
            evidence_start=0,
            evidence_end=len(text),
            evidence_quote=text,
        )
        result = write_one(db, record, claim)
        if result.status == "review":
            pending = enqueue_pending(db, input_id="daily-pkb-review-" + uuid4().hex, raw_text=text, reason=result.reason, entity_id=entity["id"], predicate=parsed["predicate"], proposed_value=parsed["value"])
            return asdict(pending)
        return asdict(result)


def correct_text(text: str) -> dict:
    text = text.strip()
    if not text:
        return {"status": "rejected", "reason": "empty_input"}
    with connection() as db:
        entities = _entity_map(db)
        parsed = parse_correction(text, set(entities))
        if parsed is None:
            result = enqueue_pending(
                db,
                input_id="daily-pkb-correction-review-" + uuid4().hex,
                raw_text=text,
                reason="unsupported_correction_in_first_slice",
            )
            payload = asdict(result)
            payload["message"] = "「訂正：旧Entityではなく新Entityを値へ更新した。」形式の明示訂正のみ扱います。"
            return payload
        if parsed["status"] != "parsed":
            result = enqueue_pending(db, input_id="daily-pkb-correction-review-" + uuid4().hex, raw_text=text, reason=parsed["reason"])
            return asdict(result)
        old = entities[parsed["old_entity"]]
        new = entities[parsed["new_entity"]]
        with db.cursor() as cur:
            cur.execute(
                """SELECT c.id, c.valid_from
                   FROM secretary.claims c
                   JOIN secretary.sources s ON s.id=c.source_id
                   WHERE c.entity_id=%s AND c.predicate=%s
                     AND c.value=%s::jsonb
                     AND c.origin='user_explicit'
                     AND c.verification_status='unverified'
                     AND c.retracted_at IS NULL
                     AND s.uri LIKE 'fixture://%%'
                   ORDER BY c.recorded_at DESC""",
                (UUID(old["id"]), parsed["predicate"], '"' + parsed["value"] + '"'),
            )
            rows = cur.fetchall()
        if len(rows) != 1:
            result = enqueue_pending(db, input_id="daily-pkb-correction-review-" + uuid4().hex, raw_text=text, reason="correction_target_not_unique", entity_id=old["id"], predicate=parsed["predicate"], proposed_value=parsed["value"])
            payload = asdict(result)
            payload["candidate_count"] = len(rows)
            return payload
        old_claim_id, valid_from = rows[0]
        input_id = "daily-pkb-correction-" + uuid4().hex
        now = datetime.now(timezone.utc)
        record = InputRecord(
            input_id=input_id,
            source_kind="user_statement",
            source_ref="fixture://daily-pkb/" + input_id,
            text=text,
            recorded_at=now,
            occurred_at=valid_from,
        )
        claim = ProposedClaim(
            entity_key=new["id"],
            entity_mention=new["name"],
            predicate=parsed["predicate"],
            value=parsed["value"],
            evidence_start=0,
            evidence_end=len(text),
            evidence_quote=text,
            intent="correction",
            corrects_claim_id=str(old_claim_id),
        )
        result = correct_entity(db, record, claim)
        if result.status == "review":
            pending = enqueue_pending(db, input_id="daily-pkb-correction-review-" + uuid4().hex, raw_text=text, reason=result.reason, entity_id=new["id"], predicate=parsed["predicate"], proposed_value=parsed["value"])
            return asdict(pending)
        return asdict(result)


def _search_text_with_db(db, text: str) -> dict:
    entities = _entity_map(db)
    q = text.strip()

    component_state = COMPONENT_STATE_QUERY_PATTERN.search(q)
    if component_state and any(word in q for word in ("現在", "今の", "現行")):
        resolved = resolve_component_reference(
            db,
            component_state.group("parent").strip(),
            component_state.group("role"),
        )
        if resolved is not None:
            page = query_claims(
                db,
                ClaimQuery(
                    entity_id=UUID(resolved["id"]),
                    predicate="current_driver",
                    effective_at=datetime.now(timezone.utc),
                    include_history=False,
                    limit=100,
                    offset=0,
                ),
            )
            result = _serialize_page(page)
            result["result_kind"] = "claims"
            return result

    if "構成" in q:
        parent = next(
            (
                row for name, row in sorted(
                    entities.items(), key=lambda item: len(item[0]), reverse=True
                )
                if name in q and row["entity_type"] == "computer"
            ),
            None,
        )
        if parent is not None:
            rows = list_components(db, UUID(parent["id"]))
            items = []
            for row in rows:
                item = {}
                for key, value in row.items():
                    if isinstance(value, (datetime, UUID)):
                        item[key] = str(value)
                    else:
                        item[key] = value
                items.append(item)
            return {
                "status": "ok",
                "result_kind": "components",
                "total": len(items),
                "items": items,
            }
    page = query_claims(db, parse_query(text, entities))
    result = _serialize_page(page)
    result["result_kind"] = "claims"
    return result


def search_text(text: str) -> dict:
    with connection() as db:
        return _search_text_with_db(db, text)


def _core_finance_filters(text: str) -> dict:
    """Parse the intentionally small date scope supported by the first finance Core slice."""
    q = text.strip()
    start_date = None
    end_date = None

    explicit = re.search(r"(?P<year>20\d{2})年(?P<month>1[0-2]|0?[1-9])月", q)
    if explicit:
        year = int(explicit.group("year"))
        month = int(explicit.group("month"))
        start = datetime(year, month, 1).date()
        if month == 12:
            next_month = datetime(year + 1, 1, 1).date()
        else:
            next_month = datetime(year, month + 1, 1).date()
        start_date = start.isoformat()
        end_date = (next_month - timedelta(days=1)).isoformat()
    elif "今月" in q:
        today = datetime.now().date()
        start = today.replace(day=1)
        if today.month == 12:
            next_month = today.replace(year=today.year + 1, month=1, day=1)
        else:
            next_month = today.replace(month=today.month + 1, day=1)
        start_date = start.isoformat()
        end_date = (next_month - timedelta(days=1)).isoformat()

    return {
        "start_date": start_date,
        "end_date": end_date,
        "row_mode": "calculation_target",
    }


def finance_text(text: str) -> dict:
    filters = _core_finance_filters(text)
    with connection() as db:
        dashboard = load_finance_dashboard(
            db,
            recent_limit=5,
            start_date=filters["start_date"],
            end_date=filters["end_date"],
            row_mode=filters["row_mode"],
            page=1,
            sort_by="date",
            sort_dir="desc",
        )
    return {
        "status": "ok",
        "result_kind": "finance_summary",
        "total": dashboard.transaction_count,
        "transaction_count": dashboard.transaction_count,
        "calculation_target_count": dashboard.calculation_target_count,
        "requested_start_date": filters["start_date"],
        "requested_end_date": filters["end_date"],
        "data_start_date": dashboard.start_date,
        "data_end_date": dashboard.end_date,
        "income_total": dashboard.income_total,
        "expense_total": dashboard.expense_total,
        "net_total": dashboard.net_total,
        "monthly": dashboard.monthly[:12],
        "categories": dashboard.categories[:10],
        "recent_rows": dashboard.recent_rows[:5],
        "import_batches": dashboard.import_batches[:1],
    }


def finance_core_answer(result: dict) -> str:
    if int(result.get("total") or 0) == 0:
        return "保存済み家計に該当する明細が見つかりませんでした。"
    period = ""
    if result.get("requested_start_date") or result.get("requested_end_date"):
        period = (
            f"{result.get('requested_start_date') or '-'}〜"
            f"{result.get('requested_end_date') or '-'}の"
        )
    return (
        f"保存済み家計では、{period}集計対象は{result['transaction_count']}件、"
        f"収入は¥{int(result['income_total']):,}、"
        f"支出は¥{int(result['expense_total']):,}、"
        f"収支は¥{int(result['net_total']):,}です。"
    )


def web_core_answer(result: dict) -> str:
    hits = result.get("hits") or []
    facts = result.get("fact_summary") or {}
    if facts.get("kind") == "driver_version":
        if facts.get("status") == "primary_source_no_current_candidate":
            primary = ", ".join(facts.get("primary_domains") or []) or "一次Source候補"
            secondary = []
            for group in facts.get("groups") or []:
                for candidate in group.get("candidates") or []:
                    if int(candidate.get("primary_source_count") or 0) == 0:
                        secondary.append(
                            f"{group.get('kind')}={candidate.get('value')}"
                        )
            secondary_text = " / ".join(secondary[:4])
            return (
                f"Web調査では一次Source候補（{primary}）を確認しましたが、"
                "そこから現在版の番号を抽出できませんでした。"
                + (f" 第三者候補: {secondary_text}。" if secondary_text else "")
                + " 一次Sourceで確認できるまで最新値として確定しません。"
            )

        groups = facts.get("groups") or []
        preferred_kind = facts.get("preferred_kind")
        preferred = next(
            (group for group in groups if group.get("kind") == preferred_kind),
            groups[0] if groups else None,
        )
        if preferred:
            status = preferred.get("status")
            best = preferred.get("best_candidate")
            candidates = preferred.get("candidates") or []
            kind_label = {
                "adrenalin_version": "Adrenalin版",
                "driver_version": "ドライバー版",
                "si_driver_version": "SI Driver版",
            }.get(preferred.get("kind"), preferred.get("kind") or "版番号")
            other_groups = [
                group for group in groups if group is not preferred and group.get("best_candidate")
            ]
            other_text = ""
            if other_groups:
                other_text = " 別種の版番号: " + " / ".join(
                    f"{group.get('kind')}={group.get('best_candidate')}"
                    for group in other_groups[:3]
                ) + "。"

            if status == "leading_consensus" and best:
                leading = candidates[0] if candidates else {}
                return (
                    f"Web調査では{kind_label}候補 {best} が"
                    f"{leading.get('source_count', 0)}件のSourceで一致しています。"
                    + other_text
                    + "異なる種類の版番号同士は競合扱いしていません。"
                )
            if status == "latest_by_date" and best:
                leading = candidates[0] if candidates else {}
                return (
                    f"Web調査では{kind_label}候補 {best} が、"
                    f"近傍日付 {leading.get('latest_date') or '-'} を持つ最新候補として上位です。"
                    + other_text
                    + "ただし日付対応はページ本文の近傍文脈から抽出したため、一次Source表示で最終確認してください。"
                )
            if status == "single_candidate" and best:
                return (
                    f"Web調査では{kind_label}候補 {best} を1系統で抽出しました。"
                    + other_text
                    + "同じ種類の複数Source一致はまだ確認できていないため、確定値とは扱いません。"
                )
            if status == "conflicting_candidates":
                values = " / ".join(
                    str(row.get("value")) for row in candidates[:4] if row.get("value")
                )
                return (
                    f"Web調査では同じ種類の{kind_label}候補が一致していません。"
                    f"候補: {values}。"
                    + other_text
                    + "一次Sourceと対象期間を追加確認する必要があります。"
                )

    if not hits:
        return "Web検索で結果が見つかりませんでした。"

    parts = []
    for hit in hits[:3]:
        title = hit.get("title") or hit.get("url") or "検索結果"
        snippet = (hit.get("snippet") or "").strip()
        if len(snippet) > 220:
            snippet = snippet[:217] + "..."
        parts.append(title + (f" — {snippet}" if snippet else ""))
    return "Web調査では、根拠候補の上位は " + " / ".join(parts) + "。"


def web_text(text: str) -> dict:
    result = research_web(text, max_results=5, max_fetches=2).as_dict()
    result["status"] = "ok"
    result["result_kind"] = "web_research"
    return result


_VERSION_VALUE_PATTERN = re.compile(r"\b\d{2,4}\.\d{1,3}(?:\.\d{1,4}){1,2}\b")


def _pkb_current_driver_value(result: dict) -> str | None:
    for row in result.get("items") or []:
        value = row.get("current_driver")
        if value:
            return str(value).strip()
        if row.get("predicate") == "current_driver" and row.get("value"):
            return str(row.get("value")).strip()
    return None


def _web_latest_version_value(result: dict) -> tuple[str | None, str | None, str | None]:
    summary = result.get("fact_summary") or {}
    return (
        summary.get("best_candidate"),
        summary.get("preferred_kind"),
        summary.get("status"),
    )


def _compare_driver_values(pkb_result: dict, web_result: dict) -> dict:
    current = _pkb_current_driver_value(pkb_result)
    latest, latest_kind, web_status = _web_latest_version_value(web_result)

    if not current or not latest:
        return {
            "status": "insufficient_evidence",
            "current": current,
            "latest": latest,
            "latest_kind": latest_kind,
            "web_status": web_status,
            "message": "PKB現在値またはWeb最新候補が不足しているため比較できません。",
        }

    current_match = _VERSION_VALUE_PATTERN.search(current)
    latest_match = _VERSION_VALUE_PATTERN.search(str(latest))
    if not current_match or not latest_match:
        return {
            "status": "not_comparable",
            "current": current,
            "latest": str(latest),
            "latest_kind": latest_kind,
            "web_status": web_status,
            "message": (
                f"PKB現在値は {current}、Web最新候補は {latest} ですが、"
                "同じ版番号形式として安全に比較できません。"
            ),
        }

    current_value = current_match.group(0)
    latest_value = latest_match.group(0)
    same = current_value == latest_value
    return {
        "status": "match" if same else "different",
        "current": current_value,
        "latest": latest_value,
        "latest_kind": latest_kind,
        "web_status": web_status,
        "message": (
            f"PKB現在値 {current_value} とWeb最新候補 {latest_value} は一致しています。"
            if same
            else f"PKB現在値 {current_value} とWeb最新候補 {latest_value} は異なります。"
        ),
    }


def _plain_claim_value(value):
    if isinstance(value, str):
        return value.strip()
    return str(value).strip() if value is not None else None


def _driver_web_query_from_detail(detail: dict | None) -> dict:
    if not detail:
        return {
            "status": "missing_target",
            "query": None,
            "manufacturer": None,
            "model": None,
            "entity_name": None,
        }

    current = detail.get("current") or []
    attrs = {}
    for row in current:
        predicate = row.get("predicate")
        if predicate in {"manufacturer", "model"} and predicate not in attrs:
            value = _plain_claim_value(row.get("value"))
            if value:
                attrs[predicate] = value

    entity = detail.get("entity") or {}
    entity_name = str(entity.get("name") or "").strip() or None
    manufacturer = attrs.get("manufacturer")
    model = attrs.get("model")

    # Prefer explicit authoritative attributes. A generic Entity name such as
    # GPU1 is not safe enough to send to web search as the product identity.
    if not model:
        return {
            "status": "missing_model",
            "query": None,
            "manufacturer": manufacturer,
            "model": None,
            "entity_name": entity_name,
        }

    parts = [part for part in (manufacturer, model) if part]
    return {
        "status": "ready",
        "query": " ".join(parts) + " latest driver official",
        "manufacturer": manufacturer,
        "model": model,
        "entity_name": entity_name,
    }


def _resolve_driver_web_target(text: str) -> dict:
    component_state = COMPONENT_STATE_QUERY_PATTERN.search(text.strip())
    if not component_state:
        return {
            "status": "missing_component_reference",
            "query": None,
            "manufacturer": None,
            "model": None,
            "entity_name": None,
        }

    parent_name = component_state.group("parent").strip()
    role_token = component_state.group("role")
    with connection() as db:
        resolved = resolve_component_reference(db, parent_name, role_token)
        if resolved is None:
            return {
                "status": "component_not_unique_or_missing",
                "query": None,
                "manufacturer": None,
                "model": None,
                "entity_name": None,
            }
        detail = load_entity_detail(db, resolved["id"])
    target = _driver_web_query_from_detail(detail)
    target["parent_name"] = parent_name
    target["role_token"] = role_token
    target["component_id"] = resolved["id"]
    return target


def _clarified_driver_web_target(reply: str) -> dict:
    value = reply.strip()
    value = re.sub(
        r"^(?:GPU(?:の)?モデル|モデル|製品名|GPU)\s*(?:は|:|：)?\s*",
        "",
        value,
        flags=re.IGNORECASE,
    ).strip()
    if not value:
        return {
            "status": "missing_model",
            "query": None,
            "manufacturer": None,
            "model": None,
            "entity_name": None,
        }
    return {
        "status": "ready",
        "query": value + " latest driver official",
        "manufacturer": None,
        "model": value,
        "entity_name": None,
        "clarified_by_user": True,
    }


def _execute_pkb_web_compare(text: str, *, target_override: str | None = None) -> dict:
    pkb = _execute_core_read("pkb_search", text)
    target = (
        _clarified_driver_web_target(target_override)
        if target_override is not None
        else _resolve_driver_web_target(text)
    )
    if target.get("status") != "ready":
        comparison = {
            "status": "insufficient_target",
            "current": _pkb_current_driver_value(pkb["result"]),
            "latest": None,
            "latest_kind": None,
            "web_status": None,
            "web_query": None,
            "target": target,
            "message": (
                "PKBで現在ドライバーは取得できましたが、Web検索に使うGPUの"
                "manufacturer / model をPKBから安全に特定できません。"
                "GPUモデルをPKBへ登録するか、依頼で明示してください。"
            ),
        }
        return {
            "capability": "pkb_web_compare",
            "executions": [pkb],
            "comparison": comparison,
            "answer": comparison["message"],
            "pkb": pkb["result"],
            "web": None,
            "web_query": None,
            "needs_clarification": True,
        }

    web_query = target["query"]
    web = _execute_core_read("web_research", web_query)
    comparison = _compare_driver_values(pkb["result"], web["result"])
    comparison["web_query"] = web_query
    comparison["target"] = target
    return {
        "capability": "pkb_web_compare",
        "executions": [pkb, web],
        "comparison": comparison,
        "answer": comparison["message"],
        "pkb": pkb["result"],
        "web": web["result"],
        "web_query": web_query,
        "needs_clarification": False,
    }


def _json_safe(value):
    """Convert bounded Core evidence into JSON-safe values without dropping structure."""
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return value


def _execute_cooperative_local_probe(
    capability: str,
    text: str,
    observation_pack: dict,
) -> dict:
    """Expand a synthesized local probe without broadening to finance or Web."""
    if capability != "pkb_search":
        raise ValueError("Cooperative ambiguous probe only permits pkb_search")

    matched = list(observation_pack.get("matched_entities") or [])
    target = (
        matched[0]
        if len(matched) == 1 and matched[0].get("entity_type") == "computer"
        else None
    )
    if target and target.get("id"):
        with connection() as db:
            detail = _json_safe(
                load_entity_detail(db, str(target["id"])) or {}
            )
            items = _json_safe(
                list_components(db, UUID(str(target["id"])))
            )
        result = {
            "status": "ok",
            "result_kind": "components",
            "total": len(items),
            "items": items,
            "entity": detail.get("entity"),
            "current": (detail.get("current") or [])[:12],
            "relations": [
                row for row in (detail.get("relations") or [])
                if row.get("valid_to") is None
            ][:12],
            "events": (detail.get("events") or [])[:12],
            "probe_kind": "entity_overview",
        }
        return {
            "capability": "pkb_search",
            "result": result,
            "answer": core_answer(result),
            "total": len(items),
            "tool": "pkb",
            "operation": "entity_overview",
            "source_slug": "pkb-overview",
            "citation": "Secretary Core bounded PKB Entity/Component overview",
            "verified_by": "deterministic_pkb_query",
            "source_metadata": {
                "probe_kind": "entity_overview",
                "entity_id": str(target["id"]),
                "entity_name": target.get("name"),
            },
        }

    return _execute_core_read("pkb_search", text)


def _execute_magi_pkb_request(
    user_raw: str,
    pending_request: dict,
) -> dict:
    """Execute one deterministic bounded PKB read requested by MAGI.

    MAGI chooses the information source and describes the needed fact.
    RITSUKO resolves only already-modelled Entity/component identifiers and
    performs the actual local read. It does not invent a new semantic route.
    """
    requested = str(pending_request.get("what") or "").strip()
    combined = (user_raw.strip() + "\n" + requested).strip()

    with connection() as db:
        entities = _entity_map(db)
        parents = [
            row for name, row in sorted(
                entities.items(), key=lambda item: len(item[0]), reverse=True
            )
            if name and name in combined and row.get("entity_type") == "computer"
        ]
        roles = [token for token in COMPONENT_ROLE_TOKENS if token in combined]

        if len(parents) == 1 and len(roles) == 1:
            parent = parents[0]
            component = resolve_component_reference(
                db, parent["name"], roles[0]
            )
            if component is not None:
                detail = _json_safe(
                    load_entity_detail(db, component["id"]) or {}
                )
                current = [
                    item for item in (detail.get("current") or [])
                    if item.get("valid_to") is None
                ][:20]
                values = {
                    str(item.get("predicate")): item.get("value")
                    for item in current
                    if item.get("predicate")
                }
                manufacturer = str(values.get("manufacturer") or "").strip()
                model = str(values.get("model") or "").strip()
                driver = str(values.get("current_driver") or "").strip()
                identity = model or component.get("name") or roles[0]
                if manufacturer and manufacturer.lower() not in identity.lower():
                    identity = manufacturer + " " + identity
                answer = (
                    f"PKBの記録では、{parent['name']}の{roles[0]}は {identity} です。"
                    if model
                    else (
                        f"PKBには{parent['name']}の{roles[0]} Entity "
                        f"{component.get('name')}がありますが、モデル属性は確認できませんでした。"
                    )
                )
                if driver:
                    answer += f" 現在ドライバーは {driver} です。"
                result = {
                    "status": "ok",
                    "result_kind": "entity_detail",
                    "total": len(current),
                    "entity": detail.get("entity"),
                    "parent": {
                        "id": parent.get("id"),
                        "name": parent.get("name"),
                    },
                    "component_role": component.get("relation_role"),
                    "current": current,
                    "relations": (detail.get("relations") or [])[:12],
                    "events": (detail.get("events") or [])[:12],
                }
                return {
                    "capability": "pkb_search",
                    "result": result,
                    "answer": answer,
                    "total": len(current),
                    "tool": "pkb",
                    "operation": "entity_detail",
                    "source_slug": "pkb-entity-detail",
                    "citation": "RITSUKO bounded PKB Entity detail",
                    "verified_by": "deterministic_pkb_query",
                    "source_metadata": {
                        "parent_entity_id": str(parent.get("id") or ""),
                        "parent_entity_name": parent.get("name"),
                        "component_entity_id": component.get("id"),
                        "component_entity_name": component.get("name"),
                        "relation_role": component.get("relation_role"),
                    },
                }

    return _execute_core_read("pkb_search", requested or user_raw)


def _create_magi_core_task_record(
    task_id: UUID,
    request: str,
    member_specs: list[dict],
) -> None:
    with connection() as db:
        create_magi_core_task(
            db,
            task_id=task_id,
            request=request,
            member_specs=member_specs,
        )


def _claim_magi_user_resume_record(
    task_id: UUID,
    reply_length: int,
    reply_fingerprint: str,
) -> tuple[dict, str | None]:
    with connection() as db:
        return claim_magi_user_resume(
            db,
            task_id=task_id,
            reply_length=reply_length,
            reply_fingerprint=reply_fingerprint,
        )


def _abort_magi_user_resume_record(
    task_id: UUID,
    error_type: str,
) -> None:
    with connection() as db:
        abort_magi_user_resume(
            db,
            task_id=task_id,
            error=error_type,
        )


def _claim_magi_proposal_review_record(
    task_id: UUID,
    decision: str,
    memory_result: dict | None,
) -> tuple[dict, str | None, dict, dict]:
    with connection() as db:
        return claim_magi_proposal_review(
            db,
            task_id=task_id,
            decision=decision,
            memory_result=memory_result,
        )


def _finalize_magi_proposal_review_record(
    task_id: UUID,
    session: dict,
    selected_capability: str | None,
) -> dict:
    with connection() as db:
        return finalize_magi_proposal_review(
            db,
            task_id=task_id,
            session=session,
            selected_capability=selected_capability,
        )


def _abort_magi_proposal_review_record(
    task_id: UUID,
    error_type: str,
) -> None:
    with connection() as db:
        abort_magi_proposal_review(
            db,
            task_id=task_id,
            error=error_type,
        )


def _prepare_magi_memory_intake_record(task_id: UUID) -> MemoryIntake:
    with connection() as db:
        return prepare_magi_memory_intake(db, task_id=task_id)


def _persist_magi_core_session_record(
    task_id: UUID,
    session: dict,
    selected_capability: str | None = None,
) -> dict:
    with connection() as db:
        return persist_magi_core_session(
            db,
            task_id=task_id,
            session=session,
            selected_capability=selected_capability,
        )


def _record_magi_pkb_read_record(
    task_id: UUID,
    execution: dict,
    pending_request: dict,
) -> tuple[str, str]:
    with connection() as db:
        return record_magi_pkb_read(
            db,
            task_id=task_id,
            execution=execution,
            pending_request=pending_request,
        )


def _fail_magi_core_task_record(task_id: UUID, error_type: str) -> None:
    with connection() as db:
        fail_magi_core_task(db, task_id=task_id, error=error_type)


def _execute_core_read(capability: str, text: str) -> dict:
    """Execute one bounded read-only capability and return normalized evidence metadata."""
    if capability == "pkb_search":
        result = search_text(text)
        return {
            "capability": capability,
            "result": result,
            "answer": core_answer(result),
            "total": int(result.get("total") or 0),
            "tool": "pkb",
            "operation": "search",
            "source_slug": "pkb-search",
            "citation": "Secretary Core read-only PKB search result",
            "verified_by": "deterministic_pkb_query",
        }
    if capability == "finance_read":
        result = finance_text(text)
        return {
            "capability": capability,
            "result": result,
            "answer": finance_core_answer(result),
            "total": int(result.get("total") or 0),
            "tool": "finance",
            "operation": "summary",
            "source_slug": "finance-read",
            "citation": "Secretary Core read-only finance summary",
            "verified_by": "deterministic_finance_query",
        }
    if capability == "web_research":
        result = web_text(text)
        return {
            "capability": capability,
            "result": result,
            "answer": web_core_answer(result),
            "total": int(result.get("total") or 0),
            "tool": "web",
            "operation": "research",
            "source_slug": "web-research",
            "citation": "Secretary Core bounded read-only web research result",
            "verified_by": "bounded_web_retrieval",
            "source_metadata": {
                "provider": result.get("provider"),
                "region": result.get("region"),
                "web_sources": [
                    {
                        "rank": hit.get("rank"),
                        "title": hit.get("title"),
                        "url": hit.get("url"),
                        "snippet": hit.get("snippet"),
                        "fetch_status": hit.get("fetch_status"),
                        "evidence_rank": hit.get("evidence_rank"),
                        "quality_score": hit.get("quality_score"),
                        "authority_hint": hit.get("authority_hint"),
                        "authority_level": hit.get("authority_level"),
                        "version_candidates": hit.get("version_candidates"),
                        "version_facts": hit.get("version_facts"),
                        "date_hints": hit.get("date_hints"),
                    }
                    for hit in (result.get("hits") or [])
                ],
            },
        }
    raise ValueError("Unsupported Core read capability")


def scope_core_request(
    text: str,
    entities: dict[str, dict],
    observation_pack: dict | None = None,
) -> dict:
    """Fail closed unless this first Core slice can safely scope a PKB read."""
    q = text.strip()
    if not q:
        return {
            "status": "question",
            "question": "何を確認したいか入力してください。",
            "reason": "empty_request",
        }

    wants_web = any(
        word in q
        for word in ("Web", "WEB", "web", "ウェブ", "ネット", "インターネット", "公式サイト")
    )
    wants_compare = any(word in q for word in ("比較", "最新か", "新しいか", "最新版か"))
    wants_current_driver = "ドライバ" in q and any(
        word in q for word in ("現在", "今の", "現行")
    )
    if wants_web and wants_compare and wants_current_driver:
        return {
            "status": "ready",
            "capability": "pkb_web_compare",
            "domain": "pc",
        }

    if wants_web:
        return {
            "status": "ready",
            "capability": "web_research",
            "domain": "research",
        }

    if any(word in q for word in ("家計", "支出", "収入", "収支", "出費")):
        return {
            "status": "ready",
            "capability": "finance_read",
            "domain": "finance",
        }

    component_state = COMPONENT_STATE_QUERY_PATTERN.search(q)
    if component_state and any(word in q for word in ("現在", "今の", "現行")):
        return {"status": "ready", "capability": "pkb_search", "domain": "pc"}

    if observation_pack is not None:
        mentioned = list(observation_pack.get("matched_entities") or [])
    else:
        mentioned = [
            row for name, row in sorted(
                entities.items(), key=lambda item: len(item[0]), reverse=True
            )
            if name in q
        ]
    if mentioned and any(word in q for word in ("構成", "ドライバ", "サーボ", "履歴")):
        return {
            "status": "ready",
            "capability": "pkb_search",
            "domain": mentioned[0]["domain"],
        }

    return {
        "status": "question",
        "question": (
            "この最小Coreでは、まだ対象と確認項目を安全に特定できません。"
            " 例: 「メインPCのGPUの現在のドライバーを調べて」のように"
            "対象と確認したい内容を指定してください。"
        ),
        "reason": "request_not_safely_scoped",
    }


def core_answer(search_result: dict) -> str:
    rows = search_result.get("items") or []
    if not rows:
        return "PKBに該当する記録が見つかりませんでした。"

    if search_result.get("result_kind") == "components":
        parts = []
        for row in rows[:8]:
            label = row.get("component_name") or "構成要素"
            role = row.get("relation_role")
            driver = row.get("current_driver")
            detail = label
            if role:
                detail += f"（{role}）"
            if driver:
                detail += f": 現在ドライバー {driver}"
            parts.append(detail)
        return "PKBの構成記録では、" + " / ".join(parts) + "。"

    parts = []
    for row in rows[:8]:
        entity = row.get("entity_name") or "対象"
        predicate = row.get("predicate") or "項目"
        value = row.get("value")
        parts.append(f"{entity}: {predicate}={value}")
    return "PKBの記録では、" + " / ".join(parts) + "。"


def load_core_task_window(loader, visible_count: int) -> tuple[list[dict], bool]:
    """Read a user-expanded window in bounded SQL pages plus one lookahead row."""
    if type(visible_count) is not int or visible_count < 1:
        raise ValueError("visible_count must be positive")
    rows = []
    while len(rows) < visible_count + 1:
        size = min(50, visible_count + 1 - len(rows))
        page = loader(limit=size, offset=len(rows))
        rows.extend(page)
        if len(page) < size:
            break
    return rows[:visible_count], len(rows) > visible_count


def core_magi_presentation(result: dict, advisor: dict, trace: dict) -> dict:
    """Present saved proposals and decisions; never promote a synthesis to final."""
    from copy import deepcopy

    task = {**result, **(trace.get("task") or {})}
    baseline = task.get("magi_baseline")
    pack = task.get("observation_pack") or advisor.get("observation_pack_after_action") or {}
    execution = advisor.get("cooperative_execution") or {}
    cycles = deepcopy(advisor.get("cycles") or [])
    if not cycles and advisor.get("synthesis"):
        cycles = [{"cycle": advisor.get("cycle") or 1,
                   "casper": {key: advisor.get(key) for key in
                              ("status", "next_step", "proposed_action", "reason")},
                   "synthesis": deepcopy(advisor["synthesis"])}]
    for cycle in cycles:
        if "melchior" not in cycle and baseline:
            cycle["melchior"] = deepcopy(baseline)
            cycle["melchior_reused"] = cycle.get("cycle", 1) > 1
        # Existing v0 records put the Action/Result references outside cycles.
        if cycle.get("cycle") == 1 and execution.get("action_id"):
            cycle.setdefault("action", {"action_id": execution["action_id"],
                                        "capability": execution.get("capability")})
            observation = next((o for o in pack.get("task_observations", [])
                                if o.get("cycle") == 1), {})
            cycle.setdefault("result", {"result_id": execution.get("result_id"),
                                        "result_count": execution.get("result_count"),
                                        **deepcopy(observation)})
    final = deepcopy(task.get("final_core_decision") or advisor.get("final_core_decision"))
    if final is None and execution.get("final_next_step"):
        final = {"next_step": execution["final_next_step"],
                 "reason": execution.get("final_reason"),
                 "task_status": task.get("status"),
                 "source": "saved_cooperative_execution"}
    return {"cycles": cycles, "final_core_decision": final}


def _memory_intake_log_export(envelope, result: dict | None) -> dict:
    """Build a copy-friendly Memory Intake trace without model reasoning."""
    response = result or {}
    candidates = []
    for item in response.get("candidates") or []:
        audit = item.get("audit") or {}
        candidates.append({
            "candidate_id": item.get("candidate_id"),
            "decision": item.get("decision"),
            "status": item.get("status"),
            "reason": item.get("reason"),
            "claim_id": item.get("claim_id"),
            "derived_claim_ids": list(item.get("derived_claim_ids") or []),
            "pending_id": item.get("pending_id"),
            "draft": audit.get("draft"),
            "grounding": audit.get("grounding"),
        })
    return {
        "input": asdict(envelope) if envelope is not None else None,
        "write_result": {
            "status": response.get("status"),
            "source_id": response.get("source_id"),
            "message": response.get("message"),
        },
        "candidates": candidates,
    }


def _advisor_log_export(result: dict, advisor: dict, trace: dict) -> dict:
    """Build one copy-friendly Advisor log payload from the current UI state."""
    trace_task = trace.get("task") or {}
    observation_pack = (
        trace_task.get("observation_pack")
        or result.get("observation_pack")
    )
    presentation = core_magi_presentation(result, advisor, trace)
    current = {**result, **trace_task}
    return {
        "task_id": result.get("task_id") or trace_task.get("id"),
        "request": result.get("request") or trace_task.get("request"),
        "core": {
            "status": current.get("status"),
            "phase": current.get("phase"),
            "question": current.get("question"),
            "selected_capability": current.get("selected_capability"),
        },
        "magi": {
            "melchior_scope_status": (
                (trace_task.get("magi_baseline") or {}).get("status")
            ),
            "melchior_baseline": (
                (trace_task.get("magi_baseline") or {}).get("selected_capability")
            ),
            "casper_next_step": advisor.get("next_step"),
            "casper_proposal": advisor.get("proposed_action"),
            "synthesis": advisor.get("synthesis"),
            "comparison": advisor.get("comparison"),
            "cycles": presentation["cycles"],
        },
        "advisor": {
            "model": advisor.get("model"),
            "timeout_seconds": advisor.get("timeout_seconds"),
            "job_status": advisor.get("job_status"),
            "status": advisor.get("status"),
            "elapsed_seconds": advisor.get("elapsed_seconds"),
            "situation": advisor.get("situation"),
            "reason": advisor.get("reason"),
            "missing_information": advisor.get("missing_information") or [],
            "next_step": advisor.get("next_step"),
            "expected_result": advisor.get("expected_result"),
            "error": advisor.get("error"),
        },
        "final_core_decision": presentation["final_core_decision"],
        "state_transitions": list(trace.get("advisor_events") or []),
        "observation_pack": observation_pack,
        "request_context": advisor.get("request_context"),
        "response_diagnostic": advisor.get("response_diagnostic"),
    }


def _build_core_observation_pack(request: str, db, entities: dict[str, dict]) -> dict:
    """Build the same bounded observation snapshot for MELCHIOR and CASPER."""
    return build_observation_pack(
        request,
        entities,
        detail_lookup=lambda entity_id: load_entity_detail(db, entity_id),
        components_lookup=lambda entity_id: list_components(db, UUID(entity_id)),
    )


_CORE_ADVISOR_STOP_EVENT = Event()
_CORE_ADVISOR_EXECUTOR = DaemonSerialBackgroundExecutor("core-advisor-shadow")


class _CoreAdvisorShutdown(RuntimeError):
    pass


def _raise_if_core_advisor_stopping() -> None:
    if _CORE_ADVISOR_STOP_EVENT.is_set():
        raise _CoreAdvisorShutdown("server_shutdown")


def _advisor_shadow_initial(
    model: str | None,
    timeout_seconds: float,
) -> dict:
    return {
        "magi_member": "CASPER",
        "job_status": "queued",
        "status": "queued",
        "comparison": "pending",
        "synthesis": None,
        "model": model,
        "timeout_seconds": timeout_seconds,
        "queued_at": datetime.now(timezone.utc).isoformat(),
        "started_at": None,
        "finished_at": None,
        "elapsed_seconds": 0.0,
        "situation": None,
        "missing_information": [],
        "next_step": None,
        "proposed_action": None,
        "reason": None,
        "expected_result": None,
        "error": None,
    }


def _write_core_advisor_shadow(task_id: UUID, shadow: dict, event_type: str) -> bool:
    """Atomically replace only checkpoint.advisor_shadow and append audit."""
    with connection() as db:
        with db.transaction():
            with db.cursor() as cur:
                cur.execute(
                    """UPDATE secretary.tasks
                       SET checkpoint=jsonb_set(
                           COALESCE(checkpoint, '{}'::jsonb),
                           '{advisor_shadow}',
                           %s,
                           true
                       )
                       WHERE id=%s""",
                    (Jsonb(shadow), task_id),
                )
                if cur.rowcount != 1:
                    return False
                cur.execute(
                    """INSERT INTO secretary.audit_events
                       (actor, event_type, task_id, object_type, object_id, details)
                       VALUES ('daily_core_advisor', %s, %s, 'task', %s, %s)""",
                    (event_type, task_id, task_id, Jsonb({"advisor_shadow": shadow})),
                )
    return True


def _claim_cooperative_probe(task_id: UUID, capability: str) -> bool:
    """Atomically claim an untouched clarification Task for one bounded local probe."""
    with connection() as db:
        with db.transaction():
            with db.cursor() as cur:
                cur.execute(
                    """SELECT status, checkpoint
                       FROM secretary.tasks
                       WHERE id=%s
                       FOR UPDATE""",
                    (task_id,),
                )
                row = cur.fetchone()
                if row is None:
                    return False
                status, checkpoint = row[0], row[1] or {}
                if status != "waiting_external":
                    return False
                if list(checkpoint.get("user_replies") or []):
                    return False

                next_checkpoint = {
                    **checkpoint,
                    "phase": "act",
                    "selected_capability": capability,
                    "question": None,
                    "reason": "magi_cooperative_probe",
                    "cooperative_cycle": 1,
                    "cooperative_resume": {
                        "phase": checkpoint.get("phase"),
                        "selected_capability": checkpoint.get("selected_capability"),
                        "question": checkpoint.get("question"),
                        "reason": checkpoint.get("reason"),
                    },
                }
                cur.execute(
                    """UPDATE secretary.tasks
                       SET status='running', checkpoint=%s
                       WHERE id=%s""",
                    (Jsonb(next_checkpoint), task_id),
                )
                cur.execute(
                    """INSERT INTO secretary.audit_events
                       (actor, event_type, task_id, object_type, object_id, details)
                       VALUES ('daily_core', 'core.magi.probe_claimed',
                               %s, 'task', %s, %s)""",
                    (
                        task_id,
                        task_id,
                        Jsonb({"capability": capability, "cycle": 1}),
                    ),
                )
    return True


def _record_cooperative_probe(
    task_id: UUID,
    request: str,
    execution: dict,
    observation_pack: dict,
) -> tuple[UUID, UUID]:
    """Persist one cooperative read-only Action/Result and move Task back to Orient."""
    execution_result = execution["result"]
    execution_total = int(execution.get("total") or 0)
    with connection() as db:
        with db.transaction():
            with db.cursor() as cur:
                cur.execute(
                    """INSERT INTO secretary.sources
                       (source_type, uri, citation, retrieved_at,
                        confidentiality, metadata)
                       VALUES ('tool', %s, %s, now(), 'private', %s)
                       RETURNING id""",
                    (
                        f"tool://daily-core/{execution['source_slug']}/{task_id}/magi-1",
                        execution["citation"],
                        Jsonb({
                            "task_id": str(task_id),
                            "capability": execution["capability"],
                            "query": request,
                            "result_count": execution_total,
                            "magi_cooperative": True,
                            "cycle": 1,
                            **(execution.get("source_metadata") or {}),
                        }),
                    ),
                )
                source_id = cur.fetchone()[0]

                cur.execute(
                    """INSERT INTO secretary.actions
                       (task_id, actor, tool, operation, parameters, risk,
                        authorization_basis, status, idempotency_key,
                        reversible, started_at, finished_at)
                       VALUES (%s, 'daily_core', %s, %s, %s,
                               'read_only', 'magi_local_pkb_read',
                               'succeeded', %s, true, now(), now())
                       RETURNING id""",
                    (
                        task_id,
                        execution["tool"],
                        execution["operation"],
                        Jsonb({
                            "query": request,
                            "bounded": True,
                            "magi_cooperative": True,
                            "cycle": 1,
                        }),
                        f"daily-core:{task_id}:magi:{execution['source_slug']}:1",
                    ),
                )
                action_id = cur.fetchone()[0]

                cur.execute(
                    """INSERT INTO secretary.results
                       (action_id, source_id, outcome, summary, evidence,
                        verified_by, verified_at)
                       VALUES (%s, %s, %s, %s, %s, %s, now())
                       RETURNING id""",
                    (
                        action_id,
                        source_id,
                        "success" if execution_total > 0 else "inconclusive",
                        execution["answer"],
                        Jsonb({
                            "result_kind": execution_result.get("result_kind"),
                            "total": execution_total,
                            "data": execution_result,
                            "magi_cooperative": True,
                            "cycle": 1,
                        }),
                        execution["verified_by"],
                    ),
                )
                result_id = cur.fetchone()[0]

                cur.execute(
                    """SELECT checkpoint
                       FROM secretary.tasks
                       WHERE id=%s
                       FOR UPDATE""",
                    (task_id,),
                )
                checkpoint = (cur.fetchone() or [{}])[0] or {}
                action_ids = list(checkpoint.get("action_ids") or [])
                result_ids = list(checkpoint.get("result_ids") or [])
                action_ids.append(str(action_id))
                result_ids.append(str(result_id))
                next_checkpoint = {
                    **checkpoint,
                    "phase": "orient",
                    "selected_capability": execution["capability"],
                    "action_id": str(action_id),
                    "result_id": str(result_id),
                    "action_ids": action_ids,
                    "result_ids": result_ids,
                    "result_count": execution_total,
                    "observation_pack": observation_pack,
                    "cooperative_result": execution_result,
                    "cooperative_cycle": 2,
                }
                cur.execute(
                    """UPDATE secretary.tasks
                       SET checkpoint=%s
                       WHERE id=%s""",
                    (Jsonb(next_checkpoint), task_id),
                )
                cur.execute(
                    """INSERT INTO secretary.audit_events
                       (actor, event_type, task_id, action_id,
                        object_type, object_id, details)
                       VALUES ('daily_core', 'core.magi.probe_observed',
                               %s, %s, 'task', %s, %s)""",
                    (
                        task_id,
                        action_id,
                        task_id,
                        Jsonb({
                            "capability": execution["capability"],
                            "result_count": execution_total,
                            "cycle": 1,
                        }),
                    ),
                )
    return action_id, result_id


def _finalize_cooperative_probe(
    task_id: UUID,
    final_decision: dict,
    final_observation_pack: dict,
    execution: dict,
) -> None:
    next_step = final_decision["next_step"]
    task_status = "completed" if next_step == "respond" else "waiting_external"
    phase = "completed" if next_step == "respond" else "awaiting_clarification"
    with connection() as db:
        with db.transaction():
            with db.cursor() as cur:
                cur.execute(
                    """SELECT checkpoint
                       FROM secretary.tasks
                       WHERE id=%s
                       FOR UPDATE""",
                    (task_id,),
                )
                row = cur.fetchone()
                if row is None:
                    return
                checkpoint = row[0] or {}
                next_checkpoint = {
                    **checkpoint,
                    "phase": phase,
                    "selected_capability": execution["capability"],
                    "question": final_decision.get("question"),
                    "message": final_decision.get("message"),
                    "reason": final_decision.get("reason"),
                    "observation_pack": final_observation_pack,
                    "cooperative_result": execution.get("result"),
                    "cooperative_cycle": 2,
                    "final_core_decision": {**final_decision, "task_status": task_status},
                }
                cur.execute(
                    """UPDATE secretary.tasks
                       SET status=%s,
                           checkpoint=%s,
                           completed_at=CASE WHEN %s='completed' THEN now() ELSE NULL END
                       WHERE id=%s""",
                    (task_status, Jsonb(next_checkpoint), task_status, task_id),
                )
                cur.execute(
                    """INSERT INTO secretary.audit_events
                       (actor, event_type, task_id, object_type, object_id, details)
                       VALUES ('daily_core', %s, %s, 'task', %s, %s)""",
                    (
                        "core.magi.responded"
                        if next_step == "respond"
                        else "core.magi.clarify_after_probe",
                        task_id,
                        task_id,
                        Jsonb({
                            "next_step": next_step,
                            "capability": execution["capability"],
                            "reason": final_decision.get("reason"),
                            "cycle": 2,
                        }),
                    ),
                )


def _fail_cooperative_probe(task_id: UUID, error: str) -> None:
    with connection() as db:
        with db.transaction():
            with db.cursor() as cur:
                cur.execute(
                    """SELECT checkpoint
                       FROM secretary.tasks
                       WHERE id=%s
                       FOR UPDATE""",
                    (task_id,),
                )
                row = cur.fetchone()
                if row is None:
                    return
                checkpoint = row[0] or {}
                next_checkpoint = {
                    **checkpoint,
                    "phase": "failed",
                    "question": None,
                    "reason": "magi_cooperative_probe_failed",
                    "cooperative_error": error,
                }
                cur.execute(
                    """UPDATE secretary.tasks
                       SET status='failed', checkpoint=%s
                       WHERE id=%s""",
                    (Jsonb(next_checkpoint), task_id),
                )


def _restore_interrupted_cooperative_probe(
    task_id: UUID,
    error: str,
) -> bool:
    """Return an interrupted legacy cooperative probe to a resumable state."""
    with connection() as db:
        with db.transaction():
            with db.cursor() as cur:
                cur.execute(
                    """SELECT status, checkpoint
                       FROM secretary.tasks
                       WHERE id=%s
                       FOR UPDATE""",
                    (task_id,),
                )
                row = cur.fetchone()
                if row is None:
                    return False
                status, checkpoint = row[0], row[1] or {}
                if status != "running":
                    return False
                if checkpoint.get("core_slice") != "daily_read_only_v1":
                    return False
                if (
                    checkpoint.get("reason") != "magi_cooperative_probe"
                    and checkpoint.get("cooperative_cycle") not in {1, 2}
                ):
                    return False

                resume = checkpoint.get("cooperative_resume")
                if not isinstance(resume, dict):
                    resume = {}
                next_checkpoint = {
                    **checkpoint,
                    "phase": resume.get("phase") or "awaiting_clarification",
                    "selected_capability": resume.get("selected_capability"),
                    "question": (
                        resume.get("question")
                        or "バックグラウンドAdvisorが中断されました。同じTaskを再開してください。"
                    ),
                    "reason": resume.get("reason") or "advisor_interrupted",
                    "cooperative_error": error,
                    "cooperative_interrupted": True,
                }
                cur.execute(
                    """UPDATE secretary.tasks
                       SET status='waiting_external',
                           checkpoint=%s,
                           completed_at=NULL
                       WHERE id=%s""",
                    (Jsonb(next_checkpoint), task_id),
                )
                cur.execute(
                    """INSERT INTO secretary.audit_events
                       (actor, event_type, task_id, object_type, object_id, details)
                       VALUES ('daily_core', 'core.advisor.task_restored',
                               %s, 'task', %s, %s)""",
                    (task_id, task_id, Jsonb({"error": error})),
                )
    return True


def _run_core_advisor_shadow(
    task_id: UUID,
    request: str,
    melchior: dict,
    observation_pack: dict,
    model: str | None,
    timeout_seconds: float,
) -> None:
    """Run cooperative MAGI in background; only bounded ambiguous PKB probes may execute."""
    started_at = datetime.now(timezone.utc)
    started_perf = time.perf_counter()
    attempted_model = model
    claimed_probe = False
    try:
        _raise_if_core_advisor_stopping()
        try:
            attempted_model = choose_advisor_model(list_advisor_models(), model)
        except Exception:
            attempted_model = model

        running = _advisor_shadow_initial(attempted_model, timeout_seconds)
        running.update({
            "job_status": "running",
            "status": "running",
            "started_at": started_at.isoformat(),
            "cooperative_mode": "bounded_execution_v0",
            "cycle": 1,
            "cycles": [],
        })
        if not _write_core_advisor_shadow(
            task_id, running, "core.advisor.running"
        ):
            return
        _raise_if_core_advisor_stopping()

        current_selection = melchior.get("selected_capability")
        melchior_next_step = (
            "observe"
            if melchior.get("status") == "ready" and current_selection is not None
            else "clarify"
        )
        first_result = advise_core(
            request,
            current_selection=current_selection,
            melchior_next_step=melchior_next_step,
            task_state="received",
            observations=observation_pack,
            model=attempted_model,
            timeout=timeout_seconds,
        ).as_dict()
        _raise_if_core_advisor_stopping()
        first_synthesis = synthesize_magi(
            melchior,
            first_result,
            permissions={
                "pkb_read": True,
                "finance_read": True,
                "web_research": True,
                "external_actions": False,
            },
        ).as_dict()
        first_cycle = {
            "cycle": 1,
            "melchior": dict(melchior),
            "observation_version": observation_pack.get("version"),
            "casper": {
                "status": first_result.get("status"),
                "next_step": first_result.get("next_step"),
                "proposed_action": first_result.get("proposed_action"),
                "reason": first_result.get("reason"),
                "missing_information": first_result.get("missing_information") or [],
            },
            "synthesis": first_synthesis,
        }

        if can_auto_execute_ambiguous_probe(melchior, first_synthesis):
            intermediate = {
                **first_result,
                "job_status": "running",
                "started_at": started_at.isoformat(),
                "finished_at": None,
                "elapsed_seconds": round(time.perf_counter() - started_perf, 3),
                "synthesis": first_synthesis,
                "cooperative_mode": "bounded_execution_v0",
                "cycle": 1,
                "cycles": [first_cycle],
                "cooperative_execution": {
                    "status": "claiming",
                    "capability": first_synthesis.get("selected_capability"),
                },
            }
            _write_core_advisor_shadow(
                task_id, intermediate, "core.magi.synthesis_ready"
            )

            capability = str(first_synthesis["selected_capability"])
            _raise_if_core_advisor_stopping()
            claimed_probe = _claim_cooperative_probe(task_id, capability)
            _raise_if_core_advisor_stopping()
            if not claimed_probe:
                final = {
                    **first_result,
                    "job_status": "completed",
                    "started_at": started_at.isoformat(),
                    "finished_at": datetime.now(timezone.utc).isoformat(),
                    "elapsed_seconds": round(time.perf_counter() - started_perf, 3),
                    "synthesis": first_synthesis,
                    "cooperative_mode": "bounded_execution_v0",
                    "cycle": 1,
                    "cycles": [first_cycle],
                    "cooperative_execution": {
                        "status": "skipped_task_changed",
                        "capability": capability,
                    },
                }
                _write_core_advisor_shadow(
                    task_id, final, "core.advisor.completed"
                )
                return

            execution = _execute_cooperative_local_probe(
                capability,
                request,
                observation_pack,
            )
            _raise_if_core_advisor_stopping()
            second_observation = extend_observation_pack(
                observation_pack,
                execution,
                cycle=1,
            )
            action_id, result_id = _record_cooperative_probe(
                task_id,
                request,
                execution,
                second_observation,
            )
            _raise_if_core_advisor_stopping()

            first_cycle["action"] = {
                "action_id": str(action_id), "capability": capability,
            }
            first_cycle["result"] = {
                "result_id": str(result_id),
                **second_observation["task_observations"][-1],
            }
            observed = {
                **intermediate,
                "cycle": 2,
                "cooperative_execution": {
                    "status": "observed",
                    "capability": capability,
                    "result_count": int(execution.get("total") or 0),
                    "action_id": str(action_id),
                    "result_id": str(result_id),
                },
            }
            _write_core_advisor_shadow(
                task_id, observed, "core.magi.probe_observed"
            )

            second_result = advise_core(
                request,
                current_selection=current_selection,
                melchior_next_step=melchior_next_step,
                task_state="observed",
                observations=second_observation,
                model=attempted_model,
                timeout=timeout_seconds,
            ).as_dict()
            _raise_if_core_advisor_stopping()
            second_synthesis = synthesize_magi(
                melchior,
                second_result,
                permissions={
                    "pkb_read": True,
                    "finance_read": True,
                    "web_research": True,
                    "external_actions": False,
                },
            ).as_dict()
            second_cycle = {
                "cycle": 2,
                "melchior": dict(melchior),
                "melchior_reused": True,
                "observation_version": second_observation.get("version"),
                "casper": {
                    "status": second_result.get("status"),
                    "next_step": second_result.get("next_step"),
                    "proposed_action": second_result.get("proposed_action"),
                    "reason": second_result.get("reason"),
                    "missing_information": second_result.get("missing_information") or [],
                },
                "synthesis": second_synthesis,
            }
            final_decision = decide_after_observation(
                second_synthesis,
                execution=execution,
                executed_capabilities=(capability,),
            )
            _raise_if_core_advisor_stopping()
            _finalize_cooperative_probe(
                task_id,
                final_decision,
                second_observation,
                execution,
            )
            _raise_if_core_advisor_stopping()
            final = {
                **second_result,
                "job_status": "completed",
                "started_at": started_at.isoformat(),
                "finished_at": datetime.now(timezone.utc).isoformat(),
                "elapsed_seconds": round(time.perf_counter() - started_perf, 3),
                "synthesis": second_synthesis,
                "cooperative_mode": "bounded_execution_v0",
                "cycle": 2,
                "cycles": [first_cycle, second_cycle],
                "cooperative_execution": {
                    "status": "completed",
                    "capability": capability,
                    "result_count": int(execution.get("total") or 0),
                    "action_id": str(action_id),
                    "result_id": str(result_id),
                    "final_next_step": final_decision.get("next_step"),
                    "final_reason": final_decision.get("reason"),
                },
                "final_core_decision": {
                    **final_decision,
                    "task_status": "completed" if final_decision["next_step"] == "respond" else "waiting_external",
                },
                "observation_pack_after_action": second_observation,
            }
            _write_core_advisor_shadow(
                task_id, final, "core.advisor.completed"
            )
            return

        elapsed = round(time.perf_counter() - started_perf, 3)
        error = first_result.get("error")
        if error == "TimeoutError":
            job_status = "timeout"
        elif first_result.get("status") == "unavailable":
            job_status = "error"
        else:
            job_status = "completed"
        final = {
            **first_result,
            "job_status": job_status,
            "started_at": started_at.isoformat(),
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "elapsed_seconds": elapsed,
            "synthesis": first_synthesis,
            "cooperative_mode": "bounded_execution_v0",
            "cycle": 1,
            "cycles": [first_cycle],
            "cooperative_execution": {"status": "not_executed"},
        }
        _raise_if_core_advisor_stopping()
        _write_core_advisor_shadow(
            task_id, final, f"core.advisor.{job_status}"
        )
    except _CoreAdvisorShutdown:
        return
    except Exception as exc:
        elapsed = round(time.perf_counter() - started_perf, 3)
        if claimed_probe:
            try:
                _fail_cooperative_probe(task_id, type(exc).__name__)
            except Exception:
                pass
        failed = {
            **_advisor_shadow_initial(attempted_model, timeout_seconds),
            "job_status": "error",
            "status": "unavailable",
            "comparison": "unavailable",
            "model": attempted_model,
            "started_at": started_at.isoformat(),
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "elapsed_seconds": elapsed,
            "error": type(exc).__name__,
            "cooperative_mode": "bounded_execution_v0",
        }
        try:
            _write_core_advisor_shadow(task_id, failed, "core.advisor.error")
        except Exception:
            pass


def _queue_core_advisor_shadow(
    task_id: UUID,
    request: str,
    melchior: dict,
    observation_pack: dict,
    model: str | None,
    timeout_seconds: float,
) -> None:
    if _CORE_ADVISOR_STOP_EVENT.is_set():
        return
    _CORE_ADVISOR_EXECUTOR.submit(
        _run_core_advisor_shadow,
        task_id,
        request,
        melchior,
        observation_pack,
        model,
        timeout_seconds,
    )


def protocol_probe_export(result: dict) -> dict:
    """One copyable result for the isolated Cycle 1 probe (no Thinking text)."""
    return {
        "protocol_path": result.get("protocol_path"),
        "status": result.get("status"),
        "assignment": result.get("assignment"),
        "validation_errors": result.get("validation_errors") or [],
        "diagnostic": result.get("diagnostic") or {},
        "analysis_result": result.get("response"),
        "request_envelope": result.get("request_envelope"),
        "legacy_router_used": result.get("legacy_router_used"),
        "pkb_read_executed": result.get("pkb_read_executed"),
    }


def run_ritsuko_magi_cycle1_probe(
    text: str,
    *,
    model: str,
    timeout: float = 60.0,
) -> dict:
    """One isolated Protocol v1 cycle owned end-to-end by RITSUKO."""
    request=text.strip()
    if not request:
        return {"status":"rejected","validation_errors":["empty_user_input"],"response":None}
    envelope=build_request_envelope(
        task_id=str(uuid4()),
        cycle=1,
        member_name="MELCHIOR",
        user_raw=request,
        task_context={
            "status":"received","goal":None,"previous_user_messages":[],
            "previous_actions":[],"previous_results":[],
        },
        resource_catalog=default_resource_catalog(),
        observations=[],
    )
    result=call_magi_member(
        envelope,member_name="MELCHIOR",model=model,timeout=timeout,
    )
    return {
        **result,
        "protocol_path":"ritsuko_magi_v1_cycle1",
        "legacy_router_used":False,
        "pkb_read_executed":False,
    }


def run_core_request(
    text: str,
    advisor_model: str | None = None,
    advisor_timeout: float = 60.0,
) -> dict:
    """Daily Secretary Core slice with asynchronous Shadow Advisor."""
    request = text.strip()
    if not request:
        return {
            "status": "rejected",
            "phase": "input",
            "message": "依頼を入力してください。",
        }

    with connection() as read_db:
        entities = _entity_map(read_db)
        observation_pack = _build_core_observation_pack(request, read_db, entities)
    scoped = scope_core_request(request, entities, observation_pack)
    advisor_shadow = _advisor_shadow_initial(advisor_model, advisor_timeout)

    task_id = uuid4()
    with connection() as db:
        with db.transaction():
            domain = scoped.get("domain") or "general"
            initial_status = "running" if scoped["status"] == "ready" else "waiting_external"
            checkpoint = {
                "core_slice": "daily_read_only_v1",
                "phase": (
                    "decide"
                    if scoped["status"] == "ready"
                    else "awaiting_clarification"
                ),
                "selected_capability": scoped.get("capability"),
                "question": scoped.get("question"),
                "reason": scoped.get("reason"),
                "observation_pack": observation_pack,
                "magi_baseline": {
                    "member": "MELCHIOR",
                    "status": scoped["status"],
                    "selected_capability": scoped.get("capability"),
                },
                "advisor_shadow": advisor_shadow,
            }
            with db.cursor() as cur:
                cur.execute(
                    """INSERT INTO secretary.tasks
                       (id, request, requested_by, domain, completion_criteria,
                        permission_scope, status, checkpoint)
                       VALUES (%s, %s, 'local_user', %s, %s, %s, %s, %s)""",
                    (
                        task_id,
                        request,
                        domain,
                        "Return bounded local evidence with provenance or ask for clarification.",
                        Jsonb({
                            "pkb_read": True,
                            "finance_read": True,
                            "web_research": True,
                            "pkb_web_compare": True,
                            "external_actions": False,
                        }),
                        initial_status,
                        Jsonb(checkpoint),
                    ),
                )

                if scoped["status"] != "ready":
                    cur.execute(
                        """INSERT INTO secretary.audit_events
                           (actor, event_type, task_id, object_type, object_id, details)
                           VALUES ('daily_core', 'core.awaiting_clarification',
                                   %s, 'task', %s, %s)""",
                        (
                            task_id,
                            task_id,
                            Jsonb({
                                "reason": scoped["reason"],
                                "advisor_shadow": advisor_shadow,
                            }),
                        ),
                    )
                    response = {
                        "task_id": str(task_id),
                        "status": "waiting_external",
                        "phase": "awaiting_clarification",
                        "message": "追加情報が必要です。",
                        "question": scoped["question"],
                        "selected_capability": scoped.get("capability"),
                        "observation_pack": observation_pack,
                        "magi_baseline": {
                            "member": "MELCHIOR",
                            "status": scoped["status"],
                            "selected_capability": scoped.get("capability"),
                        },
                        "advisor_shadow": advisor_shadow,
                    }
                else:
                    # Read capabilities use their own bounded DB connections so the
                    # surrounding Task write transaction remains independent.
                    if scoped["capability"] == "pkb_web_compare":
                        plan_result = _execute_pkb_web_compare(request)
                        executions = plan_result["executions"]
                        answer = plan_result["answer"]
                        comparison = plan_result["comparison"]
                        result = {
                            "status": "ok",
                            "result_kind": "pkb_web_compare",
                            "comparison": comparison,
                        }
                        total = sum(int(item.get("total") or 0) for item in executions)
                    else:
                        execution = _execute_core_read(scoped["capability"], request)
                        executions = [execution]
                        answer = execution["answer"]
                        result = execution["result"]
                        comparison = None
                        total = execution["total"]

                    enough = (
                        all(int(item.get("total") or 0) > 0 for item in executions)
                        and not (
                            scoped["capability"] == "pkb_web_compare"
                            and plan_result.get("needs_clarification")
                        )
                    )
                    phase = "completed" if enough else "awaiting_clarification"
                    task_status = "completed" if enough else "waiting_external"
                    question = None if enough else (
                        "比較に必要な記録またはWeb検索対象が不足しています。"
                        "GPUのメーカー・モデルをPKBへ登録するか、依頼で明示してください。"
                    )

                    action_ids = []
                    result_ids = []
                    for attempt, execution in enumerate(executions, start=1):
                        execution_result = execution["result"]
                        execution_total = int(execution.get("total") or 0)
                        cur.execute(
                            """INSERT INTO secretary.sources
                               (source_type, uri, citation, retrieved_at,
                                confidentiality, metadata)
                               VALUES ('tool', %s, %s, now(), 'private', %s)
                               RETURNING id""",
                            (
                                f"tool://daily-core/{execution['source_slug']}/{task_id}/{attempt}",
                                execution["citation"],
                                Jsonb({
                                    "task_id": str(task_id),
                                    "capability": execution["capability"],
                                    "query": request,
                                    "result_count": execution_total,
                                    **(execution.get("source_metadata") or {}),
                                }),
                            ),
                        )
                        source_id = cur.fetchone()[0]

                        cur.execute(
                            """INSERT INTO secretary.actions
                               (task_id, actor, tool, operation, parameters, risk,
                                authorization_basis, status, idempotency_key,
                                reversible, started_at, finished_at)
                               VALUES (%s, 'daily_core', %s, %s, %s,
                                       'read_only', 'localhost_read_only',
                                       'succeeded', %s, true, now(), now())
                               RETURNING id""",
                            (
                                task_id,
                                execution["tool"],
                                execution["operation"],
                                Jsonb({"query": request, "bounded": True, "step": attempt}),
                                f"daily-core:{task_id}:{execution['source_slug']}:{attempt}",
                            ),
                        )
                        action_id = cur.fetchone()[0]
                        action_ids.append(action_id)

                        cur.execute(
                            """INSERT INTO secretary.results
                               (action_id, source_id, outcome, summary, evidence,
                                verified_by, verified_at)
                               VALUES (%s, %s, %s, %s, %s,
                                       %s, now())
                               RETURNING id""",
                            (
                                action_id,
                                source_id,
                                "success" if execution_total > 0 else "inconclusive",
                                execution["answer"],
                                Jsonb({
                                    "result_kind": execution_result.get("result_kind"),
                                    "total": execution_total,
                                    "data": execution_result,
                                }),
                                execution["verified_by"],
                            ),
                        )
                        result_ids.append(cur.fetchone()[0])

                    action_id = action_ids[-1]
                    result_id = result_ids[-1]

                    final_checkpoint = {
                        "core_slice": "daily_read_only_v1",
                        "phase": phase,
                        "selected_capability": scoped["capability"],
                        "action_id": str(action_id),
                        "result_id": str(result_id),
                        "action_ids": [str(value) for value in action_ids],
                        "result_ids": [str(value) for value in result_ids],
                        "result_count": total,
                        "comparison": comparison,
                        "question": question,
                        "observation_pack": observation_pack,
                        "magi_baseline": {
                            "member": "MELCHIOR",
                            "status": scoped["status"],
                            "selected_capability": scoped.get("capability"),
                        },
                        "advisor_shadow": advisor_shadow,
                    }
                    cur.execute(
                        """UPDATE secretary.tasks
                           SET status=%s,
                               checkpoint=%s,
                               completed_at=CASE WHEN %s='completed' THEN now() ELSE NULL END
                           WHERE id=%s""",
                        (task_status, Jsonb(final_checkpoint), task_status, task_id),
                    )
                    cur.execute(
                        """INSERT INTO secretary.audit_events
                           (actor, event_type, task_id, action_id,
                            object_type, object_id, details)
                           VALUES ('daily_core', %s, %s, %s, 'task', %s, %s)""",
                        (
                            "core.completed" if total > 0 else "core.needs_more_context",
                            task_id,
                            action_id,
                            task_id,
                            Jsonb({
                                "capability": scoped["capability"],
                                "result_count": total,
                                "action_count": len(action_ids),
                                "comparison": comparison,
                                "advisor_shadow": advisor_shadow,
                            }),
                        ),
                    )

                    response = {
                        "task_id": str(task_id),
                        "status": task_status,
                        "phase": phase,
                        "message": answer,
                        "question": question,
                        "selected_capability": scoped["capability"],
                        "observation_pack": observation_pack,
                        "magi_baseline": {
                            "member": "MELCHIOR",
                            "status": scoped["status"],
                            "selected_capability": scoped.get("capability"),
                        },
                        "capability_result": result,
                        "comparison": comparison,
                        "advisor_shadow": advisor_shadow,
                        "search": (
                            plan_result["pkb"]
                            if scoped["capability"] == "pkb_web_compare"
                            else result if scoped["capability"] == "pkb_search" else None
                        ),
                        "finance": result if scoped["capability"] == "finance_read" else None,
                        "web": (
                            plan_result["web"]
                            if scoped["capability"] == "pkb_web_compare"
                            else result if scoped["capability"] == "web_research" else None
                        ),
                    }

    # Queue only after the Task transaction commits. The Advisor can run for
    # minutes without delaying or changing the deterministic Task outcome.
    _queue_core_advisor_shadow(
        task_id,
        request,
        {
            "member": "MELCHIOR",
            "status": scoped["status"],
            "selected_capability": scoped.get("capability"),
        },
        observation_pack,
        advisor_model,
        advisor_timeout,
    )
    return response


def _contextualize_core_reply(
    original_request: str,
    reply: str,
    entities: dict[str, dict],
) -> str:
    """Carry a uniquely named Entity from the original request into a short reply."""
    reply = reply.strip()
    if not reply:
        return reply

    ordered = sorted(entities.items(), key=lambda item: len(item[0]), reverse=True)
    reply_mentions = [name for name, _ in ordered if name in reply]
    if reply_mentions:
        return reply

    original_mentions = [name for name, _ in ordered if name in original_request]
    if len(original_mentions) == 1:
        entity_name = original_mentions[0]
        if reply.startswith(("の", "について", "に関して")):
            return entity_name + reply
        return entity_name + "の" + reply
    return reply


def load_recent_core_tasks(limit: int = 10, offset: int = 0) -> list[dict]:
    """Load a compact screen-wide Core activity view for debugging."""
    if type(limit) is not int or not 1 <= limit <= 50:
        raise ValueError("limit must be 1..50")
    if type(offset) is not int or offset < 0:
        raise ValueError("offset must be a nonnegative integer")
    with connection() as db:
        with db.cursor() as cur:
            cur.execute(
                """SELECT t.id, t.request, t.status, t.revision,
                          t.created_at, t.updated_at, t.completed_at,
                          t.checkpoint,
                          count(DISTINCT a.id) AS action_count,
                          count(DISTINCT r.id) AS result_count
                   FROM secretary.tasks t
                   LEFT JOIN secretary.actions a ON a.task_id=t.id
                   LEFT JOIN secretary.results r ON r.action_id=a.id
                   WHERE t.requested_by='local_user'
                     AND COALESCE(t.checkpoint->>'core_slice', '') IN
                         ('daily_read_only_v1','ritsuko_magi_observation_v1')
                   GROUP BY t.id
                   ORDER BY t.updated_at DESC, t.id DESC
                   LIMIT %s OFFSET %s""",
                (limit, offset),
            )
            rows = cur.fetchall()
    result = []
    for row in rows:
        checkpoint = row[7] or {}
        result.append({
            "id": str(row[0]),
            "request": row[1],
            "status": row[2],
            "revision": row[3],
            "created_at": row[4],
            "updated_at": row[5],
            "completed_at": row[6],
            "core_slice": checkpoint.get("core_slice"),
            "phase": checkpoint.get("phase"),
            "selected_capability": checkpoint.get("selected_capability"),
            "action_count": row[8],
            "result_count": row[9],
        })
    return result


def load_open_core_tasks(limit: int = 20, offset: int = 0) -> list[dict]:
    """Load unfinished daily Core tasks so they can survive page/server restarts."""
    if type(limit) is not int or not 1 <= limit <= 50:
        raise ValueError("limit must be 1..50")
    if type(offset) is not int or offset < 0:
        raise ValueError("offset must be a nonnegative integer")
    with connection() as db:
        with db.cursor() as cur:
            cur.execute(
                """SELECT t.id, t.request, t.status, t.revision,
                          t.created_at, t.updated_at, t.checkpoint,
                          count(DISTINCT a.id) AS action_count,
                          count(DISTINCT r.id) AS result_count
                   FROM secretary.tasks t
                   LEFT JOIN secretary.actions a ON a.task_id=t.id
                   LEFT JOIN secretary.results r ON r.action_id=a.id
                   WHERE t.requested_by='local_user'
                     AND COALESCE(t.checkpoint->>'core_slice', '') IN
                         ('daily_read_only_v1','ritsuko_magi_observation_v1')
                     AND t.status IN ('waiting_external', 'running', 'paused')
                   GROUP BY t.id
                   ORDER BY t.updated_at DESC, t.id DESC
                   LIMIT %s OFFSET %s""",
                (limit, offset),
            )
            rows = cur.fetchall()
    result = []
    for row in rows:
        checkpoint = row[6] or {}
        result.append({
            "id": str(row[0]),
            "request": row[1],
            "status": row[2],
            "revision": row[3],
            "created_at": row[4],
            "updated_at": row[5],
            "core_slice": checkpoint.get("core_slice"),
            "phase": checkpoint.get("phase"),
            "selected_capability": checkpoint.get("selected_capability"),
            "question": checkpoint.get("question"),
            "message": checkpoint.get("message"),
            "effective_request": checkpoint.get("effective_request"),
            "user_replies": list(checkpoint.get("user_replies") or []),
            "advisor_shadow": checkpoint.get("advisor_shadow"),
            "action_count": row[7],
            "result_count": row[8],
        })
    return result


def load_completed_core_tasks(limit: int = 8, offset: int = 0) -> list[dict]:
    """Load recently completed daily Core tasks for read-only review."""
    if type(limit) is not int or not 1 <= limit <= 50:
        raise ValueError("limit must be 1..50")
    if type(offset) is not int or offset < 0:
        raise ValueError("offset must be a nonnegative integer")
    with connection() as db:
        with db.cursor() as cur:
            cur.execute(
                """SELECT t.id, t.request, t.status, t.revision,
                          t.created_at, t.updated_at, t.completed_at,
                          t.checkpoint,
                          count(DISTINCT a.id) AS action_count,
                          count(DISTINCT r.id) AS result_count
                   FROM secretary.tasks t
                   LEFT JOIN secretary.actions a ON a.task_id=t.id
                   LEFT JOIN secretary.results r ON r.action_id=a.id
                   WHERE t.requested_by='local_user'
                     AND COALESCE(t.checkpoint->>'core_slice', '') IN
                         ('daily_read_only_v1','ritsuko_magi_observation_v1')
                     AND t.status='completed'
                   GROUP BY t.id
                   ORDER BY COALESCE(t.completed_at, t.updated_at) DESC, t.id DESC
                   LIMIT %s OFFSET %s""",
                (limit, offset),
            )
            rows = cur.fetchall()
    result = []
    for row in rows:
        checkpoint = row[7] or {}
        result.append({
            "id": str(row[0]),
            "request": row[1],
            "status": row[2],
            "revision": row[3],
            "created_at": row[4],
            "updated_at": row[5],
            "completed_at": row[6],
            "core_slice": checkpoint.get("core_slice"),
            "phase": checkpoint.get("phase"),
            "selected_capability": checkpoint.get("selected_capability"),
            "question": checkpoint.get("question"),
            "message": checkpoint.get("message"),
            "effective_request": checkpoint.get("effective_request"),
            "user_replies": list(checkpoint.get("user_replies") or []),
            "comparison": checkpoint.get("comparison"),
            "advisor_shadow": checkpoint.get("advisor_shadow"),
            "action_count": row[8],
            "result_count": row[9],
        })
    return result


def core_task_selection_result(item: dict) -> dict:
    """Convert one persisted Task summary into the same UI state as a live request."""
    task_id = str(item.get("id") or "").strip()
    status = str(item.get("status") or "").strip()
    if not task_id:
        raise ValueError("Task ID is required")
    if status not in {"waiting_external", "running", "paused", "completed", "failed"}:
        raise ValueError("Unsupported Task status")
    return {
        "task_id": task_id,
        "status": status,
        "core_slice": item.get("core_slice"),
        "phase": item.get("phase"),
        "selected_capability": item.get("selected_capability"),
        "question": item.get("question"),
        "message": (
            item.get("message")
            or (
                "完了済みTaskを閲覧しています。"
                if status == "completed"
                else "保存済みTaskを選択しました。"
            )
        ),
        "effective_request": item.get("effective_request"),
        "comparison": item.get("comparison"),
        "advisor_shadow": item.get("advisor_shadow"),
        "read_only_history": status == "completed",
        "resumed_from_storage": True,
    }


def load_core_task_trace(task_id: UUID) -> dict:
    """Load a structured, user-visible execution trace for one Core Task."""
    with connection() as db:
        with db.cursor() as cur:
            cur.execute(
                """SELECT id, request, domain, status, revision,
                          created_at, updated_at, completed_at, checkpoint
                   FROM secretary.tasks
                   WHERE id=%s""",
                (task_id,),
            )
            task = cur.fetchone()
            if task is None:
                raise ValueError("Taskが見つかりません。")

            cur.execute(
                """SELECT a.id, a.tool, a.operation, a.risk, a.status,
                          a.recorded_at, a.started_at, a.finished_at,
                          r.id, r.outcome, r.summary, r.verified_by,
                          r.verified_at, s.uri
                   FROM secretary.actions a
                   LEFT JOIN secretary.results r ON r.action_id=a.id
                   LEFT JOIN secretary.sources s ON s.id=r.source_id
                   WHERE a.task_id=%s
                   ORDER BY a.recorded_at, a.id""",
                (task_id,),
            )
            action_rows = cur.fetchall()

            cur.execute(
                """SELECT event_type, occurred_at
                   FROM secretary.audit_events
                   WHERE task_id=%s
                     AND actor IN ('daily_core_advisor', 'ritsuko_core')
                   ORDER BY occurred_at, id""",
                (task_id,),
            )
            advisor_event_rows = cur.fetchall()

    checkpoint = task[8] or {}
    return {
        "task": {
            "id": str(task[0]),
            "request": task[1],
            "domain": task[2],
            "status": task[3],
            "revision": task[4],
            "created_at": task[5],
            "updated_at": task[6],
            "completed_at": task[7],
            "core_slice": checkpoint.get("core_slice"),
            "phase": checkpoint.get("phase"),
            "selected_capability": checkpoint.get("selected_capability"),
            "question": checkpoint.get("question"),
            "effective_request": checkpoint.get("effective_request"),
            "user_replies": list(checkpoint.get("user_replies") or []),
            "observation_pack": checkpoint.get("observation_pack"),
            "magi_baseline": checkpoint.get("magi_baseline"),
            "advisor_shadow": checkpoint.get("advisor_shadow"),
            "message": checkpoint.get("message"),
            "cooperative_result": checkpoint.get("cooperative_result"),
            "cooperative_cycle": checkpoint.get("cooperative_cycle"),
            "reason": checkpoint.get("reason"),
            "final_core_decision": checkpoint.get("final_core_decision"),
            "result_count": checkpoint.get("result_count"),
            "magi_session": checkpoint.get("magi_session"),
            "proposal_review": checkpoint.get("proposal_review"),
            "proposal_memory_intake": checkpoint.get("proposal_memory_intake"),
            "user_resume": checkpoint.get("user_resume"),
        },
        "actions": [
            {
                "action_id": str(row[0]),
                "tool": row[1],
                "operation": row[2],
                "risk": row[3],
                "action_status": row[4],
                "recorded_at": row[5],
                "started_at": row[6],
                "finished_at": row[7],
                "result_id": str(row[8]) if row[8] else None,
                "outcome": row[9],
                "summary": row[10],
                "verified_by": row[11],
                "verified_at": row[12],
                "source_uri": row[13],
            }
            for row in action_rows
        ],
        "advisor_events": [
            {
                "event_type": row[0],
                "occurred_at": row[1],
            }
            for row in advisor_event_rows
        ],
    }


def resume_core_task(task_id: UUID, reply: str) -> dict:
    """Resume one waiting Core task with a user clarification, preserving Task ID."""
    user_reply = reply.strip()
    if not user_reply:
        return {
            "task_id": str(task_id),
            "status": "waiting_external",
            "phase": "awaiting_clarification",
            "message": "追加回答を入力してください。",
        }

    with connection() as db:
        with db.transaction():
            with db.cursor() as cur:
                cur.execute(
                    """SELECT id, request, domain, status, checkpoint
                       FROM secretary.tasks
                       WHERE id=%s
                       FOR UPDATE""",
                    (task_id,),
                )
                row = cur.fetchone()
                if row is None:
                    raise ValueError("Taskが見つかりません。")
                original_request, current_domain, status, checkpoint = (
                    row[1], row[2], row[3], row[4] or {}
                )
                if status != "waiting_external":
                    raise ValueError("waiting_external のTaskだけ再開できます。")
                if checkpoint.get("core_slice") != "daily_read_only_v1":
                    raise ValueError("このTaskは日常Core最小縦断のTaskではありません。")

                entities = _entity_map(db)
                prior_capability = checkpoint.get("selected_capability")
                if prior_capability == "pkb_web_compare":
                    effective_request = original_request
                    scoped = {
                        "status": "ready",
                        "capability": "pkb_web_compare",
                        "domain": current_domain or "pc",
                    }
                else:
                    effective_request = _contextualize_core_reply(
                        original_request, user_reply, entities
                    )
                    scoped = scope_core_request(effective_request, entities)
                replies = list(checkpoint.get("user_replies") or [])
                replies.append(user_reply)

                cur.execute(
                    """INSERT INTO secretary.audit_events
                       (actor, event_type, task_id, object_type, object_id, details)
                       VALUES ('daily_core', 'core.clarification_received',
                               %s, 'task', %s, %s)""",
                    (
                        task_id,
                        task_id,
                        Jsonb({
                            "resolved": scoped["status"] == "ready",
                            "reason": scoped.get("reason"),
                            "reply_count": len(replies),
                        }),
                    ),
                )

                if scoped["status"] != "ready":
                    next_checkpoint = {
                        **checkpoint,
                        "phase": "awaiting_clarification",
                        "question": scoped.get("question"),
                        "reason": scoped.get("reason"),
                        "user_replies": replies,
                        "effective_request": effective_request,
                    }
                    cur.execute(
                        """UPDATE secretary.tasks
                           SET checkpoint=%s
                           WHERE id=%s""",
                        (Jsonb(next_checkpoint), task_id),
                    )
                    return {
                        "task_id": str(task_id),
                        "status": "waiting_external",
                        "phase": "awaiting_clarification",
                        "message": "まだ追加情報が必要です。",
                        "question": scoped["question"],
                    }

                cur.execute(
                    "UPDATE secretary.tasks SET status='running' WHERE id=%s",
                    (task_id,),
                )

                if scoped["capability"] == "pkb_web_compare":
                    plan_result = _execute_pkb_web_compare(
                        original_request,
                        target_override=user_reply,
                    )
                    executions = plan_result["executions"]
                    comparison = plan_result["comparison"]
                    answer = plan_result["answer"]
                    result = {
                        "status": "ok",
                        "result_kind": "pkb_web_compare",
                        "comparison": comparison,
                    }
                    total = sum(int(item.get("total") or 0) for item in executions)
                    enough = (
                        all(int(item.get("total") or 0) > 0 for item in executions)
                        and not plan_result.get("needs_clarification")
                    )
                else:
                    execution = _execute_core_read(
                        scoped["capability"], effective_request
                    )
                    executions = [execution]
                    comparison = None
                    answer = execution["answer"]
                    result = execution["result"]
                    total = execution["total"]
                    enough = total > 0

                phase = "completed" if enough else "awaiting_clarification"
                task_status = "completed" if enough else "waiting_external"
                question = None if enough else (
                    "比較に必要な対象情報または根拠が不足しています。"
                    "GPUのメーカー・モデルを確認してください。"
                )

                cur.execute(
                    "SELECT count(*) FROM secretary.actions WHERE task_id=%s",
                    (task_id,),
                )
                attempt = int(cur.fetchone()[0]) + 1
                action_ids = []
                result_ids = []

                for step_offset, execution in enumerate(executions):
                    step = attempt + step_offset
                    execution_result = execution["result"]
                    execution_total = int(execution.get("total") or 0)

                    cur.execute(
                        """INSERT INTO secretary.sources
                           (source_type, uri, citation, retrieved_at,
                            confidentiality, metadata)
                           VALUES ('tool', %s, %s, now(), 'private', %s)
                           RETURNING id""",
                        (
                            f"tool://daily-core/{execution['source_slug']}/{task_id}/{step}",
                            "Secretary Core resumed " + execution["citation"],
                            Jsonb({
                                "task_id": str(task_id),
                                "capability": execution["capability"],
                                "original_request": original_request,
                                "user_reply": user_reply,
                                "effective_request": effective_request,
                                "result_count": execution_total,
                                **(execution.get("source_metadata") or {}),
                            }),
                        ),
                    )
                    source_id = cur.fetchone()[0]

                    cur.execute(
                        """INSERT INTO secretary.actions
                           (task_id, actor, tool, operation, parameters, risk,
                            authorization_basis, status, idempotency_key,
                            reversible, started_at, finished_at)
                           VALUES (%s, 'daily_core', %s, %s, %s,
                                   'read_only', 'localhost_read_only',
                                   'succeeded', %s, true, now(), now())
                           RETURNING id""",
                        (
                            task_id,
                            execution["tool"],
                            execution["operation"],
                            Jsonb({
                                "query": (
                                    plan_result.get("web_query")
                                    if (
                                        scoped["capability"] == "pkb_web_compare"
                                        and execution["capability"] == "web_research"
                                    )
                                    else effective_request
                                ),
                                "bounded": True,
                                "resumed": True,
                                "step": step,
                            }),
                            f"daily-core:{task_id}:{execution['source_slug']}:{step}",
                        ),
                    )
                    action_id = cur.fetchone()[0]
                    action_ids.append(action_id)

                    cur.execute(
                        """INSERT INTO secretary.results
                           (action_id, source_id, outcome, summary, evidence,
                            verified_by, verified_at)
                           VALUES (%s, %s, %s, %s, %s,
                                   %s, now())
                           RETURNING id""",
                        (
                            action_id,
                            source_id,
                            "success" if execution_total > 0 else "inconclusive",
                            execution["answer"],
                            Jsonb({
                                "result_kind": execution_result.get("result_kind"),
                                "total": execution_total,
                                "data": execution_result,
                                "resumed": True,
                            }),
                            execution["verified_by"],
                        ),
                    )
                    result_ids.append(cur.fetchone()[0])

                action_id = action_ids[-1]
                result_id = result_ids[-1]

                next_checkpoint = {
                    **checkpoint,
                    "phase": phase,
                    "selected_capability": scoped["capability"],
                    "action_id": str(action_id),
                    "result_id": str(result_id),
                    "action_ids": [str(value) for value in action_ids],
                    "result_ids": [str(value) for value in result_ids],
                    "result_count": total,
                    "comparison": comparison,
                    "question": question,
                    "reason": None,
                    "user_replies": replies,
                    "effective_request": effective_request,
                }
                cur.execute(
                    """UPDATE secretary.tasks
                       SET status=%s,
                           domain=%s,
                           checkpoint=%s,
                           completed_at=CASE WHEN %s='completed' THEN now() ELSE NULL END
                       WHERE id=%s""",
                    (
                        task_status,
                        scoped.get("domain") or current_domain,
                        Jsonb(next_checkpoint),
                        task_status,
                        task_id,
                    ),
                )
                cur.execute(
                    """INSERT INTO secretary.audit_events
                       (actor, event_type, task_id, action_id,
                        object_type, object_id, details)
                       VALUES ('daily_core', %s, %s, %s, 'task', %s, %s)""",
                    (
                        "core.resumed_completed"
                        if total > 0
                        else "core.resumed_needs_more_context",
                        task_id,
                        action_id,
                        task_id,
                        Jsonb({
                            "capability": scoped["capability"],
                            "result_count": total,
                            "reply_count": len(replies),
                            "action_count": len(action_ids),
                            "comparison": comparison,
                        }),
                    ),
                )

                return {
                    "task_id": str(task_id),
                    "status": task_status,
                    "phase": phase,
                    "message": answer,
                    "question": question,
                    "selected_capability": scoped["capability"],
                    "capability_result": result,
                    "comparison": comparison,
                    "search": (
                        plan_result["pkb"]
                        if scoped["capability"] == "pkb_web_compare"
                        else result if scoped["capability"] == "pkb_search" else None
                    ),
                    "finance": result if scoped["capability"] == "finance_read" else None,
                    "web": (
                        plan_result["web"]
                        if scoped["capability"] == "pkb_web_compare"
                        else result if scoped["capability"] == "web_research" else None
                    ),
                    "resumed": True,
                    "effective_request": effective_request,
                }


from pkb.memory_contracts import MemoryIntake
from pkb.memory_intake import write_intake


def register_memory_intake(intake: MemoryIntake) -> dict:
    with connection() as db:
        with db.cursor() as cur:
            cur.execute("SELECT to_regclass('secretary.pkb_memory_intakes')")
            if cur.fetchone()[0] is None:
                raise ValueError('Memory Intake用の隔離DB migration 019が未適用です。')
        return write_intake(db, intake)


class TextInput(BaseModel):
    text: str = Field(min_length=1, max_length=4000)


@app.post('/api/pkb/intakes/issue')
def api_issue_intake(params: TextInput):
    return asdict(MemoryIntake.issue(params.text))


@app.post('/api/pkb/intakes')
def api_write_intake(params: MemoryIntake):
    try:
        return register_memory_intake(params)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    except (RuntimeError, psycopg.Error) as exc:
        raise HTTPException(503, 'Memory Intakeの保存に失敗しました。同じ入力のまま再試行できます。') from exc


class PendingDecisionInput(BaseModel):
    decision: str = Field(pattern="^(rejected|needs_edit)$")


@app.get("/api/core/tasks/open")
def api_core_open_tasks():
    try:
        return {"items": load_open_core_tasks(20)}
    except (RuntimeError, ValueError, psycopg.Error) as exc:
        raise HTTPException(503, str(exc)) from exc


@app.get("/api/core/tasks/completed")
def api_core_completed_tasks():
    try:
        return {"items": load_completed_core_tasks(8)}
    except (RuntimeError, ValueError, psycopg.Error) as exc:
        raise HTTPException(503, str(exc)) from exc


@app.get("/api/core/tasks/{task_id}/trace")
def api_core_trace(task_id: UUID):
    try:
        return load_core_task_trace(task_id)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    except (RuntimeError, psycopg.Error) as exc:
        raise HTTPException(503, str(exc)) from exc


@app.post("/api/core/tasks/{task_id}/resume")
def api_core_resume(task_id: UUID, params: TextInput):
    try:
        return resume_core_task(task_id, params.text)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    except (RuntimeError, psycopg.Error) as exc:
        raise HTTPException(503, str(exc)) from exc


@app.post("/api/core/request")
def api_core_request(params: TextInput):
    try:
        return run_core_request(params.text)
    except (RuntimeError, ValueError, psycopg.Error) as exc:
        raise HTTPException(503, str(exc)) from exc


@app.get("/api/pkb/entities")
def api_entities():
    try:
        with connection() as db:
            return _entities(db)
    except (RuntimeError, psycopg.Error) as exc:
        raise HTTPException(503, str(exc)) from exc


@app.post("/api/pkb/register")
def api_register(params: TextInput):
    try:
        return register_text(params.text)
    except (RuntimeError, ValueError, psycopg.Error) as exc:
        raise HTTPException(503, str(exc)) from exc


@app.post("/api/pkb/correct")
def api_correct(params: TextInput):
    try:
        return correct_text(params.text)
    except (RuntimeError, ValueError, psycopg.Error) as exc:
        raise HTTPException(503, str(exc)) from exc


@app.post("/api/pkb/search")
def api_search(params: TextInput):
    try:
        return search_text(params.text)
    except (RuntimeError, ValueError, psycopg.Error) as exc:
        raise HTTPException(503, str(exc)) from exc


@app.get("/api/pkb/pending")
def api_pending():
    try:
        with connection() as db:
            rows = list_pending(db)
            result = []
            for row in rows:
                item = dict(row)
                item["id"] = str(item["id"])
                if isinstance(item.get("recorded_at"), datetime):
                    item["recorded_at"] = item["recorded_at"].isoformat()
                result.append(item)
            return result
    except (RuntimeError, ValueError, psycopg.Error) as exc:
        raise HTTPException(503, str(exc)) from exc


@app.post("/api/pkb/pending/{pending_id}/review")
def api_pending_review(pending_id: str, params: PendingDecisionInput):
    try:
        with connection() as db:
            return asdict(review_pending(db, pending_id, params.decision))
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except (RuntimeError, psycopg.Error) as exc:
        raise HTTPException(503, str(exc)) from exc


@app.post("/api/pkb/pending/{pending_id}/accept")
def api_pending_accept(pending_id: str):
    try:
        with connection() as db:
            return asdict(accept_pending(db, pending_id))
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except (RuntimeError, psycopg.Error) as exc:
        raise HTTPException(503, str(exc)) from exc


def _debug_database_summary() -> dict:
    try:
        with connection() as db:
            with db.cursor() as cur:
                cur.execute("SELECT current_database(), current_user")
                database, user = cur.fetchone()
            info = db.info
        boundary_ok = (
            database == DBNAME
            and user == WRITER
            and (info.host or "") in ("127.0.0.1", "localhost", "::1")
        )
        return {
            "status": "ok" if boundary_ok else "warning",
            "database": database,
            "user": user,
            "host": info.host or "",
            "port": info.port,
            "boundary_ok": boundary_ok,
            "latest_migration": (
                os.environ.get("LSA_PKB_DAILY_LATEST_MIGRATION") or "unknown"
            ),
        }
    except Exception as exc:
        return {
            "status": "error",
            "database": "unavailable",
            "user": "unavailable",
            "host": "",
            "port": None,
            "boundary_ok": False,
            "latest_migration": (
                os.environ.get("LSA_PKB_DAILY_LATEST_MIGRATION") or "unknown"
            ),
            "error": type(exc).__name__,
        }


def _load_system_debug_snapshot() -> dict:
    root = Path(__file__).resolve().parents[1]
    runtime = build_runtime_snapshot(root)
    environment = build_environment_snapshot()
    try:
        with connection() as db:
            database = build_database_snapshot(
                db,
                expected_database=DBNAME,
                expected_user=WRITER,
                preflight_latest_migration=(
                    os.environ.get("LSA_PKB_DAILY_LATEST_MIGRATION") or None
                ),
                preflight_migration_count=(
                    os.environ.get("LSA_PKB_DAILY_MIGRATION_COUNT") or None
                ),
            )
    except Exception as exc:
        database = {
            "status": "error",
            "boundary_ok": False,
            "error": type(exc).__name__,
            "identity": {},
            "migration": {
                "latest": (
                    os.environ.get("LSA_PKB_DAILY_LATEST_MIGRATION") or "unknown"
                ),
                "count": (
                    os.environ.get("LSA_PKB_DAILY_MIGRATION_COUNT") or "unknown"
                ),
                "source": "launcher preflight",
            },
            "counts": [],
            "relations": [],
        }
    return {
        "runtime": runtime,
        "database": database,
        "environment": environment,
    }


_FEATURES = [
    ("Personal Knowledge Base", "利用可能", "green", "/pkb", "自然言語の記録・訂正・検索・履歴・Entity詳細・Pending"),
    ("家計・資産", "試験中", "orange", "/finance", "保存済み家計のSQL集計・明細・Import履歴とMoneyForward CSV取込"),
    ("予定", "未実装", "grey", None, "Google Calendarの閲覧・検索・PKB関連付け"),
    ("給与・税金", "未実装", "grey", None, "原本保管・抽出・照合・集計"),
    ("RITSUKO", "試験中", "blue-grey", "/core", "Secretary Core / Orchestrator。Task・MAGI通信・最終判断を管理"),
    ("利用料金・契約", "試験中", "indigo", "/service-billing", "OpenAI / Google等の利用量・料金・契約・上限をAdapter経由で正規化表示"),
    ("開発Workbench", "利用可能", "green", "http://127.0.0.1:8092/", "LLM/PKB/Coreの開発検証用。日常GUIとは分離"),
    ("システム状態 / デバッグ", "利用可能", "blue-grey", "/debug", "Runtime・PostgreSQL・主要件数・環境変数のread-only診断"),
]


def _nav():
    with ui.row().classes("w-full items-center gap-2 mb-2"):
        ui.button("TOP", icon="home").props("flat href=/ tag=a")
        ui.button("機能一覧", icon="apps").props("flat href=/features tag=a")
        ui.button("PKB", icon="account_tree").props("flat href=/pkb tag=a")
        ui.button("RITSUKO", icon="hub").props("flat href=/core tag=a")
        ui.button("家計・資産", icon="account_balance_wallet").props("flat href=/finance tag=a")
        ui.button("利用料金・契約", icon="query_stats").props("flat href=/service-billing tag=a")
        ui.button("デバッグ", icon="monitor_heart").props("flat href=/debug tag=a")
        ui.button("設定", icon="settings").props("flat href=/settings tag=a")


def _portal_header(title: str, subtitle: str):
    _nav()
    ui.label(title).classes("text-2xl font-bold")
    ui.label(subtitle).classes("text-sm text-grey-7")


def _pending_count() -> int | None:
    try:
        with connection() as db:
            return len(list_pending(db))
    except Exception:
        return None


async def _uploaded_bytes(event) -> tuple[str, bytes]:
    """Support NiceGUI 3 uploads while keeping a fallback for older event shape."""
    file_obj = getattr(event, "file", None)
    filename = (
        getattr(file_obj, "name", None)
        or getattr(event, "name", None)
        or "moneyforward.csv"
    )
    if file_obj is not None and hasattr(file_obj, "read"):
        value = file_obj.read()
        data = await value if inspect.isawaitable(value) else value
        return filename, bytes(data)
    content = getattr(event, "content", None)
    if content is not None and hasattr(content, "read"):
        value = content.read()
        data = await value if inspect.isawaitable(value) else value
        return filename, bytes(data)
    if isinstance(content, (bytes, bytearray)):
        return filename, bytes(content)
    raise ValueError("アップロード内容を読み取れません")


@ui.page("/")
def top_page():
    with ui.column().classes("w-full max-w-7xl mx-auto gap-4 p-4"):
        _portal_header(
            "Local Secretary",
            "Personal Local Secretary AI — 日常用ポータル（開発中）",
        )

        pending = _pending_count()
        debug_summary = _debug_database_summary()
        with ui.row().classes("w-full gap-4 flex-wrap"):
            with ui.card().classes("w-72 border-2 border-green-300 bg-green-50"):
                ui.label("Personal Knowledge Base").classes("text-lg font-bold")
                ui.label("記録・検索・履歴・例外確認").classes("text-sm")
                ui.label(
                    "Pending: " + (str(pending) + "件" if pending is not None else "取得不可")
                ).classes("text-sm text-purple-800")
                ui.button("PKBを開く", icon="arrow_forward", color="green").props(
                    "href=/pkb tag=a"
                )
            with ui.card().classes("w-72 border-2 border-blue-300 bg-blue-50"):
                ui.label("家計・資産").classes("text-lg font-bold")
                ui.label("保存済み家計表示＋MoneyForward CSV取込").classes("text-sm")
                ui.label("隔離DBの保存済み明細・集計・Import履歴を表示").classes(
                    "text-xs text-blue-800"
                )
                ui.button("家計・資産を開く", icon="arrow_forward", color="blue").props(
                    "href=/finance tag=a"
                )
            with ui.card().classes("w-72 border-2 border-grey-300 bg-grey-1"):
                ui.label("予定").classes("text-lg font-bold")
                ui.badge("未実装", color="grey")
                ui.label("Google Calendar閲覧・検索を予定").classes("text-sm")
            with ui.card().classes("w-72 border-2 border-blue-grey-300 bg-blue-grey-1"):
                ui.label("RITSUKO").classes("text-lg font-bold")
                ui.badge("試験中", color="blue-grey")
                ui.label("Secretary Core / Orchestrator。MAGIへ依頼し最終判断を管理").classes("text-sm")
                ui.button("RITSUKOを開く", icon="arrow_forward", color="blue-grey").props(
                    "href=/core tag=a"
                )
            with ui.card().classes("w-72 border-2 border-slate-300 bg-slate-50"):
                ui.label("システム状態 / デバッグ").classes("text-lg font-bold")
                if debug_summary["status"] == "ok":
                    ui.badge("DB OK", color="green")
                elif debug_summary["status"] == "warning":
                    ui.badge("BOUNDARY WARNING", color="orange")
                else:
                    ui.badge("DB NG", color="red")
                ui.label(
                    "DB: " + str(debug_summary.get("database") or "unavailable")
                ).classes("text-sm")
                ui.label(
                    "Migration: "
                    + str(debug_summary.get("latest_migration") or "unknown")
                ).classes("text-xs text-grey-7")
                ui.button(
                    "システム状態を開く",
                    icon="monitor_heart",
                    color="blue-grey",
                ).props("href=/debug tag=a")

        with ui.card().classes("w-full"):
            ui.label("開発中の現在地").classes("text-lg font-bold")
            ui.label("PKB: 架空隔離DBでEntity / Relation / Event / State縦断まで実機確認済み")
            ui.label("家計: MoneyForward CSV 2,270件を隔離DBへ保存し、保存済み表示へ拡張")
            ui.label("金融実データは隔離DBのみ。運用DB・外部金融サービス操作はまだ行いません。").classes(
                "text-sm text-orange-800"
            )


@ui.page("/debug")
def debug_page():
    def kv_rows(mapping: dict, keys: tuple[tuple[str, str], ...]) -> list[dict]:
        return [
            {"item": label, "value": str(mapping.get(key, "-"))}
            for key, label in keys
        ]

    with ui.column().classes("w-full max-w-7xl mx-auto gap-4 p-4"):
        _portal_header(
            "システム状態 / デバッグ",
            "現在のRuntime・PostgreSQL・DB件数・環境変数をread-onlyで確認します。",
        )
        ui.label(
            "この画面は状態表示だけを行い、DB・環境変数・Docker設定を変更しません。"
            " Secret系環境変数は値を表示せずSET / NOT SETだけを表示します。"
        ).classes("text-sm text-orange-800")

        @ui.refreshable
        def diagnostic_panel():
            snapshot = _load_system_debug_snapshot()
            runtime = snapshot["runtime"]
            database = snapshot["database"]
            environment = snapshot["environment"]
            identity = database.get("identity") or {}
            migration = database.get("migration") or {}

            with ui.row().classes("w-full gap-3 flex-wrap"):
                with ui.card().classes("min-w-56 border-2 border-blue-grey-200"):
                    ui.label("Runtime").classes("text-xs text-grey-7")
                    ui.label(
                        str(runtime.get("branch") or "unknown")
                        + " / "
                        + str(runtime.get("commit") or "unknown")[:12]
                    ).classes("text-lg font-bold")
                with ui.card().classes(
                    "min-w-56 border-2 "
                    + (
                        "border-green-300 bg-green-50"
                        if database.get("status") == "ok"
                        and database.get("boundary_ok")
                        else "border-red-300 bg-red-50"
                    )
                ):
                    ui.label("PostgreSQL").classes("text-xs text-grey-7")
                    ui.label(
                        "OK"
                        if database.get("status") == "ok"
                        and database.get("boundary_ok")
                        else "CHECK"
                    ).classes("text-lg font-bold")
                    ui.label(str(identity.get("database") or "unavailable")).classes(
                        "text-sm"
                    )
                with ui.card().classes("min-w-56 border-2 border-indigo-200"):
                    ui.label("Migration").classes("text-xs text-grey-7")
                    ui.label(str(migration.get("latest") or "unknown")).classes(
                        "text-sm font-bold"
                    )
                    ui.label(
                        "count="
                        + str(migration.get("count") or "unknown")
                        + " / "
                        + str(migration.get("source") or "unknown")
                    ).classes("text-xs text-grey-7")

            if database.get("error"):
                ui.label(
                    "DB診断エラー: " + str(database["error"])
                ).classes("text-sm text-red-700")

            with ui.expansion(
                "Runtime",
                value=True,
                icon="memory",
            ).classes("w-full border-2 border-blue-grey-200 bg-blue-grey-50"):
                runtime_rows = kv_rows(
                    runtime,
                    (
                        ("branch", "Git branch"),
                        ("commit", "Git commit"),
                        ("python", "Python"),
                        ("python_executable", "Python executable"),
                        ("platform", "Platform"),
                        ("pid", "Process PID"),
                        ("working_directory", "Working directory"),
                        ("runtime_root", "Runtime root"),
                        ("process_started_at", "Process started"),
                        ("fetched_at", "取得時刻"),
                    ),
                )
                ui.table(
                    columns=[
                        {"name": "item", "label": "項目", "field": "item", "align": "left"},
                        {"name": "value", "label": "現在値", "field": "value", "align": "left"},
                    ],
                    rows=runtime_rows,
                    row_key="item",
                ).classes("w-full")

            with ui.expansion(
                "PostgreSQL",
                value=True,
                icon="storage",
            ).classes("w-full border-2 border-green-200 bg-green-50"):
                db_rows = kv_rows(
                    identity,
                    (
                        ("database", "Database"),
                        ("user", "DB Role"),
                        ("host", "Host"),
                        ("port", "Port"),
                        ("server_version", "PostgreSQL"),
                        ("database_size", "DB Size"),
                        ("backend_pid", "Backend PID"),
                        ("database_connections", "DB Connections"),
                    ),
                )
                db_rows.extend(
                    [
                        {
                            "item": "Isolation boundary",
                            "value": (
                                "OK"
                                if database.get("boundary_ok")
                                else "WARNING / NG"
                            ),
                        },
                        {
                            "item": "Latest migration",
                            "value": str(migration.get("latest") or "unknown"),
                        },
                        {
                            "item": "Migration count",
                            "value": str(migration.get("count") or "unknown"),
                        },
                        {
                            "item": "Migration source",
                            "value": str(migration.get("source") or "unknown"),
                        },
                    ]
                )
                ui.table(
                    columns=[
                        {"name": "item", "label": "項目", "field": "item", "align": "left"},
                        {"name": "value", "label": "現在値", "field": "value", "align": "left"},
                    ],
                    rows=db_rows,
                    row_key="item",
                ).classes("w-full")
                if migration.get("database_read") == "unavailable":
                    ui.label(
                        "migration履歴は現在のDB Roleから直接読めないため、"
                        "ランチャー起動前検証の値を表示しています。"
                    ).classes("text-xs text-grey-7")

            with ui.expansion(
                "DB主要データ件数",
                value=True,
                icon="table_chart",
            ).classes("w-full border-2 border-teal-200 bg-teal-50"):
                count_rows = [
                    {
                        "item": item["label"],
                        "relation": item["relation"],
                        "count": (
                            f"{item['count']:,}"
                            if item.get("count") is not None
                            else "-"
                        ),
                        "status": item.get("status") or "unknown",
                    }
                    for item in database.get("counts") or []
                ]
                if count_rows:
                    ui.table(
                        columns=[
                            {"name": "item", "label": "データ", "field": "item", "align": "left"},
                            {"name": "relation", "label": "Relation", "field": "relation", "align": "left"},
                            {"name": "count", "label": "件数", "field": "count", "align": "right"},
                            {"name": "status", "label": "状態", "field": "status", "align": "left"},
                        ],
                        rows=count_rows,
                        row_key="relation",
                    ).classes("w-full")
                else:
                    ui.label("DB件数を取得できません。").classes("text-sm text-red-700")

            with ui.expansion(
                "DB Relation一覧",
                value=False,
                icon="view_list",
            ).classes("w-full border border-grey-300 bg-white"):
                relation_rows = database.get("relations") or []
                ui.label(
                    f"secretary schema: {len(relation_rows)} relations"
                ).classes("text-sm text-grey-7")
                if relation_rows:
                    ui.table(
                        columns=[
                            {"name": "name", "label": "Name", "field": "name", "align": "left"},
                            {"name": "type", "label": "Type", "field": "type", "align": "left"},
                        ],
                        rows=relation_rows,
                        row_key="name",
                    ).classes("w-full")

            with ui.expansion(
                "Environment",
                value=False,
                icon="tune",
            ).classes("w-full border-2 border-amber-200 bg-amber-50"):
                ui.label(
                    "Local Secretaryが利用するallowlistだけを表示します。"
                    " API Key / Secretの実値は表示しません。"
                ).classes("text-sm text-grey-7")
                env_rows = [
                    {
                        "name": item["name"],
                        "value": item["value"],
                        "kind": "SECRET (masked)" if item["secret"] else "value",
                    }
                    for item in environment
                ]
                ui.table(
                    columns=[
                        {"name": "name", "label": "環境変数", "field": "name", "align": "left"},
                        {"name": "value", "label": "現在値 / 状態", "field": "value", "align": "left"},
                        {"name": "kind", "label": "表示種別", "field": "kind", "align": "left"},
                    ],
                    rows=env_rows,
                    row_key="name",
                ).classes("w-full")

        ui.button(
            "最新状態を再取得",
            icon="refresh",
            color="blue-grey",
            on_click=diagnostic_panel.refresh,
        )
        diagnostic_panel()


@ui.page("/service-billing")
def service_billing_page():
    state = {
        "result": None,
        "error": None,
        "busy": False,
        "profiles": [],
        "connections": {},
    }

    try:
        with connection() as db:
            bootstrap_connection_auth_from_env(db)
            bootstrap_openai_billing_profile(db)
            state["profiles"] = list_service_billing_profiles(db)
            for profile in state["profiles"]:
                state["connections"][profile["connection_id"]] = (
                    get_service_connection(db, profile["connection_id"])
                )
    except Exception as exc:
        state["error"] = "利用料金・契約設定を読み込めません: " + str(exc)[:180]

    profile_by_id = {item["id"]: item for item in state["profiles"]}
    profile_options = {
        item["id"]: (
            item["display_name"]
            + " → "
            + item["connection_name"]
            + " ["
            + item["adapter_key"]
            + "]"
        )
        for item in state["profiles"]
    }

    with ui.column().classes("w-full max-w-6xl mx-auto gap-4 p-4"):
        _portal_header(
            "利用料金・契約",
            "Service Billing Profileが明示的に選んだService Connection経由で利用量・料金・契約状況を読取表示します。",
        )

        with ui.card().classes("w-full border-2 border-indigo-200"):
            ui.label("Service Billing").classes("text-xl font-bold")
            ui.label(
                "利用量 / charges / subscription / limits / creditsを別概念として扱い、"
                "取得できない値を0とはみなしません。Adapterごとに取得可能な情報だけを正規化します。"
            ).classes("text-sm text-grey-7")
            ui.label(
                "接続先・認証情報は設定画面のService Connectionで管理し、"
                "利用料金・契約はService Billing Profileのconnection_idだけを使用します。"
            ).classes("text-xs text-grey-7")

            profile_select = ui.select(
                options=profile_options,
                value=(
                    state["profiles"][0]["id"]
                    if state["profiles"]
                    else None
                ),
                label="Service Billing Profile",
            ).classes("w-full")

            with ui.row().classes("w-full items-center justify-between gap-3"):
                status = ui.label(
                    state["error"] or (
                        "未取得"
                        if state["profiles"]
                        else "Service Billing Profileがありません。設定画面でConnectionを割り当ててください。"
                    )
                ).classes(
                    "text-sm text-red-700"
                    if state["error"]
                    else "text-sm text-grey-7"
                )
                refresh_button = ui.button(
                    "最新を取得",
                    icon="refresh",
                    color="indigo",
                )
                if not state["profiles"]:
                    refresh_button.disable()

            metrics = ui.row().classes("w-full gap-3 flex-wrap")
            model_table = ui.column().classes("w-full gap-2")

            def render_result(result: dict | None, error: str | None = None):
                metrics.clear()
                model_table.clear()
                if error:
                    status.set_text(error)
                    status.classes(replace="text-sm text-red-700")
                    return
                if not result:
                    status.set_text("未取得")
                    return

                status.classes(replace="text-sm text-grey-7")
                status.set_text(
                    "取得: "
                    + str(result.get("fetched_at") or "-")
                    + " / connection: "
                    + str(result.get("connection_name") or "-")
                )
                activity = result.get("activity") or {}
                totals = activity.get("totals") or {}
                charges = result.get("charges") or {}
                charge_values = charges.get("values") or {}
                charge_text = " / ".join(
                    f"{currency.upper()} {value:,.4f}"
                    for currency, value in sorted(charge_values.items())
                ) or (
                    "0"
                    if charges.get("status") == "known"
                    else "unknown"
                )

                with metrics:
                    for title, value in (
                        ("今月の料金", charge_text),
                        (
                            "Requests",
                            f"{int(totals.get('requests') or 0):,}"
                            if activity.get("status") in {"known", "partial"}
                            else "unknown",
                        ),
                        (
                            "Input tokens",
                            f"{int(totals.get('input_tokens') or 0):,}"
                            if activity.get("status") in {"known", "partial"}
                            else "unknown",
                        ),
                        (
                            "Output tokens",
                            f"{int(totals.get('output_tokens') or 0):,}"
                            if activity.get("status") in {"known", "partial"}
                            else "unknown",
                        ),
                        (
                            "Limits",
                            str((result.get("limits") or {}).get("status") or "unknown"),
                        ),
                        (
                            "Credits",
                            str((result.get("credits") or {}).get("status") or "unknown"),
                        ),
                        (
                            "Subscription",
                            str((result.get("subscription") or {}).get("status") or "unknown"),
                        ),
                    ):
                        with ui.card().classes("min-w-48 bg-indigo-50"):
                            ui.label(title).classes("text-xs text-grey-7")
                            ui.label(value).classes("text-lg font-bold")

                with model_table:
                    ui.label("モデル別利用量").classes("font-bold")
                    rows = activity.get("by_model") or []
                    if not rows:
                        ui.label(
                            "この期間のモデル別利用実績はありません。"
                        ).classes("text-sm text-grey-7")
                    else:
                        ui.table(
                            columns=[
                                {
                                    "name": "model",
                                    "label": "Model",
                                    "field": "model",
                                    "align": "left",
                                },
                                {
                                    "name": "requests",
                                    "label": "Requests",
                                    "field": "requests",
                                    "align": "right",
                                },
                                {
                                    "name": "input_tokens",
                                    "label": "Input",
                                    "field": "input_tokens",
                                    "align": "right",
                                },
                                {
                                    "name": "output_tokens",
                                    "label": "Output",
                                    "field": "output_tokens",
                                    "align": "right",
                                },
                            ],
                            rows=rows,
                            row_key="model",
                        ).classes("w-full")

            async def refresh():
                if state["busy"]:
                    return
                profile_id = str(profile_select.value or "").strip()
                profile = profile_by_id.get(profile_id)
                if profile is None:
                    ui.notify(
                        "Service Billing Profileを選択してください",
                        type="warning",
                    )
                    return
                selected = state["connections"].get(profile["connection_id"])
                if selected is None:
                    ui.notify(
                        "割り当てられたService Connectionを読み込めません",
                        type="negative",
                    )
                    return
                state["busy"] = True
                refresh_button.disable()
                status.set_text(
                    str(profile.get("display_name") or "Service Billing")
                    + " / "
                    + str(selected.get("display_name") or "Connection")
                    + " から取得中..."
                )
                try:
                    result = await run.io_bound(
                        read_service_billing_snapshot,
                        selected,
                    )
                    state["result"] = result
                    state["error"] = None
                    render_result(result)
                except ServiceBillingError as exc:
                    state["error"] = str(exc)
                    render_result(None, state["error"])
                except Exception:
                    state["error"] = (
                        "利用料金・契約の取得で予期しないエラーが発生しました。"
                    )
                    render_result(None, state["error"])
                finally:
                    state["busy"] = False
                    refresh_button.enable()

            refresh_button.on_click(refresh)

        ui.label(
            "取得値はConnection設定へ保存しません。料金・契約・残高・利用上限は取得できない限り"
            "unknownとして扱います。接続先の変更は設定画面のProfile→Connection割当で行います。"
        ).classes("text-xs text-grey-7")


@ui.page("/features")
def features_page():
    with ui.column().classes("w-full max-w-7xl mx-auto gap-4 p-4"):
        _portal_header("機能一覧", "利用可能・試験中・未実装を日常GUIから確認")
        with ui.grid(columns=2).classes("w-full gap-4"):
            for name, status, color, target, description in _FEATURES:
                with ui.card().classes("w-full"):
                    with ui.row().classes("w-full items-center justify-between"):
                        ui.label(name).classes("text-lg font-bold")
                        ui.badge(status, color=color)
                    ui.label(description).classes("text-sm")
                    if target:
                        ui.button("開く", icon="open_in_new", color="blue").props(
                            f"href={target} tag=a flat"
                        )


@ui.page("/core/history")
def core_history_page():
    state = {"offset": 0}
    page_size = 20
    with ui.column().classes("w-full max-w-5xl mx-auto p-4 gap-3"):
        _nav()
        ui.label("Task履歴").classes("text-2xl font-bold")
        ui.link("RITSUKOへ戻る", "/core")
        ui.label("失敗を含む全状態のTaskを更新日時の新しい順で表示します。")

        def move_page(delta):
            state["offset"] = max(0, state["offset"] + delta * page_size)
            history.refresh()

        @ui.refreshable
        def history():
            try:
                rows = load_recent_core_tasks(limit=page_size + 1, offset=state["offset"])
            except Exception as exc:
                ui.label("履歴を取得できません: " + str(exc)).classes("text-red-700")
                ui.button("再読み込み", on_click=history.refresh)
                return
            with ui.row().classes("items-center"):
                ui.button("前へ", on_click=lambda: move_page(-1)).set_enabled(state["offset"] > 0)
                ui.label(f"{state['offset'] // page_size + 1}ページ")
                ui.button("次へ", on_click=lambda: move_page(1)).set_enabled(len(rows) > page_size)
            if not rows:
                ui.label("Taskはありません。")
            for item in rows[:page_size]:
                with ui.card().classes("w-full gap-1"):
                    ui.badge(item["status"])
                    ui.label(item["request"])
                    ui.label(str(item["updated_at"])).classes("text-xs text-grey-7")
                    ui.link("Taskを開く", "/core?task_id=" + item["id"])
        history()


@ui.page("/core")
def core_page(task_id: str = ""):
    state = {"result": None, "busy": False, "resume_busy": False,
             "trace": None, "trace_error": None,
             "advisor_model": _UI_PREFERENCES.get("core_advisor_model"),
             "advisor_timeout": int(_UI_PREFERENCES.get("core_advisor_timeout") or 60),
             "protocol_result": None, "protocol_busy": False,
             "guided_session": None, "guided_busy": False,
             "guided_turn": 0, "guided_stop_event": None,
             "guided_stop_requested": False,
             "guided_history_read_only": False}

    list_limits = {key: _UI_PREFERENCES["core"][key] for key in CORE_TASK_LIST_DEFAULTS}
    list_defaults = dict(list_limits)

    def more_tasks(key, panel):
        list_limits[key] += _UI_PREFERENCES["core"][key]
        panel.refresh()

    def current_ooda():
        if state["busy"] or state["resume_busy"]:
            return derive_ooda({"status": "received", "phase": "observe"})
        trace = state["trace"]
        if trace:
            return derive_ooda(trace["task"], trace["actions"])
        return derive_ooda(state["result"])

    def current_advisor_shadow():
        # Prefer DB trace because background Advisor updates arrive after the
        # deterministic result object has already been returned to the UI.
        trace = state["trace"]
        if trace:
            shadow = (trace.get("task") or {}).get("advisor_shadow")
            if shadow:
                return shadow
        result = state["result"] or {}
        return result.get("advisor_shadow")

    def load_current_trace():
        # Share one snapshot between the bar, Task detail and execution log.
        state["trace"] = None
        state["trace_error"] = None
        task_id = (state["result"] or {}).get("task_id")
        if task_id:
            try:
                state["trace"] = load_core_task_trace(UUID(task_id))
            except Exception as exc:
                state["trace_error"] = str(exc)

    async def poll_advisor_shadow():
        result = state["result"] or {}
        task_id = result.get("task_id")
        if not task_id:
            return
        advisor = current_advisor_shadow() or {}
        if advisor.get("job_status") not in {"queued", "running"}:
            return
        try:
            state["trace"] = await run.io_bound(
                load_core_task_trace, UUID(task_id)
            )
            state["trace_error"] = None
            trace_task = (state["trace"] or {}).get("task") or {}
            for key in ("status", "phase", "selected_capability", "question"):
                if key in trace_task:
                    result[key] = trace_task.get(key)
            if trace_task.get("message"):
                result["message"] = trace_task.get("message")
            cooperative_result = trace_task.get("cooperative_result")
            if cooperative_result and trace_task.get("selected_capability") == "pkb_search":
                result["search"] = cooperative_result
        except Exception as exc:
            state["trace_error"] = str(exc)
            return
        ooda_bar.refresh()
        core_result.refresh()
        trace_panel.refresh()
        resume_panel.refresh()
        open_tasks_panel.refresh()
        completed_tasks_panel.refresh()
        screen_log_panel.refresh()

    # NiceGUI drawers are top-level layout elements and must be created as
    # direct children of the page, not inside the central content column.
    task_drawer = ui.right_drawer(value=True).classes("bg-orange-50 p-3").props(
        "bordered width=300 breakpoint=700"
    )

    def remember_core_expansion(key: str):
        def _remember(event):
            _set_core_ui_open(key, event.value)
        return _remember

    with ui.column().classes("w-full max-w-5xl mx-auto gap-4 p-4"):
        _nav()
        ui.label("RITSUKO — Secretary Core / Orchestrator").classes("text-2xl font-bold")

        try:
            installed_magi_models = list_magi_models()
        except Exception:
            installed_magi_models = []
        try:
            default_magi_model = choose_magi_model(installed_magi_models)
        except Exception:
            default_magi_model = None

        def copy_protocol_json(text: str, label: str) -> None:
            ui.run_javascript(
                "navigator.clipboard.writeText("
                + json.dumps(text, ensure_ascii=False) + ")"
            )
            ui.notify(label + "をコピーしました", type="positive")

        with ui.card().classes("w-full border-2 border-teal-300 bg-teal-50"):
            ui.label("RITSUKO ⇄ MAGI Observation Loop v1").classes(
                "text-lg font-bold text-teal-900"
            )
            ui.label(
                "RITSUKOが分類を入口に、Task状態・前回返答・Observationから"
                "次の質問目的を選び、問いを組み立て直します。"
            ).classes("text-sm")
            ui.label(
                "MELCHIOR / CASPER / BALTHASARはProvider非依存のLLM席です。"
                "分類・分析でPKB readが必要と判断された場合、RITSUKOがread-onlyで実PKBを取得し、"
                "Task / Action / Result / Source / Auditへ記録してObservationとして再投入します。"
                "個人PKB Observationを含む再分析はCloud Context Gateによりlocal席だけへ送信します。"
                "このv1ではWeb・家計・記憶書込・外部操作は自動実行しません。"
            ).classes("text-xs text-orange-800")
            guided_input = ui.textarea(
                label="RITSUKOへ依頼",
                value="メインPCのGPUの種類は？",
            ).classes("w-full")

            try:
                magi_profiles, configured_specs = _load_magi_configuration(
                    installed_magi_models, default_magi_model
                )
                state["magi_settings_error"] = None
            except Exception as exc:
                magi_profiles = []
                configured_specs = fallback_member_specs(default_magi_model)
                state["magi_settings_error"] = str(exc)

            state["magi_member_specs"] = configured_specs
            spec_by_member = {item["name"]: item for item in configured_specs}
            profile_options = {
                item["id"]: (
                    f"{item['display_name']}  [{item['provider']} / {item['model']}]"
                    + (
                        f" [ctx={int(item['context_window_tokens']) // 1024}K]"
                        f" [gen={int(item['ollama_num_predict'])}]"
                        if item["provider"] == "ollama"
                        and item.get("context_window_tokens")
                        and item.get("ollama_num_predict")
                        else ""
                    )
                )
                for item in magi_profiles
            }
            guided_member_controls = {}

            if state.get("magi_settings_error"):
                ui.label(
                    "DBのMAGI設定を読み込めません。bootstrap値を表示中: "
                    + state["magi_settings_error"][:180]
                ).classes("text-xs text-red-700")

            ui.label("MAGI member configuration").classes("font-medium text-teal-900")
            with ui.element("div").classes("w-full grid grid-cols-3 gap-3 items-stretch"):
                for member in MEMBER_NAMES:
                    spec = spec_by_member.get(member) or {
                        "profile_id": None, "enabled": False,
                        "weight": 1.0, "timeout_seconds": DEFAULT_TIMEOUT_SECONDS,
                    }
                    with ui.card().classes("w-full min-w-0 border border-teal-200 bg-white"):
                        ui.label(member).classes("font-bold")
                        enabled_control = ui.switch(
                            "有効", value=bool(spec.get("enabled"))
                        )
                        profile_control = ui.select(
                            options=profile_options,
                            value=spec.get("profile_id"),
                            label=f"{member} / LLM profile",
                        ).classes("w-full")
                        weight_control = ui.number(
                            label="Weight",
                            value=float(spec.get("weight") or 1.0),
                            min=0.1, max=100, step=0.1,
                        ).classes("w-full")
                        timeout_control = ui.select(
                            options=list(CORE_ADVISOR_TIMEOUT_OPTIONS),
                            value=int(spec.get("timeout_seconds") or DEFAULT_TIMEOUT_SECONDS),
                            label="Timeout（秒）",
                        ).classes("w-full")
                        retry_control = ui.switch(
                            "Turn内リトライ",
                            value=bool(
                                spec.get("retry_within_turn", DEFAULT_RETRY_WITHIN_TURN)
                            ),
                        )
                        ui.label(
                            "Retry時間上限はTimeoutの50%・最大3回"
                        ).classes("text-xs text-grey-7")
                        guided_member_controls[member] = {
                            "enabled": enabled_control,
                            "profile": profile_control,
                            "weight": weight_control,
                            "timeout": timeout_control,
                            "retry": retry_control,
                        }

            def collect_guided_assignments() -> list[dict]:
                return [
                    {
                        "name": member,
                        "profile_id": guided_member_controls[member]["profile"].value,
                        "enabled": bool(guided_member_controls[member]["enabled"].value),
                        "weight": float(guided_member_controls[member]["weight"].value or 1.0),
                        "timeout_seconds": int(
                            guided_member_controls[member]["timeout"].value or DEFAULT_TIMEOUT_SECONDS
                        ),
                        "retry_within_turn": bool(
                            guided_member_controls[member]["retry"].value
                        ),
                    }
                    for member in MEMBER_NAMES
                ]

            def save_guided_assignments(*, notify: bool = True) -> list[dict] | None:
                try:
                    saved = _save_magi_assignments(collect_guided_assignments())
                    state["magi_member_specs"] = saved
                    state["magi_settings_error"] = None
                    if notify:
                        ui.notify("MAGI LLM設定をDBへ保存しました", type="positive")
                    return saved
                except Exception as exc:
                    state["magi_settings_error"] = str(exc)
                    ui.notify(
                        "MAGI LLM設定を保存できません: " + str(exc)[:180],
                        type="negative",
                    )
                    return None

            with ui.row().classes("w-full gap-2 items-center"):
                ui.button(
                    "LLM設定を保存",
                    icon="save",
                    on_click=lambda: save_guided_assignments(notify=True),
                ).props("outline dense")
                ui.link("LLM profileの追加・確認", "/settings").classes(
                    "text-sm text-blue-700"
                )

            def guided_timeout_seconds(session: dict | None = None) -> float:
                specs = (session or {}).get("member_specs") or state.get("magi_member_specs") or []
                enabled = [
                    int(item.get("timeout_seconds") or DEFAULT_TIMEOUT_SECONDS)
                    for item in specs if item.get("enabled")
                ]
                return float(
                    max(enabled) if enabled else DEFAULT_TIMEOUT_SECONDS
                )

            def begin_guided_run(next_turn: int) -> Event:
                stop_event = Event()
                state["guided_stop_event"] = stop_event
                state["guided_stop_requested"] = False
                state["guided_turn"] = max(1, int(next_turn))
                state["guided_busy"] = True
                return stop_event

            def note_guided_turn(turn_number: int) -> None:
                state["guided_turn"] = max(1, int(turn_number))

            def request_guided_stop() -> None:
                stop_event = state.get("guided_stop_event")
                if not state.get("guided_busy") or stop_event is None:
                    return
                stop_event.set()
                state["guided_stop_requested"] = True
                ui.notify(
                    "現在のTurn完了後に停止します",
                    type="warning",
                )
                guided_result_panel.refresh()

            def saved_task_for_session(session: dict) -> dict:
                trace_task = (state.get("trace") or {}).get("task") or {}
                trace_task_id = str(
                    trace_task.get("id") or trace_task.get("task_id") or ""
                )
                session_task_id = str(session.get("task_id") or "")
                if not session_task_id or trace_task_id != session_task_id:
                    return {}
                return trace_task

            @ui.refreshable
            def guided_result_panel():
                session = state.get("guided_session")
                if state["guided_busy"]:
                    ui.label("RITSUKO ⇄ MAGI 対話中...").classes("font-bold text-teal-900")
                    ui.label(
                        f"Turn {int(state.get('guided_turn') or 1)}"
                        f"（各resume cycle最大{MAX_TURNS} Turn）"
                    ).classes("font-mono text-sm")
                    if state.get("guided_stop_requested"):
                        ui.label(
                            "停止要求済み：現在のTurnが完了したら次へ進まず停止します。"
                        ).classes("text-sm text-orange-900")
                    else:
                        ui.button(
                            "このTurnで停止",
                            icon="stop_circle",
                            color="orange",
                            on_click=request_guided_stop,
                        ).props("outline")
                    return
                if session is None:
                    ui.label("初回はLLMに分類だけを聞き、回答に合わせて次の問いを送ります。").classes(
                        "text-sm text-grey-7"
                    )
                    return
                saved_task = saved_task_for_session(session)
                if state.get("guided_history_read_only"):
                    ui.label("保存済みTaskのMAGI対話を閲覧中（read-only）").classes(
                        "font-bold text-blue-grey-800"
                    )
                    ui.label(
                        "Task status="
                        + str(saved_task.get("status") or "-")
                        + " / MAGI session status="
                        + str(session.get("status") or "-")
                    ).classes("font-mono text-xs text-grey-7")
                ui.label(
                    f"Task: {session['task_id']} / status={session['status']}"
                    f" / RITSUKO next={session['next_step']}"
                ).classes("font-mono text-xs")
                if session.get("tool_read_executed"):
                    ui.label(
                        "実PKB read済み / Action・Result記録対象"
                    ).classes("text-xs text-green-800")
                gate = session.get("cloud_context_gate") or {}
                if gate:
                    ui.label(
                        "Cloud Context Gate: " + str(gate.get("status") or "-")
                        + " / " + str(gate.get("mode") or "-")
                    ).classes("font-mono text-xs text-purple-800")
                proposal_review = saved_task.get("proposal_review")
                if isinstance(proposal_review, dict):
                    ui.label(
                        "Proposal review: "
                        + str(proposal_review.get("decision") or "-")
                        + " / " + str(proposal_review.get("status") or "-")
                    ).classes("font-mono text-xs text-green-800")
                    evaluation = proposal_review.get("magi_evaluation")
                    if isinstance(evaluation, dict):
                        ui.label(
                            "Post-review MAGI: "
                            + str(evaluation.get("state") or "-")
                        ).classes("font-mono text-xs text-purple-800")
                    memory_summary = proposal_review.get("memory_intake")
                    if isinstance(memory_summary, dict):
                        decisions = [
                            str(item.get("decision") or "-")
                            for item in (memory_summary.get("receipts") or [])
                            if isinstance(item, dict)
                        ]
                        ui.label(
                            "Memory Intake: "
                            + str(memory_summary.get("status") or "-")
                            + (" / " + ", ".join(decisions) if decisions else "")
                        ).classes("font-mono text-xs text-green-800")
                if (
                    not state.get("guided_history_read_only")
                    and session["status"] == "waiting_information"
                ):
                    ui.label(
                        "未解決の情報要求（自動PKB read対象外または追加情報が必要）"
                    ).classes("font-bold text-orange-900")
                    ui.code(json.dumps(
                        session["pending_requests"], ensure_ascii=False, indent=2
                    ), language="json").classes("w-full")
                    observation_input = ui.textarea(
                        label="開発用手動Observation（実PKB取得ではない）",
                        placeholder="自動read対象外の開発検証にだけ使用",
                    ).classes("w-full")

                    async def continue_guided():
                        if state["guided_busy"]:
                            return
                        if not str(observation_input.value or "").strip():
                            ui.notify("試験用Observationを入力してください", type="warning")
                            return
                        stop_event = begin_guided_run(len(session["turns"]) + 1)
                        guided_button.disable()
                        guided_result_panel.refresh()
                        try:
                            state["guided_session"] = await continue_with_observation_async(
                                session,
                                observation_input.value,
                                timeout=guided_timeout_seconds(session),
                                stop_requested=stop_event.is_set,
                                on_turn_start=note_guided_turn,
                            )
                        except Exception as exc:
                            ui.notify(type(exc).__name__ + ": " + str(exc)[:160], type="negative")
                        finally:
                            state["guided_busy"] = False
                            state["guided_stop_event"] = None
                            guided_button.enable()
                            guided_result_panel.refresh()

                    ui.button(
                        "手動Observationで継続（開発用）",
                        icon="refresh", on_click=continue_guided,
                    ).props("outline")
                elif (
                    not state.get("guided_history_read_only")
                    and session["status"] == "waiting_user"
                ):
                    with ui.card().classes(
                        "w-full border border-orange-300 bg-orange-50 shadow-none"
                    ):
                        ui.label("RITSUKOからの確認").classes(
                            "font-bold text-orange-900"
                        )
                        ui.label(
                            "RITSUKOが本人への確認を必要とする状態です。"
                        ).classes("text-sm text-orange-900")
                        user_resume = saved_task.get("user_resume")
                        if isinstance(user_resume, dict) and user_resume.get("status") in {
                            "processing", "retry_required"
                        }:
                            ui.label(
                                "前回の本人回答Turnは "
                                + str(user_resume.get("status"))
                                + " です。安全のため、前回と同じ回答内容を再入力して再試行してください。"
                            ).classes("text-xs text-orange-800")
                        question = session.get("user_question") or (
                            (session.get("detail") or {}).get("question_for_user")
                        )
                        if question:
                            ui.label("質問: " + question).classes("text-sm")
                        clarification_input = ui.textarea(
                            label="追加説明・選択",
                            placeholder="回答や追加説明を自然な言葉で入力",
                        ).props("rows=2 outlined dense").classes("w-full")

                        async def continue_with_user():
                            if state["guided_busy"]:
                                return
                            if not str(clarification_input.value or "").strip():
                                ui.notify("追加説明を入力してください", type="warning")
                                return
                            stop_event = begin_guided_run(len(session["turns"]) + 1)
                            guided_button.disable()
                            guided_result_panel.refresh()
                            try:
                                state["guided_session"] = await resume_user_answer(
                                    UUID(session["task_id"]),
                                    clarification_input.value,
                                    timeout=guided_timeout_seconds(session),
                                    claim_user_resume_record=_claim_magi_user_resume_record,
                                    persist_session_record=_persist_magi_core_session_record,
                                    abort_user_resume_record=_abort_magi_user_resume_record,
                                    fail_task_record=_fail_magi_core_task_record,
                                    stop_requested=stop_event.is_set,
                                    on_turn_start=note_guided_turn,
                                )
                            except Exception as exc:
                                ui.notify(
                                    type(exc).__name__ + ": " + str(exc)[:160],
                                    type="negative",
                                )
                            finally:
                                state["guided_busy"] = False
                                state["guided_stop_event"] = None
                                guided_button.enable()
                                try:
                                    saved_trace = load_core_task_trace(
                                        UUID(session["task_id"])
                                    )
                                    if (
                                        (state.get("result") or {}).get("task_id")
                                        == session["task_id"]
                                    ):
                                        state["result"] = core_task_selection_result(
                                            saved_trace["task"]
                                        )
                                        state["trace"] = saved_trace
                                except Exception:
                                    pass
                                guided_result_panel.refresh()
                                open_tasks_panel.refresh()
                                completed_tasks_panel.refresh()
                                core_result.refresh()
                                trace_panel.refresh()
                                resume_panel.refresh()

                        ui.button(
                            "追加説明を渡して対話継続",
                            icon="chat",
                            on_click=continue_with_user,
                        ).props("outline")
                elif (
                    not state.get("guided_history_read_only")
                    and session["status"] == "proposal_ready"
                ):
                    detail = session.get("detail") or {}
                    if detail.get("state") == "KNOWLEDGE_CANDIDATE":
                        with ui.card().classes(
                            "w-full border border-blue-300 bg-blue-50 shadow-none"
                        ):
                            ui.label("RITSUKOからの確認").classes(
                                "font-bold text-blue-900"
                            )
                            reviewable_proposal = (
                                reviewable_user_knowledge_proposal(session)
                            )
                            answer = str(
                                (
                                    reviewable_proposal or {}
                                ).get("answer")
                                or detail.get("answer_candidate")
                                or ""
                            ).strip()
                            knowledge = str(
                                (
                                    reviewable_proposal or {}
                                ).get("knowledge_candidate")
                                or detail.get("knowledge_candidate")
                                or ""
                            ).strip()
                            ui.label(
                                "本人回答を根拠に、回答候補と記憶候補ができています。"
                            ).classes("font-bold text-orange-900")
                            if answer:
                                ui.label("回答候補: " + answer).classes("text-sm")
                            if knowledge:
                                ui.label("記憶候補: " + knowledge).classes(
                                    "text-sm font-medium"
                                )
                            if (
                                isinstance(reviewable_proposal, dict)
                                and reviewable_proposal.get("answer_source")
                                == "knowledge_candidate_fallback"
                            ):
                                ui.label(
                                    "回答候補が未生成だったため、本人回答でGrounding済みの"
                                    "記憶候補を回答文として使用します。"
                                ).classes("text-xs text-blue-800")
                            if reviewable_proposal is None:
                                ui.label(
                                    "本人回答とのGroundingを検証できないため、"
                                    "このProposalは完了操作できません。"
                                ).classes("text-xs text-red-700 font-medium")
                            ui.label(
                                "「回答だけで完了」はPKBへ新規記憶を書きません。"
                                "「記憶にも反映」は表示中の記憶候補を本人が確認した内容として"
                                "Memory IntakeのGrounding / WriteDecisionへ渡し、"
                                "その結果をObservationとしてMAGIへ再評価してから"
                                "RITSUKOが完了判定します。"
                                "MAGIが直接PKBを書き換えることはありません。"
                            ).classes("text-xs text-grey-7")
                            active_review = saved_task.get("proposal_review")
                            prepared_intake = saved_task.get(
                                "proposal_memory_intake"
                            )
                            active_review_status = (
                                active_review.get("status")
                                if isinstance(active_review, dict)
                                else None
                            )
                            active_review_decision = (
                                active_review.get("decision")
                                if isinstance(active_review, dict)
                                else None
                            )
                            if active_review_status in {
                                "processing", "retry_required"
                            }:
                                ui.label(
                                    "前回のProposal review: "
                                    + str(active_review_decision or "-")
                                    + " / "
                                    + str(active_review_status)
                                    + "。同じTaskで再評価を再試行できます。"
                                ).classes("text-xs text-orange-800")
                            if isinstance(prepared_intake, dict):
                                ui.label(
                                    "Memory Intakeは開始済みです。"
                                    "同じinput_idで再送・再評価し、"
                                    "回答だけ経路へは戻しません。"
                                ).classes("text-xs text-blue-800")

                            async def load_after_proposal_review() -> None:
                                saved_trace = await run.io_bound(
                                    load_core_task_trace,
                                    UUID(session["task_id"]),
                                )
                                state["trace"] = saved_trace
                                state["result"] = core_task_selection_result(
                                    saved_trace["task"]
                                )
                                state["guided_session"] = (
                                    saved_trace["task"].get("magi_session")
                                    or session
                                )
                                state["guided_history_read_only"] = True

                            async def execute_proposal_review(
                                decision: str,
                            ) -> None:
                                if state["guided_busy"]:
                                    return
                                notification_client = context.client
                                notification_message = None
                                notification_type = None
                                stop_event = begin_guided_run(
                                    len(session["turns"]) + 1
                                )
                                answer_only_button.disable()
                                remember_button.disable()
                                guided_button.disable()
                                guided_result_panel.refresh()
                                memory_result = None
                                try:
                                    if decision == "remember":
                                        intake = await run.io_bound(
                                            _prepare_magi_memory_intake_record,
                                            UUID(session["task_id"]),
                                        )
                                        memory_result = await run.io_bound(
                                            register_memory_intake,
                                            intake,
                                        )
                                    state["guided_session"] = (
                                        await review_magi_proposal(
                                            UUID(session["task_id"]),
                                            decision,
                                            timeout=guided_timeout_seconds(
                                                session
                                            ),
                                            claim_proposal_review_record=(
                                                _claim_magi_proposal_review_record
                                            ),
                                            finalize_proposal_review_record=(
                                                _finalize_magi_proposal_review_record
                                            ),
                                            abort_proposal_review_record=(
                                                _abort_magi_proposal_review_record
                                            ),
                                            memory_result=memory_result,
                                            stop_requested=stop_event.is_set,
                                            on_turn_start=note_guided_turn,
                                        )
                                    )
                                    await load_after_proposal_review()
                                    if decision == "remember":
                                        decisions = [
                                            str(item.get("decision") or "-")
                                            for item in (
                                                (memory_result or {}).get(
                                                    "candidates"
                                                )
                                                or []
                                            )
                                            if isinstance(item, dict)
                                        ]
                                        notification_message = (
                                            "Memory Intake結果をMAGIへ再評価し、"
                                            "RITSUKOがTaskを完了しました"
                                            + (
                                                " (" + ", ".join(decisions) + ")"
                                                if decisions
                                                else ""
                                            )
                                        )
                                    else:
                                        notification_message = (
                                            "回答レビューをMAGIへ再評価し、"
                                            "RITSUKOがTaskを完了しました"
                                        )
                                    notification_type = "positive"
                                except Exception as exc:
                                    try:
                                        saved_trace = await run.io_bound(
                                            load_core_task_trace,
                                            UUID(session["task_id"]),
                                        )
                                        state["trace"] = saved_trace
                                        state["result"] = (
                                            core_task_selection_result(
                                                saved_trace["task"]
                                            )
                                        )
                                    except Exception:
                                        pass
                                    notification_message = (
                                        type(exc).__name__
                                        + ": "
                                        + str(exc)[:180]
                                    )
                                    notification_type = "negative"
                                finally:
                                    state["guided_busy"] = False
                                    state["guided_stop_event"] = None
                                    guided_button.enable()
                                    guided_result_panel.refresh()
                                    open_tasks_panel.refresh()
                                    completed_tasks_panel.refresh()
                                    core_result.refresh()
                                    trace_panel.refresh()
                                    resume_panel.refresh()
                                if notification_message and notification_type:
                                    _notify_client(
                                        notification_client,
                                        notification_message,
                                        type=notification_type,
                                    )

                            async def complete_answer_only():
                                await execute_proposal_review("answer_only")

                            async def remember_and_complete():
                                await execute_proposal_review("remember")

                            with ui.row().classes("gap-2 flex-wrap"):
                                answer_label = (
                                    "回答レビューを再試行"
                                    if (
                                        active_review_decision == "answer_only"
                                        and active_review_status
                                        in {"processing", "retry_required"}
                                    )
                                    else "回答だけで完了"
                                )
                                remember_label = (
                                    "記憶結果の再評価を再試行"
                                    if (
                                        isinstance(prepared_intake, dict)
                                        or active_review_decision == "remember"
                                    )
                                    else "記憶にも反映して完了"
                                )
                                answer_only_button = ui.button(
                                    answer_label,
                                    icon="done",
                                    color="green",
                                    on_click=complete_answer_only,
                                )
                                remember_button = ui.button(
                                    remember_label,
                                    icon="save",
                                    color="blue",
                                    on_click=remember_and_complete,
                                )
                                if reviewable_proposal is None:
                                    answer_only_button.disable()
                                    remember_button.disable()
                                if isinstance(prepared_intake, dict) or (
                                    active_review_status == "processing"
                                    and active_review_decision == "remember"
                                ):
                                    answer_only_button.disable()
                                if (
                                    active_review_status == "processing"
                                    and active_review_decision == "answer_only"
                                ):
                                    remember_button.disable()
                    else:
                        ui.label(
                            "このProposal種別の実行経路はまだ接続していません。"
                        ).classes("text-sm text-orange-900")
                session_text = json.dumps(export_dialogue(session), ensure_ascii=False, indent=2)
                ui.button(
                    "対話結果を一括コピー", icon="content_copy",
                    on_click=lambda value=session_text: copy_protocol_json(value, "対話結果"),
                ).props("outline dense")
                ui.label(
                    "Prompt: " + str(session.get("prompt_version") or "-")
                    + " / 最終Question Purpose: " + str(session.get("last_question_purpose") or "-")
                ).classes("font-mono text-xs text-grey-7")
                if session.get("classification"):
                    ui.label(
                        "分類: " + session["classification"]["category"]
                        + " / 理解: " + session["classification"]["understood_request"]
                    ).classes("font-bold")
                if session.get("detail"):
                    detail = session["detail"]
                    ui.label(
                        "次の分析: " + detail["state"] + " / " + detail["reason"]
                    ).classes("font-medium")
                for turn in session["turns"]:
                    purpose = turn.get("question_purpose") or turn["request_envelope"].get("question_purpose")
                    with ui.expansion(
                        f"Turn {turn['request_envelope']['turn']}: "
                        + ("大まかな分類" if turn["stage"] == "classify" else
                           f"{purpose or 'analysis'} / 再分析"),
                        value=turn is session["turns"][-1],
                    ).classes("w-full border"):
                        ui.label(f"status={turn['status']} / errors={turn['errors']}").classes(
                            "font-mono text-xs"
                        )
                        ui.label("MAGI統合結果").classes("font-bold text-sm")
                        ui.code(
                            json.dumps(turn["response"], ensure_ascii=False, indent=2)
                            if turn["response"] is not None else "null",
                            language="json",
                        ).classes("w-full")
                        if turn.get("consensus") is not None:
                            ui.label("重み付き投票").classes("font-bold text-sm")
                            ui.code(
                                json.dumps(turn["consensus"], ensure_ascii=False, indent=2),
                                language="json",
                            ).classes("w-full")
                        if turn.get("member_results"):
                            ui.label("各MAGI member返答").classes("font-bold text-sm")
                            ui.code(
                                json.dumps(turn["member_results"], ensure_ascii=False, indent=2),
                                language="json",
                            ).classes("w-full")
                        ui.label("RITSUKOからの質問・Envelope").classes("font-bold text-sm")
                        ui.code(
                            json.dumps(turn["request_envelope"], ensure_ascii=False, indent=2),
                            language="json",
                        ).classes("w-full")
                        ui.label("通信診断").classes("font-bold text-sm")
                        ui.code(
                            json.dumps(turn["diagnostic"], ensure_ascii=False, indent=2),
                            language="json",
                        ).classes("w-full")


            async def start_guided():
                if state["guided_busy"]:
                    return
                request_text = str(guided_input.value or "").strip()
                if not request_text:
                    ui.notify("入力文を指定してください", type="warning")
                    return
                specs = save_guided_assignments(notify=False)
                if not specs:
                    return
                stop_event = begin_guided_run(1)
                state["guided_session"] = None
                state["guided_history_read_only"] = False
                state["trace"] = None
                state["trace_error"] = None
                guided_button.disable()
                guided_result_panel.refresh()
                try:
                    state["guided_session"] = await run_pkb_observation_loop(
                        request_text,
                        member_specs=specs,
                        timeout=guided_timeout_seconds(),
                        create_task_record=_create_magi_core_task_record,
                        execute_pkb_request=_execute_magi_pkb_request,
                        record_pkb_read_record=_record_magi_pkb_read_record,
                        persist_session_record=_persist_magi_core_session_record,
                        fail_task_record=_fail_magi_core_task_record,
                        stop_requested=stop_event.is_set,
                        on_turn_start=note_guided_turn,
                    )
                except Exception as exc:
                    ui.notify(type(exc).__name__ + ": " + str(exc)[:160], type="negative")
                finally:
                    state["guided_busy"] = False
                    state["guided_stop_event"] = None
                    guided_button.enable()
                    guided_result_panel.refresh()
                    open_tasks_panel.refresh()
                    completed_tasks_panel.refresh()

            guided_button = ui.button(
                "RITSUKOへ依頼", icon="play_arrow",
                color="teal", on_click=start_guided,
            )
            guided_result_panel()

            def refresh_guided_progress() -> None:
                if state.get("guided_busy"):
                    guided_result_panel.refresh()

            ui.timer(1.0, refresh_guided_progress)

        with ui.expansion(
            "旧 Protocol v1 全項目一括分析（比較用）",
            value=False, icon="history",
        ).classes("w-full border"):
            ui.label(
                "前の通信方式は比較用に保存。今回の分類対話には使いません。"
            ).classes("text-xs text-grey-7")
            with ui.card().classes("w-full border-2 border-indigo-300 bg-indigo-50"):
                ui.label("RITSUKO → MAGI Protocol v1 / Cycle 1 試験").classes(
                    "text-lg font-bold text-indigo-900"
                )
                ui.label(
                    "RITSUKOが依頼Envelopeを作成 → MELCHIOR slotのLLMへ送信 → "
                    "RITSUKOがanalysis_resultを受信・Schema検証します。"
                ).classes("text-sm")
                ui.label(
                    "旧deterministic routerへのfallback、PKB/Web read、Task DB更新は行いません。"
                ).classes("text-xs text-orange-800")
                protocol_input = ui.textarea(
                    label="ユーザー原文",
                    value="メインPCのGPUの種類は？",
                ).classes("w-full")
                ui.label("MAGI member configuration").classes("font-medium text-indigo-900")
                ui.label(
                    "3つのmemberと実モデルは独立です。現在のCycle 1試験で実行するのは"
                    "MELCHIORのみ。無効な2枠のモデルはロードしません。"
                ).classes("text-xs text-grey-7")
                with ui.row().classes("w-full items-stretch gap-3 flex-wrap"):
                    with ui.card().classes("min-w-64 grow border border-indigo-300 bg-white"):
                        ui.label("MELCHIOR").classes("font-bold")
                        ui.switch("有効（Cycle 1）", value=True).disable()
                        ui.label("Provider: Ollama").classes("text-xs text-grey-7")
                        protocol_model_select = ui.select(
                            options=installed_magi_models,
                            value=default_magi_model,
                            label="MELCHIOR / model",
                        ).classes("w-full")
                    for inactive_member in ("BALTHASAR", "CASPER"):
                        with ui.card().classes("min-w-64 grow border border-grey-300 bg-white"):
                            ui.label(inactive_member).classes("font-bold")
                            ui.switch("無効（multi-member未実装）", value=False).disable()
                            ui.label("Provider: Ollama（予定）").classes("text-xs text-grey-7")
                            inactive_model_select = ui.select(
                                options=installed_magi_models,
                                value=None,
                                label=f"{inactive_member} / model",
                            ).classes("w-full")
                            inactive_model_select.disable()
                protocol_timeout_select = ui.select(
                    options=list(CORE_ADVISOR_TIMEOUT_OPTIONS),
                    value=int(state.get("advisor_timeout") or 60),
                    label="Timeout (秒)［MELCHIORのみ］",
                ).classes("min-w-40")

                @ui.refreshable
                def protocol_result_panel():
                    result=state.get("protocol_result")
                    if state.get("protocol_busy"):
                        ui.label("RITSUKOがMAGI依頼を実行中...").classes(
                            "text-indigo-800 font-bold"
                        )
                        return
                    if not result:
                        ui.label("まだ実行していません。Cycle 1のLLM応答だけを観測します。").classes(
                            "text-sm text-grey-7"
                        )
                        return
                    status=result.get("status")
                    color="green" if status=="ok" else ("orange" if status=="invalid" else "red")
                    ui.badge("Protocol v1: " + str(status), color=color)
                    export_text=json.dumps(
                        protocol_probe_export(result),ensure_ascii=False,indent=2,default=str
                    )
                    ui.button(
                        "試験結果を一括コピー",icon="content_copy",
                        on_click=lambda value=export_text: copy_protocol_json(value,"試験結果"),
                    ).props("outline dense").classes("self-start")
                    assignment=result.get("assignment") or {}
                    ui.label(
                        "member=" + str(assignment.get("member") or "-")
                        + " / provider=" + str(assignment.get("provider") or "-")
                        + " / model=" + str(assignment.get("model") or "-")
                    ).classes("font-mono text-sm")
                    ui.label(
                        "legacy_router_used=" + str(result.get("legacy_router_used"))
                        + " / pkb_read_executed=" + str(result.get("pkb_read_executed"))
                    ).classes("font-mono text-xs text-grey-7")
                    errors=result.get("validation_errors") or []
                    if errors:
                        ui.label("Schema / 通信エラー: " + " | ".join(errors)).classes("text-red-700")
                    diagnostic=result.get("diagnostic") or {}
                    if diagnostic:
                        ui.label("LLM応答診断（返答本文・Thinking本文は非表示）").classes(
                            "font-bold text-sm"
                        )
                        diagnostic_text=json.dumps({
                            "status":status,
                            "assignment":assignment,
                            "validation_errors":errors,
                            "diagnostic":diagnostic,
                        },ensure_ascii=False,indent=2)
                        ui.button(
                            "診断情報をコピー",icon="content_copy",
                            on_click=lambda value=diagnostic_text: copy_protocol_json(value,"診断情報"),
                        ).props("outline dense")
                        ui.code(diagnostic_text,language="json").classes("w-full")
                    if result.get("response") is not None:
                        ui.label("MAGI analysis_result").classes("font-bold")
                        response_text=json.dumps(result["response"],ensure_ascii=False,indent=2)
                        ui.button(
                            "analysis_resultをコピー",icon="content_copy",
                            on_click=lambda value=response_text: copy_protocol_json(value,"analysis_result"),
                        ).props("outline dense")
                        ui.code(response_text,language="json").classes("w-full")
                    with ui.expansion("RITSUKOが作成した依頼Envelope",icon="data_object").classes(
                        "w-full border"
                    ):
                        envelope_text=json.dumps(
                            result.get("request_envelope") or {},ensure_ascii=False,indent=2
                        )
                        ui.button(
                            "Envelopeをコピー",icon="content_copy",
                            on_click=lambda value=envelope_text: copy_protocol_json(value,"Envelope"),
                        ).props("outline dense")
                        ui.code(envelope_text,language="json").classes("w-full")

                async def submit_protocol_v1():
                    if state.get("protocol_busy"):
                        return
                    selected_model=str(protocol_model_select.value or "").strip()
                    if not selected_model:
                        ui.notify("Ollama chat modelを選択してください",type="negative")
                        return
                    state["protocol_busy"]=True
                    state["protocol_result"]=None
                    protocol_run_button.disable()
                    protocol_result_panel.refresh()
                    try:
                        state["protocol_result"]=await run.io_bound(
                            run_ritsuko_magi_cycle1_probe,
                            protocol_input.value or "",
                            model=selected_model,
                            timeout=float(protocol_timeout_select.value or 60),
                        )
                    except Exception as exc:
                        state["protocol_result"]={
                            "status":"error","response":None,
                            "validation_errors":[type(exc).__name__ + ": " + str(exc)[:200]],
                            "legacy_router_used":False,"pkb_read_executed":False,
                        }
                    finally:
                        state["protocol_busy"]=False
                        protocol_run_button.enable()
                        protocol_result_panel.refresh()

                protocol_run_button=ui.button(
                    "MELCHIORへ分析依頼",icon="psychology",color="indigo",
                    on_click=submit_protocol_v1,
                )
                protocol_result_panel()

        ui.separator()
        with ui.expansion(
            "旧MAGI v0・現行経路（回帰用）",
            value=False,
            icon="history",
        ).classes("w-full border"):
            ui.label(
                "旧経路のAdvisor設定・OODA・Task操作・フロー図です。"
                "Protocol v1のCycle 1試験とは独立しています。"
            ).classes("text-sm text-grey-7")

            try:
                installed_advisor_models = list_advisor_models()
            except Exception:
                installed_advisor_models = []

            saved_advisor_model = state.get("advisor_model")
            if saved_advisor_model not in installed_advisor_models:
                try:
                    saved_advisor_model = choose_advisor_model(installed_advisor_models)
                except Exception:
                    saved_advisor_model = None
                state["advisor_model"] = saved_advisor_model

            with ui.row().classes("w-full items-end gap-2 flex-wrap"):
                advisor_model_select = ui.select(
                    options=installed_advisor_models,
                    value=saved_advisor_model,
                    label="Legacy CASPER Advisor Model",
                ).classes("min-w-64")
                advisor_timeout_select = ui.select(
                    options=list(CORE_ADVISOR_TIMEOUT_OPTIONS),
                    value=int(state.get("advisor_timeout") or 60),
                    label="Advisor Timeout (秒)",
                ).classes("min-w-40")
                ui.label(
                    "旧MAGI v0のCASPER Advisor用。Protocol v1 MELCHIORとは別設定です。"
                ).classes("text-xs text-grey-7")

                def save_advisor_model():
                    selected = str(advisor_model_select.value or "").strip() or None
                    if selected and selected not in advisor_model_select.options:
                        ui.notify("インストール済みモデルを選択してください", type="negative")
                        return
                    timeout_seconds = int(advisor_timeout_select.value or 60)
                    if timeout_seconds not in CORE_ADVISOR_TIMEOUT_OPTIONS:
                        ui.notify("Timeoutは一覧から選択してください", type="negative")
                        return
                    saved_model, saved_timeout = _save_core_advisor_settings(
                        selected, timeout_seconds
                    )
                    state["advisor_model"] = saved_model
                    state["advisor_timeout"] = saved_timeout
                    ui.notify(
                        "Advisor設定を保存しました: "
                        + str(state["advisor_model"] or "自動")
                        + f" / {state['advisor_timeout']}秒",
                        type="positive",
                    )

                def refresh_advisor_models():
                    try:
                        available = list_advisor_models()
                    except Exception as exc:
                        ui.notify("Ollamaモデル一覧を取得できません: " + str(exc)[:180], type="negative")
                        return
                    previous = advisor_model_select.value
                    advisor_model_select.options = available
                    if previous in available:
                        advisor_model_select.value = previous
                    else:
                        try:
                            advisor_model_select.value = choose_advisor_model(available)
                        except Exception:
                            advisor_model_select.value = None
                    advisor_model_select.update()
                    ui.notify(f"Chat model {len(available)}件を取得しました", type="positive")

                ui.button(
                    "保存",
                    icon="save",
                    color="indigo",
                    on_click=save_advisor_model,
                ).props("dense")
                ui.button(
                    "モデル一覧更新",
                    icon="refresh",
                    on_click=refresh_advisor_models,
                ).props("flat dense")

            @ui.refreshable
            def ooda_bar():
                display = current_ooda()
                with ui.column().classes("w-full gap-1").props('role=status aria-live=polite'):
                    with ui.row().classes("w-full items-center gap-2 flex-wrap"):
                        ui.label("OODA").classes("font-bold")
                        for index, (key, name, note) in enumerate(OODA_PHASES):
                            if index:
                                ui.label("→").classes("text-grey-6").props('aria-hidden=true')
                            active = display.phase == key and not display.terminal
                            label = f"{name}（{note}）" + (" · 現在" if active else "")
                            badge = ui.badge(label, color="blue" if active else "grey-3",
                                             text_color="white" if active else "grey-8")
                            if active:
                                badge.classes("font-bold").props('aria-current=step')
                    if display.terminal:
                        ui.label(f"最終状態: {display.terminal}（OODA外）").classes("font-bold")
                    elif display.phase is None:
                        ui.label(display.reason).classes("text-sm text-grey-7")
                    ui.label("既存状態からの表示用推定（途中段階のライブ配信ではありません）").classes(
                        "text-xs text-grey-6"
                    )

            ooda_bar()
            ui.label("最小縦断: 依頼 → Task → 能力選択 → 読取 → Result / 追加質問").classes(
                "text-sm text-grey-7"
            )
            ui.label(
                "PKB・家計・明示的なWeb調査を読み取り専用で扱います。"
                "曖昧依頼からの自動実行は限定PKB readのみです。"
            ).classes("text-sm text-orange-700")

            def select_saved_task(item: dict):
                if state["busy"] or state["resume_busy"]:
                    return
                state["result"] = core_task_selection_result(item)
                load_current_trace()
                saved_task = (state.get("trace") or {}).get("task") or {}
                if saved_task.get("core_slice") == "ritsuko_magi_observation_v1":
                    saved_session = saved_task.get("magi_session")
                    if isinstance(saved_session, dict):
                        state["guided_session"] = saved_session
                        state["guided_history_read_only"] = (
                            saved_task.get("status") == "completed"
                        )
                    else:
                        state["guided_session"] = None
                        state["guided_history_read_only"] = False
                else:
                    state["guided_session"] = None
                    state["guided_history_read_only"] = False
                ooda_bar.refresh()
                core_result.refresh()
                resume_panel.refresh()
                trace_panel.refresh()
                guided_result_panel.refresh()

            @ui.refreshable
            def completed_tasks_panel():
                try:
                    rows, has_more = load_core_task_window(load_completed_core_tasks, list_limits["completed_limit"])
                except Exception as exc:
                    with ui.card().classes("w-full border border-red-200 bg-red-50"):
                        ui.label("完了済みTaskを取得できません: " + str(exc)).classes(
                            "text-red-700"
                        )
                    return

                ui.separator()
                ui.label("完了済み").classes("text-base font-bold")
                ui.label(
                    "直近の完了Taskを閲覧専用で開けます。再開・再実行は行いません。"
                ).classes("text-xs text-grey-7")
                if not rows:
                    ui.label("完了済みTaskはありません。").classes("text-sm text-grey-7")
                    return
                for item in rows:
                    with ui.card().classes("w-full p-2 gap-1 bg-green-50"):
                        with ui.row().classes("w-full items-center gap-2 no-wrap"):
                            ui.badge("completed", color="green").classes("shrink-0")
                            ui.label(item["request"]).classes(
                                "font-medium text-sm grow overflow-hidden"
                            )
                        ui.label(
                            f"rev={item['revision']} / {item.get('phase') or '-'} / "
                            f"{item.get('selected_capability') or '-'}"
                        ).classes("font-mono text-xs text-grey-6")
                        ui.label(
                            f"Action={item['action_count']} / Result={item['result_count']}"
                        ).classes("text-xs text-grey-6")
                        ui.button(
                            "開く",
                            icon="visibility",
                            color="green",
                            on_click=lambda item=item: select_saved_task(item),
                        ).props("flat dense").classes("self-start")

                if has_more:
                    ui.button("さらに読み込む", on_click=lambda: more_tasks("completed_limit", completed_tasks_panel)).props("flat dense")

            @ui.refreshable
            def open_tasks_panel():
                try:
                    rows, has_more = load_core_task_window(load_open_core_tasks, list_limits["open_limit"])
                except Exception as exc:
                    with ui.card().classes("w-full border border-red-200 bg-red-50"):
                        ui.label("未完了Taskを取得できません: " + str(exc)).classes(
                            "text-red-700"
                        )
                    return

                ui.label("進行中・確認待ち").classes("text-base font-bold")
                ui.label(
                    "未完了Taskを選ぶと中央に開きます。F5・サーバー再起動後もDBから復元します。"
                ).classes("text-xs text-grey-7")
                if not rows:
                    ui.label("未完了Taskはありません。").classes("text-sm text-grey-7")
                    return
                for item in rows:
                    status_color = {
                        "waiting_external": "orange",
                        "running": "blue",
                        "paused": "grey",
                    }.get(item["status"], "grey")
                    with ui.card().classes("w-full p-2 gap-1"):
                        with ui.row().classes("w-full items-center gap-2 no-wrap"):
                            ui.badge(item["status"], color=status_color).classes("shrink-0")
                            ui.label(item["request"]).classes(
                                "font-medium text-sm grow overflow-hidden"
                            )
                        ui.label(
                            f"rev={item['revision']} / {item.get('phase') or '-'}"
                        ).classes("font-mono text-xs text-grey-6")
                        ui.button(
                            "開く",
                            icon="open_in_new",
                            color="orange",
                            on_click=lambda item=item: select_saved_task(item),
                        ).props("flat dense").classes("self-start")

                if has_more:
                    ui.button("さらに読み込む", on_click=lambda: more_tasks("open_limit", open_tasks_panel)).props("flat dense")

            def sync_task_preferences():
                changed = False
                for key in list_defaults:
                    value = _UI_PREFERENCES["core"][key]
                    if value != list_defaults[key]:
                        list_defaults[key] = value
                        list_limits[key] = value
                        changed = True
                if changed:
                    open_tasks_panel.refresh()
                    completed_tasks_panel.refresh()

            @ui.refreshable
            def screen_log_panel():
                try:
                    rows = load_recent_core_tasks(10)
                except Exception as exc:
                    with ui.expansion(
                        "Core画面 全体稼働ログ",
                        value=_CORE_UI_OPEN["screen_log"],
                        on_value_change=remember_core_expansion("screen_log"),
                    ).classes(
                        "w-full border border-red-200 bg-red-50"
                        + _block_visibility_class("core", "screen_log")
                    ):
                        ui.label("最近のTaskを取得できません: " + str(exc)).classes(
                            "text-red-700"
                        )
                    return

                with ui.expansion(
                    "Core画面 全体稼働ログ",
                    value=_CORE_UI_OPEN["screen_log"],
                    on_value_change=remember_core_expansion("screen_log"),
                ).classes(
                    "w-full border-2 border-blue-grey-200 bg-blue-grey-1"
                    + _block_visibility_class("core", "screen_log")
                ):
                    ui.label(
                        "この画面で扱った直近のCore Taskを横断表示します。"
                        " 詳細なAction / Result / Sourceは各Taskのログで確認します。"
                    ).classes("text-sm text-grey-7")
                    if not rows:
                        ui.label("Core Taskはまだありません。")
                        return
                    for item in rows:
                        with ui.row().classes(
                            "w-full items-start gap-3 border-b border-blue-grey-100 py-2"
                        ):
                            status_color = {
                                "completed": "green",
                                "waiting_external": "orange",
                                "running": "blue",
                                "paused": "grey",
                                "failed": "red",
                            }.get(item["status"], "grey")
                            ui.badge(item["status"], color=status_color)
                            with ui.column().classes("grow gap-0"):
                                ui.label(item["request"]).classes("font-medium")
                                ui.label(
                                    f"Task {item['id']} / revision={item['revision']} / "
                                    f"phase={item.get('phase') or '-'} / "
                                    f"capability={item.get('selected_capability') or '-'}"
                                ).classes("font-mono text-xs text-grey-7")
                                ui.label(
                                    f"Action={item['action_count']} / Result={item['result_count']} / "
                                    f"updated={item['updated_at']}"
                                ).classes("text-xs text-grey-7")

            with task_drawer:
                with ui.column().classes("w-full gap-2 no-wrap"):
                    ui.label("既存Task").classes("text-lg font-bold shrink-0")
                    ui.label(
                        "RITSUKOのTaskは経路に関係なくここから確認できます。"
                    ).classes("text-xs text-grey-7")
                    ui.link("Task履歴を見る", "/core/history").classes("shrink-0")
                    with ui.column().classes("w-full no-wrap"):
                        open_tasks_panel()
                    with ui.column().classes("w-full no-wrap"):
                        completed_tasks_panel()
                ui.timer(1.0, sync_task_preferences)

            with ui.card().classes("w-full border-2 border-blue-grey-300 bg-blue-grey-1"):
                ui.label("依頼").classes("text-lg font-bold")
                ui.label(
                    "例: メインPCのGPUの現在のドライバーを調べて / "
                    "メインPCの構成を確認して / GPU1のドライバー更新履歴を見て"
                ).classes("text-sm")
                request_input = ui.textarea(
                    label="Secretary Coreへ依頼",
                    placeholder="対象と確認したい内容を自然言語で入力",
                ).classes("w-full")

                @ui.refreshable
                def core_result():
                    result = state["result"]
                    if result or state["busy"] or state["resume_busy"]:
                        display = current_ooda()
                        suffix = "（完了直前の表示用段階）" if display.terminal == "completed" else ""
                        ui.label(f"OODA: {display.label}{suffix}").classes("font-medium")
                        ui.label("理由: " + display.reason).classes("text-sm")
                    advisor = current_advisor_shadow()
                    if advisor:
                        with ui.card().classes(
                            "w-full border border-indigo-200 bg-indigo-50"
                        ):
                            ui.label("MAGI v0 · Cooperative Synthesis").classes(
                                "font-bold text-indigo-900"
                            )
                            ui.label(
                                "MELCHIORのGuardとCASPERの前進案をCoreが統合します。"
                                "曖昧依頼では、Synthesisが選んだ限定的なPKB readだけを自動実行できます。"
                            ).classes("text-xs text-grey-7")
                            presentation = core_magi_presentation(result or {}, advisor, state.get("trace") or {})
                            for cycle in presentation["cycles"]:
                                with ui.card().classes("w-full bg-white gap-1"):
                                    ui.label(f"Cycle {cycle['cycle']}").classes("font-bold")
                                    for key, label in (("melchior", "MELCHIOR"), ("casper", "CASPER"),
                                                       ("synthesis", "Synthesis（中間提案）"),
                                                       ("action", "Action"), ("result", "Result")):
                                        if cycle.get(key) is not None:
                                            note = "（初回Guardを再利用）" if key == "melchior" and cycle.get("melchior_reused") else ""
                                            ui.label(label + note).classes("font-medium text-sm")
                                            entry = cycle[key]
                                            if key == "result":
                                                summary = f"result_count={entry.get('result_count', '-')} / {entry.get('answer') or entry.get('result_id') or '-'}"
                                            else:
                                                summary = " / ".join(str(entry[field]) for field in
                                                    ("status", "next_step", "proposed_action", "selected_capability", "capability", "reason")
                                                    if entry.get(field) is not None) or "未記録"
                                            ui.label(summary).classes("text-sm break-words")
                                    with ui.expansion("Cycle詳細").classes("w-full"):
                                        ui.code(json.dumps(cycle, ensure_ascii=False, indent=2, default=str), language="json").classes("w-full text-xs")
                            with ui.card().classes("w-full border-2 border-green-600 bg-green-50"):
                                ui.label("FINAL CORE DECISION").classes("font-bold")
                                final = presentation["final_core_decision"]
                                for key in ("next_step", "reason", "task_status"):
                                    ui.label(f"{key} = {final.get(key) if final and final.get(key) is not None else '未記録'}").classes("font-mono text-sm")
                                if not final:
                                    ui.label("最終判断は未記録です。Synthesisからは補完しません。").classes("text-xs")
                            job_status = str(
                                advisor.get("job_status") or advisor.get("status") or "-"
                            )
                            elapsed = advisor.get("elapsed_seconds")
                            if job_status in {"queued", "running"} and advisor.get("started_at"):
                                try:
                                    started = datetime.fromisoformat(str(advisor["started_at"]))
                                    elapsed = max(
                                        0.0,
                                        (datetime.now(timezone.utc) - started).total_seconds(),
                                    )
                                except ValueError:
                                    pass
                            ui.label(
                                "model="
                                + str(advisor.get("model") or "-")
                                + " / timeout="
                                + str(advisor.get("timeout_seconds") or "-")
                                + "s / job="
                                + job_status
                                + " / status="
                                + str(advisor.get("status") or "-")
                                + " / elapsed="
                                + (f"{float(elapsed):.1f}s" if elapsed is not None else "-")
                            ).classes("font-mono text-xs text-grey-7")
                            if job_status in {"queued", "running"}:
                                with ui.row().classes("items-center gap-2"):
                                    ui.spinner(size="sm", color="indigo")
                                    ui.label(
                                        "Advisorはバックグラウンド評価中です。"
                                        " Cycle単位の提案と最終Core判断を下に表示します。"
                                    ).classes("text-xs text-indigo-800")
                            if advisor.get("situation"):
                                ui.label("状況整理: " + str(advisor["situation"])).classes(
                                    "text-sm"
                                )
                            if advisor.get("next_step"):
                                ui.label(
                                    "CASPER提案（最終判断ではありません）: " + str(advisor["next_step"])
                                ).classes("text-sm font-medium text-indigo-900")
                            if advisor.get("reason"):
                                ui.label("提案理由: " + str(advisor["reason"])).classes(
                                    "text-sm"
                                )
                            missing = advisor.get("missing_information") or []
                            if missing:
                                ui.label(
                                    "不足情報: " + " / ".join(str(x) for x in missing)
                                ).classes("text-xs text-orange-800")
                            if advisor.get("error"):
                                ui.label(
                                    "Advisor error: " + str(advisor["error"])
                                ).classes("text-xs text-red-700")

                            with ui.expansion(
                                "Advisor 稼働ログ",
                                value=bool(advisor.get("error")),
                            ).classes("w-full border border-indigo-100 bg-white"):
                                ui.label(
                                    "Cycleごとの提案・Action / Result・最終Core判断を確認するログです。"
                                    " 推論過程は保存・表示しません。"
                                ).classes("text-xs text-grey-7")

                                trace = state.get("trace") or {}
                                export_text = json.dumps(
                                    _advisor_log_export(result, advisor, trace),
                                    ensure_ascii=False,
                                    indent=2,
                                    default=str,
                                )

                                def copy_advisor_log(text: str = export_text) -> None:
                                    ui.run_javascript(
                                        'navigator.clipboard.writeText('
                                        + json.dumps(text, ensure_ascii=False)
                                        + ')'
                                    )
                                    ui.notify("Advisor稼働ログをコピーしました", type="positive")

                                ui.button(
                                    "ログをコピー",
                                    icon="content_copy",
                                    on_click=copy_advisor_log,
                                ).props("outline dense").classes("self-start")

                                lifecycle = []
                                for event in trace.get("advisor_events") or []:
                                    lifecycle.append(
                                        str(event.get("occurred_at") or "")
                                        + "  "
                                        + str(event.get("event_type") or "")
                                    )
                                if lifecycle:
                                    ui.label("状態遷移").classes("font-medium text-sm")
                                    for line in lifecycle:
                                        ui.label(line).classes("font-mono text-xs")
                                else:
                                    ui.label(
                                        "状態遷移: DB監査イベントはまだありません。"
                                    ).classes("text-xs text-grey-6")

                                ui.label("検査結果").classes("font-medium text-sm")
                                ui.label(
                                    "job="
                                    + str(advisor.get("job_status") or "-")
                                    + " / status="
                                    + str(advisor.get("status") or "-")
                                    + " / comparison="
                                    + str(advisor.get("comparison") or "-")
                                    + " / error="
                                    + str(advisor.get("error") or "-")
                                ).classes("font-mono text-xs")

                                trace_task = (state.get("trace") or {}).get("task") or {}
                                observation_pack = (
                                    trace_task.get("observation_pack")
                                    or result.get("observation_pack")
                                )
                                if observation_pack:
                                    ui.label("MELCHIOR / CASPER 共通 Observation Pack").classes(
                                        "font-medium text-sm"
                                    )
                                    ui.code(
                                        json.dumps(
                                            observation_pack,
                                            ensure_ascii=False,
                                            indent=2,
                                            default=str,
                                        ),
                                        language="json",
                                    ).classes("w-full text-xs")

                                request_context = advisor.get("request_context")
                                if request_context:
                                    ui.label("Ollamaへ渡した判断コンテキスト").classes(
                                        "font-medium text-sm"
                                    )
                                    ui.code(
                                        json.dumps(
                                            request_context,
                                            ensure_ascii=False,
                                            indent=2,
                                            default=str,
                                        ),
                                        language="json",
                                    ).classes("w-full text-xs")

                                response_diagnostic = advisor.get("response_diagnostic")
                                if response_diagnostic:
                                    ui.label(
                                        "LLM返却値の形式検査（安全化済み）"
                                    ).classes("font-medium text-sm")
                                    ui.label(
                                        "契約対象フィールドだけを保存しています。"
                                        " 追加キーは名前だけ記録し、値は保存しません。"
                                    ).classes("text-xs text-grey-7")
                                    ui.code(
                                        json.dumps(
                                            response_diagnostic,
                                            ensure_ascii=False,
                                            indent=2,
                                            default=str,
                                        ),
                                        language="json",
                                    ).classes("w-full text-xs")

                    if state["busy"]:
                        with ui.row().classes("items-center gap-2"):
                            ui.spinner(size="sm", color="blue-grey")
                            ui.label("Taskを作成してPKBを確認しています…")
                        return
                    if not result:
                        ui.label("まだ依頼していません。")
                        return

                    status = result.get("status", "")
                    color = {
                        "completed": "green",
                        "waiting_external": "orange",
                        "rejected": "red",
                    }.get(status, "grey")
                    ui.badge(status or "result", color=color)
                    if result.get("selected_capability"):
                        ui.label(
                            "選択した能力: " + result["selected_capability"]
                        ).classes("text-sm text-blue-grey-800")
                    if result.get("message"):
                        ui.label(result["message"]).classes("text-base")
                    if result.get("question"):
                        with ui.card().classes(
                            "w-full border-2 border-orange-300 bg-orange-50"
                        ):
                            ui.label("追加確認").classes("font-bold text-orange-900")
                            ui.label(result["question"])
                    if result.get("task_id"):
                        ui.label(
                            "Task: " + result["task_id"]
                        ).classes("font-mono text-xs text-grey-6")

                    comparison = result.get("comparison") or {}
                    if comparison:
                        with ui.card().classes("w-full border border-purple-200 bg-purple-50"):
                            ui.label("PKB＋Web 比較").classes("font-bold text-purple-900")
                            ui.label(
                                "status="
                                + str(comparison.get("status") or "-")
                                + " / current="
                                + str(comparison.get("current") or "-")
                                + " / latest="
                                + str(comparison.get("latest") or "-")
                                + " / kind="
                                + str(comparison.get("latest_kind") or "-")
                            ).classes("font-mono text-xs")
                            if comparison.get("web_query"):
                                ui.label(
                                    "Web検索語: " + str(comparison.get("web_query"))
                                ).classes("text-xs text-grey-7")
                            ui.label(
                                comparison.get("message") or "比較結果はありません。"
                            ).classes("text-sm")

                    web_result = result.get("web") or {}
                    if web_result:
                        with ui.expansion("根拠になったWeb調査", value=True).classes(
                            "w-full border border-cyan-200 bg-white"
                        ):
                            ui.label(
                                f"Provider: {web_result.get('provider') or '-'} / "
                                f"Query: {web_result.get('query') or '-'}"
                            ).classes("text-xs text-grey-7")
                            fact_summary = web_result.get("fact_summary") or {}
                            if fact_summary.get("kind") == "driver_version":
                                with ui.card().classes("w-full border border-indigo-200 bg-indigo-50"):
                                    ui.label("抽出した事実候補").classes("font-bold text-indigo-900")
                                    ui.label(
                                        "preferred_kind="
                                        + str(fact_summary.get("preferred_kind") or "-")
                                        + " / primary_domains="
                                        + ",".join(fact_summary.get("primary_domains") or [])
                                        + " / status="
                                        + str(fact_summary.get("status") or "-")
                                        + " / best="
                                        + str(fact_summary.get("best_candidate") or "-")
                                    ).classes("font-mono text-xs")
                                    for group in (fact_summary.get("groups") or [])[:5]:
                                        ui.label(
                                            f"{group.get('kind')} / "
                                            f"status={group.get('status')} / "
                                            f"best={group.get('best_candidate')}"
                                        ).classes("font-medium text-xs text-indigo-900")
                                        for candidate in (group.get("candidates") or [])[:5]:
                                            ui.label(
                                                f"  {candidate.get('value')} / "
                                                f"sources={candidate.get('source_count', 0)} / "
                                                f"domains={candidate.get('domain_count', 0)} / "
                                                f"primary={candidate.get('primary_source_count', 0)} / "
                                                f"date={candidate.get('latest_date') or '-'} / "
                                                f"context={candidate.get('best_context_score', 0)} / "
                                                f"best_quality={candidate.get('best_quality_score', 0)}"
                                            ).classes("text-xs")
                                    historical = fact_summary.get("historical_groups") or []
                                    if historical:
                                        ui.label("過去版候補").classes("font-bold text-xs text-grey-7")
                                        for group in historical[:5]:
                                            ui.label(
                                                f"{group.get('kind')} / best={group.get('best_candidate')}"
                                            ).classes("text-xs text-grey-7")
                            for hit in (web_result.get("hits") or [])[:5]:
                                with ui.card().classes("w-full p-2 gap-1"):
                                    ui.label(
                                        f"{hit.get('rank')}. {hit.get('title') or hit.get('url') or '検索結果'}"
                                    ).classes("font-medium text-sm")
                                    if hit.get("url"):
                                        ui.link(
                                            hit["url"],
                                            hit["url"],
                                            new_tab=True,
                                        ).classes("text-xs")
                                    if hit.get("snippet"):
                                        ui.label(hit["snippet"]).classes("text-xs text-grey-8")
                                    ui.label(
                                        "evidence_rank="
                                        + str(hit.get("evidence_rank") or "-")
                                        + " / quality="
                                        + str(hit.get("quality_score") or 0)
                                        + " / "
                                        + str(hit.get("authority_hint") or "-")
                                        + " / authority="
                                        + str(hit.get("authority_level") or "-")
                                    ).classes("font-mono text-xs text-grey-6")
                                    ui.label(
                                        "fetch=" + str(hit.get("fetch_status") or "unknown")
                                    ).classes("font-mono text-xs text-grey-6")
                                    if hit.get("version_facts"):
                                        ui.label(
                                            "version候補: "
                                            + ", ".join(
                                                f"{fact.get('kind')}={fact.get('value')}"
                                                + (
                                                    f"@{fact.get('date_hint')}"
                                                    if fact.get("date_hint")
                                                    else ""
                                                )
                                                for fact in (hit.get("version_facts") or [])
                                            )
                                        ).classes("text-xs text-indigo-8")
                                    elif hit.get("version_candidates"):
                                        ui.label(
                                            "version候補: "
                                            + ", ".join(hit.get("version_candidates") or [])
                                        ).classes("text-xs text-indigo-8")
                                    if hit.get("date_hints"):
                                        ui.label(
                                            "日付候補: " + ", ".join(hit.get("date_hints") or [])
                                        ).classes("text-xs text-grey-7")

                    finance_result = result.get("finance") or {}
                    if finance_result:
                        with ui.expansion("根拠になった家計集計", value=True).classes(
                            "w-full border border-teal-200 bg-white"
                        ):
                            ui.label(
                                f"検索期間: {finance_result.get('requested_start_date') or '-'}"
                                f" 〜 {finance_result.get('requested_end_date') or '-'}"
                            ).classes("text-sm")
                            ui.label(
                                f"明細存在期間: {finance_result.get('data_start_date') or '-'}"
                                f" 〜 {finance_result.get('data_end_date') or '-'}"
                            ).classes("text-xs text-grey-7")
                            ui.label(
                                f"明細 {finance_result.get('transaction_count', 0)}件 / "
                                f"収入 ¥{int(finance_result.get('income_total') or 0):,} / "
                                f"支出 ¥{int(finance_result.get('expense_total') or 0):,} / "
                                f"収支 ¥{int(finance_result.get('net_total') or 0):,}"
                            ).classes("text-sm")

                    search_result = result.get("search") or {}
                    rows = search_result.get("items") or []
                    if rows:
                        with ui.expansion("根拠になったPKB記録", value=True).classes(
                            "w-full border border-blue-grey-200 bg-white"
                        ):
                            if search_result.get("result_kind") == "components":
                                for row in rows:
                                    ui.label(
                                        f"{row.get('component_name')} / "
                                        f"role={row.get('relation_role')} / "
                                        f"current_driver={row.get('current_driver')} / "
                                        f"Source={row.get('state_source_uri')}"
                                    ).classes("text-sm")
                            else:
                                for row in rows:
                                    ui.label(
                                        f"{row.get('entity_name')} / "
                                        f"{row.get('predicate')}={row.get('value')} / "
                                        f"Source={row.get('source_uri')}"
                                    ).classes("text-sm")

                @ui.refreshable
                def trace_panel():
                    result = state["result"] or {}
                    task_id = result.get("task_id")
                    if not task_id:
                        return
                    trace = state["trace"]
                    if not trace:
                        with ui.expansion(
                            "Task検証・稼働ログ",
                            value=_CORE_UI_OPEN["trace"],
                            on_value_change=remember_core_expansion("trace"),
                        ).classes(
                            "w-full border border-red-200 bg-red-50"
                            + _block_visibility_class("core", "trace")
                        ):
                            ui.label("Traceを取得できません: " + str(state["trace_error"] or "未取得")).classes(
                                "text-red-700"
                            )
                        return

                    task = trace["task"]
                    with ui.expansion(
                        "Task検証・稼働ログ",
                        value=_CORE_UI_OPEN["trace"],
                        on_value_change=remember_core_expansion("trace"),
                    ).classes(
                        "w-full border-2 border-slate-300 bg-slate-50"
                        + _block_visibility_class("core", "trace")
                    ):
                        ui.label(
                            "この依頼Taskに属するDB上のAction / Result / Sourceを表示します。"
                        ).classes("text-sm text-grey-7")
                        with ui.grid(columns=2).classes("w-full gap-2"):
                            ui.label("Task ID")
                            ui.label(task["id"]).classes("font-mono text-xs")
                            ui.label("Status / Revision")
                            ui.label(f"{task['status']} / {task['revision']}")
                            ui.label("Phase")
                            ui.label(str(task.get("phase") or "-"))
                            ui.label("能力")
                            ui.label(str(task.get("selected_capability") or "-"))
                            ui.label("元依頼")
                            ui.label(task["request"])
                            if task.get("effective_request"):
                                ui.label("実効依頼")
                                ui.label(task["effective_request"])
                            if task.get("user_replies"):
                                ui.label("追加回答")
                                ui.label(" / ".join(task["user_replies"]))
                            ui.label("更新時刻")
                            ui.label(str(task["updated_at"]))

                        actions = trace["actions"]
                        ui.separator()
                        ui.label(f"Action / Result: {len(actions)}件").classes("font-bold")
                        if not actions:
                            ui.label(
                                "まだActionはありません。追加確認待ちTaskでは正常です。"
                            ).classes("text-sm")
                        for index, item in enumerate(actions, start=1):
                            with ui.card().classes("w-full bg-white"):
                                ui.label(
                                    f"{index}. {item['tool']}.{item['operation']} "
                                    f"[{item['risk']}] → {item['action_status']}"
                                ).classes("font-medium")
                                if item.get("outcome"):
                                    ui.label(
                                        f"Result: {item['outcome']} / "
                                        f"{item.get('summary') or ''}"
                                    ).classes("text-sm")
                                if item.get("source_uri"):
                                    ui.label(
                                        "Source: " + item["source_uri"]
                                    ).classes("font-mono text-xs text-grey-7")
                                if item.get("verified_by"):
                                    ui.label(
                                        "Verified: "
                                        + str(item["verified_by"])
                                        + " / "
                                        + str(item.get("verified_at") or "")
                                    ).classes("text-xs text-grey-7")

                @ui.refreshable
                def resume_panel():
                    result = state["result"] or {}
                    if result.get("status") != "waiting_external" or not result.get("task_id"):
                        return

                    if result.get("core_slice") == "ritsuko_magi_observation_v1":
                        with ui.card().classes(
                            "w-full border-2 border-orange-300 bg-orange-50"
                        ):
                            ui.label("このTaskは待機中です").classes(
                                "font-bold text-orange-900"
                            )
                            ui.label(
                                "上部のRITSUKO ⇄ MAGI Observation Loopから、"
                                "保存済み対話へ追加回答して同じTask IDで再開できます。"
                            ).classes("text-sm")
                        return

                    with ui.card().classes(
                        "w-full border-2 border-orange-300 bg-orange-50"
                    ):
                        ui.label("このTaskへ追加回答").classes(
                            "font-bold text-orange-900"
                        )
                        ui.label(
                            "新しいTaskは作らず、上のTask IDをそのまま再開します。"
                        ).classes("text-sm")
                        reply_input = ui.textarea(
                            label="追加回答",
                            placeholder="例: GPUの現在のドライバーを調べて",
                        ).classes("w-full")

                        async def submit_resume():
                            if state["busy"] or state["resume_busy"]:
                                return
                            state["resume_busy"] = True
                            resume_button.disable()
                            ooda_bar.refresh()
                            core_result.refresh()
                            try:
                                state["result"] = await run.io_bound(
                                    resume_core_task,
                                    UUID(result["task_id"]),
                                    reply_input.value or "",
                                )
                            except Exception as exc:
                                ui.notify(str(exc)[:240], type="negative")
                            finally:
                                state["resume_busy"] = False
                                load_current_trace()
                                ooda_bar.refresh()
                                resume_panel.refresh()
                                core_result.refresh()
                                trace_panel.refresh()
                                open_tasks_panel.refresh()
                                completed_tasks_panel.refresh()
                                screen_log_panel.refresh()

                        resume_button = ui.button(
                            "同じTaskを再開",
                            icon="resume",
                            color="orange",
                            on_click=submit_resume,
                        )

                async def submit_core():
                    if state["busy"] or state["resume_busy"]:
                        return
                    state["busy"] = True
                    state["result"] = None
                    state["trace"] = None
                    run_button.disable()
                    ooda_bar.refresh()
                    core_result.refresh()
                    resume_panel.refresh()
                    trace_panel.refresh()
                    try:
                        state["result"] = await run.io_bound(
                            run_core_request,
                            request_input.value or "",
                            state.get("advisor_model"),
                            float(state.get("advisor_timeout") or 60),
                        )
                    except Exception as exc:
                        state["result"] = {
                            "status": "error",
                            "phase": "failed",
                            "message": str(exc),
                        }
                    finally:
                        state["busy"] = False
                        run_button.enable()
                        load_current_trace()
                        ooda_bar.refresh()
                        core_result.refresh()
                        resume_panel.refresh()
                        trace_panel.refresh()
                        open_tasks_panel.refresh()
                        completed_tasks_panel.refresh()
                        screen_log_panel.refresh()

                run_button = ui.button(
                    "依頼する",
                    icon="play_arrow",
                    color="blue-grey",
                    on_click=submit_core,
                )
                core_result()
                resume_panel()
                trace_panel()
                ui.timer(2.0, poll_advisor_shadow)

            screen_log_panel()

            if task_id:
                try:
                    saved_trace = load_core_task_trace(UUID(task_id))
                    select_saved_task(saved_trace["task"])
                except (ValueError, psycopg.Error) as exc:
                    ui.notify("Taskを開けません: " + str(exc), type="negative")

            with ui.card().classes(
                "w-full" + _block_visibility_class("core", "limits")
            ):
                ui.label("この縦断でまだ行わないこと").classes("font-bold")
                ui.label(
                    "承認付き外部変更、任意Toolからの汎用再計画、条件待ち自動再開は未実装です。"
                    "保存済みTaskは選択して閲覧でき、確認待ちTaskへ追加回答すると同じTaskを再開します。"
                ).classes("text-sm")

            # The legacy MAGI v0 flow is regression context, not the current v1 path.
            with ui.expansion(
                "旧MAGI v0 処理フロー（回帰用）",
                value=False,
                icon="account_tree",
            ).classes("w-full border"):
                ui.label(
                    "Protocol v1とは独立した過去の協調経路です。"
                    "現在のRITSUKO→MAGI通信試験の処理順ではありません。"
                ).classes("text-xs text-grey-7")
                with ui.row().classes("w-full items-center gap-2 flex-wrap"):
                    for index, step in enumerate(CORE_FLOW_STEPS):
                        if index:
                            ui.label("→").props("aria-hidden=true")
                        ui.label(step).classes("border rounded p-2 text-sm")
                ui.label(
                    "曖昧依頼の旧協調経路です。Cycle 1で限定PKB readを行い、"
                    "結果をObservation v2へ戻してCycle 2で再検討します。"
                    "Synthesisは中間提案。Coordinatorが反復・最大2 cycle・外部への拡張を制限します。"
                    "明示的な能力指定と追加回答による再開も現行回帰経路側です。"
                ).classes("text-sm")


@ui.page("/settings")
def settings_page():
    pkb_labels = {
        "write": "記録",
        "correction": "訂正",
        "search": "検索・履歴",
        "entities": "Entity一覧",
        "pending": "確認待ち（Drawer）",
        "reviewed": "処理済みの確認待ち（Drawer）",
    }
    finance_labels = {
        "filter": "家計フィルタ・検索",
        "stored": "保存済み家計",
        "monthly": "保存済み月別集計",
        "categories": "保存済みカテゴリ別支出",
        "details": "保存済み明細",
        "imports": "Import履歴 / Source",
        "csv": "MoneyForward CSV 取込",
    }
    core_labels = {
        "trace": "Task単位の検証・稼働ログ",
        "screen_log": "画面全体の最近のCore稼働ログ",
        "limits": "この縦断でまだ行わないこと",
    }

    with ui.column().classes("w-full max-w-5xl mx-auto gap-4 p-4"):
        _portal_header(
            "設定",
            "接続・外部サービスと日常用GUIの表示・初期状態を変更します。",
        )
        ui.label(
            "接続・外部サービスの設定と、画面レイアウトの設定を分けて管理します。"
        ).classes("text-sm text-grey-7")

        with ui.expansion(
            "接続・外部サービス設定",
            value=True,
            icon="cable",
        ).classes("w-full border-2 border-indigo-300 bg-indigo-50"):
            ui.label(
                "接続先・認証情報と、それを利用する機能の割当を管理します。"
            ).classes("text-sm text-grey-7")
            with ui.expansion(
                "Service Connections",
                value=False,
                icon="hub",
            ).classes("w-full border-2 border-indigo-200 bg-indigo-50"):
                ui.label(
                    "接続先と認証情報を1つのService ConnectionとしてPostgreSQLで共通管理します。"
                    "各機能は使用するconnection_idを明示的に選択します。"
                ).classes("text-sm")
                ui.label(
                    "connection_role / Capabilityは分類・技術情報であり、自動選択やRITSUKOの実行許可には使いません。"
                ).classes("text-xs text-grey-7")
                try:
                    with connection() as db:
                        bootstrap_connection_auth_from_env(db)
                        bootstrap_openai_billing_profile(db)
                        connection_rows = list_service_connections(
                            db,
                            include_disabled=True,
                        )
                except Exception as exc:
                    connection_rows = []
                    ui.label(
                        "Service Connectionを読み込めません: " + str(exc)[:180]
                    ).classes("text-sm text-red-700")

                connection_by_id = {
                    item["id"]: item for item in connection_rows
                }

                if connection_rows:
                    ui.table(
                        columns=[
                            {"name": "display_name", "label": "Connection", "field": "display_name", "align": "left"},
                            {"name": "adapter_key", "label": "Adapter", "field": "adapter_key", "align": "left"},
                            {"name": "connection_type", "label": "Type", "field": "connection_type", "align": "left"},
                            {"name": "endpoint", "label": "Endpoint", "field": "endpoint", "align": "left"},
                            {"name": "auth_text", "label": "Auth", "field": "auth_text", "align": "center"},
                            {"name": "role_text", "label": "Role", "field": "role_text", "align": "left"},
                            {"name": "capabilities_text", "label": "Capabilities", "field": "capabilities_text", "align": "left"},
                            {"name": "enabled_text", "label": "Enabled", "field": "enabled_text", "align": "center"},
                        ],
                        rows=[
                            {
                                **item,
                                "auth_text": "SET" if item.get("auth_configured") else "-",
                                "role_text": item.get("connection_role") or "-",
                                "capabilities_text": ", ".join(item.get("capabilities") or []),
                                "enabled_text": "ON" if item.get("enabled") else "OFF",
                            }
                            for item in connection_rows
                        ],
                        row_key="id",
                    ).classes("w-full")
                else:
                    ui.label("登録済みService Connectionはありません。").classes(
                        "text-sm text-grey-7"
                    )

                connection_edit_target = ui.select(
                    options={
                        item["id"]: item["display_name"]
                        + f" [{item['adapter_key']} / {item['connection_type']}]"
                        for item in connection_rows
                    },
                    value=None,
                    label="既存Connectionを編集（未選択なら新規）",
                ).props("clearable").classes("w-full")

                with ui.row().classes("w-full gap-2 items-end flex-wrap"):
                    connection_name = ui.input(
                        "接続名",
                        placeholder="例: OpenAI 通常API / OpenAI Admin / Ollama SubPC",
                    ).classes("min-w-64 grow")
                    connection_adapter = ui.select(
                        options=list(connection_adapter_keys()),
                        value="openai",
                        label="Adapter",
                    ).classes("min-w-40")
                    connection_type = ui.select(
                        options=list(CONNECTION_TYPES),
                        value="api_key",
                        label="Connection Type",
                    ).classes("min-w-48")
                    connection_enabled = ui.switch("Enabled", value=True)

                with ui.row().classes("w-full gap-2 items-end flex-wrap"):
                    connection_endpoint = ui.input(
                        "Endpoint",
                        placeholder="https://api.openai.com/v1",
                    ).classes("min-w-96 grow")
                    connection_role = ui.input(
                        "Role（任意・分類表示用）",
                        placeholder="llm / service_billing など",
                    ).classes("min-w-56")
                    connection_config = ui.textarea(
                        "Config JSON（非認証の接続固有設定）",
                        value="{}",
                    ).classes("min-w-80 grow")

                with ui.row().classes("w-full gap-4 items-center flex-wrap"):
                    connection_cap_llm = ui.checkbox(
                        "LLM inference",
                        value=True,
                    )
                    connection_cap_billing = ui.checkbox(
                        "Service billing read",
                        value=False,
                    )

                auth_api_key = ui.input(
                    "API Key（登録済みの場合は空欄で維持）"
                ).props("type=password autocomplete=new-password").classes("w-full")
                with ui.row().classes("w-full gap-2 flex-wrap"):
                    auth_username = ui.input(
                        "Login ID / Username（空欄で維持）"
                    ).classes("min-w-64 grow")
                    auth_password = ui.input(
                        "Password（空欄で維持）"
                    ).props("type=password autocomplete=new-password").classes("min-w-64 grow")
                with ui.row().classes("w-full gap-2 flex-wrap"):
                    auth_client_id = ui.input(
                        "OAuth Client ID（空欄で維持）"
                    ).classes("min-w-64 grow")
                    auth_client_secret = ui.input(
                        "OAuth Client Secret（空欄で維持）"
                    ).props("type=password autocomplete=new-password").classes("min-w-64 grow")
                    auth_refresh_token = ui.input(
                        "OAuth Refresh Token（空欄で維持）"
                    ).props("type=password autocomplete=new-password").classes("min-w-64 grow")

                def update_auth_field_visibility(_event=None):
                    kind = str(connection_type.value or "")
                    auth_api_key.set_visibility(kind == "api_key")
                    auth_username.set_visibility(kind == "username_password")
                    auth_password.set_visibility(kind == "username_password")
                    auth_client_id.set_visibility(kind == "oauth2")
                    auth_client_secret.set_visibility(kind == "oauth2")
                    auth_refresh_token.set_visibility(kind == "oauth2")

                def fill_connection_defaults(_event=None):
                    try:
                        defaults = connection_adapter_defaults(
                            str(connection_adapter.value or "")
                        )
                    except Exception:
                        return
                    connection_endpoint.value = defaults["endpoint"]
                    connection_type.value = defaults["connection_type"]
                    connection_config.value = json.dumps(
                        defaults.get("config_data") or {},
                        ensure_ascii=False,
                        indent=2,
                    )
                    default_caps = set(defaults["capabilities"])
                    connection_cap_llm.value = LLM_INFERENCE in default_caps
                    connection_cap_billing.value = SERVICE_BILLING_READ in default_caps
                    for control in (
                        connection_endpoint,
                        connection_type,
                        connection_config,
                        connection_cap_llm,
                        connection_cap_billing,
                    ):
                        control.update()
                    update_auth_field_visibility()

                def apply_selected_connection(_event=None):
                    connection_id = str(connection_edit_target.value or "").strip()
                    item = connection_by_id.get(connection_id)
                    if item is None:
                        return
                    connection_name.value = item["display_name"]
                    connection_adapter.value = item["adapter_key"]
                    connection_type.value = item["connection_type"]
                    connection_endpoint.value = item["endpoint"]
                    connection_role.value = item.get("connection_role") or ""
                    connection_enabled.value = bool(item.get("enabled"))
                    connection_config.value = json.dumps(
                        item.get("config_data") or {},
                        ensure_ascii=False,
                        indent=2,
                    )
                    caps = set(item.get("capabilities") or [])
                    connection_cap_llm.value = LLM_INFERENCE in caps
                    connection_cap_billing.value = SERVICE_BILLING_READ in caps
                    auth_api_key.value = ""
                    auth_username.value = ""
                    auth_password.value = ""
                    auth_client_id.value = ""
                    auth_client_secret.value = ""
                    auth_refresh_token.value = ""
                    for control in (
                        connection_name, connection_adapter, connection_type,
                        connection_endpoint, connection_role, connection_enabled,
                        connection_config, connection_cap_llm, connection_cap_billing,
                        auth_api_key, auth_username, auth_password,
                        auth_client_id, auth_client_secret, auth_refresh_token,
                    ):
                        control.update()
                    update_auth_field_visibility()

                connection_type.on_value_change(update_auth_field_visibility)
                connection_adapter.on_value_change(fill_connection_defaults)
                connection_edit_target.on_value_change(apply_selected_connection)
                fill_connection_defaults()

                def save_service_connection_from_ui():
                    try:
                        config_value = json.loads(
                            str(connection_config.value or "{}")
                        )
                        if not isinstance(config_value, dict):
                            raise ValueError("Config JSONはobjectにしてください")
                        kind = str(connection_type.value or "")
                        editing_id = str(connection_edit_target.value or "").strip() or None
                        existing = connection_by_id.get(editing_id or "")
                        auth_data = None
                        if kind == "none":
                            auth_data = {}
                        elif kind == "api_key":
                            secret = str(auth_api_key.value or "").strip()
                            if secret:
                                auth_data = {"api_key": secret}
                            elif existing is None:
                                auth_data = {}
                        elif kind == "username_password":
                            username = str(auth_username.value or "").strip()
                            password_value = str(auth_password.value or "").strip()
                            if username or password_value:
                                if not username or not password_value:
                                    raise ValueError("IDとPasswordは両方入力してください")
                                auth_data = {
                                    "username": username,
                                    "password": password_value,
                                }
                            elif existing is None:
                                auth_data = {}
                        elif kind == "oauth2":
                            client_id = str(auth_client_id.value or "").strip()
                            client_secret = str(auth_client_secret.value or "").strip()
                            refresh_token = str(auth_refresh_token.value or "").strip()
                            if client_id or client_secret or refresh_token:
                                if not client_id:
                                    raise ValueError("OAuth Client IDが必要です")
                                auth_data = {
                                    "client_id": client_id,
                                    "client_secret": client_secret,
                                    "refresh_token": refresh_token,
                                }
                            elif existing is None:
                                auth_data = {}

                        capabilities = []
                        if connection_cap_llm.value:
                            capabilities.append(LLM_INFERENCE)
                        if connection_cap_billing.value:
                            capabilities.append(SERVICE_BILLING_READ)

                        with connection() as db:
                            saved = upsert_service_connection(
                                db,
                                adapter_key=str(connection_adapter.value or ""),
                                display_name=str(connection_name.value or "").strip(),
                                endpoint=str(connection_endpoint.value or "").strip(),
                                credential_ref="",
                                capabilities=capabilities,
                                config_data=config_value,
                                auth_data=auth_data,
                                connection_type=kind,
                                connection_role=(
                                    str(connection_role.value or "").strip() or None
                                ),
                                enabled=bool(connection_enabled.value),
                                connection_id=editing_id,
                            )
                        connection_by_id[saved["id"]] = saved
                        connection_edit_target.options[saved["id"]] = (
                            saved["display_name"]
                            + f" [{saved['adapter_key']} / {saved['connection_type']}]"
                        )
                        connection_edit_target.value = saved["id"]
                        connection_edit_target.update()
                        billing_connection.options = {
                            item["id"]: (
                                item["display_name"] + f" [{item['adapter_key']}]"
                            )
                            for item in connection_by_id.values()
                            if SERVICE_BILLING_READ in set(item.get("capabilities") or [])
                        }
                        if billing_connection.value not in billing_connection.options:
                            billing_connection.value = None
                        billing_connection.update()

                        llm_connection_by_id.clear()
                        llm_connection_by_id.update({
                            item["id"]: item
                            for item in connection_by_id.values()
                            if LLM_INFERENCE in set(item.get("capabilities") or [])
                        })
                        profile_connection.options = {
                            item["id"]: (
                                item["display_name"]
                                + f" [{item['adapter_key']} / {item['connection_type']}]"
                            )
                            for item in llm_connection_by_id.values()
                        }
                        if profile_connection.value not in profile_connection.options:
                            profile_connection.value = None
                        profile_connection.update()

                        ui.notify(
                            "Service Connectionを保存しました。関連するConnection候補にも反映しました。",
                            type="positive",
                        )
                    except Exception as exc:
                        ui.notify(
                            "Service Connectionを保存できません: " + str(exc)[:220],
                            type="negative",
                        )

                with ui.row().classes("gap-2"):
                    ui.button(
                        "Connectionを保存",
                        icon="save",
                        color="indigo",
                        on_click=save_service_connection_from_ui,
                    )
                    ui.button(
                        "Adapter既定値",
                        icon="auto_fix_high",
                        on_click=fill_connection_defaults,
                    ).props("flat")
                ui.label(
                    "認証情報はこのローカルDBのauth_dataへ保存します。Prompt / Task / Audit / Gitには出しません。"
                ).classes("text-xs text-grey-7")

            with ui.expansion(
                "利用料金・契約 — 接続割当",
                value=False,
                icon="query_stats",
            ).classes("w-full border-2 border-indigo-200 bg-indigo-50"):
                ui.label(
                    "利用料金・契約の定義が使用するConnectionを明示的に選びます。"
                    "ConnectionのRoleやCapabilityから自動選択しません。"
                ).classes("text-sm")
                try:
                    with connection() as db:
                        billing_profiles = list_service_billing_profiles(
                            db,
                            include_disabled=True,
                        )
                        billing_connections = list_service_connections(
                            db,
                            include_disabled=True,
                            capability=SERVICE_BILLING_READ,
                        )
                except Exception as exc:
                    billing_profiles = []
                    billing_connections = []
                    ui.label(
                        "Service Billing設定を読み込めません: " + str(exc)[:180]
                    ).classes("text-sm text-red-700")

                billing_profile_by_id = {
                    item["id"]: item for item in billing_profiles
                }
                billing_profile_edit = ui.select(
                    options={
                        item["id"]: item["display_name"]
                        + " → "
                        + item["connection_name"]
                        for item in billing_profiles
                    },
                    value=None,
                    label="既存Service Billing Profileを編集（未選択なら新規）",
                ).props("clearable").classes("w-full")
                billing_profile_name = ui.input(
                    "表示名",
                    value="OpenAI Billing",
                ).classes("w-full")
                billing_connection = ui.select(
                    options={
                        item["id"]: item["display_name"]
                        + f" [{item['adapter_key']}]"
                        for item in billing_connections
                    },
                    value=None,
                    label="使用するService Connection",
                ).classes("w-full")
                billing_profile_enabled = ui.switch("Enabled", value=True)

                def apply_billing_profile(_event=None):
                    item = billing_profile_by_id.get(
                        str(billing_profile_edit.value or "")
                    )
                    if item is None:
                        return
                    billing_profile_name.value = item["display_name"]
                    billing_connection.value = item["connection_id"]
                    billing_profile_enabled.value = bool(item.get("enabled"))
                    billing_profile_name.update()
                    billing_connection.update()
                    billing_profile_enabled.update()

                billing_profile_edit.on_value_change(apply_billing_profile)

                def save_billing_profile():
                    try:
                        with connection() as db:
                            saved = upsert_service_billing_profile(
                                db,
                                display_name=str(
                                    billing_profile_name.value or ""
                                ).strip(),
                                connection_id=str(
                                    billing_connection.value or ""
                                ).strip(),
                                enabled=bool(billing_profile_enabled.value),
                                profile_id=(
                                    str(billing_profile_edit.value)
                                    if billing_profile_edit.value
                                    else None
                                ),
                            )
                        billing_profile_by_id[saved["id"]] = saved
                        billing_profile_edit.options[saved["id"]] = (
                            saved["display_name"]
                            + " → "
                            + saved["connection_name"]
                        )
                        billing_profile_edit.value = saved["id"]
                        billing_profile_edit.update()
                        ui.notify(
                            "Service Billing Profileを保存しました",
                            type="positive",
                        )
                    except Exception as exc:
                        ui.notify(
                            "Service Billing Profileを保存できません: "
                            + str(exc)[:220],
                            type="negative",
                        )

                ui.button(
                    "利用料金・契約の接続割当を保存",
                    icon="save",
                    color="indigo",
                    on_click=save_billing_profile,
                )

            with ui.expansion(
                "MAGI — LLM profile",
                value=False,
                icon="tune",
            ).classes("w-full border-2 border-teal-200 bg-teal-50"):
                ui.label(
                    "MELCHIOR / CASPER / BALTHASARはLLMの席名です。"
                    "LLM Profileはmodel/runtime条件だけを持ち、接続・認証はService Connectionを参照します。"
                ).classes("text-sm")
                try:
                    with connection() as db:
                        editable_profiles = list_llm_profiles(
                            db,
                            include_disabled=True,
                        )
                        llm_connections = list_service_connections(
                            db,
                            include_disabled=True,
                            capability=LLM_INFERENCE,
                        )
                except Exception as exc:
                    editable_profiles = []
                    llm_connections = []
                    ui.label(
                        "LLM Profile設定を読み込めません: " + str(exc)[:180]
                    ).classes("text-sm text-red-700")

                editable_profile_by_id = {
                    item["id"]: item for item in editable_profiles
                }
                llm_connection_by_id = {
                    item["id"]: item for item in llm_connections
                }
                profile_edit_target = ui.select(
                    options={
                        item["id"]: (
                            item["display_name"]
                            + f" [{item['provider']} / {item['model']}]"
                        )
                        for item in editable_profiles
                    },
                    value=None,
                    label="既存LLM profileを編集（未選択なら新規）",
                ).props("clearable").classes("w-full")

                profile_connection = ui.select(
                    options={
                        item["id"]: (
                            item["display_name"]
                            + f" [{item['adapter_key']} / {item['connection_type']}]"
                        )
                        for item in llm_connections
                    },
                    value=None,
                    label="使用するService Connection",
                ).classes("w-full")

                with ui.row().classes(
                    "w-full gap-2 items-end flex-wrap border-b border-teal-200 pb-3"
                ):
                    profile_model = ui.input(
                        "Model",
                        placeholder="例: gemma4:12b / gpt-5.6-sol / gemini-...",
                    ).classes("min-w-64 grow")
                    profile_display = ui.input(
                        "表示名（任意）",
                        placeholder="未指定なら adapter / model",
                    ).classes("min-w-64")

                with ui.row().classes(
                    "w-full gap-2 items-end flex-wrap border-b border-teal-200 py-3"
                ):
                    profile_context = ui.select(
                        options={
                            value: f"{value // 1024}K ({value})"
                            for value in OLLAMA_CONTEXT_OPTIONS
                        },
                        value=DEFAULT_OLLAMA_CONTEXT_TOKENS,
                        label="Context Window（Ollamaのみ）",
                    ).classes("min-w-56")
                    profile_generation = ui.select(
                        options={
                            value: f"{value} tokens"
                            for value in OLLAMA_NUM_PREDICT_OPTIONS
                        },
                        value=DEFAULT_MAGI_OLLAMA_NUM_PREDICT,
                        label="Generation Budget（Ollamaのみ）",
                    ).classes("min-w-56")
                    profile_retry_codes = ui.input(
                        "Retry HTTP codes",
                        value=",".join(str(code) for code in DEFAULT_RETRY_HTTP_CODES),
                        placeholder="429,500,502,503,504",
                    ).classes("min-w-64 grow")

                def apply_selected_profile(_event=None):
                    profile_id = str(profile_edit_target.value or "").strip()
                    item = editable_profile_by_id.get(profile_id)
                    if item is None:
                        return
                    profile_connection.value = item["connection_id"]
                    profile_model.value = item["model"]
                    profile_display.value = item["display_name"]
                    profile_context.value = item.get("context_window_tokens")
                    profile_generation.value = item.get("ollama_num_predict")
                    profile_retry_codes.value = ",".join(
                        str(code) for code in item.get("retry_http_codes") or []
                    )
                    for control in (
                        profile_connection, profile_model, profile_display,
                        profile_context, profile_generation, profile_retry_codes,
                    ):
                        control.update()

                profile_edit_target.on_value_change(apply_selected_profile)

                def add_llm_profile():
                    try:
                        selected_connection_id = str(
                            profile_connection.value or ""
                        ).strip()
                        selected_connection = llm_connection_by_id.get(
                            selected_connection_id
                        )
                        if selected_connection is None:
                            raise ValueError("Service Connectionを選択してください")
                        is_ollama = selected_connection["adapter_key"] == "ollama"
                        saved = _register_magi_profile(
                            connection_id=selected_connection_id,
                            model=str(profile_model.value or ""),
                            display_name=(
                                str(profile_display.value or "").strip() or None
                            ),
                            context_window_tokens=(
                                int(profile_context.value)
                                if is_ollama and profile_context.value is not None
                                else None
                            ),
                            ollama_num_predict=(
                                int(profile_generation.value)
                                if is_ollama and profile_generation.value is not None
                                else None
                            ),
                            retry_http_codes=normalize_retry_http_codes(
                                profile_retry_codes.value
                            ),
                            profile_id=(
                                str(profile_edit_target.value)
                                if profile_edit_target.value
                                else None
                            ),
                        )
                        editable_profile_by_id[saved["id"]] = saved
                        profile_edit_target.options[saved["id"]] = (
                            saved["display_name"]
                            + f" [{saved['provider']} / {saved['model']}]"
                        )
                        profile_edit_target.value = saved["id"]
                        profile_edit_target.update()
                        ui.notify(
                            "LLM profileを保存しました: " + saved["display_name"],
                            type="positive",
                        )
                    except Exception as exc:
                        ui.notify(
                            "LLM profileを保存できません: " + str(exc)[:200],
                            type="negative",
                        )

                ui.button(
                    "LLM profileを保存",
                    icon="save",
                    color="teal",
                    on_click=add_llm_profile,
                )
                ui.label(
                    "Ollamaのインストール済みmodelはRITSUKO画面を開いたとき自動でprofile同期されます。"
                ).classes("text-xs text-grey-7")

        with ui.expansion(
            "画面レイアウト設定",
            value=False,
            icon="dashboard_customize",
        ).classes("w-full border-2 border-blue-grey-200 bg-grey-50"):
            ui.label(
                "「表示」はブロック自体の表示/非表示を設定します。"
                "PKBの確認待ち・処理済みは右Drawerに表示し、"
                "Drawer自体の初期表示は確認待ち側で設定します。"
                "その他のアコーディオンは初期展開を設定します。"
            ).classes("text-sm text-grey-7")
            ui.label(
                "このブロックの「保存して反映」「初期値に戻す」は画面レイアウト設定だけに作用します。"
                "保存先はローカルの data/ui_preferences.json（Git管理外）です。"
            ).classes("text-sm text-grey-7")
            pkb_open_controls = {}
            pkb_visible_controls = {}
            with ui.expansion(
                "PKB",
                value=False,
                icon="account_tree",
            ).classes("w-full border-2 border-green-200 bg-green-50"):
                for key, label in pkb_labels.items():
                    with ui.row().classes("w-full items-center gap-4 border-b border-grey-300 py-2"):
                        ui.label(label).classes("grow")
                        pkb_visible_controls[key] = ui.switch(
                            "表示",
                            value=_UI_PREFERENCES["visibility"]["pkb"][key],
                        )
                        if key != "reviewed":
                            pkb_open_controls[key] = ui.switch(
                                "Drawer初期表示"
                                if key == "pending"
                                else "初期展開",
                                value=_UI_PREFERENCES["pkb"][key],
                            )

            entity_visible_controls = {}
            with ui.expansion(
                "Entity詳細",
                value=False,
                icon="category",
            ).classes("w-full border-2 border-purple-200 bg-purple-50"):
                ui.label(
                    "各タブの表示と、Entity詳細を開いたときの初期タブを設定します。"
                ).classes("text-sm text-grey-7")

                for key in ENTITY_TAB_ORDER:
                    with ui.row().classes("w-full items-center gap-4 border-b border-grey-300 py-2"):
                        ui.label(ENTITY_TAB_LABELS[key]).classes("grow")
                        entity_visible_controls[key] = ui.switch(
                            "表示",
                            value=_UI_PREFERENCES["visibility"]["entity"][key],
                        )

                entity_default_tab_select = ui.select(
                    options={
                        key: ENTITY_TAB_LABELS[key]
                        for key in ENTITY_TAB_ORDER
                    },
                    label="初期表示タブ",
                    value=_UI_PREFERENCES["entity"]["default_tab"],
                ).classes("min-w-64")

            finance_open_controls = {}
            finance_visible_controls = {}
            with ui.expansion(
                "家計・資産",
                value=False,
                icon="account_balance_wallet",
            ).classes("w-full border-2 border-blue-200 bg-blue-50"):
                for key, label in finance_labels.items():
                    with ui.row().classes("w-full items-center gap-4 border-b border-grey-300 py-2"):
                        ui.label(label).classes("grow")
                        finance_visible_controls[key] = ui.switch(
                            "表示",
                            value=_UI_PREFERENCES["visibility"]["finance"][key],
                        )
                        finance_open_controls[key] = ui.switch(
                            "初期展開",
                            value=_UI_PREFERENCES["finance"][key],
                        )
                page_size_select = ui.select(
                    options=list(FINANCE_PAGE_SIZE_OPTIONS),
                    label="保存済み明細の既定1ページ件数",
                    value=_UI_PREFERENCES["finance"]["recent_limit"],
                ).classes("min-w-64")

            core_list_controls = {}
            core_open_controls = {}
            core_visible_controls = {}
            with ui.expansion(
                "RITSUKO — Task表示件数",
                value=False,
                icon="format_list_numbered",
            ).classes("w-full border-2 border-slate-200 bg-slate-50"):
                ui.label(
                    "各一覧の初期件数と「さらに読み込む」で追加する件数です。"
                    "保存するとサーバー再起動なしで反映されます。"
                ).classes("text-sm text-grey-7")
                for key, label in (("open_limit", "進行中・確認待ち 初期表示件数"),
                                   ("completed_limit", "完了済み 初期表示件数")):
                    core_list_controls[key] = ui.select(
                        options=list(CORE_TASK_PAGE_SIZE_OPTIONS), label=label,
                        value=_UI_PREFERENCES["core"][key],
                    ).classes("min-w-64")

            with ui.expansion(
                "RITSUKO — 検証・補足の表示",
                value=False,
                icon="fact_check",
            ).classes("w-full border-2 border-slate-200 bg-slate-50"):
                ui.label(
                    "主操作の依頼ブロックは常時表示。検証・補足ブロックだけ非表示にできます。"
                ).classes("text-sm text-grey-7")
                for key, label in core_labels.items():
                    with ui.row().classes("w-full items-center gap-4 border-b border-grey-300 py-2"):
                        ui.label(label).classes("grow")
                        core_visible_controls[key] = ui.switch(
                            "表示",
                            value=_UI_PREFERENCES["visibility"]["core"][key],
                        )
                        if key in CORE_UI_DEFAULT_OPEN:
                            core_open_controls[key] = ui.switch(
                                "初期展開",
                                value=_UI_PREFERENCES["core"][key],
                            )

            ui.label(
                "保存後、別画面へ移動するかページを再読み込みすると表示/非表示が反映されます。"
                "現在のアコーディオン開閉状態と保存済み初期値は別管理です。"
            ).classes("text-sm text-grey-7")

            def collect_preferences() -> dict:
                pkb_preferences = dict(_UI_PREFERENCES["pkb"])
                pkb_preferences.update({
                    key: bool(control.value)
                    for key, control in pkb_open_controls.items()
                })

                return {
                    "pkb": pkb_preferences,
                    "entity": {
                        "default_tab": (
                            entity_default_tab_select.value
                            or ENTITY_TAB_DEFAULT
                        ),
                    },
                    "finance": {
                        **{
                            key: bool(control.value)
                            for key, control in finance_open_controls.items()
                        },
                        "recent_limit": int(
                            page_size_select.value or FINANCE_PAGE_SIZE_DEFAULT
                        ),
                    },
                    "core": {
                        **{key: bool(control.value) for key, control in core_open_controls.items()},
                        **{key: int(control.value) for key, control in core_list_controls.items()},
                    },
                    "core_advisor_model": _UI_PREFERENCES.get("core_advisor_model"),
                    "core_advisor_timeout": _UI_PREFERENCES.get("core_advisor_timeout", 60),
                    "visibility": {
                        "pkb": {
                            key: bool(control.value)
                            for key, control in pkb_visible_controls.items()
                        },
                        "entity": {
                            key: bool(control.value)
                            for key, control in entity_visible_controls.items()
                        },
                        "finance": {
                            key: bool(control.value)
                            for key, control in finance_visible_controls.items()
                        },
                        "core": {
                            key: bool(control.value)
                            for key, control in core_visible_controls.items()
                        },
                    },
                }

            def sync_controls(preferences: dict) -> None:
                for key, control in pkb_open_controls.items():
                    control.value = preferences["pkb"][key]
                for key, control in pkb_visible_controls.items():
                    control.value = preferences["visibility"]["pkb"][key]
                for key, control in entity_visible_controls.items():
                    control.value = preferences["visibility"]["entity"][key]

                entity_default_tab_select.value = (
                    preferences["entity"]["default_tab"]
                )
                for key, control in finance_open_controls.items():
                    control.value = preferences["finance"][key]
                for key, control in finance_visible_controls.items():
                    control.value = preferences["visibility"]["finance"][key]
                for key, control in core_visible_controls.items():
                    control.value = preferences["visibility"]["core"][key]
                for key, control in core_open_controls.items():
                    control.value = preferences["core"][key]
                for key, control in core_list_controls.items():
                    control.value = preferences["core"][key]
                page_size_select.value = preferences["finance"]["recent_limit"]

            def save_and_apply():
                try:
                    if not any(
                        bool(control.value)
                        for control in entity_visible_controls.values()
                    ):
                        ui.notify(
                            "Entity詳細は少なくとも1つのタブを表示してください",
                            type="negative",
                        )
                        return

                    saved = save_ui_preferences(collect_preferences())
                    _apply_ui_preferences(saved)

                    # Reflect validation/fallback immediately in the settings page.
                    sync_controls(saved)

                    ui.notify(
                        "表示設定を保存しました。各画面を開き直すと反映されます",
                        type="positive",
                    )
                except Exception as exc:
                    ui.notify(
                        "表示設定を保存できません: " + str(exc)[:220],
                        type="negative",
                    )

            def restore_builtin():
                defaults = _default_ui_preferences()
                sync_controls(defaults)
                try:
                    saved = save_ui_preferences(defaults)
                    _apply_ui_preferences(saved)
                    ui.notify("初期値へ戻して保存しました", type="positive")
                except Exception as exc:
                    ui.notify("初期値を保存できません: " + str(exc)[:220], type="negative")

            with ui.row().classes("gap-2"):
                ui.button(
                    "保存して反映",
                    icon="save",
                    color="blue",
                    on_click=save_and_apply,
                )
                ui.button(
                    "初期値に戻す",
                    icon="restart_alt",
                    on_click=restore_builtin,
                ).props("outline")


@ui.page("/finance")
def finance_page():
    finance_preferences = _UI_PREFERENCES["finance"]
    state = {
        "preview": None,
        "error": None,
        "filename": None,
        "csv_bytes": None,
        "import_plan": None,
        "import_result": None,
        "import_busy": False,
        "stored": None,
        "stored_error": None,
        "filter_options": {"accounts": [], "major_categories": []},
        "filters": {
            "start_date": None,
            "end_date": None,
            "account": None,
            "major_category": None,
            "search_text": None,
            "row_mode": "calculation_target",
            "page": 1,
            "sort_by": "date",
            "sort_dir": "desc",
            "recent_limit": finance_preferences["recent_limit"],
        },
        # Current navigation state is kept separately from saved defaults,
        # matching PKB behavior across normal page navigation.
        "ui_open": dict(_FINANCE_UI_OPEN),
    }

    try:
        with connection() as db:
            state["stored"] = load_finance_dashboard(db, **state["filters"])
            state["filter_options"] = finance_filter_options(db)
    except Exception as exc:
        state["stored_error"] = str(exc)

    def remember_expansion(key: str):
        def _remember(event):
            state["ui_open"][key] = bool(event.value)
            _set_finance_ui_open(key, event.value)
        return _remember

    with ui.column().classes("w-full max-w-7xl mx-auto gap-4 p-4"):
        _portal_header(
            "家計・資産",
            "保存済み家計をSQL-firstで表示し、MoneyForward CSVを差分Import",
        )

        with ui.expansion(
            "家計フィルタ・検索",
            value=state["ui_open"]["filter"],
            on_value_change=remember_expansion("filter"),
        ).classes(
            "w-full border-2 border-teal-200 bg-teal-50 text-teal-900"
            + _block_visibility_class("finance", "filter")
        ):
            with ui.row().classes("w-full gap-3 flex-wrap items-end"):
                start_input = ui.input("開始日").props("type=date").classes("min-w-40")
                end_input = ui.input("終了日").props("type=date").classes("min-w-40")
                account_select = ui.select(
                    options=[""] + state["filter_options"]["accounts"],
                    label="金融機関",
                    value="",
                ).classes("min-w-56")
                category_select = ui.select(
                    options=[""] + state["filter_options"]["major_categories"],
                    label="大項目",
                    value="",
                ).classes("min-w-48")
                row_mode_select = ui.select(
                    options={
                        "calculation_target": "集計対象のみ",
                        "all": "全明細（振替含む）",
                        "transfer": "振替のみ",
                    },
                    label="対象",
                    value="calculation_target",
                ).classes("min-w-48")
                search_input_finance = ui.input(
                    "明細検索",
                    placeholder="内容・メモ・金融機関・カテゴリ",
                ).classes("min-w-72 grow")

            def reload_stored():
                try:
                    filters = {
                        "start_date": (start_input.value or None),
                        "end_date": (end_input.value or None),
                        "account": (account_select.value or None),
                        "major_category": (category_select.value or None),
                        "search_text": ((search_input_finance.value or "").strip() or None),
                        "row_mode": (row_mode_select.value or "calculation_target"),
                        "page": 1,
                        "sort_by": state["filters"].get("sort_by", "date"),
                        "sort_dir": state["filters"].get("sort_dir", "desc"),
                        "recent_limit": state["filters"].get(
                            "recent_limit", finance_preferences["recent_limit"]
                        ),
                    }
                    if filters["start_date"] and filters["end_date"] and filters["start_date"] > filters["end_date"]:
                        ui.notify("開始日は終了日以前にしてください", type="warning")
                        return
                    with connection() as db:
                        state["stored"] = load_finance_dashboard(db, **filters)
                    state["filters"] = filters
                    state["stored_error"] = None
                except Exception as exc:
                    state["stored_error"] = str(exc)
                stored_finance.refresh()

            def reset_stored():
                start_input.value = ""
                end_input.value = ""
                account_select.value = ""
                category_select.value = ""
                row_mode_select.value = "calculation_target"
                search_input_finance.value = ""
                state["filters"]["sort_by"] = "date"
                state["filters"]["sort_dir"] = "desc"
                state["filters"]["recent_limit"] = finance_preferences["recent_limit"]
                reload_stored()

            with ui.row().classes("gap-2"):
                ui.button("適用", icon="filter_alt", color="teal", on_click=reload_stored)
                ui.button("クリア", icon="restart_alt", on_click=reset_stored).props("outline")

        @ui.refreshable
        def stored_finance():
            if state["stored_error"]:
                with ui.card().classes("w-full border-2 border-red-300 bg-red-50"):
                    ui.label("保存済み家計を読み取れません: " + state["stored_error"]).classes(
                        "text-red-800"
                    )
                return
            stored = state["stored"]
            if stored is None or stored.transaction_count == 0:
                with ui.card().classes("w-full border-2 border-grey-300"):
                    ui.label("保存済み家計データはまだありません。")
                return

            with ui.expansion(
                "保存済み家計",
                value=state["ui_open"]["stored"],
                on_value_change=remember_expansion("stored"),
            ).classes(
                "w-full border-2 border-green-300 bg-green-50 text-green-900"
                + _block_visibility_class("finance", "stored")
            ):
                fstate = state["filters"]
                has_user_filter = any(
                    fstate.get(key)
                    for key in ("start_date", "end_date", "account", "major_category", "search_text")
                ) or fstate.get("row_mode") != "calculation_target"
                if has_user_filter:
                    ui.badge("フィルタ適用中", color="teal")
                mode_labels = {
                    "calculation_target": "集計対象のみ",
                    "all": "全明細（振替含む）",
                    "transfer": "振替のみ",
                }
                ui.badge(
                    "対象: " + mode_labels.get(stored.row_mode, stored.row_mode),
                    color="green",
                )
                ui.label(
                    "PostgreSQLの正規化済みTransactionをSQL-firstで集計しています。"
                ).classes("text-sm text-green-900")
                with ui.row().classes("w-full gap-3 flex-wrap"):
                    for label, value in (
                        ("明細件数", f"{stored.transaction_count:,}件"),
                        ("集計対象", f"{stored.calculation_target_count:,}件"),
                        ("期間", f"{stored.start_date} ～ {stored.end_date}"),
                        ("収入", f"¥{stored.income_total:,.0f}"),
                        ("支出", f"¥{stored.expense_total:,.0f}"),
                        ("収支", f"¥{stored.net_total:,.0f}"),
                        ("Import Batch", f"{len(stored.import_batches):,}件"),
                    ):
                        with ui.card().classes("min-w-40"):
                            ui.label(label).classes("text-xs text-grey-7")
                            ui.label(value).classes("text-lg font-bold")

                with ui.expansion(
                    "保存済み月別集計",
                    value=state["ui_open"]["monthly"],
                    on_value_change=remember_expansion("monthly"),
                ).classes(
                    "w-full border-2 border-green-200 bg-white text-green-900"
                    + _block_visibility_class("finance", "monthly")
                ):
                    monthly_rows = [
                        {
                            **row,
                            "income_display": f"¥{row['income']:,}",
                            "expense_display": f"¥{row['expense']:,}",
                            "net_display": f"¥{row['net']:,}",
                        }
                        for row in stored.monthly
                    ]
                    ui.table(
                        columns=[
                            {"name": "month", "label": "月", "field": "month"},
                            {"name": "income", "label": "収入", "field": "income_display", "align": "right"},
                            {"name": "expense", "label": "支出", "field": "expense_display", "align": "right"},
                            {"name": "net", "label": "収支", "field": "net_display", "align": "right"},
                            {"name": "count", "label": "件数", "field": "count", "align": "right"},
                        ],
                        rows=monthly_rows,
                        row_key="month",
                    ).classes("w-full")

                with ui.expansion(
                    "保存済みカテゴリ別支出",
                    value=state["ui_open"]["categories"],
                    on_value_change=remember_expansion("categories"),
                ).classes(
                    "w-full border-2 border-green-200 bg-white text-green-900"
                    + _block_visibility_class("finance", "categories")
                ):
                    category_rows = [
                        {**row, "expense_display": f"¥{row['expense']:,}"}
                        for row in stored.categories
                    ]
                    ui.table(
                        columns=[
                            {"name": "major", "label": "大項目", "field": "major"},
                            {"name": "minor", "label": "中項目", "field": "minor"},
                            {"name": "expense", "label": "支出", "field": "expense_display", "align": "right"},
                        ],
                        rows=category_rows,
                        row_key="minor",
                    ).classes("w-full")

                with ui.expansion(
                    "保存済み明細",
                    value=state["ui_open"]["details"],
                    on_value_change=remember_expansion("details"),
                ).classes(
                    "w-full border-2 border-green-200 bg-white text-green-900"
                    + _block_visibility_class("finance", "details")
                ):
                    with ui.row().classes("w-full gap-3 flex-wrap items-end"):
                        sort_by_select = ui.select(
                            options={"date": "日付", "amount": "金額"},
                            label="並び替え",
                            value=stored.sort_by,
                        ).classes("min-w-32")
                        sort_dir_select = ui.select(
                            options={"desc": "降順", "asc": "昇順"},
                            label="順序",
                            value=stored.sort_dir,
                        ).classes("min-w-28")
                        page_size_select = ui.select(
                            options=[25, 50, 100],
                            label="1ページ件数",
                            value=stored.page_size,
                        ).classes("min-w-32")

                        def reload_page(
                            target_page: int | None = None,
                            apply_sort: bool = False,
                        ):
                            try:
                                filters = dict(state["filters"])
                                if apply_sort:
                                    filters["sort_by"] = sort_by_select.value or "date"
                                    filters["sort_dir"] = sort_dir_select.value or "desc"
                                    filters["recent_limit"] = int(
                                        page_size_select.value
                                        or finance_preferences["recent_limit"]
                                    )
                                    filters["page"] = 1
                                elif target_page is not None:
                                    filters["page"] = target_page
                                with connection() as db:
                                    state["stored"] = load_finance_dashboard(db, **filters)
                                state["filters"] = filters
                                state["stored_error"] = None
                            except Exception as exc:
                                state["stored_error"] = str(exc)
                            stored_finance.refresh()

                        ui.button(
                            "表示更新",
                            icon="sort",
                            color="green",
                            on_click=lambda: reload_page(apply_sort=True),
                        ).props("outline")

                    with ui.row().classes("w-full items-center justify-between"):
                        ui.label(
                            f"{stored.transaction_count:,}件中 "
                            f"{(stored.page - 1) * stored.page_size + 1:,}～"
                            f"{min(stored.page * stored.page_size, stored.transaction_count):,}件"
                        ).classes("text-sm")
                        with ui.row().classes("items-center gap-2"):
                            prev_button = ui.button(
                                "前へ",
                                icon="chevron_left",
                                on_click=lambda: reload_page(max(1, stored.page - 1)),
                            ).props("outline")
                            ui.label(f"{stored.page} / {stored.total_pages} ページ")
                            next_button = ui.button(
                                "次へ",
                                icon="chevron_right",
                                on_click=lambda: reload_page(
                                    min(stored.total_pages, stored.page + 1)
                                ),
                            ).props("outline")
                            if stored.page <= 1:
                                prev_button.disable()
                            if stored.page >= stored.total_pages:
                                next_button.disable()

                    def _compact_text(value: str, limit: int = 56) -> str:
                        text = value or ""
                        return text if len(text) <= limit else text[: limit - 1] + "…"

                    recent_rows = [
                        {
                            **row,
                            "content_display": _compact_text(row["content"]),
                            "amount_display": f"¥{row['amount']:,}",
                            "target_display": "○" if row["calculation_target"] else "",
                            "transfer_display": "○" if row["is_transfer"] else "",
                        }
                        for row in stored.recent_rows
                    ]
                    ui.table(
                        columns=[
                            {
                                "name": "date", "label": "日付", "field": "date",
                                "style": "width: 9%; white-space: nowrap;",
                                "headerStyle": "width: 9%;",
                            },
                            {
                                "name": "content", "label": "内容", "field": "content_display",
                                "style": (
                                    "width: 35%; max-width: 35%; overflow: hidden; "
                                    "text-overflow: ellipsis; white-space: nowrap;"
                                ),
                                "headerStyle": "width: 35%;",
                            },
                            {
                                "name": "amount", "label": "金額", "field": "amount_display",
                                "style": "width: 9%; white-space: nowrap;",
                                "headerStyle": "width: 9%; text-align: right;",
                                "align": "right",
                            },
                            {
                                "name": "account", "label": "金融機関", "field": "account",
                                "style": (
                                    "width: 16%; max-width: 16%; overflow: hidden; "
                                    "text-overflow: ellipsis; white-space: nowrap;"
                                ),
                                "headerStyle": "width: 16%;",
                            },
                            {
                                "name": "major", "label": "大項目", "field": "major_category",
                                "style": (
                                    "width: 10%; max-width: 10%; overflow: hidden; "
                                    "text-overflow: ellipsis; white-space: nowrap;"
                                ),
                                "headerStyle": "width: 10%;",
                            },
                            {
                                "name": "minor", "label": "中項目", "field": "minor_category",
                                "style": (
                                    "width: 13%; max-width: 13%; overflow: hidden; "
                                    "text-overflow: ellipsis; white-space: nowrap;"
                                ),
                                "headerStyle": "width: 13%;",
                            },
                            {
                                "name": "target", "label": "集計", "field": "target_display",
                                "style": "width: 4%; text-align: center;",
                                "headerStyle": "width: 4%; text-align: center;",
                                "align": "center",
                            },
                            {
                                "name": "transfer", "label": "振替", "field": "transfer_display",
                                "style": "width: 4%; text-align: center;",
                                "headerStyle": "width: 4%; text-align: center;",
                            },
                        ],
                        rows=recent_rows,
                        row_key="external_id",
                    ).props(
                        'dense flat table-style="table-layout: fixed; width: 100%;"'
                    ).classes("w-full")

                with ui.expansion(
                    "Import履歴 / Source",
                    value=state["ui_open"]["imports"],
                    on_value_change=remember_expansion("imports"),
                ).classes(
                    "w-full border-2 border-green-200 bg-white text-green-900"
                    + _block_visibility_class("finance", "imports")
                ):
                    ui.table(
                        columns=[
                            {"name": "filename", "label": "ファイル", "field": "source_filename"},
                            {"name": "rows", "label": "行数", "field": "row_count"},
                            {"name": "imported", "label": "Import時刻", "field": "imported_at"},
                            {"name": "status", "label": "状態", "field": "status"},
                            {"name": "sha", "label": "SHA-256", "field": "source_sha256"},
                        ],
                        rows=stored.import_batches,
                        row_key="id",
                    ).classes("w-full")
        stored_finance()

        with ui.expansion(
            "MoneyForward CSV 取込",
            value=state["ui_open"]["csv"],
            on_value_change=remember_expansion("csv"),
        ).classes(
            "w-full border-2 border-blue-300 bg-blue-50 text-blue-900"
            + _block_visibility_class("finance", "csv")
        ):
            ui.label("MoneyForward CSV プレビュー").classes("text-lg font-bold text-blue-900")
            ui.label(
                "CSVはまずローカルWebプロセスのメモリ上で読み取り専用解析します。"
                "明示的に「隔離DBへImport」を押すまでPostgreSQLへ保存しません。"
                "LLMには送信しません。"
            ).classes("text-sm text-blue-900")
            ui.label(
                "想定列: 計算対象 / 日付 / 内容 / 金額（円） / 保有金融機関 / "
                "大項目 / 中項目 / メモ / 振替 / ID"
            ).classes("text-xs text-grey-7")

            @ui.refreshable
            def finance_result():
                if state["error"]:
                    ui.label(str(state["error"])).classes("text-red-700")
                    return
                preview = state["preview"]
                if preview is None:
                    ui.label("CSVを選択すると、DBへ登録せず内容をプレビューします。")
                    return

                with ui.row().classes("w-full gap-3 flex-wrap"):
                    for label, value in (
                        ("明細件数", f"{preview.row_count:,}件"),
                        ("期間", f"{preview.start_date} ～ {preview.end_date}"),
                        ("集計対象", f"{preview.calculation_target_count:,}件"),
                        ("振替", f"{preview.transfer_count:,}件"),
                        ("ID重複", f"{preview.duplicate_id_count:,}件"),
                    ):
                        with ui.card().classes("min-w-40"):
                            ui.label(label).classes("text-xs text-grey-7")
                            ui.label(value).classes("text-lg font-bold")

                with ui.row().classes("w-full gap-3 flex-wrap"):
                    for label, value in (
                        ("収入", preview.income_total),
                        ("支出", preview.expense_total),
                        ("収支", preview.net_total),
                    ):
                        with ui.card().classes("min-w-48"):
                            ui.label(label).classes("text-xs text-grey-7")
                            ui.label(f"¥{value:,.0f}").classes("text-xl font-bold")

                with ui.expansion("月別集計", value=True).classes(
                    "w-full border-2 border-blue-200 bg-white text-blue-900"
                ):
                    ui.table(
                        columns=[
                            {"name": "month", "label": "月", "field": "month"},
                            {"name": "income", "label": "収入", "field": "income"},
                            {"name": "expense", "label": "支出", "field": "expense"},
                            {"name": "net", "label": "収支", "field": "net"},
                            {"name": "count", "label": "件数", "field": "count"},
                        ],
                        rows=preview.monthly,
                        row_key="month",
                    ).classes("w-full")

                with ui.expansion("支出カテゴリ上位", value=False).classes(
                    "w-full border-2 border-blue-200 bg-white text-blue-900"
                ):
                    ui.table(
                        columns=[
                            {"name": "major", "label": "大項目", "field": "major"},
                            {"name": "minor", "label": "中項目", "field": "minor"},
                            {"name": "expense", "label": "支出", "field": "expense"},
                        ],
                        rows=preview.categories,
                        row_key="minor",
                    ).classes("w-full")

                with ui.expansion("直近明細（最大100件）", value=False).classes(
                    "w-full border-2 border-blue-200 bg-white text-blue-900"
                ):
                    ui.table(
                        columns=[
                            {"name": "date", "label": "日付", "field": "date"},
                            {"name": "content", "label": "内容", "field": "content"},
                            {"name": "amount", "label": "金額", "field": "amount"},
                            {"name": "account", "label": "金融機関", "field": "account"},
                            {"name": "major", "label": "大項目", "field": "major_category"},
                            {"name": "minor", "label": "中項目", "field": "minor_category"},
                            {"name": "transfer", "label": "振替", "field": "is_transfer"},
                        ],
                        rows=preview.recent_rows,
                        row_key="external_id",
                    ).classes("w-full")

                plan = state["import_plan"]
                if plan is not None:
                    with ui.expansion("隔離DB Import", value=True).classes(
                        "w-full border-2 border-amber-300 bg-amber-50 text-amber-900 mt-3"
                    ):
                        ui.label(
                            "保存先は secretary_pkb_proto_20260927 の金融テーブルのみ。"
                            "元CSVそのものはDBへ保存せず、ファイル名・SHA-256・Import Batchを出典として保持します。"
                        ).classes("text-sm text-amber-900")
                        with ui.row().classes("w-full gap-3 flex-wrap"):
                            for label, value in (
                                ("新規", f"{plan.inserted:,}件"),
                                ("変更", f"{plan.updated:,}件"),
                                ("変更なし", f"{plan.unchanged:,}件"),
                                ("CSV内ID重複", f"{plan.duplicate_external_ids:,}件"),
                            ):
                                with ui.card().classes("min-w-36"):
                                    ui.label(label).classes("text-xs text-grey-7")
                                    ui.label(value).classes("text-lg font-bold")
                        ui.label("Source SHA-256: " + plan.source_sha256).classes(
                            "font-mono text-xs text-grey-7"
                        )

                        async def do_finance_import():
                            if state["import_busy"] or state["csv_bytes"] is None:
                                return
                            state["import_busy"] = True
                            import_button.disable()
                            try:
                                def _commit():
                                    with connection() as db:
                                        return commit_import(
                                            db,
                                            state["preview"],
                                            state["csv_bytes"],
                                            state["filename"] or "moneyforward.csv",
                                        )
                                state["import_result"] = await run.io_bound(_commit)
                                result = state["import_result"]
                                if result.status == "committed":
                                    ui.notify(
                                        f"隔離DBへImportしました: 新規{result.inserted} / "
                                        f"変更{result.updated} / 変更なし{result.unchanged}",
                                        type="positive",
                                    )
                                elif result.status == "replayed":
                                    ui.notify("同じCSVはすでにImport済みです", type="info")
                                else:
                                    ui.notify("Importを実行しませんでした: " + str(result.reason), type="warning")
                                with connection() as db:
                                    state["import_plan"] = plan_import(
                                        db, state["preview"], state["csv_bytes"]
                                    )
                                    state["stored"] = load_finance_dashboard(
                                        db, **state["filters"]
                                    )
                                    state["filter_options"] = finance_filter_options(db)
                                    state["stored_error"] = None
                                stored_finance.refresh()
                            except Exception as exc:
                                state["import_result"] = None
                                state["error"] = str(exc)
                                ui.notify(str(exc)[:240], type="negative")
                            finally:
                                state["import_busy"] = False
                                finance_result.refresh()

                        import_button = ui.button(
                            "隔離DBへImport",
                            icon="save",
                            color="orange",
                            on_click=do_finance_import,
                        )
                        if plan.duplicate_external_ids:
                            import_button.disable()

                        result = state["import_result"]
                        if result is not None:
                            ui.separator()
                            ui.label(
                                "直近Import結果: "
                                + f"{result.status} / 新規 {result.inserted:,} / "
                                + f"変更 {result.updated:,} / 変更なし {result.unchanged:,}"
                            ).classes("text-sm font-medium")
                            if result.batch_id:
                                ui.label("Import Batch: " + result.batch_id).classes(
                                    "font-mono text-xs"
                                )

            async def handle_finance_upload(event):
                try:
                    filename, data = await _uploaded_bytes(event)
                    preview = analyze_moneyforward_csv(data, filename)
                    with connection() as db:
                        import_plan = plan_import(db, preview, data)
                    state["preview"] = preview
                    state["filename"] = filename
                    state["csv_bytes"] = data
                    state["import_plan"] = import_plan
                    state["import_result"] = None
                    state["error"] = None
                    ui.notify(
                        f"{filename}: {preview.row_count:,}件を読み取り専用で解析しました",
                        type="positive",
                    )
                except Exception as exc:
                    state["preview"] = None
                    state["filename"] = None
                    state["csv_bytes"] = None
                    state["import_plan"] = None
                    state["import_result"] = None
                    state["error"] = str(exc)
                    ui.notify(str(exc)[:240], type="negative")
                finance_result.refresh()

            ui.upload(
                label="MoneyForward CSVを選択",
                on_upload=handle_finance_upload,
                auto_upload=True,
                max_file_size=20_000_000,
            ).props("accept=.csv").classes("w-full")
            finance_result()


def _display_result(result: dict):
    status = result.get("status", "")
    colors = {
        "inserted": "green",
        "replayed": "green",
        "corrected": "orange",
        "ok": "blue",
        "review": "orange",
        "needs_edit": "orange",
        "rejected": "red",
        "accepted": "green",
        "error": "red",
    }
    color = colors.get(status, "grey")
    ui.badge(status or "result", color=color)
    if result.get("reason"):
        ui.label("理由: " + str(result["reason"]))
    if result.get("message"):
        ui.label(str(result["message"]))
    if result.get("claim_id"):
        ui.label("Claim: " + result["claim_id"]).classes("font-mono text-xs")
    if result.get("old_claim_id"):
        ui.label("旧Claim: " + result["old_claim_id"]).classes("font-mono text-xs")
    if result.get("new_claim_id"):
        ui.label("新Claim: " + result["new_claim_id"]).classes("font-mono text-xs")
    if result.get("pending_id"):
        ui.label("Pending: " + result["pending_id"]).classes("font-mono text-xs")


@ui.page("/entity/{entity_id}")
def entity_page(entity_id: str):
    try:
        with connection() as db:
            detail = load_entity_detail(db, entity_id)
    except Exception as exc:
        detail = None
        error = str(exc)
    else:
        error = None

    with ui.column().classes("w-full max-w-7xl mx-auto gap-4 p-4"):
        _portal_header(
            "Entity 詳細",
            "現在・履歴・関連・出典を共通Entity画面で確認",
        )

        # No arrow icon: keep navigation compact and text-only.
        ui.button("PKBへ戻る").props("flat href=/pkb tag=a")

        if error:
            with ui.card().classes(
                "w-full border-2 border-red-300 bg-red-50"
            ):
                ui.label(
                    "Entity詳細を読み取れません: " + error
                ).classes("text-red-800")
            return

        if detail is None:
            with ui.card().classes("w-full border-2 border-grey-300"):
                ui.label("Entityが見つかりません。")
            return

        entity = detail["entity"]

        # Compact Entity header.
        with ui.card().classes(
            "w-full border-2 border-blue-300 bg-blue-50"
        ):
            with ui.row().classes(
                "w-full items-center gap-4 flex-wrap"
            ):
                with ui.column().classes("gap-0 grow"):
                    ui.label(entity["name"]).classes(
                        "text-2xl font-bold text-blue-900"
                    )
                    ui.label(
                        f'{entity["domain"]} / {entity["entity_type"]}'
                    ).classes("text-sm text-grey-7")

            ui.label(
                f'Entity ID: {entity["id"]}'
            ).classes("font-mono text-xs text-grey-7")

        visible_tabs, default_tab = _entity_tab_config()
        tab_refs = {}

        with ui.tabs().classes("w-full") as tabs:
            for key in ENTITY_TAB_ORDER:
                if visible_tabs[key]:
                    tab_refs[key] = ui.tab(ENTITY_TAB_LABELS[key])

        with ui.tab_panels(
            tabs,
            value=tab_refs[default_tab],
        ).classes("w-full"):

            if visible_tabs["overview"]:
                with ui.tab_panel(tab_refs["overview"]):
                    rows = detail["current"]

                    if not rows:
                        ui.label(
                            "現在値として表示できるState / Attributeはありません。"
                        )
                    else:
                        display = [
                            {
                                **row,
                                "value_display": str(row["value"]),
                                "valid_from_display": str(row["valid_from"]),
                            }
                            for row in rows
                        ]

                        ui.table(
                            columns=[
                                {
                                    "name": "kind",
                                    "label": "意味",
                                    "field": "semantic_kind",
                                    "align": "left",
                                },
                                {
                                    "name": "predicate",
                                    "label": "項目",
                                    "field": "predicate",
                                    "align": "left",
                                },
                                {
                                    "name": "value",
                                    "label": "現在値",
                                    "field": "value_display",
                                    "align": "left",
                                },
                                {
                                    "name": "since",
                                    "label": "開始",
                                    "field": "valid_from_display",
                                    "align": "left",
                                },
                            ],
                            rows=display,
                            row_key="id",
                        ).props("dense flat").classes("w-full")

            if visible_tabs["history"]:
                with ui.tab_panel(tab_refs["history"]):
                    combined = []

                    for row in detail["events"]:
                        combined.append(
                            {
                                "id": "event:" + row["id"],
                                "kind": "Event",
                                "predicate": row["predicate"],
                                "value_display": str(row["value"]),
                                "time_display": str(row["valid_from"]),
                                "_sort": str(row["valid_from"]),
                            }
                        )

                    for row in detail["history"]:
                        combined.append(
                            {
                                "id": "history:" + row["id"],
                                "kind": row["semantic_kind"],
                                "predicate": row["predicate"],
                                "value_display": str(row["value"]),
                                "time_display": (
                                    f'{row["valid_from"]} ～ {row["valid_to"]}'
                                ),
                                "_sort": str(row["valid_from"]),
                            }
                        )

                    combined.sort(
                        key=lambda row: row["_sort"],
                        reverse=True,
                    )

                    if not combined:
                        ui.label("履歴はありません。")
                    else:
                        ui.table(
                            columns=[
                                {
                                    "name": "kind",
                                    "label": "種類",
                                    "field": "kind",
                                    "align": "left",
                                },
                                {
                                    "name": "time",
                                    "label": "時点 / 有効期間",
                                    "field": "time_display",
                                    "align": "left",
                                },
                                {
                                    "name": "predicate",
                                    "label": "項目",
                                    "field": "predicate",
                                    "align": "left",
                                },
                                {
                                    "name": "value",
                                    "label": "値",
                                    "field": "value_display",
                                    "align": "left",
                                },
                            ],
                            rows=combined,
                            row_key="id",
                        ).props("dense flat").classes("w-full")

            if visible_tabs["relations"]:
                with ui.tab_panel(tab_refs["relations"]):
                    relations = detail["relations"]

                    if not relations:
                        ui.label(
                            "現在または履歴Relationはありません。"
                        )
                    else:
                        for rel in relations:
                            direction_label = (
                                "このEntityから"
                                if rel["direction"] == "outgoing"
                                else "このEntityへ"
                            )
                            role = (
                                f' / role={rel["relation_role"]}'
                                if rel["relation_role"]
                                else ""
                            )

                            with ui.row().classes(
                                "w-full items-center gap-3 "
                                "border-b border-purple-200 py-2"
                            ):
                                with ui.column().classes("grow gap-0"):
                                    ui.label(
                                        f'{direction_label} / '
                                        f'{rel["predicate"]}{role} / '
                                        f'{rel["other_entity_name"]}'
                                    ).classes("font-medium")

                                    ui.label(
                                        f'{rel["other_entity_type"]} / '
                                        f'from {rel["valid_from"]}'
                                    ).classes(
                                        "text-xs text-grey-7"
                                    )

                                # No arrow/open icon.
                                ui.button("詳細").props(
                                    f'flat href=/entity/'
                                    f'{rel["other_entity_id"]} tag=a'
                                )

            if visible_tabs["sources"]:
                with ui.tab_panel(tab_refs["sources"]):
                    rows = _entity_source_rows(detail)

                    if not rows:
                        ui.label("表示できるSourceはありません。")
                    else:
                        ui.table(
                            columns=[
                                {
                                    "name": "source",
                                    "label": "Source",
                                    "field": "source_uri",
                                    "align": "left",
                                },
                                {
                                    "name": "used_by",
                                    "label": "参照箇所",
                                    "field": "used_by",
                                    "align": "left",
                                },
                                {
                                    "name": "count",
                                    "label": "参照数",
                                    "field": "reference_count",
                                    "align": "right",
                                },
                            ],
                            rows=rows,
                            row_key="id",
                        ).props("dense flat").classes("w-full")


@ui.page("/pkb")
def pkb_page():
    state = {
        "write": None,
        "write_busy": False,
        "correction": None,
        "search": None,
    }
    drawer_limits = {
        "pending": PKB_DRAWER_PAGE_SIZE,
        "reviewed": PKB_DRAWER_PAGE_SIZE,
    }

    def remember_expansion(key: str):
        def _remember(event):
            _set_pkb_ui_open(key, event.value)
        return _remember

    def more_drawer_rows(key: str, panel) -> None:
        drawer_limits[key] += PKB_DRAWER_PAGE_SIZE
        panel.refresh()

    @ui.refreshable
    def pending_panel():
        try:
            with connection() as db:
                rows = list_pending(db)
        except Exception as exc:
            ui.label("確認待ち一覧を取得できません: " + str(exc)).classes(
                "text-red-600"
            )
            return

        ui.label(f"確認待ち {len(rows)}件").classes(
            "text-lg font-bold text-purple-900"
        )
        ui.label("Pending Claims").classes("text-xs text-grey-7")

        if not rows:
            ui.label("確認待ちはありません。").classes("text-sm text-grey-7")
            return

        def decide(pending_id: str, decision: str):
            try:
                with connection() as db:
                    review_pending(db, pending_id, decision)
                label = "却下" if decision == "rejected" else "要修正"
                ui.notify(label + "として記録しました", type="positive")
                pending_panel.refresh()
                reviewed_panel.refresh()
            except Exception as exc:
                ui.notify(str(exc)[:240], type="negative")

        def accept(pending_id: str):
            try:
                with connection() as db:
                    result = accept_pending(db, pending_id)
                if result.status == "accepted":
                    ui.notify("承認して正式Claimへ登録しました", type="positive")
                else:
                    ui.notify(
                        "この候補は承認できません: " + result.reason,
                        type="warning",
                    )
                pending_panel.refresh()
                reviewed_panel.refresh()
            except Exception as exc:
                ui.notify(str(exc)[:240], type="negative")

        visible_rows = rows[: drawer_limits["pending"]]
        for row in visible_rows:
            pending_id = str(row["id"])
            when = (
                row["recorded_at"].isoformat()
                if isinstance(row["recorded_at"], datetime)
                else str(row["recorded_at"])
            )
            with ui.card().classes(
                "w-full p-2 gap-1 border border-purple-200 bg-white"
            ):
                ui.label(row["raw_text"]).classes(
                    "w-full font-medium text-sm break-words"
                )
                ui.label("保留理由: " + row["reason"]).classes(
                    "w-full text-xs text-purple-900 break-all"
                )
                meta = when
                if row.get("entity_name"):
                    meta += " / " + row["entity_name"]
                if row.get("interpreter_model"):
                    meta += " / 解釈: " + row["interpreter_model"]
                ui.label(meta).classes("w-full text-xs text-grey-6 break-all")
                with ui.row().classes("w-full gap-1 flex-wrap"):
                    if acceptance_eligible(row):
                        ui.button(
                            "承認",
                            on_click=lambda pid=pending_id: accept(pid),
                            color="green",
                        ).props("outline dense")
                    ui.button(
                        "要修正",
                        on_click=lambda pid=pending_id: decide(
                            pid, "needs_edit"
                        ),
                        color="orange",
                    ).props("outline dense")
                    ui.button(
                        "却下",
                        on_click=lambda pid=pending_id: decide(
                            pid, "rejected"
                        ),
                        color="red",
                    ).props("outline dense")

        if len(rows) > drawer_limits["pending"]:
            remaining = len(rows) - drawer_limits["pending"]
            ui.button(
                f"さらに読み込む（残り{remaining}件）",
                on_click=lambda: more_drawer_rows(
                    "pending", pending_panel
                ),
            ).props("flat dense").classes("self-start")

    @ui.refreshable
    def reviewed_panel():
        try:
            with connection() as db:
                rows = list_reviewed(db)
        except Exception as exc:
            ui.label("処理履歴を取得できません: " + str(exc)).classes(
                "text-red-600"
            )
            return

        ui.label(f"処理済み {len(rows)}件").classes(
            "text-base font-bold text-grey-8"
        )

        if not rows:
            ui.label("処理済みの項目はまだありません。").classes(
                "text-sm text-grey-7"
            )
            return

        visible_rows = rows[: drawer_limits["reviewed"]]
        for row in visible_rows:
            status = row["review_status"]
            label = (
                "承認"
                if status == "accepted"
                else "要修正"
                if status == "needs_edit"
                else "却下"
                if status == "rejected"
                else status
            )
            when = (
                row["reviewed_at"].isoformat()
                if isinstance(row.get("reviewed_at"), datetime)
                else str(row.get("reviewed_at") or "")
            )
            with ui.card().classes(
                "w-full p-2 gap-1 border border-grey-300 bg-white"
            ):
                with ui.row().classes("w-full items-start gap-2 no-wrap"):
                    ui.badge(
                        label,
                        color=(
                            "green"
                            if status == "accepted"
                            else "orange"
                            if status == "needs_edit"
                            else "red"
                            if status == "rejected"
                            else "grey"
                        ),
                    ).classes("shrink-0")
                    ui.label(row["raw_text"]).classes(
                        "grow font-medium text-sm break-words"
                    )
                ui.label("理由: " + row["reason"]).classes(
                    "w-full text-xs text-grey-7 break-all"
                )
                ui.label("処理時点: " + when).classes(
                    "w-full text-xs text-grey-6 break-all"
                )

        if len(rows) > drawer_limits["reviewed"]:
            remaining = len(rows) - drawer_limits["reviewed"]
            ui.button(
                f"さらに読み込む（残り{remaining}件）",
                on_click=lambda: more_drawer_rows(
                    "reviewed", reviewed_panel
                ),
            ).props("flat dense").classes("self-start")

    pending_visible = _UI_PREFERENCES["visibility"]["pkb"]["pending"]
    reviewed_visible = _UI_PREFERENCES["visibility"]["pkb"]["reviewed"]
    pending_drawer = None
    if pending_visible or reviewed_visible:
        pending_drawer = ui.right_drawer(
            value=_PKB_UI_OPEN["pending"]
        ).classes("bg-purple-50 p-3").props(
            "bordered width=340 breakpoint=700"
        )
        with pending_drawer:
            with ui.column().classes("w-full gap-3 no-wrap"):
                if pending_visible:
                    pending_panel()
                if pending_visible and reviewed_visible:
                    ui.separator()
                if reviewed_visible:
                    reviewed_panel()

    with ui.column().classes("w-full max-w-7xl mx-auto gap-4 p-4"):
        _portal_header(
            "Local Secretary — Personal Knowledge Base",
            "日常用PKB • Core/Workbenchから独立 • localhostのみ",
        )
        ui.label(
            "現在は架空データ専用の隔離DB secretary_pkb_proto_20260927。運用DB・実データには接続しません。"
        ).classes("text-sm text-orange-700")

        with ui.row().classes("w-full items-center gap-2"):
            with ui.tabs().classes("grow") as pkb_tabs:
                tab_refs = {
                    key: ui.tab(PKB_TAB_LABELS[key])
                    for key in PKB_TAB_ORDER
                }
            if pending_drawer is not None:
                ui.button(
                    "確認待ち",
                    on_click=pending_drawer.toggle,
                    color="purple",
                ).props("outline dense")

        with ui.tab_panels(
            pkb_tabs,
            value=tab_refs[PKB_TAB_DEFAULT],
        ).classes("w-full"):
            with ui.tab_panel(tab_refs["record"]).classes("p-0 pt-3"):
                with ui.expansion('自然言語でまとめて記録（Memory Intake v1）', value=True).classes('w-full'):
                    ui.label('例: サブPCのWindows11を26H2に上げた。 明確な対応文は自動記録し、曖昧な部分は保留します。予定から現在状態は更新しません。').classes('text-sm')
                    intake_text = ui.textarea(label='記録する内容').classes('w-full')
                    memory_state = {'envelope': None, 'busy': False, 'result': None}

                    @ui.refreshable
                    def memory_result():
                        result = memory_state['result']
                        if result:
                            ui.label(result.get('message') or ('再送済みの結果です。' if result.get('status') == 'replayed' else '処理結果'))
                            labels = {'auto_commit': '記録済み', 'pending': '確認・補足待ち',
                                      'task_context_only': '一時的な内容', 'ignore': '記録対象外'}
                            for candidate in result.get('candidates', []):
                                if candidate.get('reason') == 'duplicate_existing_event':
                                    prefix = '既存記録と同一のため追加なし'
                                else:
                                    prefix = labels[candidate['decision']]
                                ui.label(prefix + ': ' + candidate['audit']['draft']['evidence']['quote'])
                            if result.get('status') in {'committed', 'replayed'} and not result.get('candidates'):
                                ui.label('記憶として保存する内容はありません。')

                            envelope = memory_state['envelope']
                            if envelope is not None:
                                with ui.expansion('Memory Intake 稼働ログ', value=False).classes(
                                    'w-full border border-blue-100 bg-white mt-2'
                                ):
                                    ui.label(
                                        'Extractor候補、Grounding、WriteDecision、Claim / derived State / PendingのIDを表示します。'
                                        ' モデルの推論過程は保存・表示しません。'
                                    ).classes('text-xs text-grey-7')
                                    export_text = json.dumps(
                                        _memory_intake_log_export(envelope, result),
                                        ensure_ascii=False,
                                        indent=2,
                                        default=str,
                                    )

                                    def copy_memory_intake_log(text: str = export_text) -> None:
                                        ui.run_javascript(
                                            'navigator.clipboard.writeText('
                                            + json.dumps(text, ensure_ascii=False)
                                            + ')'
                                        )
                                        ui.notify('Memory Intake稼働ログをコピーしました', type='positive')

                                    ui.button(
                                        'ログをコピー',
                                        icon='content_copy',
                                        on_click=copy_memory_intake_log,
                                    ).props('outline dense').classes('self-start')
                                    ui.code(export_text, language='json').classes('w-full text-xs')

                    async def do_memory_write():
                        if memory_state['busy']:
                            return
                        text = intake_text.value or ''
                        if not text.strip():
                            ui.notify('記録する内容を入力してください。')
                            return
                        envelope = memory_state['envelope']
                        if envelope is None or envelope.raw_text != text:
                            envelope = MemoryIntake.issue(text)
                            memory_state['envelope'] = envelope
                        memory_state['busy'] = True
                        memory_button.disable()
                        try:
                            memory_state['result'] = await run.io_bound(register_memory_intake, envelope)
                        except Exception:
                            memory_state['result'] = {'message': '保存できませんでした。隔離DBのmigration 019適用と接続を確認してください。同じ内容で再試行できます。'}
                        finally:
                            memory_state['busy'] = False
                            memory_button.enable()
                            memory_result.refresh()
                            pending_panel.refresh()
                    memory_button = ui.button('まとめて記録する', on_click=do_memory_write)
                    memory_result()


                with ui.expansion(
                    "記録",
                    value=_PKB_UI_OPEN["write"],
                    on_value_change=remember_expansion("write"),
                ).classes(
                    "w-full border-2 border-green-300 bg-green-50 text-green-900"
                    + _block_visibility_class("pkb", "write")
                ):
                    ui.label("例: メインPCをDRV-A3へ更新した。 / メインPCのGPUドライバーをDRV-G1へ更新した。 / RCカーBのサーボをSERVO-X3へ交換した。").classes("text-sm")
                    write_input = ui.textarea(label="自然言語で記録").classes("w-full")
                    @ui.refreshable
                    def write_result():
                        if state["write_busy"]:
                            with ui.row().classes("items-center gap-2"):
                                ui.spinner(size="sm", color="green")
                                ui.label("ローカルLLMで解析中… 画面はそのまま利用できます。")
                        elif state["write"]:
                            _display_result(state["write"])
                        else:
                            ui.label("まだ記録していません。")
                    async def do_write():
                        if state["write_busy"]:
                            return
                        state["write_busy"] = True
                        write_button.disable()
                        write_result.refresh()
                        try:
                            # register_text may wait on local Ollama for tens of seconds.
                            # Keep NiceGUI's event loop responsive by moving the blocking
                            # DB/Ollama work to an I/O worker thread.
                            state["write"] = await run.io_bound(register_text, write_input.value or "")
                        except Exception as exc:
                            state["write"] = {"status": "error", "reason": str(exc)}
                        finally:
                            state["write_busy"] = False
                            write_button.enable()
                            write_result.refresh()
                            pending_panel.refresh()
                    write_button = ui.button("記録する", on_click=do_write, color="green")
                    write_result()


                with ui.expansion(
                    "訂正",
                    value=_PKB_UI_OPEN["correction"],
                    on_value_change=remember_expansion("correction"),
                ).classes(
                    "w-full border-2 border-amber-300 bg-amber-50 text-amber-900" + _block_visibility_class("pkb", "correction")
                ):
                    ui.label("例: 訂正：サブPCではなくメインPCをDRV-A1へ更新した。").classes("text-sm")
                    correction_input = ui.textarea(label="明示的に訂正").classes("w-full")
                    @ui.refreshable
                    def correction_result():
                        if state["correction"]:
                            _display_result(state["correction"])
                        else:
                            ui.label("まだ訂正していません。")
                    def do_correct():
                        try:
                            state["correction"] = correct_text(correction_input.value or "")
                        except Exception as exc:
                            state["correction"] = {"status": "error", "reason": str(exc)}
                        correction_result.refresh()
                        search_result.refresh()
                        pending_panel.refresh()
                    ui.button("訂正する", on_click=do_correct, color="orange")
                    correction_result()
            with ui.tab_panel(tab_refs["search"]).classes("p-0 pt-3"):
                with ui.expansion(
                    "検索・履歴",
                    value=_PKB_UI_OPEN["search"],
                    on_value_change=remember_expansion("search"),
                ).classes(
                    "w-full border-2 border-blue-300 bg-blue-50 text-blue-900"
                    + _block_visibility_class("pkb", "search")
                ):
                    ui.label("例: メインPCの構成 / メインPCのGPUの現在のドライバー / サブPCのドライバー更新履歴").classes("text-sm")
                    search_input = ui.input(label="自然言語で検索").classes("w-full")
                    @ui.refreshable
                    def search_result():
                        result = state["search"]
                        if not result:
                            ui.label("検索結果はまだありません。")
                            return
                        ui.label(f'件数: {result.get("total", 0)}')
                        rows = result.get("items", [])
                        if not rows:
                            ui.label("該当する記録はありません。")
                            return
                        if result.get("result_kind") == "components":
                            columns = [
                                {"name": "parent", "label": "親Entity", "field": "parent_name"},
                                {"name": "relation", "label": "関係", "field": "relation_predicate"},
                                {"name": "role", "label": "役割", "field": "relation_role"},
                                {"name": "component", "label": "構成要素", "field": "component_name"},
                                {"name": "type", "label": "型", "field": "component_type"},
                                {"name": "driver", "label": "現在ドライバー", "field": "current_driver"},
                                {"name": "since", "label": "状態開始", "field": "state_valid_from"},
                                {"name": "source", "label": "状態の出典", "field": "state_source_uri"},
                            ]
                            ui.table(columns=columns, rows=rows, row_key="relation_id").classes("w-full")
                            return
                        for row in rows:
                            raw_status = row.get("status_at_cutoff")
                            if raw_status == "active":
                                row["status_at_cutoff"] = "現行記録"
                            elif raw_status == "superseded":
                                row["status_at_cutoff"] = "旧版・訂正済み"
                        columns = [
                            {"name": "entity", "label": "対象", "field": "entity_name"},
                            {"name": "predicate", "label": "種類", "field": "predicate"},
                            {"name": "semantic", "label": "意味", "field": "semantic_kind"},
                            {"name": "value", "label": "値", "field": "value"},
                            {"name": "valid_from", "label": "有効時点", "field": "valid_from"},
                            {"name": "status", "label": "記録状態", "field": "status_at_cutoff"},
                            {"name": "source", "label": "出典", "field": "source_uri"},
                        ]
                        ui.table(columns=columns, rows=rows, row_key="id").classes("w-full")
                    def do_search():
                        try:
                            state["search"] = search_text(search_input.value or "")
                        except Exception as exc:
                            state["search"] = {"status": "error", "total": 0, "items": [], "reason": str(exc)}
                        search_result.refresh()
                    ui.button("検索する", on_click=do_search, color="blue")
                    search_result()


                with ui.expansion(
                    "Entity一覧",
                    value=_PKB_UI_OPEN["entities"],
                    on_value_change=remember_expansion("entities"),
                ).classes(
                    "w-full border-2 border-indigo-300 bg-indigo-50 text-indigo-900" + _block_visibility_class("pkb", "entities")
                ):
                    try:
                        with connection() as db:
                            entity_rows = _entities(db)
                    except Exception as exc:
                        ui.label("Entity一覧を取得できません: " + str(exc)).classes("text-red-700")
                    else:
                        ui.label(
                            "詳細画面は共通骨格です。PC・RCなどのEntity型ごとの専用表示は必要に応じて追加します。"
                        ).classes("text-sm")
                        for row in entity_rows:
                            with ui.row().classes(
                                "w-full items-center gap-3 border-b border-indigo-200 py-2"
                            ):
                                with ui.column().classes("grow gap-0"):
                                    ui.label(row["name"]).classes("font-medium")
                                    ui.label(
                                        f"{row['domain']} / {row['entity_type']}"
                                    ).classes("text-xs text-grey-7")
                                ui.button("詳細", icon="open_in_new").props(
                                    f"flat href=/entity/{row['id']} tag=a"
                                )


def _recover_interrupted_core_advisors(
    error: str = "interrupted_by_server_restart",
) -> int:
    """Mark queued/running advisor jobs interrupted and restore resumable probes."""
    try:
        with connection() as db:
            with db.cursor() as cur:
                cur.execute(
                    """SELECT id, checkpoint->'advisor_shadow'
                       FROM secretary.tasks
                       WHERE requested_by='local_user'
                         AND COALESCE(checkpoint->>'core_slice', '')='daily_read_only_v1'
                         AND COALESCE(checkpoint->'advisor_shadow'->>'job_status', '')
                             IN ('queued', 'running')"""
                )
                rows = cur.fetchall()
    except Exception:
        return 0

    recovered = 0
    now = datetime.now(timezone.utc)
    for task_id, shadow in rows:
        current = dict(shadow or {})
        started_text = current.get("started_at")
        elapsed = current.get("elapsed_seconds") or 0.0
        if started_text:
            try:
                started = datetime.fromisoformat(str(started_text))
                elapsed = max(0.0, (now - started).total_seconds())
            except ValueError:
                pass
        current.update({
            "job_status": "error",
            "status": "unavailable",
            "comparison": "unavailable",
            "finished_at": now.isoformat(),
            "elapsed_seconds": round(float(elapsed), 3),
            "error": error,
        })
        try:
            normalized_id = UUID(str(task_id))
            if _write_core_advisor_shadow(
                normalized_id, current, "core.advisor.interrupted"
            ):
                recovered += 1
            _restore_interrupted_cooperative_probe(normalized_id, error)
        except Exception:
            pass
    return recovered


def _shutdown_core_advisor_background_jobs() -> None:
    """Prevent legacy Advisor work from keeping NiceGUI alive after shutdown."""
    _CORE_ADVISOR_STOP_EVENT.set()
    _CORE_ADVISOR_EXECUTOR.close(wait=False, cancel_pending=True)
    _recover_interrupted_core_advisors("interrupted_by_server_shutdown")


app.on_shutdown(_shutdown_core_advisor_background_jobs)


if __name__ == "__main__":
    _recover_interrupted_core_advisors()
    ui.run(host="127.0.0.1", port=8093, reload=False, show=False, title="Local Secretary PKB")

