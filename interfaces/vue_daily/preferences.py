# Provenance-Origin: original
# Provenance-AI: AI-generated
"""VueとNiceGUIが共有する既存ローカルUI設定の判断方式部分。

設定ファイルは既存NiceGUIのui_preferences.jsonがマスタ。ここでは二つの
キーだけを読み書きし、他の表示設定・モデル設定・Secretを変更しない。
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from uuid import uuid4


def preferences_path() -> Path:
    """既存NiceGUIと同じ非Git管理UI設定ファイルを指す。"""
    override = os.environ.get("LSA_UI_PREFERENCES_PATH", "").strip()
    if override:
        return Path(override)
    return Path(__file__).resolve().parents[2] / "data" / "ui_preferences.json"


def read_judgment_preferences(path: Path | None = None) -> dict:
    """既存ファイルから新規Taskの既定方式と検証UI表示だけを返す。

    ファイル未作成時は既存NiceGUI既定値に合わせる。破損した既存設定を
    書込み処理が勝手に初期化しないよう、JSON不正は呼出し元へ通知する。
    """
    target = path if path is not None else preferences_path()
    try:
        content = target.read_text(encoding="utf-8")
    except FileNotFoundError:
        raw = {}
    else:
        raw = json.loads(content)
        if not isinstance(raw, dict):
            raise ValueError("ui_preferences_not_object")
    goal_mode = raw.get("core_goal_mode")
    selector = raw.get("core_show_judgment_mode")
    return {
        "default_strategy": "llm" if goal_mode is True else "standard",
        "show_strategy_selector": selector if isinstance(selector, bool) else True,
    }


def save_judgment_preferences(
    default_strategy: str, show_strategy_selector: bool, path: Path | None = None,
) -> dict:
    """判断関連二項目だけを既存UI設定へ原子的に反映する。

    Args:
        default_strategy: 今後の新規Taskに適用するstandard/llm既定値。
        show_strategy_selector: 新規Taskの検証用方式選択欄を表示するか。
        path: 隔離試験でだけ注入する保存先。

    Returns:
        保存後に再読込した判断設定。既存Taskの方式は変更しない。
    """
    if default_strategy not in {"standard", "llm"} or not isinstance(
        show_strategy_selector, bool
    ):
        raise ValueError("invalid_judgment_preferences")
    target = path if path is not None else preferences_path()
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raw = {}
    if not isinstance(raw, dict):
        raise ValueError("ui_preferences_not_object")
    raw["core_goal_mode"] = default_strategy == "llm"
    raw["core_show_judgment_mode"] = show_strategy_selector
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(target.name + "." + uuid4().hex + ".tmp")
    try:
        temporary.write_text(
            json.dumps(raw, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
        )
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)
    return read_judgment_preferences(target)
