"""Daily PKB Web UI prototype on an isolated fictional PostgreSQL DB.

This is the first user-facing PKB slice, separate from the developer Workbench.
It deliberately refuses the production DB and accepts only the existing
secretary_pkb_proto_20260927 fixture database through the dedicated writer role.

Run with: python -m pkb_proto.daily_pkb
"""
from __future__ import annotations

import os
import re
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID, uuid4

import psycopg
from fastapi import HTTPException
from nicegui import app, ui
from pydantic import BaseModel, Field

from .correction_service import correct_entity
from .ingestion_gate import InputRecord, ProposedClaim
from .query_service import ClaimQuery, query_claims
from .write_service import write_one
from .pending_service import enqueue as enqueue_pending, list_pending

DBNAME = "secretary_pkb_proto_20260927"
WRITER = "secretary_pkb_proto_writer_20260927"
HOST = "127.0.0.1"

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
        predicate = "driver_updated"
    elif "サーボ" in q or "SERVO-" in q:
        predicate = "servo_updated"
    include_history = any(word in q for word in ("履歴", "過去", "全部", "すべて", "訂正前"))
    return ClaimQuery(
        entity_id=entity_id,
        predicate=predicate,
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
        parsed = parse_write(text, set(entities))
        if parsed is None:
            result = enqueue_pending(
                db,
                input_id="daily-pkb-review-" + uuid4().hex,
                raw_text=text,
                reason="unsupported_natural_language_in_first_slice",
            )
            payload = asdict(result)
            payload["message"] = "現在の最小実装では、既知Entityへの明示的な更新／交換だけを安全に自動登録します。"
            return payload
        if parsed["status"] != "parsed":
            result = enqueue_pending(db, input_id="daily-pkb-review-" + uuid4().hex, raw_text=text, reason=parsed["reason"])
            return asdict(result)
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
            entity_mention=entity["name"],
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


def search_text(text: str) -> dict:
    with connection() as db:
        entities = _entity_map(db)
        page = query_claims(db, parse_query(text, entities))
        return _serialize_page(page)


class TextInput(BaseModel):
    text: str = Field(min_length=1, max_length=4000)


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


def _display_result(result: dict):
    status = result.get("status", "")
    color = "green" if status in ("inserted", "replayed", "corrected", "ok") else "orange"
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


@ui.page("/")
def index():
    state = {"write": None, "correction": None, "search": None}
    with ui.column().classes("w-full max-w-7xl mx-auto gap-4 p-4"):
        ui.label("Local Secretary — Personal Knowledge Base").classes("text-2xl font-bold")
        ui.label(
            "日常用PKBの最初の縦断スライス • Core/Workbenchから独立 • localhostのみ"
        ).classes("text-sm text-green-700")
        ui.label(
            "現在は架空データ専用の隔離DB secretary_pkb_proto_20260927。運用DB・実データには接続しません。"
        ).classes("text-sm text-orange-700")

        with ui.card().classes("w-full"):
            ui.label("記録").classes("text-lg font-bold")
            ui.label("例: メインPCをDRV-A3へ更新した。 / RCカーBのサーボをSERVO-X3へ交換した。").classes("text-sm")
            write_input = ui.textarea(label="自然言語で記録").classes("w-full")
            @ui.refreshable
            def write_result():
                if state["write"]:
                    _display_result(state["write"])
                else:
                    ui.label("まだ記録していません。")
            def do_write():
                try:
                    state["write"] = register_text(write_input.value or "")
                except Exception as exc:
                    state["write"] = {"status": "error", "reason": str(exc)}
                write_result.refresh()
                pending_panel.refresh()
            ui.button("記録する", on_click=do_write, color="green")
            write_result()

        with ui.card().classes("w-full"):
            ui.label("訂正").classes("text-lg font-bold")
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
            ui.button("訂正する", on_click=do_correct)
            correction_result()

        with ui.card().classes("w-full"):
            ui.label("検索・履歴").classes("text-lg font-bold")
            ui.label("例: サブPCのドライバー更新履歴 / RCカーBのサーボ更新").classes("text-sm")
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
                columns = [
                    {"name": "entity", "label": "対象", "field": "entity_name"},
                    {"name": "predicate", "label": "種類", "field": "predicate"},
                    {"name": "value", "label": "値", "field": "value"},
                    {"name": "valid_from", "label": "有効時点", "field": "valid_from"},
                    {"name": "status", "label": "状態", "field": "status_at_cutoff"},
                    {"name": "source", "label": "出典", "field": "source_uri"},
                ]
                ui.table(columns=columns, rows=rows, row_key="id").classes("w-full")
            def do_search():
                try:
                    state["search"] = search_text(search_input.value or "")
                except Exception as exc:
                    state["search"] = {"status": "error", "total": 0, "items": [], "reason": str(exc)}
                search_result.refresh()
            ui.button("検索する", on_click=do_search)
            search_result()

        @ui.refreshable
        def pending_panel():
            with ui.card().classes("w-full"):
                ui.label("確認待ち（Pending Claims）").classes("text-lg font-bold")
                try:
                    with connection() as db:
                        rows = list_pending(db)
                except Exception as exc:
                    ui.label("確認待ち一覧を取得できません: " + str(exc)).classes("text-red-600")
                    return
                if not rows:
                    ui.label("確認待ちはありません。")
                    return
                columns = [
                    {"name": "recorded", "label": "記録時点", "field": "recorded_at"},
                    {"name": "entity", "label": "対象", "field": "entity_name"},
                    {"name": "text", "label": "入力", "field": "raw_text"},
                    {"name": "reason", "label": "保留理由", "field": "reason"},
                ]
                rendered = []
                for row in rows:
                    item = dict(row)
                    if isinstance(item.get("recorded_at"), datetime):
                        item["recorded_at"] = item["recorded_at"].isoformat()
                    rendered.append(item)
                ui.table(columns=columns, rows=rendered, row_key="id").classes("w-full")
        pending_panel()

        with ui.expansion("この最小実装の制限"):
            ui.label("既知Entityへの明示的な更新／交換と、明示訂正だけを自動処理します。")
            ui.label("曖昧な入力・未知Entity・複数候補はPending扱い相当のreviewとして返し、勝手に登録しません。")
            ui.label("LLMによる一般的な日本語解釈、実データ、金融・給与・税務・Googleカレンダー連携はまだ未実装です。")


if __name__ == "__main__":
    ui.run(host="127.0.0.1", port=8093, reload=False, show=False, title="Local Secretary PKB")
