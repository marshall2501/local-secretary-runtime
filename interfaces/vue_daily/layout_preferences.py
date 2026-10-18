# Provenance-Origin: original
# Provenance-AI: AI-generated
"""Shared, bounded Vue layout preferences stored in the existing NiceGUI UI file.

The file's non-layout settings (notably judgment mode and advisor preferences)
are preserved untouched. This module never imports the legacy NiceGUI server.
"""
from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

from interfaces.vue_daily.preferences import preferences_path
from interfaces.web.core_ui import CORE_UI_DEFAULT_OPEN, CORE_UI_VISIBILITY_DEFAULT

PKB_DEFAULT_OPEN = {
    "write": True, "correction": False, "search": True,
    "entities": False, "pending": True, "reviewed": False, "limits": False,
}
FINANCE_DEFAULT_OPEN = {
    "filter": True, "stored": True, "monthly": True,
    "categories": False, "details": False, "imports": False, "csv": False,
}
ENTITY_TABS = ("overview", "history", "relations", "sources")
ER_PROVIDERS = ("native_mermaid", "schemacrawler", "tbls", "eralchemy")
COUNT_OPTIONS = tuple(range(1, 11))
FINANCE_PAGE_SIZE_OPTIONS = (25, 50, 100)


def _defaults() -> dict:
    """Match the legacy portal's saved schema without running its UI."""
    return {
        "pkb": dict(PKB_DEFAULT_OPEN),
        "entity": {"default_tab": "overview"},
        "finance": {**FINANCE_DEFAULT_OPEN, "recent_limit": 25},
        "core": {**CORE_UI_DEFAULT_OPEN, "open_limit": 5, "completed_limit": 5},
        "debug": {"er_diagram_providers": {
            name: name == "native_mermaid" for name in ER_PROVIDERS,
        }},
        "visibility": {
            "pkb": {name: True for name in PKB_DEFAULT_OPEN},
            "entity": {name: True for name in ENTITY_TABS},
            "finance": {name: True for name in FINANCE_DEFAULT_OPEN},
            "core": dict(CORE_UI_VISIBILITY_DEFAULT),
        },
    }


def _raw(path: Path) -> dict:
    """Read without erasing unknown keys or silently overwriting invalid JSON."""
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    if not isinstance(loaded, dict):
        raise ValueError("ui_preferences_not_object")
    return loaded


def _validated(raw: dict, *, strict: bool = False) -> dict:
    """Select only supported layout keys; reject invalid submitted values."""
    defaults = _defaults()

    def boolean_section(target: dict, source: object, prefix: str) -> None:
        if not isinstance(source, dict):
            if strict:
                raise ValueError(prefix + "_not_object")
            return
        if strict and (set(source) != set(target)):
            raise ValueError(prefix + "_keys_invalid")
        for key in target:
            value = source.get(key)
            if isinstance(value, bool):
                target[key] = value
            elif strict:
                raise ValueError(prefix + "_" + key + "_invalid")

    for name in ("pkb", "finance", "core"):
        candidate = raw.get(name)
        if isinstance(candidate, dict):
            boolean_section(
                {k: v for k, v in defaults[name].items() if isinstance(v, bool)},
                candidate if not strict else {
                    k: v for k, v in candidate.items() if k in defaults[name] and isinstance(defaults[name][k], bool)
                },
                name,
            )
            for key in defaults[name]:
                if isinstance(defaults[name][key], bool) and isinstance(candidate.get(key), bool):
                    defaults[name][key] = candidate[key]
        elif strict:
            raise ValueError(name + "_not_object")

    if strict:
        for section in ("pkb", "finance", "core"):
            if not isinstance(raw.get(section), dict) or set(raw[section]) != set(defaults[section]):
                raise ValueError(section + "_keys_invalid")
    if strict and set(raw) != set(defaults):
        raise ValueError("layout_keys_invalid")

    for name, field, choices in (
        ("finance", "recent_limit", FINANCE_PAGE_SIZE_OPTIONS),
        ("core", "open_limit", COUNT_OPTIONS),
        ("core", "completed_limit", COUNT_OPTIONS),
    ):
        value = (raw.get(name) or {}).get(field) if isinstance(raw.get(name), dict) else None
        if type(value) is int and value in choices:
            defaults[name][field] = value
        elif strict:
            raise ValueError(name + "_" + field + "_invalid")

    entity = raw.get("entity")
    if isinstance(entity, dict):
        value = entity.get("default_tab")
        if value in ENTITY_TABS:
            defaults["entity"]["default_tab"] = value
        elif strict:
            raise ValueError("entity_default_tab_invalid")
        elif "default_tab" not in entity:
            for old, new in (("current", "overview"), ("events", "history"),
                             ("history", "history"), ("relations", "relations")):
                if entity.get(old) is True:
                    defaults["entity"]["default_tab"] = new
                    break
        if strict and set(entity) != {"default_tab"}:
            raise ValueError("entity_keys_invalid")
    elif strict:
        raise ValueError("entity_not_object")

    visibility = raw.get("visibility")
    if isinstance(visibility, dict):
        if strict and set(visibility) != set(defaults["visibility"]):
            raise ValueError("visibility_keys_invalid")
        for section, target in defaults["visibility"].items():
            values = visibility.get(section)
            if section == "entity" and isinstance(values, dict) and not strict:
                if "overview" not in values and "sources" not in values:
                    values = {
                        "overview": values.get("current", True),
                        "relations": values.get("relations", True),
                        "history": any(values.get(key) is True for key in ("events", "history")),
                        "sources": True,
                    }
            boolean_section(target, values, "visibility_" + section)
    elif strict:
        raise ValueError("visibility_not_object")

    debug = raw.get("debug")
    if isinstance(debug, dict):
        if strict and set(debug) != {"er_diagram_providers"}:
            raise ValueError("debug_keys_invalid")
        boolean_section(defaults["debug"]["er_diagram_providers"],
                        debug.get("er_diagram_providers"), "debug_er")
    elif strict:
        raise ValueError("debug_not_object")

    visible_entity = defaults["visibility"]["entity"]
    if not any(visible_entity.values()):
        if strict:
            raise ValueError("all_entity_tabs_hidden")
        visible_entity["overview"] = True
    if not visible_entity[defaults["entity"]["default_tab"]]:
        if strict:
            raise ValueError("default_entity_tab_hidden")
        defaults["entity"]["default_tab"] = next(
            key for key in ENTITY_TABS if visible_entity[key]
        )
    return defaults


def default_layout_preferences() -> dict:
    """Return factory defaults without touching the current preferences file."""
    return _defaults()


def read_layout_preferences(path: Path | None = None) -> dict:
    """Read normalized display options, including old Entity key migration."""
    return _validated(_raw(path or preferences_path()))


def save_layout_preferences(payload: dict, path: Path | None = None) -> dict:
    """Persist only layout keys, preserving judgment, advisor and unknown settings.

    Args:
        payload: Full layout projection returned by GET, with supported edits.
        path: Isolated fixture path or the current local preferences file.

    Returns:
        Re-read normalized layout projection after atomic replacement.
    """
    validated = _validated(payload, strict=True)
    target = path or preferences_path()
    raw = _raw(target)
    raw.update(validated)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(target.name + "." + uuid4().hex + ".tmp")
    try:
        temporary.write_text(
            json.dumps(raw, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)
    return read_layout_preferences(target)
