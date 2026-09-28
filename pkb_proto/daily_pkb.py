"""Daily PKB Web UI prototype on an isolated fictional PostgreSQL DB.

This is the first user-facing PKB slice, separate from the developer Workbench.
It deliberately refuses the production DB and accepts only the existing
secretary_pkb_proto_20260927 fixture database through the dedicated writer role.

Run with: python -m pkb_proto.daily_pkb
"""
from __future__ import annotations

import inspect
import os
import re
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID, uuid4

import psycopg
from fastapi import HTTPException
from nicegui import app, run, ui
from pydantic import BaseModel, Field

from .correction_service import correct_entity
from .daily_interpreter import interpret as interpret_daily
from .entity_model_service import list_components, resolve_component_reference
from .finance_preview import analyze_moneyforward_csv
from .ingestion_gate import InputRecord, ProposedClaim
from .query_service import ClaimQuery, query_claims
from .write_service import write_one
from .pending_service import (accept_pending, acceptance_eligible, enqueue as enqueue_pending,
    list_pending, list_reviewed, review_pending)

DBNAME = "secretary_pkb_proto_20260927"
WRITER = "secretary_pkb_proto_writer_20260927"
HOST = "127.0.0.1"

# UI color semantics: green=create/confirm, blue=read/search, orange=edit/review, red=reject/destructive.

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


def search_text(text: str) -> dict:
    with connection() as db:
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


class TextInput(BaseModel):
    text: str = Field(min_length=1, max_length=4000)


class PendingDecisionInput(BaseModel):
    decision: str = Field(pattern="^(rejected|needs_edit)$")


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
    ("Personal Knowledge Base", "利用可能", "green", "/pkb", "自然言語の記録・訂正・検索・履歴・Pending"),
    ("家計・資産", "試験中", "orange", "/finance", "MoneyForward CSVの読み取り専用プレビュー"),
    ("予定", "未実装", "grey", None, "Google Calendarの閲覧・検索・PKB関連付け"),
    ("給与・税金", "未実装", "grey", None, "原本保管・抽出・照合・集計"),
    ("Secretary Core", "開発予定", "blue-grey", None, "依頼・計画・承認・実行・継続"),
    ("開発Workbench", "利用可能", "green", "http://127.0.0.1:8092/", "LLM/PKB/Coreの開発検証用。日常GUIとは分離"),
]


def _nav():
    with ui.row().classes("w-full items-center gap-2 mb-2"):
        ui.button("TOP", icon="home").props("flat href=/ tag=a")
        ui.button("機能一覧", icon="apps").props("flat href=/features tag=a")
        ui.button("PKB", icon="account_tree").props("flat href=/pkb tag=a")
        ui.button("家計・資産", icon="account_balance_wallet").props("flat href=/finance tag=a")


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
                ui.label("MoneyForward CSVプレビューを実装中").classes("text-sm")
                ui.label("実データはDBへ保存せず、まず構造と集計を確認").classes(
                    "text-xs text-blue-800"
                )
                ui.button("家計・資産を開く", icon="arrow_forward", color="blue").props(
                    "href=/finance tag=a"
                )
            with ui.card().classes("w-72 border-2 border-grey-300 bg-grey-1"):
                ui.label("予定").classes("text-lg font-bold")
                ui.badge("未実装", color="grey")
                ui.label("Google Calendar閲覧・検索を予定").classes("text-sm")
            with ui.card().classes("w-72 border-2 border-grey-300 bg-grey-1"):
                ui.label("Secretary Core").classes("text-lg font-bold")
                ui.badge("開発予定", color="blue-grey")
                ui.label("依頼・計画・承認・継続を同じGUIへ追加予定").classes("text-sm")

        with ui.card().classes("w-full"):
            ui.label("開発中の現在地").classes("text-lg font-bold")
            ui.label("PKB: 架空隔離DBでEntity / Relation / Event / State縦断まで実機確認済み")
            ui.label("家計: MoneyForward CSVの読み取り専用プレビューを開始")
            ui.label("実データのDB投入・外部金融サービス操作はまだ行いません。").classes(
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


@ui.page("/finance")
def finance_page():
    state = {"preview": None, "error": None}

    with ui.column().classes("w-full max-w-7xl mx-auto gap-4 p-4"):
        _portal_header(
            "家計・資産",
            "最初の縦断: MoneyForward ME CSVを読み取り専用で解析",
        )
        with ui.card().classes("w-full border-2 border-blue-300 bg-blue-50"):
            ui.label("MoneyForward CSV プレビュー").classes("text-lg font-bold text-blue-900")
            ui.label(
                "CSVはこのローカルWebプロセスのメモリ上で解析します。"
                "PostgreSQLへ保存せず、LLMにも送信しません。"
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

                ui.label("月別集計").classes("text-lg font-bold mt-2")
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

                ui.label("支出カテゴリ上位").classes("text-lg font-bold mt-2")
                ui.table(
                    columns=[
                        {"name": "major", "label": "大項目", "field": "major"},
                        {"name": "minor", "label": "中項目", "field": "minor"},
                        {"name": "expense", "label": "支出", "field": "expense"},
                    ],
                    rows=preview.categories,
                    row_key="minor",
                ).classes("w-full")

                with ui.expansion("直近明細（最大100件）", value=False).classes("w-full"):
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

            async def handle_finance_upload(event):
                try:
                    filename, data = await _uploaded_bytes(event)
                    state["preview"] = analyze_moneyforward_csv(data, filename)
                    state["error"] = None
                    ui.notify(
                        f"{filename}: {state['preview'].row_count:,}件を読み取り専用で解析しました",
                        type="positive",
                    )
                except Exception as exc:
                    state["preview"] = None
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


@ui.page("/pkb")
def pkb_page():
    state = {"write": None, "write_busy": False, "correction": None, "search": None}
    with ui.column().classes("w-full max-w-7xl mx-auto gap-4 p-4"):
        _portal_header(
            "Local Secretary — Personal Knowledge Base",
            "日常用PKB • Core/Workbenchから独立 • localhostのみ",
        )
        ui.label(
            "現在は架空データ専用の隔離DB secretary_pkb_proto_20260927。運用DB・実データには接続しません。"
        ).classes("text-sm text-orange-700")

        with ui.expansion("記録", value=True).classes(
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

        with ui.expansion("訂正", value=False).classes(
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

        with ui.expansion("検索・履歴", value=True).classes(
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

        @ui.refreshable
        def pending_panel():
            try:
                with connection() as db:
                    rows = list_pending(db)
            except Exception as exc:
                with ui.expansion("確認待ち（Pending Claims）", value=True).classes(
                    "w-full border-2 border-purple-500 bg-purple-50 text-purple-900"
                ):
                    ui.label("確認待ち一覧を取得できません: " + str(exc)).classes("text-red-600")
                return

            title = f"確認待ち（Pending Claims） {len(rows)}件"
            with ui.expansion(title, value=True).classes(
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
            with ui.expansion("処理済みの確認待ち", value=False):
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

        with ui.expansion("この最小実装の制限", value=False):
            ui.label("限定文型は決定的に処理し、それ以外はローカルLLMで単一の構造化候補化を試みます。")
            ui.label("LLM由来候補・曖昧入力・未知Entity・複数候補はPendingへ回し、勝手に正式Claimへ登録しません。")
            ui.label("LLM解釈は現在driver_updated / servo_updatedの単一候補だけ。実データ、金融・給与・税務・Googleカレンダー連携は未実装です。")


if __name__ == "__main__":
    ui.run(host="127.0.0.1", port=8093, reload=False, show=False, title="Local Secretary PKB")
