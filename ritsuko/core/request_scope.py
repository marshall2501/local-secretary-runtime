"""Deterministic request scoping and grounded answer formatting for RITSUKO."""
from __future__ import annotations

from pkb.application.daily import COMPONENT_STATE_QUERY_PATTERN


def scope_core_request(text: str, entities: dict[str, dict],
                       observation_pack: dict | None = None) -> dict:
    q = text.strip()
    if not q:
        return {"status": "question", "question": "何を確認したいか入力してください。", "reason": "empty_request"}
    wants_web = any(word in q for word in ("Web", "WEB", "web", "ウェブ", "ネット", "インターネット", "公式サイト"))
    wants_compare = any(word in q for word in ("比較", "最新か", "新しいか", "最新版か"))
    wants_current_driver = "ドライバ" in q and any(word in q for word in ("現在", "今の", "現行"))
    if wants_web and wants_compare and wants_current_driver:
        return {"status": "ready", "capability": "pkb_web_compare", "domain": "pc"}
    if wants_web:
        return {"status": "ready", "capability": "web_research", "domain": "research"}
    if any(word in q for word in ("家計", "支出", "収入", "収支", "出費")):
        return {"status": "ready", "capability": "finance_read", "domain": "finance"}
    component_state = COMPONENT_STATE_QUERY_PATTERN.search(q)
    if component_state and any(word in q for word in ("現在", "今の", "現行")):
        return {"status": "ready", "capability": "pkb_search", "domain": "pc"}
    if observation_pack is not None:
        mentioned = list(observation_pack.get("matched_entities") or [])
    else:
        mentioned = [
            row for name, row in sorted(entities.items(), key=lambda item: len(item[0]), reverse=True)
            if name in q
        ]
    if mentioned and any(word in q for word in ("構成", "ドライバ", "サーボ", "履歴")):
        return {"status": "ready", "capability": "pkb_search", "domain": mentioned[0]["domain"]}
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
        parts.append(f"{entity}: {predicate}={row.get('value')}")
    return "PKBの記録では、" + " / ".join(parts) + "。"
