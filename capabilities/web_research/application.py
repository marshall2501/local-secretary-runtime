"""Web research application helpers shared by direct UI and RITSUKO."""
from __future__ import annotations

from .web_research import research_web


def web_core_answer(result: dict) -> str:
    hits = result.get("hits") or []
    facts = result.get("fact_summary") or {}
    if facts.get("kind") == "driver_version":
        if facts.get("status") == "primary_source_no_current_candidate":
            primary = ", ".join(facts.get("primary_domains") or []) or "一次Source候補"
            secondary = []
            for group in facts.get("groups") or []:
                for candidate in group.get("candidates") or []:
                    if int(candidate.get("primary_source_count") or 0) == 0:
                        secondary.append(f"{group.get('kind')}={candidate.get('value')}")
            secondary_text = " / ".join(secondary[:4])
            return (
                f"Web調査では一次Source候補（{primary}）を確認しましたが、"
                "そこから現在版の番号を抽出できませんでした。"
                + (f" 第三者候補: {secondary_text}。" if secondary_text else "")
                + " 一次Sourceで確認できるまで最新値として確定しません。"
            )
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
            other_groups = [g for g in groups if g is not preferred and g.get("best_candidate")]
            other_text = ""
            if other_groups:
                other_text = " 別種の版番号: " + " / ".join(
                    f"{g.get('kind')}={g.get('best_candidate')}" for g in other_groups[:3]
                ) + "。"
            if status == "leading_consensus" and best:
                leading = candidates[0] if candidates else {}
                return (
                    f"Web調査では{kind_label}候補 {best} が{leading.get('source_count', 0)}件のSourceで一致しています。"
                    + other_text + "異なる種類の版番号同士は競合扱いしていません。"
                )
            if status == "latest_by_date" and best:
                leading = candidates[0] if candidates else {}
                return (
                    f"Web調査では{kind_label}候補 {best} が、近傍日付 {leading.get('latest_date') or '-'} を持つ最新候補として上位です。"
                    + other_text + "ただし日付対応はページ本文の近傍文脈から抽出したため、一次Source表示で最終確認してください。"
                )
            if status == "single_candidate" and best:
                return (
                    f"Web調査では{kind_label}候補 {best} を1系統で抽出しました。"
                    + other_text + "同じ種類の複数Source一致はまだ確認できていないため、確定値とは扱いません。"
                )
            if status == "conflicting_candidates":
                values = " / ".join(str(row.get("value")) for row in candidates[:4] if row.get("value"))
                return (
                    f"Web調査では同じ種類の{kind_label}候補が一致していません。候補: {values}。"
                    + other_text + "一次Sourceと対象期間を追加確認する必要があります。"
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


def research_text(text: str) -> dict:
    result = research_web(text, max_results=5, max_fetches=2).as_dict()
    result["status"] = "ok"
    result["result_kind"] = "web_research"
    return result
