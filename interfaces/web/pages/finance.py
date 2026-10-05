"""finance page for the daily portal."""


def sync(portal_context: dict) -> None:
    globals().update(portal_context)


def register(portal_context: dict):
    sync(portal_context)
    @ui.page("/finance")
    def finance_page():
        globals().update(portal_context)
        finance_preferences = _UI_PREFERENCES["finance"]
        state = {
            "preview": None,
            "error": None,
            "filename": None,
            "csv_bytes": None,
            "import_plan": None,
            "import_result": None,
            "import_busy": False,
            "stored": None,
            "stored_error": None,
            "filter_options": {"accounts": [], "major_categories": []},
            "filters": {
                "start_date": None,
                "end_date": None,
                "account": None,
                "major_category": None,
                "search_text": None,
                "row_mode": "calculation_target",
                "page": 1,
                "sort_by": "date",
                "sort_dir": "desc",
                "recent_limit": finance_preferences["recent_limit"],
            },
            # Current navigation state is kept separately from saved defaults,
            # matching PKB behavior across normal page navigation.
            "ui_open": dict(_FINANCE_UI_OPEN),
        }
    
        try:
            state["stored"] = load_finance_dashboard(
                _finance_repository,
                **state["filters"],
            )
            state["filter_options"] = finance_filter_options(
                _finance_repository
            )
        except Exception as exc:
            state["stored_error"] = str(exc)
    
        def remember_expansion(key: str):
            def _remember(event):
                state["ui_open"][key] = bool(event.value)
                _set_finance_ui_open(key, event.value)
            return _remember
    
        with ui.column().classes("w-full max-w-7xl mx-auto gap-4 p-4"):
            _portal_header(
                "家計・資産",
                "保存済み家計をSQL-firstで表示し、MoneyForward CSVを差分Import",
            )
    
            with ui.expansion(
                "家計フィルタ・検索",
                value=state["ui_open"]["filter"],
                on_value_change=remember_expansion("filter"),
            ).classes(
                "w-full border-2 border-teal-200 bg-teal-50 text-teal-900"
                + _block_visibility_class("finance", "filter")
            ):
                with ui.row().classes("w-full gap-3 flex-wrap items-end"):
                    start_input = ui.input("開始日").props("type=date").classes("min-w-40")
                    end_input = ui.input("終了日").props("type=date").classes("min-w-40")
                    account_select = ui.select(
                        options=[""] + state["filter_options"]["accounts"],
                        label="金融機関",
                        value="",
                    ).classes("min-w-56")
                    category_select = ui.select(
                        options=[""] + state["filter_options"]["major_categories"],
                        label="大項目",
                        value="",
                    ).classes("min-w-48")
                    row_mode_select = ui.select(
                        options={
                            "calculation_target": "集計対象のみ",
                            "all": "全明細（振替含む）",
                            "transfer": "振替のみ",
                        },
                        label="対象",
                        value="calculation_target",
                    ).classes("min-w-48")
                    search_input_finance = ui.input(
                        "明細検索",
                        placeholder="内容・メモ・金融機関・カテゴリ",
                    ).classes("min-w-72 grow")
    
                def reload_stored():
                    try:
                        filters = {
                            "start_date": (start_input.value or None),
                            "end_date": (end_input.value or None),
                            "account": (account_select.value or None),
                            "major_category": (category_select.value or None),
                            "search_text": ((search_input_finance.value or "").strip() or None),
                            "row_mode": (row_mode_select.value or "calculation_target"),
                            "page": 1,
                            "sort_by": state["filters"].get("sort_by", "date"),
                            "sort_dir": state["filters"].get("sort_dir", "desc"),
                            "recent_limit": state["filters"].get(
                                "recent_limit", finance_preferences["recent_limit"]
                            ),
                        }
                        if filters["start_date"] and filters["end_date"] and filters["start_date"] > filters["end_date"]:
                            ui.notify("開始日は終了日以前にしてください", type="warning")
                            return
                        state["stored"] = load_finance_dashboard(
                            _finance_repository,
                            **filters,
                        )
                        state["filters"] = filters
                        state["stored_error"] = None
                    except Exception as exc:
                        state["stored_error"] = str(exc)
                    stored_finance.refresh()
    
                def reset_stored():
                    start_input.value = ""
                    end_input.value = ""
                    account_select.value = ""
                    category_select.value = ""
                    row_mode_select.value = "calculation_target"
                    search_input_finance.value = ""
                    state["filters"]["sort_by"] = "date"
                    state["filters"]["sort_dir"] = "desc"
                    state["filters"]["recent_limit"] = finance_preferences["recent_limit"]
                    reload_stored()
    
                with ui.row().classes("gap-2"):
                    ui.button("適用", icon="filter_alt", color="teal", on_click=reload_stored)
                    ui.button("クリア", icon="restart_alt", on_click=reset_stored).props("outline")
    
            @ui.refreshable
            def stored_finance():
                if state["stored_error"]:
                    with ui.card().classes("w-full border-2 border-red-300 bg-red-50"):
                        ui.label("保存済み家計を読み取れません: " + state["stored_error"]).classes(
                            "text-red-800"
                        )
                    return
                stored = state["stored"]
                if stored is None or stored.transaction_count == 0:
                    with ui.card().classes("w-full border-2 border-grey-300"):
                        ui.label("保存済み家計データはまだありません。")
                    return
    
                with ui.expansion(
                    "保存済み家計",
                    value=state["ui_open"]["stored"],
                    on_value_change=remember_expansion("stored"),
                ).classes(
                    "w-full border-2 border-green-300 bg-green-50 text-green-900"
                    + _block_visibility_class("finance", "stored")
                ):
                    fstate = state["filters"]
                    has_user_filter = any(
                        fstate.get(key)
                        for key in ("start_date", "end_date", "account", "major_category", "search_text")
                    ) or fstate.get("row_mode") != "calculation_target"
                    if has_user_filter:
                        ui.badge("フィルタ適用中", color="teal")
                    mode_labels = {
                        "calculation_target": "集計対象のみ",
                        "all": "全明細（振替含む）",
                        "transfer": "振替のみ",
                    }
                    ui.badge(
                        "対象: " + mode_labels.get(stored.row_mode, stored.row_mode),
                        color="green",
                    )
                    ui.label(
                        "PostgreSQLの正規化済みTransactionをSQL-firstで集計しています。"
                    ).classes("text-sm text-green-900")
                    with ui.row().classes("w-full gap-3 flex-wrap"):
                        for label, value in (
                            ("明細件数", f"{stored.transaction_count:,}件"),
                            ("集計対象", f"{stored.calculation_target_count:,}件"),
                            ("期間", f"{stored.start_date} ～ {stored.end_date}"),
                            ("収入", f"¥{stored.income_total:,.0f}"),
                            ("支出", f"¥{stored.expense_total:,.0f}"),
                            ("収支", f"¥{stored.net_total:,.0f}"),
                            ("Import Batch", f"{len(stored.import_batches):,}件"),
                        ):
                            with ui.card().classes("min-w-40"):
                                ui.label(label).classes("text-xs text-grey-7")
                                ui.label(value).classes("text-lg font-bold")
    
                    with ui.expansion(
                        "保存済み月別集計",
                        value=state["ui_open"]["monthly"],
                        on_value_change=remember_expansion("monthly"),
                    ).classes(
                        "w-full border-2 border-green-200 bg-white text-green-900"
                        + _block_visibility_class("finance", "monthly")
                    ):
                        monthly_rows = [
                            {
                                **row,
                                "income_display": f"¥{row['income']:,}",
                                "expense_display": f"¥{row['expense']:,}",
                                "net_display": f"¥{row['net']:,}",
                            }
                            for row in stored.monthly
                        ]
                        ui.table(
                            columns=[
                                {"name": "month", "label": "月", "field": "month"},
                                {"name": "income", "label": "収入", "field": "income_display", "align": "right"},
                                {"name": "expense", "label": "支出", "field": "expense_display", "align": "right"},
                                {"name": "net", "label": "収支", "field": "net_display", "align": "right"},
                                {"name": "count", "label": "件数", "field": "count", "align": "right"},
                            ],
                            rows=monthly_rows,
                            row_key="month",
                        ).classes("w-full")
    
                    with ui.expansion(
                        "保存済みカテゴリ別支出",
                        value=state["ui_open"]["categories"],
                        on_value_change=remember_expansion("categories"),
                    ).classes(
                        "w-full border-2 border-green-200 bg-white text-green-900"
                        + _block_visibility_class("finance", "categories")
                    ):
                        category_rows = [
                            {**row, "expense_display": f"¥{row['expense']:,}"}
                            for row in stored.categories
                        ]
                        ui.table(
                            columns=[
                                {"name": "major", "label": "大項目", "field": "major"},
                                {"name": "minor", "label": "中項目", "field": "minor"},
                                {"name": "expense", "label": "支出", "field": "expense_display", "align": "right"},
                            ],
                            rows=category_rows,
                            row_key="minor",
                        ).classes("w-full")
    
                    with ui.expansion(
                        "保存済み明細",
                        value=state["ui_open"]["details"],
                        on_value_change=remember_expansion("details"),
                    ).classes(
                        "w-full border-2 border-green-200 bg-white text-green-900"
                        + _block_visibility_class("finance", "details")
                    ):
                        with ui.row().classes("w-full gap-3 flex-wrap items-end"):
                            sort_by_select = ui.select(
                                options={"date": "日付", "amount": "金額"},
                                label="並び替え",
                                value=stored.sort_by,
                            ).classes("min-w-32")
                            sort_dir_select = ui.select(
                                options={"desc": "降順", "asc": "昇順"},
                                label="順序",
                                value=stored.sort_dir,
                            ).classes("min-w-28")
                            page_size_select = ui.select(
                                options=[25, 50, 100],
                                label="1ページ件数",
                                value=stored.page_size,
                            ).classes("min-w-32")
    
                            def reload_page(
                                target_page: int | None = None,
                                apply_sort: bool = False,
                            ):
                                try:
                                    filters = dict(state["filters"])
                                    if apply_sort:
                                        filters["sort_by"] = sort_by_select.value or "date"
                                        filters["sort_dir"] = sort_dir_select.value or "desc"
                                        filters["recent_limit"] = int(
                                            page_size_select.value
                                            or finance_preferences["recent_limit"]
                                        )
                                        filters["page"] = 1
                                    elif target_page is not None:
                                        filters["page"] = target_page
                                    state["stored"] = load_finance_dashboard(
                                        _finance_repository,
                                        **filters,
                                    )
                                    state["filters"] = filters
                                    state["stored_error"] = None
                                except Exception as exc:
                                    state["stored_error"] = str(exc)
                                stored_finance.refresh()
    
                            ui.button(
                                "表示更新",
                                icon="sort",
                                color="green",
                                on_click=lambda: reload_page(apply_sort=True),
                            ).props("outline")
    
                        with ui.row().classes("w-full items-center justify-between"):
                            ui.label(
                                f"{stored.transaction_count:,}件中 "
                                f"{(stored.page - 1) * stored.page_size + 1:,}～"
                                f"{min(stored.page * stored.page_size, stored.transaction_count):,}件"
                            ).classes("text-sm")
                            with ui.row().classes("items-center gap-2"):
                                prev_button = ui.button(
                                    "前へ",
                                    icon="chevron_left",
                                    on_click=lambda: reload_page(max(1, stored.page - 1)),
                                ).props("outline")
                                ui.label(f"{stored.page} / {stored.total_pages} ページ")
                                next_button = ui.button(
                                    "次へ",
                                    icon="chevron_right",
                                    on_click=lambda: reload_page(
                                        min(stored.total_pages, stored.page + 1)
                                    ),
                                ).props("outline")
                                if stored.page <= 1:
                                    prev_button.disable()
                                if stored.page >= stored.total_pages:
                                    next_button.disable()
    
                        def _compact_text(value: str, limit: int = 56) -> str:
                            text = value or ""
                            return text if len(text) <= limit else text[: limit - 1] + "…"
    
                        recent_rows = [
                            {
                                **row,
                                "content_display": _compact_text(row["content"]),
                                "amount_display": f"¥{row['amount']:,}",
                                "target_display": "○" if row["calculation_target"] else "",
                                "transfer_display": "○" if row["is_transfer"] else "",
                            }
                            for row in stored.recent_rows
                        ]
                        ui.table(
                            columns=[
                                {
                                    "name": "date", "label": "日付", "field": "date",
                                    "style": "width: 9%; white-space: nowrap;",
                                    "headerStyle": "width: 9%;",
                                },
                                {
                                    "name": "content", "label": "内容", "field": "content_display",
                                    "style": (
                                        "width: 35%; max-width: 35%; overflow: hidden; "
                                        "text-overflow: ellipsis; white-space: nowrap;"
                                    ),
                                    "headerStyle": "width: 35%;",
                                },
                                {
                                    "name": "amount", "label": "金額", "field": "amount_display",
                                    "style": "width: 9%; white-space: nowrap;",
                                    "headerStyle": "width: 9%; text-align: right;",
                                    "align": "right",
                                },
                                {
                                    "name": "account", "label": "金融機関", "field": "account",
                                    "style": (
                                        "width: 16%; max-width: 16%; overflow: hidden; "
                                        "text-overflow: ellipsis; white-space: nowrap;"
                                    ),
                                    "headerStyle": "width: 16%;",
                                },
                                {
                                    "name": "major", "label": "大項目", "field": "major_category",
                                    "style": (
                                        "width: 10%; max-width: 10%; overflow: hidden; "
                                        "text-overflow: ellipsis; white-space: nowrap;"
                                    ),
                                    "headerStyle": "width: 10%;",
                                },
                                {
                                    "name": "minor", "label": "中項目", "field": "minor_category",
                                    "style": (
                                        "width: 13%; max-width: 13%; overflow: hidden; "
                                        "text-overflow: ellipsis; white-space: nowrap;"
                                    ),
                                    "headerStyle": "width: 13%;",
                                },
                                {
                                    "name": "target", "label": "集計", "field": "target_display",
                                    "style": "width: 4%; text-align: center;",
                                    "headerStyle": "width: 4%; text-align: center;",
                                    "align": "center",
                                },
                                {
                                    "name": "transfer", "label": "振替", "field": "transfer_display",
                                    "style": "width: 4%; text-align: center;",
                                    "headerStyle": "width: 4%; text-align: center;",
                                },
                            ],
                            rows=recent_rows,
                            row_key="external_id",
                        ).props(
                            'dense flat table-style="table-layout: fixed; width: 100%;"'
                        ).classes("w-full")
    
                    with ui.expansion(
                        "Import履歴 / Source",
                        value=state["ui_open"]["imports"],
                        on_value_change=remember_expansion("imports"),
                    ).classes(
                        "w-full border-2 border-green-200 bg-white text-green-900"
                        + _block_visibility_class("finance", "imports")
                    ):
                        ui.table(
                            columns=[
                                {"name": "filename", "label": "ファイル", "field": "source_filename"},
                                {"name": "rows", "label": "行数", "field": "row_count"},
                                {"name": "imported", "label": "Import時刻", "field": "imported_at"},
                                {"name": "status", "label": "状態", "field": "status"},
                                {"name": "sha", "label": "SHA-256", "field": "source_sha256"},
                            ],
                            rows=stored.import_batches,
                            row_key="id",
                        ).classes("w-full")
            stored_finance()
    
            with ui.expansion(
                "MoneyForward CSV 取込",
                value=state["ui_open"]["csv"],
                on_value_change=remember_expansion("csv"),
            ).classes(
                "w-full border-2 border-blue-300 bg-blue-50 text-blue-900"
                + _block_visibility_class("finance", "csv")
            ):
                ui.label("MoneyForward CSV プレビュー").classes("text-lg font-bold text-blue-900")
                ui.label(
                    "CSVはまずローカルWebプロセスのメモリ上で読み取り専用解析します。"
                    "明示的に「PostgreSQLへImport」を押すまで保存しません。"
                    "LLMには送信しません。"
                ).classes("text-sm text-blue-900")
                ui.label(
                    "想定列: 計算対象 / 日付 / 内容 / 金額（円） / 保有金融機関 / "
                    "大項目 / 中項目 / メモ / 振替 / ID"
                ).classes("text-xs text-grey-7")
    
                @ui.refreshable
                def finance_result():
                    if state["error"]:
                        ui.label(str(state["error"])).classes("text-red-700")
                        return
                    preview = state["preview"]
                    if preview is None:
                        ui.label("CSVを選択すると、DBへ登録せず内容をプレビューします。")
                        return
    
                    with ui.row().classes("w-full gap-3 flex-wrap"):
                        for label, value in (
                            ("明細件数", f"{preview.row_count:,}件"),
                            ("期間", f"{preview.start_date} ～ {preview.end_date}"),
                            ("集計対象", f"{preview.calculation_target_count:,}件"),
                            ("振替", f"{preview.transfer_count:,}件"),
                            ("ID重複", f"{preview.duplicate_id_count:,}件"),
                        ):
                            with ui.card().classes("min-w-40"):
                                ui.label(label).classes("text-xs text-grey-7")
                                ui.label(value).classes("text-lg font-bold")
    
                    with ui.row().classes("w-full gap-3 flex-wrap"):
                        for label, value in (
                            ("収入", preview.income_total),
                            ("支出", preview.expense_total),
                            ("収支", preview.net_total),
                        ):
                            with ui.card().classes("min-w-48"):
                                ui.label(label).classes("text-xs text-grey-7")
                                ui.label(f"¥{value:,.0f}").classes("text-xl font-bold")
    
                    with ui.expansion("月別集計", value=True).classes(
                        "w-full border-2 border-blue-200 bg-white text-blue-900"
                    ):
                        ui.table(
                            columns=[
                                {"name": "month", "label": "月", "field": "month"},
                                {"name": "income", "label": "収入", "field": "income"},
                                {"name": "expense", "label": "支出", "field": "expense"},
                                {"name": "net", "label": "収支", "field": "net"},
                                {"name": "count", "label": "件数", "field": "count"},
                            ],
                            rows=preview.monthly,
                            row_key="month",
                        ).classes("w-full")
    
                    with ui.expansion("支出カテゴリ上位", value=False).classes(
                        "w-full border-2 border-blue-200 bg-white text-blue-900"
                    ):
                        ui.table(
                            columns=[
                                {"name": "major", "label": "大項目", "field": "major"},
                                {"name": "minor", "label": "中項目", "field": "minor"},
                                {"name": "expense", "label": "支出", "field": "expense"},
                            ],
                            rows=preview.categories,
                            row_key="minor",
                        ).classes("w-full")
    
                    with ui.expansion("直近明細（最大100件）", value=False).classes(
                        "w-full border-2 border-blue-200 bg-white text-blue-900"
                    ):
                        ui.table(
                            columns=[
                                {"name": "date", "label": "日付", "field": "date"},
                                {"name": "content", "label": "内容", "field": "content"},
                                {"name": "amount", "label": "金額", "field": "amount"},
                                {"name": "account", "label": "金融機関", "field": "account"},
                                {"name": "major", "label": "大項目", "field": "major_category"},
                                {"name": "minor", "label": "中項目", "field": "minor_category"},
                                {"name": "transfer", "label": "振替", "field": "is_transfer"},
                            ],
                            rows=preview.recent_rows,
                            row_key="external_id",
                        ).classes("w-full")
    
                    plan = state["import_plan"]
                    if plan is not None:
                        with ui.expansion("PostgreSQL Import", value=True).classes(
                            "w-full border-2 border-amber-300 bg-amber-50 text-amber-900 mt-3"
                        ):
                            ui.label(
                                "保存先はDaily Runtimeが接続しているFinanceテーブルです。DB/Roleはデバッグ画面で確認できます。"
                                "元CSVそのものはDBへ保存せず、ファイル名・SHA-256・Import Batchを出典として保持します。"
                            ).classes("text-sm text-amber-900")
                            with ui.row().classes("w-full gap-3 flex-wrap"):
                                for label, value in (
                                    ("新規", f"{plan.inserted:,}件"),
                                    ("変更", f"{plan.updated:,}件"),
                                    ("変更なし", f"{plan.unchanged:,}件"),
                                    ("CSV内ID重複", f"{plan.duplicate_external_ids:,}件"),
                                ):
                                    with ui.card().classes("min-w-36"):
                                        ui.label(label).classes("text-xs text-grey-7")
                                        ui.label(value).classes("text-lg font-bold")
                            ui.label("Source SHA-256: " + plan.source_sha256).classes(
                                "font-mono text-xs text-grey-7"
                            )
    
                            async def do_finance_import():
                                if state["import_busy"] or state["csv_bytes"] is None:
                                    return
                                state["import_busy"] = True
                                import_button.disable()
                                try:
                                    def _commit():
                                        return commit_import(
                                            _finance_repository,
                                            state["preview"],
                                            state["csv_bytes"],
                                            state["filename"] or "moneyforward.csv",
                                        )
                                    state["import_result"] = await run.io_bound(_commit)
                                    result = state["import_result"]
                                    if result.status == "committed":
                                        ui.notify(
                                            f"PostgreSQLへImportしました: 新規{result.inserted} / "
                                            f"変更{result.updated} / 変更なし{result.unchanged}",
                                            type="positive",
                                        )
                                    elif result.status == "replayed":
                                        ui.notify("同じCSVはすでにImport済みです", type="info")
                                    else:
                                        ui.notify("Importを実行しませんでした: " + str(result.reason), type="warning")
                                    state["import_plan"] = plan_import(
                                        _finance_repository,
                                        state["preview"],
                                        state["csv_bytes"],
                                    )
                                    state["stored"] = load_finance_dashboard(
                                        _finance_repository,
                                        **state["filters"],
                                    )
                                    state["filter_options"] = finance_filter_options(
                                        _finance_repository
                                    )
                                    state["stored_error"] = None
                                    stored_finance.refresh()
                                except Exception as exc:
                                    state["import_result"] = None
                                    state["error"] = str(exc)
                                    ui.notify(str(exc)[:240], type="negative")
                                finally:
                                    state["import_busy"] = False
                                    finance_result.refresh()
    
                            import_button = ui.button(
                                "PostgreSQLへImport",
                                icon="save",
                                color="orange",
                                on_click=do_finance_import,
                            )
                            if plan.duplicate_external_ids:
                                import_button.disable()
    
                            result = state["import_result"]
                            if result is not None:
                                ui.separator()
                                ui.label(
                                    "直近Import結果: "
                                    + f"{result.status} / 新規 {result.inserted:,} / "
                                    + f"変更 {result.updated:,} / 変更なし {result.unchanged:,}"
                                ).classes("text-sm font-medium")
                                if result.batch_id:
                                    ui.label("Import Batch: " + result.batch_id).classes(
                                        "font-mono text-xs"
                                    )
    
                async def handle_finance_upload(event):
                    try:
                        filename, data = await _uploaded_bytes(event)
                        preview = analyze_moneyforward_csv(data, filename)
                        import_plan = plan_import(
                            _finance_repository,
                            preview,
                            data,
                        )
                        state["preview"] = preview
                        state["filename"] = filename
                        state["csv_bytes"] = data
                        state["import_plan"] = import_plan
                        state["import_result"] = None
                        state["error"] = None
                        ui.notify(
                            f"{filename}: {preview.row_count:,}件を読み取り専用で解析しました",
                            type="positive",
                        )
                    except Exception as exc:
                        state["preview"] = None
                        state["filename"] = None
                        state["csv_bytes"] = None
                        state["import_plan"] = None
                        state["import_result"] = None
                        state["error"] = str(exc)
                        ui.notify(str(exc)[:240], type="negative")
                    finance_result.refresh()
    
                ui.upload(
                    label="MoneyForward CSVを選択",
                    on_upload=handle_finance_upload,
                    auto_upload=True,
                    max_file_size=20_000_000,
                ).props("accept=.csv").classes("w-full")
                finance_result()
    
    finance_page._context_sync = sync
    return finance_page
