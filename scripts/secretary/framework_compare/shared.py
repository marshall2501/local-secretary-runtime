"""Shared, strictly read-only evidence sources for two secretary orchestrator trials.

The default fixture is fictional. Live mode reads the *existing* Secretary API
through its existing restricted token; it never changes PostgreSQL or the PC.
Both frameworks use these exact same sources to make comparisons meaningful.
"""
from __future__ import annotations

import json
from pathlib import Path
import sys

SECRETARY_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SECRETARY_DIR))

QUESTION = (
    "架空テストPCで架空ゲームAが突然終了します。以前の経験と現在の情報から"
    "原因は分かりますか？必要な確認事項があれば教えて。"
)
TARGET = "架空テストPC"
DOMAIN = "pc"
MODEL = "qwen3:8b"
RULES = (
    "あなたは個人秘書の統括役です。依頼は原因についての相談であって修理の代行ではありません。"
    "必要なときだけ記憶担当や資料調査担当へ委任し、得た結果から次の手を選び直してください。"
    "根拠が足りたら回答し、調査を増やすこと自体を目的にしないでください。"
    "明示的な対象紐付けのない過去のActionは当該PCで行ったと断定しない。"
    "模擬データは実機の証拠ではなく、タイムアウト観測だけでは原因を確定しない。"
    "PC設定の変更・購入・予約などの外部操作は禁止。確認済み/未確認/次に聞くべきことを区別する。"
)
FIXTURE_CLAIMS = [
    {"entity_name": TARGET, "kind": "claim", "title": "ram_gb",
     "value_text": "16", "state": "unverified",
     "source_citation": "fictional user statement"}
]
FIXTURE_HISTORY = [
    {"task_id": "fictional-unlinked", "entity_name": None,
     "tool": "prototype_mock", "operation": "simulated_read_only",
     "outcome": "succeeded", "summary": "Simulated diagnostic only",
     "evidence": {"simulated": True},
     "source_citation": "fictional diagnostic: no real PC inspected"},
]
FICTIONAL_NOTES = [
    {"id": "fictional-guide", "title": "架空ゲームAの調査メモ",
     "text": "突然終了時はログを確認し、イベントと実際のドライバー情報を照合する。再インストール前に過去の実施結果と現在の構成を比較する。",
     "provenance": "fictional_local_fixture"},
    {"id": "fictional-driver", "title": "架空ドライバ2.0のメモ",
     "text": "旧版と挙動が異なる場合がある。これだけでは障害原因は特定できない。",
     "provenance": "fictional_local_fixture"},
]


class Evidence:
    def __init__(self, mode="fixture"):
        if mode not in ("fixture", "live"):
            raise ValueError("mode must be fixture or live")
        self.mode = mode
        self.calls = []

    def recall(self, target: str = TARGET, domain: str = DOMAIN) -> str:
        self.calls.append({"specialist": "memory", "target": target,
                           "domain": domain, "mode": self.mode})
        if self.mode == "fixture":
            claims, actions, count = FIXTURE_CLAIMS, FIXTURE_HISTORY, 1
        else:
            import ask
            token_file = ask.ROOT / "secrets" / "secretary-api-token.txt"
            if not token_file.is_file():
                raise RuntimeError("Existing Secretary API token file is missing")
            token = token_file.read_text(encoding="utf-8-sig").strip()
            memory = ask.search(token, domain=domain)
            past = ask.search_experience(token, domain=domain)
            claims = memory.get("items") or []
            actions = past.get("items") or []
            count = past.get("total")
        norm = lambda x: "".join(str(x or "").casefold().split())
        named = [c for c in claims if norm(c.get("entity_name")) == norm(target)]
        relevant = [a for a in actions
                    if not a.get("entity_name")
                    or norm(a.get("entity_name")) == norm(target)]
        result = []
        for item in relevant[:8]:
            x = {k: item.get(k) for k in
                 ("task_id", "entity_name", "tool", "operation", "outcome",
                  "summary", "evidence", "source_citation", "recorded_at")}
            x["association"] = (
                "explicit_entity_match" if item.get("entity_name")
                else "unlinked_same_domain_not_proof_of_target"
            )
            x["simulated"] = (item.get("tool") == "prototype_mock"
                              or bool((item.get("evidence") or {}).get("simulated"))
                              if isinstance(item.get("evidence"), dict)
                              else item.get("tool") == "prototype_mock")
            result.append(x)
        return json.dumps({
            "mode": self.mode, "target": target, "domain": domain,
            "claims": named[:12], "previous_actions": result,
            "history_domain_total": count,
            "coverage": "bounded first 100 domain records; no proof of DB-wide absence",
        }, ensure_ascii=False, default=str)

    def research(self, query: str) -> str:
        self.calls.append({"specialist": "research", "query": query,
                           "mode": "fictional_fixture"})
        words = [w for w in query.casefold().split() if len(w) > 2]
        matches = [n for n in FICTIONAL_NOTES if not words or any(
            w in (n["title"] + " " + n["text"]).casefold() for w in words)]
        return json.dumps({
            "query": query,
            "sources": (matches or FICTIONAL_NOTES)[:2],
            "coverage": "fictional local catalog; no current web or real diagnostics",
        }, ensure_ascii=False)
