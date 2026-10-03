"""top page for the daily portal."""


def sync(portal_context: dict) -> None:
    globals().update(portal_context)


def register(portal_context: dict):
    sync(portal_context)
    @ui.page("/")
    def top_page():
        globals().update(portal_context)
        with ui.column().classes("w-full max-w-7xl mx-auto gap-4 p-4"):
            _portal_header(
                "Local Secretary",
                "Personal Local Secretary AI — 日常用ポータル（開発中）",
            )
    
            pending = _pending_count()
            debug_summary = _debug_database_summary()
            with ui.row().classes("w-full gap-4 flex-wrap"):
                with ui.card().classes("w-72 border-2 border-green-300 bg-green-50"):
                    ui.label("Personal Knowledge Base").classes("text-lg font-bold")
                    ui.label("記録・検索・履歴・例外確認").classes("text-sm")
                    ui.label(
                        "Pending: " + (str(pending) + "件" if pending is not None else "取得不可")
                    ).classes("text-sm text-purple-800")
                    ui.button("PKBを開く", icon="arrow_forward", color="green").props(
                        "href=/pkb tag=a"
                    )
                with ui.card().classes("w-72 border-2 border-blue-300 bg-blue-50"):
                    ui.label("家計・資産").classes("text-lg font-bold")
                    ui.label("保存済み家計表示＋MoneyForward CSV取込").classes("text-sm")
                    ui.label("隔離DBの保存済み明細・集計・Import履歴を表示").classes(
                        "text-xs text-blue-800"
                    )
                    ui.button("家計・資産を開く", icon="arrow_forward", color="blue").props(
                        "href=/finance tag=a"
                    )
                with ui.card().classes("w-72 border-2 border-grey-300 bg-grey-1"):
                    ui.label("予定").classes("text-lg font-bold")
                    ui.badge("未実装", color="grey")
                    ui.label("Google Calendar閲覧・検索を予定").classes("text-sm")
                with ui.card().classes("w-72 border-2 border-blue-grey-300 bg-blue-grey-1"):
                    ui.label("RITSUKO").classes("text-lg font-bold")
                    ui.badge("試験中", color="blue-grey")
                    ui.label("Secretary Core / Orchestrator。MAGIへ依頼し最終判断を管理").classes("text-sm")
                    ui.button("RITSUKOを開く", icon="arrow_forward", color="blue-grey").props(
                        "href=/core tag=a"
                    )
                with ui.card().classes("w-72 border-2 border-slate-300 bg-slate-50"):
                    ui.label("システム状態 / デバッグ").classes("text-lg font-bold")
                    if debug_summary["status"] == "ok":
                        ui.badge("DB OK", color="green")
                    elif debug_summary["status"] == "warning":
                        ui.badge("BOUNDARY WARNING", color="orange")
                    else:
                        ui.badge("DB NG", color="red")
                    ui.label(
                        "DB: " + str(debug_summary.get("database") or "unavailable")
                    ).classes("text-sm")
                    ui.label(
                        "Migration: "
                        + str(debug_summary.get("latest_migration") or "unknown")
                    ).classes("text-xs text-grey-7")
                    ui.button(
                        "システム状態を開く",
                        icon="monitor_heart",
                        color="blue-grey",
                    ).props("href=/debug tag=a")
    
            with ui.card().classes("w-full"):
                ui.label("開発中の現在地").classes("text-lg font-bold")
                ui.label("PKB: 架空隔離DBでEntity / Relation / Event / State縦断まで実機確認済み")
                ui.label("家計: MoneyForward CSV 2,270件を隔離DBへ保存し、保存済み表示へ拡張")
                ui.label("金融実データは隔離DBのみ。運用DB・外部金融サービス操作はまだ行いません。").classes(
                    "text-sm text-orange-800"
                )
    
    top_page._context_sync = sync
    return top_page
