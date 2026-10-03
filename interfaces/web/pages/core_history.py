"""core history page for the daily portal."""


def sync(portal_context: dict) -> None:
    globals().update(portal_context)


def register(portal_context: dict):
    sync(portal_context)
    @ui.page("/core/history")
    def core_history_page():
        globals().update(portal_context)
        state = {"offset": 0}
        page_size = 20
        with ui.column().classes("w-full max-w-5xl mx-auto p-4 gap-3"):
            _nav()
            ui.label("Task履歴").classes("text-2xl font-bold")
            ui.link("RITSUKOへ戻る", "/core")
            ui.label("失敗を含む全状態のTaskを更新日時の新しい順で表示します。")
    
            def move_page(delta):
                state["offset"] = max(0, state["offset"] + delta * page_size)
                history.refresh()
    
            @ui.refreshable
            def history():
                try:
                    rows = load_recent_core_tasks(limit=page_size + 1, offset=state["offset"])
                except Exception as exc:
                    ui.label("履歴を取得できません: " + str(exc)).classes("text-red-700")
                    ui.button("再読み込み", on_click=history.refresh)
                    return
                with ui.row().classes("items-center"):
                    ui.button("前へ", on_click=lambda: move_page(-1)).set_enabled(state["offset"] > 0)
                    ui.label(f"{state['offset'] // page_size + 1}ページ")
                    ui.button("次へ", on_click=lambda: move_page(1)).set_enabled(len(rows) > page_size)
                if not rows:
                    ui.label("Taskはありません。")
                for item in rows[:page_size]:
                    with ui.card().classes("w-full gap-1"):
                        ui.badge(item["status"])
                        ui.label(item["request"])
                        ui.label(str(item["updated_at"])).classes("text-xs text-grey-7")
                        ui.link("Taskを開く", "/core?task_id=" + item["id"])
            history()
    
    core_history_page._context_sync = sync
    return core_history_page
