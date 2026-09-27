"""Read-only view model for the initial Secretary Core GUI.

No GUI framework dependency: unit tests can verify exactly what the user sees.
Only loads locally persisted experimental checkpoints, never GitHub or DB dumps.
"""
from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
import uuid

from secretary_core import load_state


STATUS_LABELS = {
    "running": "処理中",
    "waiting_user": "回答待ち",
    "answered": "回答済み",
    "error": "実行エラー",
}

EVENT_LABELS = {
    "decision": "統括役の判断",
    "dispatch": "専門担当へ依頼",
    "evaluation": "取得結果を評価",
    "answer_review": "回答案を独立監査",
    "evidence_fallback": "取得済み根拠による限定報告",
    "duplicate_blocked": "重複した依頼を停止",
    "evidence_unchanged": "再調査したが新しい資料なし",
    "invalid_specialist": "不正な担当指定を停止",
    "user_update": "ユーザーから追加情報",
    "finish": "今回の処理を終了",
    "goal_gate": "元の依頼の完了条件を確認",
    "task_reopened": "継続依頼を再開",
    "error": "処理中にエラー",
}

SPECIALIST_LABELS = {"memory": "記憶担当", "research": "資料調査担当"}


def task_summaries(folder: Path, limit: int = 40) -> list[dict]:
    """List local tasks sorted newest first; skip malformed checkpoint files."""
    if not folder.is_dir():
        return []
    paths = sorted(folder.glob("*.json"), key=lambda p: p.stat().st_mtime_ns,
                   reverse=True)
    result = []
    for path in paths:
        try:
            task_id = str(uuid.UUID(path.stem))
            state = load_state(task_id, folder)
        except (ValueError, OSError, KeyError, TypeError):
            continue
        result.append({
            "task_id": task_id,
            "request": str(state.get("original_request") or "（依頼内容なし）"),
            "status": str(state.get("status") or "unknown"),
            "status_label": STATUS_LABELS.get(state.get("status"), "不明"),
            "modified": datetime.fromtimestamp(path.stat().st_mtime).strftime("%m/%d %H:%M"),
        })
        if len(result) >= limit:
            break
    return result


def event_rows(state: dict) -> list[dict]:
    """Adapt actual structured events into a human-readable timeline."""
    rows = []
    for index, event in enumerate(state.get("events") or [], 1):
        kind = str(event.get("type") or "unknown")
        action = event.get("action")
        heading = EVENT_LABELS.get(kind, kind)
        details = []
        if kind == "decision":
            labels = {"delegate": "担当に依頼", "answer": "回答案を作成",
                      "ask_user": "ユーザーに質問"}
            details.append(labels.get(action, str(action or "次の行動を選択")))
            to = event.get("specialist")
            if to:
                details.append("担当: " + SPECIALIST_LABELS.get(str(to), str(to)))
            if event.get("reason"):
                details.append("理由: " + str(event["reason"]))
            if action == "ask_user" and event.get("question"):
                details.append("質問: " + str(event["question"]))
            if action == "answer" and event.get("draft_answer"):
                details.append("回答案: " + str(event["draft_answer"]))
        elif kind == "dispatch":
            details.append("依頼先: " + SPECIALIST_LABELS.get(
                str(event.get("to") or ""), str(event.get("to") or "不明")))
            details.append("依頼内容: " + str(event.get("query") or ""))
        elif kind == "answer_review":
            verdict = event.get("supported")
            details.append("判定: " + (
                "根拠の範囲内" if verdict is True else
                "要修正" if verdict is False else "判定不能"))
            if event.get("feedback"):
                details.append(str(event["feedback"]))
        elif kind == "evaluation":
            details.append(str(event.get("feedback") or "確認済み"))
        elif kind == "user_update":
            details.append(str(event.get("text") or ""))
        else:
            for key in ("reason", "feedback", "message", "question"):
                if event.get(key):
                    details.append(("次に必要な情報: " if key == "question" else "")
                                   + str(event[key]))
        rows.append({"index": index, "kind": kind, "title": heading,
                     "details": details})
    return rows


def evidence_rows(state: dict) -> list[dict]:
    """Preserve simulated/unverified/unlinked flags rather than just prose."""
    rows = []
    for observation in state.get("observations") or []:
        specialist = str(observation.get("specialist") or "unknown")
        if observation.get("error"):
            rows.append({
                "id": "取得失敗", "specialist": specialist,
                "text": str(observation["error"]),
                "flags": ["取得失敗"], "source": "",
            })
        for record in observation.get("records") or []:
            flags = []
            if record.get("simulated"):
                flags.append("模擬")
            if record.get("verification") in ("unverified", "unknown"):
                flags.append("未検証")
            if record.get("verification") == "fictional_not_real_reference":
                flags.append("架空資料")
            if record.get("linkage") == "unlinked_same_domain_not_proof_of_target":
                flags.append("対象との紐付けなし")
            if not flags:
                flags.append("記録上の情報")
            rows.append({
                "id": str(record.get("id") or "記録"),
                "specialist": specialist,
                "text": str(record.get("text") or ""),
                "flags": flags, "source": str(record.get("source") or ""),
            })
    return rows


