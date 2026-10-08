"""Presentation-only helpers for the daily RITSUKO Core UI.

This module must not decide Task transitions, permissions, completion, or MAGI
consensus. It converts already-established runtime state into stable UI labels
and preference metadata.
"""
from __future__ import annotations

from copy import deepcopy


CORE_UI_PREFERENCE_SPEC = {
    "magi_configuration": {
        "label": "MAGI設定",
        "group": "normal",
        "default_open": False,
        "default_visible": True,
    },
    "magi_melchior": {
        "label": "MELCHIOR設定",
        "group": "normal",
        "default_open": False,
        "default_visible": None,
    },
    "magi_casper": {
        "label": "CASPER設定",
        "group": "normal",
        "default_open": False,
        "default_visible": None,
    },
    "magi_balthasar": {
        "label": "BALTHASAR設定",
        "group": "normal",
        "default_open": False,
        "default_visible": None,
    },
    "verification": {
        "label": "検証情報",
        "group": "verification",
        "default_open": False,
        "default_visible": True,
    },
    "trace": {
        "label": "Task詳細・実行記録",
        "group": "verification",
        "default_open": False,
        "default_visible": True,
    },
    "legacy_protocol": {
        "label": "旧Protocol / 比較・確認用",
        "group": "verification",
        "default_open": False,
        "default_visible": False,
    },
    "limits": {
        "label": "動作・安全境界",
        "group": "normal",
        "default_open": False,
        "default_visible": True,
    },
}

CORE_UI_DEFAULT_OPEN = {
    key: bool(spec["default_open"])
    for key, spec in CORE_UI_PREFERENCE_SPEC.items()
}
CORE_UI_VISIBILITY_DEFAULT = {
    key: bool(spec["default_visible"])
    for key, spec in CORE_UI_PREFERENCE_SPEC.items()
    if spec["default_visible"] is not None
}
CORE_UI_SETTING_GROUPS = {
    "normal": tuple(
        key for key, spec in CORE_UI_PREFERENCE_SPEC.items()
        if spec["group"] == "normal"
    ),
    "verification": tuple(
        key for key, spec in CORE_UI_PREFERENCE_SPEC.items()
        if spec["group"] == "verification"
    ),
}
CORE_UI_LABELS = {
    key: str(spec["label"])
    for key, spec in CORE_UI_PREFERENCE_SPEC.items()
}


_STATUS_LABELS = {
    "running": "処理中",
    "received": "受付済み",
    "waiting_information": "情報取得中",
    "waiting_user": "追加情報待ち",
    "proposal_ready": "本人確認待ち",
    "review_evaluated": "確認結果を評価済み",
    "candidate_ready": "回答候補あり",
    "completed": "完了",
    "failed": "失敗",
    "stopped": "停止",
    "paused": "一時停止",
    "waiting_external": "外部情報待ち",
}

_MEMBER_STATE_LABELS = {
    "queued": "待機中",
    "running": "実行中",
    "completed": "完了",
    "ok": "完了",
    "invalid": "無効な応答",
    "unavailable": "利用不可",
    "withheld": "安全境界により未送信",
}


def short_text(value: object, limit: int = 180) -> str:
    text = " ".join(str(value or "").strip().split())
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)] + "…"


def member_response_summary(response: object) -> str:
    if not isinstance(response, dict):
        return ""
    for key in (
        "understood_request",
        "reason",
        "answer_candidate",
        "question_for_user",
        "state",
        "category",
    ):
        value = response.get(key)
        if isinstance(value, str) and value.strip():
            if key in {"state", "category"}:
                continue
            return short_text(value)
    state = str(response.get("state") or response.get("category") or "").strip()
    return state


def normalize_member_progress_event(event: dict) -> dict:
    state = str(event.get("state") or "queued")
    return {
        "task_id": str(event.get("task_id") or ""),
        "turn": int(event.get("turn") or 0),
        "member": str(event.get("member") or ""),
        "provider": str(event.get("provider") or ""),
        "model": str(event.get("model") or ""),
        "state": state,
        "state_label": _MEMBER_STATE_LABELS.get(state, state),
        "elapsed_seconds": event.get("elapsed_seconds"),
        "summary": short_text(event.get("validated_summary") or ""),
        "error_kind": str(event.get("error_kind") or ""),
    }


