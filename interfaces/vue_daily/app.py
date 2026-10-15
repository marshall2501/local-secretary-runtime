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
    """注入済みTask Query ApplicationをHTTP read-onlyで公開する。

    DB接続やSecretはApplication/Composition Rootが管理する。
    本人専用loopback入口として、Origin/Hostを限定する。
    """
    app = FastAPI(
        title="Local Secretary Vue Daily API",
        docs_url=None, redoc_url=None, openapi_url=None,
    )

    @app.middleware("http")
    async def local_only(request: Request, call_next):
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
        return {"status": "ok", "synthetic": False, "mode": "read_only"}

    @app.get("/api/v1/ritsuko/tasks")
    def list_tasks(
        view: TaskView = "recent",
        limit: int = Query(default=10, ge=1, le=50),
        offset: int = Query(default=0, ge=0),
    ):
        """保存済みTask一覧だけを取得する。MAGIには送信しない。"""
        try:
            result = getattr(queries, view)(limit=limit, offset=offset)
        except (RuntimeError, OSError):
            raise HTTPException(503, "task_query_unavailable") from None
        return {"items": result, "source": "persisted", "view": view}

    @app.get("/api/v1/ritsuko/tasks/{task_id}")
    def task_trace(task_id: UUID):
        """Task IDの保存済みTask/Action/Resultを取得する。候補で完了を上書きしない。"""
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
