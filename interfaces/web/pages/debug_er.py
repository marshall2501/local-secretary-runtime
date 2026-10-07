"""Dedicated read-only ER diagram debug page."""


def sync(portal_context: dict) -> None:
    globals().update(portal_context)


def register(portal_context: dict):
    sync(portal_context)

    @ui.page("/debug/er")
    def debug_er_page():
        globals().update(portal_context)

        with ui.column().classes("w-full max-w-7xl mx-auto gap-4 p-4"):
            _portal_header(
                "DB ER図",
                "現在のDaily Runtimeが接続しているDBからschema metadataだけを読み取り、"
                "table / column / PK / FKを可視化します。",
            )

            ui.link("← システム状態 / デバッグへ戻る", "/debug").classes(
                "text-sm text-cyan-800 font-bold"
            )

            ui.label(
                "実データ行・Secretは読みません。外部Providerは自動installせず、"
                "安全な認証受け渡しと依存toolを確認した方式だけ実行します。"
            ).classes("text-sm text-orange-800")

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

            with ui.card().classes(
                "w-full border-2 border-cyan-200 bg-cyan-50"
            ):
                with ui.row().classes(
                    "w-full gap-2 items-end flex-wrap"
                ):
                    provider_select = ui.select(
                        options=provider_options,
                        value=default_provider,
                        label="ER図 Provider",
                    ).classes("min-w-64")
                    density_select = ui.select(
                        options={
                            "keys": "キー中心",
                            "all": "全カラム",
                        },
                        value="keys",
                        label="表示密度",
                    ).classes("min-w-40")
                    generate_button = ui.button(
                        "生成 / 更新",
                        icon="refresh",
                        color="cyan",
                    )

                ui.label(
                    "キー中心を既定にして全体を俯瞰し、必要なときだけ全カラムへ切り替えます。"
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
                            options={
                                "keys_only": density_select.value == "keys",
                            },
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
                                zoom = {"value": 1.0}

                                with ui.row().classes(
                                    "w-full gap-2 items-center flex-wrap"
                                ):
                                    ui.label("図のズーム").classes(
                                        "text-xs text-grey-7"
                                    )
                                    zoom_label = ui.label("100%").classes(
                                        "text-sm font-bold min-w-12"
                                    )

                                    def apply_zoom(value: float) -> None:
                                        zoom["value"] = max(
                                            0.5,
                                            min(2.5, round(value, 2)),
                                        )
                                        zoom_label.set_text(
                                            f"{int(zoom['value'] * 100)}%"
                                        )
                                        ui.run_javascript(
                                            "const el = document.querySelector("
                                            "'.er-diagram-canvas'); "
                                            "if (el) { el.style.zoom = "
                                            + repr(str(zoom["value"]))
                                            + "; }"
                                        )

                                    ui.button(
                                        "－",
                                        on_click=lambda: apply_zoom(
                                            zoom["value"] - 0.25
                                        ),
                                    ).props("dense outline")
                                    ui.button(
                                        "100%",
                                        on_click=lambda: apply_zoom(1.0),
                                    ).props("dense outline")
                                    ui.button(
                                        "＋",
                                        on_click=lambda: apply_zoom(
                                            zoom["value"] + 0.25
                                        ),
                                    ).props("dense outline")
                                    ui.button(
                                        "全画面",
                                        icon="fullscreen",
                                        on_click=lambda: ui.run_javascript(
                                            "const el = document.querySelector("
                                            "'.er-diagram-viewport'); "
                                            "if (el && el.requestFullscreen) { "
                                            "el.requestFullscreen(); }"
                                        ),
                                    ).props("dense outline")
                                    ui.label(
                                        "50〜250% / 全画面はEscで解除"
                                    ).classes("text-xs text-grey-7")

                                with ui.element("div").classes(
                                    "er-diagram-viewport w-full "
                                    "h-[65vh] min-h-[520px] overflow-auto "
                                    "bg-white border border-grey-300 p-2"
                                ):
                                    ui.mermaid(result.content).classes(
                                        "er-diagram-canvas inline-block"
                                    ).style(
                                        "transform-origin: top left; zoom: 1;"
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

    debug_er_page._context_sync = sync
    return debug_er_page
