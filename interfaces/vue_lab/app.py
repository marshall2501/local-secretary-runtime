"""Vue試作用の独立FastAPIサーバー。

既存Secretary API（8010）のDB権限・連携契約は変更しない。
架空Taskにだけ接続するため、本番接続先として登録してはならない。
"""
from __future__ import annotations

import hmac
import os
import secrets
from pathlib import Path
from typing import Annotated
from uuid import UUID

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from interfaces.vue_lab.task_service import IsolatedTaskService, TaskConflict


class Credential(BaseModel):
    """開発用の短命なブラウザセッションを作るための合言葉。"""
    token: str = Field(min_length=32, max_length=256)


class TaskRequest(BaseModel):
    """架空Taskに渡す自然言語依頼。"""
    request: str = Field(min_length=1, max_length=4000)


class AnswerRequest(BaseModel):
    """架空Taskの本人質問に対する追加回答。"""
    reply: str = Field(min_length=1, max_length=4000)


def create_app(*, service: IsolatedTaskService | None = None,
               token: str, dev_origin: str = "") -> FastAPI:
    """架空RITSUKO TaskとVue画面を同一originで提供する検証サーバー。

    Args:
        service: 架空のTaskサービス。テストで依存差し替え可能。
        token: localhost管理UIへログインする専用トークン。
        dev_origin: Vite開発プロキシで使用する許可済みOrigin。
    """
    if len(token) < 32 or len(token) > 256:
        raise ValueError("LSA_VUE_LAB_TOKEN_must_be_32_to_256_chars")
    tasks = service if service is not None else IsolatedTaskService()
    sessions: dict[str, str] = {}
    app = FastAPI(
        title="Local Secretary Vue Isolation Lab",
        docs_url=None, redoc_url=None, openapi_url=None,
    )

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        """外部接続・Origin偽装・転送Headerを遮断する。"""
        host = request.headers.get("host", "").split(":", 1)[0].lower()
        if host not in {"127.0.0.1", "localhost"}:
            return JSONResponse({"detail": "local_host_only"}, status_code=403)
        if any(name in request.headers for name in
               ("x-forwarded-host", "x-forwarded-for", "forwarded")):
            return JSONResponse({"detail": "forwarded_request_denied"}, status_code=403)
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            origin = request.headers.get("origin")
            local_origin = str(request.base_url).rstrip("/")
            if origin and origin not in {local_origin, dev_origin}:
                return JSONResponse({"detail": "invalid_origin"}, status_code=403)
            site = request.headers.get("sec-fetch-site")
            if site and site not in {"same-origin", "none"}:
                return JSONResponse({"detail": "cross_site_denied"}, status_code=403)
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response

    def owner(request: Request) -> None:
        """ブラウザ認証Cookieと更新要求のCSRFを検証する。"""
        sid = request.cookies.get("vue_lab_session", "")
        csrf = sessions.get(sid)
        if csrf is None:
            raise HTTPException(401, "login_required")
        if request.method not in {"GET", "HEAD"}:
            supplied = request.headers.get("x-csrf-token", "")
            if not hmac.compare_digest(supplied, csrf):
                raise HTTPException(403, "csrf_denied")

    @app.get("/api/lab/session")
    def session(request: Request):
        """F5後もCookieからログインを復元し、CSRFを返す。"""
        owner(request)
        return {"csrf": sessions[request.cookies["vue_lab_session"]]}

    @app.post("/api/lab/login")
    def login(body: Credential):
        """専用トークン認証後、HttpOnly Cookieを発行する。"""
        if not hmac.compare_digest(body.token, token):
            raise HTTPException(401, "invalid_credential")
        sid, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        sessions[sid] = csrf
        response = JSONResponse({"csrf": csrf})
        response.set_cookie(
            "vue_lab_session", sid, httponly=True, samesite="strict",
            secure=False, path="/", max_age=3600,
        )
        return response

    @app.post("/api/lab/logout", dependencies=[Depends(owner)])
    def logout(request: Request):
        """セッションを失効させ、画面を未認証に戻す。"""
        sessions.pop(request.cookies.get("vue_lab_session", ""), None)
        response = JSONResponse({"ok": True})
        response.delete_cookie("vue_lab_session", path="/")
        return response

    @app.get("/api/lab/tasks", dependencies=[Depends(owner)])
    def list_tasks():
        """合成Task一覧を取得する。"""
        return {"items": tasks.list_tasks()}

    @app.get("/api/lab/tasks/{task_id}", dependencies=[Depends(owner)])
    def get_task(task_id: UUID):
        """合成Task IDから保存済み状態を取得する。"""
        try:
            return tasks.get(str(task_id))
        except KeyError as exc:
            raise HTTPException(404, "task_not_found") from exc

    @app.post("/api/lab/tasks", status_code=202, dependencies=[Depends(owner)])
    async def start_task(body: TaskRequest):
        """長時間Task完了を待たず、Task IDを返す。"""
        if not body.request.strip():
            raise HTTPException(422, "empty_request")
        return await tasks.start(body.request)

    @app.post("/api/lab/tasks/{task_id}/answer",
              status_code=202, dependencies=[Depends(owner)])
    async def answer(task_id: UUID, body: AnswerRequest):
        """同一の合成Task IDで本人回答を再開する。"""
        if not body.reply.strip():
            raise HTTPException(422, "empty_reply")
        try:
            return await tasks.answer(str(task_id), body.reply)
        except KeyError as exc:
            raise HTTPException(404, "task_not_found") from exc
        except TaskConflict as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.post("/api/lab/tasks/{task_id}/stop", dependencies=[Depends(owner)])
    def stop(task_id: UUID):
        """架空Taskへ停止要求を送り、状態を返す。"""
        try:
            return tasks.stop(str(task_id))
        except KeyError as exc:
            raise HTTPException(404, "task_not_found") from exc
        except TaskConflict as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.get("/api/lab/health")
    def health():
        """実運用APIとは別の合成専用サーバーであることを明示する。"""
        return {"status": "ok", "synthetic": True}

    dist = Path(__file__).parent / "frontend" / "dist"
    if dist.is_dir():
        # ビルド済みVueだけを配信。ソースツリーやSecretは静的公開しない。
        app.mount("/ui", StaticFiles(directory=str(dist), html=True), name="vue_ui")
    return app


def main() -> None:
    """専用ローカルポートだけで試作UIと架空APIを起動する。"""
    import uvicorn

    token = os.getenv("LSA_VUE_LAB_TOKEN", "")
    if len(token) < 32:
        raise RuntimeError("Set an isolated LSA_VUE_LAB_TOKEN (32+ chars) first.")
    port = int(os.getenv("LSA_VUE_LAB_PORT", "8098"))
    if port in {8010, 8015, 8092, 8093} or not 1024 <= port <= 65535:
        raise RuntimeError("Use an isolated Vue Lab port (default 8098).")
    uvicorn.run(
        create_app(token=token, dev_origin=os.getenv("LSA_VUE_LAB_DEV_ORIGIN", "")),
        host="127.0.0.1", port=port, reload=False, log_level="warning",
    )


if __name__ == "__main__":
    main()
