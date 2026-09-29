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
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID, uuid4

import psycopg
from psycopg.types.json import Jsonb
from fastapi import HTTPException
from nicegui import app, run, ui
from pydantic import BaseModel, Field

from .correction_service import correct_entity
from .daily_interpreter import interpret as interpret_daily
from .entity_model_service import load_entity_detail, list_components, resolve_component_reference
from .finance_preview import analyze_moneyforward_csv
from .finance_import import (
    commit_import,
    finance_filter_options,
    load_finance_dashboard,
    plan_import,
)
from .ingestion_gate import InputRecord, ProposedClaim
from .query_service import ClaimQuery, query_claims
from .write_service import write_one
from .web_research import research_web
from .pending_service import (accept_pending, acceptance_eligible, enqueue as enqueue_pending,
    list_pending, list_reviewed, review_pending)

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
ENTITY_UI_DEFAULT_OPEN = {
    "current": True,
    "relations": True,
    "events": True,
    "history": False,
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

UI_VISIBILITY_DEFAULT = {
    "pkb": {key: True for key in PKB_UI_DEFAULT_OPEN},
    "entity": {key: True for key in ENTITY_UI_DEFAULT_OPEN},
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
        "entity": dict(ENTITY_UI_DEFAULT_OPEN),
        "finance": {
            **FINANCE_UI_DEFAULT_OPEN,
            "recent_limit": FINANCE_PAGE_SIZE_DEFAULT,
        },
        "core": dict(CORE_UI_DEFAULT_OPEN),
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

    entity = raw.get("entity")
    if isinstance(entity, dict):
        for key in ENTITY_UI_DEFAULT_OPEN:
            value = entity.get(key)
            if isinstance(value, bool):
                result["entity"][key] = value

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

    visibility = raw.get("visibility")
    if isinstance(visibility, dict):
        for section, defaults in UI_VISIBILITY_DEFAULT.items():
            section_values = visibility.get(section)
            if not isinstance(section_values, dict):
                continue
            for key in defaults:
                value = section_values.get(key)
                if isinstance(value, bool):
                    result["visibility"][section][key] = value
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
_ENTITY_UI_OPEN = dict(_UI_PREFERENCES["entity"])
_FINANCE_UI_OPEN = {
    key: _UI_PREFERENCES["finance"][key]
    for key in FINANCE_UI_DEFAULT_OPEN
}
_CORE_UI_OPEN = dict(_UI_PREFERENCES["core"])


def _apply_ui_preferences(preferences: dict) -> None:
    global _UI_PREFERENCES
    validated = _validate_ui_preferences(preferences)
    _UI_PREFERENCES = validated
    _PKB_UI_OPEN.clear()
    _PKB_UI_OPEN.update(validated["pkb"])
    _ENTITY_UI_OPEN.clear()
    _ENTITY_UI_OPEN.update(validated["entity"])
    _FINANCE_UI_OPEN.clear()
    _FINANCE_UI_OPEN.update(
        {key: validated["finance"][key] for key in FINANCE_UI_DEFAULT_OPEN}
    )
    _CORE_UI_OPEN.clear()
    _CORE_UI_OPEN.update(validated["core"])


def _set_pkb_ui_open(key: str, value: bool) -> None:
    if key not in PKB_UI_DEFAULT_OPEN:
        raise KeyError("unknown PKB accordion key")
    _PKB_UI_OPEN[key] = bool(value)


def _set_entity_ui_open(key: str, value: bool) -> None:
    if key not in ENTITY_UI_DEFAULT_OPEN:
        raise KeyError("unknown Entity accordion key")
    _ENTITY_UI_OPEN[key] = bool(value)


def _set_finance_ui_open(key: str, value: bool) -> None:
    if key not in FINANCE_UI_DEFAULT_OPEN:
        raise KeyError("unknown finance accordion key")
    _FINANCE_UI_OPEN[key] = bool(value)


def _set_core_ui_open(key: str, value: bool) -> None:
    if key not in CORE_UI_DEFAULT_OPEN:
        raise KeyError("unknown Core accordion key")
    _CORE_UI_OPEN[key] = bool(value)


def _block_visibility_class(section: str, key: str) -> str:
    visible = _UI_PREFERENCES["visibility"][section][key]
    return "" if visible else " hidden"


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
                        "version_candidates": hit.get("version_candidates"),
                        "version_facts": hit.get("version_facts"),
                        "date_hints": hit.get("date_hints"),
                    }
                    for hit in (result.get("hits") or [])
                ],
            },
        }
    raise ValueError("Unsupported Core read capability")


