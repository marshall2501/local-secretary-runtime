"""service billing page for the daily portal."""


def sync(context: dict) -> None:
    globals().update(context)


def register(context: dict):
    sync(context)
    @ui.page("/service-billing")
    def service_billing_page():
        globals().update(context)
        state = {
            "result": None,
            "error": None,
            "busy": False,
            "profiles": [],
            "connections": {},
        }
    
        try:
            with connection() as db:
                bootstrap_connection_auth_from_env(db)
                bootstrap_openai_billing_profile(db)
                state["profiles"] = list_service_billing_profiles(db)
                for profile in state["profiles"]:
                    state["connections"][profile["connection_id"]] = (
                        get_service_connection(db, profile["connection_id"])
                    )
        except Exception as exc:
            state["error"] = "利用料金・契約設定を読み込めません: " + str(exc)[:180]
    
        profile_by_id = {item["id"]: item for item in state["profiles"]}
        profile_options = {
            item["id"]: (
                item["display_name"]
                + " → "
                + item["connection_name"]
                + " ["
                + item["adapter_key"]
                + "]"
            )
            for item in state["profiles"]
        }
    
        with ui.column().classes("w-full max-w-6xl mx-auto gap-4 p-4"):
            _portal_header(
                "利用料金・契約",
                "Service Billing Profileが明示的に選んだService Connection経由で利用量・料金・契約状況を読取表示します。",
            )
    
            with ui.card().classes("w-full border-2 border-indigo-200"):
                ui.label("Service Billing").classes("text-xl font-bold")
                ui.label(
                    "利用量 / charges / subscription / limits / creditsを別概念として扱い、"
                    "取得できない値を0とはみなしません。Adapterごとに取得可能な情報だけを正規化します。"
                ).classes("text-sm text-grey-7")
                ui.label(
                    "接続先・認証情報は設定画面のService Connectionで管理し、"
                    "利用料金・契約はService Billing Profileのconnection_idだけを使用します。"
                ).classes("text-xs text-grey-7")
    
                profile_select = ui.select(
                    options=profile_options,
                    value=(
                        state["profiles"][0]["id"]
                        if state["profiles"]
                        else None
                    ),
                    label="Service Billing Profile",
                ).classes("w-full")
    
                with ui.row().classes("w-full items-center justify-between gap-3"):
                    status = ui.label(
                        state["error"] or (
                            "未取得"
                            if state["profiles"]
                            else "Service Billing Profileがありません。設定画面でConnectionを割り当ててください。"
                        )
                    ).classes(
                        "text-sm text-red-700"
                        if state["error"]
                        else "text-sm text-grey-7"
                    )
                    refresh_button = ui.button(
                        "最新を取得",
                        icon="refresh",
                        color="indigo",
                    )
                    if not state["profiles"]:
                        refresh_button.disable()
    
                metrics = ui.row().classes("w-full gap-3 flex-wrap")
                model_table = ui.column().classes("w-full gap-2")
                debug_panel = ui.column().classes("w-full")
    
                def render_result(result: dict | None, error: str | None = None):
                    metrics.clear()
                    model_table.clear()
                    debug_panel.clear()
                    if error:
                        status.set_text(error)
                        status.classes(replace="text-sm text-red-700")
                        return
                    if not result:
                        status.set_text("未取得")
                        return
    
                    status.classes(replace="text-sm text-grey-7")
                    status.set_text(
                        "取得: "
                        + str(result.get("fetched_at") or "-")
                        + " / connection: "
                        + str(result.get("connection_name") or "-")
                    )
                    activity = result.get("activity") or {}
                    totals = activity.get("totals") or {}
                    charges = result.get("charges") or {}
                    charge_values = charges.get("values") or {}
                    charge_text = " / ".join(
                        f"{currency.upper()} {value:,.4f}"
                        for currency, value in sorted(charge_values.items())
                    ) or (
                        "0"
                        if charges.get("status") == "known"
                        else "unknown"
                    )
    
                    with metrics:
                        for title, value in (
                            ("今月の料金", charge_text),
                            (
                                "Requests",
                                f"{int(totals.get('requests') or 0):,}"
                                if activity.get("status") in {"known", "partial"}
                                else "unknown",
                            ),
                            (
                                "Input tokens",
                                f"{int(totals.get('input_tokens') or 0):,}"
                                if activity.get("status") in {"known", "partial"}
                                else "unknown",
                            ),
                            (
                                "Output tokens",
                                f"{int(totals.get('output_tokens') or 0):,}"
                                if activity.get("status") in {"known", "partial"}
                                else "unknown",
                            ),
                            (
                                "Limits",
                                str((result.get("limits") or {}).get("status") or "unknown"),
                            ),
                            (
                                "Credits",
                                str((result.get("credits") or {}).get("status") or "unknown"),
                            ),
                            (
                                "Subscription",
                                str((result.get("subscription") or {}).get("status") or "unknown"),
                            ),
                        ):
                            with ui.card().classes("min-w-48 bg-indigo-50"):
                                ui.label(title).classes("text-xs text-grey-7")
                                ui.label(value).classes("text-lg font-bold")
    
                    with model_table:
                        ui.label("モデル別利用量").classes("font-bold")
                        rows = activity.get("by_model") or []
                        if not rows:
                            ui.label(
                                "この期間のモデル別利用実績はありません。"
                            ).classes("text-sm text-grey-7")
                        else:
                            ui.table(
                                columns=[
                                    {
                                        "name": "model",
                                        "label": "Model",
                                        "field": "model",
                                        "align": "left",
                                    },
                                    {
                                        "name": "requests",
                                        "label": "Requests",
                                        "field": "requests",
                                        "align": "right",
                                    },
                                    {
                                        "name": "input_tokens",
                                        "label": "Input",
                                        "field": "input_tokens",
                                        "align": "right",
                                    },
                                    {
                                        "name": "output_tokens",
                                        "label": "Output",
                                        "field": "output_tokens",
                                        "align": "right",
                                    },
                                ],
                                rows=rows,
                                row_key="model",
                            ).classes("w-full")

                    with debug_panel:
                        with ui.expansion(
                            "取得デバッグ（Secret非表示）",
                            icon="bug_report",
                            value=False,
                        ).classes("w-full border border-indigo-200"):
                            safe_debug = {
                                "service": result.get("service"),
                                "connection_name": result.get("connection_name"),
                                "credential_source": result.get("credential_source"),
                                "period_start": result.get("period_start"),
                                "fetched_at": result.get("fetched_at"),
                                "metadata": result.get("metadata") or {},
                            }
                            ui.code(
                                json.dumps(
                                    safe_debug,
                                    ensure_ascii=False,
                                    indent=2,
                                    default=str,
                                )
                            ).classes("w-full text-xs")
    
                async def refresh():
                    if state["busy"]:
                        return
                    profile_id = str(profile_select.value or "").strip()
                    profile = profile_by_id.get(profile_id)
                    if profile is None:
                        ui.notify(
                            "Service Billing Profileを選択してください",
                            type="warning",
                        )
                        return
                    selected = state["connections"].get(profile["connection_id"])
                    if selected is None:
                        ui.notify(
                            "割り当てられたService Connectionを読み込めません",
                            type="negative",
                        )
                        return
                    state["busy"] = True
                    refresh_button.disable()
                    status.set_text(
                        str(profile.get("display_name") or "Service Billing")
                        + " / "
                        + str(selected.get("display_name") or "Connection")
                        + " から取得中..."
                    )
                    try:
                        result = await run.io_bound(
                            read_service_billing_snapshot,
                            selected,
                        )
                        state["result"] = result
                        state["error"] = None
                        render_result(result)
                    except ServiceBillingError as exc:
                        state["error"] = str(exc)
                        render_result(None, state["error"])
                    except Exception:
                        state["error"] = (
                            "利用料金・契約の取得で予期しないエラーが発生しました。"
                        )
                        render_result(None, state["error"])
                    finally:
                        state["busy"] = False
                        refresh_button.enable()
    
                refresh_button.on_click(refresh)
    
            ui.label(
                "取得値はConnection設定へ保存しません。料金・契約・残高・利用上限は取得できない限り"
                "unknownとして扱います。接続先の変更は設定画面のProfile→Connection割当で行います。"
            ).classes("text-xs text-grey-7")
    
    service_billing_page._context_sync = sync
    return service_billing_page