def can_reply(state: dict) -> bool:
    return state.get("status") == "waiting_user"


def can_retry(state: dict, running_tasks: set[str]) -> bool:
    return (state.get("status") in ("error", "running")
            and state.get("task_id") not in running_tasks)


def task_text_report(state: dict) -> str:
    """Lossless-enough plain-text trace for copying a single task into a report.

    Includes ALL persisted events and observations, never screenshots or hidden
    browser state. Original structured checkpoint is independently available
    through the optional JSON pane in the GUI. Does not claim timestamps absent
    from the original event schema.
    """
    events = state.get("events") or []
    observations = state.get("observations") or []
    status = str(state.get("status") or "unknown")
    lines = [
        "Local Secretary AI — 検証用テキストログ",
        "Task ID: " + str(state.get("task_id") or "不明"),
        "状態: " + STATUS_LABELS.get(status, "不明") + " (" + status + ")",
        "対象: " + str(state.get("target") or "不明"),
        "領域: " + str(state.get("domain") or "不明"),
        "モード: " + str(state.get("mode") or "不明"),
        "モデル: " + str(state.get("model") or "不明"),
        "※ ローカルcheckpointの内容。各イベントの日時は現行形式では未記録です。",
        "",
        "=== 元の依頼 ===",
        str(state.get("original_request") or "（記録なし）"),
    ]
    if state.get("user_update"):
        lines.extend(["", "=== 最後に受け取った追加情報 ===",
                      str(state["user_update"])])
    if state.get("awaiting"):
        heading = ("=== 現在の質問 ===" if status == "waiting_user" else
                   "=== 記録に残る最後の質問（現在は回答待ちではありません） ===")
        lines.extend(["", heading, str(state["awaiting"])])
    lines.extend([
        "",
        "=== 判断・作業の全履歴（" + str(len(events)) + "件） ===",
    ])
    for row, event in zip(event_rows(state), events):
        lines.append("[" + str(row["index"]) + "] " + row["title"]
                     + " / " + row["kind"])
        for detail in row["details"]:
            lines.append("  " + detail)
        # Raw event makes diagnostic fields (query, status, simulated flags,
        # old manager decision data) copyable, including fields not yet in UI.
        lines.append("  EVENT: " + json.dumps(
            event, ensure_ascii=False, sort_keys=True, default=str))
        lines.append("")
    if not events:
        lines.append("（履歴なし）")
    lines.extend([
        "=== 取得した証拠の全履歴（" + str(len(observations)) + "回） ===",
    ])
    for index, observation in enumerate(observations, 1):
        specialist = str(observation.get("specialist") or "unknown")
        lines.append("[" + str(index) + "] "
                     + SPECIALIST_LABELS.get(specialist, specialist))
        for key, label in (
            ("query", "検索・依頼内容"),
            ("coverage", "取得範囲"),
            ("source_mode", "情報源モード"),
            ("target", "対象"),
            ("error", "取得エラー"),
        ):
            if observation.get(key) is not None:
                lines.append("  " + label + ": " + str(observation[key]))
        records = observation.get("records") or []
        if not records:
            lines.append("  記録: なし（取得失敗や検索範囲の制約と区別）")
        for record in records:
            qualifiers = []
            if record.get("simulated"):
                qualifiers.append("模擬")
            if record.get("verification") in ("unverified", "unknown"):
                qualifiers.append("未検証")
            if record.get("verification") == "fictional_not_real_reference":
                qualifiers.append("架空資料")
            if record.get("linkage") == "unlinked_same_domain_not_proof_of_target":
                qualifiers.append("対象との紐付けなし")
            lines.append(
                "  " + str(record.get("id") or "記録") + ": "
                + str(record.get("text") or "")
                + (" [" + ", ".join(qualifiers) + "]" if qualifiers else "")
            )
            if record.get("source") is not None:
                lines.append("    出典: " + str(record["source"]))
            lines.append("    RECORD: " + json.dumps(
                record, ensure_ascii=False, sort_keys=True, default=str))
        # Retain the complete retrieval metadata as well as displayed records.
        lines.append("  OBSERVATION: " + json.dumps(
            observation, ensure_ascii=False, sort_keys=True, default=str))
        lines.append("")
    if not observations:
        lines.append("（取得結果なし）")
    lines.extend(["", "=== 現在の結論・保留事項 ==="])
    if state.get("answer"):
        lines.append(str(state["answer"]))
    elif status == "running":
        lines.append("処理中（まだ回答なし）")
    else:
        lines.append("回答なし")
    if state.get("feedback"):
        lines.append("最新の評価: " + str(state["feedback"]))
    if state.get("error"):
        lines.append("エラー: " + str(state["error"]))
    if status == "waiting_user":
        lines.append("依頼は未完了です。上記の質問に対する回答待ちです。")
    elif status == "answered":
        lines.append("今回の回答を生成済み（元の問題の解決を保証しません）。")
    return "\n".join(lines).rstrip() + "\n"
