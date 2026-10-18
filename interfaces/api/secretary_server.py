# Provenance-Origin: original
# Provenance-AI: AI-generated
"""Canonical Secretary FastAPI host: Vue/static and shared Task/PKB HTTP APIs.

HTTP and presentation live here, DB/MAGI/Task execution do not.
Normal startup injects a non-None TaskCommands port from bootstrap.
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable
from typing import Literal

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from ritsuko.application.task_commands import TaskCommands
from interfaces.api.ritsuko_tasks import create_ritsuko_task_router
from interfaces.api.pkb_read import create_pkb_read_router
from interfaces.api.pkb_commands import create_pkb_command_router
from interfaces.api.external_read import create_external_read_router
from interfaces.vue_daily.preferences import (
    read_judgment_preferences, save_judgment_preferences,
)


class JudgmentPreferencesBody(BaseModel):
    """新規Taskの既定方式と検証選択欄の表示（MAGI席数とは独立）。"""

    default_strategy: Literal["standard", "llm"]
    show_strategy_selector: bool


def create_app(*, queries, commands: TaskCommands | None = None,
               read_service=None, pkb_repository=None, pkb_interpreter=None,
               external_read_token_loader: Callable[[], str | None] | None = None,
               recover_pending_at_startup: bool = False,
               dist_dir: Path | None = None) -> FastAPI:
    """保存済みTaskの参照APIとビルド済みVueを提供する入口を作る。

    DB接続やSecretはComposition Root側で解決し、この入口には渡さない。

    Args:
        queries: 保存済みTask一覧と履歴を読み取る共有Query Service。
        commands: 実DB・権限・Sourceを正しく組み立てた場合だけ渡すコマンド受付サービス。
            省略時は従来どおり読取専用（POSTは503）とする。
        read_service: 共通ReadService。省略時はPKB検索を503で拒否。
        pkb_repository: 独立PKBの書込み・Pending操作に使うRepository。
        pkb_interpreter: 判断できない記憶登録文に使う既存Interpreter。
        external_read_token_loader: 既存MCP/PVA read用のtoken取得Port。
        recover_pending_at_startup: Commands注入時の未着手Task自動再開。
            実Taskの手動プレビューではFalseにして過去のJobを起動しない。

    Returns:
        FastAPI: ローカルTask参照と、ビルド済みVueだけを公開するアプリ。
    """
    app = FastAPI(
        title="Secretary Server",
        docs_url=None, redoc_url=None, openapi_url=None,
    )

    if (recover_pending_at_startup and commands is not None
            and hasattr(commands, "recover_pending")):
        @app.on_event("startup")
        async def recover_durable_pending_tasks() -> None:
            """明示的にCommandsを注入した場合のみ未着手Taskを復旧する。

            DBのpendingのみが対象。runningの実行済み部分は自動再実行しない。
            通常Serverは本人操作を待ち、自動再実行しない。
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
    app.include_router(create_pkb_command_router(
        repository=pkb_repository, interpreter=pkb_interpreter,
    ))
    app.include_router(create_external_read_router(
        read_service=read_service, token_loader=external_read_token_loader,
    ))

    # Vue画面はビルド後のみ公開。ソースやCredentialは静的公開しない。
    dist = dist_dir if dist_dir is not None else (Path(__file__).parents[1] / "vue_daily" / "frontend" / "dist")

    @app.get("/", include_in_schema=False)
    def daily_home():
        """一般のlocalhost入口をVueへ案内する（Task書込は行わない）。"""
        if not dist.is_dir():
            raise HTTPException(503, "vue_frontend_not_built")
        return RedirectResponse(url="/ui/", status_code=307)

    if dist.is_dir():
        app.mount("/ui", StaticFiles(directory=str(dist), html=True), name="vue_daily_ui")
    return app

