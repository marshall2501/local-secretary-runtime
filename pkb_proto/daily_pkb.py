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
from datetime import datetime, timezone
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
FINANCE_PAGE_SIZE_DEFAULT = 25
FINANCE_PAGE_SIZE_OPTIONS = (25, 50, 100)


def _default_ui_preferences() -> dict:
    return {
        "pkb": dict(PKB_UI_DEFAULT_OPEN),
        "entity": dict(ENTITY_UI_DEFAULT_OPEN),
        "finance": {
            **FINANCE_UI_DEFAULT_OPEN,
            "recent_limit": FINANCE_PAGE_SIZE_DEFAULT,
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


def scope_core_request(text: str, entities: dict[str, dict]) -> dict:
    """Fail closed unless this first Core slice can safely scope a PKB read."""
    q = text.strip()
    if not q:
        return {
            "status": "question",
            "question": "何を確認したいか入力してください。",
            "reason": "empty_request",
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
                        "Return bounded PKB evidence with provenance or ask for clarification.",
                        Jsonb({"pkb_read": True, "external_actions": False}),
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

                # query_claims deliberately opens its own REPEATABLE READ,
                # READ ONLY transaction for a consistent count/page snapshot.
                # Keep that Knowledge read on a separate connection so the
                # surrounding Task write transaction does not become a nested
                # transaction whose isolation level is set after prior queries.
                result = search_text(request)
                answer = core_answer(result)
                total = int(result.get("total") or 0)
                phase = "completed" if total > 0 else "awaiting_clarification"
                task_status = "completed" if total > 0 else "waiting_external"
                question = None if total > 0 else (
                    "該当記録が見つかりませんでした。対象名や確認したい項目を"
                    "もう少し具体的にしてください。"
                )

                cur.execute(
                    """INSERT INTO secretary.sources
                       (source_type, uri, citation, retrieved_at,
                        confidentiality, metadata)
                       VALUES ('tool', %s, %s, now(), 'private', %s)
                       RETURNING id""",
                    (
                        f"tool://daily-core/pkb-search/{task_id}",
                        "Secretary Core read-only PKB search result",
                        Jsonb({
                            "task_id": str(task_id),
                            "capability": "pkb_search",
                            "query": request,
                            "result_count": total,
                        }),
                    ),
                )
                source_id = cur.fetchone()[0]

                cur.execute(
                    """INSERT INTO secretary.actions
                       (task_id, actor, tool, operation, parameters, risk,
                        authorization_basis, status, idempotency_key,
                        reversible, started_at, finished_at)
                       VALUES (%s, 'daily_core', 'pkb', 'search', %s,
                               'read_only', 'localhost_read_only',
                               'succeeded', %s, true, now(), now())
                       RETURNING id""",
                    (
                        task_id,
                        Jsonb({"query": request, "bounded": True}),
                        f"daily-core:{task_id}:pkb-search:1",
                    ),
                )
                action_id = cur.fetchone()[0]

                cur.execute(
                    """INSERT INTO secretary.results
                       (action_id, source_id, outcome, summary, evidence,
                        verified_by, verified_at)
                       VALUES (%s, %s, %s, %s, %s,
                               'deterministic_pkb_query', now())
                       RETURNING id""",
                    (
                        action_id,
                        source_id,
                        "success" if total > 0 else "inconclusive",
                        answer,
                        Jsonb({
                            "result_kind": result.get("result_kind"),
                            "total": total,
                            "items": (result.get("items") or [])[:20],
                        }),
                    ),
                )
                result_id = cur.fetchone()[0]

                final_checkpoint = {
                    "core_slice": "daily_read_only_v1",
                    "phase": phase,
                    "selected_capability": "pkb_search",
                    "action_id": str(action_id),
                    "result_id": str(result_id),
                    "result_count": total,
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
                        Jsonb({"capability": "pkb_search", "result_count": total}),
                    ),
                )

                return {
                    "task_id": str(task_id),
                    "status": task_status,
                    "phase": phase,
                    "message": answer,
                    "question": question,
                    "selected_capability": "pkb_search",
                    "search": result,
                }


class TextInput(BaseModel):
    text: str = Field(min_length=1, max_length=4000)


class PendingDecisionInput(BaseModel):
    decision: str = Field(pattern="^(rejected|needs_edit)$")


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
    state = {"result": None, "busy": False}

    with ui.column().classes("w-full max-w-5xl mx-auto gap-4 p-4"):
        _portal_header(
            "Secretary Core",
            "最小縦断: 依頼 → Task → 能力選択 → PKB読取 → Result / 追加質問",
        )
        ui.label(
            "現在は架空隔離DBの読み取りだけを扱います。"
            "外部操作・承認・Web調査・自動再開はまだ実行しません。"
        ).classes("text-sm text-orange-700")

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

            run_button = ui.button(
                "依頼する",
                icon="play_arrow",
                color="blue-grey",
                on_click=submit_core,
            )
            core_result()

        with ui.card().classes("w-full"):
            ui.label("この縦断でまだ行わないこと").classes("font-bold")
            ui.label(
                "追加質問への同一Task再開、Web調査、外部Tool実行、承認、"
                "結果検証による再計画は次の拡張対象です。"
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
    finance_labels = {
        "filter": "家計フィルタ・検索",
        "stored": "保存済み家計",
        "monthly": "保存済み月別集計",
        "categories": "保存済みカテゴリ別支出",
        "details": "保存済み明細",
        "imports": "Import履歴 / Source",
        "csv": "MoneyForward CSV 取込",
    }

    with ui.column().classes("w-full max-w-5xl mx-auto gap-4 p-4"):
        _portal_header(
            "表示設定",
            "日常用GUIの初期表示を変更。サーバー再起動なしで保存・反映します。",
        )
        ui.label(
            "保存先はローカルの data/ui_preferences.json（Git管理外）。"
            "PKBの本人データとは分離しています。"
        ).classes("text-sm text-grey-7")

        pkb_controls = {}
        with ui.card().classes("w-full border-2 border-green-200 bg-green-50"):
            ui.label("PKB").classes("text-lg font-bold text-green-900")
            ui.label("各ブロックを最初に開いて表示するか設定します。").classes(
                "text-sm text-green-900"
            )
            for key, label in pkb_labels.items():
                pkb_controls[key] = ui.switch(
                    label,
                    value=_UI_PREFERENCES["pkb"][key],
                )

        entity_labels = {
            "current": "現在のState / Attribute",
            "relations": "Relations",
            "events": "Event履歴",
            "history": "過去のState / Attribute",
        }
        entity_controls = {}
        with ui.card().classes("w-full border-2 border-purple-200 bg-purple-50"):
            ui.label("Entity詳細").classes("text-lg font-bold text-purple-900")
            ui.label("Entity詳細画面の各ブロックの初期状態を設定します。").classes(
                "text-sm text-purple-900"
            )
            for key, label in entity_labels.items():
                entity_controls[key] = ui.switch(
                    label,
                    value=_UI_PREFERENCES["entity"][key],
                )

        finance_controls = {}
        with ui.card().classes("w-full border-2 border-blue-200 bg-blue-50"):
            ui.label("家計・資産").classes("text-lg font-bold text-blue-900")
            ui.label("各ブロックの初期状態と明細の既定件数を設定します。").classes(
                "text-sm text-blue-900"
            )
            for key, label in finance_labels.items():
                finance_controls[key] = ui.switch(
                    label,
                    value=_UI_PREFERENCES["finance"][key],
                )
            page_size_select = ui.select(
                options=list(FINANCE_PAGE_SIZE_OPTIONS),
                label="保存済み明細の既定1ページ件数",
                value=_UI_PREFERENCES["finance"]["recent_limit"],
            ).classes("min-w-64")

        ui.label(
            "「保存して反映」は保存済みの初期値を更新し、現在のPKB/家計の"
            "アコーディオン状態もその値へそろえます。"
        ).classes("text-sm text-grey-7")

        def collect_preferences() -> dict:
            return {
                "pkb": {
                    key: bool(control.value)
                    for key, control in pkb_controls.items()
                },
                "entity": {
                    key: bool(control.value)
                    for key, control in entity_controls.items()
                },
                "finance": {
                    **{
                        key: bool(control.value)
                        for key, control in finance_controls.items()
                    },
                    "recent_limit": int(
                        page_size_select.value or FINANCE_PAGE_SIZE_DEFAULT
                    ),
                },
            }

        def save_and_apply():
            try:
                saved = save_ui_preferences(collect_preferences())
                _apply_ui_preferences(saved)
                ui.notify(
                    "表示設定を保存し、現在のセッションにも反映しました",
                    type="positive",
                )
            except Exception as exc:
                ui.notify("表示設定を保存できません: " + str(exc)[:220], type="negative")

        def restore_builtin():
            defaults = _default_ui_preferences()
            for key, control in pkb_controls.items():
                control.value = defaults["pkb"][key]
            for key, control in entity_controls.items():
                control.value = defaults["entity"][key]
            for key, control in finance_controls.items():
                control.value = defaults["finance"][key]
            page_size_select.value = defaults["finance"]["recent_limit"]
            try:
                saved = save_ui_preferences(defaults)
                _apply_ui_preferences(saved)
                ui.notify("初期値へ戻して保存・反映しました", type="positive")
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
            ).classes("w-full border-2 border-green-300 bg-green-50 text-green-900"):
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
            "w-full border-2 border-amber-300 bg-amber-50 text-amber-900"
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
            "w-full border-2 border-indigo-300 bg-indigo-50 text-indigo-900"
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
                    "w-full border-2 border-purple-500 bg-purple-50 text-purple-900"
                ):
                    ui.label("確認待ち一覧を取得できません: " + str(exc)).classes("text-red-600")
                return

            title = f"確認待ち（Pending Claims） {len(rows)}件"
            with ui.expansion(
                title,
                value=_PKB_UI_OPEN["pending"],
                on_value_change=remember_expansion("pending"),
            ).classes(
                "w-full border-2 border-purple-500 bg-purple-50 text-purple-900"
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
            ):
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
        ):
            ui.label("限定文型は決定的に処理し、それ以外はローカルLLMで単一の構造化候補化を試みます。")
            ui.label("LLM由来候補・曖昧入力・未知Entity・複数候補はPendingへ回し、勝手に正式Claimへ登録しません。")
            ui.label("LLM解釈は現在driver_updated / servo_updatedの単一候補だけ。実データ、金融・給与・税務・Googleカレンダー連携は未実装です。")


if __name__ == "__main__":
    ui.run(host="127.0.0.1", port=8093, reload=False, show=False, title="Local Secretary PKB")
