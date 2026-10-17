# Provenance-Origin: original
# Provenance-AI: AI-generated
"""Vue実Task操作の明示起動入口。既存8097読取プレビュー／8093には触れない。

このモジュールのimportはDB設定変更やMAGI呼出しをしない。
起動は本人の実DB Task書込の個別判断後に限定する。既存pendingは自動復旧しない。
"""
from __future__ import annotations

import argparse


def main(argv: list[str] | None = None) -> None:
    """同意した本人による手動の実Task API起動だけを受け付ける。"""
    parser = argparse.ArgumentParser(description="Owner-local Vue Task command preview")
    parser.add_argument(
        "--enable-task-commands", action="store_true",
        help="現行日常DBへの新Task操作を明示的に有効化する",
    )
    args = parser.parse_args(argv)
    if not args.enable_task_commands:
        parser.error("実DBのTask writeを開始するには --enable-task-commands が必要です。")

    # DBの接続変数はmodule import時に読み取るため、設定を先に確定する。
    from scripts.db.probe_magi_runtime_readonly import (
        ROOT, configure_daily_environment,
    )
    configure_daily_environment(ROOT)
    from bootstrap.magi_task_runtime import build_daily_magi_task_commands
    from bootstrap.web_runtime import (
        build_core_task_queries, build_daily_memory_read_service,
    )
    from interfaces.vue_daily.app import create_app
    import uvicorn
    from interfaces.vue_daily.server_logging import build_vue_log_config

    commands = build_daily_magi_task_commands()
    app = create_app(
        queries=build_core_task_queries(), commands=commands,
        read_service=build_daily_memory_read_service(),
        recover_pending_at_startup=False,
    )
    print("Vue Task command preview: http://127.0.0.1:8099/ui/")
    print("New Task write is enabled; existing pending Tasks are NOT resumed automatically.")
    uvicorn.run(app, host="127.0.0.1", port=8099,
                log_config=build_vue_log_config())


if __name__ == "__main__":
    main()
