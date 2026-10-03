"""entity page for the daily portal."""


def sync(portal_context: dict) -> None:
    globals().update(portal_context)


def register(portal_context: dict):
    sync(portal_context)
    @ui.page("/entity/{entity_id}")
    def entity_page(entity_id: str):
        globals().update(portal_context)
        try:
            with connection() as db:
                detail = load_entity_detail(db, entity_id)
        except Exception as exc:
            detail = None
            error = str(exc)
        else:
            error = None
    
        with ui.column().classes("w-full max-w-7xl mx-auto gap-4 p-4"):
            _portal_header(
                "Entity 詳細",
                "現在・履歴・関連・出典を共通Entity画面で確認",
            )
    
            # No arrow icon: keep navigation compact and text-only.
            ui.button("PKBへ戻る").props("flat href=/pkb tag=a")
    
            if error:
                with ui.card().classes(
                    "w-full border-2 border-red-300 bg-red-50"
                ):
                    ui.label(
                        "Entity詳細を読み取れません: " + error
                    ).classes("text-red-800")
                return
    
            if detail is None:
                with ui.card().classes("w-full border-2 border-grey-300"):
                    ui.label("Entityが見つかりません。")
                return
    
            entity = detail["entity"]
    
            # Compact Entity header.
            with ui.card().classes(
                "w-full border-2 border-blue-300 bg-blue-50"
            ):
                with ui.row().classes(
                    "w-full items-center gap-4 flex-wrap"
                ):
                    with ui.column().classes("gap-0 grow"):
                        ui.label(entity["name"]).classes(
                            "text-2xl font-bold text-blue-900"
                        )
                        ui.label(
                            f'{entity["domain"]} / {entity["entity_type"]}'
                        ).classes("text-sm text-grey-7")
    
                ui.label(
                    f'Entity ID: {entity["id"]}'
                ).classes("font-mono text-xs text-grey-7")
    
            visible_tabs, default_tab = _entity_tab_config()
            tab_refs = {}
    
            with ui.tabs().classes("w-full") as tabs:
                for key in ENTITY_TAB_ORDER:
                    if visible_tabs[key]:
                        tab_refs[key] = ui.tab(ENTITY_TAB_LABELS[key])
    
            with ui.tab_panels(
                tabs,
                value=tab_refs[default_tab],
            ).classes("w-full"):
    
                if visible_tabs["overview"]:
                    with ui.tab_panel(tab_refs["overview"]):
                        rows = detail["current"]
    
                        if not rows:
                            ui.label(
                                "現在値として表示できるState / Attributeはありません。"
                            )
                        else:
                            display = [
                                {
                                    **row,
                                    "value_display": str(row["value"]),
                                    "valid_from_display": str(row["valid_from"]),
                                }
                                for row in rows
                            ]
    
                            ui.table(
                                columns=[
                                    {
                                        "name": "kind",
                                        "label": "意味",
                                        "field": "semantic_kind",
                                        "align": "left",
                                    },
                                    {
                                        "name": "predicate",
                                        "label": "項目",
                                        "field": "predicate",
                                        "align": "left",
                                    },
                                    {
                                        "name": "value",
                                        "label": "現在値",
                                        "field": "value_display",
                                        "align": "left",
                                    },
                                    {
                                        "name": "since",
                                        "label": "開始",
                                        "field": "valid_from_display",
                                        "align": "left",
                                    },
                                ],
                                rows=display,
                                row_key="id",
                            ).props("dense flat").classes("w-full")
    
                if visible_tabs["history"]:
                    with ui.tab_panel(tab_refs["history"]):
                        combined = []
    
                        for row in detail["events"]:
                            combined.append(
                                {
                                    "id": "event:" + row["id"],
                                    "kind": "Event",
                                    "predicate": row["predicate"],
                                    "value_display": str(row["value"]),
                                    "time_display": str(row["valid_from"]),
                                    "_sort": str(row["valid_from"]),
                                }
                            )
    
                        for row in detail["history"]:
                            combined.append(
                                {
                                    "id": "history:" + row["id"],
                                    "kind": row["semantic_kind"],
                                    "predicate": row["predicate"],
                                    "value_display": str(row["value"]),
                                    "time_display": (
                                        f'{row["valid_from"]} ～ {row["valid_to"]}'
                                    ),
                                    "_sort": str(row["valid_from"]),
                                }
                            )
    
                        combined.sort(
                            key=lambda row: row["_sort"],
                            reverse=True,
                        )
    
                        if not combined:
                            ui.label("履歴はありません。")
                        else:
                            ui.table(
                                columns=[
                                    {
                                        "name": "kind",
                                        "label": "種類",
                                        "field": "kind",
                                        "align": "left",
                                    },
                                    {
                                        "name": "time",
                                        "label": "時点 / 有効期間",
                                        "field": "time_display",
                                        "align": "left",
                                    },
                                    {
                                        "name": "predicate",
                                        "label": "項目",
                                        "field": "predicate",
                                        "align": "left",
                                    },
                                    {
                                        "name": "value",
                                        "label": "値",
                                        "field": "value_display",
                                        "align": "left",
                                    },
                                ],
                                rows=combined,
                                row_key="id",
                            ).props("dense flat").classes("w-full")
    
                if visible_tabs["relations"]:
                    with ui.tab_panel(tab_refs["relations"]):
                        relations = detail["relations"]
    
                        if not relations:
                            ui.label(
                                "現在または履歴Relationはありません。"
                            )
                        else:
                            for rel in relations:
                                direction_label = (
                                    "このEntityから"
                                    if rel["direction"] == "outgoing"
                                    else "このEntityへ"
                                )
                                role = (
                                    f' / role={rel["relation_role"]}'
                                    if rel["relation_role"]
                                    else ""
                                )
    
                                with ui.row().classes(
                                    "w-full items-center gap-3 "
                                    "border-b border-purple-200 py-2"
                                ):
                                    with ui.column().classes("grow gap-0"):
                                        ui.label(
                                            f'{direction_label} / '
                                            f'{rel["predicate"]}{role} / '
                                            f'{rel["other_entity_name"]}'
                                        ).classes("font-medium")
    
                                        ui.label(
                                            f'{rel["other_entity_type"]} / '
                                            f'from {rel["valid_from"]}'
                                        ).classes(
                                            "text-xs text-grey-7"
                                        )
    
                                    # No arrow/open icon.
                                    ui.button("詳細").props(
                                        f'flat href=/entity/'
                                        f'{rel["other_entity_id"]} tag=a'
                                    )
    
                if visible_tabs["sources"]:
                    with ui.tab_panel(tab_refs["sources"]):
                        rows = _entity_source_rows(detail)
    
                        if not rows:
                            ui.label("表示できるSourceはありません。")
                        else:
                            ui.table(
                                columns=[
                                    {
                                        "name": "source",
                                        "label": "Source",
                                        "field": "source_uri",
                                        "align": "left",
                                    },
                                    {
                                        "name": "used_by",
                                        "label": "参照箇所",
                                        "field": "used_by",
                                        "align": "left",
                                    },
                                    {
                                        "name": "count",
                                        "label": "参照数",
                                        "field": "reference_count",
                                        "align": "right",
                                    },
                                ],
                                rows=rows,
                                row_key="id",
                            ).props("dense flat").classes("w-full")
    
    entity_page._context_sync = sync
    return entity_page
