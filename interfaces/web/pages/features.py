"""features page for the daily portal."""


def sync(context: dict) -> None:
    globals().update(context)


def register(context: dict):
    sync(context)
    @ui.page("/features")
    def features_page():
        globals().update(context)
        with ui.column().classes("w-full max-w-7xl mx-auto gap-4 p-4"):
            _portal_header("機能一覧", "利用可能・試験中・未実装を日常GUIから確認")
            with ui.grid(columns=2).classes("w-full gap-4"):
                for name, status, color, target, description in _FEATURES:
                    with ui.card().classes("w-full"):
                        with ui.row().classes("w-full items-center justify-between"):
                            ui.label(name).classes("text-lg font-bold")
                            ui.badge(status, color=color)
                        ui.label(description).classes("text-sm")
                        if target:
                            ui.button("開く", icon="open_in_new", color="blue").props(
                                f"href={target} tag=a flat"
                            )
    
    features_page._context_sync = sync
    return features_page
