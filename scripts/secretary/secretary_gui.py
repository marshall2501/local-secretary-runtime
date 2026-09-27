"""Localhost GUI for the experimental Secretary Core (NiceGUI 3.17.1).

From the isolated LangGraph virtual environment:
    python -m pip install nicegui==3.17.1
    python scripts/secretary/secretary_gui.py

Open http://127.0.0.1:8091 on the same sub-PC. No cloud service,
no automatic machine changes, and no modifications to yt-topic-search.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import uuid

from nicegui import background_tasks, run, ui

from secretary_core import (
    DEFAULT_STATE_DIR,
    SecretaryCore,
    load_state,
    save_state,
    requires_continuation,
    verified_objective,
)
from secretary_gui_model import (
    SPECIALIST_LABELS,
    STATUS_LABELS,
    can_reply,
    can_retry,
    event_rows,
    evidence_rows,
    task_summaries,
    task_text_report,
)

STATE_DIR = Path(os.getenv(
    "LSA_GUI_STATE_DIR", str(DEFAULT_STATE_DIR)
)).expanduser().resolve()
ACTIVE_TASKS: set[str] = set()
TASK_ERROR_LIMIT = 500


def _task_path(task_id: str) -> Path:
    return STATE_DIR / (str(uuid.UUID(task_id)) + ".json")


def _write_failure(task_id: str, error: Exception) -> None:
    """Persist failure so the UI never leaves a task silently stuck running."""
    try:
        state = load_state(task_id, STATE_DIR)
    except (FileNotFoundError, ValueError, KeyError):
        return  # Input validation can fail before Core created a checkpoint.
    state["status"] = "error"
    state["error"] = (type(error).__name__ + ": " + str(error))[:TASK_ERROR_LIMIT]
    state.setdefault("events", []).append({
        "type": "error",
        "message": state["error"],
    })
    save_state(state, STATE_DIR)


def _execute_start(task_id: str, request: str, target: str,
                   domain: str, mode: str, model: str) -> None:
    SecretaryCore(STATE_DIR).start(
        request, target=target, domain=domain, mode=mode,
        model=model, task_id=task_id,
    )


def _execute_resume(task_id: str, answer: str) -> None:
    SecretaryCore(STATE_DIR).resume(task_id, answer)


def _execute_reopen(task_id: str, update: str) -> None:
    SecretaryCore(STATE_DIR).reopen(task_id, update)


async def _run_background(task_id: str, function, *args) -> None:
    """All slow local Ollama calls run outside NiceGUI's event loop."""
    try:
        await run.io_bound(function, *args)
    except Exception as error:
        _write_failure(task_id, error)
    finally:
        ACTIVE_TASKS.discard(task_id)


def launch(task_id: str, function, *args) -> bool:
    """At most one model run at a time; prevents accidental double-resumes."""
    if ACTIVE_TASKS:
        return False
    ACTIVE_TASKS.add(task_id)
    background_tasks.create(
        _run_background(task_id, function, *args),
        name="secretary-" + task_id,
    )
    return True


def state_revision(task_id: str | None):
    if not task_id:
        return None
    try:
        return task_id, _task_path(task_id).stat().st_mtime_ns, (
            task_id in ACTIVE_TASKS)
    except OSError:
        return task_id, None, task_id in ACTIVE_TASKS


