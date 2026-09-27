"""Read-only view model for the initial Secretary Core GUI.

No GUI framework dependency: unit tests can verify exactly what the user sees.
Only loads locally persisted experimental checkpoints, never GitHub or DB dumps.
"""
from __future__ import annotations

from datetime import datetime
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
    "invalid_specialist": "不正な担当指定を停止",
    "user_update": "ユーザーから追加情報",
    "finish": "今回の処理を終了",
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
            for key in ("reason", "feedback", "message"):
                if event.get(key):
                    details.append(str(event[key]))
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
