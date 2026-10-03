"""RITSUKO orchestration for comparing local PKB driver state with web evidence."""
from __future__ import annotations

import re

from pkb.application.daily import COMPONENT_STATE_QUERY_PATTERN

_VERSION_VALUE_PATTERN = re.compile(r"\b\d{2,4}\.\d{1,3}(?:\.\d{1,4}){1,2}\b")


def pkb_current_driver_value(result: dict) -> str | None:
    for row in result.get("items") or []:
        value = row.get("current_driver")
        if value:
            return str(value).strip()
        if row.get("predicate") == "current_driver" and row.get("value"):
            return str(row.get("value")).strip()
    return None


def web_latest_version_value(result: dict) -> tuple[str | None, str | None, str | None]:
    summary = result.get("fact_summary") or {}
    return summary.get("best_candidate"), summary.get("preferred_kind"), summary.get("status")


def compare_driver_values(pkb_result: dict, web_result: dict) -> dict:
    current = pkb_current_driver_value(pkb_result)
    latest, latest_kind, web_status = web_latest_version_value(web_result)
    if not current or not latest:
        return {
            "status": "insufficient_evidence", "current": current, "latest": latest,
            "latest_kind": latest_kind, "web_status": web_status,
            "message": "PKB現在値またはWeb最新候補が不足しているため比較できません。",
        }
    current_match = _VERSION_VALUE_PATTERN.search(current)
    latest_match = _VERSION_VALUE_PATTERN.search(str(latest))
    if not current_match or not latest_match:
        return {
            "status": "not_comparable", "current": current, "latest": str(latest),
            "latest_kind": latest_kind, "web_status": web_status,
            "message": f"PKB現在値は {current}、Web最新候補は {latest} ですが、同じ版番号形式として安全に比較できません。",
        }
    current_value = current_match.group(0)
    latest_value = latest_match.group(0)
    same = current_value == latest_value
    return {
        "status": "match" if same else "different",
        "current": current_value, "latest": latest_value,
        "latest_kind": latest_kind, "web_status": web_status,
        "message": (
            f"PKB現在値 {current_value} とWeb最新候補 {latest_value} は一致しています。"
            if same else f"PKB現在値 {current_value} とWeb最新候補 {latest_value} は異なります。"
        ),
    }


def _plain_claim_value(value):
    if isinstance(value, str):
        return value.strip()
    return str(value).strip() if value is not None else None


def driver_web_query_from_detail(detail: dict | None) -> dict:
    if not detail:
        return {"status": "missing_target", "query": None, "manufacturer": None,
                "model": None, "entity_name": None}
    attrs = {}
    for row in detail.get("current") or []:
        predicate = row.get("predicate")
        if predicate in {"manufacturer", "model"} and predicate not in attrs:
            value = _plain_claim_value(row.get("value"))
            if value:
                attrs[predicate] = value
    entity_name = str((detail.get("entity") or {}).get("name") or "").strip() or None
    manufacturer = attrs.get("manufacturer")
    model = attrs.get("model")
    if not model:
        return {"status": "missing_model", "query": None, "manufacturer": manufacturer,
                "model": None, "entity_name": entity_name}
    parts = [part for part in (manufacturer, model) if part]
    return {
        "status": "ready", "query": " ".join(parts) + " latest driver official",
        "manufacturer": manufacturer, "model": model, "entity_name": entity_name,
    }


def resolve_driver_web_target(text: str, *, connection_factory,
                              resolve_component, load_detail) -> dict:
    component_state = COMPONENT_STATE_QUERY_PATTERN.search(text.strip())
    if not component_state:
        return {"status": "missing_component_reference", "query": None,
                "manufacturer": None, "model": None, "entity_name": None}
    parent_name = component_state.group("parent").strip()
    role_token = component_state.group("role")
    with connection_factory() as db:
        resolved = resolve_component(db, parent_name, role_token)
        if resolved is None:
            return {"status": "component_not_unique_or_missing", "query": None,
                    "manufacturer": None, "model": None, "entity_name": None}
        detail = load_detail(db, resolved["id"])
    target = driver_web_query_from_detail(detail)
    target["parent_name"] = parent_name
    target["role_token"] = role_token
    target["component_id"] = resolved["id"]
    return target


def clarified_driver_web_target(reply: str) -> dict:
    value = reply.strip()
    value = re.sub(
        r"^(?:GPU(?:の)?モデル|モデル|製品名|GPU)\s*(?:は|:|：)?\s*",
        "", value, flags=re.IGNORECASE,
    ).strip()
    if not value:
        return {"status": "missing_model", "query": None, "manufacturer": None,
                "model": None, "entity_name": None}
    return {
        "status": "ready", "query": value + " latest driver official",
        "manufacturer": None, "model": value, "entity_name": None,
        "clarified_by_user": True,
    }


def execute_pkb_web_compare(text: str, *, target_override: str | None,
                            execute_read, resolve_target) -> dict:
    pkb = execute_read("pkb_search", text)
    target = (
        clarified_driver_web_target(target_override)
        if target_override is not None else resolve_target(text)
    )
    if target.get("status") != "ready":
        comparison = {
            "status": "insufficient_target",
            "current": pkb_current_driver_value(pkb["result"]),
            "latest": None, "latest_kind": None, "web_status": None,
            "web_query": None, "target": target,
            "message": (
                "PKBで現在ドライバーは取得できましたが、Web検索に使うGPUの"
                "manufacturer / model をPKBから安全に特定できません。"
                "GPUモデルをPKBへ登録するか、依頼で明示してください。"
            ),
        }
        return {
            "capability": "pkb_web_compare", "executions": [pkb],
            "comparison": comparison, "answer": comparison["message"],
            "pkb": pkb["result"], "web": None, "web_query": None,
            "needs_clarification": True,
        }
    web_query = target["query"]
    web = execute_read("web_research", web_query)
    comparison = compare_driver_values(pkb["result"], web["result"])
    comparison["web_query"] = web_query
    comparison["target"] = target
    return {
        "capability": "pkb_web_compare", "executions": [pkb, web],
        "comparison": comparison, "answer": comparison["message"],
        "pkb": pkb["result"], "web": web["result"], "web_query": web_query,
        "needs_clarification": False,
    }