def scope_core_request(text: str, entities: dict[str, dict]) -> dict:
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


def run_core_request(text: str) -> dict:
    """First daily Secretary Core slice: Task -> bounded PKB read -> Result."""
    request = text.strip()
    if not request:
        return {
            "status": "rejected",
            "phase": "input",
            "message": "依頼を入力してください。",
        }

    task_id = uuid4()
    with connection() as db:
        with db.transaction():
            entities = _entity_map(db)
            scoped = scope_core_request(request, entities)
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
                        (task_id, task_id, Jsonb({"reason": scoped["reason"]})),
                    )
                    return {
                        "task_id": str(task_id),
                        "status": "waiting_external",
                        "phase": "awaiting_clarification",
                        "message": "追加情報が必要です。",
                        "question": scoped["question"],
                    }

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
                }


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


def load_recent_core_tasks(limit: int = 10) -> list[dict]:
    """Load a compact screen-wide Core activity view for debugging."""
    if type(limit) is not int or not 1 <= limit <= 50:
        raise ValueError("limit must be 1..50")
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
                     AND COALESCE(t.checkpoint->>'core_slice', '')='daily_read_only_v1'
                   GROUP BY t.id
                   ORDER BY t.updated_at DESC, t.id DESC
                   LIMIT %s""",
                (limit,),
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
            "phase": checkpoint.get("phase"),
            "selected_capability": checkpoint.get("selected_capability"),
            "action_count": row[8],
            "result_count": row[9],
        })
    return result


def load_open_core_tasks(limit: int = 20) -> list[dict]:
    """Load unfinished daily Core tasks so they can survive page/server restarts."""
    if type(limit) is not int or not 1 <= limit <= 50:
        raise ValueError("limit must be 1..50")
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
                     AND COALESCE(t.checkpoint->>'core_slice', '')='daily_read_only_v1'
                     AND t.status IN ('waiting_external', 'running')
                   GROUP BY t.id
                   ORDER BY t.updated_at DESC, t.id DESC
                   LIMIT %s""",
                (limit,),
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
            "phase": checkpoint.get("phase"),
            "selected_capability": checkpoint.get("selected_capability"),
            "question": checkpoint.get("question"),
            "effective_request": checkpoint.get("effective_request"),
            "user_replies": list(checkpoint.get("user_replies") or []),
            "action_count": row[7],
            "result_count": row[8],
        })
    return result


