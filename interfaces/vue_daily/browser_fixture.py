# Provenance-Origin: original
# Provenance-AI: AI-generated
"""Vue日常UIの実ブラウザ隔離テスト専用。日常DB/MAGI/Secretへ接続しない。

単一Secretary Serverの同一OriginでVue/Task API/PKB APIを起動し、
HTML/JS実ロードからTask受付・回答・Review・停止・F5まで検証する。
本番entrypointからimportしない。実Taskデータとの混用を防ぐ。
"""
from __future__ import annotations

import asyncio
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from copy import deepcopy
from uuid import UUID, uuid5, NAMESPACE_DNS

from interfaces.api.secretary_server import create_app
from application.schema_diagram import (
    SchemaColumn, SchemaDiagramService, SchemaForeignKey,
    SchemaSnapshot, SchemaTable,
)
from infrastructure.schema_diagram.svg_renderer import NativeSvgProvider
from infrastructure.schema_diagram.providers import NativeMermaidProvider



class BrowserFixtureStore:
    """実DBの代わりに固定の匿名Taskをメモリに置く照会fixture。"""

    def __init__(self):
        """追加読み込みの検証用に12件の架空Taskを用意する。"""
        self.items: dict[str, dict] = {}
        for index in range(12):
            uid = str(uuid5(NAMESPACE_DNS, f"vue-browser-task-{index}"))
            self.items[uid] = {
                "id": uid, "request": f"架空の過去Task {index + 1}",
                "status": "completed", "revision": 1, "message": "保存済みの回答",
                "magi_session": {"status": "candidate_ready", "turns": [],
                                  "member_specs": []},
                "goal_definition": {
                    "resolution": "confirmed", "version": 2,
                    "interpreted_goal": "架空Goal",
                    "criteria": ["根拠が存在すること"],
                },
            }

    def _sorted(self, kind: str, limit: int, offset: int) -> list[dict]:
        """指定ビューに対応するタスク概要を新しい順で返す。"""
        records = list(self.items.values())[::-1]
        if kind == "open":
            records = [item for item in records if item["status"]
                       in {"pending", "running", "waiting_external", "paused"}]
        elif kind == "completed":
            records = [item for item in records if item["status"] == "completed"]
        return [deepcopy(item) for item in records[offset:offset + limit]]

    def recent(self, limit: int = 10, offset: int = 0) -> list[dict]:
        """最近のTaskを列挙する。"""
        return self._sorted("recent", limit, offset)

    def open(self, limit: int = 10, offset: int = 0) -> list[dict]:
        """処理途中Taskだけを列挙する。"""
        return self._sorted("open", limit, offset)

    def completed(self, limit: int = 10, offset: int = 0) -> list[dict]:
        """完了Taskだけを列挙する。"""
        return self._sorted("completed", limit, offset)

    def trace(self, task_id: UUID) -> dict:
        """Action・MAGI Turn・監査イベントを含むTask詳細を返す。"""
        task = self.items.get(str(task_id))
        if task is None:
            raise ValueError("not found")
        return {"task": deepcopy(task), "actions": [{
            "action_id": "synthetic-a1", "tool": "fake", "operation": "read",
            "action_status": "completed", "result_id": "synthetic-r1",
            "summary": "架空の検証済み結果", "verified_by": "fixture",
            "source_uri": "https://example.org/reference",
        }], "advisor_events": [{
            "event_type": "synthetic_verified", "occurred_at": "2026-10-17T00:00:00",
        }]}


