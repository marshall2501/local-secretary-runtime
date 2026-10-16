# Provenance-Origin: original
# Provenance-AI: AI-generated
"""Vue日常UIへ実DB-backed Taskの読取を提供する独立FastAPI入口。

本APIはTaskの業務判断や保存をしない。既存CoreTaskQueryServiceを再利用し、
既存Secretary REST /tasks（別のTask作成契約）には手を加えない。
新Vue画面・Task write接続は後続段階で行う。
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Literal
from uuid import UUID

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles


TaskView = Literal["recent", "open", "completed"]


def create_app(*, queries) -> FastAPI:
    """既存Task Query Applicationを使う読取専用のHTTP入口を構築する。

    DB接続やSecretはComposition Root側で解決し、この入口には渡さない。

    Args:
        queries: 保存済みTask一覧とTask履歴を取得する共有Query Service。
            この関数では接続・Task実行・DB更新を行わない。

    Returns:
        FastAPI: ローカルTask参照と、ビルド済みVueだけを公開するアプリ。
    """
    app = FastAPI(
        title="Local Secretary Vue Daily API",
        docs_url=None, redoc_url=None, openapi_url=None,
    )

    @app.middleware("http")
    async def local_only(request: Request, call_next):
        """本人用ローカルプレビューへのHTTPアクセスだけを通す。

        外部Web入口や既存APIの認可を代替するものではなく、今後の
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
        """Vueの接続状況表示向けに、実Taskの読取専用入口の稼働状態を返す。"""
        return {"status": "ok", "synthetic": False, "mode": "read_only"}

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
