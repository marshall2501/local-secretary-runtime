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
                "現在のRuntime・PostgreSQL・DB件数・ER図・環境変数をread-onlyで確認します。",
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
    
                with ui.expansion(
                    "DB ER図",
                    value=False,
                    icon="account_tree",
                ).classes("w-full border-2 border-cyan-200 bg-cyan-50"):
                    ui.label(
                        "現在のDaily Runtimeが接続しているDBのschema metadataだけを読み取り、"
                        "table / column / PK / FKを可視化します。実データ行やSecretは読みません。"
                    ).classes("text-sm text-grey-7")

                    enabled_map = dict(
                        _UI_PREFERENCES.get("debug", {})
                        .get("er_diagram_providers", {})
                    )
                    provider_statuses = _schema_diagram_service.provider_statuses(
                        enabled_map
                    )

                    with ui.row().classes("w-full gap-3 flex-wrap"):
                        for status in provider_statuses:
                            if not status.enabled:
                                border = "border-grey-300 bg-grey-50"
                                state_text = "DISABLED"
                            elif status.available:
                                border = "border-green-300 bg-green-50"
                                state_text = "AVAILABLE"
                            else:
                                border = "border-orange-300 bg-orange-50"
                                state_text = "UNAVAILABLE"
                            with ui.card().classes(
                                "min-w-56 border-2 " + border
                            ):
                                ui.label(status.display_name).classes(
                                    "text-sm font-bold"
                                )
                                ui.label(state_text).classes("text-xs")
                                if status.version:
                                    ui.label(
                                        "version: " + str(status.version)
                                    ).classes("text-xs text-grey-7")
                                if status.dependency_status:
                                    ui.label(
                                        str(status.dependency_status)
                                    ).classes("text-xs text-grey-7")

                    provider_options = {
                        status.key: status.display_name
                        for status in provider_statuses
                    }
                    default_provider = (
                        "native_mermaid"
                        if "native_mermaid" in provider_options
                        else next(iter(provider_options), None)
                    )
                    with ui.row().classes(
                        "w-full gap-2 items-end flex-wrap"
                    ):
                        provider_select = ui.select(
                            options=provider_options,
                            value=default_provider,
                            label="ER図 Provider",
                        ).classes("min-w-64")
                        generate_button = ui.button(
                            "生成 / 更新",
                            icon="refresh",
                            color="cyan",
                        )

                    ui.label(
                        "外部Providerは設定で有効化しても、依存toolと安全な認証受け渡しが"
                        "確認できるまでは実行しません。自動installもしません。"
                    ).classes("text-xs text-grey-7")

                    result_area = ui.column().classes("w-full gap-2")

                    async def generate_er_diagram():
                        provider_key = str(provider_select.value or "")
                        generate_button.disable()
                        try:
                            result = await run.io_bound(
                                lambda: _schema_diagram_service.generate(
                                    provider_key,
                                    enabled=enabled_map,
                                )
                            )
                            result_area.clear()
                            with result_area:
                                if result.status == "ok":
                                    with ui.row().classes(
                                        "w-full gap-2 items-center flex-wrap"
                                    ):
                                        ui.badge("OK", color="green")
                                        ui.label(
                                            f"DB: {result.database} / "
                                            f"schema: {result.schema}"
                                        ).classes("text-sm")
                                        ui.label(
                                            f"tables={result.table_count} / "
                                            f"FK={result.relation_count} / "
                                            f"{result.duration_ms} ms"
                                        ).classes("text-xs text-grey-7")
                                    for warning in result.warnings:
                                        ui.label(
                                            "注意: " + str(warning)
                                        ).classes("text-xs text-orange-800")
                                    if (
                                        result.output_format == "mermaid"
                                        and result.content
                                    ):
                                        ui.mermaid(result.content).classes(
                                            "w-full overflow-auto bg-white p-2"
                                        )
                                    elif result.content:
                                        ui.code(result.content).classes(
                                            "w-full text-xs"
                                        )
                                else:
                                    color = (
                                        "grey"
                                        if result.status == "disabled"
                                        else "orange"
                                    )
                                    ui.badge(
                                        result.status.upper(),
                                        color=color,
                                    )
                                    for warning in result.warnings:
                                        ui.label(
                                            str(warning)
                                        ).classes("text-sm text-grey-7")
                        except Exception as exc:
                            result_area.clear()
                            with result_area:
                                ui.badge("ERROR", color="red")
                                ui.label(
                                    "ER図を生成できません: "
                                    + type(exc).__name__
                                ).classes("text-sm text-red-700")
                        finally:
                            generate_button.enable()

                    generate_button.on_click(generate_er_diagram)

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