def core_task_selection_result(item: dict) -> dict:
    """Convert one persisted Task summary into the same UI state as a live request."""
    task_id = str(item.get("id") or "").strip()
    status = str(item.get("status") or "").strip()
    if not task_id:
        raise ValueError("Task ID is required")
    if status not in {"waiting_external", "running", "completed", "failed"}:
        raise ValueError("Unsupported Task status")
    return {
        "task_id": task_id,
        "status": status,
        "phase": item.get("phase"),
        "selected_capability": item.get("selected_capability"),
        "question": item.get("question"),
        "message": "保存済みTaskを選択しました。",
        "effective_request": item.get("effective_request"),
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
            "phase": checkpoint.get("phase"),
            "selected_capability": checkpoint.get("selected_capability"),
            "effective_request": checkpoint.get("effective_request"),
            "user_replies": list(checkpoint.get("user_replies") or []),
            "result_count": checkpoint.get("result_count"),
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


class TextInput(BaseModel):
    text: str = Field(min_length=1, max_length=4000)


class PendingDecisionInput(BaseModel):
    decision: str = Field(pattern="^(rejected|needs_edit)$")


@app.get("/api/core/tasks/open")
def api_core_open_tasks():
    try:
        return {"items": load_open_core_tasks(20)}
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


_FEATURES = [
    ("Personal Knowledge Base", "利用可能", "green", "/pkb", "自然言語の記録・訂正・検索・履歴・Entity詳細・Pending"),
    ("家計・資産", "試験中", "orange", "/finance", "保存済み家計のSQL集計・明細・Import履歴とMoneyForward CSV取込"),
    ("予定", "未実装", "grey", None, "Google Calendarの閲覧・検索・PKB関連付け"),
    ("給与・税金", "未実装", "grey", None, "原本保管・抽出・照合・集計"),
    ("Secretary Core", "試験中", "blue-grey", "/core", "依頼をTask化し、最小のPKB読取能力を選択・記録"),
    ("開発Workbench", "利用可能", "green", "http://127.0.0.1:8092/", "LLM/PKB/Coreの開発検証用。日常GUIとは分離"),
]


def _nav():
    with ui.row().classes("w-full items-center gap-2 mb-2"):
        ui.button("TOP", icon="home").props("flat href=/ tag=a")
        ui.button("機能一覧", icon="apps").props("flat href=/features tag=a")
        ui.button("PKB", icon="account_tree").props("flat href=/pkb tag=a")
        ui.button("Core", icon="hub").props("flat href=/core tag=a")
        ui.button("家計・資産", icon="account_balance_wallet").props("flat href=/finance tag=a")
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
                ui.label("Secretary Core").classes("text-lg font-bold")
                ui.badge("試験中", color="blue-grey")
                ui.label("依頼をTask化し、PKBの読取能力を選んで結果を記録").classes("text-sm")
                ui.button("Coreを開く", icon="arrow_forward", color="blue-grey").props(
                    "href=/core tag=a"
                )

        with ui.card().classes("w-full"):
            ui.label("開発中の現在地").classes("text-lg font-bold")
            ui.label("PKB: 架空隔離DBでEntity / Relation / Event / State縦断まで実機確認済み")
            ui.label("家計: MoneyForward CSV 2,270件を隔離DBへ保存し、保存済み表示へ拡張")
            ui.label("金融実データは隔離DBのみ。運用DB・外部金融サービス操作はまだ行いません。").classes(
                "text-sm text-orange-800"
            )


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


@ui.page("/core")
def core_page():
    state = {"result": None, "busy": False, "resume_busy": False}

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
        _portal_header(
            "Secretary Core",
            "最小縦断: 依頼 → Task → 能力選択 → PKB読取 → Result / 追加質問",
        )
        ui.label(
            "現在は架空隔離DBの読み取りだけを扱います。"
            "外部操作・承認・Web調査・自動再開はまだ実行しません。"
        ).classes("text-sm text-orange-700")

        def select_saved_task(item: dict):
            state["result"] = core_task_selection_result(item)
            core_result.refresh()
            resume_panel.refresh()
            trace_panel.refresh()

        @ui.refreshable
        def open_tasks_panel():
            try:
                rows = load_open_core_tasks(20)
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
            with ui.column().classes("w-full gap-3"):
                ui.label("Core Tasks").classes("text-lg font-bold")
                open_tasks_panel()

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
                try:
                    trace = load_core_task_trace(UUID(task_id))
                except Exception as exc:
                    with ui.expansion(
                        "Task検証・稼働ログ",
                        value=_CORE_UI_OPEN["trace"],
                        on_value_change=remember_core_expansion("trace"),
                    ).classes(
                        "w-full border border-red-200 bg-red-50"
                        + _block_visibility_class("core", "trace")
                    ):
                        ui.label("Traceを取得できません: " + str(exc)).classes(
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
                        if state["resume_busy"]:
                            return
                        state["resume_busy"] = True
                        resume_button.disable()
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
                            resume_panel.refresh()
                            core_result.refresh()
                            trace_panel.refresh()
                            open_tasks_panel.refresh()
                            screen_log_panel.refresh()

                    resume_button = ui.button(
                        "同じTaskを再開",
                        icon="resume",
                        color="orange",
                        on_click=submit_resume,
                    )

            async def submit_core():
                if state["busy"]:
                    return
                state["busy"] = True
                run_button.disable()
                core_result.refresh()
                try:
                    state["result"] = await run.io_bound(
                        run_core_request, request_input.value or ""
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
                    core_result.refresh()
                    resume_panel.refresh()
                    trace_panel.refresh()
                    open_tasks_panel.refresh()
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

        screen_log_panel()

        with ui.card().classes(
            "w-full" + _block_visibility_class("core", "limits")
        ):
            ui.label("この縦断でまだ行わないこと").classes("font-bold")
            ui.label(
                "Web調査、外部Tool実行、承認、結果検証による再計画は"
                "次の拡張対象です。複数Taskの並行保持と、保存済みwaiting_external "
                "Taskの手動選択・同一Task再開をこの画面で試験します。"
            ).classes("text-sm")


@ui.page("/settings")
def settings_page():
    pkb_labels = {
        "write": "記録",
        "correction": "訂正",
        "search": "検索・履歴",
        "entities": "Entity一覧",
        "pending": "確認待ち（Pending Claims）",
        "reviewed": "処理済みの確認待ち",
        "limits": "この最小実装の制限",
    }
    entity_labels = {
        "current": "現在のState / Attribute",
        "relations": "Relations",
        "events": "Event履歴",
        "history": "過去のState / Attribute",
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
            "表示設定",
            "日常用GUIの表示・初期展開を変更。サーバー再起動は不要です。",
        )
        ui.label(
            "「表示」はブロック自体の表示/非表示、「初期展開」は表示する"
            "アコーディオンを最初から開くかを設定します。"
        ).classes("text-sm text-grey-7")
        ui.label(
            "保存先はローカルの data/ui_preferences.json（Git管理外）。"
            "PKBの本人データとは分離しています。"
        ).classes("text-sm text-grey-7")

        pkb_open_controls = {}
        pkb_visible_controls = {}
        with ui.card().classes("w-full border-2 border-green-200 bg-green-50"):
            ui.label("PKB").classes("text-lg font-bold text-green-900")
            for key, label in pkb_labels.items():
                with ui.row().classes("w-full items-center gap-4"):
                    ui.label(label).classes("grow")
                    pkb_visible_controls[key] = ui.switch(
                        "表示",
                        value=_UI_PREFERENCES["visibility"]["pkb"][key],
                    )
                    pkb_open_controls[key] = ui.switch(
                        "初期展開",
                        value=_UI_PREFERENCES["pkb"][key],
                    )

        entity_open_controls = {}
        entity_visible_controls = {}
        with ui.card().classes("w-full border-2 border-purple-200 bg-purple-50"):
            ui.label("Entity詳細").classes("text-lg font-bold text-purple-900")
            for key, label in entity_labels.items():
                with ui.row().classes("w-full items-center gap-4"):
                    ui.label(label).classes("grow")
                    entity_visible_controls[key] = ui.switch(
                        "表示",
                        value=_UI_PREFERENCES["visibility"]["entity"][key],
                    )
                    entity_open_controls[key] = ui.switch(
                        "初期展開",
                        value=_UI_PREFERENCES["entity"][key],
                    )

        finance_open_controls = {}
        finance_visible_controls = {}
        with ui.card().classes("w-full border-2 border-blue-200 bg-blue-50"):
            ui.label("家計・資産").classes("text-lg font-bold text-blue-900")
            for key, label in finance_labels.items():
                with ui.row().classes("w-full items-center gap-4"):
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

        core_open_controls = {}
        core_visible_controls = {}
        with ui.card().classes("w-full border-2 border-slate-200 bg-slate-50"):
            ui.label("Secretary Core").classes("text-lg font-bold")
            ui.label(
                "主操作の依頼ブロックは常時表示。検証・補足ブロックだけ非表示にできます。"
            ).classes("text-sm text-grey-7")
            for key, label in core_labels.items():
                with ui.row().classes("w-full items-center gap-4"):
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
            return {
                "pkb": {
                    key: bool(control.value)
                    for key, control in pkb_open_controls.items()
                },
                "entity": {
                    key: bool(control.value)
                    for key, control in entity_open_controls.items()
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
                    key: bool(control.value)
                    for key, control in core_open_controls.items()
                },
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
            for key, control in entity_open_controls.items():
                control.value = preferences["entity"][key]
            for key, control in entity_visible_controls.items():
                control.value = preferences["visibility"]["entity"][key]
            for key, control in finance_open_controls.items():
                control.value = preferences["finance"][key]
            for key, control in finance_visible_controls.items():
                control.value = preferences["visibility"]["finance"][key]
            for key, control in core_visible_controls.items():
                control.value = preferences["visibility"]["core"][key]
            for key, control in core_open_controls.items():
                control.value = preferences["core"][key]
            page_size_select.value = preferences["finance"]["recent_limit"]

        def save_and_apply():
            try:
                saved = save_ui_preferences(collect_preferences())
                _apply_ui_preferences(saved)
                ui.notify(
                    "表示設定を保存しました。各画面を開き直すと反映されます",
                    type="positive",
                )
            except Exception as exc:
                ui.notify("表示設定を保存できません: " + str(exc)[:220], type="negative")

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
    def remember_expansion(key: str):
        def _remember(event):
            _set_entity_ui_open(key, event.value)
        return _remember

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
            "Entity / Current State / Relation / Event / Source を共通形式で表示",
        )
        ui.button("PKBへ戻る", icon="arrow_back").props("flat href=/pkb tag=a")

        if error:
            with ui.card().classes("w-full border-2 border-red-300 bg-red-50"):
                ui.label("Entity詳細を読み取れません: " + error).classes("text-red-800")
            return
        if detail is None:
            with ui.card().classes("w-full border-2 border-grey-300"):
                ui.label("Entityが見つかりません。")
            return

        entity = detail["entity"]
        with ui.card().classes("w-full border-2 border-blue-300 bg-blue-50"):
            ui.label(entity["name"]).classes("text-2xl font-bold text-blue-900")
            with ui.row().classes("w-full gap-3 flex-wrap"):
                for label, value in (
                    ("Domain", entity["domain"]),
                    ("Type", entity["entity_type"]),
                    ("Entity ID", entity["id"]),
                ):
                    with ui.card().classes("min-w-48"):
                        ui.label(label).classes("text-xs text-grey-7")
                        ui.label(str(value)).classes(
                            "font-mono text-sm" if label == "Entity ID" else "text-base font-medium"
                        )

        with ui.expansion(
            "現在のState / Attribute",
            value=_ENTITY_UI_OPEN["current"],
            on_value_change=remember_expansion("current"),
        ).classes(
            "w-full border-2 border-green-300 bg-green-50 text-green-900"
            + _block_visibility_class("entity", "current")
        ):
            rows = detail["current"]
            if not rows:
                ui.label("現在値として表示できるState / Attributeはありません。")
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
                        {"name": "kind", "label": "意味", "field": "semantic_kind", "align": "left"},
                        {"name": "predicate", "label": "項目", "field": "predicate", "align": "left"},
                        {"name": "value", "label": "現在値", "field": "value_display", "align": "left"},
                        {"name": "since", "label": "開始", "field": "valid_from_display", "align": "left"},
                        {"name": "source", "label": "Source", "field": "source_uri", "align": "left"},
                    ],
                    rows=display,
                    row_key="id",
                ).props("dense flat").classes("w-full")

        with ui.expansion(
            "Relations",
            value=_ENTITY_UI_OPEN["relations"],
            on_value_change=remember_expansion("relations"),
        ).classes(
            "w-full border-2 border-purple-300 bg-purple-50 text-purple-900"
            + _block_visibility_class("entity", "relations")
        ):
            relations = detail["relations"]
            if not relations:
                ui.label("現在または履歴Relationはありません。")
            else:
                for rel in relations:
                    arrow = "→" if rel["direction"] == "outgoing" else "←"
                    role = f" / role={rel['relation_role']}" if rel["relation_role"] else ""
                    with ui.row().classes(
                        "w-full items-center gap-3 border-b border-purple-200 py-2"
                    ):
                        ui.label(arrow).classes("text-lg font-bold")
                        with ui.column().classes("grow gap-0"):
                            ui.label(
                                f"{rel['predicate']}{role} / {rel['other_entity_name']}"
                            ).classes("font-medium")
                            ui.label(
                                f"{rel['other_entity_type']} / from {rel['valid_from']}"
                            ).classes("text-xs text-grey-7")
                        ui.button("詳細", icon="open_in_new").props(
                            f"flat href=/entity/{rel['other_entity_id']} tag=a"
                        )

        with ui.expansion(
            "Event履歴",
            value=_ENTITY_UI_OPEN["events"],
            on_value_change=remember_expansion("events"),
        ).classes(
            "w-full border-2 border-orange-300 bg-orange-50 text-orange-900"
            + _block_visibility_class("entity", "events")
        ):
            rows = detail["events"]
            if not rows:
                ui.label("Event履歴はありません。")
            else:
                display = [
                    {
                        **row,
                        "value_display": str(row["value"]),
                        "when_display": str(row["valid_from"]),
                    }
                    for row in rows
                ]
                ui.table(
                    columns=[
                        {"name": "when", "label": "時点", "field": "when_display", "align": "left"},
                        {"name": "predicate", "label": "Event", "field": "predicate", "align": "left"},
                        {"name": "value", "label": "値", "field": "value_display", "align": "left"},
                        {"name": "source", "label": "Source", "field": "source_uri", "align": "left"},
                    ],
                    rows=display,
                    row_key="id",
                ).props("dense flat").classes("w-full")

        with ui.expansion(
            "過去のState / Attribute",
            value=_ENTITY_UI_OPEN["history"],
            on_value_change=remember_expansion("history"),
        ).classes(
            "w-full border-2 border-grey-300 bg-grey-1"
            + _block_visibility_class("entity", "history")
        ):
            rows = detail["history"]
            if not rows:
                ui.label("終了済みのState / Attributeはありません。")
            else:
                display = [
                    {
                        **row,
                        "value_display": str(row["value"]),
                        "period_display": f"{row['valid_from']} ～ {row['valid_to']}",
                    }
                    for row in rows
                ]
                ui.table(
                    columns=[
                        {"name": "kind", "label": "意味", "field": "semantic_kind", "align": "left"},
                        {"name": "predicate", "label": "項目", "field": "predicate", "align": "left"},
                        {"name": "value", "label": "値", "field": "value_display", "align": "left"},
                        {"name": "period", "label": "有効期間", "field": "period_display", "align": "left"},
                        {"name": "source", "label": "Source", "field": "source_uri", "align": "left"},
                    ],
                    rows=display,
                    row_key="id",
                ).props("dense flat").classes("w-full")


@ui.page("/pkb")
def pkb_page():
    state = {"write": None, "write_busy": False, "correction": None, "search": None}

    def remember_expansion(key: str):
        def _remember(event):
            _set_pkb_ui_open(key, event.value)
        return _remember
    with ui.column().classes("w-full max-w-7xl mx-auto gap-4 p-4"):
        _portal_header(
            "Local Secretary — Personal Knowledge Base",
            "日常用PKB • Core/Workbenchから独立 • localhostのみ",
        )
        ui.label(
            "現在は架空データ専用の隔離DB secretary_pkb_proto_20260927。運用DB・実データには接続しません。"
        ).classes("text-sm text-orange-700")

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

        @ui.refreshable
        def pending_panel():
            try:
                with connection() as db:
                    rows = list_pending(db)
            except Exception as exc:
                with ui.expansion(
                    "確認待ち（Pending Claims）",
                    value=_PKB_UI_OPEN["pending"],
                    on_value_change=remember_expansion("pending"),
                ).classes(
                    "w-full border-2 border-purple-500 bg-purple-50 text-purple-900" + _block_visibility_class("pkb", "pending")
                ):
                    ui.label("確認待ち一覧を取得できません: " + str(exc)).classes("text-red-600")
                return

            title = f"確認待ち（Pending Claims） {len(rows)}件"
            with ui.expansion(
                title,
                value=_PKB_UI_OPEN["pending"],
                on_value_change=remember_expansion("pending"),
            ).classes(
                "w-full border-2 border-purple-500 bg-purple-50 text-purple-900" + _block_visibility_class("pkb", "pending")
            ):
                if not rows:
                    ui.label("確認待ちはありません。")
                    return

                def decide(pending_id: str, decision: str):
                    try:
                        with connection() as db:
                            result = review_pending(db, pending_id, decision)
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
                            ui.notify("この候補は承認できません: " + result.reason, type="warning")
                        pending_panel.refresh()
                        reviewed_panel.refresh()
                    except Exception as exc:
                        ui.notify(str(exc)[:240], type="negative")

                for row in rows:
                    pending_id = str(row["id"])
                    when = row["recorded_at"].isoformat() if isinstance(row["recorded_at"], datetime) else str(row["recorded_at"])
                    with ui.row().classes("w-full items-center gap-3 border-b border-purple-200 py-2"):
                        with ui.column().classes("grow gap-1"):
                            ui.label(row["raw_text"]).classes("font-medium")
                            ui.label("保留理由: " + row["reason"]).classes("text-sm text-purple-900")
                            meta = when
                            if row.get("entity_name"):
                                meta += " / " + row["entity_name"]
                            if row.get("interpreter_model"):
                                meta += " / 解釈: " + row["interpreter_model"]
                            ui.label(meta).classes("text-xs text-gray-600")
                        if acceptance_eligible(row):
                            ui.button(
                                "承認",
                                on_click=lambda pid=pending_id: accept(pid),
                                color="green",
                            ).props("outline")
                        ui.button(
                            "要修正",
                            on_click=lambda pid=pending_id: decide(pid, "needs_edit"),
                            color="orange",
                        ).props("outline")
                        ui.button(
                            "却下",
                            on_click=lambda pid=pending_id: decide(pid, "rejected"),
                            color="red",
                        ).props("outline")
        pending_panel()

        @ui.refreshable
        def reviewed_panel():
            with ui.expansion(
                "処理済みの確認待ち",
                value=_PKB_UI_OPEN["reviewed"],
                on_value_change=remember_expansion("reviewed"),
            ).classes(_block_visibility_class("pkb", "reviewed")):
                try:
                    with connection() as db:
                        rows = list_reviewed(db)
                except Exception as exc:
                    ui.label("処理履歴を取得できません: " + str(exc)).classes("text-red-600")
                    return
                if not rows:
                    ui.label("処理済みの項目はまだありません。")
                    return
                for row in rows:
                    status = row["review_status"]
                    label = "承認" if status == "accepted" else "要修正" if status == "needs_edit" else "却下" if status == "rejected" else status
                    when = row["reviewed_at"].isoformat() if isinstance(row.get("reviewed_at"), datetime) else str(row.get("reviewed_at") or "")
                    with ui.row().classes("w-full items-center gap-3 border-b py-2"):
                        ui.badge(label, color="green" if status == "accepted" else "orange" if status == "needs_edit" else "red" if status == "rejected" else "grey")
                        with ui.column().classes("grow gap-1"):
                            ui.label(row["raw_text"]).classes("font-medium")
                            ui.label("理由: " + row["reason"]).classes("text-sm")
                            ui.label("処理時点: " + when).classes("text-xs text-gray-600")
        reviewed_panel()

        with ui.expansion(
            "この最小実装の制限",
            value=_PKB_UI_OPEN["limits"],
            on_value_change=remember_expansion("limits"),
        ).classes(_block_visibility_class("pkb", "limits")):
            ui.label("限定文型は決定的に処理し、それ以外はローカルLLMで単一の構造化候補化を試みます。")
            ui.label("LLM由来候補・曖昧入力・未知Entity・複数候補はPendingへ回し、勝手に正式Claimへ登録しません。")
            ui.label("LLM解釈は現在driver_updated / servo_updatedの単一候補だけ。実データ、金融・給与・税務・Googleカレンダー連携は未実装です。")


if __name__ == "__main__":
    ui.run(host="127.0.0.1", port=8093, reload=False, show=False, title="Local Secretary PKB")