class BrowserFixtureCommands:
    """fake RITSUKO受付。実MAGI/DB呼出しをしない。"""

    def __init__(self, store: BrowserFixtureStore):
        """両FastAPIで共有する架空Task保存領域を受け取る。"""
        self.store = store

    async def start(self, request: str, *, decision_strategy: str,
                    task_id: UUID | None = None) -> dict:
        """新規依頼をfake Taskとして保存し、本人回答待ちにする。"""
        uid = str(task_id or uuid5(NAMESPACE_DNS, request))
        if uid not in self.store.items:
            is_stop = "停止試験" in request
            is_ready_candidate = "回答候補試験" in request
            is_memory_candidate = "記憶反映試験" in request
            self.store.items[uid] = {
                "id": uid, "request": request,
                "status": "pending" if is_stop else "waiting_external",
                "phase": ("observe" if is_stop else "awaiting_review"
                          if is_ready_candidate or is_memory_candidate
                          else "awaiting_clarification"),
                "revision": 1, "question": (
                    None if is_ready_candidate or is_memory_candidate else "好きな言語を教えてください。"
                ),
                "magi_session": {
                    "decision_strategy": decision_strategy,
                    "status": (
                        "running" if is_stop else
                        "proposal_ready" if is_memory_candidate else
                        "candidate_ready" if is_ready_candidate else "waiting_user"
                    ),
                    "next_step": (
                        "review_proposal" if is_memory_candidate else
                        "review_answer_candidate" if is_ready_candidate else "ask_user"
                    ),
                    "detail": (
                        {"state": "KNOWLEDGE_CANDIDATE",
                         "answer_candidate": "本人が確認したOSはFedoraです。",
                         "knowledge_candidate": "本人が確認したOSはFedoraです。"}
                        if is_memory_candidate else
                        {"state": "READY",
                         "answer_candidate": "辞書はキーと値の組です。"}
                        if is_ready_candidate else None
                    ),
                    "tool_read_executed": is_memory_candidate,
                    "observations": [
                        {"source": "pkb", "verified": True, "text": "確認が必要"},
                        {"source": "user_clarification", "verified": False,
                         "text": "Fedora", "responds_to": ["REQ-1"]},
                    ] if is_memory_candidate else [],
                    "pending_requests": [],
                    "member_specs": [
                        {"name": "MELCHIOR", "enabled": True, "provider": "ollama", "model": "fixture"},
                        {"name": "CASPER", "enabled": True, "provider": "openai", "model": "fixture"},
                        {"name": "BALTHASAR", "enabled": True, "provider": "google", "model": "fixture"},
                    ],
                    "turns": [{"stage": "classify", "status": "ok",
                               "member_results": [
                                   {"name": name, "status": "ok", "elapsed_seconds": 0.1}
                                   for name in ("MELCHIOR", "CASPER", "BALTHASAR")
                               ]}],
                },
                "goal_definition": {
                    "resolution": "proposed", "version": 1,
                    "interpreted_goal": "本人に説明する",
                    "criteria": ["簡潔に回答する"],
                },
            }
        return {"task_id": uid, "kind": "start", "status": "accepted"}

    async def answer(self, task_id: UUID, reply: str) -> dict:
        """同じTaskに本人回答を紐づけ、確認待ち候補へ進める。"""
        task = self.store.items[str(task_id)]
        task["status"] = "waiting_external"
        task["revision"] += 1
        task["phase"] = "awaiting_review"
        # This synthetic answer is an ordinary READY candidate, not an
        # invented user-grounded PKB knowledge proposal.
        task["magi_session"]["status"] = "candidate_ready"
        task["magi_session"]["detail"] = {
            "state": "READY", "answer_candidate": f"回答候補：{reply}",
        }
        task["magi_session"]["pending_requests"] = []
        task["message"] = f"回答候補：{reply}"
        task["question"] = None
        return {"task_id": str(task_id), "kind": "answer", "status": "accepted"}

    async def review(self, task_id: UUID, decision: str) -> dict:
        """回答だけのReviewを模擬し、Taskを完了にする。"""
        task = self.store.items[str(task_id)]
        task["status"] = "completed"
        task["phase"] = "completed"
        task["revision"] += 1
        task["magi_session"]["status"] = "candidate_ready"
        task["message"] = (
            "架空Taskの記憶反映を確定しました。"
            if decision == "remember" else "架空Taskの回答を確定しました。"
        )
        return {"task_id": str(task_id), "kind": "review", "status": "accepted"}

    def stop(self, task_id: UUID) -> dict:
        """pending Taskに停止要求を記録して一時停止する。"""
        task = self.store.items[str(task_id)]
        task["status"] = "paused"
        task["phase"] = "paused"
        task["stop_requested"] = True
        task["revision"] += 1
        return {"task_id": str(task_id), "status": "paused", "stop_requested": True}

    def operation(self, task_id: UUID) -> dict:
        """受付Jobの代わりに架空Taskの保存状態を返す。"""
        if str(task_id) not in self.store.items:
            raise KeyError("missing")
        task_status = self.store.items[str(task_id)]["status"]
        operation = {
            "task_id": str(task_id), "kind": None,
            "status": "running" if task_status == "pending" else "operation_not_in_process",
            "source": "fixture", "task_status": task_status,
        }
        if task_status == "pending":
            # Explicit synthetic progress; the fixture has no real model worker.
            operation["live_turn"] = 1
            operation["member_progress"] = {
                "MELCHIOR": {
                    "member": "MELCHIOR", "provider": "ollama",
                    "model": "fixture", "state": "running", "turn": 1,
                },
            }
        return operation


class BrowserFixtureReadService:
    """Fake SQL-first PKB data for isolated browser tests only."""

    def search_memory(self, q=None, domain=None, kind=None,
                      include_history=False, limit=20, offset=0):
        """Filter fictional source records without any database connection."""
        items = [{
            "id": "synthetic-memory-1", "kind": "claim", "domain": "pc",
            "entity_name": "架空サブPC", "title": "GPU",
            "value_text": '"架空GPU"', "evidence": "隔離テストの記録",
            "state": "verified", "source_citation": "架空の本人申告",
            "source_uri": "fixture://vue-pkb/1",
            "recorded_at": "2026-10-17T00:00:00+09:00",
        }]
        if q:
            items = [x for x in items if q.lower() in (
                x["entity_name"] + x["title"] + x["value_text"] +
                x["source_citation"]).lower()]
        if domain:
            items = [x for x in items if x["domain"] == domain]
        if kind:
            items = [x for x in items if x["kind"] == kind]
        total = len(items)
        return {
            "total": total, "limit": limit, "offset": offset,
            "include_history": include_history,
            "items": items[offset:offset + limit],
        }


