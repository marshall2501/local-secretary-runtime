# Provenance-Origin: original
# Provenance-AI: AI-generated
"""RITSUKOのTask操作・保存状態をVueから切り離した共通HTTP API。

Router自体はDB・LLM・Policyを生成しない。呼び出すホストアプリが
既存のTaskQueries／TaskCommandsを注入し、localhost／認証／Origin等の
通信境界を保持してから組み込む。UIのmountや静的ファイル配信はしない。
"""
from __future__ import annotations

from typing import Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from ritsuko.application.task_commands import (
    TaskCommandConflict, TaskCommandNotFound, TaskCommands,
)
from ritsuko.core.magi_bridge import (
    reviewable_answer_candidate, reviewable_user_knowledge_proposal,
)

TaskView = Literal["recent", "open", "completed"]


class TaskStartBody(BaseModel):
    """新規依頼と固定判断方式、再送時の同一Task UUIDを表す。"""

    request: str = Field(min_length=1, max_length=4000)
    decision_strategy: Literal["standard", "llm"]
    task_id: UUID | None = None


class TaskAnswerBody(BaseModel):
    """保存Taskの追加本人回答を表す。"""

    reply: str = Field(min_length=1, max_length=4000)


class TaskReviewBody(BaseModel):
    """本人が明示選択する回答のみ／記憶反映Reviewを表す。"""

    decision: Literal["answer_only", "remember"]


def create_ritsuko_task_router(*, queries, commands: TaskCommands | None = None) -> APIRouter:
    """Taskのread/commandsを単一のAPI Routerへ接続する。

    Args:
        queries: 保存済みTaskの一覧・追跡を取得する共通Query。
        commands: 権限とRepositoryを組み立て済みの操作Port。
            None時は従来どおりTask POSTを503で拒否する。

    Returns:
        APIRouter: UI固有の状態・静的資産に依存しないTask API群。

    Note:
        Host側のlocalhost・Origin・認証検査を省略して直接公開してはいけない。
    """
    router = APIRouter()

    @router.get("/api/v1/ritsuko/tasks")
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

    @router.get("/api/v1/ritsuko/tasks/{task_id}")
    def task_trace(task_id: UUID):
        """指定Taskの確定状態とAction/Result履歴を読み取る。

        MAGIの回答候補をTaskの完了状態へ置き換えない。

        Args:
            task_id: 再読み込み後にも同じ履歴を特定する保存済みTask ID。

        Raises:
            HTTPException: Task不在なら404、Query Service利用不能なら503。
        """
        try:
            saved = queries.trace(task_id)
            task = dict(saved.get("task") or {})
            session = task.get("magi_session") or {}
            # The Core determines reviewability; Vue must not infer whether
            # an ungrounded MAGI knowledge candidate can be written.
            reviewing = (
                task.get("status") == "waiting_external"
                and task.get("phase") == "awaiting_review"
            )
            knowledge = (
                reviewable_user_knowledge_proposal(session)
                if reviewing else None
            )
            ordinary = reviewable_answer_candidate(session) if reviewing else None
            # This is a preview of the actual checked candidate, not a model
            # status flag. It is only available to the existing local Task UI.
            task["can_remember"] = knowledge is not None
            task["can_answer_only"] = knowledge is not None or ordinary is not None
            task["memory_review_candidate"] = (
                {
                    "knowledge_candidate": knowledge["knowledge_candidate"],
                    "answer": knowledge["answer"],
                    "user_text": knowledge["user_text"],
                }
                if knowledge is not None else None
            )
            return {"trace": {**saved, "task": task}, "source": "persisted"}
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


    @router.post("/api/v1/ritsuko/tasks", status_code=202)
    async def start_task(body: TaskStartBody):
        """実Taskの新規受付。Operationの受理とDBへのTask保存・完了は別段階。"""
        service = require_commands()
        return await accept_command(service.start(
            body.request, decision_strategy=body.decision_strategy,
            task_id=body.task_id,
        ))

    @router.post("/api/v1/ritsuko/tasks/{task_id}/answer", status_code=202)
    async def answer_task(task_id: UUID, body: TaskAnswerBody):
        """同一Task IDへの本人回答を受付ける。DBの最終競合判定はRITSUKOが行う。"""
        service = require_commands()
        return await accept_command(service.answer(task_id, body.reply))

    @router.post("/api/v1/ritsuko/tasks/{task_id}/review", status_code=202)
    async def review_task(task_id: UUID, body: TaskReviewBody):
        """Task Commandsへ本人の明示選択を渡す。Memory IntakeはRuntime Port側。"""
        service = require_commands()
        return await accept_command(service.review(task_id, body.decision))

    @router.post("/api/v1/ritsuko/tasks/{task_id}/stop")
    def stop_task(task_id: UUID):
        """実行中Jobへ協調停止を要求し、確定停止と混同しない。"""
        service = require_commands()
        try:
            return service.stop(task_id)
        except TaskCommandNotFound:
            raise HTTPException(404, "task_not_found") from None
        except TaskCommandConflict as exc:
            raise HTTPException(409, str(exc)) from None

    @router.get("/api/v1/ritsuko/operations/{task_id}")
    def task_operation(task_id: UUID):
        """プロセス内の受付状況を返す。確定Taskは従来のTask詳細APIを使う。"""
        service = require_commands()
        try:
            return service.operation(task_id)
        except TaskCommandNotFound:
            raise HTTPException(404, "operation_not_found") from None


    return router
