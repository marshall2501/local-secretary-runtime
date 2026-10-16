# Provenance-Origin: original
# Provenance-AI: AI-generated
"""サブPCの日常DBでMAGI設定・権限・Task読取の接続だけを確認する。

利用者に長い一時PowerShellを貼り付けてもらわず、既存ランチャーと同じ
DBモード・Port・Secretファイルへの参照を一回のPython実行へ閉じ込める。
Task作成・保存・外部MAGI呼出しは行わず、Secret・Task本文を表示しない。
"""
from __future__ import annotations

import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.db.probe_runtime_policy_readonly import configured_port


def configure_daily_environment(root: Path) -> None:
    """既存Vueランチャーと同じ日常DB接続情報を現在の検証プロセスに設定する。

    Secretはファイルパスだけ指定し、値は標準出力へ出さない。
    親PowerShellの環境変数は変更されない。

    Args:
        root: 秘書AI Runtimeのリポジトリroot。

    Raises:
        RuntimeError: 必要なPort設定や既存Secretファイルが見つからない場合。
    """
    port = configured_port(root / ".env.postgres")
    secret_path = root / "secrets" / "secretary-daily-runtime-password.txt"
    if not secret_path.is_file():
        raise RuntimeError("daily runtime secret file missing")
    os.environ.update({
        "LSA_DAILY_DB_MODE": "production",
        "LSA_DAILY_DB_NAME": "secretary",
        "LSA_PKB_DAILY_PORT": str(port),
        "LSA_PKB_DAILY_SECRET": str(secret_path),
    })


def member_states(specs: list[dict], names: tuple[str, ...]) -> list[tuple[str, str]]:
    """3席の各設定を有効・無効・未登録に分け、SecretやモデルURLは含めない。

    Args:
        specs: DBから読み込んだMAGI席の設定。
        names: 画面上のMAGI席名の正本。

    Returns:
        list[tuple[str, str]]: 各席の名前と状態。
    """
    by_name = {str(spec.get("name")): spec for spec in specs}
    return [
        (name, "ENABLED" if by_name[name].get("enabled") else "DISABLED")
        if name in by_name else (name, "NOT_ASSIGNED")
        for name in names
    ]


def main() -> None:
    """共有MAGI Compositionと実DB参照を確認する。Task・Policyは変更しない。"""
    configure_daily_environment(ROOT)

    # DBのモードはmodule import時に固定されるため、設定後にimportする。
    from bootstrap.magi_task_runtime import build_daily_magi_task_commands
    from bootstrap.web_runtime import (
        build_core_task_queries,
        build_magi_settings_repository,
        build_runtime_policy_repository,
    )
    from ritsuko.magi.settings import MEMBER_NAMES, load_member_specs

    build_daily_magi_task_commands()
    print("Composition: OK")

    specs = load_member_specs(build_magi_settings_repository())
    states = member_states(specs, MEMBER_NAMES)
    print("MAGI active:", sum(state == "ENABLED" for _, state in states))
    for name, state in states:
        print(f"  {name}: {state}")

    policy = build_runtime_policy_repository().load_snapshot_or_none("lan")
    print("LAN policy:", "SET" if policy else "MISSING")

    build_core_task_queries().recent(limit=1, offset=0)
    print("Task DB read: OK")
    print("PASS: Task/Policy/Settings read only; no Task write or LLM request")


if __name__ == "__main__":
    main()
