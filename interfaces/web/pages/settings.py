"""settings page for the daily portal."""


def sync(portal_context: dict) -> None:
    globals().update(portal_context)


def register(portal_context: dict):
    sync(portal_context)
    @ui.page("/settings")
    def settings_page():
        globals().update(portal_context)
        pkb_labels = {
            "write": "記録",
            "correction": "訂正",
            "search": "検索・履歴",
            "entities": "Entity一覧",
            "pending": "確認待ち（Drawer）",
            "reviewed": "処理済みの確認待ち（Drawer）",
        }
        finance_labels = {
            "filter": "家計フィルタ・検索",
            "stored": "保存済み家計",
            "monthly": "保存済み月別集計",
            "categories": "保存済みカテゴリ別支出",
            "details": "保存済み明細",
            "imports": "Import履歴 / Source",
            "csv": "MoneyForward CSV 取込",
        }
        core_labels = {
            "trace": "Task単位の検証・稼働ログ",
            "screen_log": "画面全体の最近のCore稼働ログ",
            "limits": "この縦断でまだ行わないこと",
        }
    
        with ui.column().classes("w-full max-w-5xl mx-auto gap-4 p-4"):
            _portal_header(
                "設定",
                "接続・外部サービスと日常用GUIの表示・初期状態を変更します。",
            )
            ui.label(
                "接続・外部サービスの設定と、画面レイアウトの設定を分けて管理します。"
            ).classes("text-sm text-grey-7")
    
            with ui.expansion(
                "接続・外部サービス設定",
                value=True,
                icon="cable",
            ).classes("w-full border-2 border-indigo-300 bg-indigo-50"):
                ui.label(
                    "接続先・認証情報と、それを利用する機能の割当を管理します。"
                ).classes("text-sm text-grey-7")
                with ui.expansion(
                    "Service Connections",
                    value=False,
                    icon="hub",
                ).classes("w-full border-2 border-indigo-200 bg-indigo-50"):
                    ui.label(
                        "接続先と認証情報を1つのService ConnectionとしてPostgreSQLで共通管理します。"
                        "各機能は使用するconnection_idを明示的に選択します。"
                    ).classes("text-sm")
                    ui.label(
                        "connection_role / Capabilityは分類・技術情報であり、自動選択やRITSUKOの実行許可には使いません。"
                    ).classes("text-xs text-grey-7")
                    try:
                        with connection() as db:
                            bootstrap_connection_auth_from_env(db)
                            bootstrap_openai_billing_profile(db)
                            connection_rows = list_service_connections(
                                db,
                                include_disabled=True,
                            )
                    except Exception as exc:
                        connection_rows = []
                        ui.label(
                            "Service Connectionを読み込めません: " + str(exc)[:180]
                        ).classes("text-sm text-red-700")
    
                    connection_by_id = {
                        item["id"]: item for item in connection_rows
                    }
    
                    if connection_rows:
                        ui.table(
                            columns=[
                                {"name": "display_name", "label": "Connection", "field": "display_name", "align": "left"},
                                {"name": "adapter_key", "label": "Adapter", "field": "adapter_key", "align": "left"},
                                {"name": "connection_type", "label": "Type", "field": "connection_type", "align": "left"},
                                {"name": "endpoint", "label": "Endpoint", "field": "endpoint", "align": "left"},
                                {"name": "auth_text", "label": "Auth", "field": "auth_text", "align": "center"},
                                {"name": "role_text", "label": "Role", "field": "role_text", "align": "left"},
                                {"name": "capabilities_text", "label": "Capabilities", "field": "capabilities_text", "align": "left"},
                                {"name": "enabled_text", "label": "Enabled", "field": "enabled_text", "align": "center"},
                            ],
                            rows=[
                                {
                                    **item,
                                    "auth_text": "SET" if item.get("auth_configured") else "-",
                                    "role_text": item.get("connection_role") or "-",
                                    "capabilities_text": ", ".join(item.get("capabilities") or []),
                                    "enabled_text": "ON" if item.get("enabled") else "OFF",
                                }
                                for item in connection_rows
                            ],
                            row_key="id",
                        ).classes("w-full")
                    else:
                        ui.label("登録済みService Connectionはありません。").classes(
                            "text-sm text-grey-7"
                        )
    
                    connection_edit_target = ui.select(
                        options={
                            item["id"]: item["display_name"]
                            + f" [{item['adapter_key']} / {item['connection_type']}]"
                            for item in connection_rows
                        },
                        value=None,
                        label="既存Connectionを編集（未選択なら新規）",
                    ).props("clearable").classes("w-full")
    
                    with ui.row().classes("w-full gap-2 items-end flex-wrap"):
                        connection_name = ui.input(
                            "接続名",
                            placeholder="例: OpenAI 通常API / OpenAI Admin / Ollama SubPC",
                        ).classes("min-w-64 grow")
                        connection_adapter = ui.select(
                            options=list(connection_adapter_keys()),
                            value="openai",
                            label="Adapter",
                        ).classes("min-w-40")
                        connection_type = ui.select(
                            options=list(CONNECTION_TYPES),
                            value="api_key",
                            label="Connection Type",
                        ).classes("min-w-48")
                        connection_enabled = ui.switch("Enabled", value=True)
    
                    with ui.row().classes("w-full gap-2 items-end flex-wrap"):
                        connection_endpoint = ui.input(
                            "Endpoint",
                            placeholder="https://api.openai.com/v1",
                        ).classes("min-w-96 grow")
                        connection_role = ui.input(
                            "Role（任意・分類表示用）",
                            placeholder="llm / service_billing など",
                        ).classes("min-w-56")
                        connection_config = ui.textarea(
                            "Config JSON（非認証の接続固有設定）",
                            value="{}",
                        ).classes("min-w-80 grow")
    
                    with ui.row().classes("w-full gap-4 items-center flex-wrap"):
                        connection_cap_llm = ui.checkbox(
                            "LLM inference",
                            value=True,
                        )
                        connection_cap_billing = ui.checkbox(
                            "Service billing read",
                            value=False,
                        )
    
                    auth_api_key = ui.input(
                        "API Key（登録済みの場合は空欄で維持）"
                    ).props("type=password autocomplete=new-password").classes("w-full")
                    with ui.row().classes("w-full gap-2 flex-wrap"):
                        auth_username = ui.input(
                            "Login ID / Username（空欄で維持）"
                        ).classes("min-w-64 grow")
                        auth_password = ui.input(
                            "Password（空欄で維持）"
                        ).props("type=password autocomplete=new-password").classes("min-w-64 grow")
                    with ui.row().classes("w-full gap-2 flex-wrap"):
                        auth_client_id = ui.input(
                            "OAuth Client ID（空欄で維持）"
                        ).classes("min-w-64 grow")
                        auth_client_secret = ui.input(
                            "OAuth Client Secret（空欄で維持）"
                        ).props("type=password autocomplete=new-password").classes("min-w-64 grow")
                        auth_refresh_token = ui.input(
                            "OAuth Refresh Token（空欄で維持）"
                        ).props("type=password autocomplete=new-password").classes("min-w-64 grow")
    
                    def update_auth_field_visibility(_event=None):
                        kind = str(connection_type.value or "")
                        auth_api_key.set_visibility(kind == "api_key")
                        auth_username.set_visibility(kind == "username_password")
                        auth_password.set_visibility(kind == "username_password")
                        auth_client_id.set_visibility(kind == "oauth2")
                        auth_client_secret.set_visibility(kind == "oauth2")
                        auth_refresh_token.set_visibility(kind == "oauth2")
    
                    def fill_connection_defaults(_event=None):
                        try:
                            defaults = connection_adapter_defaults(
                                str(connection_adapter.value or "")
                            )
                        except Exception:
                            return
                        connection_endpoint.value = defaults["endpoint"]
                        connection_type.value = defaults["connection_type"]
                        connection_config.value = json.dumps(
                            defaults.get("config_data") or {},
                            ensure_ascii=False,
                            indent=2,
                        )
                        default_caps = set(defaults["capabilities"])
                        connection_cap_llm.value = LLM_INFERENCE in default_caps
                        connection_cap_billing.value = SERVICE_BILLING_READ in default_caps
                        for control in (
                            connection_endpoint,
                            connection_type,
                            connection_config,
                            connection_cap_llm,
                            connection_cap_billing,
                        ):
                            control.update()
                        update_auth_field_visibility()
    
                    def apply_selected_connection(_event=None):
                        connection_id = str(connection_edit_target.value or "").strip()
                        item = connection_by_id.get(connection_id)
                        if item is None:
                            return
                        connection_name.value = item["display_name"]
                        connection_adapter.value = item["adapter_key"]
                        connection_type.value = item["connection_type"]
                        connection_endpoint.value = item["endpoint"]
                        connection_role.value = item.get("connection_role") or ""
                        connection_enabled.value = bool(item.get("enabled"))
                        connection_config.value = json.dumps(
                            item.get("config_data") or {},
                            ensure_ascii=False,
                            indent=2,
                        )
                        caps = set(item.get("capabilities") or [])
                        connection_cap_llm.value = LLM_INFERENCE in caps
                        connection_cap_billing.value = SERVICE_BILLING_READ in caps
                        auth_api_key.value = ""
                        auth_username.value = ""
                        auth_password.value = ""
                        auth_client_id.value = ""
                        auth_client_secret.value = ""
                        auth_refresh_token.value = ""
                        for control in (
                            connection_name, connection_adapter, connection_type,
                            connection_endpoint, connection_role, connection_enabled,
                            connection_config, connection_cap_llm, connection_cap_billing,
                            auth_api_key, auth_username, auth_password,
                            auth_client_id, auth_client_secret, auth_refresh_token,
                        ):
                            control.update()
                        update_auth_field_visibility()
    
                    connection_type.on_value_change(update_auth_field_visibility)
                    connection_adapter.on_value_change(fill_connection_defaults)
                    connection_edit_target.on_value_change(apply_selected_connection)
                    fill_connection_defaults()
    
                    def save_service_connection_from_ui():
                        try:
                            config_value = json.loads(
                                str(connection_config.value or "{}")
                            )
                            if not isinstance(config_value, dict):
                                raise ValueError("Config JSONはobjectにしてください")
                            kind = str(connection_type.value or "")
                            editing_id = str(connection_edit_target.value or "").strip() or None
                            existing = connection_by_id.get(editing_id or "")
                            auth_data = None
                            if kind == "none":
                                auth_data = {}
                            elif kind == "api_key":
                                secret = str(auth_api_key.value or "").strip()
                                if secret:
                                    auth_data = {"api_key": secret}
                                elif existing is None:
                                    auth_data = {}
                            elif kind == "username_password":
                                username = str(auth_username.value or "").strip()
                                password_value = str(auth_password.value or "").strip()
                                if username or password_value:
                                    if not username or not password_value:
                                        raise ValueError("IDとPasswordは両方入力してください")
                                    auth_data = {
                                        "username": username,
                                        "password": password_value,
                                    }
                                elif existing is None:
                                    auth_data = {}
                            elif kind == "oauth2":
                                client_id = str(auth_client_id.value or "").strip()
                                client_secret = str(auth_client_secret.value or "").strip()
                                refresh_token = str(auth_refresh_token.value or "").strip()
                                if client_id or client_secret or refresh_token:
                                    if not client_id:
                                        raise ValueError("OAuth Client IDが必要です")
                                    auth_data = {
                                        "client_id": client_id,
                                        "client_secret": client_secret,
                                        "refresh_token": refresh_token,
                                    }
                                elif existing is None:
                                    auth_data = {}
    
                            capabilities = []
                            if connection_cap_llm.value:
                                capabilities.append(LLM_INFERENCE)
                            if connection_cap_billing.value:
                                capabilities.append(SERVICE_BILLING_READ)
    
                            with connection() as db:
                                saved = upsert_service_connection(
                                    db,
                                    adapter_key=str(connection_adapter.value or ""),
                                    display_name=str(connection_name.value or "").strip(),
                                    endpoint=str(connection_endpoint.value or "").strip(),
                                    credential_ref="",
                                    capabilities=capabilities,
                                    config_data=config_value,
                                    auth_data=auth_data,
                                    connection_type=kind,
                                    connection_role=(
                                        str(connection_role.value or "").strip() or None
                                    ),
                                    enabled=bool(connection_enabled.value),
                                    connection_id=editing_id,
                                )
                            connection_by_id[saved["id"]] = saved
                            connection_edit_target.options[saved["id"]] = (
                                saved["display_name"]
                                + f" [{saved['adapter_key']} / {saved['connection_type']}]"
                            )
                            connection_edit_target.value = saved["id"]
                            connection_edit_target.update()
                            billing_connection.options = {
                                item["id"]: (
                                    item["display_name"] + f" [{item['adapter_key']}]"
                                )
                                for item in connection_by_id.values()
                                if SERVICE_BILLING_READ in set(item.get("capabilities") or [])
                            }
                            if billing_connection.value not in billing_connection.options:
                                billing_connection.value = None
                            billing_connection.update()
    
                            llm_connection_by_id.clear()
                            llm_connection_by_id.update({
                                item["id"]: item
                                for item in connection_by_id.values()
                                if LLM_INFERENCE in set(item.get("capabilities") or [])
                            })
                            profile_connection.options = {
                                item["id"]: (
                                    item["display_name"]
                                    + f" [{item['adapter_key']} / {item['connection_type']}]"
                                )
                                for item in llm_connection_by_id.values()
                            }
                            if profile_connection.value not in profile_connection.options:
                                profile_connection.value = None
                            profile_connection.update()
    
                            ui.notify(
                                "Service Connectionを保存しました。関連するConnection候補にも反映しました。",
                                type="positive",
                            )
                        except Exception as exc:
                            ui.notify(
                                "Service Connectionを保存できません: " + str(exc)[:220],
                                type="negative",
                            )
    
                    with ui.row().classes("gap-2"):
                        ui.button(
                            "Connectionを保存",
                            icon="save",
                            color="indigo",
                            on_click=save_service_connection_from_ui,
                        )
                        ui.button(
                            "Adapter既定値",
                            icon="auto_fix_high",
                            on_click=fill_connection_defaults,
                        ).props("flat")
                    ui.label(
                        "認証情報はこのローカルDBのauth_dataへ保存します。Prompt / Task / Audit / Gitには出しません。"
                    ).classes("text-xs text-grey-7")
    
                with ui.expansion(
                    "利用料金・契約 — 接続割当",
                    value=False,
                    icon="query_stats",
                ).classes("w-full border-2 border-indigo-200 bg-indigo-50"):
                    ui.label(
                        "利用料金・契約の定義が使用するConnectionを明示的に選びます。"
                        "ConnectionのRoleやCapabilityから自動選択しません。"
                    ).classes("text-sm")
                    try:
                        with connection() as db:
                            billing_profiles = list_service_billing_profiles(
                                db,
                                include_disabled=True,
                            )
                            billing_connections = list_service_connections(
                                db,
                                include_disabled=True,
                                capability=SERVICE_BILLING_READ,
                            )
                    except Exception as exc:
                        billing_profiles = []
                        billing_connections = []
                        ui.label(
                            "Service Billing設定を読み込めません: " + str(exc)[:180]
                        ).classes("text-sm text-red-700")
    
                    billing_profile_by_id = {
                        item["id"]: item for item in billing_profiles
                    }
                    billing_profile_edit = ui.select(
                        options={
                            item["id"]: item["display_name"]
                            + " → "
                            + item["connection_name"]
                            for item in billing_profiles
                        },
                        value=None,
                        label="既存Service Billing Profileを編集（未選択なら新規）",
                    ).props("clearable").classes("w-full")
                    billing_profile_name = ui.input(
                        "表示名",
                        value="OpenAI Billing",
                    ).classes("w-full")
                    billing_connection = ui.select(
                        options={
                            item["id"]: item["display_name"]
                            + f" [{item['adapter_key']}]"
                            for item in billing_connections
                        },
                        value=None,
                        label="使用するService Connection",
                    ).classes("w-full")
                    billing_profile_enabled = ui.switch("Enabled", value=True)
    
                    def apply_billing_profile(_event=None):
                        item = billing_profile_by_id.get(
                            str(billing_profile_edit.value or "")
                        )
                        if item is None:
                            return
                        billing_profile_name.value = item["display_name"]
                        billing_connection.value = item["connection_id"]
                        billing_profile_enabled.value = bool(item.get("enabled"))
                        billing_profile_name.update()
                        billing_connection.update()
                        billing_profile_enabled.update()
    
                    billing_profile_edit.on_value_change(apply_billing_profile)
    
                    def save_billing_profile():
                        try:
                            with connection() as db:
                                saved = upsert_service_billing_profile(
                                    db,
                                    display_name=str(
                                        billing_profile_name.value or ""
                                    ).strip(),
                                    connection_id=str(
                                        billing_connection.value or ""
                                    ).strip(),
                                    enabled=bool(billing_profile_enabled.value),
                                    profile_id=(
                                        str(billing_profile_edit.value)
                                        if billing_profile_edit.value
                                        else None
                                    ),
                                )
                            billing_profile_by_id[saved["id"]] = saved
                            billing_profile_edit.options[saved["id"]] = (
                                saved["display_name"]
                                + " → "
                                + saved["connection_name"]
                            )
                            billing_profile_edit.value = saved["id"]
                            billing_profile_edit.update()
                            ui.notify(
                                "Service Billing Profileを保存しました",
                                type="positive",
                            )
                        except Exception as exc:
                            ui.notify(
                                "Service Billing Profileを保存できません: "
                                + str(exc)[:220],
                                type="negative",
                            )
    
                    ui.button(
                        "利用料金・契約の接続割当を保存",
                        icon="save",
                        color="indigo",
                        on_click=save_billing_profile,
                    )
    
                with ui.expansion(
                    "MAGI — LLM profile",
                    value=False,
                    icon="tune",
                ).classes("w-full border-2 border-teal-200 bg-teal-50"):
                    ui.label(
                        "MELCHIOR / CASPER / BALTHASARはLLMの席名です。"
                        "LLM Profileはmodel/runtime条件だけを持ち、接続・認証はService Connectionを参照します。"
                    ).classes("text-sm")
                    try:
                        with connection() as db:
                            editable_profiles = list_llm_profiles(
                                db,
                                include_disabled=True,
                            )
                            llm_connections = list_service_connections(
                                db,
                                include_disabled=True,
                                capability=LLM_INFERENCE,
                            )
                    except Exception as exc:
                        editable_profiles = []
                        llm_connections = []
                        ui.label(
                            "LLM Profile設定を読み込めません: " + str(exc)[:180]
                        ).classes("text-sm text-red-700")
    
                    editable_profile_by_id = {
                        item["id"]: item for item in editable_profiles
                    }
                    llm_connection_by_id = {
                        item["id"]: item for item in llm_connections
                    }
                    profile_edit_target = ui.select(
                        options={
                            item["id"]: (
                                item["display_name"]
                                + f" [{item['provider']} / {item['model']}]"
                            )
                            for item in editable_profiles
                        },
                        value=None,
                        label="既存LLM profileを編集（未選択なら新規）",
                    ).props("clearable").classes("w-full")
    
                    profile_connection = ui.select(
                        options={
                            item["id"]: (
                                item["display_name"]
                                + f" [{item['adapter_key']} / {item['connection_type']}]"
                            )
                            for item in llm_connections
                        },
                        value=None,
                        label="使用するService Connection",
                    ).classes("w-full")
    
                    with ui.row().classes(
                        "w-full gap-2 items-end flex-wrap border-b border-teal-200 pb-3"
                    ):
                        profile_model = ui.input(
                            "Model",
                            placeholder="例: gemma4:12b / gpt-5.6-sol / gemini-...",
                        ).classes("min-w-64 grow")
                        profile_display = ui.input(
                            "表示名（任意）",
                            placeholder="未指定なら adapter / model",
                        ).classes("min-w-64")
    
                    with ui.row().classes(
                        "w-full gap-2 items-end flex-wrap border-b border-teal-200 py-3"
                    ):
                        profile_context = ui.select(
                            options={
                                value: f"{value // 1024}K ({value})"
                                for value in OLLAMA_CONTEXT_OPTIONS
                            },
                            value=DEFAULT_OLLAMA_CONTEXT_TOKENS,
                            label="Context Window（Ollamaのみ）",
                        ).classes("min-w-56")
                        profile_generation = ui.select(
                            options={
                                value: f"{value} tokens"
                                for value in OLLAMA_NUM_PREDICT_OPTIONS
                            },
                            value=DEFAULT_MAGI_OLLAMA_NUM_PREDICT,
                            label="Generation Budget（Ollamaのみ）",
                        ).classes("min-w-56")
                        profile_retry_codes = ui.input(
                            "Retry HTTP codes",
                            value=",".join(str(code) for code in DEFAULT_RETRY_HTTP_CODES),
                            placeholder="429,500,502,503,504",
                        ).classes("min-w-64 grow")
    
                    def apply_selected_profile(_event=None):
                        profile_id = str(profile_edit_target.value or "").strip()
                        item = editable_profile_by_id.get(profile_id)
                        if item is None:
                            return
                        profile_connection.value = item["connection_id"]
                        profile_model.value = item["model"]
                        profile_display.value = item["display_name"]
                        profile_context.value = item.get("context_window_tokens")
                        profile_generation.value = item.get("ollama_num_predict")
                        profile_retry_codes.value = ",".join(
                            str(code) for code in item.get("retry_http_codes") or []
                        )
                        for control in (
                            profile_connection, profile_model, profile_display,
                            profile_context, profile_generation, profile_retry_codes,
                        ):
                            control.update()
    
                    profile_edit_target.on_value_change(apply_selected_profile)
    
                    def add_llm_profile():
                        try:
                            selected_connection_id = str(
                                profile_connection.value or ""
                            ).strip()
                            selected_connection = llm_connection_by_id.get(
                                selected_connection_id
                            )
                            if selected_connection is None:
                                raise ValueError("Service Connectionを選択してください")
                            is_ollama = selected_connection["adapter_key"] == "ollama"
                            saved = _register_magi_profile(
                                connection_id=selected_connection_id,
                                model=str(profile_model.value or ""),
                                display_name=(
                                    str(profile_display.value or "").strip() or None
                                ),
                                context_window_tokens=(
                                    int(profile_context.value)
                                    if is_ollama and profile_context.value is not None
                                    else None
                                ),
                                ollama_num_predict=(
                                    int(profile_generation.value)
                                    if is_ollama and profile_generation.value is not None
                                    else None
                                ),
                                retry_http_codes=normalize_retry_http_codes(
                                    profile_retry_codes.value
                                ),
                                profile_id=(
                                    str(profile_edit_target.value)
                                    if profile_edit_target.value
                                    else None
                                ),
                            )
                            editable_profile_by_id[saved["id"]] = saved
                            profile_edit_target.options[saved["id"]] = (
                                saved["display_name"]
                                + f" [{saved['provider']} / {saved['model']}]"
                            )
                            profile_edit_target.value = saved["id"]
                            profile_edit_target.update()
                            ui.notify(
                                "LLM profileを保存しました: " + saved["display_name"],
                                type="positive",
                            )
                        except Exception as exc:
                            ui.notify(
                                "LLM profileを保存できません: " + str(exc)[:200],
                                type="negative",
                            )
    
                    ui.button(
                        "LLM profileを保存",
                        icon="save",
                        color="teal",
                        on_click=add_llm_profile,
                    )
                    ui.label(
                        "Ollamaのインストール済みmodelはRITSUKO画面を開いたとき自動でprofile同期されます。"
                    ).classes("text-xs text-grey-7")
    
            with ui.expansion(
                "画面レイアウト設定",
                value=False,
                icon="dashboard_customize",
            ).classes("w-full border-2 border-blue-grey-200 bg-grey-50"):
                ui.label(
                    "「表示」はブロック自体の表示/非表示を設定します。"
                    "PKBの確認待ち・処理済みは右Drawerに表示し、"
                    "Drawer自体の初期表示は確認待ち側で設定します。"
                    "その他のアコーディオンは初期展開を設定します。"
                ).classes("text-sm text-grey-7")
                ui.label(
                    "このブロックの「保存して反映」「初期値に戻す」は画面レイアウト設定だけに作用します。"
                    "保存先はローカルの data/ui_preferences.json（Git管理外）です。"
                ).classes("text-sm text-grey-7")
                pkb_open_controls = {}
                pkb_visible_controls = {}
                with ui.expansion(
                    "PKB",
                    value=False,
                    icon="account_tree",
                ).classes("w-full border-2 border-green-200 bg-green-50"):
                    for key, label in pkb_labels.items():
                        with ui.row().classes("w-full items-center gap-4 border-b border-grey-300 py-2"):
                            ui.label(label).classes("grow")
                            pkb_visible_controls[key] = ui.switch(
                                "表示",
                                value=_UI_PREFERENCES["visibility"]["pkb"][key],
                            )
                            if key != "reviewed":
                                pkb_open_controls[key] = ui.switch(
                                    "Drawer初期表示"
                                    if key == "pending"
                                    else "初期展開",
                                    value=_UI_PREFERENCES["pkb"][key],
                                )
    
                entity_visible_controls = {}
                with ui.expansion(
                    "Entity詳細",
                    value=False,
                    icon="category",
                ).classes("w-full border-2 border-purple-200 bg-purple-50"):
                    ui.label(
                        "各タブの表示と、Entity詳細を開いたときの初期タブを設定します。"
                    ).classes("text-sm text-grey-7")
    
                    for key in ENTITY_TAB_ORDER:
                        with ui.row().classes("w-full items-center gap-4 border-b border-grey-300 py-2"):
                            ui.label(ENTITY_TAB_LABELS[key]).classes("grow")
                            entity_visible_controls[key] = ui.switch(
                                "表示",
                                value=_UI_PREFERENCES["visibility"]["entity"][key],
                            )
    
                    entity_default_tab_select = ui.select(
                        options={
                            key: ENTITY_TAB_LABELS[key]
                            for key in ENTITY_TAB_ORDER
                        },
                        label="初期表示タブ",
                        value=_UI_PREFERENCES["entity"]["default_tab"],
                    ).classes("min-w-64")
    
                finance_open_controls = {}
                finance_visible_controls = {}
                with ui.expansion(
                    "家計・資産",
                    value=False,
                    icon="account_balance_wallet",
                ).classes("w-full border-2 border-blue-200 bg-blue-50"):
                    for key, label in finance_labels.items():
                        with ui.row().classes("w-full items-center gap-4 border-b border-grey-300 py-2"):
                            ui.label(label).classes("grow")
                            finance_visible_controls[key] = ui.switch(
                                "表示",
                                value=_UI_PREFERENCES["visibility"]["finance"][key],
                            )
                            finance_open_controls[key] = ui.switch(
                                "初期展開",
                                value=_UI_PREFERENCES["finance"][key],
                            )
                    page_size_select = ui.select(
                        options=list(FINANCE_PAGE_SIZE_OPTIONS),
                        label="保存済み明細の既定1ページ件数",
                        value=_UI_PREFERENCES["finance"]["recent_limit"],
                    ).classes("min-w-64")
    
                core_list_controls = {}
                core_open_controls = {}
                core_visible_controls = {}
                with ui.expansion(
                    "RITSUKO — Task表示件数",
                    value=False,
                    icon="format_list_numbered",
                ).classes("w-full border-2 border-slate-200 bg-slate-50"):
                    ui.label(
                        "各一覧の初期件数と「さらに読み込む」で追加する件数です。"
                        "保存するとサーバー再起動なしで反映されます。"
                    ).classes("text-sm text-grey-7")
                    for key, label in (("open_limit", "進行中・確認待ち 初期表示件数"),
                                       ("completed_limit", "完了済み 初期表示件数")):
                        core_list_controls[key] = ui.select(
                            options=list(CORE_TASK_PAGE_SIZE_OPTIONS), label=label,
                            value=_UI_PREFERENCES["core"][key],
                        ).classes("min-w-64")
    
                with ui.expansion(
                    "RITSUKO — 検証・補足の表示",
                    value=False,
                    icon="fact_check",
                ).classes("w-full border-2 border-slate-200 bg-slate-50"):
                    ui.label(
                        "主操作の依頼ブロックは常時表示。検証・補足ブロックだけ非表示にできます。"
                    ).classes("text-sm text-grey-7")
                    for key, label in core_labels.items():
                        with ui.row().classes("w-full items-center gap-4 border-b border-grey-300 py-2"):
                            ui.label(label).classes("grow")
                            core_visible_controls[key] = ui.switch(
                                "表示",
                                value=_UI_PREFERENCES["visibility"]["core"][key],
                            )
                            if key in CORE_UI_DEFAULT_OPEN:
                                core_open_controls[key] = ui.switch(
                                    "初期展開",
                                    value=_UI_PREFERENCES["core"][key],
                                )
    
                ui.label(
                    "保存後、別画面へ移動するかページを再読み込みすると表示/非表示が反映されます。"
                    "現在のアコーディオン開閉状態と保存済み初期値は別管理です。"
                ).classes("text-sm text-grey-7")
    
                def collect_preferences() -> dict:
                    pkb_preferences = dict(_UI_PREFERENCES["pkb"])
                    pkb_preferences.update({
                        key: bool(control.value)
                        for key, control in pkb_open_controls.items()
                    })
    
                    return {
                        "pkb": pkb_preferences,
                        "entity": {
                            "default_tab": (
                                entity_default_tab_select.value
                                or ENTITY_TAB_DEFAULT
                            ),
                        },
                        "finance": {
                            **{
                                key: bool(control.value)
                                for key, control in finance_open_controls.items()
                            },
                            "recent_limit": int(
                                page_size_select.value or FINANCE_PAGE_SIZE_DEFAULT
                            ),
                        },
                        "core": {
                            **{key: bool(control.value) for key, control in core_open_controls.items()},
                            **{key: int(control.value) for key, control in core_list_controls.items()},
                        },
                        "core_advisor_model": _UI_PREFERENCES.get("core_advisor_model"),
                        "core_advisor_timeout": _UI_PREFERENCES.get("core_advisor_timeout", 60),
                        "visibility": {
                            "pkb": {
                                key: bool(control.value)
                                for key, control in pkb_visible_controls.items()
                            },
                            "entity": {
                                key: bool(control.value)
                                for key, control in entity_visible_controls.items()
                            },
                            "finance": {
                                key: bool(control.value)
                                for key, control in finance_visible_controls.items()
                            },
                            "core": {
                                key: bool(control.value)
                                for key, control in core_visible_controls.items()
                            },
                        },
                    }
    
                def sync_controls(preferences: dict) -> None:
                    for key, control in pkb_open_controls.items():
                        control.value = preferences["pkb"][key]
                    for key, control in pkb_visible_controls.items():
                        control.value = preferences["visibility"]["pkb"][key]
                    for key, control in entity_visible_controls.items():
                        control.value = preferences["visibility"]["entity"][key]
    
                    entity_default_tab_select.value = (
                        preferences["entity"]["default_tab"]
                    )
                    for key, control in finance_open_controls.items():
                        control.value = preferences["finance"][key]
                    for key, control in finance_visible_controls.items():
                        control.value = preferences["visibility"]["finance"][key]
                    for key, control in core_visible_controls.items():
                        control.value = preferences["visibility"]["core"][key]
                    for key, control in core_open_controls.items():
                        control.value = preferences["core"][key]
                    for key, control in core_list_controls.items():
                        control.value = preferences["core"][key]
                    page_size_select.value = preferences["finance"]["recent_limit"]
    
                def save_and_apply():
                    try:
                        if not any(
                            bool(control.value)
                            for control in entity_visible_controls.values()
                        ):
                            ui.notify(
                                "Entity詳細は少なくとも1つのタブを表示してください",
                                type="negative",
                            )
                            return
    
                        saved = save_ui_preferences(collect_preferences())
                        _apply_ui_preferences(saved)
    
                        # Reflect validation/fallback immediately in the settings page.
                        sync_controls(saved)
    
                        ui.notify(
                            "表示設定を保存しました。各画面を開き直すと反映されます",
                            type="positive",
                        )
                    except Exception as exc:
                        ui.notify(
                            "表示設定を保存できません: " + str(exc)[:220],
                            type="negative",
                        )
    
                def restore_builtin():
                    defaults = _default_ui_preferences()
                    sync_controls(defaults)
                    try:
                        saved = save_ui_preferences(defaults)
                        _apply_ui_preferences(saved)
                        ui.notify("初期値へ戻して保存しました", type="positive")
                    except Exception as exc:
                        ui.notify("初期値を保存できません: " + str(exc)[:220], type="negative")
    
                with ui.row().classes("gap-2"):
                    ui.button(
                        "保存して反映",
                        icon="save",
                        color="blue",
                        on_click=save_and_apply,
                    )
                    ui.button(
                        "初期値に戻す",
                        icon="restart_alt",
                        on_click=restore_builtin,
                    ).props("outline")
    
    settings_page._context_sync = sync
    return settings_page