def resource_catalog_summary(
    catalog: dict,
) -> tuple[list[str], list[str], list[str]]:
    read_sources = []
    user_routes = []
    unavailable = []
    for name, spec in (catalog or {}).items():
        if not isinstance(spec, dict):
            continue
        source_name = str(name)
        access = str(spec.get("access") or "")
        if access == "ask_user":
            if spec.get("available"):
                user_routes.append(source_name)
            continue
        if spec.get("available"):
            read_sources.append(source_name)
        else:
            unavailable.append(source_name)
    return read_sources, user_routes, unavailable


def _latest_observation_summary(session: dict) -> str:
    for item in reversed(session.get("observations") or []):
        if not isinstance(item, dict):
            continue
        text = short_text(item.get("text"))
        if text:
            source = str(item.get("source") or "").strip()
            return (source + ": " if source else "") + text
    return ""


def _latest_result_summary(session: dict, saved_task: dict) -> str:
    detail = session.get("detail") or {}
    if isinstance(detail, dict):
        for key in ("answer_candidate", "knowledge_candidate"):
            value = short_text(detail.get(key))
            if value:
                return value

    message = short_text(saved_task.get("message"))
    if message:
        return message

    observation = _latest_observation_summary(session)
    if observation:
        return observation

    classification = session.get("classification") or {}
    if isinstance(classification, dict):
        understood = short_text(classification.get("understood_request"))
        if understood:
            return understood
    return "まだ結果はありません。"


def _user_prompt(session: dict) -> str:
    question = short_text(session.get("user_question"))
    if question:
        return question
    detail = session.get("detail") or {}
    if isinstance(detail, dict):
        return short_text(detail.get("question_for_user"))
    return ""


def _next_action(status: str, busy: bool, read_only: bool) -> tuple[str, str | None]:
    if read_only:
        return "保存済みTaskを閲覧中です。変更操作はありません。", None
    if busy or status in {"running", "received"}:
        return "RITSUKOが処理中です。必要なら停止できます。", None
    if status == "waiting_information":
        return "RITSUKOが不足情報を取得中です。あなたの操作は通常不要です。", None
    if status == "waiting_user":
        return "下の「このTaskへ回答」から追加情報を入力してください。", "clarification"
    if status == "proposal_ready":
        return "回答だけで完了するか、記憶にも反映するか選択してください。", "proposal_review"
    if status == "completed":
        return "このTaskは完了しています。", None
    if status == "failed":
        return "このTaskは失敗しました。実行記録で原因を確認してください。", None
    if status == "stopped":
        return "このTaskは停止しています。利用可能な再開操作があれば画面に表示します。", None
    return "RITSUKOの状態を確認しています。", None


def _recent_updates(session: dict, limit: int = 6) -> list[str]:
    updates: list[str] = []

    for turn in reversed(session.get("turns") or []):
        if not isinstance(turn, dict):
            continue
        envelope = turn.get("request_envelope") or {}
        number = envelope.get("turn") or "?"
        purpose = (
            turn.get("question_purpose")
            or envelope.get("question_purpose")
            or turn.get("stage")
            or "analysis"
        )
        status = turn.get("status") or "-"
        updates.append(f"Turn {number}: {purpose} / {status}")

    for item in reversed(session.get("observations") or []):
        if not isinstance(item, dict):
            continue
        source = str(item.get("source") or "Observation")
        text = short_text(item.get("text"), 120)
        updates.append(f"{source}を取得" + (f": {text}" if text else ""))

    return updates[:limit]


def build_task_presentation(
    session: dict | None,
    *,
    saved_task: dict | None = None,
    fallback_request: str = "",
    busy: bool = False,
    read_only: bool = False,
    live_member_states: dict | None = None,
) -> dict:
    session = session if isinstance(session, dict) else {}
    saved_task = saved_task if isinstance(saved_task, dict) else {}

    status = str(
        (
            saved_task.get("status")
            if read_only and saved_task.get("status")
            else session.get("status")
        )
        or saved_task.get("status")
        or ("running" if busy else "")
    )
    request_text = short_text(
        saved_task.get("request")
        or session.get("user_raw")
        or fallback_request,
        300,
    )
    next_action, action_kind = _next_action(status, busy, read_only)

    return {
        "request_text": request_text,
        "status": status,
        "status_label": _STATUS_LABELS.get(status, status or "未開始"),
        "latest_result_summary": _latest_result_summary(session, saved_task),
        "user_action_required": action_kind is not None,
        "user_action_kind": action_kind,
        "user_prompt": _user_prompt(session),
        "system_next_action": next_action,
        "is_read_only": bool(read_only),
        "live_member_states": deepcopy(live_member_states or {}),
        "recent_updates": _recent_updates(session),
    }
