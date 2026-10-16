# Provenance-Origin: original
# Provenance-AI: AI-generated
"""Vue日常UIから保存済みRITSUKO Taskを参照するためのFastAPI入口。

Task一覧・履歴は既存Query Serviceへ委譲する。Stage B-1ではコマンドを
外部から注入できる契約だけ追加し、デフォルト起動は読取専用のまま。
既存Secretary REST /tasks（別のTask作成契約）は変更しない。
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Literal
from uuid import UUID

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from ritsuko.application.task_commands import (
    TaskCommandConflict, TaskCommandNotFound, TaskCommands,
)


TaskView = Literal["recent", "open", "completed"]


class TaskStartBody(BaseModel):
    """新規RITSUKO Taskへ送る依頼。空白だけの入力は受付時に拒否する。"""

    request: str = Field(min_length=1, max_length=4000)
    # Task開始時に方式を明示し、API側の暗黙standard選択を避ける。
    decision_strategy: Literal["standard", "llm"]
    task_id: UUID | None = None  # 通信途絶後の同一依頼再送に使うUUID


class TaskAnswerBody(BaseModel):
    """保存済みTaskの本人質問に対する追加回答。"""

    reply: str = Field(min_length=1, max_length=4000)


class TaskReviewBody(BaseModel):
    """既存Taskの提案への本人判断。記憶反映はまだ公開しない。"""

    decision: Literal["answer_only", "remember"]




def create_app(*, queries, commands: TaskCommands | None = None) -> FastAPI:
    """保存済みTaskの参照APIとビルド済みVueを提供する入口を作る。

    DB接続やSecretはComposition Root側で解決し、この入口には渡さない。

    Args:
        queries: 保存済みTask一覧と履歴を読み取る共有Query Service。
        commands: 実DB・権限・Sourceを正しく組み立てた場合だけ渡すコマンド受付サービス。
            省略時は従来どおり読取専用（POSTは503）とする。

    Returns:
        FastAPI: ローカルTask参照と、ビルド済みVueだけを公開するアプリ。
    """
    app = FastAPI(
        title="Local Secretary Vue Daily API",
        docs_url=None, redoc_url=None, openapi_url=None,
    )

    if commands is not None and hasattr(commands, "recover_pending"):
        @app.on_event("startup")
        async def recover_durable_pending_tasks() -> None:
            """明示的にCommandsを注入した場合のみ未着手Taskを復旧する。

            DBのpendingのみが対象。runningの実行済み部分は自動再実行しない。
            通常8097のread-only previewではこの処理も実行されない。
            """
            await commands.recover_pending(limit=20)

    @app.middleware("http")
    async def local_only(request: Request, call_next):
        """ローカルホストでの参照を想定し、外部Originと転送ヘッダを拒否する。

        Host・Originの照合は本人認証ではない。外部Web入口の認可を代替せず、今後の
        実Task更新APIを無条件で公開するための許可ではない。
        """
        host = request.headers.get("host", "").split(":", 1)[0].lower()
        if host not in {"127.0.0.1", "localhost"}:
            return JSONResponse({"detail": "local_host_only"}, status_code=403)
        if any(name in request.headers for name in (
            "x-forwarded-host", "x-forwarded-for", "forwarded",
        )):
            return JSONResponse({"detail": "forwarded_request_denied"}, status_code=403)
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

    @app.get("/api/v1/ritsuko/tasks")
    def list_tasks(
        view: TaskView = "recent",
        limit: int = Query(default=10, ge=1, le=50),
        offset: int = Query(default=0, ge=0),
    ):
        """既存Queryから指定した範囲の保存済みTaskを返す。

        Args:
            view: 最近・進行中・完了のどの一覧を表示するか。
            limit: 表示件数。大量のTaskを一度に画面へ返さない。
            offset: 履歴一覧の読み取り開始位置。

        Returns:
            dict: DB由来の一覧と、保存済みデータであることの識別子。
        """
        try:
            result = getattr(queries, view)(limit=limit, offset=offset)
        except (RuntimeError, OSError):
            raise HTTPException(503, "task_query_unavailable") from None
        return {"items": result, "source": "persisted", "view": view}

    @app.get("/api/v1/ritsuko/tasks/{task_id}")
    def task_trace(task_id: UUID):
        """指定Taskの確定状態とAction/Result履歴を読み取る。

        MAGIの回答候補をTaskの完了状態へ置き換えない。

        Args:
            task_id: 再読み込み後にも同じ履歴を特定する保存済みTask ID。

        Raises:
            HTTPException: Task不在なら404、Query Service利用不能なら503。
        """
        try:
            return {"trace": queries.trace(task_id), "source": "persisted"}
        except ValueError:
            raise HTTPException(404, "task_not_found") from None
        except (RuntimeError, OSError):
            raise HTTPException(503, "task_query_unavailable") from None

    def require_commands() -> TaskCommands:
        """正しい実Task依存が注入されていない間は操作を明示的に拒否する。"""
        if commands is None:
            raise HTTPException(503, "task_commands_not_configured")
        return commands

    async def accept_command(awaitable) -> dict:
        """HTTP受付エラーを整理し、Taskの失敗理由や機密本文をHTTPへ漏らさない。"""
        try:
            return await awaitable
        except TaskCommandNotFound:
            raise HTTPException(404, "task_not_found") from None
        except TaskCommandConflict as exc:
            raise HTTPException(409, str(exc)) from None
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
        except (OSError, RuntimeError):
            raise HTTPException(503, "task_command_unavailable") from None

    @app.post("/api/v1/ritsuko/tasks", status_code=202)
    async def start_task(body: TaskStartBody):
        """実Taskの新規受付。Operationの受理とDBへのTask保存・完了は別段階。"""
        service = require_commands()
        return await accept_command(service.start(
            body.request, decision_strategy=body.decision_strategy,
            task_id=body.task_id,
        ))

    @app.post("/api/v1/ritsuko/tasks/{task_id}/answer", status_code=202)
    async def answer_task(task_id: UUID, body: TaskAnswerBody):
        """同一Task IDへの本人回答を受付ける。DBの最終競合判定はRITSUKOが行う。"""
        service = require_commands()
        return await accept_command(service.answer(task_id, body.reply))

    @app.post("/api/v1/ritsuko/tasks/{task_id}/review", status_code=202)
    async def review_task(task_id: UUID, body: TaskReviewBody):
        """回答のみのReviewを受付ける。記憶反映には別のMemory Intake接続が必要。"""
        service = require_commands()
        return await accept_command(service.review(task_id, body.decision))

    @app.post("/api/v1/ritsuko/tasks/{task_id}/stop")
    def stop_task(task_id: UUID):
        """実行中Jobへ協調停止を要求し、確定停止と混同しない。"""
        service = require_commands()
        try:
            return service.stop(task_id)
        except TaskCommandNotFound:
            raise HTTPException(404, "task_not_found") from None
        except TaskCommandConflict as exc:
            raise HTTPException(409, str(exc)) from None

    @app.get("/api/v1/ritsuko/operations/{task_id}")
    def task_operation(task_id: UUID):
        """プロセス内の受付状況を返す。確定Taskは従来のTask詳細APIを使う。"""
        service = require_commands()
        try:
            return service.operation(task_id)
        except TaskCommandNotFound:
            raise HTTPException(404, "operation_not_found") from None

    # Vue画面はビルド後のみ公開。ソースやCredentialは静的公開しない。
    dist = Path(__file__).parent / "frontend" / "dist"
    if dist.is_dir():
        app.mount("/ui", StaticFiles(directory=str(dist), html=True), name="vue_daily_ui")
    return app


def main() -> None:
    """既存GUIやAPIを停止せず、明示起動時だけ別localhost portで受け付ける。"""
    from bootstrap.web_runtime import build_core_task_queries
    import uvicorn

    port = int(os.getenv("LSA_VUE_DAILY_PORT", "8097"))
    if port in {8010, 8015, 8092, 8093, 8098} or not 1024 <= port <= 65535:
        raise RuntimeError("Use an isolated Vue Daily port (default 8097).")
    uvicorn.run(create_app(queries=build_core_task_queries()), host="127.0.0.1", port=port)


if __name__ == "__main__":
    main()