class BrowserFinance:
    """Synthetic persisted Finance data; no CSV or personal DB."""

    def load_finance_dashboard(self, **filters):
        from capabilities.finance.finance_import import FinanceDashboard

        return FinanceDashboard(
            transaction_count=1, calculation_target_count=1,
            start_date="2026-10-01", end_date="2026-10-01",
            income_total=2000, expense_total=500, net_total=1500,
            monthly=[{"month": "2026-10", "income": 2000, "expense": 500,
                      "net": 1500, "count": 1}],
            categories=[{"major": "食費", "minor": "架空", "expense": 500}],
            recent_rows=[{"external_id": "test-finance-1",
                          "date": "2026-10-01", "content": "架空の買い物",
                          "amount": -500, "account": "架空銀行",
                          "major_category": "食費", "minor_category": "架空",
                          "memo": "", "is_transfer": False,
                          "calculation_target": True,
                          "last_seen_at": "2026-10-01T10:00:00"}],
            import_batches=[], page=filters.get("page", 1), page_size=25,
            total_pages=1, sort_by="date", sort_dir="desc",
            row_mode=filters.get("row_mode", "calculation_target"),
        )

    def finance_filter_options(self):
        return {"accounts": ["架空銀行"], "major_categories": ["食費"]}


class BrowserConnections:
    """Synthetic existing connection metadata; secret values never generated."""

    def list_service_connections(self, **kwargs):
        return [{"id": "fake-connection", "display_name": "架空 OpenAI",
                 "adapter_key": "openai", "endpoint": "https://example.org",
                 "enabled": True, "capabilities": ["service_billing_read"],
                 "auth_configured": True, "auth_fields": ["api_key"]}]

    def get_service_connection(self, connection_id):
        return self.list_service_connections()[0]


class BrowserBilling:
    """Synthetic billing metadata; no network usage checks on page load."""

    def list_service_billing_profiles(self, **kwargs):
        return [{"id": "fake-profile", "display_name": "架空の利用料金",
                 "connection_name": "架空 OpenAI", "connection_id": "fake-connection",
                 "adapter_key": "openai", "enabled": True,
                 "connection_enabled": True}]



class BrowserMagiSettings:
    """Three fake MAGI slots using the already isolated browser fixture."""

    def list_llm_profiles(self, *, include_disabled=False):
        return [{
            "id": "fake-llm-profile", "display_name": "架空 Ollama",
            "model": "fixture-model", "connection_id": "fake-connection",
            "connection_display_name": "架空 Ollama",
            "provider": "ollama", "enabled": True,
        }]

    def load_member_specs(self):
        return [{
            "name": name, "profile_id": "fake-llm-profile",
            "provider": "ollama", "model": "fixture-model",
            "enabled": name == "CASPER", "weight": 1.0,
            "timeout_seconds": 120, "retry_within_turn": False,
        } for name in ("MELCHIOR", "CASPER", "BALTHASAR")]

    def upsert_llm_profile(self, **details):
        return {"id": "fake-llm-new", "display_name": details.get("display_name"),
                "model": details.get("model"), "enabled": True}

    def save_member_assignments(self, assignments):
        return assignments



async def serve() -> None:
    """Start one fake Secretary Server with no DB/MAGI or external calls."""
    import uvicorn

    # E2E modifies UI defaults. Keep them in a disposable directory, never
    # in the user's existing data/ui_preferences.json.
    fixture_preferences = TemporaryDirectory(prefix="secretary-ui-browser-")
    os.environ["LSA_UI_PREFERENCES_PATH"] = str(
        Path(fixture_preferences.name) / "ui_preferences.json"
    )

    store = BrowserFixtureStore()
    reader = BrowserFixtureReadService()
    schema = SchemaSnapshot(
        database="secretary", schema="secretary",
        tables=(
            SchemaTable(name="tasks", kind="BASE TABLE", columns=(
                SchemaColumn("id", "uuid", False, primary_key=True),
                SchemaColumn("request", "text", False),
            )),
            SchemaTable(name="results", kind="BASE TABLE", columns=(
                SchemaColumn("id", "uuid", False, primary_key=True),
                SchemaColumn("task_id", "uuid", False, foreign_key=True),
            )),
        ),
        foreign_keys=(
            SchemaForeignKey("results_task_fk", "results", ("task_id",),
                             "tasks", ("id",), "NO ACTION", "CASCADE"),
        ),
        views=(),
    )
    schema_diagrams = SchemaDiagramService((
        NativeSvgProvider(lambda: schema),
        NativeMermaidProvider(lambda: schema),
    ))
    app = create_app(
        queries=store, commands=BrowserFixtureCommands(store),
        read_service=reader, finance_repository=BrowserFinance(),
        service_connections=BrowserConnections(), billing_settings=BrowserBilling(),
        magi_settings=BrowserMagiSettings(),
        schema_diagram_service=schema_diagrams,
        recover_pending_at_startup=False,
    )
    server = uvicorn.Server(uvicorn.Config(
        app, host="127.0.0.1", port=8097, log_level="warning",
    ))
    try:
        await server.serve()
    finally:
        fixture_preferences.cleanup()


if __name__ == "__main__":
    asyncio.run(serve())
