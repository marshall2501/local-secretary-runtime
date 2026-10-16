# Provenance-Origin: original
# Provenance-AI: AI-generated
"""RITSUKOとMAGIが利用する既存PKB観測の画面非依存処理。

旧NiceGUIの対象PC/部品解決ロジックを移し、repositoryと読取関数を注入する。
不明な対象を広いPKB検索へすり替えない制限を維持する。
"""
from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pkb.entity_model_service import COMPONENT_ROLE_TOKENS


def _json_safe(value):
    """保存済みPKBのUUID・日時を、情報構造を失わずJSONに変換する。"""
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return value


def _pkb_unresolved_target_execution(
    *,
    parent_name: str | None,
    role_token: str | None,
    reason: str,
) -> dict:
    """対象が不明な場合に広範な記憶検索へ拡張せず、不一致を返す。"""
    if parent_name and role_token:
        answer = (
            f"PKBでは{parent_name}の{role_token}を一意に確認できませんでした。"
        )
    elif role_token:
        answer = (
            f"PKBでは{role_token}の対象PCを一意に確認できませんでした。"
        )
    else:
        answer = "PKBでは要求対象を一意に確認できませんでした。"
    return {
        "capability": "pkb_search",
        "result": {
            "status": "ok",
            "result_kind": "pkb_target_unresolved",
            "total": 0,
            "items": [],
            "reason": reason,
            "parent_name": parent_name,
            "role_token": role_token,
        },
        "answer": answer,
        "total": 0,
        "tool": "pkb",
        "operation": "target_resolution",
        "source_slug": "pkb-target-unresolved",
        "citation": "RITSUKO bounded PKB target resolution",
        "verified_by": "deterministic_pkb_query",
        "status": "ok",
        "confidentiality": "private",
        "source_metadata": {
            "target_resolution": reason,
            "parent_entity_name": parent_name,
            "role_token": role_token,
        },
    }


def _public_query_terms_from_pkb_result(result: dict, combined_request: str) -> list[str]:
    """単一Entityに限定された結果から、公開可能な技術識別子のみ抽出する。"""
    rows = [
        item for item in (result.get("items") or [])
        if isinstance(item, dict)
    ]
    entity_names = {
        str(item.get("entity_name") or "").strip()
        for item in rows
        if str(item.get("entity_name") or "").strip()
    }
    # 複数Entityにまたがる結果から外部送信用の検索語を推測しない。
    if len(entity_names) != 1:
        return []
    entity_name = next(iter(entity_names))
    if entity_name not in combined_request:
        return []

    terms: list[str] = []
    for item in rows[:12]:
        predicate = str(item.get("predicate") or "").strip()
        value = item.get("value")
        if predicate in {"manufacturer", "model", "current_os_release"}:
            text = str(value or "").strip()
            if text:
                terms.append(text)
        elif predicate == "os_release_changed" and isinstance(value, dict):
            for key in ("product", "to_release"):
                text = str(value.get(key) or "").strip()
                if text:
                    terms.append(text)
    return list(dict.fromkeys(terms))[:20]


def execute_magi_pkb_request(
    user_raw: str,
    pending_request: dict,
    *,
    pkb_repository,
    entity_map,
    execute_read,
) -> dict:
    """MAGIが要求したPKB情報を、既存Entity/Componentの範囲で読み取る。

    対象PCや部品関係が曖昧なら広いPKB検索へ拡張せず、不明と返す。
    返す情報の境界は従来のNiceGUI版と同じで、参照元をDIで受け取る。

    Args:
        user_raw: 元の依頼。対象PCなどの照合にのみ使用する。
        pending_request: MAGIが指定した情報源と必要情報。
        pkb_repository: 読取対象のPKB Repository。
        entity_map: 利用者のEntity名を既存のIDへ解決する関数。
        execute_read: PKB検索を既存RITSUKO能力へ委譲する関数。

    Returns:
        dict: 証拠・引用条件付きの保存用Observation実行結果。
    """
    requested = str(pending_request.get("what") or "").strip()
    combined = (user_raw.strip() + "\n" + requested).strip()

    entities = entity_map()
    parents = [
        row for name, row in sorted(
            entities.items(), key=lambda item: len(item[0]), reverse=True
        )
        if name and name in combined and row.get("entity_type") == "computer"
    ]
    roles = [token for token in COMPONENT_ROLE_TOKENS if token in combined]

    if roles:
        if len(roles) != 1:
            return _pkb_unresolved_target_execution(
                parent_name=(parents[0].get("name") if len(parents) == 1 else None),
                role_token=None,
                reason="component_role_not_unique",
            )
        role = roles[0]
        if len(parents) != 1:
            return _pkb_unresolved_target_execution(
                parent_name=None,
                role_token=role,
                reason="parent_computer_not_unique_or_missing",
            )

        parent = parents[0]
        component = pkb_repository.resolve_component_reference(
            parent["name"], role
        )
        if component is None:
            return _pkb_unresolved_target_execution(
                parent_name=parent["name"],
                role_token=role,
                reason="component_relation_not_unique_or_missing",
            )

        detail = _json_safe(
            pkb_repository.load_entity_detail(component["id"]) or {}
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
        identity = model or component.get("name") or role
        if manufacturer and manufacturer.lower() not in identity.lower():
            identity = manufacturer + " " + identity
        answer = (
            f"PKBの記録では、{parent['name']}の{role}は {identity} です。"
            if model
            else (
                f"PKBには{parent['name']}の{role} Entity "
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
                "public_query_terms": [
                    value
                    for value in (manufacturer, model)
                    if str(value or "").strip()
                ],
            },
        }

    execution = execute_read("pkb_search", requested or user_raw)
    result = execution.get("result") or {}
    public_terms = _public_query_terms_from_pkb_result(result, combined)
    if public_terms:
        metadata = dict(execution.get("source_metadata") or {})
        metadata["public_query_terms"] = public_terms
        execution["source_metadata"] = metadata
    return execution
