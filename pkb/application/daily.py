"""Daily standalone PKB application use cases extracted from the NiceGUI shell."""
from __future__ import annotations

import re
from dataclasses import asdict
from datetime import datetime, timezone
from uuid import UUID, uuid4

from pkb.correction_service import correct_entity
from pkb.entity_model_service import list_components, resolve_component_reference
from pkb.ingestion_gate import InputRecord, ProposedClaim
from pkb.pending_service import enqueue as enqueue_pending
from pkb.query_service import ClaimQuery, query_claims
from pkb.write_service import write_one


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


def list_entities(db) -> list[dict]:
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


def entity_map(db) -> dict[str, dict]:
    return {row["name"]: row for row in list_entities(db)}


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
            "status": "parsed", "intent": "assertion", "entity": entity,
            "predicate": predicate, "value": match.group("value"),
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
                "status": "review", "reason": "unknown_or_ambiguous_entity",
                "old_entity": old_name, "new_entity": new_name,
            }
        if old_name == new_name:
            return {"status": "review", "reason": "correction_entities_are_same"}
        return {
            "status": "parsed", "intent": "correction", "old_entity": old_name,
            "new_entity": new_name, "predicate": predicate,
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
            item[key] = str(value) if isinstance(value, (datetime, UUID)) else value
        items.append(item)
    return {
        "status": page.status, "total": page.total, "items": items,
        "known_at": page.known_at.isoformat(),
        "include_history": page.include_history,
    }


def register_text(text: str, *, connection_factory, interpreter) -> dict:
    text = text.strip()
    if not text:
        return {"status": "rejected", "reason": "empty_input"}
    with connection_factory() as db:
        entities = entity_map(db)
        component_parsed = parse_component_write(text)
        resolved_entity_mention = None
        if component_parsed is not None:
            component_entity = resolve_component_reference(
                db, component_parsed["parent"], component_parsed["role"]
            )
            if component_entity is None:
                parsed = {"status": "review", "reason": "component_relation_not_unique_or_missing"}
            else:
                resolved_entity_mention = component_parsed["parent"] + "の" + component_parsed["role"]
                parsed = {
                    "status": "parsed", "intent": "assertion",
                    "entity": component_entity["name"],
                    "predicate": component_parsed["predicate"],
                    "value": component_parsed["value"],
                }
                entities[component_entity["name"]] = {
                    "id": component_entity["id"], "name": component_entity["name"],
                    "domain": "pc", "entity_type": component_entity["entity_type"],
                }
        else:
            parsed = parse_write(text, set(entities))

        if parsed is None or parsed["status"] != "parsed":
            interpreted = interpreter(text, set(entities))
            if interpreted.status == "candidate" and interpreted.candidate is not None:
                candidate = interpreted.candidate
                entity = entities[candidate.entity_mention]
                result = enqueue_pending(
                    db, input_id="daily-pkb-model-review-" + uuid4().hex,
                    raw_text=text, reason="model_candidate_needs_user_confirmation",
                    entity_id=entity["id"], predicate=candidate.predicate,
                    proposed_value=candidate.value, interpreter_kind="local_ollama",
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
                db, input_id="daily-pkb-review-" + uuid4().hex,
                raw_text=text, reason=reason,
                interpreter_kind=("local_ollama" if interpreted.model else None),
                interpreter_model=interpreted.model,
            )
            payload = asdict(result)
            payload["message"] = "安全に構造化できなかったため、元の入力をそのまま確認待ちに保存しました。"
            return payload

        entity = entities[parsed["entity"]]
        input_id = "daily-pkb-" + uuid4().hex
        now = datetime.now(timezone.utc)
        record = InputRecord(
            input_id=input_id, source_kind="user_statement",
            source_ref="fixture://daily-pkb/" + input_id, text=text,
            recorded_at=now, occurred_at=now,
        )
        claim = ProposedClaim(
            entity_key=entity["id"], entity_mention=resolved_entity_mention or entity["name"],
            predicate=parsed["predicate"], value=parsed["value"],
            evidence_start=0, evidence_end=len(text), evidence_quote=text,
        )
        result = write_one(db, record, claim)
        if result.status == "review":
            pending = enqueue_pending(
                db, input_id="daily-pkb-review-" + uuid4().hex,
                raw_text=text, reason=result.reason, entity_id=entity["id"],
                predicate=parsed["predicate"], proposed_value=parsed["value"],
            )
            return asdict(pending)
        return asdict(result)


def correct_text(text: str, *, connection_factory) -> dict:
    text = text.strip()
    if not text:
        return {"status": "rejected", "reason": "empty_input"}
    with connection_factory() as db:
        entities = entity_map(db)
        parsed = parse_correction(text, set(entities))
        if parsed is None:
            result = enqueue_pending(
                db, input_id="daily-pkb-correction-review-" + uuid4().hex,
                raw_text=text, reason="unsupported_correction_in_first_slice",
            )
            payload = asdict(result)
            payload["message"] = "「訂正：旧Entityではなく新Entityを値へ更新した。」形式の明示訂正のみ扱います。"
            return payload
        if parsed["status"] != "parsed":
            result = enqueue_pending(
                db, input_id="daily-pkb-correction-review-" + uuid4().hex,
                raw_text=text, reason=parsed["reason"],
            )
            return asdict(result)
        old = entities[parsed["old_entity"]]
        new = entities[parsed["new_entity"]]
        with db.cursor() as cur:
            cur.execute(
                """SELECT c.id, c.valid_from
                   FROM secretary.claims c
                   JOIN secretary.sources s ON s.id=c.source_id
                   WHERE c.entity_id=%s AND c.predicate=%s
                     AND c.value=%s::jsonb AND c.origin='user_explicit'
                     AND c.verification_status='unverified'
                     AND c.retracted_at IS NULL AND s.uri LIKE 'fixture://%%'
                   ORDER BY c.recorded_at DESC""",
                (UUID(old["id"]), parsed["predicate"], '"' + parsed["value"] + '"'),
            )
            rows = cur.fetchall()
        if len(rows) != 1:
            result = enqueue_pending(
                db, input_id="daily-pkb-correction-review-" + uuid4().hex,
                raw_text=text, reason="correction_target_not_unique",
                entity_id=old["id"], predicate=parsed["predicate"],
                proposed_value=parsed["value"],
            )
            payload = asdict(result)
            payload["candidate_count"] = len(rows)
            return payload
        old_claim_id, valid_from = rows[0]
        input_id = "daily-pkb-correction-" + uuid4().hex
        now = datetime.now(timezone.utc)
        record = InputRecord(
            input_id=input_id, source_kind="user_statement",
            source_ref="fixture://daily-pkb/" + input_id, text=text,
            recorded_at=now, occurred_at=valid_from,
        )
        claim = ProposedClaim(
            entity_key=new["id"], entity_mention=new["name"],
            predicate=parsed["predicate"], value=parsed["value"],
            evidence_start=0, evidence_end=len(text), evidence_quote=text,
            intent="correction", corrects_claim_id=str(old_claim_id),
        )
        result = correct_entity(db, record, claim)
        if result.status == "review":
            pending = enqueue_pending(
                db, input_id="daily-pkb-correction-review-" + uuid4().hex,
                raw_text=text, reason=result.reason, entity_id=new["id"],
                predicate=parsed["predicate"], proposed_value=parsed["value"],
            )
            return asdict(pending)
        return asdict(result)


def search_text_with_db(db, text: str) -> dict:
    entities = entity_map(db)
    q = text.strip()
    component_state = COMPONENT_STATE_QUERY_PATTERN.search(q)
    if component_state and any(word in q for word in ("現在", "今の", "現行")):
        resolved = resolve_component_reference(
            db, component_state.group("parent").strip(), component_state.group("role")
        )
        if resolved is not None:
            page = query_claims(
                db,
                ClaimQuery(
                    entity_id=UUID(resolved["id"]), predicate="current_driver",
                    effective_at=datetime.now(timezone.utc), include_history=False,
                    limit=100, offset=0,
                ),
            )
            result = _serialize_page(page)
            result["result_kind"] = "claims"
            return result
    if "構成" in q:
        parent = next(
            (row for name, row in sorted(entities.items(), key=lambda item: len(item[0]), reverse=True)
             if name in q and row["entity_type"] == "computer"),
            None,
        )
        if parent is not None:
            rows = list_components(db, UUID(parent["id"]))
            items = []
            for row in rows:
                items.append({
                    key: str(value) if isinstance(value, (datetime, UUID)) else value
                    for key, value in row.items()
                })
            return {"status": "ok", "result_kind": "components", "total": len(items), "items": items}
    page = query_claims(db, parse_query(text, entities))
    result = _serialize_page(page)
    result["result_kind"] = "claims"
    return result


def search_text(text: str, *, connection_factory) -> dict:
    with connection_factory() as db:
        return search_text_with_db(db, text)
