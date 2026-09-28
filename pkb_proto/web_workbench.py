"""NiceGUI + FastAPI facade for fictional local experiments.

Run with: python -m pkb_proto.web_workbench
Single localhost port 8092; no PostgreSQL credentials, cloud endpoints, or
automatic model downloads. Developer-only sandbox, NOT the daily PKB UI.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from fastapi import HTTPException
from nicegui import app, ui
from pydantic import BaseModel, Field

from .diagnostic_cases import MODES, build_ollama_payload
from .web_workbench_core import ExperimentRunner, RunStore

DB_PATH = Path(os.environ.get(
    "LSA_WORKBENCH_DB",
    str(Path.home() / ".local-secretary-ai" / "workbench" / "runs.sqlite3"),
))
store = RunStore(DB_PATH)
runner = ExperimentRunner(store)


class RunInput(BaseModel):
    episode_id: str = "pc-01"
    mode: str = "抽出：簡略"
    model: str = Field(default="qwen3.5:9b", min_length=1, max_length=100)
    predict: int = 1100
    think: str = "自動"


@app.get("/api/workbench/episodes")
def episodes():
    return [{"id": ep["id"], "domain": ep["domain"], "text": ep["text"]}
            for ep in runner.episodes.values()]


@app.get("/api/workbench/models")
def models():
    try:
        return runner.installed_models()
    except OSError as exc:
        raise HTTPException(503, "Local Ollama is unavailable") from exc


@app.get("/api/workbench/runs")
def runs(limit: int = 50):
    return store.list(limit)


@app.get("/api/workbench/runs/{run_id}")
def get_run(run_id: str):
    record = store.get(run_id)
    if record is None:
        raise HTTPException(404, "Run not found")
    return record


@app.post("/api/workbench/runs", status_code=202)
def create_run(params: RunInput):
    try:
        return runner.submit(**params.model_dump())
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except OSError as exc:
        raise HTTPException(503, "Local Ollama is unavailable") from exc


@app.post("/api/workbench/runs/{run_id}/rerun", status_code=202)
def rerun(run_id: str):
    try:
        return runner.rerun(run_id)
    except KeyError as exc:
        raise HTTPException(404, "Run not found") from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except OSError as exc:
        raise HTTPException(503, "Local Ollama is unavailable") from exc


def as_json(data):
    return json.dumps(data, ensure_ascii=False, indent=2)


@ui.page("/")
def index():
    selected = {"detail": None, "left": None, "right": None}
    notify_on_finish = set()
    with ui.column().classes("w-full max-w-7xl mx-auto gap-4 p-4"):
        ui.label("Local Secretary — Development Workbench").classes("text-2xl font-bold")
        ui.label("架空PC/RCのみ • 127.0.0.1のOllamaのみ • DB接続なし • 未検証の候補は記憶登録しません").classes(
            "text-sm text-green-700"
        )
        ui.label("日常用PKB画面とは別の、開発・診断専用画面です。").classes("text-sm")

        with ui.card().classes("w-full"):
            ui.label("実験条件").classes("text-lg font-bold")
            with ui.row().classes("w-full gap-4 items-end"):
                model = ui.input("インストール済みモデル名", value="qwen3.5:9b").classes("w-52")
                episode = ui.select(
                    list(runner.episodes), value="pc-01", label="架空Episode"
                ).classes("w-40")
                mode = ui.select(list(MODES), value="抽出：簡略", label="検証モード").classes("w-52")
                predict = ui.select([1100, 2048, 4096], value=1100, label="生成上限").classes("w-36")
                think = ui.select(["自動", "無効"], value="自動", label="Thinking").classes("w-32")
            installed_label = ui.label("モデル一覧は「モデルを確認」で取得できます。")

            def check_models():
                try:
                    installed_label.set_text("ローカルモデル: " + ", ".join(runner.installed_models()))
                except OSError:
                    ui.notify("127.0.0.1:11434 のOllamaへ接続できません", type="negative")

            @ui.refreshable
            def request_preview():
                try:
                    payload = build_ollama_payload(
                        runner.episodes[episode.value], model.value.strip(),
                        int(predict.value), think.value, mode.value,
                    )
                    ui.label("実際に送信するJSON").classes("font-medium")
                    ui.code(as_json(payload), language="json").classes("w-full")
                except (ValueError, KeyError, TypeError):
                    ui.label("実験条件を確認してください。")

            def refresh_views():
                status_panel.refresh()
                history.refresh()
                detail.refresh()
                comparison.refresh()

            def do_run():
                try:
                    record = runner.submit(
                        episode_id=episode.value, mode=mode.value,
                        model=model.value.strip(), predict=int(predict.value),
                        think=think.value,
                    )
                    selected["detail"] = record["id"]
                    notify_on_finish.add(record["id"])
                    ui.notify("実験を受付：状態を画面上部に表示します", type="info")
                    refresh_views()
                except (ValueError, OSError, TypeError) as exc:
                    ui.notify(str(exc)[:240], type="negative")

            with ui.row().classes("items-center gap-3"):
                ui.button("モデルを確認", on_click=check_models)
                run_button = ui.button("この条件で実行", on_click=do_run, color="green")
                ui.button("結果を更新", on_click=refresh_views)
            for component in (model, episode, mode, predict, think):
                component.on_value_change(lambda _: request_preview.refresh())
            request_preview()

        @ui.refreshable
        def status_panel():
            # Prefer an active job; otherwise show the selected or latest run.
            records = store.list(50)
            active = next((r for r in records if r["status"] == "running"), None)
            if active is None:
                active = next((r for r in records if r["status"] == "queued"), None)
            selected_record = store.get(selected["detail"]) if selected["detail"] else None
            record = active or selected_record or (records[0] if records else None)
            run_button.set_enabled(not bool(active))
            with ui.card().classes("w-full border-2 border-blue-300"):
                if record is None:
                    ui.label("実行状態：未実行").classes("text-xl font-bold")
                    ui.label("モデルと条件を選び、実行してください。")
                    return
                state = record["status"]
                labels = {
                    "queued": ("実行待ち", "orange"),
                    "running": ("実行中", "orange"),
                    "completed": ("実行終了・形式検査通過", "green"),
                    "check_failed": ("実行終了・検査不合格", "red"),
                    "timeout": ("タイムアウト", "red"),
                    "connection_error": ("通信エラー", "red"),
                    "error": ("実行エラー", "red"),
                    "interrupted": ("前回起動時の処理が中断", "red"),
                }
                title, color = labels.get(state, (state, "grey"))
                with ui.row().classes("items-center gap-3"):
                    if state in ("running", "queued"):
                        ui.spinner(size="lg", color="orange")
                    ui.badge(title, color=color).classes("text-lg p-2")
                    ui.label(record["model"] + " / " + record["episode_id"]).classes("text-lg")
                    ui.label("Run " + record["id"][:10]).classes("font-mono text-xs")
                if state in ("running", "queued"):
                    if record["started_at"]:
                        try:
                            started = datetime.fromisoformat(record["started_at"])
                            elapsed = max(0, int((datetime.now(timezone.utc) - started).total_seconds()))
                            ui.label(f"実行経過：{elapsed}秒（自動更新：2秒間隔）").classes("text-lg")
                        except ValueError:
                            ui.label("実行時間を取得できません")
                    else:
                        ui.label("モデル実行待ち")
                    ui.label("実行中はこの条件の実行ボタンを無効化します。")
                else:
                    elapsed = (record["result"] or {}).get("elapsed_seconds")
                    if elapsed is not None:
                        ui.label(f"実行時間：{elapsed}秒").classes("text-lg")
                    if record["result"]:
                        ui.label("検査結果：" + record["result"].get("check", "未判定"))
                    if state == "completed":
                        ui.label("JSON形式などの簡易検査が通過しました。意味の正確性は別検証です。").classes("text-sm")
                    if record["error"]:
                        ui.label(record["error"]).classes("text-red-600")
                    if record["id"] in notify_on_finish:
                        notify_on_finish.discard(record["id"])
                        ui.notify(title + "：" + record["model"], type=(
                            "positive" if state == "completed" else "warning"
                        ))

        status_panel()

        def select_detail(run_id):
            selected["detail"] = run_id
            detail.refresh()

        def select_compare(side, run_id):
            selected[side] = run_id
            comparison.refresh()

        def rerun_saved(run_id):
            try:
                record = runner.rerun(run_id)
                selected["detail"] = record["id"]
                notify_on_finish.add(record["id"])
                ui.notify("保存された条件で新しいRunを受付", type="info")
                refresh_views()
            except (KeyError, ValueError, OSError) as exc:
                ui.notify(str(exc)[:240], type="negative")

        @ui.refreshable
        def history():
            with ui.card().classes("w-full"):
                ui.label("実行履歴（最新50件）").classes("text-lg font-bold")
                records = store.list(50)
                if not records:
                    ui.label("まだRunがありません。")
                for run in records:
                    with ui.row().classes("w-full items-center gap-3"):
                        ui.label(run["id"][:10]).classes("font-mono text-xs")
                        ui.badge(run["status"], color=(
                            "green" if run["status"] == "completed"
                            else "orange" if run["status"] in ("queued", "running")
                            else "red"
                        ))
                        ui.label(f'{run["model"]} / {run["episode_id"]} / {run["mode"]}').classes(
                            "text-sm grow"
                        )
                        ui.button("詳細", on_click=lambda rid=run["id"]: select_detail(rid)).props(
                            "flat dense"
                        )
                        ui.button("比較A", on_click=lambda rid=run["id"]: select_compare("left", rid)).props(
                            "flat dense"
                        )
                        ui.button("比較B", on_click=lambda rid=run["id"]: select_compare("right", rid)).props(
                            "flat dense"
                        )
                        ui.button("再試験", on_click=lambda rid=run["id"]: rerun_saved(rid)).props(
                            "flat dense"
                        )

        def show_record(run):
            ui.label(f'Run {run["id"]} / {run["status"]}').classes("font-mono")
            ui.label(f'{run["model"]} / {run["mode"]} / {run["episode_id"]}')
            if run["error"]:
                ui.label(run["error"]).classes("text-red-600")
            if run["result"]:
                result = run["result"]
                ui.label("判定: " + result.get("check", "未判定"))
                ui.label("終了理由: " + str(result.get("done_reason")))
                ui.label("総実行秒: " + str(result.get("elapsed_seconds", "未計測")))
                ui.label("回答本文（推論本文は保存しません）")
                ui.code(result.get("content", ""), language="json").classes("w-full")
                ui.label("その他の観測・候補（意味の正確性は別検証）")
                summary = {k: v for k, v in result.items()
                           if k not in ("content", "content_preview", "request_payload")}
                ui.code(as_json(summary), language="json").classes("w-full")
            ui.label("実際の送信JSON")
            ui.code(as_json(run["payload"]), language="json").classes("w-full")

        @ui.refreshable
        def detail():
            run_id = selected["detail"]
            with ui.card().classes("w-full"):
                ui.label("選択したRunの詳細").classes("text-lg font-bold")
                item = store.get(run_id) if run_id else None
                if item:
                    show_record(item)
                    ui.button("このRunのJSONをコピー",
                              on_click=lambda: ui.run_javascript(
                                  'navigator.clipboard.writeText(' + json.dumps(
                                      as_json(store.get(run_id)), ensure_ascii=False
                                  ) + ')'
                              ))
                else:
                    ui.label("履歴の「詳細」を選んでください。")

        @ui.refreshable
        def comparison():
            with ui.card().classes("w-full"):
                ui.label("二つのRunを比較").classes("text-lg font-bold")
                ui.label("履歴の「比較A」「比較B」でRunを選択。設定差も表示します。")
                with ui.row().classes("w-full gap-4 items-start"):
                    for side in ("left", "right"):
                        with ui.column().classes("w-full md:w-[48%]"):
                            item = store.get(selected[side]) if selected[side] else None
                            if item:
                                show_record(item)
                            else:
                                ui.label("比較" + ("A" if side == "left" else "B") + " 未選択")

        history()
        detail()
        comparison()
        ui.timer(2.0, refresh_views)


if __name__ == "__main__":
    ui.run(host="127.0.0.1", port=8092, reload=False, show=False, title="Local Secretary Workbench")
