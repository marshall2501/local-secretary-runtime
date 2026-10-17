# Provenance-Origin: original
# Provenance-AI: AI-generated
"""Vue日常UIを提供し、共通RITSUKO Task API Routerを組み込むHost。

Task操作／判断・DBアクセスはこのUI Hostに実装しない。従来の
localhost通信制限・read-only既定・独立preview portを維持する。
既存Secretary REST /tasks（別Task契約）は変更しない。
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from ritsuko.application.task_commands import TaskCommands
from interfaces.api.ritsuko_tasks import create_ritsuko_task_router
from interfaces.api.pkb_read import create_pkb_read_router
from interfaces.vue_daily.preferences import (
    read_judgment_preferences, save_judgment_preferences,
)


class JudgmentPreferencesBody(BaseModel):
    """新規Taskの既定方式と検証選択欄の表示（MAGI席数とは独立）。"""

    default_strategy: Literal["standard", "llm"]
    show_strategy_selector: bool


def create_app(*, queries, commands: TaskCommands | None = None,
               read_service=None,
               recover_pending_at_startup: bool = True) -> FastAPI:
    """保存済みTaskの参照APIとビルド済みVueを提供する入口を作る。

    DB接続やSecretはComposition Root側で解決し、この入口には渡さない。

    Args:
        queries: 保存済みTask一覧と履歴を読み取る共有Query Service。
        commands: 実DB・権限・Sourceを正しく組み立てた場合だけ渡すコマンド受付サービス。
            省略時は従来どおり読取専用（POSTは503）とする。
        read_service: 共通ReadService。省略時はPKB検索を503で拒否。
        recover_pending_at_startup: Commands注入時の未着手Task自動再開。
            実Taskの手動プレビューではFalseにして過去のJobを起動しない。

    Returns:
        FastAPI: ローカルTask参照と、ビルド済みVueだけを公開するアプリ。
    """
    app = FastAPI(
        title="Local Secretary Vue Daily API",
        docs_url=None, redoc_url=None, openapi_url=None,
    )

    if (recover_pending_at_startup and commands is not None
            and hasattr(commands, "recover_pending")):
        @app.on_event("startup")
        async def recover_durable_pending_tasks() -> None:
            """明示的にCommandsを注入した場合のみ未着手Taskを復旧する。

            DBのpendingのみが対象。runningの実行済み部分は自動再実行しない。
            通常8097のread-only previewではこの処理も実行されない。
            """
            await commands.recover_pending(limit=20)

    @app.middleware("http")
    async def local_only(request: Request, call_next):
        """localhost入口を保ち、画面閲覧とTask書込の扱いを分ける。

        URLを外部ページから開くと、Chrome等はGETでもSec-Fetch-Site:
        cross-site / same-siteを送る。GETまで拒否すると本人がUIを開けない。
        Task変更を伴うリクエストだけOrigin・Fetch Siteを照合する。
        """
        host = request.headers.get("host", "").split(":", 1)[0].lower()
        if host not in {"127.0.0.1", "localhost"}:
            return JSONResponse({"detail": "local_host_only"}, status_code=403)
        if any(name in request.headers for name in (
            "x-forwarded-host", "x-forwarded-for", "forwarded",
        )):
            return JSONResponse({"detail": "forwarded_request_denied"}, status_code=403)
        if request.method not in {"GET", "HEAD"}:
            origin = request.headers.get("origin")
            if origin and origin != str(request.base_url).rstrip("/"):
                return JSONResponse({"detail": "invalid_origin"}, status_code=403)
            site = request.headers.get("sec-fetch-site")
            if site and site not in {"same-origin", "none"}:
                return JSONResponse({"detail": "cross_site_denied"}, status_code=403)
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response

    @app.get("/api/v1/ritsuko/health")
    def health():
        """プレビューAPI自体の応答を返す。DB接続の正常性は検査しない。"""
        return {
            "status": "ok", "synthetic": False,
            "mode": "commands_enabled" if commands is not None else "read_only",
        }

    @app.get("/api/v1/ritsuko/preferences")
    def get_judgment_preferences():
        """既存NiceGUIと共有する判断方式設定を返す。Secretは含めない。"""
        try:
            return read_judgment_preferences()
        except (OSError, ValueError):
            raise HTTPException(503, "ui_preferences_unavailable") from None

    @app.post("/api/v1/ritsuko/preferences")
    def update_judgment_preferences(body: JudgmentPreferencesBody):
        """明示的な操作入口だけで既定方式／検証UI表示を保存する。"""
        if commands is None:
            raise HTTPException(503, "task_commands_not_configured")
        try:
            return save_judgment_preferences(
                body.default_strategy, body.show_strategy_selector,
            )
        except (OSError, ValueError):
            raise HTTPException(503, "ui_preferences_unavailable") from None

    # UIと無関係のTask APIを注入する。通信境界はこのhostで一律に保護。
    app.include_router(create_ritsuko_task_router(queries=queries, commands=commands))
    app.include_router(create_pkb_read_router(read_service=read_service))

    # Vue画面はビルド後のみ公開。ソースやCredentialは静的公開しない。
    dist = Path(__file__).parent / "frontend" / "dist"

    @app.get("/", include_in_schema=False)
    def daily_home():
        """一般のlocalhost入口をVueへ案内する（Task書込は行わない）。"""
        if not dist.is_dir():
            raise HTTPException(503, "vue_frontend_not_built")
        return RedirectResponse(url="/ui/", status_code=307)

    if dist.is_dir():
        app.mount("/ui", StaticFiles(directory=str(dist), html=True), name="vue_daily_ui")
    return app


def main() -> None:
    """既存GUIやAPIを停止せず、明示起動時だけ別localhost portで受け付ける。"""
    from bootstrap.web_runtime import (
        build_core_task_queries, build_daily_memory_read_service,
    )
    import uvicorn
    from interfaces.vue_daily.server_logging import build_vue_log_config

    port = int(os.getenv("LSA_VUE_DAILY_PORT", "8097"))
    if port in {8010, 8015, 8092, 8093, 8098} or not 1024 <= port <= 65535:
        raise RuntimeError("Use an isolated Vue Daily port (default 8097).")
    uvicorn.run(create_app(
        queries=build_core_task_queries(),
        read_service=build_daily_memory_read_service(),
    ), host="127.0.0.1", port=port, log_config=build_vue_log_config(server_name="vue-readonly-8097"))


if __name__ == "__main__":
    main()
