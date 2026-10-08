"""core page for the daily portal."""


def sync(portal_context: dict) -> None:
    globals().update(portal_context)


def register(portal_context: dict):
    sync(portal_context)

    def _portal(name: str):
        """Resolve parent application dependencies at call time."""
        return portal_context[name]

    @ui.page("/core")
    def core_page(task_id: str = ""):
        globals().update(portal_context)
        state = {"result": None, "busy": False, "resume_busy": False,
                 "trace": None, "trace_error": None,
                 "advisor_model": _UI_PREFERENCES.get("core_advisor_model"),
                 "advisor_timeout": int(_UI_PREFERENCES.get("core_advisor_timeout") or 60),
                 "protocol_result": None, "protocol_busy": False,
                 "guided_session": None, "guided_busy": False,
                 "guided_turn": 0, "guided_stop_event": None,
                 "guided_stop_requested": False,
                 "guided_history_read_only": False,
                 "guided_request": "", "guided_task_id": "",
                 "guided_member_progress": {}}
    
        list_limits = {key: _UI_PREFERENCES["core"][key] for key in CORE_TASK_LIST_DEFAULTS}
        list_defaults = dict(list_limits)
    
        def more_tasks(key, panel):
            list_limits[key] += _UI_PREFERENCES["core"][key]
            panel.refresh()
    
        def current_ooda():
            if state["busy"] or state["resume_busy"]:
                return derive_ooda({"status": "received", "phase": "observe"})
            trace = state["trace"]
            if trace:
                return derive_ooda(trace["task"], trace["actions"])
            return derive_ooda(state["result"])
    
        def current_advisor_shadow():
            # Prefer DB trace because background Advisor updates arrive after the
            # deterministic result object has already been returned to the UI.
            trace = state["trace"]
            if trace:
                shadow = (trace.get("task") or {}).get("advisor_shadow")
                if shadow:
                    return shadow
            result = state["result"] or {}
            return result.get("advisor_shadow")
    
        def load_current_trace():
            # Share one snapshot between the bar, Task detail and execution log.
            state["trace"] = None
            state["trace_error"] = None
            task_id = (state["result"] or {}).get("task_id")
            if task_id:
                try:
                    state["trace"] = _portal("load_core_task_trace")(UUID(task_id))
                except Exception as exc:
                    state["trace_error"] = str(exc)
    
        async def poll_advisor_shadow():
            result = state["result"] or {}
            task_id = result.get("task_id")
            if not task_id:
                return
            advisor = current_advisor_shadow() or {}
            if advisor.get("job_status") not in {"queued", "running"}:
                return
            try:
                state["trace"] = await run.io_bound(
                    _portal("load_core_task_trace"), UUID(task_id)
                )
                state["trace_error"] = None
                trace_task = (state["trace"] or {}).get("task") or {}
                for key in ("status", "phase", "selected_capability", "question"):
                    if key in trace_task:
                        result[key] = trace_task.get(key)
                if trace_task.get("message"):
                    result["message"] = trace_task.get("message")
                cooperative_result = trace_task.get("cooperative_result")
                if cooperative_result and trace_task.get("selected_capability") == "pkb_search":
                    result["search"] = cooperative_result
            except Exception as exc:
                state["trace_error"] = str(exc)
                return
            ooda_bar.refresh()
            core_result.refresh()
            trace_panel.refresh()
            resume_panel.refresh()
            open_tasks_panel.refresh()
            completed_tasks_panel.refresh()
            screen_log_panel.refresh()
    
        # NiceGUI drawers are top-level layout elements and must be created as
        # direct children of the page, not inside the central content column.
        task_drawer = ui.right_drawer(value=True).classes("bg-orange-50 p-3").props(
            "bordered width=300 breakpoint=700"
        )
    
        def remember_core_expansion(key: str):
            def _remember(event):
                _set_core_ui_open(key, event.value)
            return _remember
    
        with ui.column().classes("w-full max-w-5xl mx-auto gap-4 p-4"):
            _nav()
            ui.label("RITSUKO — Secretary Core / Orchestrator").classes("text-2xl font-bold")
    
            try:
                installed_magi_models = list_magi_models()
            except Exception:
                installed_magi_models = []
            try:
                default_magi_model = choose_magi_model(installed_magi_models)
            except Exception:
                default_magi_model = None
    
            def copy_protocol_json(text: str, label: str) -> None:
                ui.run_javascript(
                    "navigator.clipboard.writeText("
                    + json.dumps(text, ensure_ascii=False) + ")"
                )
                ui.notify(label + "をコピーしました", type="positive")
    
            with ui.card().classes("w-full border-2 border-teal-300 bg-teal-50"):
                ui.label("RITSUKO").classes("text-lg font-bold text-teal-900")
                current_task_slot = ui.column().classes("w-full gap-2")
                new_request_slot = ui.column().classes("w-full gap-2")
                with new_request_slot:
                    ui.label("新しい依頼").classes("font-bold text-teal-900")
                    guided_input = ui.textarea(
                        label="新しいTaskとして依頼",
                        placeholder="自然言語で依頼を入力",
                    ).classes("w-full")

                source_catalog = default_resource_catalog()
                available_sources, unavailable_sources = resource_catalog_summary(
                    source_catalog
                )
                with ui.expansion(
                    "MAGI / Observation / 安全境界",
                    value=_CORE_UI_OPEN["limits"],
                    on_value_change=remember_core_expansion("limits"),
                    icon="info",
                ).classes(
                    "w-full border border-teal-200 bg-white"
                    + _block_visibility_class("core", "limits")
                ):
                    ui.label(
                        "RITSUKOはTask状態とObservationから次の判断を選び、"
                        "MELCHIOR / CASPER / BALTHASARをProvider非依存slotとして利用します。"
                    ).classes("text-sm")
                    ui.label(
                        "private / sensitive Observationを含む再分析はCloud Context Gateでlocal-only。"
                        "外部変更操作や未接続Sourceを利用可能とは扱いません。"
                    ).classes("text-xs text-orange-800")
                    ui.label(
                        "利用可能Source: "
                        + (" / ".join(available_sources) or "なし")
                        + "　｜　未接続: "
                        + (" / ".join(unavailable_sources) or "なし")
                    ).classes("text-xs font-mono text-teal-900")
    
                try:
                    magi_profiles, configured_specs = _load_magi_configuration(
                        installed_magi_models, default_magi_model
                    )
                    state["magi_settings_error"] = None
                except Exception as exc:
                    magi_profiles = []
                    configured_specs = fallback_member_specs(default_magi_model)
                    state["magi_settings_error"] = str(exc)
    
                state["magi_member_specs"] = configured_specs
                spec_by_member = {item["name"]: item for item in configured_specs}
                profile_options = {
                    item["id"]: (
                        f"{item['display_name']}  [{item['provider']} / {item['model']}]"
                        + (
                            f" [ctx={int(item['context_window_tokens']) // 1024}K]"
                            f" [gen={int(item['ollama_num_predict'])}]"
                            if item["provider"] == "ollama"
                            and item.get("context_window_tokens")
                            and item.get("ollama_num_predict")
                            else ""
                        )
                    )
                    for item in magi_profiles
                }
                guided_member_controls = {}
    
                with ui.expansion(
                    "MAGI設定",
                    value=_CORE_UI_OPEN["magi_configuration"],
                    on_value_change=remember_core_expansion("magi_configuration"),
                    icon="psychology",
                ).classes(
                    "w-full border border-teal-200 bg-white"
                    + _block_visibility_class("core", "magi_configuration")
                ):
                    if state.get("magi_settings_error"):
                        ui.label(
                            "DBのMAGI設定を読み込めません。bootstrap値を表示中: "
                            + state["magi_settings_error"][:180]
                        ).classes("text-xs text-red-700")

                    with ui.element("div").classes("w-full grid grid-cols-3 gap-3 items-start"):
                        for member in MEMBER_NAMES:
                            spec = spec_by_member.get(member) or {
                                "profile_id": None, "enabled": False,
                                "weight": 1.0, "timeout_seconds": DEFAULT_TIMEOUT_SECONDS,
                                "provider": "-", "model": "-",
                            }
                            member_key = "magi_" + member.lower()
                            member_summary = (
                                ("有効" if spec.get("enabled") else "無効")
                                + " / " + str(spec.get("provider") or "-")
                                + " / " + str(spec.get("model") or "-")
                            )
                            with ui.expansion(
                                member + " — " + member_summary,
                                value=_CORE_UI_OPEN[member_key],
                                on_value_change=remember_core_expansion(member_key),
                            ).classes("w-full min-w-0 border border-teal-200 bg-white"):
                                enabled_control = ui.switch(
                                    "有効", value=bool(spec.get("enabled"))
                                )
                                profile_control = ui.select(
                                    options=profile_options,
                                    value=spec.get("profile_id"),
                                    label=f"{member} / LLM profile",
                                ).classes("w-full")
                                weight_control = ui.number(
                                    label="Weight",
                                    value=float(spec.get("weight") or 1.0),
                                    min=0.1, max=100, step=0.1,
                                ).classes("w-full")
                                timeout_control = ui.select(
                                    options=list(CORE_ADVISOR_TIMEOUT_OPTIONS),
                                    value=int(spec.get("timeout_seconds") or DEFAULT_TIMEOUT_SECONDS),
                                    label="Timeout（秒）",
                                ).classes("w-full")
                                retry_control = ui.switch(
                                    "Turn内リトライ",
                                    value=bool(
                                        spec.get("retry_within_turn", DEFAULT_RETRY_WITHIN_TURN)
                                    ),
                                )
                                ui.label(
                                    "Retry時間上限はTimeoutの50%・最大3回"
                                ).classes("text-xs text-grey-7")
                                guided_member_controls[member] = {
                                    "enabled": enabled_control,
                                    "profile": profile_control,
                                    "weight": weight_control,
                                    "timeout": timeout_control,
                                    "retry": retry_control,
                                }
    
                def collect_guided_assignments() -> list[dict]:
                    return [
                        {
                            "name": member,
                            "profile_id": guided_member_controls[member]["profile"].value,
                            "enabled": bool(guided_member_controls[member]["enabled"].value),
                            "weight": float(guided_member_controls[member]["weight"].value or 1.0),
                            "timeout_seconds": int(
                                guided_member_controls[member]["timeout"].value or DEFAULT_TIMEOUT_SECONDS
                            ),
                            "retry_within_turn": bool(
                                guided_member_controls[member]["retry"].value
                            ),
                        }
                        for member in MEMBER_NAMES
                    ]
    
                def save_guided_assignments(*, notify: bool = True) -> list[dict] | None:
                    try:
                        saved = _save_magi_assignments(collect_guided_assignments())
                        state["magi_member_specs"] = saved
                        state["magi_settings_error"] = None
                        if notify:
                            ui.notify("MAGI LLM設定をDBへ保存しました", type="positive")
                        return saved
                    except Exception as exc:
                        state["magi_settings_error"] = str(exc)
                        ui.notify(
                            "MAGI LLM設定を保存できません: " + str(exc)[:180],
                            type="negative",
                        )
                        return None
    
                with magi_settings_actions_slot:
                    ui.button(
                        "MAGI設定を保存",
                        icon="save",
                        on_click=lambda: save_guided_assignments(notify=True),
                    ).props("outline dense")
                    ui.link("LLM profileの追加・確認", "/settings").classes(
                        "text-sm text-blue-700"
                    )
    
                def guided_timeout_seconds(session: dict | None = None) -> float:
                    specs = (session or {}).get("member_specs") or state.get("magi_member_specs") or []
                    enabled = [
                        int(item.get("timeout_seconds") or DEFAULT_TIMEOUT_SECONDS)
                        for item in specs if item.get("enabled")
                    ]
                    return float(
                        max(enabled) if enabled else DEFAULT_TIMEOUT_SECONDS
                    )
    
                def begin_guided_run(
                    next_turn: int,
                    *,
                    task_id_value: str | None = None,
                    request_text: str | None = None,
                ) -> Event:
                    stop_event = Event()
                    state["guided_stop_event"] = stop_event
                    state["guided_stop_requested"] = False
                    state["guided_turn"] = max(1, int(next_turn))
                    state["guided_busy"] = True
                    state["guided_member_progress"] = {}
                    if task_id_value is not None:
                        state["guided_task_id"] = str(task_id_value)
                    if request_text is not None:
                        state["guided_request"] = str(request_text)
                    return stop_event

                def note_guided_turn(turn_number: int) -> None:
                    state["guided_turn"] = max(1, int(turn_number))

                def note_member_progress(event: dict) -> None:
                    normalized = normalize_member_progress_event(event)
                    if (
                        normalized["task_id"]
                        and normalized["task_id"] != str(state.get("guided_task_id") or "")
                    ):
                        return
                    member = normalized["member"]
                    if member:
                        state["guided_member_progress"][member] = normalized
                        guided_result_panel.refresh()

                async def guided_panel_caller(
                    envelope: dict,
                    *,
                    model: str = "",
                    timeout: float = DEFAULT_TIMEOUT_SECONDS,
                    member_specs: list[dict] | None = None,
                ) -> dict:
                    active_specs = list(member_specs or [])
                    active_names = {
                        str(item.get("name") or "")
                        for item in active_specs
                        if item.get("enabled")
                    }
                    for spec in state.get("magi_member_specs") or []:
                        member = str(spec.get("name") or "")
                        if (
                            spec.get("enabled")
                            and member
                            and member not in active_names
                        ):
                            note_member_progress({
                                "task_id": str(envelope.get("task_id") or ""),
                                "turn": int(envelope.get("turn") or 0),
                                "member": member,
                                "provider": str(spec.get("provider") or ""),
                                "model": str(spec.get("model") or ""),
                                "state": "withheld",
                                "validated_summary": "Cloud Context Gate / local-only",
                            })
                    return await call_guided_panel_async(
                        envelope,
                        model=model,
                        timeout=timeout,
                        member_specs=active_specs,
                        on_member_progress=note_member_progress,
                    )

                async def guided_dialogue_starter(user_raw: str, **kwargs) -> dict:
                    return await start_dialogue_async(
                        user_raw,
                        caller=guided_panel_caller,
                        **kwargs,
                    )

                async def guided_observation_continuation(
                    session: dict, observations: list[dict], **kwargs
                ) -> dict:
                    return await continue_with_verified_observations_async(
                        session,
                        observations,
                        caller=guided_panel_caller,
                        **kwargs,
                    )

                async def guided_user_continuation(
                    session: dict, user_text: str, **kwargs
                ) -> dict:
                    return await continue_with_user_clarification_async(
                        session,
                        user_text,
                        caller=guided_panel_caller,
                        **kwargs,
                    )

                async def guided_review_continuation(
                    session: dict, observation: dict, **kwargs
                ) -> dict:
                    return await continue_with_proposal_review_async(
                        session,
                        observation,
                        caller=guided_panel_caller,
                        **kwargs,
                    )
    
                def request_guided_stop() -> None:
                    stop_event = state.get("guided_stop_event")
                    if not state.get("guided_busy") or stop_event is None:
                        return
                    stop_event.set()
                    state["guided_stop_requested"] = True
                    ui.notify(
                        "現在のTurn完了後に停止します",
                        type="warning",
                    )
                    guided_result_panel.refresh()
    
                def saved_task_for_session(session: dict) -> dict:
                    trace_task = (state.get("trace") or {}).get("task") or {}
                    trace_task_id = str(
                        trace_task.get("id") or trace_task.get("task_id") or ""
                    )
                    session_task_id = str(session.get("task_id") or "")
                    if not session_task_id or trace_task_id != session_task_id:
                        return {}
                    return trace_task
    
                def render_guided_verification(session: dict) -> None:
                    session_text = json.dumps(
                        export_dialogue(session),
                        ensure_ascii=False,
                        indent=2,
                    )
                    ui.button(
                        "対話結果を一括コピー",
                        icon="content_copy",
                        on_click=lambda value=session_text: copy_protocol_json(
                            value, "対話結果"
                        ),
                    ).props("outline dense")
                    ui.label(
                        "Prompt: " + str(session.get("prompt_version") or "-")
                        + " / 最終Question Purpose: "
                        + str(session.get("last_question_purpose") or "-")
                    ).classes("font-mono text-xs text-grey-7")
                    for turn in session.get("turns") or []:
                        envelope = turn.get("request_envelope") or {}
                        purpose = (
                            turn.get("question_purpose")
                            or envelope.get("question_purpose")
                            or "analysis"
                        )
                        with ui.expansion(
                            "Turn "
                            + str(envelope.get("turn") or "?")
                            + " — "
                            + (
                                "classify"
                                if turn.get("stage") == "classify"
                                else str(purpose)
                            )
                            + " / "
                            + str(turn.get("status") or "-"),
                            value=False,
                        ).classes("w-full border"):
                            ui.label(
                                "RITSUKOからの問い: "
                                + str(envelope.get("question_from_ritsuko") or "-")
                            ).classes("text-sm")
                            member_results = [
                                item
                                for item in (turn.get("member_results") or [])
                                if isinstance(item, dict)
                            ]
                            if member_results:
                                with ui.element("div").classes(
                                    "w-full grid grid-cols-3 gap-2 items-start"
                                ):
                                    by_member = {
                                        str(item.get("name") or ""): item
                                        for item in member_results
                                    }
                                    for member in MEMBER_NAMES:
                                        item = by_member.get(member)
                                        if item is None:
                                            continue
                                        with ui.card().classes(
                                            "w-full border border-blue-grey-200 shadow-none"
                                        ):
                                            ui.label(member).classes("font-bold")
                                            ui.label(
                                                str(item.get("provider") or "-")
                                                + " / "
                                                + str(item.get("model") or "-")
                                                + " / "
                                                + str(item.get("status") or "-")
                                            ).classes("text-xs text-grey-7")
                                            summary = member_response_summary(
                                                item.get("response")
                                            )
                                            if summary:
                                                ui.label(summary).classes("text-sm")
                                            with ui.expansion(
                                                "member技術詳細", value=False
                                            ).classes("w-full"):
                                                ui.code(
                                                    json.dumps(
                                                        {
                                                            "response": item.get("response"),
                                                            "errors": item.get("errors"),
                                                            "diagnostic": item.get("diagnostic"),
                                                        },
                                                        ensure_ascii=False,
                                                        indent=2,
                                                    ),
                                                    language="json",
                                                ).classes("w-full")
                            if turn.get("consensus") is not None:
                                with ui.expansion(
                                    "MAGI統合結果", value=False
                                ).classes("w-full"):
                                    ui.code(
                                        json.dumps(
                                            turn.get("consensus"),
                                            ensure_ascii=False,
                                            indent=2,
                                        ),
                                        language="json",
                                    ).classes("w-full")
                            with ui.expansion(
                                "Turn技術詳細", value=False
                            ).classes("w-full"):
                                ui.label(
                                    "Envelope / 通信診断。hidden reasoning / thinking本文は表示しません。"
                                ).classes("text-xs text-grey-7")
                                ui.code(
                                    json.dumps(
                                        {
                                            "request_envelope": envelope,
                                            "diagnostic": turn.get("diagnostic") or {},
                                            "errors": turn.get("errors") or [],
                                        },
                                        ensure_ascii=False,
                                        indent=2,
                                    ),
                                    language="json",
                                ).classes("w-full")

                @ui.refreshable
                def guided_result_panel():
                    session = state.get("guided_session")
                    saved_task = saved_task_for_session(session) if session else {}
                    presentation = build_task_presentation(
                        session,
                        saved_task=saved_task,
                        fallback_request=state.get("guided_request") or "",
                        busy=bool(state.get("guided_busy")),
                        read_only=bool(state.get("guided_history_read_only")),
                        live_member_states=state.get("guided_member_progress") or {},
                    )

                    if (
                        presentation["request_text"]
                        or session is not None
                        or state.get("guided_busy")
                    ):
                        with ui.card().classes(
                            "w-full border-2 border-teal-300 bg-white shadow-none"
                        ):
                            ui.label("現在のTask").classes("font-bold text-teal-900")
                            with ui.grid(columns=2).classes("w-full gap-2"):
                                ui.label("依頼").classes("text-xs text-grey-7")
                                ui.label(
                                    presentation["request_text"] or "読み込み中"
                                ).classes("font-medium")
                                ui.label("現在").classes("text-xs text-grey-7")
                                ui.label(presentation["status_label"]).classes("font-medium")
                                ui.label("最新結果").classes("text-xs text-grey-7")
                                ui.label(
                                    presentation["latest_result_summary"]
                                ).classes("text-sm")
                                ui.label("次の操作").classes("text-xs text-grey-7")
                                ui.label(
                                    presentation["system_next_action"]
                                ).classes(
                                    "text-sm font-medium"
                                    + (
                                        " text-orange-900"
                                        if presentation["user_action_required"]
                                        else ""
                                    )
                                )

                            live_states = presentation["live_member_states"]
                            enabled_specs = {
                                str(item.get("name") or ""): item
                                for item in (state.get("magi_member_specs") or [])
                                if item.get("enabled")
                            }
                            if state.get("guided_busy") and (live_states or enabled_specs):
                                ui.label(
                                    "MAGI 進行状況 — 現在のTurn"
                                ).classes("font-bold text-sm text-blue-900")
                                with ui.element("div").classes(
                                    "w-full grid grid-cols-3 gap-2 items-stretch"
                                ):
                                    for member in MEMBER_NAMES:
                                        progress = live_states.get(member)
                                        spec = enabled_specs.get(member) or {}
                                        if progress is None and not spec:
                                            continue
                                        progress = progress or {
                                            "state": "queued",
                                            "state_label": "待機中",
                                            "provider": str(spec.get("provider") or ""),
                                            "model": str(spec.get("model") or ""),
                                            "summary": "",
                                            "elapsed_seconds": None,
                                        }
                                        color = {
                                            "running": "blue",
                                            "completed": "green",
                                            "withheld": "grey",
                                            "invalid": "orange",
                                            "unavailable": "red",
                                        }.get(progress.get("state"), "grey")
                                        with ui.card().classes(
                                            "w-full border border-blue-grey-200 bg-grey-50 shadow-none"
                                        ):
                                            with ui.row().classes(
                                                "w-full items-center justify-between gap-2"
                                            ):
                                                ui.label(member).classes("font-bold text-sm")
                                                ui.badge(
                                                    str(progress.get("state_label") or "-"),
                                                    color=color,
                                                )
                                            ui.label(
                                                str(progress.get("provider") or "-")
                                                + " / "
                                                + str(progress.get("model") or "-")
                                            ).classes("text-xs text-grey-7")
                                            if progress.get("elapsed_seconds") is not None:
                                                ui.label(
                                                    "elapsed="
                                                    + str(progress["elapsed_seconds"])
                                                    + "s"
                                                ).classes("text-xs text-grey-7")
                                            if progress.get("summary"):
                                                ui.label(
                                                    str(progress["summary"])
                                                ).classes("text-xs")

                            if presentation["recent_updates"]:
                                ui.label("最新の更新").classes("font-bold text-sm")
                                for update in presentation["recent_updates"][:4]:
                                    ui.label("• " + update).classes("text-xs text-grey-8")

                    if state["guided_busy"]:
                        ui.label(
                            f"Turn {int(state.get('guided_turn') or 1)} を実行中"
                        ).classes("font-mono text-sm")
                        if state.get("guided_stop_requested"):
                            ui.label(
                                "停止要求済み：現在のTurnが完了したら次へ進まず停止します。"
                            ).classes("text-sm text-orange-900")
                        else:
                            ui.button(
                                "このTurnで停止",
                                icon="stop_circle",
                                color="orange",
                                on_click=request_guided_stop,
                            ).props("outline")
                        return
                    if session is None:
                        ui.label(
                            "表示中のTaskはありません。下の「新しい依頼」から開始できます。"
                        ).classes("text-sm text-grey-7")
                        return
                    if state.get("guided_history_read_only"):
                        ui.label("保存済みTaskのMAGI対話を閲覧中（read-only）").classes(
                            "font-bold text-blue-grey-800"
                        )
                        ui.label(
                            "Task status="
                            + str(saved_task.get("status") or "-")
                            + " / MAGI session status="
                            + str(session.get("status") or "-")
                        ).classes("font-mono text-xs text-grey-7")
                    ui.label(
                        f"Task: {session['task_id']} / status={session['status']}"
                        f" / RITSUKO next={session['next_step']}"
                    ).classes("font-mono text-xs")
                    if session.get("tool_read_executed"):
                        verified_sources = list(dict.fromkeys(
                            str(item.get("source") or "")
                            for item in (session.get("observations") or [])
                            if isinstance(item, dict)
                            and item.get("verified") is True
                            and str(item.get("source") or "")
                        ))
                        ui.label(
                            "実Source read済み / Action・Result・Source・Audit記録対象"
                            + (
                                " / verified=" + ", ".join(verified_sources)
                                if verified_sources else ""
                            )
                        ).classes("text-xs text-green-800")
                    unavailable_requests = [
                        item
                        for item in (
                            session.get("unavailable_source_requests") or []
                        )
                        if isinstance(item, dict)
                    ]
                    if unavailable_requests:
                        ui.label(
                            "利用不可Source要求: "
                            + ", ".join(dict.fromkeys(
                                str(item.get("source") or "unknown")
                                for item in unavailable_requests
                            ))
                        ).classes("text-xs text-orange-800")
                    gate = session.get("cloud_context_gate") or {}
                    if gate:
                        ui.label(
                            "Cloud Context Gate: " + str(gate.get("status") or "-")
                            + " / " + str(gate.get("mode") or "-")
                        ).classes("font-mono text-xs text-purple-800")
                    fallback = session.get("ritsuko_grounded_fallback") or {}
                    if fallback:
                        ui.label(
                            "RITSUKO Grounded fallback: "
                            + str(fallback.get("kind") or "-")
                            + " / source="
                            + str(fallback.get("source") or "-")
                            + " / result_count="
                            + str(fallback.get("result_count"))
                        ).classes("font-mono text-xs text-teal-800")
                    proposal_review = saved_task.get("proposal_review")
                    if isinstance(proposal_review, dict):
                        ui.label(
                            "Proposal review: "
                            + str(proposal_review.get("decision") or "-")
                            + " / " + str(proposal_review.get("status") or "-")
                        ).classes("font-mono text-xs text-green-800")
                        evaluation = proposal_review.get("magi_evaluation")
                        if isinstance(evaluation, dict):
                            ui.label(
                                "Post-review MAGI: "
                                + str(evaluation.get("state") or "-")
                            ).classes("font-mono text-xs text-purple-800")
                        memory_summary = proposal_review.get("memory_intake")
                        if isinstance(memory_summary, dict):
                            decisions = [
                                str(item.get("decision") or "-")
                                for item in (memory_summary.get("receipts") or [])
                                if isinstance(item, dict)
                            ]
                            ui.label(
                                "Memory Intake: "
                                + str(memory_summary.get("status") or "-")
                                + (" / " + ", ".join(decisions) if decisions else "")
                            ).classes("font-mono text-xs text-green-800")
                    if (
                        not state.get("guided_history_read_only")
                        and session["status"] == "waiting_information"
                    ):
                        ui.label(
                            "未解決の情報要求（利用不可Sourceまたは追加情報が必要）"
                        ).classes("font-bold text-orange-900")
                        ui.code(json.dumps(
                            session["pending_requests"], ensure_ascii=False, indent=2
                        ), language="json").classes("w-full")
                        observation_input = ui.textarea(
                            label="開発用手動Observation（実PKB取得ではない）",
                            placeholder="自動read対象外の開発検証にだけ使用",
                        ).classes("w-full")
    
                        async def continue_guided():
                            if state["guided_busy"]:
                                return
                            if not str(observation_input.value or "").strip():
                                ui.notify("試験用Observationを入力してください", type="warning")
                                return
                            stop_event = begin_guided_run(
                                len(session["turns"]) + 1,
                                task_id_value=str(session.get("task_id") or ""),
                                request_text=str(session.get("user_raw") or ""),
                            )
                            guided_button.disable()
                            guided_result_panel.refresh()
                            try:
                                state["guided_session"] = await continue_with_observation_async(
                                    session,
                                    observation_input.value,
                                    timeout=guided_timeout_seconds(session),
                                    caller=guided_panel_caller,
                                    stop_requested=stop_event.is_set,
                                    on_turn_start=note_guided_turn,
                                )
                            except Exception as exc:
                                ui.notify(type(exc).__name__ + ": " + str(exc)[:160], type="negative")
                            finally:
                                state["guided_busy"] = False
                                state["guided_stop_event"] = None
                                guided_button.enable()
                                guided_result_panel.refresh()
    
                        ui.button(
                            "手動Observationで継続（開発用）",
                            icon="refresh", on_click=continue_guided,
                        ).props("outline")
                    elif (
                        not state.get("guided_history_read_only")
                        and session["status"] == "waiting_user"
                    ):
                        with ui.card().classes(
                            "w-full border border-orange-300 bg-orange-50 shadow-none"
                        ):
                            ui.label("RITSUKOからの確認").classes(
                                "font-bold text-orange-900"
                            )
                            ui.label(
                                "RITSUKOが本人への確認を必要とする状態です。"
                            ).classes("text-sm text-orange-900")
                            user_resume = saved_task.get("user_resume")
                            if isinstance(user_resume, dict) and user_resume.get("status") in {
                                "processing", "retry_required"
                            }:
                                ui.label(
                                    "前回の本人回答Turnは "
                                    + str(user_resume.get("status"))
                                    + " です。安全のため、前回と同じ回答内容を再入力して再試行してください。"
                                ).classes("text-xs text-orange-800")
                            question = session.get("user_question") or (
                                (session.get("detail") or {}).get("question_for_user")
                            )
                            if question:
                                ui.label("質問: " + question).classes("text-sm")
                            clarification_input = ui.textarea(
                                label="このTaskへ回答",
                                placeholder="回答や追加説明を自然な言葉で入力",
                            ).props("rows=2 outlined dense").classes("w-full")
    
                            async def continue_with_user():
                                if state["guided_busy"]:
                                    return
                                if not str(clarification_input.value or "").strip():
                                    ui.notify("追加説明を入力してください", type="warning")
                                    return
                                stop_event = begin_guided_run(
                                    len(session["turns"]) + 1,
                                    task_id_value=str(session.get("task_id") or ""),
                                    request_text=str(session.get("user_raw") or ""),
                                )
                                guided_button.disable()
                                guided_result_panel.refresh()
                                try:
                                    state["guided_session"] = await resume_user_answer(
                                        UUID(session["task_id"]),
                                        clarification_input.value,
                                        timeout=guided_timeout_seconds(session),
                                        claim_user_resume_record=_claim_magi_user_resume_record,
                                        persist_session_record=_persist_magi_core_session_record,
                                        abort_user_resume_record=_abort_magi_user_resume_record,
                                        fail_task_record=_fail_magi_core_task_record,
                                        stop_requested=stop_event.is_set,
                                        on_turn_start=note_guided_turn,
                                        user_continuation=guided_user_continuation,
                                    )
                                except Exception as exc:
                                    ui.notify(
                                        type(exc).__name__ + ": " + str(exc)[:160],
                                        type="negative",
                                    )
                                finally:
                                    state["guided_busy"] = False
                                    state["guided_stop_event"] = None
                                    guided_button.enable()
                                    try:
                                        saved_trace = _portal("load_core_task_trace")(
                                            UUID(session["task_id"])
                                        )
                                        if (
                                            (state.get("result") or {}).get("task_id")
                                            == session["task_id"]
                                        ):
                                            state["result"] = core_task_selection_result(
                                                saved_trace["task"]
                                            )
                                            state["trace"] = saved_trace
                                    except Exception:
                                        pass
                                    guided_result_panel.refresh()
                                    open_tasks_panel.refresh()
                                    completed_tasks_panel.refresh()
                                    core_result.refresh()
                                    trace_panel.refresh()
                                    resume_panel.refresh()
    
                            ui.button(
                                "このTaskへ回答して続行",
                                icon="chat",
                                on_click=continue_with_user,
                            ).props("outline")
                    elif (
                        not state.get("guided_history_read_only")
                        and session["status"] == "proposal_ready"
                    ):
                        detail = session.get("detail") or {}
                        if detail.get("state") == "KNOWLEDGE_CANDIDATE":
                            with ui.card().classes(
                                "w-full border border-blue-300 bg-blue-50 shadow-none"
                            ):
                                ui.label("RITSUKOからの確認").classes(
                                    "font-bold text-blue-900"
                                )
                                reviewable_proposal = (
                                    reviewable_user_knowledge_proposal(session)
                                )
                                answer = str(
                                    (
                                        reviewable_proposal or {}
                                    ).get("answer")
                                    or detail.get("answer_candidate")
                                    or ""
                                ).strip()
                                knowledge = str(
                                    (
                                        reviewable_proposal or {}
                                    ).get("knowledge_candidate")
                                    or detail.get("knowledge_candidate")
                                    or ""
                                ).strip()
                                ui.label(
                                    "本人回答を根拠に、回答候補と記憶候補ができています。"
                                ).classes("font-bold text-orange-900")
                                if answer:
                                    ui.label("回答候補: " + answer).classes("text-sm")
                                if knowledge:
                                    ui.label("記憶候補: " + knowledge).classes(
                                        "text-sm font-medium"
                                    )
                                if (
                                    isinstance(reviewable_proposal, dict)
                                    and reviewable_proposal.get("answer_source")
                                    == "knowledge_candidate_fallback"
                                ):
                                    ui.label(
                                        "回答候補が未生成だったため、本人回答でGrounding済みの"
                                        "記憶候補を回答文として使用します。"
                                    ).classes("text-xs text-blue-800")
                                if reviewable_proposal is None:
                                    ui.label(
                                        "本人回答とのGroundingを検証できないため、"
                                        "このProposalは完了操作できません。"
                                    ).classes("text-xs text-red-700 font-medium")
                                ui.label(
                                    "「回答だけで完了」はPKBへ新規記憶を書きません。"
                                    "「記憶にも反映」は表示中の記憶候補を本人が確認した内容として"
                                    "Memory IntakeのGrounding / WriteDecisionへ渡し、"
                                    "その結果をObservationとしてMAGIへ再評価してから"
                                    "RITSUKOが完了判定します。"
                                    "MAGIが直接PKBを書き換えることはありません。"
                                ).classes("text-xs text-grey-7")
                                active_review = saved_task.get("proposal_review")
                                prepared_intake = saved_task.get(
                                    "proposal_memory_intake"
                                )
                                active_review_status = (
                                    active_review.get("status")
                                    if isinstance(active_review, dict)
                                    else None
                                )
                                active_review_decision = (
                                    active_review.get("decision")
                                    if isinstance(active_review, dict)
                                    else None
                                )
                                if active_review_status in {
                                    "processing", "retry_required"
                                }:
                                    ui.label(
                                        "前回のProposal review: "
                                        + str(active_review_decision or "-")
                                        + " / "
                                        + str(active_review_status)
                                        + "。同じTaskで再評価を再試行できます。"
                                    ).classes("text-xs text-orange-800")
                                if isinstance(prepared_intake, dict):
                                    ui.label(
                                        "Memory Intakeは開始済みです。"
                                        "同じinput_idで再送・再評価し、"
                                        "回答だけ経路へは戻しません。"
                                    ).classes("text-xs text-blue-800")
    
                                async def load_after_proposal_review() -> None:
                                    saved_trace = await run.io_bound(
                                        _portal("load_core_task_trace"),
                                        UUID(session["task_id"]),
                                    )
                                    state["trace"] = saved_trace
                                    state["result"] = core_task_selection_result(
                                        saved_trace["task"]
                                    )
                                    state["guided_session"] = (
                                        saved_trace["task"].get("magi_session")
                                        or session
                                    )
                                    state["guided_history_read_only"] = True
    
                                async def execute_proposal_review(
                                    decision: str,
                                ) -> None:
                                    if state["guided_busy"]:
                                        return
                                    notification_client = context.client
                                    notification_message = None
                                    notification_type = None
                                    stop_event = begin_guided_run(
                                        len(session["turns"]) + 1,
                                        task_id_value=str(session.get("task_id") or ""),
                                        request_text=str(session.get("user_raw") or ""),
                                    )
                                    answer_only_button.disable()
                                    remember_button.disable()
                                    guided_button.disable()
                                    guided_result_panel.refresh()
                                    memory_result = None
                                    try:
                                        if decision == "remember":
                                            intake = await run.io_bound(
                                                _prepare_magi_memory_intake_record,
                                                UUID(session["task_id"]),
                                            )
                                            memory_result = await run.io_bound(
                                                register_memory_intake,
                                                intake,
                                            )
                                        state["guided_session"] = (
                                            await review_magi_proposal(
                                                UUID(session["task_id"]),
                                                decision,
                                                timeout=guided_timeout_seconds(
                                                    session
                                                ),
                                                claim_proposal_review_record=(
                                                    _claim_magi_proposal_review_record
                                                ),
                                                finalize_proposal_review_record=(
                                                    _finalize_magi_proposal_review_record
                                                ),
                                                abort_proposal_review_record=(
                                                    _abort_magi_proposal_review_record
                                                ),
                                                memory_result=memory_result,
                                                stop_requested=stop_event.is_set,
                                                on_turn_start=note_guided_turn,
                                                review_continuation=guided_review_continuation,
                                            )
                                        )
                                        await load_after_proposal_review()
                                        if decision == "remember":
                                            decisions = [
                                                str(item.get("decision") or "-")
                                                for item in (
                                                    (memory_result or {}).get(
                                                        "candidates"
                                                    )
                                                    or []
                                                )
                                                if isinstance(item, dict)
                                            ]
                                            notification_message = (
                                                "Memory Intake結果をMAGIへ再評価し、"
                                                "RITSUKOがTaskを完了しました"
                                                + (
                                                    " (" + ", ".join(decisions) + ")"
                                                    if decisions
                                                    else ""
                                                )
                                            )
                                        else:
                                            notification_message = (
                                                "回答レビューをMAGIへ再評価し、"
                                                "RITSUKOがTaskを完了しました"
                                            )
                                        notification_type = "positive"
                                    except Exception as exc:
                                        try:
                                            saved_trace = await run.io_bound(
                                                _portal("load_core_task_trace"),
                                                UUID(session["task_id"]),
                                            )
                                            state["trace"] = saved_trace
                                            state["result"] = (
                                                core_task_selection_result(
                                                    saved_trace["task"]
                                                )
                                            )
                                        except Exception:
                                            pass
                                        notification_message = (
                                            type(exc).__name__
                                            + ": "
                                            + str(exc)[:180]
                                        )
                                        notification_type = "negative"
                                    finally:
                                        state["guided_busy"] = False
                                        state["guided_stop_event"] = None
                                        guided_button.enable()
                                        guided_result_panel.refresh()
                                        open_tasks_panel.refresh()
                                        completed_tasks_panel.refresh()
                                        core_result.refresh()
                                        trace_panel.refresh()
                                        resume_panel.refresh()
                                    if notification_message and notification_type:
                                        _notify_client(
                                            notification_client,
                                            notification_message,
                                            type=notification_type,
                                        )
    
                                async def complete_answer_only():
                                    await execute_proposal_review("answer_only")
    
                                async def remember_and_complete():
                                    await execute_proposal_review("remember")
    
                                with ui.row().classes("gap-2 flex-wrap"):
                                    answer_label = (
                                        "回答レビューを再試行"
                                        if (
                                            active_review_decision == "answer_only"
                                            and active_review_status
                                            in {"processing", "retry_required"}
                                        )
                                        else "回答だけで完了"
                                    )
                                    remember_label = (
                                        "記憶結果の再評価を再試行"
                                        if (
                                            isinstance(prepared_intake, dict)
                                            or active_review_decision == "remember"
                                        )
                                        else "記憶にも反映して完了"
                                    )
                                    answer_only_button = ui.button(
                                        answer_label,
                                        icon="done",
                                        color="green",
                                        on_click=complete_answer_only,
                                    )
                                    remember_button = ui.button(
                                        remember_label,
                                        icon="save",
                                        color="blue",
                                        on_click=remember_and_complete,
                                    )
                                    if reviewable_proposal is None:
                                        answer_only_button.disable()
                                        remember_button.disable()
                                    if isinstance(prepared_intake, dict) or (
                                        active_review_status == "processing"
                                        and active_review_decision == "remember"
                                    ):
                                        answer_only_button.disable()
                                    if (
                                        active_review_status == "processing"
                                        and active_review_decision == "answer_only"
                                    ):
                                        remember_button.disable()
                        else:
                            ui.label(
                                "このProposal種別の実行経路はまだ接続していません。"
                            ).classes("text-sm text-orange-900")
                    with ui.expansion(
                        "検証情報 / 実行経緯",
                        value=_CORE_UI_OPEN["verification"],
                        on_value_change=remember_core_expansion("verification"),
                        icon="fact_check",
                    ).classes(
                        "w-full border border-purple-200 bg-purple-50"
                        + _block_visibility_class("core", "verification")
                    ):
                        ui.label(
                            "検証では古いTurnから新しいTurnへ順番に追います。"
                        ).classes("text-xs text-grey-7")
                        render_guided_verification(session)
    
    
                async def start_guided():
                    if state["guided_busy"]:
                        return
                    request_text = str(guided_input.value or "").strip()
                    if not request_text:
                        ui.notify("入力文を指定してください", type="warning")
                        return
                    specs = save_guided_assignments(notify=False)
                    if not specs:
                        return
                    task_uuid = uuid4()
                    stop_event = begin_guided_run(
                        1,
                        task_id_value=str(task_uuid),
                        request_text=request_text,
                    )
                    state["guided_session"] = None
                    state["guided_history_read_only"] = False
                    state["result"] = None
                    state["trace"] = None
                    state["trace_error"] = None
                    guided_button.disable()
                    guided_result_panel.refresh()
                    core_result.refresh()
                    trace_panel.refresh()
                    try:
                        state["guided_session"] = await run_observation_loop(
                            request_text,
                            member_specs=specs,
                            timeout=guided_timeout_seconds(),
                            create_task_record=_create_magi_core_task_record,
                            execute_source_request=_execute_magi_source_request,
                            record_source_read_record=_record_magi_source_read_record,
                            persist_session_record=_persist_magi_core_session_record,
                            fail_task_record=_fail_magi_core_task_record,
                            stop_requested=stop_event.is_set,
                            on_turn_start=note_guided_turn,
                            task_id=task_uuid,
                            dialogue_starter=guided_dialogue_starter,
                            observation_continuation=guided_observation_continuation,
                        )
                        guided_task_id = str(
                            (state.get("guided_session") or {}).get("task_id") or ""
                        ).strip()
                        if guided_task_id:
                            try:
                                saved_trace = await run.io_bound(
                                    _portal("load_core_task_trace"),
                                    UUID(guided_task_id),
                                )
                                state["trace"] = saved_trace
                                state["trace_error"] = None
                                state["result"] = core_task_selection_result(
                                    saved_trace["task"]
                                )
                            except Exception as trace_exc:
                                state["trace"] = None
                                state["trace_error"] = (
                                    type(trace_exc).__name__
                                    + ": "
                                    + str(trace_exc)[:160]
                                )
                    except Exception as exc:
                        ui.notify(type(exc).__name__ + ": " + str(exc)[:160], type="negative")
                    finally:
                        state["guided_busy"] = False
                        state["guided_stop_event"] = None
                        guided_button.enable()
                        guided_result_panel.refresh()
                        core_result.refresh()
                        trace_panel.refresh()
                        resume_panel.refresh()
                        screen_log_panel.refresh()
                        open_tasks_panel.refresh()
                        completed_tasks_panel.refresh()
    
                with new_request_slot:
                    guided_button = ui.button(
                        "新しい依頼を開始",
                        icon="play_arrow",
                        color="teal",
                        on_click=start_guided,
                    )
                with current_task_slot:
                    guided_result_panel()
    
                def refresh_guided_progress() -> None:
                    if state.get("guided_busy"):
                        guided_result_panel.refresh()
    
                ui.timer(1.0, refresh_guided_progress)
    
            @ui.refreshable
            def trace_panel():
                result = state["result"] or {}
                task_id = result.get("task_id")
                if not task_id:
                    return
                trace = state["trace"]
                if not trace:
                    with ui.expansion(
                        "Task詳細・実行記録",
                        value=_CORE_UI_OPEN["trace"],
                        on_value_change=remember_core_expansion("trace"),
                    ).classes(
                        "w-full border border-red-200 bg-red-50"
                        + _block_visibility_class("core", "trace")
                    ):
                        ui.label("Traceを取得できません: " + str(state["trace_error"] or "未取得")).classes(
                            "text-red-700"
                        )
                    return
    
                task = trace["task"]
                with ui.expansion(
                    "Task詳細・実行記録",
                    value=_CORE_UI_OPEN["trace"],
                    on_value_change=remember_core_expansion("trace"),
                ).classes(
                    "w-full border-2 border-slate-300 bg-slate-50"
                    + _block_visibility_class("core", "trace")
                ):
                    ui.label(
                        "この依頼Taskに属するDB上のAction / Result / Sourceを表示します。"
                    ).classes("text-sm text-grey-7")
                    with ui.grid(columns=2).classes("w-full gap-2"):
                        ui.label("Task ID")
                        ui.label(task["id"]).classes("font-mono text-xs")
                        ui.label("Status / Revision")
                        ui.label(f"{task['status']} / {task['revision']}")
                        ui.label("Phase")
                        ui.label(str(task.get("phase") or "-"))
                        ui.label("能力")
                        ui.label(str(task.get("selected_capability") or "-"))
                        ui.label("元依頼")
                        ui.label(task["request"])
                        if task.get("effective_request"):
                            ui.label("実効依頼")
                            ui.label(task["effective_request"])
                        if task.get("user_replies"):
                            ui.label("追加回答")
                            ui.label(" / ".join(task["user_replies"]))
                        ui.label("更新時刻")
                        ui.label(str(task["updated_at"]))
    
                    actions = trace["actions"]
                    ui.separator()
                    ui.label(f"Action / Result: {len(actions)}件").classes("font-bold")
                    if not actions:
                        ui.label(
                            "まだActionはありません。追加確認待ちTaskでは正常です。"
                        ).classes("text-sm")
                    for index, item in enumerate(actions, start=1):
                        with ui.card().classes("w-full bg-white"):
                            ui.label(
                                f"{index}. {item['tool']}.{item['operation']} "
                                f"[{item['risk']}] → {item['action_status']}"
                            ).classes("font-medium")
                            if item.get("outcome"):
                                ui.label(
                                    f"Result: {item['outcome']} / "
                                    f"{item.get('summary') or ''}"
                                ).classes("text-sm")
                            if item.get("source_uri"):
                                ui.label(
                                    "Source: " + item["source_uri"]
                                ).classes("font-mono text-xs text-grey-7")
                            if item.get("verified_by"):
                                ui.label(
                                    "Verified: "
                                    + str(item["verified_by"])
                                    + " / "
                                    + str(item.get("verified_at") or "")
                                ).classes("text-xs text-grey-7")
    
            trace_panel()

            @ui.refreshable
            def screen_log_panel():
                # Cross-Task history belongs to the right drawer and /core/history.
                return

            with ui.expansion(
                "旧 Protocol v1 全項目一括分析（比較用）",
                value=_CORE_UI_OPEN["legacy_protocol"],
                on_value_change=remember_core_expansion("legacy_protocol"),
                icon="history",
            ).classes(
                "w-full border"
                + _block_visibility_class("core", "legacy_protocol")
            ):
                ui.label(
                    "前の通信方式は比較用に保存。今回の分類対話には使いません。"
                ).classes("text-xs text-grey-7")
                with ui.card().classes("w-full border-2 border-indigo-300 bg-indigo-50"):
                    ui.label("RITSUKO → MAGI Protocol v1 / Cycle 1 試験").classes(
                        "text-lg font-bold text-indigo-900"
                    )
                    ui.label(
                        "RITSUKOが依頼Envelopeを作成 → MELCHIOR slotのLLMへ送信 → "
                        "RITSUKOがanalysis_resultを受信・Schema検証します。"
                    ).classes("text-sm")
                    ui.label(
                        "旧deterministic routerへのfallback、PKB/Web read、Task DB更新は行いません。"
                    ).classes("text-xs text-orange-800")
                    protocol_input = ui.textarea(
                        label="ユーザー原文",
                        value="メインPCのGPUの種類は？",
                    ).classes("w-full")
                    ui.label("MAGI member configuration").classes("font-medium text-indigo-900")
                    ui.label(
                        "3つのmemberと実モデルは独立です。現在のCycle 1試験で実行するのは"
                        "MELCHIORのみ。無効な2枠のモデルはロードしません。"
                    ).classes("text-xs text-grey-7")
                    with ui.row().classes("w-full items-stretch gap-3 flex-wrap"):
                        with ui.card().classes("min-w-64 grow border border-indigo-300 bg-white"):
                            ui.label("MELCHIOR").classes("font-bold")
                            ui.switch("有効（Cycle 1）", value=True).disable()
                            ui.label("Provider: Ollama").classes("text-xs text-grey-7")
                            protocol_model_select = ui.select(
                                options=installed_magi_models,
                                value=default_magi_model,
                                label="MELCHIOR / model",
                            ).classes("w-full")
                        for inactive_member in ("BALTHASAR", "CASPER"):
                            with ui.card().classes("min-w-64 grow border border-grey-300 bg-white"):
                                ui.label(inactive_member).classes("font-bold")
                                ui.switch("無効（multi-member未実装）", value=False).disable()
                                ui.label("Provider: Ollama（予定）").classes("text-xs text-grey-7")
                                inactive_model_select = ui.select(
                                    options=installed_magi_models,
                                    value=None,
                                    label=f"{inactive_member} / model",
                                ).classes("w-full")
                                inactive_model_select.disable()
                    protocol_timeout_select = ui.select(
                        options=list(CORE_ADVISOR_TIMEOUT_OPTIONS),
                        value=int(state.get("advisor_timeout") or 60),
                        label="Timeout (秒)［MELCHIORのみ］",
                    ).classes("min-w-40")
    
                    @ui.refreshable
                    def protocol_result_panel():
                        result=state.get("protocol_result")
                        if state.get("protocol_busy"):
                            ui.label("RITSUKOがMAGI依頼を実行中...").classes(
                                "text-indigo-800 font-bold"
                            )
                            return
                        if not result:
                            ui.label("まだ実行していません。Cycle 1のLLM応答だけを観測します。").classes(
                                "text-sm text-grey-7"
                            )
                            return
                        status=result.get("status")
                        color="green" if status=="ok" else ("orange" if status=="invalid" else "red")
                        ui.badge("Protocol v1: " + str(status), color=color)
                        export_text=json.dumps(
                            protocol_probe_export(result),ensure_ascii=False,indent=2,default=str
                        )
                        ui.button(
                            "試験結果を一括コピー",icon="content_copy",
                            on_click=lambda value=export_text: copy_protocol_json(value,"試験結果"),
                        ).props("outline dense").classes("self-start")
                        assignment=result.get("assignment") or {}
                        ui.label(
                            "member=" + str(assignment.get("member") or "-")
                            + " / provider=" + str(assignment.get("provider") or "-")
                            + " / model=" + str(assignment.get("model") or "-")
                        ).classes("font-mono text-sm")
                        ui.label(
                            "legacy_router_used=" + str(result.get("legacy_router_used"))
                            + " / pkb_read_executed=" + str(result.get("pkb_read_executed"))
                        ).classes("font-mono text-xs text-grey-7")
                        errors=result.get("validation_errors") or []
                        if errors:
                            ui.label("Schema / 通信エラー: " + " | ".join(errors)).classes("text-red-700")
                        diagnostic=result.get("diagnostic") or {}
                        if diagnostic:
                            ui.label("LLM応答診断（返答本文・Thinking本文は非表示）").classes(
                                "font-bold text-sm"
                            )
                            diagnostic_text=json.dumps({
                                "status":status,
                                "assignment":assignment,
                                "validation_errors":errors,
                                "diagnostic":diagnostic,
                            },ensure_ascii=False,indent=2)
                            ui.button(
                                "診断情報をコピー",icon="content_copy",
                                on_click=lambda value=diagnostic_text: copy_protocol_json(value,"診断情報"),
                            ).props("outline dense")
                            ui.code(diagnostic_text,language="json").classes("w-full")
                        if result.get("response") is not None:
                            ui.label("MAGI analysis_result").classes("font-bold")
                            response_text=json.dumps(result["response"],ensure_ascii=False,indent=2)
                            ui.button(
                                "analysis_resultをコピー",icon="content_copy",
                                on_click=lambda value=response_text: copy_protocol_json(value,"analysis_result"),
                            ).props("outline dense")
                            ui.code(response_text,language="json").classes("w-full")
                        with ui.expansion("RITSUKOが作成した依頼Envelope",icon="data_object").classes(
                            "w-full border"
                        ):
                            envelope_text=json.dumps(
                                result.get("request_envelope") or {},ensure_ascii=False,indent=2
                            )
                            ui.button(
                                "Envelopeをコピー",icon="content_copy",
                                on_click=lambda value=envelope_text: copy_protocol_json(value,"Envelope"),
                            ).props("outline dense")
                            ui.code(envelope_text,language="json").classes("w-full")
    
                    async def submit_protocol_v1():
                        if state.get("protocol_busy"):
                            return
                        selected_model=str(protocol_model_select.value or "").strip()
                        if not selected_model:
                            ui.notify("Ollama chat modelを選択してください",type="negative")
                            return
                        state["protocol_busy"]=True
                        state["protocol_result"]=None
                        protocol_run_button.disable()
                        protocol_result_panel.refresh()
                        try:
                            state["protocol_result"]=await run.io_bound(
                                run_ritsuko_magi_cycle1_probe,
                                protocol_input.value or "",
                                model=selected_model,
                                timeout=float(protocol_timeout_select.value or 60),
                            )
                        except Exception as exc:
                            state["protocol_result"]={
                                "status":"error","response":None,
                                "validation_errors":[type(exc).__name__ + ": " + str(exc)[:200]],
                                "legacy_router_used":False,"pkb_read_executed":False,
                            }
                        finally:
                            state["protocol_busy"]=False
                            protocol_run_button.enable()
                            protocol_result_panel.refresh()
    
                    protocol_run_button=ui.button(
                        "MELCHIORへ分析依頼",icon="psychology",color="indigo",
                        on_click=submit_protocol_v1,
                    )
                    protocol_result_panel()
    
            ui.separator()
            with ui.expansion(
                "旧MAGI v0（比較・確認用）",
                value=_CORE_UI_OPEN["legacy_protocol"],
                on_value_change=remember_core_expansion("legacy_protocol"),
                icon="history",
            ).classes(
                "w-full border"
                + _block_visibility_class("core", "legacy_protocol")
            ):
                ui.label(
                    "旧MAGI v0のAdvisor設定・OODA・旧処理フローです。"
                    "現在のRITSUKO ⇄ MAGI Observation Loopには使用しません。"
                ).classes("text-sm text-grey-7")
    
                try:
                    installed_advisor_models = list_advisor_models()
                except Exception:
                    installed_advisor_models = []
    
                saved_advisor_model = state.get("advisor_model")
                if saved_advisor_model not in installed_advisor_models:
                    try:
                        saved_advisor_model = choose_advisor_model(installed_advisor_models)
                    except Exception:
                        saved_advisor_model = None
                    state["advisor_model"] = saved_advisor_model
    
                with ui.row().classes("w-full items-end gap-2 flex-wrap"):
                    advisor_model_select = ui.select(
                        options=installed_advisor_models,
                        value=saved_advisor_model,
                        label="Legacy CASPER Advisor Model",
                    ).classes("min-w-64")
                    advisor_timeout_select = ui.select(
                        options=list(CORE_ADVISOR_TIMEOUT_OPTIONS),
                        value=int(state.get("advisor_timeout") or 60),
                        label="Advisor Timeout (秒)",
                    ).classes("min-w-40")
                    ui.label(
                        "旧MAGI v0のCASPER Advisor用。Protocol v1 MELCHIORとは別設定です。"
                    ).classes("text-xs text-grey-7")
    
                    def save_advisor_model():
                        selected = str(advisor_model_select.value or "").strip() or None
                        if selected and selected not in advisor_model_select.options:
                            ui.notify("インストール済みモデルを選択してください", type="negative")
                            return
                        timeout_seconds = int(advisor_timeout_select.value or 60)
                        if timeout_seconds not in CORE_ADVISOR_TIMEOUT_OPTIONS:
                            ui.notify("Timeoutは一覧から選択してください", type="negative")
                            return
                        saved_model, saved_timeout = _save_core_advisor_settings(
                            selected, timeout_seconds
                        )
                        state["advisor_model"] = saved_model
                        state["advisor_timeout"] = saved_timeout
                        ui.notify(
                            "Advisor設定を保存しました: "
                            + str(state["advisor_model"] or "自動")
                            + f" / {state['advisor_timeout']}秒",
                            type="positive",
                        )
    
                    def refresh_advisor_models():
                        try:
                            available = list_advisor_models()
                        except Exception as exc:
                            ui.notify("Ollamaモデル一覧を取得できません: " + str(exc)[:180], type="negative")
                            return
                        previous = advisor_model_select.value
                        advisor_model_select.options = available
                        if previous in available:
                            advisor_model_select.value = previous
                        else:
                            try:
                                advisor_model_select.value = choose_advisor_model(available)
                            except Exception:
                                advisor_model_select.value = None
                        advisor_model_select.update()
                        ui.notify(f"Chat model {len(available)}件を取得しました", type="positive")
    
                    ui.button(
                        "保存",
                        icon="save",
                        color="indigo",
                        on_click=save_advisor_model,
                    ).props("dense")
                    ui.button(
                        "モデル一覧更新",
                        icon="refresh",
                        on_click=refresh_advisor_models,
                    ).props("flat dense")
    
                @ui.refreshable
                def ooda_bar():
                    display = current_ooda()
                    with ui.column().classes("w-full gap-1").props('role=status aria-live=polite'):
                        with ui.row().classes("w-full items-center gap-2 flex-wrap"):
                            ui.label("OODA").classes("font-bold")
                            for index, (key, name, note) in enumerate(OODA_PHASES):
                                if index:
                                    ui.label("→").classes("text-grey-6").props('aria-hidden=true')
                                active = display.phase == key and not display.terminal
                                label = f"{name}（{note}）" + (" · 現在" if active else "")
                                badge = ui.badge(label, color="blue" if active else "grey-3",
                                                 text_color="white" if active else "grey-8")
                                if active:
                                    badge.classes("font-bold").props('aria-current=step')
                        if display.terminal:
                            ui.label(f"最終状態: {display.terminal}（OODA外）").classes("font-bold")
                        elif display.phase is None:
                            ui.label(display.reason).classes("text-sm text-grey-7")
                        ui.label("既存状態からの表示用推定（途中段階のライブ配信ではありません）").classes(
                            "text-xs text-grey-6"
                        )
    
                ooda_bar()
                ui.label("最小縦断: 依頼 → Task → 能力選択 → 読取 → Result / 追加質問").classes(
                    "text-sm text-grey-7"
                )
                ui.label(
                    "PKB・家計・明示的なWeb調査を読み取り専用で扱います。"
                    "曖昧依頼からの自動実行は限定PKB readのみです。"
                ).classes("text-sm text-orange-700")
    
                def select_saved_task(item: dict):
                    if state["busy"] or state["resume_busy"]:
                        return
                    state["result"] = core_task_selection_result(item)
                    load_current_trace()
                    saved_task = (state.get("trace") or {}).get("task") or {}
                    if saved_task.get("core_slice") == "ritsuko_magi_observation_v1":
                        saved_session = saved_task.get("magi_session")
                        if isinstance(saved_session, dict):
                            state["guided_session"] = saved_session
                            state["guided_task_id"] = str(
                                saved_session.get("task_id") or saved_task.get("id") or ""
                            )
                            state["guided_request"] = str(
                                saved_session.get("user_raw") or saved_task.get("request") or ""
                            )
                            state["guided_member_progress"] = {}
                            if isinstance(saved_session.get("member_specs"), list):
                                state["magi_member_specs"] = saved_session["member_specs"]
                            state["guided_history_read_only"] = (
                                saved_task.get("status") == "completed"
                            )
                        else:
                            state["guided_session"] = None
                            state["guided_history_read_only"] = False
                    else:
                        state["guided_session"] = None
                        state["guided_history_read_only"] = False
                    ooda_bar.refresh()
                    core_result.refresh()
                    resume_panel.refresh()
                    trace_panel.refresh()
                    guided_result_panel.refresh()
    
                @ui.refreshable
                def completed_tasks_panel():
                    try:
                        rows, has_more = load_core_task_window(_portal("load_completed_core_tasks"), list_limits["completed_limit"])
                    except Exception as exc:
                        with ui.card().classes("w-full border border-red-200 bg-red-50"):
                            ui.label("完了済みTaskを取得できません: " + str(exc)).classes(
                                "text-red-700"
                            )
                        return
    
                    ui.separator()
                    ui.label("完了済み").classes("text-base font-bold")
                    ui.label(
                        "直近の完了Taskを閲覧専用で開けます。再開・再実行は行いません。"
                    ).classes("text-xs text-grey-7")
                    if not rows:
                        ui.label("完了済みTaskはありません。").classes("text-sm text-grey-7")
                        return
                    for item in rows:
                        with ui.card().classes("w-full p-2 gap-1 bg-green-50"):
                            with ui.row().classes("w-full items-center gap-2 no-wrap"):
                                ui.badge("completed", color="green").classes("shrink-0")
                                ui.label(item["request"]).classes(
                                    "font-medium text-sm grow overflow-hidden"
                                )
                            ui.label(
                                f"rev={item['revision']} / {item.get('phase') or '-'} / "
                                f"{item.get('selected_capability') or '-'}"
                            ).classes("font-mono text-xs text-grey-6")
                            ui.label(
                                f"Action={item['action_count']} / Result={item['result_count']}"
                            ).classes("text-xs text-grey-6")
                            ui.button(
                                "開く",
                                icon="visibility",
                                color="green",
                                on_click=lambda item=item: select_saved_task(item),
                            ).props("flat dense").classes("self-start")
    
                    if has_more:
                        ui.button("さらに読み込む", on_click=lambda: more_tasks("completed_limit", completed_tasks_panel)).props("flat dense")
    
                @ui.refreshable
                def open_tasks_panel():
                    try:
                        rows, has_more = load_core_task_window(_portal("load_open_core_tasks"), list_limits["open_limit"])
                    except Exception as exc:
                        with ui.card().classes("w-full border border-red-200 bg-red-50"):
                            ui.label("未完了Taskを取得できません: " + str(exc)).classes(
                                "text-red-700"
                            )
                        return
    
                    ui.label("進行中・確認待ち").classes("text-base font-bold")
                    ui.label(
                        "未完了Taskを選ぶと中央に開きます。F5・サーバー再起動後もDBから復元します。"
                    ).classes("text-xs text-grey-7")
                    if not rows:
                        ui.label("未完了Taskはありません。").classes("text-sm text-grey-7")
                        return
                    for item in rows:
                        status_color = {
                            "waiting_external": "orange",
                            "running": "blue",
                            "paused": "grey",
                        }.get(item["status"], "grey")
                        with ui.card().classes("w-full p-2 gap-1"):
                            with ui.row().classes("w-full items-center gap-2 no-wrap"):
                                ui.badge(item["status"], color=status_color).classes("shrink-0")
                                ui.label(item["request"]).classes(
                                    "font-medium text-sm grow overflow-hidden"
                                )
                            ui.label(
                                f"rev={item['revision']} / {item.get('phase') or '-'}"
                            ).classes("font-mono text-xs text-grey-6")
                            ui.button(
                                "開く",
                                icon="open_in_new",
                                color="orange",
                                on_click=lambda item=item: select_saved_task(item),
                            ).props("flat dense").classes("self-start")
    
                    if has_more:
                        ui.button("さらに読み込む", on_click=lambda: more_tasks("open_limit", open_tasks_panel)).props("flat dense")
    
                def sync_task_preferences():
                    changed = False
                    for key in list_defaults:
                        value = _UI_PREFERENCES["core"][key]
                        if value != list_defaults[key]:
                            list_defaults[key] = value
                            list_limits[key] = value
                            changed = True
                    if changed:
                        open_tasks_panel.refresh()
                        completed_tasks_panel.refresh()
    
                with task_drawer:
                    with ui.column().classes("w-full gap-2 no-wrap"):
                        ui.label("既存Task").classes("text-lg font-bold shrink-0")
                        ui.label(
                            "RITSUKOのTaskは経路に関係なくここから確認できます。"
                        ).classes("text-xs text-grey-7")
                        ui.link("Task履歴を見る", "/core/history").classes("shrink-0")
                        with ui.column().classes("w-full no-wrap"):
                            open_tasks_panel()
                        with ui.column().classes("w-full no-wrap"):
                            completed_tasks_panel()
                    ui.timer(1.0, sync_task_preferences)
    
                with ui.card().classes("w-full border-2 border-blue-grey-300 bg-blue-grey-1"):
                    ui.label("依頼").classes("text-lg font-bold")
                    ui.label(
                        "例: メインPCのGPUの現在のドライバーを調べて / "
                        "メインPCの構成を確認して / GPU1のドライバー更新履歴を見て"
                    ).classes("text-sm")
                    request_input = ui.textarea(
                        label="Secretary Coreへ依頼",
                        placeholder="対象と確認したい内容を自然言語で入力",
                    ).classes("w-full")
    
                    @ui.refreshable
                    def core_result():
                        result = state["result"]
                        if result or state["busy"] or state["resume_busy"]:
                            display = current_ooda()
                            suffix = "（完了直前の表示用段階）" if display.terminal == "completed" else ""
                            ui.label(f"OODA: {display.label}{suffix}").classes("font-medium")
                            ui.label("理由: " + display.reason).classes("text-sm")
                        advisor = current_advisor_shadow()
                        if advisor:
                            with ui.card().classes(
                                "w-full border border-indigo-200 bg-indigo-50"
                            ):
                                ui.label("MAGI v0 · Cooperative Synthesis").classes(
                                    "font-bold text-indigo-900"
                                )
                                ui.label(
                                    "MELCHIORのGuardとCASPERの前進案をCoreが統合します。"
                                    "曖昧依頼では、Synthesisが選んだ限定的なPKB readだけを自動実行できます。"
                                ).classes("text-xs text-grey-7")
                                presentation = core_magi_presentation(result or {}, advisor, state.get("trace") or {})
                                for cycle in presentation["cycles"]:
                                    with ui.card().classes("w-full bg-white gap-1"):
                                        ui.label(f"Cycle {cycle['cycle']}").classes("font-bold")
                                        for key, label in (("melchior", "MELCHIOR"), ("casper", "CASPER"),
                                                           ("synthesis", "Synthesis（中間提案）"),
                                                           ("action", "Action"), ("result", "Result")):
                                            if cycle.get(key) is not None:
                                                note = "（初回Guardを再利用）" if key == "melchior" and cycle.get("melchior_reused") else ""
                                                ui.label(label + note).classes("font-medium text-sm")
                                                entry = cycle[key]
                                                if key == "result":
                                                    summary = f"result_count={entry.get('result_count', '-')} / {entry.get('answer') or entry.get('result_id') or '-'}"
                                                else:
                                                    summary = " / ".join(str(entry[field]) for field in
                                                        ("status", "next_step", "proposed_action", "selected_capability", "capability", "reason")
                                                        if entry.get(field) is not None) or "未記録"
                                                ui.label(summary).classes("text-sm break-words")
                                        with ui.expansion("Cycle詳細").classes("w-full"):
                                            ui.code(json.dumps(cycle, ensure_ascii=False, indent=2, default=str), language="json").classes("w-full text-xs")
                                with ui.card().classes("w-full border-2 border-green-600 bg-green-50"):
                                    ui.label("FINAL CORE DECISION").classes("font-bold")
                                    final = presentation["final_core_decision"]
                                    for key in ("next_step", "reason", "task_status"):
                                        ui.label(f"{key} = {final.get(key) if final and final.get(key) is not None else '未記録'}").classes("font-mono text-sm")
                                    if not final:
                                        ui.label("最終判断は未記録です。Synthesisからは補完しません。").classes("text-xs")
                                job_status = str(
                                    advisor.get("job_status") or advisor.get("status") or "-"
                                )
                                elapsed = advisor.get("elapsed_seconds")
                                if job_status in {"queued", "running"} and advisor.get("started_at"):
                                    try:
                                        started = datetime.fromisoformat(str(advisor["started_at"]))
                                        elapsed = max(
                                            0.0,
                                            (datetime.now(timezone.utc) - started).total_seconds(),
                                        )
                                    except ValueError:
                                        pass
                                ui.label(
                                    "model="
                                    + str(advisor.get("model") or "-")
                                    + " / timeout="
                                    + str(advisor.get("timeout_seconds") or "-")
                                    + "s / job="
                                    + job_status
                                    + " / status="
                                    + str(advisor.get("status") or "-")
                                    + " / elapsed="
                                    + (f"{float(elapsed):.1f}s" if elapsed is not None else "-")
                                ).classes("font-mono text-xs text-grey-7")
                                if job_status in {"queued", "running"}:
                                    with ui.row().classes("items-center gap-2"):
                                        ui.spinner(size="sm", color="indigo")
                                        ui.label(
                                            "Advisorはバックグラウンド評価中です。"
                                            " Cycle単位の提案と最終Core判断を下に表示します。"
                                        ).classes("text-xs text-indigo-800")
                                if advisor.get("situation"):
                                    ui.label("状況整理: " + str(advisor["situation"])).classes(
                                        "text-sm"
                                    )
                                if advisor.get("next_step"):
                                    ui.label(
                                        "CASPER提案（最終判断ではありません）: " + str(advisor["next_step"])
                                    ).classes("text-sm font-medium text-indigo-900")
                                if advisor.get("reason"):
                                    ui.label("提案理由: " + str(advisor["reason"])).classes(
                                        "text-sm"
                                    )
                                missing = advisor.get("missing_information") or []
                                if missing:
                                    ui.label(
                                        "不足情報: " + " / ".join(str(x) for x in missing)
                                    ).classes("text-xs text-orange-800")
                                if advisor.get("error"):
                                    ui.label(
                                        "Advisor error: " + str(advisor["error"])
                                    ).classes("text-xs text-red-700")
    
                                with ui.expansion(
                                    "Advisor 稼働ログ",
                                    value=bool(advisor.get("error")),
                                ).classes("w-full border border-indigo-100 bg-white"):
                                    ui.label(
                                        "Cycleごとの提案・Action / Result・最終Core判断を確認するログです。"
                                        " 推論過程は保存・表示しません。"
                                    ).classes("text-xs text-grey-7")
    
                                    trace = state.get("trace") or {}
                                    export_text = json.dumps(
                                        _advisor_log_export(result, advisor, trace),
                                        ensure_ascii=False,
                                        indent=2,
                                        default=str,
                                    )
    
                                    def copy_advisor_log(text: str = export_text) -> None:
                                        ui.run_javascript(
                                            'navigator.clipboard.writeText('
                                            + json.dumps(text, ensure_ascii=False)
                                            + ')'
                                        )
                                        ui.notify("Advisor稼働ログをコピーしました", type="positive")
    
                                    ui.button(
                                        "ログをコピー",
                                        icon="content_copy",
                                        on_click=copy_advisor_log,
                                    ).props("outline dense").classes("self-start")
    
                                    lifecycle = []
                                    for event in trace.get("advisor_events") or []:
                                        lifecycle.append(
                                            str(event.get("occurred_at") or "")
                                            + "  "
                                            + str(event.get("event_type") or "")
                                        )
                                    if lifecycle:
                                        ui.label("状態遷移").classes("font-medium text-sm")
                                        for line in lifecycle:
                                            ui.label(line).classes("font-mono text-xs")
                                    else:
                                        ui.label(
                                            "状態遷移: DB監査イベントはまだありません。"
                                        ).classes("text-xs text-grey-6")
    
                                    ui.label("検査結果").classes("font-medium text-sm")
                                    ui.label(
                                        "job="
                                        + str(advisor.get("job_status") or "-")
                                        + " / status="
                                        + str(advisor.get("status") or "-")
                                        + " / comparison="
                                        + str(advisor.get("comparison") or "-")
                                        + " / error="
                                        + str(advisor.get("error") or "-")
                                    ).classes("font-mono text-xs")
    
                                    trace_task = (state.get("trace") or {}).get("task") or {}
                                    observation_pack = (
                                        trace_task.get("observation_pack")
                                        or result.get("observation_pack")
                                    )
                                    if observation_pack:
                                        ui.label("MELCHIOR / CASPER 共通 Observation Pack").classes(
                                            "font-medium text-sm"
                                        )
                                        ui.code(
                                            json.dumps(
                                                observation_pack,
                                                ensure_ascii=False,
                                                indent=2,
                                                default=str,
                                            ),
                                            language="json",
                                        ).classes("w-full text-xs")
    
                                    request_context = advisor.get("request_context")
                                    if request_context:
                                        ui.label("Ollamaへ渡した判断コンテキスト").classes(
                                            "font-medium text-sm"
                                        )
                                        ui.code(
                                            json.dumps(
                                                request_context,
                                                ensure_ascii=False,
                                                indent=2,
                                                default=str,
                                            ),
                                            language="json",
                                        ).classes("w-full text-xs")
    
                                    response_diagnostic = advisor.get("response_diagnostic")
                                    if response_diagnostic:
                                        ui.label(
                                            "LLM返却値の形式検査（安全化済み）"
                                        ).classes("font-medium text-sm")
                                        ui.label(
                                            "契約対象フィールドだけを保存しています。"
                                            " 追加キーは名前だけ記録し、値は保存しません。"
                                        ).classes("text-xs text-grey-7")
                                        ui.code(
                                            json.dumps(
                                                response_diagnostic,
                                                ensure_ascii=False,
                                                indent=2,
                                                default=str,
                                            ),
                                            language="json",
                                        ).classes("w-full text-xs")
    
                        if state["busy"]:
                            with ui.row().classes("items-center gap-2"):
                                ui.spinner(size="sm", color="blue-grey")
                                ui.label("Taskを作成してPKBを確認しています…")
                            return
                        if not result:
                            ui.label("まだ依頼していません。")
                            return
    
                        status = result.get("status", "")
                        color = {
                            "completed": "green",
                            "waiting_external": "orange",
                            "rejected": "red",
                        }.get(status, "grey")
                        ui.badge(status or "result", color=color)
                        if result.get("selected_capability"):
                            ui.label(
                                "選択した能力: " + result["selected_capability"]
                            ).classes("text-sm text-blue-grey-800")
                        if result.get("message"):
                            ui.label(result["message"]).classes("text-base")
                        if result.get("question"):
                            with ui.card().classes(
                                "w-full border-2 border-orange-300 bg-orange-50"
                            ):
                                ui.label("追加確認").classes("font-bold text-orange-900")
                                ui.label(result["question"])
                        if result.get("task_id"):
                            ui.label(
                                "Task: " + result["task_id"]
                            ).classes("font-mono text-xs text-grey-6")
    
                        comparison = result.get("comparison") or {}
                        if comparison:
                            with ui.card().classes("w-full border border-purple-200 bg-purple-50"):
                                ui.label("PKB＋Web 比較").classes("font-bold text-purple-900")
                                ui.label(
                                    "status="
                                    + str(comparison.get("status") or "-")
                                    + " / current="
                                    + str(comparison.get("current") or "-")
                                    + " / latest="
                                    + str(comparison.get("latest") or "-")
                                    + " / kind="
                                    + str(comparison.get("latest_kind") or "-")
                                ).classes("font-mono text-xs")
                                if comparison.get("web_query"):
                                    ui.label(
                                        "Web検索語: " + str(comparison.get("web_query"))
                                    ).classes("text-xs text-grey-7")
                                ui.label(
                                    comparison.get("message") or "比較結果はありません。"
                                ).classes("text-sm")
    
                        web_result = result.get("web") or {}
                        if web_result:
                            with ui.expansion("根拠になったWeb調査", value=True).classes(
                                "w-full border border-cyan-200 bg-white"
                            ):
                                ui.label(
                                    f"Provider: {web_result.get('provider') or '-'} / "
                                    f"Query: {web_result.get('query') or '-'}"
                                ).classes("text-xs text-grey-7")
                                fact_summary = web_result.get("fact_summary") or {}
                                if fact_summary.get("kind") == "driver_version":
                                    with ui.card().classes("w-full border border-indigo-200 bg-indigo-50"):
                                        ui.label("抽出した事実候補").classes("font-bold text-indigo-900")
                                        ui.label(
                                            "preferred_kind="
                                            + str(fact_summary.get("preferred_kind") or "-")
                                            + " / primary_domains="
                                            + ",".join(fact_summary.get("primary_domains") or [])
                                            + " / status="
                                            + str(fact_summary.get("status") or "-")
                                            + " / best="
                                            + str(fact_summary.get("best_candidate") or "-")
                                        ).classes("font-mono text-xs")
                                        for group in (fact_summary.get("groups") or [])[:5]:
                                            ui.label(
                                                f"{group.get('kind')} / "
                                                f"status={group.get('status')} / "
                                                f"best={group.get('best_candidate')}"
                                            ).classes("font-medium text-xs text-indigo-900")
                                            for candidate in (group.get("candidates") or [])[:5]:
                                                ui.label(
                                                    f"  {candidate.get('value')} / "
                                                    f"sources={candidate.get('source_count', 0)} / "
                                                    f"domains={candidate.get('domain_count', 0)} / "
                                                    f"primary={candidate.get('primary_source_count', 0)} / "
                                                    f"date={candidate.get('latest_date') or '-'} / "
                                                    f"context={candidate.get('best_context_score', 0)} / "
                                                    f"best_quality={candidate.get('best_quality_score', 0)}"
                                                ).classes("text-xs")
                                        historical = fact_summary.get("historical_groups") or []
                                        if historical:
                                            ui.label("過去版候補").classes("font-bold text-xs text-grey-7")
                                            for group in historical[:5]:
                                                ui.label(
                                                    f"{group.get('kind')} / best={group.get('best_candidate')}"
                                                ).classes("text-xs text-grey-7")
                                for hit in (web_result.get("hits") or [])[:5]:
                                    with ui.card().classes("w-full p-2 gap-1"):
                                        ui.label(
                                            f"{hit.get('rank')}. {hit.get('title') or hit.get('url') or '検索結果'}"
                                        ).classes("font-medium text-sm")
                                        if hit.get("url"):
                                            ui.link(
                                                hit["url"],
                                                hit["url"],
                                                new_tab=True,
                                            ).classes("text-xs")
                                        if hit.get("snippet"):
                                            ui.label(hit["snippet"]).classes("text-xs text-grey-8")
                                        ui.label(
                                            "evidence_rank="
                                            + str(hit.get("evidence_rank") or "-")
                                            + " / quality="
                                            + str(hit.get("quality_score") or 0)
                                            + " / "
                                            + str(hit.get("authority_hint") or "-")
                                            + " / authority="
                                            + str(hit.get("authority_level") or "-")
                                        ).classes("font-mono text-xs text-grey-6")
                                        ui.label(
                                            "fetch=" + str(hit.get("fetch_status") or "unknown")
                                        ).classes("font-mono text-xs text-grey-6")
                                        if hit.get("version_facts"):
                                            ui.label(
                                                "version候補: "
                                                + ", ".join(
                                                    f"{fact.get('kind')}={fact.get('value')}"
                                                    + (
                                                        f"@{fact.get('date_hint')}"
                                                        if fact.get("date_hint")
                                                        else ""
                                                    )
                                                    for fact in (hit.get("version_facts") or [])
                                                )
                                            ).classes("text-xs text-indigo-8")
                                        elif hit.get("version_candidates"):
                                            ui.label(
                                                "version候補: "
                                                + ", ".join(hit.get("version_candidates") or [])
                                            ).classes("text-xs text-indigo-8")
                                        if hit.get("date_hints"):
                                            ui.label(
                                                "日付候補: " + ", ".join(hit.get("date_hints") or [])
                                            ).classes("text-xs text-grey-7")
    
                        finance_result = result.get("finance") or {}
                        if finance_result:
                            with ui.expansion("根拠になった家計集計", value=True).classes(
                                "w-full border border-teal-200 bg-white"
                            ):
                                ui.label(
                                    f"検索期間: {finance_result.get('requested_start_date') or '-'}"
                                    f" 〜 {finance_result.get('requested_end_date') or '-'}"
                                ).classes("text-sm")
                                ui.label(
                                    f"明細存在期間: {finance_result.get('data_start_date') or '-'}"
                                    f" 〜 {finance_result.get('data_end_date') or '-'}"
                                ).classes("text-xs text-grey-7")
                                ui.label(
                                    f"明細 {finance_result.get('transaction_count', 0)}件 / "
                                    f"収入 ¥{int(finance_result.get('income_total') or 0):,} / "
                                    f"支出 ¥{int(finance_result.get('expense_total') or 0):,} / "
                                    f"収支 ¥{int(finance_result.get('net_total') or 0):,}"
                                ).classes("text-sm")
    
                        search_result = result.get("search") or {}
                        rows = search_result.get("items") or []
                        if rows:
                            with ui.expansion("根拠になったPKB記録", value=True).classes(
                                "w-full border border-blue-grey-200 bg-white"
                            ):
                                if search_result.get("result_kind") == "components":
                                    for row in rows:
                                        ui.label(
                                            f"{row.get('component_name')} / "
                                            f"role={row.get('relation_role')} / "
                                            f"current_driver={row.get('current_driver')} / "
                                            f"Source={row.get('state_source_uri')}"
                                        ).classes("text-sm")
                                else:
                                    for row in rows:
                                        ui.label(
                                            f"{row.get('entity_name')} / "
                                            f"{row.get('predicate')}={row.get('value')} / "
                                            f"Source={row.get('source_uri')}"
                                        ).classes("text-sm")
    
                    @ui.refreshable
                    def resume_panel():
                        result = state["result"] or {}
                        if result.get("status") != "waiting_external" or not result.get("task_id"):
                            return
    
                        if result.get("core_slice") == "ritsuko_magi_observation_v1":
                            with ui.card().classes(
                                "w-full border-2 border-orange-300 bg-orange-50"
                            ):
                                ui.label("このTaskは待機中です").classes(
                                    "font-bold text-orange-900"
                                )
                                ui.label(
                                    "上部のRITSUKO ⇄ MAGI Observation Loopから、"
                                    "保存済み対話へ追加回答して同じTask IDで再開できます。"
                                ).classes("text-sm")
                            return
    
                        with ui.card().classes(
                            "w-full border-2 border-orange-300 bg-orange-50"
                        ):
                            ui.label("このTaskへ追加回答").classes(
                                "font-bold text-orange-900"
                            )
                            ui.label(
                                "新しいTaskは作らず、上のTask IDをそのまま再開します。"
                            ).classes("text-sm")
                            reply_input = ui.textarea(
                                label="追加回答",
                                placeholder="例: GPUの現在のドライバーを調べて",
                            ).classes("w-full")
    
                            async def submit_resume():
                                if state["busy"] or state["resume_busy"]:
                                    return
                                state["resume_busy"] = True
                                resume_button.disable()
                                ooda_bar.refresh()
                                core_result.refresh()
                                try:
                                    state["result"] = await run.io_bound(
                                        _portal("resume_core_task"),
                                        UUID(result["task_id"]),
                                        reply_input.value or "",
                                    )
                                except Exception as exc:
                                    ui.notify(str(exc)[:240], type="negative")
                                finally:
                                    state["resume_busy"] = False
                                    load_current_trace()
                                    ooda_bar.refresh()
                                    resume_panel.refresh()
                                    core_result.refresh()
                                    trace_panel.refresh()
                                    open_tasks_panel.refresh()
                                    completed_tasks_panel.refresh()
                                    screen_log_panel.refresh()
    
                            resume_button = ui.button(
                                "同じTaskを再開",
                                icon="resume",
                                color="orange",
                                on_click=submit_resume,
                            )
    
                    async def submit_core():
                        if state["busy"] or state["resume_busy"]:
                            return
                        state["busy"] = True
                        state["result"] = None
                        state["trace"] = None
                        run_button.disable()
                        ooda_bar.refresh()
                        core_result.refresh()
                        resume_panel.refresh()
                        trace_panel.refresh()
                        try:
                            state["result"] = await run.io_bound(
                                _portal("run_core_request"),
                                request_input.value or "",
                                state.get("advisor_model"),
                                float(state.get("advisor_timeout") or 60),
                            )
                        except Exception as exc:
                            state["result"] = {
                                "status": "error",
                                "phase": "failed",
                                "message": str(exc),
                            }
                        finally:
                            state["busy"] = False
                            run_button.enable()
                            load_current_trace()
                            ooda_bar.refresh()
                            core_result.refresh()
                            resume_panel.refresh()
                            trace_panel.refresh()
                            open_tasks_panel.refresh()
                            completed_tasks_panel.refresh()
                            screen_log_panel.refresh()
    
                    run_button = ui.button(
                        "依頼する",
                        icon="play_arrow",
                        color="blue-grey",
                        on_click=submit_core,
                    )
                    core_result()
                    resume_panel()
                    ui.timer(2.0, poll_advisor_shadow)
    
    
                if task_id:
                    try:
                        saved_trace = _portal("load_core_task_trace")(UUID(task_id))
                        select_saved_task(saved_trace["task"])
                    except Exception as exc:
                        ui.notify("Taskを開けません: " + str(exc), type="negative")
    
                # The legacy MAGI v0 flow is comparison/reference context, not the current path.
                with ui.expansion(
                    "旧MAGI v0 処理フロー（比較・確認用）",
                    value=False,
                    icon="account_tree",
                ).classes("w-full border"):
                    ui.label(
                        "過去のMAGI協調経路を比較・確認するために残しています。"
                        "現在のRITSUKO ⇄ MAGI Observation Loopには使用しません。"
                    ).classes("text-xs text-grey-7")
                    with ui.row().classes("w-full items-center gap-2 flex-wrap"):
                        for index, step in enumerate(CORE_FLOW_STEPS):
                            if index:
                                ui.label("→").props("aria-hidden=true")
                            ui.label(step).classes("border rounded p-2 text-sm")
                    ui.label(
                        "曖昧依頼の旧協調経路です。Cycle 1で限定PKB readを行い、"
                        "結果をObservation v2へ戻してCycle 2で再検討します。"
                        "Synthesisは中間提案。Coordinatorが反復・最大2 cycle・外部への拡張を制限します。"
                        "現在のObservation Loopの制御・Task状態・完了判定には使用しません。"
                    ).classes("text-sm")
    
    core_page._context_sync = sync
    return core_page
