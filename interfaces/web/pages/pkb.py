"""pkb page for the daily portal."""


def sync(portal_context: dict) -> None:
    globals().update(portal_context)


def register(portal_context: dict):
    sync(portal_context)
    @ui.page("/pkb")
    def pkb_page():
        globals().update(portal_context)
        state = {
            "write": None,
            "write_busy": False,
            "correction": None,
            "search": None,
        }
        drawer_limits = {
            "pending": PKB_DRAWER_PAGE_SIZE,
            "reviewed": PKB_DRAWER_PAGE_SIZE,
        }
    
        def remember_expansion(key: str):
            def _remember(event):
                _set_pkb_ui_open(key, event.value)
            return _remember
    
        def more_drawer_rows(key: str, panel) -> None:
            drawer_limits[key] += PKB_DRAWER_PAGE_SIZE
            panel.refresh()
    
        @ui.refreshable
        def pending_panel():
            try:
                with connection() as db:
                    rows = list_pending(db)
            except Exception as exc:
                ui.label("確認待ち一覧を取得できません: " + str(exc)).classes(
                    "text-red-600"
                )
                return
    
            ui.label(f"確認待ち {len(rows)}件").classes(
                "text-lg font-bold text-purple-900"
            )
            ui.label("Pending Claims").classes("text-xs text-grey-7")
    
            if not rows:
                ui.label("確認待ちはありません。").classes("text-sm text-grey-7")
                return
    
            def decide(pending_id: str, decision: str):
                try:
                    with connection() as db:
                        review_pending(db, pending_id, decision)
                    label = "却下" if decision == "rejected" else "要修正"
                    ui.notify(label + "として記録しました", type="positive")
                    pending_panel.refresh()
                    reviewed_panel.refresh()
                except Exception as exc:
                    ui.notify(str(exc)[:240], type="negative")
    
            def accept(pending_id: str):
                try:
                    with connection() as db:
                        result = accept_pending(db, pending_id)
                    if result.status == "accepted":
                        ui.notify("承認して正式Claimへ登録しました", type="positive")
                    else:
                        ui.notify(
                            "この候補は承認できません: " + result.reason,
                            type="warning",
                        )
                    pending_panel.refresh()
                    reviewed_panel.refresh()
                except Exception as exc:
                    ui.notify(str(exc)[:240], type="negative")
    
            visible_rows = rows[: drawer_limits["pending"]]
            for row in visible_rows:
                pending_id = str(row["id"])
                when = (
                    row["recorded_at"].isoformat()
                    if isinstance(row["recorded_at"], datetime)
                    else str(row["recorded_at"])
                )
                with ui.card().classes(
                    "w-full p-2 gap-1 border border-purple-200 bg-white"
                ):
                    ui.label(row["raw_text"]).classes(
                        "w-full font-medium text-sm break-words"
                    )
                    ui.label("保留理由: " + row["reason"]).classes(
                        "w-full text-xs text-purple-900 break-all"
                    )
                    meta = when
                    if row.get("entity_name"):
                        meta += " / " + row["entity_name"]
                    if row.get("interpreter_model"):
                        meta += " / 解釈: " + row["interpreter_model"]
                    ui.label(meta).classes("w-full text-xs text-grey-6 break-all")
                    with ui.row().classes("w-full gap-1 flex-wrap"):
                        if acceptance_eligible(row):
                            ui.button(
                                "承認",
                                on_click=lambda pid=pending_id: accept(pid),
                                color="green",
                            ).props("outline dense")
                        ui.button(
                            "要修正",
                            on_click=lambda pid=pending_id: decide(
                                pid, "needs_edit"
                            ),
                            color="orange",
                        ).props("outline dense")
                        ui.button(
                            "却下",
                            on_click=lambda pid=pending_id: decide(
                                pid, "rejected"
                            ),
                            color="red",
                        ).props("outline dense")
    
            if len(rows) > drawer_limits["pending"]:
                remaining = len(rows) - drawer_limits["pending"]
                ui.button(
                    f"さらに読み込む（残り{remaining}件）",
                    on_click=lambda: more_drawer_rows(
                        "pending", pending_panel
                    ),
                ).props("flat dense").classes("self-start")
    
        @ui.refreshable
        def reviewed_panel():
            try:
                with connection() as db:
                    rows = list_reviewed(db)
            except Exception as exc:
                ui.label("処理履歴を取得できません: " + str(exc)).classes(
                    "text-red-600"
                )
                return
    
            ui.label(f"処理済み {len(rows)}件").classes(
                "text-base font-bold text-grey-8"
            )
    
            if not rows:
                ui.label("処理済みの項目はまだありません。").classes(
                    "text-sm text-grey-7"
                )
                return
    
            visible_rows = rows[: drawer_limits["reviewed"]]
            for row in visible_rows:
                status = row["review_status"]
                label = (
                    "承認"
                    if status == "accepted"
                    else "要修正"
                    if status == "needs_edit"
                    else "却下"
                    if status == "rejected"
                    else status
                )
                when = (
                    row["reviewed_at"].isoformat()
                    if isinstance(row.get("reviewed_at"), datetime)
                    else str(row.get("reviewed_at") or "")
                )
                with ui.card().classes(
                    "w-full p-2 gap-1 border border-grey-300 bg-white"
                ):
                    with ui.row().classes("w-full items-start gap-2 no-wrap"):
                        ui.badge(
                            label,
                            color=(
                                "green"
                                if status == "accepted"
                                else "orange"
                                if status == "needs_edit"
                                else "red"
                                if status == "rejected"
                                else "grey"
                            ),
                        ).classes("shrink-0")
                        ui.label(row["raw_text"]).classes(
                            "grow font-medium text-sm break-words"
                        )
                    ui.label("理由: " + row["reason"]).classes(
                        "w-full text-xs text-grey-7 break-all"
                    )
                    ui.label("処理時点: " + when).classes(
                        "w-full text-xs text-grey-6 break-all"
                    )
    
            if len(rows) > drawer_limits["reviewed"]:
                remaining = len(rows) - drawer_limits["reviewed"]
                ui.button(
                    f"さらに読み込む（残り{remaining}件）",
                    on_click=lambda: more_drawer_rows(
                        "reviewed", reviewed_panel
                    ),
                ).props("flat dense").classes("self-start")
    
        pending_visible = _UI_PREFERENCES["visibility"]["pkb"]["pending"]
        reviewed_visible = _UI_PREFERENCES["visibility"]["pkb"]["reviewed"]
        pending_drawer = None
        if pending_visible or reviewed_visible:
            pending_drawer = ui.right_drawer(
                value=_PKB_UI_OPEN["pending"]
            ).classes("bg-purple-50 p-3").props(
                "bordered width=340 breakpoint=700"
            )
            with pending_drawer:
                with ui.column().classes("w-full gap-3 no-wrap"):
                    if pending_visible:
                        pending_panel()
                    if pending_visible and reviewed_visible:
                        ui.separator()
                    if reviewed_visible:
                        reviewed_panel()
    
        with ui.column().classes("w-full max-w-7xl mx-auto gap-4 p-4"):
            _portal_header(
                "Local Secretary — Personal Knowledge Base",
                "日常用PKB • Core/Workbenchから独立 • localhostのみ",
            )
            ui.label(
                "保存先はDaily Runtimeで選択されたPostgreSQLです。現在のDB/Roleはデバッグ画面で確認できます。"
            ).classes("text-sm text-orange-700")
    
            with ui.row().classes("w-full items-center gap-2"):
                with ui.tabs().classes("grow") as pkb_tabs:
                    tab_refs = {
                        key: ui.tab(PKB_TAB_LABELS[key])
                        for key in PKB_TAB_ORDER
                    }
                if pending_drawer is not None:
                    ui.button(
                        "確認待ち",
                        on_click=pending_drawer.toggle,
                        color="purple",
                    ).props("outline dense")
    
            with ui.tab_panels(
                pkb_tabs,
                value=tab_refs[PKB_TAB_DEFAULT],
            ).classes("w-full"):
                with ui.tab_panel(tab_refs["record"]).classes("p-0 pt-3"):
                    memory_state = {'envelope': None, 'busy': False, 'result': None}
                    entity_state = {'busy': False, 'result': None}

                    with ui.expansion(
                        '対象(Entity)を追加',
                        value=False,
                    ).classes('w-full border border-slate-200 bg-slate-50'):
                        ui.label(
                            '未知の対象が確認待ちになった場合だけ、本人が対象名・分野・種類を明示して追加します。'
                            ' LLMの推測だけではEntityを自動作成しません。'
                        ).classes('text-sm')
                        ui.label('例: サブPC / pc / computer').classes('text-xs text-grey-7')
                        with ui.row().classes('w-full gap-2 flex-wrap'):
                            entity_name_input = ui.input(label='対象名').classes('min-w-56 grow')
                            entity_domain_input = ui.input(label='分野 (domain)').classes('min-w-40')
                            entity_type_input = ui.input(label='種類 (entity type)').classes('min-w-48')

                        @ui.refreshable
                        def entity_create_result():
                            result = entity_state['result']
                            if not result:
                                return
                            action = '追加しました' if result.get('created') else '既存Entityを利用します'
                            ui.label(
                                f"{action}: {result.get('name')} / "
                                f"{result.get('domain')} / {result.get('entity_type')}"
                            ).classes('text-sm text-green-800')

                        async def do_entity_create():
                            if entity_state['busy']:
                                return
                            entity_state['busy'] = True
                            entity_create_button.disable()
                            try:
                                result = await run.io_bound(
                                    create_entity,
                                    entity_name_input.value or '',
                                    entity_domain_input.value or '',
                                    entity_type_input.value or '',
                                )
                                entity_state['result'] = result
                                # A previous unresolved Memory Intake result is keyed by its
                                # input_id. After the authoritative Entity is created, issue a
                                # new envelope when the same text is submitted again.
                                memory_state['envelope'] = None
                                ui.notify(
                                    'Entityを登録しました。同じ記録内容をそのまま再処理できます。',
                                    type='positive',
                                )
                            except Exception as exc:
                                ui.notify(str(exc)[:240], type='negative')
                            finally:
                                entity_state['busy'] = False
                                entity_create_button.enable()
                                entity_create_result.refresh()

                        entity_create_button = ui.button(
                            '対象を追加',
                            on_click=do_entity_create,
                            color='blue-grey',
                        )
                        entity_create_result()

                    with ui.expansion('自然言語でまとめて記録（Memory Intake v1）', value=True).classes('w-full'):
                        ui.label('例: サブPCのWindows11を26H2に上げた。 明確な対応文は自動記録し、曖昧な部分は保留します。予定から現在状態は更新しません。').classes('text-sm')
                        intake_text = ui.textarea(label='記録する内容').classes('w-full')
    
                        @ui.refreshable
                        def memory_result():
                            result = memory_state['result']
                            if result:
                                ui.label(result.get('message') or ('再送済みの結果です。' if result.get('status') == 'replayed' else '処理結果'))
                                labels = {'auto_commit': '記録済み', 'pending': '確認・補足待ち',
                                          'task_context_only': '一時的な内容', 'ignore': '記録対象外'}
                                for candidate in result.get('candidates', []):
                                    if candidate.get('reason') == 'duplicate_existing_event':
                                        prefix = '既存記録と同一のため追加なし'
                                    else:
                                        prefix = labels[candidate['decision']]
                                    ui.label(prefix + ': ' + candidate['audit']['draft']['evidence']['quote'])
                                if result.get('status') in {'committed', 'replayed'} and not result.get('candidates'):
                                    ui.label('記憶として保存する内容はありません。')
    
                                envelope = memory_state['envelope']
                                if envelope is not None:
                                    with ui.expansion('Memory Intake 稼働ログ', value=False).classes(
                                        'w-full border border-blue-100 bg-white mt-2'
                                    ):
                                        ui.label(
                                            'Extractor候補、Grounding、WriteDecision、Claim / derived State / PendingのIDを表示します。'
                                            ' モデルの推論過程は保存・表示しません。'
                                        ).classes('text-xs text-grey-7')
                                        export_text = json.dumps(
                                            _memory_intake_log_export(envelope, result),
                                            ensure_ascii=False,
                                            indent=2,
                                            default=str,
                                        )
    
                                        def copy_memory_intake_log(text: str = export_text) -> None:
                                            ui.run_javascript(
                                                'navigator.clipboard.writeText('
                                                + json.dumps(text, ensure_ascii=False)
                                                + ')'
                                            )
                                            ui.notify('Memory Intake稼働ログをコピーしました', type='positive')
    
                                        ui.button(
                                            'ログをコピー',
                                            icon='content_copy',
                                            on_click=copy_memory_intake_log,
                                        ).props('outline dense').classes('self-start')
                                        ui.code(export_text, language='json').classes('w-full text-xs')
    
                        async def do_memory_write():
                            if memory_state['busy']:
                                return
                            text = intake_text.value or ''
                            if not text.strip():
                                ui.notify('記録する内容を入力してください。')
                                return
                            envelope = memory_state['envelope']
                            if envelope is None or envelope.raw_text != text:
                                envelope = MemoryIntake.issue(text)
                                memory_state['envelope'] = envelope
                            memory_state['busy'] = True
                            memory_button.disable()
                            try:
                                memory_state['result'] = await run.io_bound(register_memory_intake, envelope)
                            except Exception:
                                memory_state['result'] = {'message': '保存できませんでした。Daily Runtimeのproduction schemaと接続を確認してください。同じ内容で再試行できます。'}
                            finally:
                                memory_state['busy'] = False
                                memory_button.enable()
                                memory_result.refresh()
                                pending_panel.refresh()
                        memory_button = ui.button('まとめて記録する', on_click=do_memory_write)
                        memory_result()
    
    
                    with ui.expansion(
                        "記録",
                        value=_PKB_UI_OPEN["write"],
                        on_value_change=remember_expansion("write"),
                    ).classes(
                        "w-full border-2 border-green-300 bg-green-50 text-green-900"
                        + _block_visibility_class("pkb", "write")
                    ):
                        ui.label("例: メインPCをDRV-A3へ更新した。 / メインPCのGPUドライバーをDRV-G1へ更新した。 / RCカーBのサーボをSERVO-X3へ交換した。").classes("text-sm")
                        write_input = ui.textarea(label="自然言語で記録").classes("w-full")
                        @ui.refreshable
                        def write_result():
                            if state["write_busy"]:
                                with ui.row().classes("items-center gap-2"):
                                    ui.spinner(size="sm", color="green")
                                    ui.label("ローカルLLMで解析中… 画面はそのまま利用できます。")
                            elif state["write"]:
                                _display_result(state["write"])
                            else:
                                ui.label("まだ記録していません。")
                        async def do_write():
                            if state["write_busy"]:
                                return
                            state["write_busy"] = True
                            write_button.disable()
                            write_result.refresh()
                            try:
                                # register_text may wait on local Ollama for tens of seconds.
                                # Keep NiceGUI's event loop responsive by moving the blocking
                                # DB/Ollama work to an I/O worker thread.
                                state["write"] = await run.io_bound(register_text, write_input.value or "")
                            except Exception as exc:
                                state["write"] = {"status": "error", "reason": str(exc)}
                            finally:
                                state["write_busy"] = False
                                write_button.enable()
                                write_result.refresh()
                                pending_panel.refresh()
                        write_button = ui.button("記録する", on_click=do_write, color="green")
                        write_result()
    
    
                    with ui.expansion(
                        "訂正",
                        value=_PKB_UI_OPEN["correction"],
                        on_value_change=remember_expansion("correction"),
                    ).classes(
                        "w-full border-2 border-amber-300 bg-amber-50 text-amber-900" + _block_visibility_class("pkb", "correction")
                    ):
                        ui.label("例: 訂正：サブPCではなくメインPCをDRV-A1へ更新した。").classes("text-sm")
                        correction_input = ui.textarea(label="明示的に訂正").classes("w-full")
                        @ui.refreshable
                        def correction_result():
                            if state["correction"]:
                                _display_result(state["correction"])
                            else:
                                ui.label("まだ訂正していません。")
                        def do_correct():
                            try:
                                state["correction"] = correct_text(correction_input.value or "")
                            except Exception as exc:
                                state["correction"] = {"status": "error", "reason": str(exc)}
                            correction_result.refresh()
                            search_result.refresh()
                            pending_panel.refresh()
                        ui.button("訂正する", on_click=do_correct, color="orange")
                        correction_result()
                with ui.tab_panel(tab_refs["search"]).classes("p-0 pt-3"):
                    with ui.expansion(
                        "検索・履歴",
                        value=_PKB_UI_OPEN["search"],
                        on_value_change=remember_expansion("search"),
                    ).classes(
                        "w-full border-2 border-blue-300 bg-blue-50 text-blue-900"
                        + _block_visibility_class("pkb", "search")
                    ):
                        ui.label("例: メインPCの構成 / メインPCのGPUの現在のドライバー / サブPCのドライバー更新履歴").classes("text-sm")
                        search_input = ui.input(label="自然言語で検索").classes("w-full")
                        @ui.refreshable
                        def search_result():
                            result = state["search"]
                            if not result:
                                ui.label("検索結果はまだありません。")
                                return
                            ui.label(f'件数: {result.get("total", 0)}')
                            rows = result.get("items", [])
                            if not rows:
                                ui.label("該当する記録はありません。")
                                return
                            if result.get("result_kind") == "components":
                                columns = [
                                    {"name": "parent", "label": "親Entity", "field": "parent_name"},
                                    {"name": "relation", "label": "関係", "field": "relation_predicate"},
                                    {"name": "role", "label": "役割", "field": "relation_role"},
                                    {"name": "component", "label": "構成要素", "field": "component_name"},
                                    {"name": "type", "label": "型", "field": "component_type"},
                                    {"name": "driver", "label": "現在ドライバー", "field": "current_driver"},
                                    {"name": "since", "label": "状態開始", "field": "state_valid_from"},
                                    {"name": "source", "label": "状態の出典", "field": "state_source_uri"},
                                ]
                                ui.table(columns=columns, rows=rows, row_key="relation_id").classes("w-full")
                                return
                            for row in rows:
                                raw_status = row.get("status_at_cutoff")
                                if raw_status == "active":
                                    row["status_at_cutoff"] = "現行記録"
                                elif raw_status == "superseded":
                                    row["status_at_cutoff"] = "旧版・訂正済み"
                            columns = [
                                {"name": "entity", "label": "対象", "field": "entity_name"},
                                {"name": "predicate", "label": "種類", "field": "predicate"},
                                {"name": "semantic", "label": "意味", "field": "semantic_kind"},
                                {"name": "value", "label": "値", "field": "value"},
                                {"name": "valid_from", "label": "有効時点", "field": "valid_from"},
                                {"name": "status", "label": "記録状態", "field": "status_at_cutoff"},
                                {"name": "source", "label": "出典", "field": "source_uri"},
                            ]
                            ui.table(columns=columns, rows=rows, row_key="id").classes("w-full")
                        def do_search():
                            try:
                                state["search"] = search_text(search_input.value or "")
                            except Exception as exc:
                                state["search"] = {"status": "error", "total": 0, "items": [], "reason": str(exc)}
                            search_result.refresh()
                        ui.button("検索する", on_click=do_search, color="blue")
                        search_result()
    
    
                    with ui.expansion(
                        "Entity一覧",
                        value=_PKB_UI_OPEN["entities"],
                        on_value_change=remember_expansion("entities"),
                    ).classes(
                        "w-full border-2 border-indigo-300 bg-indigo-50 text-indigo-900" + _block_visibility_class("pkb", "entities")
                    ):
                        try:
                            with connection() as db:
                                entity_rows = _entities(db)
                        except Exception as exc:
                            ui.label("Entity一覧を取得できません: " + str(exc)).classes("text-red-700")
                        else:
                            ui.label(
                                "詳細画面は共通骨格です。PC・RCなどのEntity型ごとの専用表示は必要に応じて追加します。"
                            ).classes("text-sm")
                            for row in entity_rows:
                                with ui.row().classes(
                                    "w-full items-center gap-3 border-b border-indigo-200 py-2"
                                ):
                                    with ui.column().classes("grow gap-0"):
                                        ui.label(row["name"]).classes("font-medium")
                                        ui.label(
                                            f"{row['domain']} / {row['entity_type']}"
                                        ).classes("text-xs text-grey-7")
                                    ui.button("詳細", icon="open_in_new").props(
                                        f"flat href=/entity/{row['id']} tag=a"
                                    )
    
    pkb_page._context_sync = sync
    return pkb_page