@ui.page("/")
def home():
    ui.colors(primary="#3456bd", positive="#138657", warning="#c58b19")
    ui.add_head_html("""
    <style>
      body { background: #f3f5fa; color: #25324d; }
      .secretary-surface { border: 1px solid #e2e7f0; border-radius: 14px;
                           box-shadow: 0 2px 9px rgba(22,33,55,.04); }
      .secretary-muted { color: #65728b; font-size: .88rem; }
      .secretary-wrap { overflow-wrap: anywhere; white-space: pre-wrap; }
    </style>
    """)

    selected = {"task_id": None}
    existing = task_summaries(STATE_DIR, limit=1)
    if existing:
        selected["task_id"] = existing[0]["task_id"]
    rendered = {"revision": None}

    with ui.header().classes("items-center bg-white text-slate-800 shadow-sm"):
        ui.icon("assistant", size="27px").classes("text-blue-700")
        ui.label("Local Secretary AI").classes("text-xl font-bold")
        ui.badge("LOCAL / READ ONLY", color="blue-grey")
        ui.space()
        ui.label("Secretariat · Experimental GUI").classes("text-sm text-slate-500")

    with ui.column().classes("w-full max-w-screen-2xl mx-auto p-5 gap-5"):
        ui.label("依頼・経過・結論").classes("text-3xl font-bold")
        ui.label(
            "何を依頼し、どの担当が何を調べ、統括役がどう判断したかを記録します。"
            "情報が必要になったらこの画面から回答できます。"
        ).classes("secretary-muted")

        def select_task(task_id: str):
            selected["task_id"] = task_id
            rendered["revision"] = None
            show_tasks.refresh()
            show_detail.refresh()

        with ui.row().classes("w-full items-start gap-5 flex-wrap lg:flex-nowrap"):
            with ui.column().classes("w-full lg:w-96 gap-4"):
                with ui.card().classes("w-full secretary-surface p-5"):
                    ui.label("新しい依頼").classes("text-xl font-semibold")
                    question = ui.textarea(
                        "依頼内容",
                        value="架空テストPCで架空ゲームAが突然終了します。"
                              "以前の経験から原因わかる？",
                    ).props("outlined autogrow")
                    question.classes("w-full secretary-wrap")
                    target = ui.input("対象", value="架空テストPC").classes("w-full")
                    domain = ui.input("領域", value="pc").classes("w-full")
                    mode = ui.select({
                        "fixture": "架空の試験データ",
                        "live": "実記憶（既存API・読み取り専用）",
                    }, value="fixture", label="情報源").classes("w-full")
                    model = ui.select({
                        "qwen3:8b": "Qwen3 8B",
                        "gemma4:12b": "Gemma 4 12B",
                        "gpt-oss:20b": "GPT-OSS 20B",
                    }, value="qwen3:8b", label="統括・監査モデル").classes("w-full")
                    ui.label(
                        "試験データは架空PCのみ対応。実記憶モードでは"
                        "LSA_SECRETARY_API_TOKEN_FILEの設定が必要です。"
                        "実Web調査・PC操作は未実装です。"
                    ).classes("secretary-muted secretary-wrap")

                    def begin():
                        text = str(question.value or "").strip()
                        named = str(target.value or "").strip()
                        category = str(domain.value or "").strip()
                        source = str(mode.value or "")
                        selected_model = str(model.value or "")
                        if not text or not named or not category:
                            ui.notify("依頼、対象、領域を入力してください。", type="warning")
                            return
                        if source == "fixture" and (named, category) != (
                                "架空テストPC", "pc"):
                            ui.notify("試験データの対象は 架空テストPC / pc です。",
                                      type="warning")
                            return
                        if ACTIVE_TASKS:
                            ui.notify("別の依頼を処理中です。", type="warning")
                            return
                        task_id = str(uuid.uuid4())
                        if not launch(task_id, _execute_start, task_id, text,
                                      named, category, source, selected_model):
                            return
                        select_task(task_id)
                        ui.notify("依頼を受け付けました。判断履歴を更新しています。",
                                  type="positive")

                    ui.button("依頼を開始", on_click=begin, icon="play_arrow") \
                        .classes("w-full")

                @ui.refreshable
                def show_tasks():
                    with ui.card().classes("w-full secretary-surface p-4"):
                        ui.label("依頼履歴").classes("text-lg font-semibold")
                        items = task_summaries(STATE_DIR)
                        if not items:
                            ui.label("まだ依頼はありません。").classes("secretary-muted")
                        for item in items:
                            task_id = item["task_id"]
                            selected_item = task_id == selected["task_id"]
                            with ui.column().classes(
                                    "w-full gap-1 p-3 rounded-lg "
                                    + ("bg-blue-50" if selected_item else "bg-slate-50")):
                                ui.button(
                                    item["request"][:65],
                                    on_click=lambda tid=task_id: select_task(tid),
                                ).props("flat no-caps align=left") \
                                    .classes("text-left w-full secretary-wrap")
                                with ui.row().classes("items-center gap-2"):
                                    ui.badge(item["status_label"],
                                             color="blue" if selected_item else "grey")
                                    ui.label(item["modified"]).classes("secretary-muted")
                show_tasks()

            with ui.column().classes("w-full flex-1 min-w-0 gap-4"):
                @ui.refreshable
                def show_detail():
                    task_id = selected["task_id"]
                    if not task_id:
                        with ui.card().classes("w-full secretary-surface p-6"):
                            ui.label("依頼を選択してください。")
                        return
                    try:
                        state = load_state(task_id, STATE_DIR)
                    except (OSError, ValueError, KeyError):
                        with ui.card().classes("w-full secretary-surface p-6"):
                            ui.label("実行準備中です。").classes("secretary-muted")
                        return

                    status = str(state.get("status") or "unknown")
                    with ui.card().classes("w-full secretary-surface p-5 gap-3"):
                        with ui.row().classes("w-full items-center"):
                            ui.label("依頼内容").classes("text-xl font-bold")
                            ui.space()
                            ui.badge(STATUS_LABELS.get(status, status), color=(
                                "green" if status == "answered" else
                                "amber" if status == "waiting_user" else
                                "red" if status == "error" else "blue"))
                        ui.label(str(state.get("original_request") or "")) \
                            .classes("text-lg secretary-wrap")
                        ui.label(
                            "対象: " + str(state.get("target") or "")
                            + "　/　領域: " + str(state.get("domain") or "")
                            + "　/　モデル: " + str(state.get("model") or "")
                        ).classes("secretary-muted")
                        ui.label("ローカルTask ID: " + task_id).classes(
                            "text-xs text-slate-500 secretary-wrap")
                        if task_id in ACTIVE_TASKS:
                            with ui.row().classes("items-center gap-2"):
                                ui.spinner(size="sm")
                                ui.label("統括役・専門担当が処理中です")
                        if state.get("error"):
                            ui.label(str(state["error"])).classes(
                                "text-red-700 secretary-wrap")

                    if status == "waiting_user":
                        if state.get("latest_report"):
                            with ui.card().classes("w-full secretary-surface p-5"):
                                ui.label("現時点の報告（依頼は未完了）").classes(
                                    "text-lg font-bold text-amber-800")
                                ui.label(str(state["latest_report"])).classes(
                                    "secretary-wrap")
                        with ui.card().classes(
                                "w-full secretary-surface p-5 border-l-4 border-amber-400"):
                            ui.label("追加情報を教えてください").classes(
                                "text-lg font-bold text-amber-800")
                            ui.label(str(state.get("awaiting") or state.get("answer")
                                         or "詳しい状況を教えてください。")) \
                                .classes("secretary-wrap")
                            reply = ui.textarea(
                                "回答", placeholder="例: 終了時にエラー表示はありません"
                            ).props("outlined autogrow").classes("w-full")

                            def send_reply():
                                value = str(reply.value or "").strip()
                                if not value:
                                    ui.notify("回答を入力してください。", type="warning")
                                    return
                                if not can_reply(load_state(task_id, STATE_DIR)):
                                    ui.notify("この依頼は現在回答待ちではありません。",
                                              type="warning")
                                    return
                                if not launch(task_id, _execute_resume, task_id, value):
                                    ui.notify("別の処理が実行中です。", type="warning")
                                    return
                                # The question and its answer are preserved by
                                # Core.resume under this task_id.
                                ui.notify("回答を受け付け、同じ依頼を再開します。",
                                          type="positive")

                            ui.button("回答して再開", on_click=send_reply,
                                      icon="send").classes("self-start")

                    elif can_retry(state, ACTIVE_TASKS):
                        with ui.card().classes("w-full secretary-surface p-4"):
                            ui.label(
                                "以前の処理が途中で終了した可能性があります。"
                                "この実験版は読み取り専用です。"
                            ).classes("secretary-muted")

                            def retry():
                                if not launch(task_id, _execute_resume, task_id, ""):
                                    ui.notify("別の処理が実行中です。", type="warning")
                                    return
                                ui.notify("保存された依頼を再開します。",
                                          type="positive")

                            ui.button("保存状態から再開", on_click=retry,
                                      icon="restart_alt")

                    if status == "answered":
                        with ui.card().classes("w-full secretary-surface p-5"):
                            ui.label("今回の結論").classes(
                                "text-xl font-bold text-emerald-800")
                            ui.label(str(state.get("answer") or "回答なし")) \
                                .classes("secretary-wrap text-base")
                            ui.label("注: この実験の回答は実機の診断結果ではありません。") \
                                .classes("secretary-muted")
                            if (requires_continuation(state.get("original_request", ""))
                                    and not verified_objective(state)):
                                ui.label(
                                    "この依頼は元々、未解決の場合も調査を続ける"
                                    "よう求めています。旧版が回答済みにしたTaskも"
                                    "履歴を残して同じIDから再開できます。"
                                ).classes("secretary-muted")
                                continuation = ui.textarea(
                                    "追加情報（任意）",
                                    placeholder="新しいログ・確認結果があれば入力",
                                ).props("outlined autogrow").classes("w-full")

                                def reopen_task():
                                    if not launch(
                                            task_id, _execute_reopen, task_id,
                                            str(continuation.value or "").strip()):
                                        ui.notify("別の処理が実行中です。", type="warning")
                                        return
                                    ui.notify(
                                        "同じTaskを再開しました。過去の経過は保持します。",
                                        type="positive",
                                    )

                                ui.button(
                                    "未解決の依頼として調査を続ける",
                                    on_click=reopen_task, icon="restart_alt",
                                ).classes("self-start")
                    elif status == "running":
                        with ui.card().classes("w-full secretary-surface p-5"):
                            ui.label("処理を進めています。").classes(
                                "text-lg font-semibold")
                            ui.label("担当への指示と検証の経過は下に更新されます。") \
                                .classes("secretary-muted")

                    with ui.card().classes("w-full secretary-surface p-5"):
                        ui.label("判断・作業の経過").classes("text-xl font-bold")
                        events = event_rows(state)
                        if not events:
                            ui.label("まだ判断履歴はありません。").classes("secretary-muted")
                        for row in events:
                            accent = ("#d69e28" if row["kind"] in
                                      ("answer_review", "evidence_fallback") else
                                      "#3456bd" if row["kind"] in
                                      ("decision", "dispatch") else "#138657")
                            with ui.column().classes(
                                    "w-full gap-1 p-3 bg-slate-50 rounded-lg"
                                    ).style("border-left: 3px solid " + accent):
                                ui.label(str(row["index"]) + ". " + row["title"]) \
                                    .classes("font-semibold")
                                for detail in row["details"]:
                                    ui.label(detail).classes(
                                        "secretary-wrap text-sm text-slate-700")

                    with ui.card().classes("w-full secretary-surface p-5"):
                        ui.label("取得した証拠").classes("text-xl font-bold")
                        evidence = evidence_rows(state)
                        if not evidence:
                            ui.label("取得結果はまだありません。").classes(
                                "secretary-muted")
                        for record in evidence:
                            with ui.column().classes(
                                    "w-full gap-1 p-3 bg-slate-50 rounded-lg"):
                                with ui.row().classes("items-center gap-2"):
                                    ui.label(record["id"]).classes("font-semibold")
                                    ui.label(SPECIALIST_LABELS.get(
                                        record["specialist"], record["specialist"])
                                    ).classes("secretary-muted")
                                    for flag in record["flags"]:
                                        ui.badge(flag, color="orange" if flag in
                                                 ("模擬", "未検証", "対象との紐付けなし",
                                                  "架空資料") else "blue-grey")
                                ui.label(record["text"]).classes("secretary-wrap")
                                if record["source"]:
                                    ui.label("出典: " + record["source"]) \
                                        .classes("secretary-muted secretary-wrap")
                    # Keep a complete selectable trace at the VERY BOTTOM of
                    # the task. The source is the same persisted checkpoint as
                    # the human-friendly cards above, never a screenshot.
                    with ui.card().classes("w-full secretary-surface p-5 gap-3"):
                        ui.label("検証用ログ（全文コピー）").classes(
                            "text-xl font-bold")
                        ui.label(
                            "このTaskの元依頼・追加回答・全判断・全証拠・結論を"
                            "まとめています。上の画面に収まらない内容も、"
                            "下の欄からまとめてコピーできます。"
                        ).classes("secretary-muted secretary-wrap")

                        report = task_text_report(state)
                        ui.textarea("コピー用テキスト", value=report).props(
                            "outlined readonly rows=16"
                        ).classes("w-full font-mono secretary-wrap")
                        ui.button(
                            "ログ全文をコピー",
                            on_click=lambda value=report: ui.clipboard.write(value),
                            icon="content_copy",
                        ).classes("self-start")

                        with ui.expansion(
                                "詳細JSON（内部状態をそのままコピー）",
                                icon="data_object").classes("w-full"):
                            raw_json = json.dumps(
                                state, ensure_ascii=False, indent=2,
                                sort_keys=True, default=str,
                            )
                            ui.textarea(
                                "checkpoint JSON", value=raw_json,
                            ).props("outlined readonly rows=12").classes(
                                "w-full font-mono secretary-wrap"
                            )
                            ui.button(
                                "JSON全文をコピー",
                                on_click=lambda value=raw_json: ui.clipboard.write(value),
                                icon="content_copy",
                            )
                        if state.get("mode") == "live":
                            ui.label(
                                "注意：実記憶モードのログには個人情報が"
                                "含まれ得ます。外部への共有前に内容を確認してください。"
                            ).classes("text-amber-800 secretary-wrap")

                show_detail()

        def refresh_if_changed():
            task_id = selected["task_id"]
            new_revision = state_revision(task_id)
            if new_revision != rendered["revision"]:
                rendered["revision"] = new_revision
                show_detail.refresh()
                show_tasks.refresh()

        ui.timer(1.5, refresh_if_changed, immediate=False)


def main():
    port = int(os.getenv("LSA_GUI_PORT", "8091"))
    if not 1 <= port <= 65535:
        raise ValueError("LSA_GUI_PORT must be a valid local TCP port")
    # No authentication yet: never expose this experimental personal-data UI
    # on 0.0.0.0 or a LAN interface.
    ui.run(host="127.0.0.1", port=port, title="Local Secretary AI",
           show=False, reload=False)


if __name__ == "__main__":
    main()
