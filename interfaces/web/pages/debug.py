"""debug page for the daily portal."""


def sync(portal_context: dict) -> None:
    globals().update(portal_context)


def register(portal_context: dict):
    sync(portal_context)
    @ui.page("/debug")
    def debug_page():
        globals().update(portal_context)
        def kv_rows(mapping: dict, keys: tuple[tuple[str, str], ...]) -> list[dict]:
            return [
                {"item": label, "value": str(mapping.get(key, "-"))}
                for key, label in keys
            ]
    
        with ui.column().classes("w-full max-w-7xl mx-auto gap-4 p-4"):
            _portal_header(
                "システム状態 / デバッグ",
                "現在のRuntime・PostgreSQL・DB件数・環境変数をread-onlyで確認する診断ダッシュボードです。",
            )
            ui.label(
                "この画面は状態表示だけを行い、DB・環境変数・Docker設定を変更しません。"
                " Secret系環境変数は値を表示せずSET / NOT SETだけを表示します。"
            ).classes("text-sm text-orange-800")
    
            @ui.refreshable
            def diagnostic_panel():
                snapshot = _load_system_debug_snapshot()
                runtime = snapshot["runtime"]
                database = snapshot["database"]
                environment = snapshot["environment"]
                identity = database.get("identity") or {}
                migration = database.get("migration") or {}
    
                with ui.row().classes("w-full gap-3 flex-wrap"):
                    with ui.card().classes("min-w-56 border-2 border-blue-grey-200"):
                        ui.label("Runtime").classes("text-xs text-grey-7")
                        ui.label(
                            str(runtime.get("branch") or "unknown")
                            + " / "
                            + str(runtime.get("commit") or "unknown")[:12]
                        ).classes("text-lg font-bold")
                    with ui.card().classes(
                        "min-w-56 border-2 "
                        + (
                            "border-green-300 bg-green-50"
                            if database.get("status") == "ok"
                            and database.get("boundary_ok")
                            else "border-red-300 bg-red-50"
                        )
                    ):
                        ui.label("PostgreSQL").classes("text-xs text-grey-7")
                        ui.label(
                            "OK"
                            if database.get("status") == "ok"
                            and database.get("boundary_ok")
                            else "CHECK"
                        ).classes("text-lg font-bold")
                        ui.label(str(identity.get("database") or "unavailable")).classes(
                            "text-sm"
                        )
                    with ui.card().classes("min-w-56 border-2 border-indigo-200"):
                        ui.label("Migration").classes("text-xs text-grey-7")
                        ui.label(str(migration.get("latest") or "unknown")).classes(
                            "text-sm font-bold"
                        )
                        ui.label(
                            "count="
                            + str(migration.get("count") or "unknown")
                            + " / "
                            + str(migration.get("source") or "unknown")
                        ).classes("text-xs text-grey-7")
    
                if database.get("error"):
                    ui.label(
                        "DB診断エラー: " + str(database["error"])
                    ).classes("text-sm text-red-700")
    
                with ui.expansion(
                    "Runtime",
                    value=True,
                    icon="memory",
                ).classes("w-full border-2 border-blue-grey-200 bg-blue-grey-50"):
                    runtime_rows = kv_rows(
                        runtime,
                        (
                            ("branch", "Git branch"),
                            ("commit", "Git commit"),
                            ("python", "Python"),
                            ("python_executable", "Python executable"),
                            ("platform", "Platform"),
                            ("pid", "Process PID"),
                            ("working_directory", "Working directory"),
                            ("runtime_root", "Runtime root"),
                            ("process_started_at", "Process started"),
                            ("fetched_at", "取得時刻"),
                        ),
                    )
                    ui.table(
                        columns=[
                            {"name": "item", "label": "項目", "field": "item", "align": "left"},
                            {"name": "value", "label": "現在値", "field": "value", "align": "left"},
                        ],
                        rows=runtime_rows,
                        row_key="item",
                    ).classes("w-full")
    
                with ui.expansion(
                    "PostgreSQL",
                    value=True,
                    icon="storage",
                ).classes("w-full border-2 border-green-200 bg-green-50"):
                    db_rows = kv_rows(
                        identity,
                        (
                            ("database", "Database"),
                            ("user", "DB Role"),
                            ("host", "Host"),
                            ("port", "Port"),
                            ("server_version", "PostgreSQL"),
                            ("database_size", "DB Size"),
                            ("backend_pid", "Backend PID"),
                            ("database_connections", "DB Connections"),
                        ),
                    )
                    db_rows.extend(
                        [
                            {
                                "item": "Isolation boundary",
                                "value": (
                                    "OK"
                                    if database.get("boundary_ok")
                                    else "WARNING / NG"
                                ),
                            },
                            {
                                "item": "Latest migration",
                                "value": str(migration.get("latest") or "unknown"),
                            },
                            {
                                "item": "Migration count",
                                "value": str(migration.get("count") or "unknown"),
                            },
                            {
                                "item": "Migration source",
                                "value": str(migration.get("source") or "unknown"),
                            },
                        ]
                    )
                    ui.table(
                        columns=[
                            {"name": "item", "label": "項目", "field": "item", "align": "left"},
                            {"name": "value", "label": "現在値", "field": "value", "align": "left"},
                        ],
                        rows=db_rows,
                        row_key="item",
                    ).classes("w-full")
                    if migration.get("database_read") == "unavailable":
                        ui.label(
                            "migration履歴は現在のDB Roleから直接読めないため、"
                            "ランチャー起動前検証の値を表示しています。"
                        ).classes("text-xs text-grey-7")
    
                with ui.expansion(
                    "DB主要データ件数",
                    value=True,
                    icon="table_chart",
                ).classes("w-full border-2 border-teal-200 bg-teal-50"):
                    count_rows = [
                        {
                            "item": item["label"],
                            "relation": item["relation"],
                            "count": (
                                f"{item['count']:,}"
                                if item.get("count") is not None
                                else "-"
                            ),
                            "status": item.get("status") or "unknown",
                        }
                        for item in database.get("counts") or []
                    ]
                    if count_rows:
                        ui.table(
                            columns=[
                                {"name": "item", "label": "データ", "field": "item", "align": "left"},
                                {"name": "relation", "label": "Relation", "field": "relation", "align": "left"},
                                {"name": "count", "label": "件数", "field": "count", "align": "right"},
                                {"name": "status", "label": "状態", "field": "status", "align": "left"},
                            ],
                            rows=count_rows,
                            row_key="relation",
                        ).classes("w-full")
                    else:
                        ui.label("DB件数を取得できません。").classes("text-sm text-red-700")
    
                with ui.expansion(
                    "DB Relation一覧",
                    value=False,
                    icon="view_list",
                ).classes("w-full border border-grey-300 bg-white"):
                    relation_rows = database.get("relations") or []
                    ui.label(
                        f"secretary schema: {len(relation_rows)} relations"
                    ).classes("text-sm text-grey-7")
                    if relation_rows:
                        ui.table(
                            columns=[
                                {"name": "name", "label": "Name", "field": "name", "align": "left"},
                                {"name": "type", "label": "Type", "field": "type", "align": "left"},
                            ],
                            rows=relation_rows,
                            row_key="name",
                        ).classes("w-full")
    
                enabled_map = dict(
                    _UI_PREFERENCES.get("debug", {})
                    .get("er_diagram_providers", {})
                )
                er_statuses = _schema_diagram_service.provider_statuses(
                    enabled_map
                )
                native_status = next(
                    (
                        status
                        for status in er_statuses
                        if status.key == "native_mermaid"
                    ),
                    None,
                )
                external_enabled = sum(
                    1
                    for status in er_statuses
                    if status.key != "native_mermaid" and status.enabled
                )

                with ui.card().classes(
                    "w-full border-2 border-cyan-200 bg-cyan-50"
                ):
                    with ui.row().classes(
                        "w-full items-center gap-4 flex-wrap"
                    ):
                        ui.icon("account_tree").classes("text-cyan-800")
                        with ui.column().classes("gap-0 grow"):
                            ui.label("DB ER図").classes("text-sm font-bold")
                            if (
                                native_status is not None
                                and native_status.enabled
                                and native_status.available
                            ):
                                ui.label(
                                    "Native + Mermaid: 利用可能"
                                ).classes("text-xs text-green-800")
                            else:
                                ui.label(
                                    "Native + Mermaid: 要確認"
                                ).classes("text-xs text-orange-800")
                            ui.label(
                                f"外部Provider有効: {external_enabled} / 3"
                            ).classes("text-xs text-grey-7")
                        ui.link(
                            "ER図を開く",
                            "/debug/er",
                        ).classes(
                            "text-sm text-cyan-800 font-bold"
                        )

                with ui.expansion(
                    "Environment",
                    value=False,
                    icon="tune",
                ).classes("w-full border-2 border-amber-200 bg-amber-50"):
                    ui.label(
                        "Local Secretaryが利用するallowlistだけを表示します。"
                        " API Key / Secretの実値は表示しません。"
                    ).classes("text-sm text-grey-7")
                    env_rows = [
                        {
                            "name": item["name"],
                            "value": item["value"],
                            "kind": "SECRET (masked)" if item["secret"] else "value",
                        }
                        for item in environment
                    ]
                    ui.table(
                        columns=[
                            {"name": "name", "label": "環境変数", "field": "name", "align": "left"},
                            {"name": "value", "label": "現在値 / 状態", "field": "value", "align": "left"},
                            {"name": "kind", "label": "表示種別", "field": "kind", "align": "left"},
                        ],
                        rows=env_rows,
                        row_key="name",
                    ).classes("w-full")
    
            ui.button(
                "最新状態を再取得",
                icon="refresh",
                color="blue-grey",
                on_click=diagnostic_panel.refresh,
            )
            diagnostic_panel()
    
    debug_page._context_sync = sync
    return debug_page
