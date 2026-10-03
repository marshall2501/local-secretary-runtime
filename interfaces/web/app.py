"""Daily PKB Web UI prototype on an isolated fictional PostgreSQL DB.

This is the first user-facing PKB slice, separate from the developer Workbench.
It deliberately refuses the production DB and accepts only the existing
secretary_pkb_proto_20260927 fixture database through the dedicated writer role.

Run with: python -m interfaces.web.app
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

from fastapi import HTTPException
from nicegui import app, context, run, ui
from interfaces.web.pages.top import register as register_top_page
from interfaces.web.pages.debug import register as register_debug_page
from interfaces.web.pages.service_billing import register as register_service_billing_page
from interfaces.web.pages.features import register as register_features_page
from interfaces.web.pages.core_history import register as register_core_history_page
from interfaces.web.pages.core import register as register_core_page
from interfaces.web.pages.settings import register as register_settings_page
from interfaces.web.pages.finance import register as register_finance_page
from interfaces.web.pages.entity import register as register_entity_page
from interfaces.web.pages.pkb import register as register_pkb_page
from pydantic import BaseModel, Field

from infrastructure.async_runtime.background_jobs import DaemonSerialBackgroundExecutor
from bootstrap.web_runtime import (
    DBNAME,
    HOST,
    WRITER,
    DatabaseError,
    build_core_execution_repository,
    build_core_task_queries,
    claim_core_cooperative_probe,
    connection,
    fail_core_cooperative_probe,
    finalize_core_cooperative_probe,
    interrupted_core_advisors,
    pkb_debug_summary,
    record_core_cooperative_probe,
    restore_core_cooperative_probe,
    write_core_advisor_shadow,
)
from pkb.correction_service import correct_entity
from ritsuko.core.core_ooda import OODA_PHASES, derive_ooda
from ritsuko.core.core_observation import build_observation_pack
from ritsuko.core.core_advisor import advise as advise_core, choose_model as choose_advisor_model, list_chat_models as list_advisor_models
from ritsuko.core.core_synthesis import synthesize as synthesize_magi
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
from ritsuko.application.task_queries import core_task_selection_result
from ritsuko.application.task_records import (
    abort_proposal_review_record,
    abort_user_resume_record,
    claim_proposal_review_record,
    claim_user_resume_record,
    create_task_record,
    fail_task_record,
    finalize_proposal_review_record,
    persist_session_record,
    prepare_memory_intake_record,
    record_pkb_read_record,
)
from ritsuko.application.read_dispatch import execute_core_read
from ritsuko.application.entry import RitsukoApplicationEntry, contextualize_reply
from ritsuko.application.driver_compare import (
    clarified_driver_web_target,
    compare_driver_values,
    driver_web_query_from_detail,
    execute_pkb_web_compare,
    pkb_current_driver_value,
    resolve_driver_web_target,
    web_latest_version_value,
)
from ritsuko.core.request_scope import core_answer, scope_core_request
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
from capabilities.finance.application import (
    core_finance_filters as finance_filters,
    finance_core_answer,
    query_finance_text,
)
from capabilities.web_research.application import (
    research_text,
    web_core_answer,
)
from pkb.pending_service import (accept_pending, acceptance_eligible, enqueue as enqueue_pending,
    list_pending, list_reviewed, review_pending)
from pkb.application.daily import (
    COMPONENT_STATE_QUERY_PATTERN,
    correct_text as pkb_correct_text,
    entity_map as pkb_entity_map,
    list_entities as pkb_list_entities,
    parse_component_write,
    parse_correction,
    parse_query,
    parse_write,
    register_text as pkb_register_text,
    search_text as pkb_search_text,
    search_text_with_db as pkb_search_text_with_db,
)
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
from infrastructure.system_debug import (
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
    return Path(__file__).resolve().parents[2] / "data" / "ui_preferences.json"


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
    for sync in globals().get("_PAGE_CONTEXT_SYNCERS", ()):
        sync(globals())


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
    return pkb_list_entities(db)


def _entity_map(db) -> dict[str, dict]:
    return pkb_entity_map(db)


def register_text(text: str) -> dict:
    return pkb_register_text(
        text, connection_factory=connection, interpreter=interpret_daily
    )


def correct_text(text: str) -> dict:
    return pkb_correct_text(text, connection_factory=connection)


def _search_text_with_db(db, text: str) -> dict:
    return pkb_search_text_with_db(db, text)


def search_text(text: str) -> dict:
    return pkb_search_text(text, connection_factory=connection)


def _core_finance_filters(text: str) -> dict:
    return finance_filters(text)


def finance_text(text: str) -> dict:
    return query_finance_text(text, connection_factory=connection)


def web_text(text: str) -> dict:
    return research_text(text)


_pkb_current_driver_value = pkb_current_driver_value
_web_latest_version_value = web_latest_version_value
_compare_driver_values = compare_driver_values
_driver_web_query_from_detail = driver_web_query_from_detail
_clarified_driver_web_target = clarified_driver_web_target


def _resolve_driver_web_target(text: str) -> dict:
    return resolve_driver_web_target(
        text,
        connection_factory=connection,
        resolve_component=resolve_component_reference,
        load_detail=load_entity_detail,
    )


def _execute_pkb_web_compare(text: str, *, target_override: str | None = None) -> dict:
    return execute_pkb_web_compare(
        text,
        target_override=target_override,
        execute_read=_execute_core_read,
        resolve_target=_resolve_driver_web_target,
    )


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


def _create_magi_core_task_record(task_id: UUID, request: str, member_specs: list[dict]) -> None:
    create_task_record(connection, task_id, request, member_specs)


def _claim_magi_user_resume_record(task_id: UUID, reply_length: int, reply_fingerprint: str):
    return claim_user_resume_record(connection, task_id, reply_length, reply_fingerprint)


def _abort_magi_user_resume_record(task_id: UUID, error_type: str) -> None:
    abort_user_resume_record(connection, task_id, error_type)


def _claim_magi_proposal_review_record(task_id: UUID, decision: str, memory_result: dict | None):
    return claim_proposal_review_record(connection, task_id, decision, memory_result)


def _finalize_magi_proposal_review_record(task_id: UUID, session: dict, selected_capability: str | None) -> dict:
    return finalize_proposal_review_record(connection, task_id, session, selected_capability)


def _abort_magi_proposal_review_record(task_id: UUID, error_type: str) -> None:
    abort_proposal_review_record(connection, task_id, error_type)


def _prepare_magi_memory_intake_record(task_id: UUID) -> MemoryIntake:
    return prepare_memory_intake_record(connection, task_id)


def _persist_magi_core_session_record(task_id: UUID, session: dict, selected_capability: str | None = None) -> dict:
    return persist_session_record(connection, task_id, session, selected_capability)


def _record_magi_pkb_read_record(task_id: UUID, execution: dict, pending_request: dict) -> tuple[str, str]:
    return record_pkb_read_record(connection, task_id, execution, pending_request)


def _fail_magi_core_task_record(task_id: UUID, error_type: str) -> None:
    fail_task_record(connection, task_id, error_type)


def _execute_core_read(capability: str, text: str) -> dict:
    return execute_core_read(
        capability, text,
        pkb_search=search_text,
        finance_read=finance_text,
        web_research=web_text,
        pkb_answer=core_answer,
        finance_answer=finance_core_answer,
        web_answer=web_core_answer,
    )


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
    return write_core_advisor_shadow(task_id, shadow, event_type)


def _claim_cooperative_probe(task_id: UUID, capability: str) -> bool:
    return claim_core_cooperative_probe(task_id, capability)


def _record_cooperative_probe(task_id: UUID, request: str, execution: dict,
                              observation_pack: dict) -> tuple[UUID, UUID]:
    return record_core_cooperative_probe(task_id, request, execution, observation_pack)


def _finalize_cooperative_probe(task_id: UUID, final_decision: dict,
                                final_observation_pack: dict, execution: dict) -> None:
    finalize_core_cooperative_probe(task_id, final_decision, final_observation_pack, execution)


def _fail_cooperative_probe(task_id: UUID, error: str) -> None:
    fail_core_cooperative_probe(task_id, error)


def _restore_interrupted_cooperative_probe(task_id: UUID, error: str) -> bool:
    return restore_core_cooperative_probe(task_id, error)


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


def _load_ritsuko_request_context(request: str) -> tuple[dict[str, dict], dict]:
    with connection() as read_db:
        entities = _entity_map(read_db)
        observation_pack = _build_core_observation_pack(request, read_db, entities)
    return entities, observation_pack


def _ritsuko_application_entry() -> RitsukoApplicationEntry:
    return RitsukoApplicationEntry(
        build_core_execution_repository(),
        load_context=_load_ritsuko_request_context,
        scope_request=scope_core_request,
        execute_read=_execute_core_read,
        execute_compare=_execute_pkb_web_compare,
        advisor_shadow_initial=_advisor_shadow_initial,
        queue_advisor=_queue_core_advisor_shadow,
    )


def run_core_request(
    text: str,
    advisor_model: str | None = None,
    advisor_timeout: float = 60.0,
) -> dict:
    """GUI compatibility wrapper around the common RITSUKO application entry."""
    return _ritsuko_application_entry().request(
        text,
        advisor_model=advisor_model,
        advisor_timeout=advisor_timeout,
    )


_contextualize_core_reply = contextualize_reply

def _core_task_queries() -> CoreTaskQueryService:
    return build_core_task_queries()


def load_recent_core_tasks(limit: int = 10, offset: int = 0) -> list[dict]:
    return _core_task_queries().recent(limit, offset)


def load_open_core_tasks(limit: int = 20, offset: int = 0) -> list[dict]:
    return _core_task_queries().open(limit, offset)


def load_completed_core_tasks(limit: int = 8, offset: int = 0) -> list[dict]:
    return _core_task_queries().completed(limit, offset)


def load_core_task_trace(task_id: UUID) -> dict:
    return _core_task_queries().trace(task_id)


def resume_core_task(task_id: UUID, reply: str) -> dict:
    """GUI compatibility wrapper around the common RITSUKO application entry."""
    return _ritsuko_application_entry().resume(task_id, reply)


from pkb.memory_contracts import MemoryIntake
from pkb.memory_intake import write_intake
from pkb.application.memory_intake import register_memory_intake as write_checked_memory_intake


def register_memory_intake(intake: MemoryIntake) -> dict:
    with connection() as db:
        return write_checked_memory_intake(db, intake)


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
    except (RuntimeError, DatabaseError) as exc:
        raise HTTPException(503, 'Memory Intakeの保存に失敗しました。同じ入力のまま再試行できます。') from exc


class PendingDecisionInput(BaseModel):
    decision: str = Field(pattern="^(rejected|needs_edit)$")


@app.get("/api/core/tasks/open")
def api_core_open_tasks():
    try:
        return {"items": load_open_core_tasks(20)}
    except (RuntimeError, ValueError, DatabaseError) as exc:
        raise HTTPException(503, str(exc)) from exc


@app.get("/api/core/tasks/completed")
def api_core_completed_tasks():
    try:
        return {"items": load_completed_core_tasks(8)}
    except (RuntimeError, ValueError, DatabaseError) as exc:
        raise HTTPException(503, str(exc)) from exc


@app.get("/api/core/tasks/{task_id}/trace")
def api_core_trace(task_id: UUID):
    try:
        return load_core_task_trace(task_id)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    except (RuntimeError, DatabaseError) as exc:
        raise HTTPException(503, str(exc)) from exc


@app.post("/api/core/tasks/{task_id}/resume")
def api_core_resume(task_id: UUID, params: TextInput):
    try:
        return resume_core_task(task_id, params.text)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    except (RuntimeError, DatabaseError) as exc:
        raise HTTPException(503, str(exc)) from exc


@app.post("/api/core/request")
def api_core_request(params: TextInput):
    try:
        return run_core_request(params.text)
    except (RuntimeError, ValueError, DatabaseError) as exc:
        raise HTTPException(503, str(exc)) from exc


@app.get("/api/pkb/entities")
def api_entities():
    try:
        with connection() as db:
            return _entities(db)
    except (RuntimeError, DatabaseError) as exc:
        raise HTTPException(503, str(exc)) from exc


@app.post("/api/pkb/register")
def api_register(params: TextInput):
    try:
        return register_text(params.text)
    except (RuntimeError, ValueError, DatabaseError) as exc:
        raise HTTPException(503, str(exc)) from exc


@app.post("/api/pkb/correct")
def api_correct(params: TextInput):
    try:
        return correct_text(params.text)
    except (RuntimeError, ValueError, DatabaseError) as exc:
        raise HTTPException(503, str(exc)) from exc


@app.post("/api/pkb/search")
def api_search(params: TextInput):
    try:
        return search_text(params.text)
    except (RuntimeError, ValueError, DatabaseError) as exc:
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
    except (RuntimeError, ValueError, DatabaseError) as exc:
        raise HTTPException(503, str(exc)) from exc


@app.post("/api/pkb/pending/{pending_id}/review")
def api_pending_review(pending_id: str, params: PendingDecisionInput):
    try:
        with connection() as db:
            return asdict(review_pending(db, pending_id, params.decision))
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except (RuntimeError, DatabaseError) as exc:
        raise HTTPException(503, str(exc)) from exc


@app.post("/api/pkb/pending/{pending_id}/accept")
def api_pending_accept(pending_id: str):
    try:
        with connection() as db:
            return asdict(accept_pending(db, pending_id))
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except (RuntimeError, DatabaseError) as exc:
        raise HTTPException(503, str(exc)) from exc


def _debug_database_summary() -> dict:
    return pkb_debug_summary()


def _load_system_debug_snapshot() -> dict:
    root = Path(__file__).resolve().parents[2]
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


def _recover_interrupted_core_advisors(
    error: str = "interrupted_by_server_restart",
) -> int:
    """Mark queued/running advisor jobs interrupted and restore resumable probes."""
    try:
        rows = interrupted_core_advisors()
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


_PAGE_CONTEXT_SYNCERS = []


def _register_page(register):
    page = register(globals())
    sync = getattr(page, "_context_sync", None)
    if sync is not None:
        _PAGE_CONTEXT_SYNCERS.append(sync)
    return page


top_page = _register_page(register_top_page)
debug_page = _register_page(register_debug_page)
service_billing_page = _register_page(register_service_billing_page)
features_page = _register_page(register_features_page)
core_history_page = _register_page(register_core_history_page)
core_page = _register_page(register_core_page)
settings_page = _register_page(register_settings_page)
finance_page = _register_page(register_finance_page)
entity_page = _register_page(register_entity_page)
pkb_page = _register_page(register_pkb_page)


app.on_shutdown(_shutdown_core_advisor_background_jobs)


def main() -> None:
    _recover_interrupted_core_advisors()
    ui.run(host="127.0.0.1", port=8093, reload=False, show=False, title="Local Secretary PKB")


if __name__ == "__main__":
    main()

